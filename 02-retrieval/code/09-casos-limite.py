"""Sección 9 — Casos límite del dominio regulatorio chileno.

Demuestra los cuatro modos de falla que un RAG regulatorio enfrenta y que
ningún retriever genérico de §§1-7 resuelve sin tratamiento específico:

  A. Citas normativas — "artículo 5 de la Ley 20.730": no es búsqueda
     semántica, es lookup por referencia. Solución: extraer la cita y
     pre-filtrar los chunks.
  B. Tablas en PDFs — la tabla UTM 2024: el chunking naive la mete entera
     en un bloque, diluyendo la fila concreta. Solución: linealizar
     fila-a-chunk con contexto reconstruido.
  C. Sinonimia técnica — "USE" vs "unidad de subvención escolar", "DL 825"
     vs "Ley sobre IVA". Solución: diccionario de expansión léxica antes
     del retrieval.
  D. Versiones temporales — DL 825 antes vs después de la Ley 21.210. La
     misma query con distinta fecha pide distinta versión de la norma.
     Solución: metadata de vigencia y filtro temporal pre-retrieval.

Cierra con la síntesis: una arquitectura de referencia que combina §§1-8
con el ruteo por tipo de query que cada caso de §9 hace necesario.

Ejecutar:
    uv run python 02-retrieval/code/09-casos-limite.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrieval_lib import (  # noqa: E402
    DOC_METADATA,
    DOC_TEMPORAL,
    BM25Retriever,
    Chunk,
    CitationGuidedRetriever,
    DenseRetriever,
    OpenAIEmbedder,
    ScoredDoc,
    TemporalFilteredRetriever,
    expand_synonyms,
    extract_citations,
    linearize_utm_table,
    load_corpus_chunks,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.utils import get_project_root  # noqa: E402

ROOT = get_project_root()
CORPUS_DIR = ROOT / "shared" / "corpus_chileno"
EMB_CACHE = ROOT / "02-retrieval" / "examples" / "cache-embeddings" / "embeddings.npz"


def hr(title: str = "") -> None:
    print("\n" + "=" * 86)
    if title:
        print(title)
        print("=" * 86)


def show_top(results: list[ScoredDoc], k: int = 3) -> None:
    for rank, r in enumerate(results[:k], 1):
        snippet = " ".join(r.chunk.text.split())[:96]
        print(f"    {rank}. [{r.score:6.3f}] {r.chunk.chunk_id:46s} {snippet}…")


def main() -> None:
    chunks = load_corpus_chunks(CORPUS_DIR)
    embedder = OpenAIEmbedder(cache_path=EMB_CACHE)
    bm25 = BM25Retriever().fit(chunks)
    dense = DenseRetriever(embedder).fit(chunks)

    print(
        f"Corpus: {len(chunks)} chunks, "
        f"{len(set(c.doc_id for c in chunks))} docs."
    )

    # ============================================================ #
    hr("CASO A — Citas normativas: 'artículo 11 de la Ley 20.730'")
    # El artículo 11 de la Ley 20.730 (norma-01 chunk #14) es la disposición que
    # fija las multas. Pero TANTO BM25 como denso priorizan decreto-02 (el
    # reglamento), que CITA la ley y además tiene su propio "Artículo 20" sobre
    # sanciones. La query menciona "artículo 11" y "20.730" — el sistema debería
    # privilegiar el doc cuya norma ES la 20.730, no los reglamentos que la citan.
    query_a = "qué dice el artículo 11 de la Ley 20.730"
    cites = extract_citations(query_a)
    print(f"\n  Query: '{query_a}'")
    print(f"  Citas extraídas: {cites}")

    print("\n  (a) BM25 naive — falla: top-1 es el REGLAMENTO, no la ley.")
    show_top(bm25.search(query_a, k=3))
    print("\n  (b) Denso naive — falla igual: top-3 son TODOS de decreto-02.")
    show_top(dense.search(query_a, k=3))
    print(
        "\n  La query NO es semántica: el usuario citó la norma exacta. Tratarla"
        "\n  con cosenos hace que docs que repiten '20.730' (el reglamento) ganen"
        "\n  por frecuencia léxica, no por ser la fuente correcta."
    )

    print(
        "\n  (c) Citation-guided ingenuo (filtra a docs que citan 20.730 + BM25):"
    )
    cg_naive = CitationGuidedRetriever(
        chunks, base=bm25, doc_metadata=DOC_METADATA, prefer_primary=False
    )
    show_top(cg_naive.search(query_a, k=3))
    allowed = cg_naive._docs_for_citation(cites["leyes"])
    n_allowed_chunks = sum(1 for c in chunks if c.doc_id in allowed)
    print(
        f"\n    Universo reducido: {len(allowed)} docs / {n_allowed_chunks} chunks "
        f"(de {len(chunks)} totales).\n"
        "    Sigue ganando decreto-02 porque comparte el patrón 'artículo + número'.\n"
        "    El ruteo aisló el universo correcto, pero el doc PRIMARIO (la ley)\n"
        "    no recibe ningún boost frente a los que la citan."
    )

    print(
        "\n  (d) Citation-guided con prioridad al doc primario "
        "(PRIMARY_LAW_DOCS['20.730'] = norma-01):"
    )
    cg = CitationGuidedRetriever(chunks, base=bm25, doc_metadata=DOC_METADATA)
    show_top(cg.search(query_a, k=3))
    print(
        "\n    ↑ Top-1 es la cabecera de la ley; el top-2 ES 'Artículo 11º.- Las\n"
        "    infracciones... multa de 10 a 50 UTM' — el artículo correcto de la\n"
        "    ley correcta, finalmente en el top-3. El ruteo evolucionó de 'filtrar\n"
        "    el universo' a 'priorizar el doc que ES la norma'. El patrón normal\n"
        "    en RAG legal en producción es exactamente este: mantener un mapeo\n"
        "    cita→doc primario y privilegiar la fuente sobre los que la citan."
    )

    # ============================================================ #
    hr("CASO B — Tablas en PDFs: la UTM de septiembre 2024")
    # La tabla UTM bajo simple_chunk: el bloque entero (12 meses) es un chunk.
    # El embedding promedia 12 cifras y la query "UTM septiembre" no tiene un
    # objetivo limpio. Linealizar fila-a-chunk lo arregla.
    query_b = "valor UTM septiembre 2024"
    print(f"\n  Query: '{query_b}'")

    print("\n  (a) Denso sobre la tabla en simple_chunk (bloque único de 12 meses):")
    # Restringimos a chunks de la tabla para el demo
    tabla_chunks = [c for c in chunks if c.doc_id == "tabla-01-valores-tributarios-2024.txt"]
    dense_tabla_naive = DenseRetriever(embedder).fit(tabla_chunks)
    show_top(dense_tabla_naive.search(query_b, k=3))
    print(
        "    ↑ El chunk #2 es la tabla entera. El generador recibe 12 filas y\n"
        "    debe extraer la de septiembre — frágil para LLMs chicos o ventanas\n"
        "    de contexto ajustadas."
    )

    # Linealización: 1 chunk por fila con contexto reconstruido
    tabla_text = (CORPUS_DIR / "tabla-01-valores-tributarios-2024.txt").read_text(encoding="utf-8")
    rows = linearize_utm_table(tabla_text, "tabla-01-valores-tributarios-2024.txt")
    print(f"\n  (b) Linealizada: {len(rows)} chunks (uno por mes), con contexto:")
    print(f"     ej: '{rows[8].text}'")
    dense_lin = DenseRetriever(embedder).fit(rows)
    print("\n     Resultado denso sobre chunks linealizados:")
    show_top(dense_lin.search(query_b, k=3))
    print(
        "\n    ↑ El top-1 es la fila exacta. El generador recibe la frase \"En\n"
        "    septiembre de 2024 la UTM fue $66.362\" — auto-contenida. Es el mismo\n"
        "    fix que aplicaría un pipeline de ingesta con extractor estructurado,\n"
        "    pero linealizado a texto natural para no salirse del retrieval semántico."
    )

    # ============================================================ #
    hr("CASO C — Sinonimia técnica: 'USE' vs 'unidad de subvención escolar'")
    # El embedding de "USE" en text-embedding-3-small no fue entrenado masivamente
    # con texto regulatorio chileno: 'USE' puede colisionar con 'usar', 'usuario'.
    # El usuario que dice "USE" o "PRAIS" o "DL 825" merece que su sigla se
    # expanda antes del retrieval para que BM25 y el denso vean ambos.
    queries_c = [
        "USE para alumnos prioritarios en básica",
        "PRAIS",
        "qué dice el DL 825 sobre servicios",
    ]
    for q in queries_c:
        q_exp = expand_synonyms(q)
        print(f"\n  Query: '{q}'")
        print(f"  Expandida: '{q_exp}'")
        print("    Denso sin expandir:")
        show_top(dense.search(q, k=3))
        print("    Denso con expansión:")
        show_top(dense.search(q_exp, k=3))
    print(
        "\n  La sigla queda anexada a su forma extendida (no reemplazada): así el\n"
        "  retrieval matchea TANTO los docs que escriben 'USE' como los que\n"
        "  escriben 'unidad de subvención escolar'. Es un fix de centavos comparado\n"
        "  con fine-tunear embeddings de dominio, y para corpus de tamaño chileno\n"
        "  llega bastante lejos."
    )

    # ============================================================ #
    hr("CASO D — Versiones temporales: 'régimen IVA antes vs después de 2020'")
    # ley-01 (DL 825 texto previo, vigente hasta 2020-02-23) y ley-02 (Ley 21.210,
    # vigente desde 2020-02-24). Misma query con distinta fecha → distinto doc.
    query_d = "régimen de IVA a servicios prestados desde el extranjero"
    print(f"\n  Query: '{query_d}'")

    print("\n  (a) Denso sin filtro temporal (mezcla las dos versiones):")
    show_top(dense.search(query_d, k=5))

    print("\n  (b) Denso filtrado a la versión vigente al 2018-06-30:")
    tdr = TemporalFilteredRetriever(dense, temporal=DOC_TEMPORAL)
    res_2018 = tdr.search(query_d, k=5, date_iso="2018-06-30")
    show_top(res_2018)
    print("    ↑ ley-02 (Ley 21.210, vigente desde 2020) DESAPARECE del ranking.")

    print("\n  (c) Denso filtrado a la versión vigente al 2024-06-30:")
    res_2024 = tdr.search(query_d, k=5, date_iso="2024-06-30")
    show_top(res_2024)
    print("    ↑ ley-01 (DL 825 texto previo) DESAPARECE; ley-02 queda como ley aplicable.")
    print(
        "\n  Sin este filtro, un usuario que pregunta sobre el régimen aplicable a\n"
        "  una operación de 2018 recibe la Ley 21.210 (vigente 2020+) y obtiene\n"
        "  una respuesta legalmente incorrecta. Es el caso más caro de equivocarse\n"
        "  en RAG legal: la respuesta se ve verosímil pero está fuera de vigencia."
    )

    # ============================================================ #
    hr("SÍNTESIS — Arquitectura de referencia para RAG regulatorio")
    print(
        "  Un RAG regulatorio maduro NO es un retriever; es un ROUTER con varios:\n"
        "\n"
        "    1. Pre-procesado de query:\n"
        "       - extract_citations  → ¿hay 'Ley N°'?\n"
        "       - detect_temporal    → ¿hay fecha o 'en 20XX'?\n"
        "       - classify_query     → ¿factual (SQL), semántica, scope?\n"
        "       - expand_synonyms    → cerrar la brecha siglas↔texto\n"
        "\n"
        "    2. Ruteo:\n"
        "       cita normativa            → CitationGuidedRetriever (§9.A)\n"
        "       respuesta tabular/celda   → SQL sobre datos extraídos (§7)\n"
        "       query temporal            → TemporalFilteredRetriever (§9.D)\n"
        "       filtros duros (organismo) → FilteredDenseRetriever (§7)\n"
        "       query semántica           → Hybrid RRF (§3) + rerank (§6)\n"
        "       fuera de corpus           → módulo de abstención (no §1-9)\n"
        "\n"
        "    3. Reranking + chunk-level golden para medir (§8).\n"
        "\n"
        "  Cada caso límite de §9 es un router más. La complejidad bien colocada\n"
        "  ocurre ANTES del retrieval, no en un mejor embedder. Para corpus\n"
        "  regulatorio chileno con vocabulario fijo y citas explícitas, esto\n"
        "  rinde mucho más que cambiar de modelo."
    )


if __name__ == "__main__":
    main()
