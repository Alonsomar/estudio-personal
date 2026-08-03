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


# --------------------------------------------------------------------------- #
# §2 El grafo normativo. Vocabulario de relaciones TIPADAS entre normas —lo
# que el clasificador presupuestario de §1 no necesitaba porque solo tenía
# una relación (CONTIENE). Diseñado a partir de las competency questions:
# primero se escribe qué preguntas debe responder el sistema, después se
# decide qué entidades y relaciones hacen falta (mismo método que 01 §4 usó
# para golden datasets).
# --------------------------------------------------------------------------- #
class TipoNorma(str, Enum):
    """Los géneros documentales que el corpus regulatorio chileno usa.

    No son sinónimos intercambiables: una Circular interpreta, un Decreto
    reglamenta, una Ley modifica. La distinción de género es lo primero que
    un extractor (§5) tiene que acertar para que el resto del esquema tenga
    sentido.
    """

    LEY = "ley"
    DECRETO = "decreto"
    CIRCULAR = "circular"
    RESOLUCION = "resolucion"
    OFICIO = "oficio"
    GLOSA = "glosa"
    DIARIO_OFICIAL = "diario_oficial"
    TABLA = "tabla"


class TipoRelacion(str, Enum):
    """Vocabulario de relaciones entre normas, extraído del propio corpus:
    son literalmente los verbos que las normas chilenas usan para referirse
    unas a otras ("modifícanse", "derógase", "reglamenta la Ley Nº...").

    Colapsar todo esto en una sola relación genérica CITA —la tentación
    obvia— pierde información con consecuencias jurídicas reales: que una
    norma MODIFIQUE a otra implica que el texto original cambió; que la
    REGLAMENTE implica que la norma reglamentada sigue vigente y la
    reglamentaria depende de ella; que la INTERPRETE implica que ninguna de
    las dos deja de regir por la existencia de la otra. Un sistema que solo
    sepa "A se relaciona con B" no puede responder "¿sigue vigente el texto
    original?", que es la pregunta que de verdad importa en este dominio.
    """

    MODIFICA = "modifica"
    DEROGA = "deroga"
    REGLAMENTA = "reglamenta"
    INTERPRETA = "interpreta"
    APLICA = "aplica"
    CITA = "cita"


class Norma(BaseModel):
    """Una norma del corpus: identidad mínima para ubicarla en el grafo."""

    id: str  # nombre de archivo — la llave canónica (ver §4)
    tipo: TipoNorma
    identificador: str  # "Ley Nº 21.210", "Circular Nº 42, de 2020"
    titulo: str


class RelacionNormativa(BaseModel):
    """Una arista tipada entre dos normas, con su fundamento textual.

    `fundamento` no es adorno: es lo que permite auditar la relación contra
    la fuente (la misma disciplina de trazabilidad de la doctrina del
    portfolio) y lo que en §5 se usa para medir si el extractor automático
    acertó no solo el tipo de relación sino el artículo correcto.
    """

    origen: str  # Norma.id
    tipo: TipoRelacion
    destino: str  # Norma.id
    fundamento: str


def build_grafo_normativo(
    normas: list[Norma], relaciones: list[RelacionNormativa]
) -> nx.DiGraph:
    """Arma el grafo dirigido a partir de un catálogo de normas y relaciones.

    Cada arista lleva su tipo y fundamento como atributos, así que el grafo
    resultante conserva toda la información de `RelacionNormativa` — no es
    una proyección con pérdida.
    """
    g = nx.DiGraph()
    for norma in normas:
        g.add_node(norma.id, data=norma)
    for rel in relaciones:
        g.add_edge(rel.origen, rel.destino, tipo=rel.tipo, fundamento=rel.fundamento)
    return g


def vecinos_por_relacion(
    g: nx.DiGraph, node_id: str, tipo: TipoRelacion, direccion: str = "out"
) -> list[str]:
    """Vecinos directos de `node_id` conectados por una relación de tipo
    `tipo`. `direccion="out"` sigue A-[tipo]->B; `"in"` sigue B-[tipo]->A.

    Es la primitiva detrás de cualquier competency question de un salto:
    "¿qué normas modifica X?" es `vecinos_por_relacion(g, X, MODIFICA, "out")`;
    "¿qué normas modifican a X?" es la misma llamada con `direccion="in"`.
    """
    edges = g.out_edges(node_id, data=True) if direccion == "out" else g.in_edges(node_id, data=True)
    return [
        (v if direccion == "out" else u)
        for u, v, data in edges
        if data.get("tipo") == tipo
    ]


def alcance_transitivo(
    g: nx.DiGraph,
    node_id: str,
    tipos: list[TipoRelacion] | None = None,
    direccion: str = "out",
) -> set[str]:
    """Todo lo alcanzable desde `node_id` siguiendo relaciones de los tipos
    dados (todas si `tipos` es None), en cualquier número de saltos.

    `direccion="out"` responde "¿qué alcanza X?" (descendientes: si X es una
    ley, qué reglamentos/circulares cuelgan de ella). `direccion="in"`
    responde la pregunta inversa y más frecuente en auditoría normativa:
    "¿qué documentos DEPENDEN, directa o transitivamente, de X?" — por
    ejemplo, si el artículo 8º letra n) del DL 825 cambiara, qué circulares,
    resoluciones y oficios quedarían potencialmente desactualizados.

    Es la operación que un filtro de metadatos de una sola columna (`02 §7`)
    no puede expresar sin una consulta recursiva — la pregunta central de §7
    cuando compara el grafo con metadata filtering.
    """
    if node_id not in g:
        return set()
    sub_edges = [
        (u, v) for u, v, data in g.edges(data=True)
        if tipos is None or data.get("tipo") in tipos
    ]
    sub = nx.DiGraph(sub_edges)
    if node_id not in sub:
        return set()
    return nx.descendants(sub, node_id) if direccion == "out" else nx.ancestors(sub, node_id)
