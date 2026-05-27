"""Sección 1 — IR pre-LLM: BM25 y TF-IDF desde cero sobre el corpus chileno.

Demuestra, con números reales del corpus regulatorio:
  1. Mecánica de TF-IDF y BM25 (ranking de chunks para queries reales).
  2. Por qué BM25 clava las referencias normativas exactas ("Ley Nº 21.210").
  3. Dónde BM25 sufre por brecha de vocabulario (query "3º básico" vs texto
     "1º a 6º básico") — anticipo de la sección 2 (embeddings densos).
  4. Recall@k y MRR a nivel documento para BM25 vs TF-IDF sobre el golden
     dataset de 01-evals (las métricas de la sección 05 de esa masterclass).
  5. Curva de saturación de term-frequency de BM25 (diagrama matplotlib).

Ejecutar:
    uv run python 02-retrieval/code/01-ir-clasico.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # backend sin ventana, para guardar a archivo
import matplotlib.pyplot as plt

# El directorio del script está en sys.path al ejecutarlo, así importamos la lib local.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrieval_lib import BM25Retriever, Chunk, TfidfRetriever, tokenize  # noqa: E402

# shared/ es importable desde la raíz del proyecto.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.utils import get_project_root  # noqa: E402

CORPUS_FILES = [
    "circular-01-sii-iva-digital.txt",
    "decreto-01-subvencion-escolar.txt",
    "glosa-01-presupuesto-salud.txt",
    "norma-01-ley-lobby.txt",
]


def simple_chunk(text: str, doc_id: str) -> list[Chunk]:
    """Chunking simple por bloques separados por línea en blanco.

    Deliberadamente ingenuo: el chunking serio es la sección 4. Aquí solo
    necesitamos pasajes indexables para mostrar la mecánica de IR clásico.
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    chunks: list[Chunk] = []
    for i, block in enumerate(blocks):
        chunks.append(Chunk(chunk_id=f"{doc_id}#{i}", doc_id=doc_id, text=block))
    return chunks


def load_corpus() -> list[Chunk]:
    corpus_dir = get_project_root() / "shared" / "corpus_chileno"
    chunks: list[Chunk] = []
    for fname in CORPUS_FILES:
        text = (corpus_dir / fname).read_text(encoding="utf-8")
        chunks.extend(simple_chunk(text, doc_id=fname))
    return chunks


def load_golden() -> list[dict]:
    path = (
        get_project_root()
        / "01-evals"
        / "examples"
        / "golden-dataset-rag-fiscal.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))["items"]


# --------------------------------------------------------------------------- #
# Métricas (mismas definiciones que 01-evals/theory/05), a nivel documento.
# --------------------------------------------------------------------------- #
def retrieved_docs_in_order(results) -> list[str]:
    """Docs distintos en el orden en que aparece su primer chunk en el ranking."""
    seen: list[str] = []
    for r in results:
        if r.chunk.doc_id not in seen:
            seen.append(r.chunk.doc_id)
    return seen


def recall_at_k(retrieved_docs: list[str], expected: list[str], k: int) -> float:
    if not expected:
        return float("nan")  # ítems de abstención: recall indefinido
    top = set(retrieved_docs[:k])
    return len(top & set(expected)) / len(expected)


def reciprocal_rank(results, expected: set[str]) -> float:
    for rank, r in enumerate(results, start=1):
        if r.chunk.doc_id in expected:
            return 1.0 / rank
    return 0.0


def evaluate(retriever, golden: list[dict], k: int = 5) -> dict:
    recalls, rrs = [], []
    for item in golden:
        expected = item["expected_docs"]
        if not expected:  # saltar abstención: se mide aparte (sección 7)
            continue
        results = retriever.search(item["query"], k=k)
        docs = retrieved_docs_in_order(results)
        recalls.append(recall_at_k(docs, expected, k))
        rrs.append(reciprocal_rank(results, set(expected)))
    n = len(recalls)
    return {
        "n_queries": n,
        f"recall@{k}": sum(recalls) / n,
        "mrr": sum(rrs) / n,
    }


def hr(title: str = "") -> None:
    print("\n" + "=" * 78)
    if title:
        print(title)
        print("=" * 78)


def show_top(retriever, query: str, k: int = 3) -> None:
    print(f'\nQuery: "{query}"')
    for rank, r in enumerate(retriever.search(query, k=k), start=1):
        snippet = " ".join(r.chunk.text.split())[:90]
        print(f"  {rank}. [{r.score:5.3f}] {r.chunk.chunk_id:28s} {snippet}…")


def plot_saturation(out_path: Path) -> None:
    """Curva de saturación de term-frequency de BM25 vs TF-IDF lineal."""
    import numpy as np

    tf = np.arange(0, 21)
    avgdl_ratio = 1.0  # documento de longitud promedia
    b = 0.75
    fig, ax = plt.subplots(figsize=(8, 5))

    for k1 in [0.5, 1.2, 1.5, 3.0]:
        denom = k1 * (1 - b + b * avgdl_ratio)
        contrib = (tf * (k1 + 1)) / (tf + denom)
        contrib[0] = 0.0
        ax.plot(tf, contrib, marker="o", ms=3, label=f"BM25 (k1={k1})")

    # TF-IDF usa tf bruto (lineal): crece sin techo.
    ax.plot(tf, tf, "--", color="gray", label="TF-IDF (tf lineal)")

    ax.set_xlabel("Frecuencia del término en el documento (tf)")
    ax.set_ylabel("Contribución al score (factor de tf)")
    ax.set_title("Saturación de BM25: la 10ª aparición vale mucho menos que la 1ª")
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.set_ylim(0, 12)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    print(f"\nDiagrama guardado en: {out_path}")


def main() -> None:
    chunks = load_corpus()
    golden = load_golden()
    bm25 = BM25Retriever().fit(chunks)
    tfidf = TfidfRetriever().fit(chunks)

    hr("CORPUS")
    by_doc: dict[str, int] = {}
    for c in chunks:
        by_doc[c.doc_id] = by_doc.get(c.doc_id, 0) + 1
    print(f"{len(chunks)} chunks de {len(by_doc)} documentos:")
    for doc, n in by_doc.items():
        print(f"  {n:2d} chunks  {doc}")
    print(f"Longitud promedio de chunk (tokens): {bm25.avgdl:.1f}")

    hr("1. IDF: los términos raros pesan más")
    # IDF de BM25 de algunos términos representativos.
    for term in ["21.210", "20.730", "iva", "vacunas", "subvencion", "lobby", "salud"]:
        toks = tokenize(term)
        t = toks[0] if toks else term
        print(f"  idf({t:12s}) = {bm25.idf.get(t, 0.0):5.2f}")

    hr("2. BM25 clava la referencia normativa exacta")
    show_top(bm25, "¿Qué dice la Ley Nº 21.210?")
    print("\n  Contribución por término al chunk top-1:")
    top = bm25.search("¿Qué dice la Ley Nº 21.210?", k=1)[0]
    for term, contrib in bm25.explain("¿Qué dice la Ley Nº 21.210?", top.index):
        print(f"    {term:14s} {contrib:5.3f}")

    hr("3. BM25 vs TF-IDF: misma query, mismo orden, distinta razón")
    q = "obligaciones del prestador de servicios digitales extranjero"
    print("BM25:")
    show_top(bm25, q)
    print("\nTF-IDF:")
    show_top(tfidf, q)
    print("\n  En chunks cortos y de longitud pareja, ambos rankean igual.")
    print("  La diferencia de BM25 es la SATURACIÓN del term-frequency:")
    print("  contribución de un término (idf=1, doc de longitud media) según cuántas")
    print("  veces aparezca, BM25(k1=1.5) vs TF-IDF (tf lineal):")
    print(f"    {'tf':>4} {'BM25':>8} {'TF-IDF':>8}")
    k1, b = 1.5, 0.75
    denom = k1 * (1 - b + b * 1.0)  # doc de longitud = avgdl
    for f in [1, 2, 5, 10, 20]:
        bm = (f * (k1 + 1)) / (f + denom)
        print(f"    {f:>4} {bm:>8.3f} {float(f):>8.3f}")
    print("  La 20ª aparición casi no suma en BM25; en TF-IDF suma igual que la 1ª.")

    hr("4. Brecha de vocabulario: BM25 no entiende que 3º ∈ [1º, 6º]")
    q = "¿Cuántas USE recibe un alumno prioritario de 3º básico?"
    show_top(bm25, q)
    print('\n  El texto dice "1º a 6º básico: 1,694 USE" — nunca "3º".')
    print("  BM25 acierta por OTROS términos (use, alumno, prioritario, basico),")
    print("  no porque entienda el rango. Esto es lo que arregla el denso (sección 2).")

    hr("5. Recall@k y MRR sobre el golden dataset (25 queries con fuente)")
    for k in [1, 3, 5]:
        m_bm25 = evaluate(bm25, golden, k=k)
        m_tfidf = evaluate(tfidf, golden, k=k)
        print(
            f"  k={k}:  BM25  recall@{k}={m_bm25[f'recall@{k}']:.3f} "
            f"MRR={m_bm25['mrr']:.3f}   |   "
            f"TF-IDF recall@{k}={m_tfidf[f'recall@{k}']:.3f} "
            f"MRR={m_tfidf['mrr']:.3f}"
        )
    print(
        "\n  Nota honesta: con 4 documentos el recall a nivel-doc tiene techo bajo"
    )
    print("  (es fácil acertar el doc correcto). El corpus se expande en la sección 2+")
    print("  para que las diferencias denso/sparse sean medibles de verdad.")

    diagrams_dir = get_project_root() / "02-retrieval" / "diagrams"
    diagrams_dir.mkdir(exist_ok=True)
    plot_saturation(diagrams_dir / "bm25-saturacion.png")


if __name__ == "__main__":
    main()
