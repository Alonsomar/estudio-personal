"""§7 — Del grafo al retrieval: GraphRAG y su economía.

Produce los números de `theory/07-graphrag-economia.md`. Replica el paso de
indexación de GraphRAG (comunidades + resumen LLM por comunidad) sobre el
grafo normativo de §2, mide su costo real, y lo compara contra el costo de
responder las mismas competency questions por recorrido de grafo directo
(§2) y por SQL/metadata filter (02 §7).

    uv run python 05-ontologias/code/07-graphrag-economia.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "03-produccion" / "code"))

from ontology_lib import (  # noqa: E402
    GraphRAGIndexer,
    Norma,
    RelacionNormativa,
    alcance_transitivo,
    build_grafo_normativo,
    comunidades_del_grafo,
    vecinos_por_relacion,
    TipoRelacion,
)
from prod_lib import PRICING_USD_PER_M_TOKENS as PRICING  # noqa: E402

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)
DATA_PATH = Path(__file__).resolve().parent.parent / "examples" / "relaciones-manual.json"
CACHE_PATH = Path(__file__).resolve().parent.parent / "examples" / "cache-graphrag-comunidades.json"
MODEL = "gpt-4o-mini"


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def cargar_grafo():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    normas = [Norma(**n) for n in data["normas"]]
    relaciones = [RelacionNormativa(**r) for r in data["relaciones"]]
    return build_grafo_normativo(normas, relaciones), normas, relaciones


def demo_indexacion_graphrag(g, normas, relaciones, indexer: GraphRAGIndexer) -> None:
    """El paso de indexación: detectar comunidades y resumir cada una."""
    seccion("1. Indexación estilo GraphRAG: comunidades + resumen por LLM")

    comunidades = comunidades_del_grafo(g)
    print(f"Louvain detecta {len(comunidades)} comunidades sobre 37 nodos / 47 aristas.\n")

    por_id = {n.id: n for n in normas}
    for i, com in enumerate(comunidades):
        normas_com = [por_id[nid] for nid in com if nid in por_id]
        rel_com = [r for r in relaciones if r.origen in com and r.destino in com]
        resumen = indexer.resumir_comunidad(normas_com, rel_com)
        print(f"  Comunidad {i} ({len(com)} normas) — {resumen.tema}")
        print(f"      {resumen.resumen}")

    print(
        "\nEsto reproduce, a escala de este corpus, el paso que Microsoft GraphRAG\n"
        "hace sobre corpus reales: agrupar el grafo en comunidades y generar un\n"
        "resumen por comunidad ANTES de responder una sola query del usuario."
    )


def demo_costo_indexacion(indexer: GraphRAGIndexer) -> None:
    """El costo de ese paso, medido, no descrito."""
    seccion("2. El costo de la indexación, medido sobre este corpus")

    precio = PRICING[MODEL]
    costo = indexer.tokens_in / 1e6 * precio["in"] + indexer.tokens_out / 1e6 * precio["out"]
    print(f"Llamadas a la API:  {indexer.api_calls}")
    print(f"Tokens in/out:      {indexer.tokens_in:,} / {indexer.tokens_out:,}")
    print(f"Costo total:        ${costo:.4f}")
    print(
        "\nSobre 37 normas esto es centavos. La cita real: indexar un dataset legal\n"
        "de 5 GB con GraphRAG costó USD 33.000 en tokens de LLM en 2024 (entity\n"
        "extraction + relationship extraction + resúmenes jerárquicos de\n"
        "comunidad, pasando el corpus completo por el LLM varias veces). Para\n"
        "mediados de 2025, Microsoft Research había bajado ese costo al 0,1% de la\n"
        "cifra original (LazyGraphRAG, selección dinámica de comunidades) — la\n"
        "ola de optimización 2024-2026 ataca exactamente este problema.\n"
        "\nLa escala es lo que decide, no el método: a 37 normas la indexación es\n"
        "gratis; a 5 GB de expedientes legales, es un proyecto de infraestructura."
    )


def demo_costo_competency_questions(g) -> None:
    """El costo de responder las MISMAS preguntas por recorrido directo."""
    seccion("3. El costo de responder las competency questions de §2 sin indexar nada")

    print("Recorrido de grafo directo (sin comunidades, sin resúmenes, sin LLM):\n")
    import time

    t0 = time.perf_counter()
    mods = vecinos_por_relacion(g, "ley-02-ley-21210-modernizacion.txt", TipoRelacion.MODIFICA, "out")
    t1 = time.perf_counter()
    dependientes = alcance_transitivo(g, "ley-08-ley-20248-subvencion-preferencial.txt", direccion="in")
    t2 = time.perf_counter()

    print(f"  P1 (§2, 1 salto):        {len(mods)} resultados en {(t1 - t0) * 1000:.3f} ms, $0")
    print(f"  P4 (§2, multi-salto):    {len(dependientes)} resultados en {(t2 - t1) * 1000:.3f} ms, $0")
    print(
        "\nCero llamadas a un LLM. El grafo ya construido (§1-§2) responde ambas\n"
        "preguntas —de 1 salto y de varios— sin necesitar el paso de indexación\n"
        "de comunidades que GraphRAG clásico exige. La pregunta de esta sección no\n"
        "es 'grafo vs. sin grafo': es 'grafo simple vs. GraphRAG con comunidades'."
    )


def demo_referencia_02_7() -> None:
    """La comparación con SQL/metadata filter, tomada de 02 §7 sin repetir
    el trabajo — solo se cita el resultado ya medido."""
    seccion("4. Referencia: lo que 02 §7 ya midió para consultas factuales")

    print(f"{'estrategia':>28} | {'tiempo/query':>13} | {'$/query':>10} | {'$/1M queries':>14}")
    print("-" * 74)
    filas = [
        ("SQL puro", "~0.1 ms", "$0", "$0"),
        ("Vector denso (cacheado)", "~10 ms", "$0", "$0"),
        ("Vector denso (nueva)", "~100-500 ms", "~$10⁻⁶", "~$1-10"),
        ("Vector + extractor LLM", "+0.5-2 s", "~$10⁻³", "~$1.000"),
    ]
    for r in filas:
        print(f"{r[0]:>28} | {r[1]:>13} | {r[2]:>10} | {r[3]:>14}")

    print(
        "\nPara una competency question de 1-2 saltos ('¿qué reglamenta la Ley\n"
        "19.886?'), un WHERE en una tabla de relaciones cuesta lo mismo que el\n"
        "'SQL puro' de esta tabla: prácticamente cero. El grafo simple (§1-§2)\n"
        "cuesta lo mismo. Ninguno de los dos necesita el paso de comunidades."
    )


def grafico_comunidades(g) -> None:
    """El grafo coloreado por comunidad detectada — lo que GraphRAG resume,
    visualizado."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import networkx as nx

    comunidades = comunidades_del_grafo(g)
    color_por_nodo = {}
    paleta = ["#3498db", "#2ecc71", "#e74c3c", "#f39c12", "#9b59b6", "#1abc9c", "#e67e22"]
    for i, com in enumerate(comunidades):
        for nid in com:
            color_por_nodo[nid] = paleta[i % len(paleta)]

    fig, ax = plt.subplots(figsize=(11, 8.5))
    pos = nx.spring_layout(g, k=1.1, seed=7, iterations=200)
    node_colors = [color_por_nodo.get(n, "#bdc3c7") for n in g.nodes]
    nx.draw_networkx_nodes(g, pos, ax=ax, node_color=node_colors, node_size=380)
    nx.draw_networkx_edges(g, pos, ax=ax, alpha=0.3, width=0.7, arrows=True, arrowsize=6)
    labels = {n: n.split("-")[0][:4] for n in g.nodes}
    nx.draw_networkx_labels(g, pos, ax=ax, labels=labels, font_size=6)
    ax.set_title(
        f"{len(comunidades)} comunidades detectadas (Louvain) — cada una recibe\n"
        "un resumen de GraphRAG antes de responder ninguna query",
        fontsize=11,
    )
    ax.axis("off")

    fig.tight_layout()
    out = Path(__file__).resolve().parent.parent / "diagrams" / "comunidades-graphrag.png"
    fig.savefig(out, dpi=140, bbox_inches="tight")
    print(f"\n[diagrama] {out.relative_to(ROOT)}")


if __name__ == "__main__":
    log.info(f"Indexación estilo GraphRAG con {MODEL} (caché en {CACHE_PATH.name}).")
    g, normas, relaciones = cargar_grafo()
    indexer = GraphRAGIndexer(model=MODEL, cache_path=CACHE_PATH)
    demo_indexacion_graphrag(g, normas, relaciones, indexer)
    demo_costo_indexacion(indexer)
    demo_costo_competency_questions(g)
    demo_referencia_02_7()
    grafico_comunidades(g)
    print()
