"""ontology_lib — núcleo reutilizable de la masterclass 05-ontologias.

Acumula los componentes que las secciones introducen:

  §1  Modelo del clasificador presupuestario como property graph: nodos
      tipados (Partida...Asignación) y el parser que los extrae de los
      documentos de glosa del corpus.

Diseño: un property graph con `networkx` + esquema Pydantic, sin base de
grafos dedicada ni razonador OWL (decisión justificada en §3). Mismo patrón
que `retrieval_lib.py`, `prod_lib.py` y `econ_lib.py`: un módulo, sin estado
global, testeable in-process.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

import networkx as nx
from pydantic import BaseModel

# --------------------------------------------------------------------------- #
# §1 El clasificador presupuestario como ontología de facto. Seis niveles
# jerárquicos con una relación única (CONTIENE) — la ontología más simple
# posible, y la que el corpus ya trae hecha en texto plano.
# --------------------------------------------------------------------------- #
class NivelClasificador(str, Enum):
    """Los seis niveles del clasificador presupuestario chileno.

    El orden de la clase ES la jerarquía: cada nivel contiene al siguiente.
    Partida > Capítulo > Programa > Subtítulo > Ítem > Asignación.
    """

    PARTIDA = "partida"
    CAPITULO = "capitulo"
    PROGRAMA = "programa"
    SUBTITULO = "subtitulo"
    ITEM = "item"
    ASIGNACION = "asignacion"


class NodoClasificador(BaseModel):
    """Un nodo del clasificador presupuestario, con su procedencia.

    `id` es la llave canónica del nodo en el grafo: se construye a partir de
    la ruta completa (partida/capítulo/programa/...) para que dos nodos del
    mismo código en partidas distintas no colisionen (ej. "Subtítulo 24"
    existe en la Partida 16 y en la Partida 05, y son asignaciones distintas).
    """

    id: str
    nivel: NivelClasificador
    codigo: str
    nombre: str
    doc_id: str
    monto_miles: float | None = None
    glosa_num: str | None = None
    glosa_texto: str | None = None


# Regex por nivel. El texto del corpus usa dos convenciones distintas para
# el mismo tipo de jerarquía: "NIVEL NN: nombre" (Partida, Capítulo) y
# "Nivel NN - nombre" (Subtítulo, Ítem, Asignación) — herencia real del
# formato de la Ley de Presupuestos, no un descuido del corpus sintético.
_PATTERNS: dict[NivelClasificador, re.Pattern] = {
    NivelClasificador.PARTIDA: re.compile(r"^PARTIDA\s+(\d+):\s*(.+)$"),
    NivelClasificador.CAPITULO: re.compile(r"^CAP[ÍI]TULO\s+(\d+):\s*(.+)$"),
    NivelClasificador.PROGRAMA: re.compile(r"^Programa\s+(\d+):\s*(.+)$"),
    NivelClasificador.SUBTITULO: re.compile(r"^Subt[íi]tulo\s+(\d+)\s*-\s*(.+)$"),
    NivelClasificador.ITEM: re.compile(r"^[ÍI]tem\s+(\d+)\s*-\s*(.+)$"),
    NivelClasificador.ASIGNACION: re.compile(r"^Asignaci[óo]n\s+(\d+)\s*-\s*(.+)$"),
}
_MONTO_RE = re.compile(r"^Monto:\s*\$([\d.]+)\s*miles")
_GLOSA_RE = re.compile(r"^Glosa\s+(\d+):\s*(.+)$")

# Orden jerárquico, usado para saber qué niveles "cierra" una línea nueva.
_ORDEN = list(NivelClasificador)


def parse_clasificador_presupuestario(
    corpus_dir: Path, filenames: list[str] | None = None
) -> nx.DiGraph:
    """Extrae el clasificador presupuestario de los documentos `glosa-*.txt`
    del corpus y lo representa como un grafo dirigido de contención.

    Cada nodo es un `NodoClasificador` (guardado en el atributo `data` del
    nodo del grafo); cada arista es CONTIENE, del nivel superior al inferior.
    Es, literalmente, una ontología de un solo tipo de relación — la más
    simple que existe, y la razón por la que nadie la llama "ontología" en
    el trabajo cotidiano de finanzas públicas.
    """
    if filenames is None:
        filenames = sorted(p.name for p in corpus_dir.glob("glosa-*.txt"))

    g = nx.DiGraph()
    for filename in filenames:
        _parse_one(corpus_dir / filename, g)
    return g


def _parse_one(path: Path, g: nx.DiGraph) -> None:
    doc_id = path.name
    lines = path.read_text(encoding="utf-8").splitlines()

    # Pila de códigos CRUDOS activos por nivel (no de node_ids): al ver una
    # línea de nivel N, todo lo que estaba activo en niveles > N deja de ser
    # el contexto vigente. El id de un nodo es la ruta de códigos desde la
    # partida — entity resolution por CONSTRUCCIÓN (ver §4): en vez de
    # resolver colisiones de "Subtítulo 24" entre partidas después, el id
    # incluye la ruta completa para que no puedan colisionar nunca.
    codigos_activos: dict[NivelClasificador, str] = {}
    ids_activos: dict[NivelClasificador, str] = {}

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        matched = False
        for nivel, pat in _PATTERNS.items():
            m = pat.match(line)
            if not m:
                continue
            codigo, nombre = m.group(1), m.group(2).strip()

            idx_nivel = _ORDEN.index(nivel)
            # El padre es el ancestro activo MÁS CERCANO, no necesariamente el
            # nivel inmediatamente superior: el corpus real salta niveles (una
            # Asignación puede colgar directo de un Subtítulo sin Ítem
            # intermedio). Tratar el nivel superior como obligatorio dejaría
            # nodos huérfanos — el mismo tipo de heterogeneidad de formato que
            # motiva la extracción con LLM en §5 en vez de regex a mano.
            padre_id = next(
                (ids_activos[n] for n in reversed(_ORDEN[:idx_nivel]) if n in ids_activos),
                None,
            )

            ruta = [codigos_activos[n] for n in _ORDEN[:idx_nivel] if n in codigos_activos]
            ruta.append(codigo)
            node_id = f"{doc_id}::{'/'.join(ruta)}"

            # Limpiar niveles hijos: un Programa nuevo cierra el Subtítulo /
            # Ítem / Asignación previos como contexto activo.
            for n in _ORDEN[idx_nivel + 1:]:
                codigos_activos.pop(n, None)
                ids_activos.pop(n, None)
            codigos_activos[nivel] = codigo
            ids_activos[nivel] = node_id

            g.add_node(
                node_id,
                data=NodoClasificador(
                    id=node_id, nivel=nivel, codigo=codigo, nombre=nombre, doc_id=doc_id
                ),
            )
            if padre_id is not None:
                g.add_edge(padre_id, node_id, tipo="CONTIENE")
            matched = True
            break

        if matched:
            continue

        m = _MONTO_RE.match(line)
        if m:
            monto = float(m.group(1).replace(".", ""))
            asign_id = ids_activos.get(NivelClasificador.ASIGNACION)
            if asign_id and asign_id in g.nodes:
                g.nodes[asign_id]["data"].monto_miles = monto
            continue

        m = _GLOSA_RE.match(line)
        if m:
            asign_id = ids_activos.get(NivelClasificador.ASIGNACION)
            if asign_id and asign_id in g.nodes:
                g.nodes[asign_id]["data"].glosa_num = m.group(1)
                g.nodes[asign_id]["data"].glosa_texto = m.group(2)
            continue


def nodos_por_nivel(g: nx.DiGraph, nivel: NivelClasificador) -> list[NodoClasificador]:
    """Todos los nodos de un nivel dado, en el orden en que se insertaron."""
    return [g.nodes[n]["data"] for n in g.nodes if g.nodes[n]["data"].nivel == nivel]


def descendientes_asignacion(g: nx.DiGraph, node_id: str) -> list[NodoClasificador]:
    """Todas las Asignaciones bajo un nodo dado (Partida, Capítulo o Programa).

    Es la consulta que responde una *competency question* típica del
    dominio: "¿en qué se gasta el presupuesto de la Partida 16?" — trivial
    como recorrido de grafo, imposible de responder con grep sin reconstruir
    a mano la jerarquía que el grafo ya tiene explícita.
    """
    vistos = nx.descendants(g, node_id) if node_id in g else set()
    return [
        g.nodes[n]["data"]
        for n in vistos
        if g.nodes[n]["data"].nivel == NivelClasificador.ASIGNACION
    ]


def monto_total(g: nx.DiGraph, node_id: str) -> float:
    """Suma el monto de todas las Asignaciones bajo un nodo. Recorrido de
    grafo + agregación: la operación que un clasificador plano en texto no
    puede hacer sin parsearse a sí mismo primero."""
    return sum(a.monto_miles or 0.0 for a in descendientes_asignacion(g, node_id))
