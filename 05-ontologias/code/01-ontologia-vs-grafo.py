"""§1 — Qué es una ontología y por qué ya construiste varias.

Produce los números y el diagrama que cita `theory/01-que-es-una-ontologia.md`.
Parsea el clasificador presupuestario chileno desde los documentos de glosa
del corpus y lo representa como property graph con networkx.

    uv run python 05-ontologias/code/01-ontologia-vs-grafo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ontology_lib import (  # noqa: E402
    NivelClasificador,
    descendientes_asignacion,
    monto_total,
    nodos_por_nivel,
    parse_clasificador_presupuestario,
)

from shared.utils import get_logger, get_project_root  # noqa: E402

log = get_logger(__name__)
CORPUS_DIR = get_project_root() / "shared" / "corpus_chileno"


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def demo_taxonomia_vs_tesauro_vs_ontologia() -> None:
    """Cuatro términos que se usan sueltos; acá con ejemplos concretos del
    corpus para que dejen de ser abstractos."""
    seccion("1. Cuatro términos, cuatro ejemplos del corpus")

    filas = [
        ("Taxonomía", "jerarquía is-a, sin relaciones tipadas",
         "UNSPSC en resolucion-01: 'familia' contiene 'clase' contiene 'producto'"),
        ("Tesauro", "sinónimos y términos relacionados, sin jerarquía estricta",
         "expand_synonyms de 02 §9: 'DIPRES' ~ 'Dirección de Presupuestos'"),
        ("Ontología", "entidades + relaciones TIPADAS + reglas de qué es válido",
         "Norma MODIFICA Norma, Decreto REGLAMENTA Ley (§2 lo formaliza)"),
        ("Grafo de conocimiento", "una ontología INSTANCIADA con datos reales",
         "el grafo de esta sección: 63 nodos reales del clasificador 2024"),
    ]
    for termino, definicion, ejemplo in filas:
        print(f"\n{termino}")
        print(f"  definición: {definicion}")
        print(f"  ejemplo:    {ejemplo}")

    print(
        "\nLa progresión importa: cada término AGREGA algo al anterior. Un tesauro\n"
        "sin jerarquía no responde '¿qué contiene qué?'. Una taxonomía sin\n"
        "relaciones tipadas no distingue MODIFICA de REGLAMENTA. Una ontología sin\n"
        "instanciar es un esquema vacío. El grafo de conocimiento es donde el\n"
        "trabajo de este módulo empieza a pagar."
    )


def demo_clasificador_es_ontologia() -> None:
    """El clasificador presupuestario, parseado y recorrido como grafo."""
    seccion("2. El clasificador presupuestario chileno, parseado como grafo")

    g = parse_clasificador_presupuestario(CORPUS_DIR)
    print(f"Nodos: {g.number_of_nodes()}  ·  Aristas CONTIENE: {g.number_of_edges()}\n")

    for nivel in NivelClasificador:
        n = len(nodos_por_nivel(g, nivel))
        print(f"  {nivel.value:>12}: {n:>3} nodos")

    print(
        "\nEsto NO es una estructura nueva que el módulo inventa: es el mismo\n"
        "clasificador que aparece en cada Ley de Presupuestos, escrito con la\n"
        "misma jerarquía que un contador público usa a diario. La ontología ya\n"
        "existía; lo que este script hace es hacerla EXPLÍCITA y RECORRIBLE."
    )
    return g


def demo_competency_question(g) -> None:
    """La pregunta que el grafo responde trivial y grep no puede."""
    seccion("3. Una competency question: '¿en qué gasta cada Partida?'")

    print(
        "Pregunta del dominio: para cada Partida, ¿cuántas Asignaciones tiene\n"
        "y cuánto suman? Es la pregunta más básica de un analista presupuestario,\n"
        "y NO tiene respuesta con grep sobre el texto plano — requeriría\n"
        "reconstruir a mano la jerarquía Partida→Capítulo→Programa→...→Asignación\n"
        "cada vez que se hace la pregunta.\n"
    )
    print(f"{'partida':>45} | {'asignaciones':>13} | {'monto total (miles $)':>22}")
    print("-" * 86)
    for p in nodos_por_nivel(g, NivelClasificador.PARTIDA):
        asigs = descendientes_asignacion(g, p.id)
        total = monto_total(g, p.id)
        nombre = f"{p.codigo} {p.nombre}"
        print(f"{nombre:>45} | {len(asigs):>13} | {total:>22,.0f}")

    print(
        "\nCon el grafo, la consulta es dos líneas de código (nx.descendants +\n"
        "una suma). Sin él, es un parser ad-hoc que hay que escribir de nuevo\n"
        "cada vez que cambia el formato del documento fuente."
    )


def demo_limite_honesto(g) -> None:
    """Dónde el parser de regex se queda corto, sin esconderlo."""
    seccion("4. Límite honesto: el parser de regex no alcanza para todo")

    educ = next(
        p for p in nodos_por_nivel(g, NivelClasificador.PARTIDA) if p.codigo == "09"
    )
    asigs = descendientes_asignacion(g, educ.id)
    print(
        f"Partida 09 (Educación) muestra {len(asigs)} asignación(es) en el grafo, "
        "pero el\ndocumento fuente (glosa-02-presupuesto-educacion.txt) contiene "
        "SEIS: cinco\nen una tabla ('Programa 20: Subvenciones...') y una en el "
        "formato lineal\nque el parser sí reconoce (JUNAEB).\n"
    )
    print(
        "El parser de esta sección reconoce el patrón lineal 'Asignación NNN -\n"
        "nombre' + 'Monto: $X miles', porque es determinista y rápido de escribir\n"
        "— apropiado para ILUSTRAR que el clasificador es una ontología. Pero el\n"
        "mismo corpus, escrito por el mismo autor, ya usa un segundo formato\n"
        "(tabla) para la misma información. Un parser de reglas se rompe cada\n"
        "vez que aparece una variante nueva de formato.\n"
        "\nEsto NO es un bug a esconder: es la razón concreta por la que §5 usa\n"
        "extracción con LLM en vez de reglas escritas a mano para el corpus\n"
        "completo — un extractor semántico no le importa si la info está en\n"
        "una lista o en una tabla."
    )


def grafico_jerarquia(g) -> None:
    """Diagrama de la jerarquía completa de la Partida 16 (Salud)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import networkx as nx

    salud_partida = next(
        n for n in g.nodes if g.nodes[n]["data"].nivel == NivelClasificador.PARTIDA
        and g.nodes[n]["data"].codigo == "16"
    )
    nodos_sub = {salud_partida} | nx.descendants(g, salud_partida)
    sub = g.subgraph(nodos_sub)

    colores = {
        NivelClasificador.PARTIDA: "#3498db",
        NivelClasificador.CAPITULO: "#2ecc71",
        NivelClasificador.PROGRAMA: "#f39c12",
        NivelClasificador.SUBTITULO: "#9b59b6",
        NivelClasificador.ITEM: "#1abc9c",
        NivelClasificador.ASIGNACION: "#e74c3c",
    }
    node_colors = [colores[sub.nodes[n]["data"].nivel] for n in sub.nodes]
    labels = {
        n: f"{sub.nodes[n]['data'].nivel.value[:4]}\n{sub.nodes[n]['data'].codigo}"
        for n in sub.nodes
    }

    fig, ax = plt.subplots(figsize=(11, 7))
    pos = _tree_layout(sub, salud_partida)
    nx.draw(
        sub, pos, ax=ax, with_labels=True, labels=labels, node_color=node_colors,
        node_size=1100, font_size=7, arrows=True, arrowsize=12, edge_color="#999",
    )
    ax.set_title("Clasificador presupuestario como grafo: Partida 16 (Salud)", fontsize=12)

    from matplotlib.patches import Patch
    legend = [Patch(facecolor=c, label=n.value) for n, c in colores.items()]
    ax.legend(handles=legend, loc="upper left", fontsize=8, bbox_to_anchor=(1.0, 1.0))

    fig.tight_layout()
    out = Path(__file__).resolve().parent.parent / "diagrams" / "clasificador-como-grafo.png"
    fig.savefig(out, dpi=130, bbox_inches="tight")
    print(f"\n[diagrama] {out.relative_to(ROOT)}")


def _tree_layout(g, root) -> dict:
    """Layout jerárquico simple por niveles (sin dependencia de graphviz)."""
    import networkx as nx

    niveles: dict[str, int] = {root: 0}
    orden_bfs = list(nx.bfs_tree(g, root))
    for n in orden_bfs:
        for pred in g.predecessors(n):
            if pred in niveles:
                niveles[n] = niveles[pred] + 1
    por_nivel: dict[int, list[str]] = {}
    for n, lvl in niveles.items():
        por_nivel.setdefault(lvl, []).append(n)
    pos = {}
    for lvl, nodos in por_nivel.items():
        for i, n in enumerate(nodos):
            pos[n] = (i - len(nodos) / 2, -lvl)
    return pos


if __name__ == "__main__":
    log.info("Parseando el clasificador presupuestario desde el corpus chileno.")
    demo_taxonomia_vs_tesauro_vs_ontologia()
    g = demo_clasificador_es_ontologia()
    demo_competency_question(g)
    demo_limite_honesto(g)
    grafico_jerarquia(g)
    print()
