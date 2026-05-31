"""Sección 8 — Benchmark comparativo aislado de retrieval.

Evalúa los retrievers construidos en §1–§7 sobre el mismo corpus, el mismo
chunking (`simple_chunk`) y el mismo golden — pero ahora con ground truth a
NIVEL CHUNK (`examples/golden-retrieval.json`) además del nivel doc.

Mide recall@{1,3,5}, MRR y nDCG@{1,3,5} con intervalos de confianza bootstrap
(reusando la metodología de 01-evals §8), y separa el análisis por
`query_type` para ver qué arquitectura gana en qué tipo de query.

Sistemas comparados (todos sobre el mismo simple_chunk para que la
comparación sea pareja; §4 ya mostró el efecto de cambiar el chunker):

  1. BM25                        — §1, sparse baseline a batir
  2. TF-IDF                      — §1, baseline más antiguo
  3. Denso (text-embedding-3-small) — §2, bi-encoder
  4. Hybrid-RRF (BM25 + denso)   — §3
  5. Hybrid-weighted             — §3, con pesos manuales
  6. Hybrid + HyDE rewriting     — §5
  7. Hybrid + LLM-reranker (lw)  — §6

(Los rewriters y rerankers se aplican sobre Hybrid-RRF, que es el mejor
recall@k_grande de §3 y por tanto el mejor pool para alimentar §§5-6.)

Salida:
  - Tabla doc-level y chunk-level con IC.
  - Análisis estratificado por query_type.
  - Diagrama recall@{1,3,5} por sistema, con barras de error.
  - JSON con resultados en `examples/benchmark-retrievers.json`.

Ejecutar:
    uv run python 02-retrieval/code/08-benchmark-retrievers.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrieval_lib import (  # noqa: E402
    BM25Retriever,
    DenseRetriever,
    HybridRetriever,
    LLMReranker,
    LLMRewriter,
    OpenAIEmbedder,
    RerankedRetriever,
    RewrittenRetriever,
    TfidfRetriever,
    bootstrap_ci,
    evaluate_retriever,
    load_corpus_chunks,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.utils import get_project_root  # noqa: E402

ROOT = get_project_root()
CORPUS_DIR = ROOT / "shared" / "corpus_chileno"
GOLDEN_DOC = ROOT / "01-evals" / "examples" / "golden-dataset-rag-fiscal.json"
GOLDEN_CHUNK = ROOT / "02-retrieval" / "examples" / "golden-retrieval.json"
EMB_CACHE = ROOT / "02-retrieval" / "examples" / "cache-embeddings" / "embeddings.npz"
RW_CACHE = ROOT / "02-retrieval" / "examples" / "cache-llm.json"
RERANK_CACHE = ROOT / "02-retrieval" / "examples" / "cache-rerank.json"
DIAGRAMS = ROOT / "02-retrieval" / "diagrams"
OUT_JSON = ROOT / "02-retrieval" / "examples" / "benchmark-retrievers.json"


def hr(title: str = "") -> None:
    print("\n" + "=" * 92)
    if title:
        print(title)
        print("=" * 92)


def fmt_ci(m: dict) -> str:
    return f"{m['mean']:5.3f} [{m['lo']:.3f}, {m['hi']:.3f}]"


def print_table(name: str, results: dict, k_values=(1, 3, 5)) -> None:
    print(f"\n  {name}")
    header = f"  {'sistema':38s} " + " ".join(
        f"{f'recall@{k}':>20s}" for k in k_values
    ) + f" {'MRR':>20s}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for sys_name, res in results.items():
        s = res["summary"]
        row = f"  {sys_name:38s} " + " ".join(
            fmt_ci(s[f"recall@{k}"]).rjust(20) for k in k_values
        )
        row += f" {fmt_ci(s['mrr']).rjust(20)}"
        print(row)


def stratify_by(records: list[dict], items: list[dict], field: str, metric: str) -> dict:
    """Promedio del `metric` por valor de `field` (query_type, difficulty)."""
    by_id = {it["id"]: it for it in items}
    groups: dict[str, list[float]] = defaultdict(list)
    for rec in records:
        item = by_id[rec["id"]]
        groups[item.get(field, "?")].append(rec[metric])
    return {k: sum(v) / len(v) for k, v in sorted(groups.items())}


def main() -> None:
    chunks = load_corpus_chunks(CORPUS_DIR)
    golden_doc = json.loads(GOLDEN_DOC.read_text(encoding="utf-8"))["items"]
    golden_chunk = json.loads(GOLDEN_CHUNK.read_text(encoding="utf-8"))["items"]
    queries_by_id = {it["id"]: it["query"] for it in golden_doc}

    embedder = OpenAIEmbedder(cache_path=EMB_CACHE)
    bm25 = BM25Retriever().fit(chunks)
    tfidf = TfidfRetriever().fit(chunks)
    dense = DenseRetriever(embedder).fit(chunks)
    hybrid_rrf = HybridRetriever([bm25, dense], method="rrf", pool=20)
    hybrid_w = HybridRetriever(
        [bm25, dense], method="weighted", weights=[0.4, 0.6], pool=20
    )

    rewriter = LLMRewriter(cache_path=RW_CACHE)
    reranker = LLMReranker(cache_path=RERANK_CACHE)
    hyde_hybrid = RewrittenRetriever(hybrid_rrf, rewriter.hyde, pool=20)
    rerank_hybrid = RerankedRetriever(hybrid_rrf, reranker.listwise, pool=10)

    systems = {
        "1. BM25 (§1)": bm25,
        "2. TF-IDF (§1)": tfidf,
        "3. Denso (§2)": dense,
        "4. Hybrid-RRF (§3)": hybrid_rrf,
        "5. Hybrid-weighted (§3)": hybrid_w,
        "6. Hybrid + HyDE (§5)": hyde_hybrid,
        "7. Hybrid + LLM-rerank (§6)": rerank_hybrid,
    }

    print(
        f"Corpus: {len(chunks)} chunks, {len(set(c.doc_id for c in chunks))} docs.\n"
        f"Golden doc-level: {len(golden_doc)} items, "
        f"chunk-level: {len(golden_chunk)} items "
        f"({sum(1 for x in golden_chunk if x['expected_chunks'])} con chunks "
        f"esperados, {sum(1 for x in golden_chunk if x.get('requires_abstention'))} de abstención)."
    )

    # ---------------------------------------------------------------- #
    hr("1. Recall, MRR y nDCG con IC bootstrap — granularidad DOC")
    # Evaluación a nivel doc: reusa todo el golden v1 (más comparable con §§1-7).
    # Filtramos las queries de abstención: ningún retriever las acierta sin un
    # módulo aparte; al diluirían las métricas con ceros estructurales.
    doc_items_relevant = [it for it in golden_chunk if it["expected_docs"]]
    doc_results: dict = {}
    for name, sysr in systems.items():
        doc_results[name] = evaluate_retriever(
            sysr, doc_items_relevant, queries_by_id, granularity="doc"
        )
    print_table("DOC-level (n=27 queries con docs esperados, IC95%)", doc_results)

    hr("2. Recall, MRR y nDCG con IC bootstrap — granularidad CHUNK")
    # Chunk-level: exigente. El retriever no solo debe traer el doc correcto,
    # sino EL chunk con la respuesta. Para queries cuya respuesta está partida
    # entre N chunks, el ideal es recuperar los N (recall=1 si los trae todos).
    chunk_items_relevant = [it for it in golden_chunk if it["expected_chunks"]]
    chunk_results: dict = {}
    for name, sysr in systems.items():
        chunk_results[name] = evaluate_retriever(
            sysr, chunk_items_relevant, queries_by_id, granularity="chunk"
        )
    print_table(
        "CHUNK-level (n=27 queries con chunks esperados, IC95%)", chunk_results
    )

    hr("3. Doc-level vs chunk-level: qué historia cambia")
    print(
        "  Diferencia (chunk - doc) en recall@3:"
    )
    print(f"  {'sistema':38s}  doc r@3   chunk r@3   Δ")
    print("  " + "-" * 70)
    for name in systems:
        d = doc_results[name]["summary"]["recall@3"]["mean"]
        c = chunk_results[name]["summary"]["recall@3"]["mean"]
        print(f"  {name:38s}  {d:7.3f}   {c:7.3f}   {c - d:+.3f}")
    print(
        "\n  La caída doc→chunk es el costo real de 'haber traído el doc correcto\n"
        "  pero no el chunk con la respuesta'. Un sistema que cae mucho aquí va a\n"
        "  dañar la generación: el generador recibe contexto del doc correcto pero\n"
        "  no la frase que necesita."
    )

    # ---------------------------------------------------------------- #
    hr("4. Estratificación: ¿quién gana en qué tipo de query?")
    # Por query_type: ¿qué arquitectura gana en factual vs numérico vs entidad
    # vs multi-doc? El mensaje fuerte de §1-7 es que no hay un único ganador.
    metric = "recall@3"
    print(f"  Recall@3 promedio (CHUNK-level) por tipo de query:")
    types = sorted({it["query_type"] for it in chunk_items_relevant})
    counts = {t: sum(1 for it in chunk_items_relevant if it["query_type"] == t) for t in types}
    header = "  " + " " * 38 + " | " + " ".join(f"{t}(n={counts[t]})".rjust(14) for t in types)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name, res in chunk_results.items():
        stratum = stratify_by(res["per_query"], chunk_items_relevant, "query_type", metric)
        row = f"  {name:38s} | " + " ".join(
            f"{stratum.get(t, 0.0):14.3f}" for t in types
        )
        print(row)
    print(
        "\n  Observación esperada: BM25 brilla en 'numerico' (montos, fechas, refs.\n"
        "  exactas tipo '21.210'), el denso brilla en 'factual' (paráfrasis), el\n"
        "  rerank y el rewriting ayudan en 'multi-doc' / 'entidad' donde hay que\n"
        "  reordenar o expandir la vecindad de la query. 'scope' (abstención) sale\n"
        "  de esta tabla — ningún retriever sin módulo de abstención la resuelve."
    )

    # ---------------------------------------------------------------- #
    hr("5. Por difficulty (chunk-level)")
    difficulties = ["easy", "medium", "hard"]
    counts_d = {d: sum(1 for it in chunk_items_relevant if it["difficulty"] == d) for d in difficulties}
    header = "  " + " " * 38 + " | " + " ".join(f"{d}(n={counts_d[d]})".rjust(12) for d in difficulties)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name, res in chunk_results.items():
        stratum = stratify_by(res["per_query"], chunk_items_relevant, "difficulty", metric)
        row = f"  {name:38s} | " + " ".join(
            f"{stratum.get(d, 0.0):12.3f}" for d in difficulties
        )
        print(row)

    # ---------------------------------------------------------------- #
    hr("6. ¿Cuándo dos sistemas difieren significativamente?")
    # IC bootstrap del DELTA de medias por query. Si el IC del delta no
    # contiene 0, la diferencia es 'significativa al 5%' bajo este muestreo.
    # Comparación de referencia: BM25 vs los demás, recall@3 chunk-level.
    print(
        "  ΔRecall@3 chunk-level vs BM25 (IC95% del delta por query):\n"
        "  Si el IC del delta NO incluye 0, la diferencia es estadísticamente\n"
        "  significativa al 5% sobre la muestra de 27 queries."
    )
    bm25_pq = {r["id"]: r["recall@3"] for r in chunk_results["1. BM25 (§1)"]["per_query"]}
    print(f"  {'sistema':38s}  Δmean   IC95% del Δ          ¿sig?")
    print("  " + "-" * 80)
    for name, res in chunk_results.items():
        if name == "1. BM25 (§1)":
            continue
        deltas = [r["recall@3"] - bm25_pq[r["id"]] for r in res["per_query"]]
        mean, lo, hi = bootstrap_ci(deltas)
        sig = "sí" if (lo > 0 or hi < 0) else "no"
        print(f"  {name:38s} {mean:+6.3f}   [{lo:+.3f}, {hi:+.3f}]   {sig}")
    print(
        "\n  Con n=27 queries y métricas saturadas cerca de 1.0, casi nada da\n"
        "  significativo. Eso ES la lectura honesta: a esta escala de golden,\n"
        "  declarar 'el sistema X es mejor que el Y' por una diferencia de 0.04\n"
        "  en recall es ruido. La industria publica papers ignorando esto."
    )

    # ---------------------------------------------------------------- #
    hr("7. La trampa de Goodhart: optimizar recall sin importar abstención")
    # Las 3 queries de abstención son donde el retriever debería decir "nada".
    # Ningún retriever de §1-7 abstiene. Si las incluyéramos en la métrica con
    # la regla 'recall@k=0 si retrieved no vacío', TODOS bajarían exactamente
    # igual: la métrica no discrimina. Pero el sistema RAG completo (con
    # generador) SÍ se rompe ahí — alucina respuestas para queries fuera de
    # corpus. Mostrarlo:
    abstention = [it for it in golden_chunk if it.get("requires_abstention")]
    print(
        f"  {len(abstention)} queries de abstención (gd-025, gd-026, gd-027).\n"
        "  Ningún retriever de §§1-7 abstiene; todos devuelven docs irrelevantes."
    )
    for name, sysr in systems.items():
        falsos = 0
        for it in abstention:
            q = queries_by_id[it["id"]]
            res = sysr.search(q, k=3)
            if res:
                falsos += 1
        print(f"    {name:38s}  recupera contexto espurio en {falsos}/{len(abstention)} abstenciones")
    print(
        "\n  Las métricas estándar de retrieval IGNORAN esto. Un retriever que\n"
        "  acierte 95% en queries en-corpus y alucine en el 100% de las fuera-de-corpus\n"
        "  tiene un recall@3 muy alto y un sistema RAG roto. La señal de abstención\n"
        "  pertenece a otra capa (clasificador de query, umbral de confianza, módulo\n"
        "  separado), no a la métrica de recall. Confundirlo es el error clásico que\n"
        "  el plan de §8 quería marcar."
    )

    # ---------------------------------------------------------------- #
    hr("8. Persistir resultados")
    payload = {
        "metadata": {
            "n_systems": len(systems),
            "n_doc_items": len(doc_items_relevant),
            "n_chunk_items": len(chunk_items_relevant),
            "n_abstention": len(abstention),
        },
        "doc_level": {
            name: {"summary": res["summary"], "per_query": res["per_query"]}
            for name, res in doc_results.items()
        },
        "chunk_level": {
            name: {"summary": res["summary"], "per_query": res["per_query"]}
            for name, res in chunk_results.items()
        },
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Resultados en: {OUT_JSON}")

    # ---------------------------------------------------------------- #
    # Diagrama: recall@1/3/5 chunk-level por sistema, con barras de error.
    fig, axes = plt.subplots(1, 2, figsize=(15, 6))
    ks = [1, 3, 5]
    width = 0.11

    for ax, granularity_label, results in [
        (axes[0], "Doc-level", doc_results),
        (axes[1], "Chunk-level", chunk_results),
    ]:
        x = list(range(len(ks)))
        sys_names = list(results.keys())
        n_sys = len(sys_names)
        colors = plt.cm.tab10([i / max(1, n_sys - 1) for i in range(n_sys)])
        for i, name in enumerate(sys_names):
            s = results[name]["summary"]
            means = [s[f"recall@{k}"]["mean"] for k in ks]
            los = [s[f"recall@{k}"]["lo"] for k in ks]
            his = [s[f"recall@{k}"]["hi"] for k in ks]
            err_lo = [m - lo for m, lo in zip(means, los)]
            err_hi = [hi - m for m, hi in zip(means, his)]
            pos = [xi + (i - n_sys / 2) * width for xi in x]
            ax.bar(
                pos,
                means,
                width,
                yerr=[err_lo, err_hi],
                label=name,
                color=colors[i],
                capsize=2,
            )
        ax.set_xticks(x)
        ax.set_xticklabels([f"recall@{k}" for k in ks])
        ax.set_ylim(0, 1.05)
        ax.set_ylabel("Recall (golden, 27 queries)")
        ax.set_title(f"{granularity_label} — recall + IC95% bootstrap")
        ax.grid(True, axis="y", alpha=0.3)
        if granularity_label == "Doc-level":
            ax.legend(loc="lower right", fontsize=7.5)
    fig.suptitle("Benchmark §8: 7 arquitecturas, mismo corpus, mismo chunker (simple)")
    fig.tight_layout()
    out = DIAGRAMS / "benchmark-comparativo.png"
    fig.savefig(out, dpi=120)
    print(f"  Diagrama guardado en: {out}")

    print(f"\n  API calls esta corrida: embedder={embedder.api_calls}, "
          f"rewriter={rewriter.api_calls}, reranker={reranker.api_calls}")


if __name__ == "__main__":
    main()
