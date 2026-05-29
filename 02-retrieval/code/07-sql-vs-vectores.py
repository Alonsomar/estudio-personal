"""Sección 7 — Metadata filtering y retrieval estructurado: cuándo SQL gana.

La tesis: muchas "queries a un RAG" son consultas SQL disfrazadas. Cuando la
respuesta es un MONTO, UNA FECHA o UN ATRIBUTO, una tabla bien diseñada le
gana a cualquier vector store en precisión, costo, latencia y explicabilidad.

Demuestra cuatro casos sobre el corpus de 16 docs:
  A. "presupuesto de inmunizaciones 2024" — SQL exacto, vector necesita LLM.
  B. "valor UTM en septiembre 2024" — SQL exacto, vector pone fila incorrecta.
  C. "obligaciones de circulares SII sobre IVA" — filtro de metadata + vector
     concentra la búsqueda.
  D. "cómo se sanciona a un funcionario que esconde sus bienes" — SQL no puede
     responder; el vector gana limpio.

Ejecutar:
    uv run python 02-retrieval/code/07-sql-vs-vectores.py
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrieval_lib import (  # noqa: E402
    DOC_METADATA,
    DenseRetriever,
    FilteredDenseRetriever,
    OpenAIEmbedder,
    ScoredDoc,
    load_corpus_chunks,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.utils import get_project_root  # noqa: E402

ROOT = get_project_root()
CORPUS_DIR = ROOT / "shared" / "corpus_chileno"
EMB_CACHE = ROOT / "02-retrieval" / "examples" / "cache-embeddings" / "embeddings.npz"


# --------------------------------------------------------------------------- #
# Datos extraídos a mano de las glosas y de la tabla UTM. En producción esta
# extracción es la inversión inicial: se hace una vez y queda servida toda la
# vida útil del documento. Aquí mostramos lo que esa inversión devuelve.
# --------------------------------------------------------------------------- #
PRESUPUESTO_2024 = [
    # partida, ministerio, capitulo, programa, asignacion, descripcion, monto_miles
    (16, "Salud", 1, 1, 104, "Programa Nacional de Inmunizaciones",         198_547_320),
    (16, "Salud", 1, 1, 112, "Programa PRAIS",                                27_834_560),
    (16, "Salud", 2, 1,   1, "Aporte Fiscal Libre FONASA",                4_892_157_000),
    (16, "Salud", 2, 1, 205, "Equipamiento Hospitalario Red FONASA",         156_789_000),
    ( 9, "Educación", 1, 20,   1, "Subvención de Escolaridad",            7_842_310_500),
    ( 9, "Educación", 1, 20,   2, "Subvención Escolar Preferencial",      1_984_220_300),
    ( 9, "Educación", 1, 20,   3, "Subvención para Educación Especial",     612_450_180),
    ( 9, "Educación", 1, 20,   4, "Aporte por Gratuidad y Mantenimiento",   430_118_900),
    ( 9, "Educación", 1, 20,   5, "Subvención de Internado",                 58_903_220),
    ( 9, "Educación", 9,  1,  30, "Programa de Alimentación Escolar (PAE)", 1_012_567_400),
    (15, "Trabajo", 1, 5,   1, "Subsidio al Empleo Joven",                   89_420_150),
    (15, "Trabajo", 1, 5,   2, "Subsidio al Empleo de la Mujer",            124_785_300),
    (15, "Trabajo", 2, 1,   5, "Fiscalización Laboral",                      14_302_900),
]

# (mes_num, mes_nombre, año, utm_pesos)
UTM_2024 = [
    (1,  "Enero",      2024, 64216),
    (2,  "Febrero",    2024, 64666),
    (3,  "Marzo",      2024, 64343),
    (4,  "Abril",      2024, 64343),
    (5,  "Mayo",       2024, 64666),
    (6,  "Junio",      2024, 65052),
    (7,  "Julio",      2024, 65443),
    (8,  "Agosto",     2024, 65967),
    (9,  "Septiembre", 2024, 66362),
    (10, "Octubre",    2024, 66561),
    (11, "Noviembre",  2024, 66628),
    (12, "Diciembre",  2024, 66628),
]


def build_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        CREATE TABLE presupuesto_2024 (
            partida INTEGER, ministerio TEXT, capitulo INTEGER, programa INTEGER,
            asignacion INTEGER, descripcion TEXT, monto_miles INTEGER
        );
        CREATE TABLE valores_tributarios (
            mes INTEGER, mes_nombre TEXT, anio INTEGER, utm INTEGER
        );
        CREATE TABLE documentos (
            doc_id TEXT PRIMARY KEY, doc_type TEXT, organismo TEXT,
            tema TEXT, anio INTEGER
        );
        """
    )
    db.executemany("INSERT INTO presupuesto_2024 VALUES (?,?,?,?,?,?,?)", PRESUPUESTO_2024)
    db.executemany("INSERT INTO valores_tributarios VALUES (?,?,?,?)", UTM_2024)
    for doc_id, m in DOC_METADATA.items():
        db.execute(
            "INSERT INTO documentos VALUES (?,?,?,?,?)",
            (doc_id, m["doc_type"], m["organismo"], m["tema"], m["anio"]),
        )
    db.commit()
    return db


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


def show_top(results: list[ScoredDoc], k: int = 3) -> None:
    for rank, r in enumerate(results[:k], 1):
        snippet = " ".join(r.chunk.text.split())[:90]
        print(f"    {rank}. [{r.score:.3f}] {r.chunk.doc_id:38s} {snippet}…")


def main() -> None:
    chunks = load_corpus_chunks(CORPUS_DIR)
    embedder = OpenAIEmbedder(cache_path=EMB_CACHE)
    dense = DenseRetriever(embedder).fit(chunks)
    db = build_db()

    print(
        f"Corpus: {len(chunks)} chunks, {len(DOC_METADATA)} docs anotados.\n"
        f"Tablas SQL: presupuesto_2024 ({len(PRESUPUESTO_2024)} filas), "
        f"valores_tributarios ({len(UTM_2024)} filas), documentos "
        f"({len(DOC_METADATA)} filas)."
    )

    # ---------------------------------------------------------------- #
    hr("CASO A — SQL gana limpio: '¿Presupuesto de inmunizaciones 2024?'")
    # SQL
    t0 = time.perf_counter()
    sql = """SELECT ministerio, descripcion, monto_miles
             FROM presupuesto_2024
             WHERE descripcion LIKE '%Inmunizaciones%' AND ministerio='Salud'"""
    rows = db.execute(sql).fetchall()
    sql_ms = (time.perf_counter() - t0) * 1000
    print(f"\n  SQL ({sql_ms:.2f} ms, 0 tokens LLM):")
    for r in rows:
        print(f"    {r[0]} | {r[1]} | ${r[2]:,} miles")

    # Vector
    q = "presupuesto del programa nacional de inmunizaciones 2024"
    t0 = time.perf_counter()
    res = dense.search(q, k=3)
    v_ms = (time.perf_counter() - t0) * 1000
    print(f"\n  Vector (denso, {v_ms:.2f} ms + embedding API + LLM extractor):")
    show_top(res)
    print(
        "    ↑ El chunk contiene el monto, pero entregar el NÚMERO requiere otro\n"
        "    paso (regex frágil o llamada LLM extractora). El SQL responde el\n"
        "    valor directamente."
    )

    # ---------------------------------------------------------------- #
    hr("CASO B — SQL gana en filas exactas: '¿UTM en septiembre 2024?'")
    t0 = time.perf_counter()
    sql = "SELECT mes_nombre, anio, utm FROM valores_tributarios WHERE mes=9 AND anio=2024"
    row = db.execute(sql).fetchone()
    sql_ms = (time.perf_counter() - t0) * 1000
    print(f"\n  SQL ({sql_ms:.2f} ms):  {row[0]} {row[1]} → UTM = ${row[2]:,}")

    q = "valor de la UTM en septiembre de 2024"
    t0 = time.perf_counter()
    res = dense.search(q, k=3)
    v_ms = (time.perf_counter() - t0) * 1000
    print(f"\n  Vector ({v_ms:.2f} ms + embedding API):")
    show_top(res)
    print(
        "    ↑ El doc correcto sale top-1, pero el top-1 chunk no es la fila de\n"
        "    septiembre (es la UTA anual o la cabecera). El generador tendría\n"
        "    que leer toda la tabla del chunk para encontrar la fila."
    )

    # ---------------------------------------------------------------- #
    hr("CASO C — Metadata filter + vector: 'obligaciones de circulares SII sobre IVA'")
    q = "obligaciones del prestador en circulares del SII sobre IVA"

    # Sin filtro
    print("\n  Vector SIN filtro (16 docs candidatos):")
    show_top(dense.search(q, k=3))

    # Con filtro: solo circulares del SII sobre IVA
    allowed = {
        d for d, m in DOC_METADATA.items()
        if m["doc_type"] == "circular" and m["organismo"] == "SII" and m["tema"] == "IVA"
    }
    n_allowed_chunks = sum(1 for c in chunks if c.doc_id in allowed)
    filtered = FilteredDenseRetriever(dense, allowed)
    print(
        "\n  Vector CON pre-filtro WHERE doc_type='circular' "
        "AND organismo='SII' AND tema='IVA':"
    )
    print(f"  ({len(allowed)} docs / {n_allowed_chunks} chunks pasan el filtro)")
    show_top(filtered.search(q, k=3))
    print(
        "\n    ↑ Pre-filtrar elimina circulares de Renta, leyes y glosas: el coseno\n"
        "    se calcula solo sobre el subespacio relevante. Equivalente a un WHERE\n"
        "    en pgvector ANTES del kNN."
    )

    # ---------------------------------------------------------------- #
    hr("CASO D — SQL no puede: 'cómo se sanciona a un funcionario que esconde sus bienes'")
    print(
        "\n  SQL: no hay tabla de sanciones extraída (lo serio sería extraer\n"
        "  art. 18-19 de Ley 20.880 a una tabla; aquí no lo hicimos). El SQL\n"
        "  literalmente no tiene de dónde sacar la respuesta."
    )
    q = "cómo se sanciona a un funcionario que esconde sus bienes"
    print(f'\n  Vector ("{q}"):')
    show_top(dense.search(q, k=3))
    print(
        "\n    ↑ Aquí el vector gana sin discusión: la query es semántica\n"
        "    ('esconde bienes' = 'omisión inexcusable de información'). Para esto\n"
        "    se diseñaron los embeddings."
    )

    # ---------------------------------------------------------------- #
    hr("COSTO Y LATENCIA: el escalado decide")
    print(
        "  Por query, en órdenes de magnitud:\n"
        "    SQL puro:          ~0.1 ms        0 tokens         $0\n"
        "    Vector denso:      ~10-100 ms     1 embed (~50 tk) ~$10⁻⁶\n"
        "    Vector + extractor LLM: +0.5-2 s  ~500 tk + ~50 tk ~$10⁻³\n"
        "\n  En 1 millón de queries:\n"
        "    SQL puro:          ~0.1 s acumulados        $0\n"
        "    Vector denso:      ~horas + indexing        ~$1-10\n"
        "    Vector + extractor:~días + indexing         ~$1.000\n"
        "\n  Para queries factuales sobre datos estructurados, el SQL no es\n"
        "  conservador: es el diseño correcto. Reservá el vector para lo que\n"
        "  el SQL no puede expresar."
    )

    db.close()


if __name__ == "__main__":
    main()
