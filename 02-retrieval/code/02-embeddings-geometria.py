"""Sección 2 — Embeddings densos: geometría y sus fallos.

Sobre el corpus chileno completo (16 docs), con embeddings reales de OpenAI
(text-embedding-3-small, cacheados en disco), demuestra:
  1. Geometría: el coseno entre embeddings captura similitud semántica, no léxica.
  2. Proyección 2D del espacio vectorial vía PCA desde cero (diagrama).
  3. Dónde el denso GANA a BM25: paráfrasis y sinonimia (sin solape de palabras).
  4. Dónde el denso PIERDE contra BM25: referencias exactas, siglas, números.
  5. Recall@k BM25 vs denso sobre el golden, global y por tipo de query.

Ejecutar (requiere OPENAI_API_KEY en .env la primera vez; luego usa caché):
    uv run python 02-retrieval/code/02-embeddings-geometria.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrieval_lib import (  # noqa: E402
    BM25Retriever,
    DenseRetriever,
    OpenAIEmbedder,
    load_corpus_chunks,
    pca_2d,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.utils import get_project_root  # noqa: E402

ROOT = get_project_root()
CORPUS_DIR = ROOT / "shared" / "corpus_chileno"
CACHE = ROOT / "02-retrieval" / "examples" / "cache-embeddings" / "embeddings.npz"
DIAGRAMS = ROOT / "02-retrieval" / "diagrams"

# Familia temática de cada doc, para colorear la proyección 2D.
FAMILIA = {
    "circular-01-sii-iva-digital.txt": "IVA / tributario",
    "circular-04-sii-iva-exenciones.txt": "IVA / tributario",
    "ley-01-dl-825-iva-base.txt": "IVA / tributario",
    "ley-02-ley-21210-modernizacion.txt": "IVA / tributario",
    "circular-02-sii-renta-propyme.txt": "Renta / tributario",
    "circular-03-sii-ppm-honorarios.txt": "Renta / tributario",
    "tabla-01-valores-tributarios-2024.txt": "Renta / tributario",
    "decreto-01-subvencion-escolar.txt": "Educación",
    "glosa-02-presupuesto-educacion.txt": "Educación",
    "oficio-01-contraloria-subvenciones.txt": "Educación",
    "do-01-extracto-decreto-aranceles.txt": "Educación",
    "norma-01-ley-lobby.txt": "Probidad / lobby",
    "decreto-02-reglamento-ley-lobby.txt": "Probidad / lobby",
    "norma-02-ley-20880-probidad.txt": "Probidad / lobby",
    "glosa-01-presupuesto-salud.txt": "Salud",
    "glosa-03-presupuesto-trabajo.txt": "Trabajo",
}


def hr(title: str = "") -> None:
    print("\n" + "=" * 78)
    if title:
        print(title)
        print("=" * 78)


def cos(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def show_top(retriever, query: str, k: int = 3) -> None:
    print(f'\n  Query: "{query}"')
    for rank, r in enumerate(retriever.search(query, k=k), start=1):
        snippet = " ".join(r.chunk.text.split())[:78]
        print(f"    {rank}. [{r.score:6.3f}] {r.chunk.doc_id:38s} {snippet}…")


# ---- métricas (nivel documento), idénticas a la sección 1 ------------------ #
def docs_in_order(results) -> list[str]:
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


def plot_2d(chunks, matrix, out: Path) -> None:
    proj, var = pca_2d(matrix)
    fams = sorted(set(FAMILIA.values()))
    cmap = plt.get_cmap("tab10")
    color = {f: cmap(i) for i, f in enumerate(fams)}
    fig, ax = plt.subplots(figsize=(10, 7))
    for f in fams:
        idx = [i for i, c in enumerate(chunks) if FAMILIA[c.doc_id] == f]
        ax.scatter(proj[idx, 0], proj[idx, 1], s=22, alpha=0.7, label=f, color=color[f])
    ax.set_xlabel(f"PC1 ({var[0] * 100:.1f}% varianza)")
    ax.set_ylabel(f"PC2 ({var[1] * 100:.1f}% varianza)")
    ax.set_title(
        "Espacio de embeddings del corpus (PCA 2D)\n"
        "234 chunks · text-embedding-3-small · coloreado por familia temática"
    )
    ax.legend(loc="best", fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    print(f"\n  Diagrama guardado en: {out}")


def main() -> None:
    chunks = load_corpus_chunks(CORPUS_DIR)
    golden = json.loads(
        (ROOT / "01-evals" / "examples" / "golden-dataset-rag-fiscal.json").read_text(
            encoding="utf-8"
        )
    )["items"]

    embedder = OpenAIEmbedder(cache_path=CACHE)
    dense = DenseRetriever(embedder).fit(chunks)
    bm25 = BM25Retriever().fit(chunks)
    print(
        f"Corpus: {len(chunks)} chunks de 16 docs | embeddings {dense.matrix.shape[1]}d "
        f"| llamadas API esta corrida: {embedder.api_calls} (resto desde caché)"
    )

    hr("1. Geometría: el coseno mide significado, no palabras compartidas")
    pares = [
        ("IVA a servicios digitales", "impuesto a plataformas de streaming extranjeras"),
        ("subvención escolar para alumnos prioritarios", "ayuda estatal a niños vulnerables"),
        ("IVA a servicios digitales", "multa por no declarar patrimonio"),
    ]
    for a, b in pares:
        va, vb = embedder.embed([a]), embedder.embed([b])
        print(f"  cos = {cos(va[0], vb[0]):.3f}   '{a}'  ⟷  '{b}'")
    print("\n  Los dos primeros pares casi no comparten palabras pero el coseno es alto:")
    print("  el embedding captura el significado. El tercero (temas distintos) cae.")

    hr("2. Proyección 2D del espacio vectorial (PCA desde cero)")
    plot_2d(chunks, dense.matrix, DIAGRAMS / "espacio-vectorial-2d.png")

    hr("3. El denso GANA: paráfrasis y sinonimia (sin solape léxico)")
    for q in [
        "¿pagan impuesto las plataformas de streaming extranjeras?",
        "ayuda económica para colegios con niños de bajos recursos",
        "¿cómo se sanciona a un funcionario que esconde sus bienes?",
    ]:
        print("\n  --- BM25 ---")
        show_top(bm25, q)
        print("  --- DENSO ---")
        show_top(dense, q)

    hr("4. El denso PIERDE: referencias exactas, siglas y números")
    for q in [
        "Ley Nº 21.210",
        "¿qué es el PRAIS?",
        "valor de la UTM en septiembre de 2024",
    ]:
        print("\n  --- BM25 ---")
        show_top(bm25, q)
        print("  --- DENSO ---")
        show_top(dense, q)

    hr("5. Recall@k sobre el golden (16 docs, 25 queries con fuente)")
    for k in [1, 3, 5]:
        rb, rd = recall_at_k(bm25, golden, k), recall_at_k(dense, golden, k)
        print(f"  recall@{k}:  BM25={rb:.3f}   Denso={rd:.3f}")
    print("\n  Por tipo de query (recall@3, n = nº de queries):")
    tb, td = recall_by_type(bm25, golden, 3), recall_by_type(dense, golden, 3)
    print(f"    {'tipo':12s} {'BM25':>6s} {'Denso':>6s}  n")
    for t in sorted(set(tb) | set(td)):
        b, n = tb.get(t, (float("nan"), 0))
        d, _ = td.get(t, (float("nan"), 0))
        print(f"    {t:12s} {b:6.3f} {d:6.3f}  {n}")


if __name__ == "__main__":
    main()
