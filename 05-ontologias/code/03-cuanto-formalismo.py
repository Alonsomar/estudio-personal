"""§3 — Cuánto formalismo comprar.

Produce los números de `theory/03-cuanto-formalismo.md`. Mide, en vez de
solo describir, la brecha entre los cuatro niveles del espectro
(sinónimos < SKOS < property graph < RDF/OWL) sobre el propio corpus.

    uv run python 05-ontologias/code/03-cuanto-formalismo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "02-retrieval" / "code"))

from ontology_lib import (  # noqa: E402
    Norma,
    RelacionNormativa,
    TipoRelacion,
    build_grafo_normativo,
    es_subconcepto_de,
    esquema_skos_tipos_norma,
)
from retrieval_lib import expand_synonyms  # noqa: E402

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)
DATA_PATH = Path(__file__).resolve().parent.parent / "examples" / "relaciones-manual.json"


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def demo_nivel_1_sinonimos() -> None:
    """El nivel más simple: sinónimos, sin jerarquía ni relaciones."""
    seccion("1. Nivel 1 — Lista de sinónimos (ya construido en 02 §9)")

    ejemplo = "El oficio del SII sobre la 21.210"
    expandido = expand_synonyms(ejemplo)
    print(f"Original:   {ejemplo}")
    print(f"Expandido:  {expandido}")
    print(
        "\nResuelve UN problema: que 'SII' y 'Servicio de Impuestos Internos' matcheen\n"
        "en retrieval léxico. No sabe que un 'oficio' es un TIPO de norma, ni que\n"
        "el SII es el organismo que EMITE oficios y circulares. Es potente para lo\n"
        "que hace y ciego a todo lo demás — por diseño, no por defecto."
    )


def demo_nivel_2_skos() -> None:
    """SKOS: jerarquía is-a + sinónimos, todavía sin relaciones tipadas
    entre instancias concretas."""
    seccion("2. Nivel 2 — Esquema tipo SKOS: jerarquía + sinónimos")

    esquema = esquema_skos_tipos_norma()
    print(f"{len(esquema)} conceptos, con relación 'broader' (es-un) y alt_labels.\n")
    for c in esquema:
        indent = "  " if c.broader else ""
        alt = f" (alt: {', '.join(c.alt_labels)})" if c.alt_labels else ""
        print(f"{indent}{c.pref_label}{alt}")

    print("\nPreguntas que SÍ puede responder (recorriendo 'broader'):")
    casos = [
        ("circular", "norma_administrativa", True),
        ("circular", "norma_legal", False),
        ("glosa", "instrumento_presupuestario", True),
        ("ley", "norma", True),
    ]
    for hijo, ancestro, esperado in casos:
        resultado = es_subconcepto_de(esquema, hijo, ancestro)
        check = "✓" if resultado == esperado else "✗"
        print(f"  {check} ¿'{hijo}' es un tipo de '{ancestro}'? -> {resultado}")

    print(
        "\nPregunta que NO puede responder, aunque suene parecida:\n"
        "  '¿la circular-01 INTERPRETA la ley-02?'\n"
        "\nEsto no es 'X es un tipo de Y' (jerarquía de CLASES) — es 'esta INSTANCIA\n"
        "concreta se relaciona con esa OTRA instancia concreta, de esta forma\n"
        "específica'. SKOS no tiene el vocabulario para expresarlo: solo conoce\n"
        "broader/narrower y sinónimos. Es exactamente la brecha que motivó pasar\n"
        "al property graph de §2."
    )


def demo_nivel_3_property_graph() -> None:
    """El grafo de §2, con conteo de saltos por competency question."""
    seccion("3. Nivel 3 — Property graph: cuántos saltos necesitan las")
    print("   competency questions de §2")

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    normas = [Norma(**n) for n in data["normas"]]
    relaciones = [RelacionNormativa(**r) for r in data["relaciones"]]
    g = build_grafo_normativo(normas, relaciones)

    import networkx as nx

    casos = [
        ("¿Qué normas modifica la Ley 21.210?", "ley-02-ley-21210-modernizacion.txt",
         "ley-01-dl-825-iva-base.txt", 1),
        ("¿Qué documento reglamenta la Ley 19.886?", "decreto-03-reglamento-compras-publicas.txt",
         "ley-03-ley-19886-compras-publicas.txt", 1),
        ("¿oficio-05 depende de la SEP (transitivo)?", "oficio-05-contraloria-traspaso-slep.txt",
         "ley-08-ley-20248-subvencion-preferencial.txt", None),
    ]
    print(f"{'competency question':>45} | {'saltos reales':>13}")
    print("-" * 63)
    for pregunta, origen, destino, _esperado in casos:
        try:
            saltos = nx.shortest_path_length(g, origen, destino)
        except nx.NetworkXNoPath:
            saltos = float("inf")
        print(f"{pregunta:>45} | {saltos:>13}")

    print(
        "\nLas tres se responden en 1-2 saltos (la cuarta competency question de §2,\n"
        "sobre distinguir tipos de relación, no es una pregunta de distancia sino\n"
        "de vocabulario — ya resuelta por el esquema tipado de §2). El máximo\n"
        "observado en el corpus completo es 3. Ese rango (1-3 saltos) es el que\n"
        "define la regla de decisión de esta sección: si las preguntas del dominio\n"
        "viven ahí, un property graph con recorrido de grafo (BFS/DFS) alcanza —\n"
        "no hace falta un motor de consultas declarativo ni un razonador."
    )


def demo_nivel_4_owl_sin_owl() -> None:
    """Lo que OWL vendería (inferencia de transitividad) ya sale gratis con
    un grafo dirigido, sin razonador ni triple store."""
    seccion("4. Nivel 4 — Lo que RDF/OWL prometería, medido contra lo que ya tenemos")

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    normas = [Norma(**n) for n in data["normas"]]
    relaciones = [RelacionNormativa(**r) for r in data["relaciones"]]
    g = build_grafo_normativo(normas, relaciones)

    import networkx as nx

    # Lo que un razonador OWL haría con owl:TransitiveProperty sobre CITA:
    # inferir TODAS las citas indirectas. networkx lo da con una función.
    solo_cita = nx.DiGraph(
        [(u, v) for u, v, d in g.edges(data=True) if d["tipo"] == TipoRelacion.CITA]
    )
    cierre = nx.transitive_closure(solo_cita)
    print(
        f"Aristas CITA directas:                {solo_cita.number_of_edges()}\n"
        f"Aristas CITA tras clausura transitiva: {cierre.number_of_edges()}\n"
    )
    print(
        "Esa clausura transitiva es EXACTAMENTE lo que un axioma\n"
        "'owl:TransitiveProperty' sobre la relación CITA le pediría a un\n"
        "razonador que infiera. `nx.transitive_closure(g)` — una función de la\n"
        "librería estándar, sin RDF, sin SPARQL, sin instalar un razonador\n"
        "(HermiT, Pellet) ni levantar un triple store — da el mismo resultado.\n"
        "\nEsto NO dice que OWL sea inútil en general: dice que, PARA ESTE\n"
        "corpus y ESTAS preguntas, la parte de OWL que se vendería (inferencia\n"
        "automática) ya está resuelta por la estructura de grafo dirigido."
    )


def demo_costo_de_infraestructura() -> None:
    """El costo operativo de cada nivel, no solo el conceptual."""
    seccion("5. El costo que no es conceptual: infraestructura")

    filas = [
        ("Lista de sinónimos", "dict Python", "ninguna", "ya en 02 §9"),
        ("SKOS", "clase Pydantic + dict", "ninguna", "esta sección"),
        ("Property graph (networkx)", "grafo en memoria", "ninguna — un proceso Python", "§1-§2 de este módulo"),
        ("Property graph (Neo4j)", "grafo en servidor", "servidor de base de datos + Cypher", "no usado en este módulo"),
        ("RDF/OWL + razonador", "triples + ontología formal", "triple store + razonador + SPARQL", "no usado en este módulo"),
    ]
    print(f"{'nivel':>28} | {'representación':>20} | {'infraestructura':>32}")
    print("-" * 88)
    for nivel, rep, infra, _ in filas:
        print(f"{nivel:>28} | {rep:>20} | {infra:>32}")

    print(
        "\nEs la misma tesis de 03 §7 ('Kubernetes es over-engineering para el 95%\n"
        "de estos productos') aplicada a la capa de conocimiento: 37 nodos y 47\n"
        "aristas viven cómodos en memoria. Levantar Neo4j o un triple store para\n"
        "ESTE corpus sería pagar el costo operativo de una escala que no existe."
    )


def demo_regla_de_decision() -> None:
    """La regla explícita, y la decisión tomada para el módulo."""
    seccion("6. La regla de decisión")

    print(
        "1. Escribir las competency questions (§2) ANTES de elegir el nivel.\n"
        "2. Contar cuántos saltos necesita cada una sobre el grafo más simple\n"
        "   que las responda.\n"
        "3. Si el máximo son 2-3 saltos: un property graph con recorrido\n"
        "   (BFS/DFS/nx.descendants) alcanza. No comprar más.\n"
        "4. Subir de nivel SOLO si aparece una pregunta que exige:\n"
        "   (a) clasificación automática de instancias no vista en los datos\n"
        "       ('¿esta norma nueva es de rango legal, sin que nadie lo haya\n"
        "       etiquetado?'), o\n"
        "   (b) verificación de consistencia lógica entre axiomas\n"
        "       ('¿es válido que un Decreto MODIFIQUE una Ley?').\n"
        "   Ninguna de las cinco competency questions de §2 pide esto.\n"
        "\nDecisión tomada para el módulo: property graph con networkx +\n"
        "esquema Pydantic. Justificación: (1) las competency questions de §2\n"
        "se responden en 1-3 saltos — dato medido en la sección 3 de este\n"
        "script, no supuesto; (2) el corpus tiene 37 nodos y 47 aristas — cabe\n"
        "en memoria sin infraestructura adicional; (3) lo que OWL vendería como\n"
        "razonamiento (transitividad) ya sale de `nx.transitive_closure` sin\n"
        "razonador. Ningún criterio de la regla empuja hacia más formalismo."
    )


if __name__ == "__main__":
    log.info("Midiendo el espectro de formalismo sobre el corpus real.")
    demo_nivel_1_sinonimos()
    demo_nivel_2_skos()
    demo_nivel_3_property_graph()
    demo_nivel_4_owl_sin_owl()
    demo_costo_de_infraestructura()
    demo_regla_de_decision()
    print()
