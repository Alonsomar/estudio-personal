"""§2 — Modelado del dominio regulatorio chileno.

Produce los números que cita `theory/02-modelado-del-dominio.md`. Carga la
ontología curada a mano en `examples/relaciones-manual.json` y responde
competency questions por recorrido de grafo.

    uv run python 05-ontologias/code/02-grafo-normativo.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ontology_lib import (  # noqa: E402
    Norma,
    RelacionNormativa,
    TipoRelacion,
    alcance_transitivo,
    build_grafo_normativo,
    vecinos_por_relacion,
)

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)
DATA_PATH = Path(__file__).resolve().parent.parent / "examples" / "relaciones-manual.json"
CORPUS_DIR = ROOT / "shared" / "corpus_chileno"


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def cargar() -> tuple[list[Norma], list[RelacionNormativa], dict]:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    normas = [Norma(**n) for n in data["normas"]]
    relaciones = [RelacionNormativa(**r) for r in data["relaciones"]]
    return normas, relaciones, data["metadata"]


def demo_vocabulario(relaciones: list[RelacionNormativa]) -> None:
    """El vocabulario de relaciones, con su frecuencia real en el corpus."""
    seccion("1. El vocabulario de relaciones, con su frecuencia real")

    conteo = Counter(r.tipo.value for r in relaciones)
    print(f"{'relación':>12} | {'ocurrencias':>11} | ejemplo verificado")
    print("-" * 90)
    ejemplos = {r.tipo.value: r for r in relaciones}
    for tipo, n in conteo.most_common():
        r = ejemplos[tipo]
        origen = r.origen.split("-", 1)[0]
        destino = r.destino.split("-", 1)[0]
        print(f"{tipo:>12} | {n:>11} | {origen} --[{tipo}]--> {destino}  ({r.fundamento[:40]}...)")

    print(
        "\nCITA domina (la relación 'débil', sin implicancia sobre vigencia), pero\n"
        "MODIFICA y REGLAMENTA — las que sí cambian qué texto rige— son minoría\n"
        "numérica y máxima importancia jurídica. Colapsar todo a CITA perdería\n"
        "exactamente la distinción que le importa a un abogado o un auditor: si\n"
        "el DL 825 sigue vigente tal como fue escrito en 1974 o no."
    )


def demo_competency_questions(g) -> None:
    """Las preguntas de diseño, respondidas por recorrido de grafo."""
    seccion("2. Competency questions, respondidas por recorrido de grafo")

    print("P1 — ¿Qué normas modifica la Ley 21.210?")
    mods = vecinos_por_relacion(g, "ley-02-ley-21210-modernizacion.txt", TipoRelacion.MODIFICA, "out")
    for m in mods:
        print(f"    -> {m}")
    print(
        "    Una sola ley, DOS normas modificadas (DL 825 e DL 824) en artículos\n"
        "    distintos. Es el ejemplo real de por qué 'doc reemplaza doc' (02 §9)\n"
        "    es demasiado grueso: la 21.210 no reemplaza a ninguna de las dos.\n"
    )

    print("P2 — ¿Qué normas modifican al DL 825 (ley-01)?")
    mods_in = vecinos_por_relacion(g, "ley-01-dl-825-iva-base.txt", TipoRelacion.MODIFICA, "in")
    for m in mods_in:
        print(f"    <- {m}")

    print("\nP3 — ¿Qué documento reglamenta la Ley 19.886 (compras públicas)?")
    regl = vecinos_por_relacion(g, "ley-03-ley-19886-compras-publicas.txt", TipoRelacion.REGLAMENTA, "in")
    for r in regl:
        print(f"    <- {r}")

    print("\nP4 — Transitividad: ¿qué depende, directa o indirectamente, de la SEP")
    print("      (Ley 20.248), sin límite de saltos?")
    dependientes = alcance_transitivo(
        g, "ley-08-ley-20248-subvencion-preferencial.txt", direccion="in"
    )
    for d in sorted(dependientes):
        print(f"    <- {d}")
    print(
        f"\n    {len(dependientes)} documentos; los siete tienen una arista DIRECTA\n"
        "    a ley-08. P4 demuestra consulta estructurada, no transitividad. La\n"
        "    auditoría encontró que el único camino indirecto publicado era una\n"
        "    arista directa omitida del ground truth.\n"
        "\n    Un caso multi-hop genuino del grafo corregido es:\n"
        "      decreto-04 -> ley-06 -> ley-03 -> ley-07\n"
        "    (modificación presupuestaria -> Ley de Presupuestos -> Ley de\n"
        "    Compras -> Bases de la Administración). §8 lo evalúa en un golden\n"
        "    estructural separado."
    )


def demo_un_solo_salto_no_alcanza(g) -> None:
    """Por qué CITA sola no responde la pregunta de auditoría normativa."""
    seccion("3. Por qué un solo tipo de relación no alcanza")

    solo_cita = alcance_transitivo(
        g, "ley-01-dl-825-iva-base.txt", tipos=[TipoRelacion.CITA], direccion="in"
    )
    con_modifica = alcance_transitivo(
        g, "ley-01-dl-825-iva-base.txt", tipos=[TipoRelacion.CITA, TipoRelacion.MODIFICA], direccion="in"
    )
    print(f"Documentos que CITAN al DL 825, sin contar MODIFICA: {len(solo_cita)} -> {sorted(solo_cita)}")
    print(f"Documentos que citan O modifican, en cualquier número de saltos: "
          f"{len(con_modifica)} -> {sorted(con_modifica)}")
    print(
        "\nLa diferencia es 'ley-02' (Ley 21.210): MODIFICA al DL 825, no solo lo\n"
        "cita. Es la única norma del corpus cuya relación directa con el DL 825\n"
        "implica que el texto original cambió. El resto llega por CITA directa o\n"
        "transitiva; el dataset v1 subcontaba esas citas.\n"
        "\nUn grafo con una sola relación genérica ('se relaciona con') puede\n"
        "contar cuántos documentos mencionan al DL 825, pero no puede separar 'lo\n"
        "menciona' de 'depende de un cambio en él' — que es la pregunta que le\n"
        "importa a quien audita si una norma sigue vigente tal como fue escrita."
    )


def demo_esquema(normas: list[Norma], relaciones: list[RelacionNormativa]) -> None:
    """El catálogo de normas por tipo, como resumen del esquema."""
    seccion("4. El catálogo de normas, por tipo")

    conteo = Counter(n.tipo.value for n in normas)
    for tipo, n in conteo.most_common():
        print(f"  {tipo:>15}: {n:>2}")
    total_corpus = len(list(CORPUS_DIR.glob("*.txt")))
    fuera = sorted(p.name for p in CORPUS_DIR.glob("*.txt") if p.name not in {n.id for n in normas})
    print(f"\n{len(normas)} normas, {len(relaciones)} relaciones, {len(conteo)} géneros.")
    print(f"Cobertura del corpus: {len(normas)}/{total_corpus}; fuera: {fuera}.")


def grafico_grafo_normativo(g) -> None:
    """El grafo normativo completo, coloreado por tipo de norma y de relación."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import networkx as nx
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    colores_norma = {
        "ley": "#3498db", "decreto": "#2ecc71", "circular": "#9b59b6",
        "resolucion": "#f39c12", "oficio": "#e74c3c", "glosa": "#1abc9c",
        "diario_oficial": "#95a5a6", "tabla": "#34495e",
    }
    colores_relacion = {
        "modifica": "#e74c3c", "reglamenta": "#2ecc71", "interpreta": "#f39c12",
        "aplica": "#9b59b6", "cita": "#bdc3c7",
    }

    node_colors = [colores_norma[g.nodes[n]["data"].tipo.value] for n in g.nodes]
    edge_colors = [colores_relacion[g.edges[e]["tipo"].value] for e in g.edges]
    labels = {n: g.nodes[n]["data"].id.split("-")[0][:4] for n in g.nodes}

    fig, ax = plt.subplots(figsize=(13, 10))
    pos = nx.spring_layout(g, k=1.1, seed=7, iterations=200)
    nx.draw_networkx_nodes(g, pos, ax=ax, node_color=node_colors, node_size=450)
    nx.draw_networkx_labels(g, pos, ax=ax, labels=labels, font_size=6)
    for (u, v), color in zip(g.edges, edge_colors):
        width = 0.6 if color == "#bdc3c7" else 1.8
        nx.draw_networkx_edges(
            g, pos, ax=ax, edgelist=[(u, v)], edge_color=color, width=width,
            alpha=0.75 if width > 1 else 0.35, arrows=True, arrowsize=8,
            connectionstyle="arc3,rad=0.05",
        )
    ax.set_title(
        f"Grafo normativo del corpus chileno ({g.number_of_nodes()} normas, "
        f"{g.number_of_edges()} relaciones tipadas)",
        fontsize=12,
    )
    leg_nodos = [Patch(facecolor=c, label=t) for t, c in colores_norma.items()]
    leg_aristas = [Line2D([0], [0], color=c, lw=2, label=t) for t, c in colores_relacion.items()]
    leg1 = ax.legend(handles=leg_nodos, loc="upper left", fontsize=8, title="tipo de norma")
    ax.add_artist(leg1)
    ax.legend(handles=leg_aristas, loc="lower left", fontsize=8, title="tipo de relación")
    ax.axis("off")

    fig.tight_layout()
    out = Path(__file__).resolve().parent.parent / "diagrams" / "grafo-normativo.png"
    fig.savefig(out, dpi=140, bbox_inches="tight")
    print(f"\n[diagrama] {out.relative_to(ROOT)}")


if __name__ == "__main__":
    log.info("Cargando ontología curada a mano (verdad fundamental para §5).")
    normas, relaciones, metadata = cargar()
    g = build_grafo_normativo(normas, relaciones)
    print(f"\nGrafo: {g.number_of_nodes()} nodos, {g.number_of_edges()} aristas.")
    demo_esquema(normas, relaciones)
    demo_vocabulario(relaciones)
    demo_competency_questions(g)
    demo_un_solo_salto_no_alcanza(g)
    grafico_grafo_normativo(g)
    print()
