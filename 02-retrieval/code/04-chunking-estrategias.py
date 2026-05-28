"""Sección 4 — Chunking serio para documentos legales largos.

Aplica cinco estrategias de chunking al corpus chileno (16 docs):
  1. fixed   — ventana deslizante de caracteres con solape.
  2. simple  — bloques separados por línea en blanco (baseline de §1-3).
  3. structural — por encabezados del dominio (Artículo, Glosa, Título...).
  4. semantic — corta donde el coseno entre oraciones consecutivas cae.
  5. hierarchical — hijos = oraciones; padres = bloques estructurales.
  6. contextual — aproximación a late chunking (prepone contexto del doc).

Para cada estrategia mide: nº de chunks, longitud media, recall@k (denso),
y muestra el top-1 chunk para una misma query. Diagrama comparativo
recall vs longitud media en `diagrams/`.

Ejecutar (usa la caché de embeddings; nuevos chunks → API):
    uv run python 02-retrieval/code/04-chunking-estrategias.py
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
    Chunk,
    DenseRetriever,
    OpenAIEmbedder,
    ScoredDoc,
    contextual_chunk,
    fixed_chunk,
    hierarchical_chunk,
    load_corpus_chunks,
    semantic_chunk,
    simple_chunk,
    structural_chunk,
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


def chunk_stats(chunks: list[Chunk]) -> dict:
    lens = [len(c.text) for c in chunks]
    return {
        "n": len(chunks),
        "mean_chars": sum(lens) / len(lens),
        "min_chars": min(lens),
        "max_chars": max(lens),
    }


def build_chunks(name: str, embedder) -> list[Chunk]:
    if name == "fixed-400":
        return load_corpus_chunks(
            CORPUS_DIR, chunker=lambda t, doc_id: fixed_chunk(t, doc_id, size=400, overlap=50)
        )
    if name == "fixed-1000":
        return load_corpus_chunks(
            CORPUS_DIR, chunker=lambda t, doc_id: fixed_chunk(t, doc_id, size=1000, overlap=100)
        )
    if name == "simple":
        return load_corpus_chunks(CORPUS_DIR, chunker=simple_chunk)
    if name == "structural":
        return load_corpus_chunks(CORPUS_DIR, chunker=structural_chunk)
    if name == "semantic":
        return load_corpus_chunks(
            CORPUS_DIR,
            chunker=lambda t, doc_id: semantic_chunk(t, doc_id, embedder, threshold=0.55),
        )
    if name == "hierarchical":
        return load_corpus_chunks(CORPUS_DIR, chunker=hierarchical_chunk)
    if name == "contextual":
        return load_corpus_chunks(CORPUS_DIR, chunker=contextual_chunk)
    raise ValueError(name)


def main() -> None:
    embedder = OpenAIEmbedder(cache_path=CACHE)
    golden = json.loads(
        (ROOT / "01-evals" / "examples" / "golden-dataset-rag-fiscal.json").read_text(
            encoding="utf-8"
        )
    )["items"]

    estrategias = [
        "fixed-400",
        "fixed-1000",
        "simple",
        "structural",
        "semantic",
        "hierarchical",
        "contextual",
    ]
    chunks_by_strat: dict[str, list[Chunk]] = {}
    retr_by_strat: dict[str, DenseRetriever] = {}

    hr("1. Estadística por estrategia")
    print(f"  {'estrategia':14s} {'n':>5s} {'avg':>7s} {'min':>5s} {'max':>5s}")
    for name in estrategias:
        chs = build_chunks(name, embedder)
        chunks_by_strat[name] = chs
        st = chunk_stats(chs)
        print(
            f"  {name:14s} {st['n']:5d} {st['mean_chars']:7.0f} "
            f"{st['min_chars']:5d} {st['max_chars']:5d}"
        )

    hr("2. Recall@k denso por estrategia (golden, 25 queries con fuente)")
    rows = []
    for name in estrategias:
        retr = DenseRetriever(embedder).fit(chunks_by_strat[name])
        retr_by_strat[name] = retr
        r1 = recall_at_k(retr, golden, 1)
        r3 = recall_at_k(retr, golden, 3)
        r5 = recall_at_k(retr, golden, 5)
        rows.append((name, r1, r3, r5))
    print(f"  {'estrategia':14s} {'@1':>7s} {'@3':>7s} {'@5':>7s}")
    for name, r1, r3, r5 in rows:
        print(f"  {name:14s} {r1:7.3f} {r3:7.3f} {r5:7.3f}")
    print(
        f"\n  API calls esta corrida: {embedder.api_calls} "
        "(el resto sirvió desde caché)."
    )

    hr("3. Mismo query, distinta estrategia: ¿qué chunk top-1 devuelve?")
    q = "¿Cuántas USE recibe un alumno prioritario de 3º básico?"
    print(f'  Query: "{q}"')
    for name in estrategias:
        top = retr_by_strat[name].search(q, k=1)[0]
        text = " ".join(top.chunk.text.split())
        contiene = "✓" if "1,694" in text else "✗"
        print(f"\n  [{name}] doc={top.chunk.doc_id}  (contiene '1,694 USE': {contiene})")
        print(f"    score={top.score:.3f}  len={len(text)}  texto:")
        print(f"    {text[:240]}{'…' if len(text) > 240 else ''}")

    hr("4. Hierarchical: el truco de devolver el padre al generador")
    q = "declarar el IVA dentro de los primeros 20 días del mes siguiente"
    top = retr_by_strat["hierarchical"].search(q, k=1)[0]
    print(f'  Query: "{q}"')
    print(f"\n  HIJO (lo que se INDEXA y matchea) — len={len(top.chunk.text)}:")
    print(f"    {top.chunk.text}")
    parent = top.chunk.meta.get("parent_text", "(sin padre)")
    print(f"\n  PADRE (lo que se DEVUELVE al generador) — len={len(parent)}:")
    print(f"    {parent[:400]}{'…' if len(parent) > 400 else ''}")
    print(
        "\n  El hijo da precisión de match; el padre da contexto suficiente para responder."
    )

    hr("5. Late chunking: lo que no podemos hacer con esta API")
    print(
        "  True late chunking (Günther et al., 2024) embebe el documento ENTERO con un\n"
        "  encoder de contexto largo y luego mean-pool sobre los tokens de cada chunk.\n"
        "  Resultado: la representación de cada chunk 've' el documento completo.\n"
        "  Requiere acceso a token-level embeddings → no expuesto por la API de OpenAI."
    )
    print(
        "\n  Aproximación con la misma API: 'contextual chunking' (Anthropic, 2024) —\n"
        "  prepones una descripción del documento a cada chunk antes de embeberlo. Es\n"
        "  otra técnica para el mismo problema (chunks descontextualizados). Su efecto\n"
        "  se ve en la fila 'contextual' de la tabla de recall arriba."
    )

    # Diagrama: recall@3 vs longitud media de chunk, una etiqueta por estrategia.
    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    for name, r1, r3, r5 in rows:
        st = chunk_stats(chunks_by_strat[name])
        ax.scatter(st["mean_chars"], r3, s=80)
        ax.annotate(
            name,
            (st["mean_chars"], r3),
            textcoords="offset points",
            xytext=(8, 4),
            fontsize=9,
        )
    ax.set_xlabel("Longitud media del chunk (caracteres)")
    ax.set_ylabel("Recall@3 (denso, golden 25 queries)")
    ax.set_title("Estrategias de chunking: recall@3 vs longitud media del chunk")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    out = DIAGRAMS / "chunking-recall-vs-tamano.png"
    fig.savefig(out, dpi=120)
    print(f"\n  Diagrama guardado en: {out}")


if __name__ == "__main__":
    main()
