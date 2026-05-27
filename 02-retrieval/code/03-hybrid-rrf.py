"""Sección 3 — Hybrid search: fusión sparse + dense (RRF y ponderada).

Sobre el corpus de 16 docs, con BM25 y denso de las secciones 1-2, demuestra:
  1. RRF desde cero: cómo fusiona rankings (no scores) paso a paso.
  2. Casos de recuperación: el híbrido conserva lo que cada uno acierta y
     rescata lo que el otro falla ("Ley 21.210" y una paráfrasis multi-doc).
  3. Recall@k: BM25 vs denso vs híbrido-RRF vs híbrido-ponderado, global y por
     tipo de query.
  4. Barrido del peso α (ponderada) y de la constante k (RRF): por qué RRF es el
     default robusto sin hiperparámetros. Diagrama matplotlib.

Ejecutar (usa la caché de embeddings de la sección 2):
    uv run python 02-retrieval/code/03-hybrid-rrf.py
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
    BM25Retriever,
    DenseRetriever,
    HybridRetriever,
    OpenAIEmbedder,
    ScoredDoc,
    load_corpus_chunks,
    rrf_fuse,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.utils import get_project_root  # noqa: E402

ROOT = get_project_root()
CORPUS_DIR = ROOT / "shared" / "corpus_chileno"
CACHE = ROOT / "02-retrieval" / "examples" / "cache-embeddings" / "embeddings.npz"
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


def recall_at_k(retriever, golden, k: int) -> float:
    vals = []
    for item in golden:
        if not item["expected_docs"]:
            continue
        docs = set(docs_in_order(retriever.search(item["query"], k=k))[:k])
        vals.append(len(docs & set(item["expected_docs"])) / len(item["expected_docs"]))
    return sum(vals) / len(vals)


def recall_by_type(retriever, golden, k: int) -> dict[str, tuple[float, int]]:
    buckets: dict[str, list[float]] = {}
    for item in golden:
        if not item["expected_docs"]:
            continue
        docs = set(docs_in_order(retriever.search(item["query"], k=k))[:k])
        rec = len(docs & set(item["expected_docs"])) / len(item["expected_docs"])
        buckets.setdefault(item["query_type"], []).append(rec)
    return {t: (sum(v) / len(v), len(v)) for t, v in buckets.items()}


def show_doc_ranking(label: str, results: list[ScoredDoc], k: int = 4) -> None:
    docs = docs_in_order(results)[:k]
    print(f"    {label:16s} {' > '.join(d.replace('.txt', '') for d in docs)}")


def main() -> None:
    chunks = load_corpus_chunks(CORPUS_DIR)
    golden = json.loads(
        (ROOT / "01-evals" / "examples" / "golden-dataset-rag-fiscal.json").read_text(
            encoding="utf-8"
        )
    )["items"]

    embedder = OpenAIEmbedder(cache_path=CACHE)
    bm25 = BM25Retriever().fit(chunks)
    dense = DenseRetriever(embedder).fit(chunks)
    hybrid_rrf = HybridRetriever([bm25, dense], method="rrf")
    hybrid_w = HybridRetriever([bm25, dense], method="weighted", weights=[0.5, 0.5])
    print(f"Corpus: {len(chunks)} chunks | API esta corrida: {embedder.api_calls}")

    hr("1. RRF desde cero, paso a paso (top-5 de cada uno)")
    q = "Ley Nº 21.210"
    bl, dl = bm25.search(q, k=5), dense.search(q, k=5)
    print(f'  Query: "{q}"\n')
    print(f"  {'rank':>4}  {'BM25':36s} {'DENSO':36s}")
    for i in range(5):
        print(f"  {i + 1:>4}  {bl[i].chunk.doc_id:36s} {dl[i].chunk.doc_id:36s}")
    print("\n  RRF (k=60) combina por posición. Contribución 1/(60+rank):")
    fused = rrf_fuse([bl, dl], k=60, top_k=5)
    for sd in fused:
        rb = next((i + 1 for i, x in enumerate(bl) if x.index == sd.index), None)
        rd = next((i + 1 for i, x in enumerate(dl) if x.index == sd.index), None)
        parts = []
        if rb:
            parts.append(f"BM25#{rb}=1/{60 + rb}")
        if rd:
            parts.append(f"denso#{rd}=1/{60 + rd}")
        print(f"    {sd.score:.5f}  {sd.chunk.doc_id:34s} ({' + '.join(parts)})")

    hr("2. Recuperación: el híbrido toma lo mejor de cada uno")
    casos = [
        ("Ley Nº 21.210", "denso falla (pone el doc de contexto 1º), BM25 acierta"),
        (
            "ayuda económica para colegios con niños de bajos recursos",
            "BM25 se dispersa, el denso acierta por significado",
        ),
        (
            "¿cómo se sanciona a un funcionario que esconde sus bienes?",
            "BM25 falla feo (devuelve IVA), el denso acierta",
        ),
    ]
    for q, nota in casos:
        print(f'\n  Query: "{q}"\n  ({nota})')
        show_doc_ranking("BM25", bm25.search(q, k=8))
        show_doc_ranking("DENSO", dense.search(q, k=8))
        show_doc_ranking("HÍBRIDO-RRF", hybrid_rrf.search(q, k=8))

    hr("3. Recall@k: BM25 vs denso vs híbrido")
    sistemas = {
        "BM25": bm25,
        "Denso": dense,
        "Híbrido-RRF": hybrid_rrf,
        "Híbrido-pond(0.5)": hybrid_w,
    }
    print(f"  {'sistema':20s} {'recall@1':>9s} {'recall@3':>9s} {'recall@5':>9s}")
    for name, sysr in sistemas.items():
        r1 = recall_at_k(sysr, golden, 1)
        r3 = recall_at_k(sysr, golden, 3)
        r5 = recall_at_k(sysr, golden, 5)
        print(f"  {name:20s} {r1:9.3f} {r3:9.3f} {r5:9.3f}")

    print("\n  Por tipo de query (recall@3):")
    tablas = {n: recall_by_type(s, golden, 3) for n, s in sistemas.items()}
    tipos = sorted({t for tab in tablas.values() for t in tab})
    print(f"    {'tipo':12s} " + " ".join(f"{n[:9]:>10s}" for n in sistemas))
    for t in tipos:
        row = "    " + f"{t:12s} "
        for n in sistemas:
            v, _ = tablas[n].get(t, (float('nan'), 0))
            row += f"{v:10.3f} "
        print(row)

    hr("4. Barrido de hiperparámetros: por qué RRF es el default robusto")
    alphas = [i / 10 for i in range(11)]  # peso del denso, 0=BM25 puro, 1=denso puro
    rec_w = []
    for a in alphas:
        h = HybridRetriever([bm25, dense], method="weighted", weights=[1 - a, a])
        rec_w.append(recall_at_k(h, golden, 3))
    r_bm25 = recall_at_k(bm25, golden, 3)
    r_dense = recall_at_k(dense, golden, 3)
    r_rrf = recall_at_k(hybrid_rrf, golden, 3)
    print("  recall@3 ponderada por α (peso del denso):")
    for a, r in zip(alphas, rec_w):
        print(f"    α={a:.1f}  recall@3={r:.3f}")
    print(f"\n  BM25 solo={r_bm25:.3f} | denso solo={r_dense:.3f} | RRF@3={r_rrf:.3f}")
    print("  La ponderada sube monótona con el peso del denso: en ESTE corpus el")
    print("  denso ya domina y la fusión no lo supera en @3. RRF no tunea α y, sin")
    print("  embargo, gana en @5 (ver tabla de arriba): rescata hits complementarios.")

    # Diagrama honesto: barras recall@{1,3,5} por sistema. Muestra el cuadro real
    # (RRF gana en @5; el denso en @3; BM25 empata en @1), no un titular forzado.
    ks = [1, 3, 5]
    data = {n: [recall_at_k(s, golden, k) for k in ks] for n, s in sistemas.items()}
    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    width, x = 0.2, range(len(ks))
    colors = ["#7f7f7f", "#ff7f0e", "#2ca02c", "#1f77b4"]
    for i, (name, vals) in enumerate(data.items()):
        pos = [xi + (i - 1.5) * width for xi in x]
        bars = ax.bar(pos, vals, width, label=name, color=colors[i])
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + width / 2, v + 0.003, f"{v:.3f}", ha="center", fontsize=7)
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"recall@{k}" for k in ks])
    ax.set_ylim(0.75, 1.0)
    ax.set_ylabel("Recall (golden, 25 queries)")
    ax.set_title("BM25 vs denso vs híbrido: la fusión gana en @5, no en @3 (corpus chico)")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    out = DIAGRAMS / "hybrid-recall-comparacion.png"
    fig.savefig(out, dpi=120)
    print(f"\n  Diagrama guardado en: {out}")


if __name__ == "__main__":
    main()
