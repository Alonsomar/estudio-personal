"""Sección 5 — Query rewriting: HyDE, multi-query, decomposition, step-back.

Cuatro técnicas que actúan sobre la QUERY (no sobre el corpus). Sobre el
mismo corpus de 16 docs con dense retrieval, mide qué tanto cada una mueve
el recall, y muestra qué genera el LLM para queries representativas.

Las respuestas del LLM se cachean a disco para que la 2ª corrida sea gratis
y reproducible sin API key.

Ejecutar (usa caché de embeddings + de LLM; nuevos prompts → API gpt-4o-mini):
    uv run python 02-retrieval/code/05-query-rewriting.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrieval_lib import (  # noqa: E402
    DenseRetriever,
    LLMRewriter,
    OpenAIEmbedder,
    RewrittenRetriever,
    ScoredDoc,
    load_corpus_chunks,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.utils import get_project_root  # noqa: E402

ROOT = get_project_root()
CORPUS_DIR = ROOT / "shared" / "corpus_chileno"
EMB_CACHE = ROOT / "02-retrieval" / "examples" / "cache-embeddings" / "embeddings.npz"
LLM_CACHE = ROOT / "02-retrieval" / "examples" / "cache-llm.json"
DIAGRAMS = ROOT / "02-retrieval" / "diagrams"


def hr(title: str = "") -> None:
    print("\n" + "=" * 78)
    if title:
        print(title)
        print("=" * 78)


def docs_in_order(results: list[ScoredDoc]) -> list[str]:
    seen: list[str] = []
    for r in results:
        if r.chunk.doc_id not in seen:
            seen.append(r.chunk.doc_id)
    return seen


def recall_at_k(retriever, golden, k: int, query_type: str | None = None) -> float:
    vals = []
    for item in golden:
        if query_type is not None and item.get("query_type") != query_type:
            continue
        if not item["expected_docs"]:
            continue
        docs = set(docs_in_order(retriever.search(item["query"], k=k))[:k])
        vals.append(len(docs & set(item["expected_docs"])) / len(item["expected_docs"]))
    return sum(vals) / len(vals) if vals else float("nan")


def main() -> None:
    chunks = load_corpus_chunks(CORPUS_DIR)
    golden = json.loads(
        (ROOT / "01-evals" / "examples" / "golden-dataset-rag-fiscal.json").read_text(
            encoding="utf-8"
        )
    )["items"]
    embedder = OpenAIEmbedder(cache_path=EMB_CACHE)
    dense = DenseRetriever(embedder).fit(chunks)
    rw = LLMRewriter(cache_path=LLM_CACHE)
    print(f"Corpus: {len(chunks)} chunks | rewriter={rw.model}")

    estrategias = {
        "baseline (sin rewriting)": lambda q: [q],
        "HyDE": rw.hyde,
        "Multi-query (n=4)": lambda q: rw.multi_query(q, n=4),
        "Decomposition": rw.decompose,
        "Step-back": rw.step_back,
    }
    retrievers = {
        name: RewrittenRetriever(dense, fn) for name, fn in estrategias.items()
    }

    hr("1. Qué genera el LLM, para 3 queries representativas")
    muestras = [
        ("¿Qué autoridades son sujetos pasivos de la Ley de Lobby?", "factual / vocabulario directo"),
        (
            "¿Qué obligación trimestral comparten los prestadores de servicios "
            "digitales extranjeros y el Ministerio de Salud respecto a "
            "inmunizaciones?",
            "multi-doc / multi-hop",
        ),
        ("valor de la UTM en septiembre de 2024", "numérico / referencia exacta"),
    ]
    for q, etiq in muestras:
        print(f'\n  Query: "{q}"   [{etiq}]')
        print("  HyDE:")
        for line in rw.hyde(q):
            print(f"    > {line[:230]}{'…' if len(line) > 230 else ''}")
        print("  Multi-query:")
        for line in rw.multi_query(q, n=4)[1:]:  # saltamos el original
            print(f"    > {line[:180]}")
        print("  Decomposition:")
        for line in rw.decompose(q):
            print(f"    > {line[:180]}")
        print("  Step-back:")
        for line in rw.step_back(q)[1:]:  # saltamos el original
            print(f"    > {line[:180]}")
    print(f"\n  Llamadas LLM en esta corrida: {rw.api_calls} (resto desde caché).")

    hr("2. Recall@k por estrategia (golden, 25 queries con fuente)")
    print(f"  {'estrategia':28s} {'@1':>7s} {'@3':>7s} {'@5':>7s}")
    rows = []
    for name, retr in retrievers.items():
        r1 = recall_at_k(retr, golden, 1)
        r3 = recall_at_k(retr, golden, 3)
        r5 = recall_at_k(retr, golden, 5)
        rows.append((name, r1, r3, r5))
        print(f"  {name:28s} {r1:7.3f} {r3:7.3f} {r5:7.3f}")
    print(f"\n  Llamadas LLM acumuladas: {rw.api_calls}")

    hr("3. Recall@3 estratificado por tipo de query")
    tipos = ["factual", "numerico", "entidad", "multi-doc"]
    header = f"  {'estrategia':28s} " + " ".join(f"{t[:10]:>10s}" for t in tipos)
    print(header)
    rows_strat = []
    for name, retr in retrievers.items():
        vals = [recall_at_k(retr, golden, 3, query_type=t) for t in tipos]
        rows_strat.append((name, vals))
        print(f"  {name:28s} " + " ".join(f"{v:10.3f}" for v in vals))

    # Diagrama: barras agrupadas overall vs multi-doc por estrategia.
    fig, ax = plt.subplots(figsize=(11, 5.5))
    names = [r[0].split(" (")[0] for r in rows]
    overall = [r[2] for r in rows]  # recall@3 overall
    multidoc = [
        next(v[3] for n, v in rows_strat if n == r[0]) for r in rows
    ]
    x = list(range(len(names)))
    w = 0.38
    b1 = ax.bar([xi - w / 2 for xi in x], overall, w, color="#1f77b4", label="recall@3 (todas)")
    b2 = ax.bar([xi + w / 2 for xi in x], multidoc, w, color="#ff7f0e", label="recall@3 multi-doc")
    for bars, vals in [(b1, overall), (b2, multidoc)]:
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + w / 2, v + 0.01, f"{v:.3f}", ha="center", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=15, ha="right", fontsize=9)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Recall@3")
    ax.set_title(
        "Query rewriting (denso): efecto modesto overall, multi-doc empeora (HyDE)"
    )
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    out = DIAGRAMS / "query-rewriting-recall.png"
    fig.savefig(out, dpi=120)
    print(f"\n  Diagrama guardado en: {out}")


if __name__ == "__main__":
    main()
