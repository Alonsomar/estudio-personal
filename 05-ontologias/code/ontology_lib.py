"""ontology_lib — núcleo reutilizable de la masterclass 05-ontologias.

Acumula los componentes que las secciones introducen:

  §1  Modelo del clasificador presupuestario como property graph: nodos
      tipados (Partida...Asignación) y el parser que los extrae de los
      documentos de glosa del corpus.
  §2  Grafo normativo: vocabulario de relaciones tipadas (Norma,
      RelacionNormativa, TipoRelacion) y recorridos (vecinos_por_relacion,
      alcance_transitivo).
  §3  Esquema tipo SKOS (ConceptoSKOS) para medir la brecha entre niveles
      de formalismo sobre el mismo corpus.
  §4  Entity resolution de organismos: pipeline de dos niveles (diccionario
      exacto, luego similitud difusa como fallback).
  §5  Extracción automática con LLM + structured output (LLMExtractor),
      y resolución de identificadores de norma reutilizando el pipeline
      de §4.
  §6  Vigencia a nivel de artículo y bitemporalidad (ModificacionArticulo,
      texto_vigente, que_sabia_el_sistema): retoma el límite "documento
      reemplaza documento" que 02 §9 dejó abierto.
  §7  GraphRAG y su economía: comunidades_del_grafo (Louvain) +
      GraphRAGIndexer, réplica minimalista del paso de indexación de
      GraphRAG para medir su costo real sobre este corpus.

Diseño: un property graph con `networkx` + esquema Pydantic, sin base de
grafos dedicada ni razonador OWL (decisión justificada en §3). Mismo patrón
que `retrieval_lib.py`, `prod_lib.py` y `econ_lib.py`: un módulo, sin estado
global, testeable in-process.
"""

from __future__ import annotations

import re
import unicodedata
from enum import Enum
from pathlib import Path

import networkx as nx
from pydantic import BaseModel


def strip_accents(text: str) -> str:
    """Quita acentos/diacríticos (NFKD + filtrar combining marks). Duplicada
    a propósito desde `retrieval_lib.strip_accents`: es una función de tres
    líneas y evita acoplar el orden de sys.path entre módulos de
    masterclasses distintas."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )

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


# --------------------------------------------------------------------------- #
# §3 Cuánto formalismo comprar. Un esquema SKOS-like (jerarquía is-a +
# etiquetas alternativas, sin relaciones tipadas entre instancias) para medir
# CONCRETAMENTE la brecha entre taxonomía y ontología, en vez de solo
# describirla en prosa.
# --------------------------------------------------------------------------- #
class ConceptoSKOS(BaseModel):
    """Un concepto de un esquema tipo SKOS: jerarquía is-a + sinónimos, sin
    relaciones tipadas entre instancias. SKOS resuelve "¿qué ES-UN X?"; no
    puede resolver "¿qué NORMA REGLAMENTA a X?" — esa es la brecha exacta
    que justifica pasar a un property graph (§2)."""

    id: str
    pref_label: str
    alt_labels: list[str] = []
    broader: str | None = None  # id del concepto padre, o None si es raíz


def esquema_skos_tipos_norma() -> list[ConceptoSKOS]:
    """Jerarquía is-a de los géneros documentales del corpus (§2), como la
    modelaría un tesauro/SKOS: sin relaciones MODIFICA/REGLAMENTA, solo
    'es un tipo de'. Sirve para medir, no solo describir, la diferencia
    entre esto y el grafo normativo de §2."""
    return [
        ConceptoSKOS(id="norma", pref_label="Norma"),
        ConceptoSKOS(id="norma_legal", pref_label="Norma con rango legal", broader="norma"),
        ConceptoSKOS(
            id="norma_administrativa", pref_label="Norma administrativa", broader="norma"
        ),
        ConceptoSKOS(id="instrumento_presupuestario", pref_label="Instrumento presupuestario", broader="norma"),
        ConceptoSKOS(id="ley", pref_label="Ley", alt_labels=["DL", "DFL"], broader="norma_legal"),
        ConceptoSKOS(
            id="decreto", pref_label="Decreto", alt_labels=["DS", "decreto supremo", "decreto exento"],
            broader="norma_administrativa",
        ),
        ConceptoSKOS(id="circular", pref_label="Circular", broader="norma_administrativa"),
        ConceptoSKOS(
            id="resolucion", pref_label="Resolución", alt_labels=["res. exenta"],
            broader="norma_administrativa",
        ),
        ConceptoSKOS(
            id="oficio", pref_label="Oficio", alt_labels=["dictamen"], broader="norma_administrativa"
        ),
        ConceptoSKOS(id="glosa", pref_label="Glosa presupuestaria", broader="instrumento_presupuestario"),
    ]


def es_subconcepto_de(esquema: list[ConceptoSKOS], hijo_id: str, ancestro_id: str) -> bool:
    """Recorre la cadena `broader` para responder '¿es X un tipo de Y?'.
    Es TODO lo que SKOS puede razonar: jerarquía is-a, nada de relaciones
    tipadas entre instancias concretas."""
    por_id = {c.id: c for c in esquema}
    actual = por_id.get(hijo_id)
    visitados = 0
    while actual is not None and visitados < len(esquema) + 1:
        if actual.id == ancestro_id:
            return True
        actual = por_id.get(actual.broader) if actual.broader else None
        visitados += 1
    return False


# --------------------------------------------------------------------------- #
# §4 Identidad y llaves canónicas. Entity resolution: decidir cuándo dos
# menciones textuales distintas ("Dirección de Compras" y "CHILECOMPRA") son
# la MISMA entidad. Mismo problema que record linkage en microdatos
# administrativos — otro dominio, la misma disciplina.
# --------------------------------------------------------------------------- #
class Organismo(BaseModel):
    """Un organismo público, con su llave canónica y las variantes
    textuales bajo las que el corpus lo menciona.

    `id` es la llave canónica — el análogo, en este dominio, del RUT de un
    organismo o el `cut_comunal` de una comuna (doctrina del portfolio:
    nunca usar el nombre libre como identificador). `nombre_oficial` es la
    forma para mostrar; `variantes` son las formas bajo las que el corpus
    real menciona la misma entidad.
    """

    id: str
    nombre_oficial: str
    variantes: list[str] = []


def normalizar_nombre(texto: str) -> str:
    """Minúsculas + sin acentos + espacios colapsados. El primer nivel de
    resolución, determinista y gratis, antes de cualquier diccionario."""
    return " ".join(strip_accents(texto.lower()).split())


def resolver_organismo(
    mencion: str, catalogo: list[Organismo]
) -> str | None:
    """Resolución de Nivel 1: normalización + coincidencia exacta contra las
    variantes conocidas de cada organismo. Barato, determinista, sin falsos
    positivos — por eso va PRIMERO en el pipeline, antes de cualquier
    comparación difusa.

    Devuelve el `id` canónico o `None` si la mención no coincide con ninguna
    variante conocida (candidata a Nivel 2, o a que alguien la agregue al
    catálogo).
    """
    obj = normalizar_nombre(mencion)
    for org in catalogo:
        formas = [org.nombre_oficial, *org.variantes]
        if obj in {normalizar_nombre(f) for f in formas}:
            return org.id
    return None


def resolver_organismo_difuso(
    mencion: str, catalogo: list[Organismo], umbral: float = 0.5
) -> tuple[str | None, float]:
    """Resolución de Nivel 2: similitud de secuencia (difflib, stdlib) contra
    los nombres oficiales del catálogo. Es el fallback CARO y PROBABILÍSTICO
    — se usa solo cuando el Nivel 1 no encontró nada, nunca antes, porque
    puede producir falsos positivos con nombres institucionales que
    comparten prefijo o estructura ('Dirección de X Pública').

    Devuelve `(id_candidato, score)`; el llamador decide si el score alcanza
    para aceptar automáticamente o si hace falta revisión humana.
    """
    import difflib

    mejor_id: str | None = None
    mejor_score = 0.0
    for org in catalogo:
        score = difflib.SequenceMatcher(
            None, normalizar_nombre(mencion), normalizar_nombre(org.nombre_oficial)
        ).ratio()
        if score > mejor_score:
            mejor_score, mejor_id = score, org.id
    if mejor_score >= umbral:
        return mejor_id, mejor_score
    return None, mejor_score


def catalogo_organismos_corpus() -> list[Organismo]:
    """Catálogo curado a mano de los organismos que el corpus menciona con
    más de una forma textual, sobrevivido a un grep sistemático (no
    inventado): cada variante está tomada literalmente del texto real."""
    return [
        Organismo(
            id="dccp", nombre_oficial="Dirección de Compras y Contratación Pública",
            variantes=["Dirección de Compras", "CHILECOMPRA", "ChileCompra"],
        ),
        Organismo(
            id="sii", nombre_oficial="Servicio de Impuestos Internos",
            variantes=[],  # el corpus SIEMPRE usa la forma completa; sin variantes que resolver
        ),
        Organismo(
            id="cgr", nombre_oficial="Contraloría General de la República",
            variantes=["esta Contraloría"],
        ),
        Organismo(
            id="dipres", nombre_oficial="Dirección de Presupuestos",
            variantes=[],
        ),
        Organismo(
            id="minsal", nombre_oficial="Ministerio de Salud",
            variantes=[],
        ),
    ]


# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# §5 Extracción con LLM. Reemplaza la curación manual de §2 por un extractor
# automático + Pydantic structured output, y reutiliza el pipeline de
# resolución de identidad de §4 (aplicado acá a IDENTIFICADORES DE NORMA en
# vez de nombres de organismo — mismo problema, mismo mecanismo).
# --------------------------------------------------------------------------- #
class RelacionExtraida(BaseModel):
    """Lo que el LLM extrae de UN documento: la norma destino en texto
    libre —tal como el documento la menciona ("Ley Nº 21.210", "DL Nº
    825")—, no un `doc_id`. El LLM no conoce los nombres de archivo del
    corpus; resolver el identificador a un `doc_id` es un paso aparte
    (`resolver_identificador_norma`), deliberadamente separado de la
    extracción para poder medir el error de cada etapa por separado.
    """

    identificador_destino: str
    tipo: TipoRelacion
    fundamento: str


class ExtraccionDocumento(BaseModel):
    """La salida estructurada completa para un documento: cero o más
    relaciones detectadas."""

    relaciones: list[RelacionExtraida]


_PROMPT_EXTRACCION = """\
Eres un analista jurídico especializado en normativa chilena. Tu tarea es \
identificar TODAS las relaciones que el siguiente documento declara hacia \
OTRAS normas (leyes, decretos, circulares, resoluciones u oficios), \
clasificando cada relación según este vocabulario EXACTO:

- modifica: el texto declara que cambia, sustituye o incorpora artículos a \
otra norma (verbos: "modifícanse", "sustitúyese", "incorpórase", \
"introdúcense modificaciones").
- deroga: el texto declara que otra norma deja de regir ("derógase").
- reglamenta: el documento ES el reglamento de otra norma ("Aprueba \
Reglamento de la Ley Nº...", "reglamenta la Ley Nº...").
- interpreta: el documento imparte instrucciones o interpreta cómo aplicar \
otra norma, sin cambiarla ni ser su reglamento.
- aplica: el documento resuelve un caso concreto usando otra norma como \
fundamento (dictámenes, oficios que responden una consulta).
- cita: cualquier otra mención de una norma como referencia o fundamento \
("conforme a", "de conformidad con", "en virtud de", "establecido en").

Para cada relación detectada, extrae:
- identificador_destino: el identificador EXACTO de la norma destino tal \
como aparece en el texto (ej. "Ley Nº 21.210", "DL Nº 825", "Decreto \
Supremo Nº 250", "Circular Nº 42").
- tipo: uno de los seis valores del vocabulario de arriba.
- fundamento: la frase o cláusula del texto que sustenta la relación \
(cita textual breve, no un resumen).

Si el documento no menciona ninguna otra norma, devuelve una lista vacía. \
No inventes relaciones que el texto no declara explícitamente.

DOCUMENTO:
{texto}
"""


class LLMExtractor:
    """Extractor de relaciones normativas con salida estructurada (Pydantic)
    y caché en disco — mismo patrón que `LLMRewriter`/`LLMReranker` de
    `02-retrieval`: primera corrida llama a la API, corridas siguientes leen
    de caché, reproducibles sin API key."""

    def __init__(
        self, model: str = "gpt-4o-mini", cache_path: Path | None = None
    ) -> None:
        self.model = model
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache: dict[str, dict] = {}
        self.api_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self._load()

    def _load(self) -> None:
        import json as _json

        if self.cache_path and self.cache_path.exists():
            self._cache = _json.loads(self.cache_path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        import json as _json

        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            _json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _key(self, texto: str) -> str:
        import hashlib

        return hashlib.sha1(f"{self.model}\n{texto}".encode("utf-8")).hexdigest()

    def extraer(self, texto: str) -> ExtraccionDocumento:
        k = self._key(texto)
        if k in self._cache:
            return ExtraccionDocumento.model_validate(self._cache[k])

        from dotenv import load_dotenv

        load_dotenv()
        from openai import OpenAI

        client = OpenAI()
        prompt = _PROMPT_EXTRACCION.format(texto=texto)
        resp = client.chat.completions.parse(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format=ExtraccionDocumento,
            temperature=0.0,
        )
        resultado = resp.choices[0].message.parsed
        if resp.usage:
            self.tokens_in += resp.usage.prompt_tokens
            self.tokens_out += resp.usage.completion_tokens
        self.api_calls += 1
        self._cache[k] = resultado.model_dump(mode="json")
        self._save()
        return resultado


_NUMERO_RE = re.compile(r"(\d[\d.]*)")


def resolver_por_numero(identificador: str, normas: list[Norma]) -> str | None:
    """Nivel intermedio, específico del dominio: extrae el NÚMERO del
    identificador ('Decreto Ley Nº 825' -> '825') y matchea contra el número
    de los identificadores del catálogo.

    Es más seguro que la similitud difusa genérica para este dominio, y la
    razón es la misma que en §1 (UNSPSC es un código, no un nombre) y en §4
    (llave canónica, no texto libre): el número es la parte estable de un
    identificador legal chileno; el resto ('Decreto Ley' vs 'DL', 'de 1974')
    es ruido de formato. Comparar el número evita el falso positivo que la
    similitud de caracteres SÍ comete entre 'Ley Nº 18.695' y 'Ley Nº
    18.575' (0.85 de similitud, dos leyes completamente distintas) — porque
    acá '18.695' y '18.575' simplemente no son el mismo número, sin
    ambigüedad de umbral.
    """
    m = _NUMERO_RE.search(identificador)
    if not m:
        return None
    numero = m.group(1)
    numeros_normas = [(n, _NUMERO_RE.search(n.identificador)) for n in normas]
    candidatos = [n for n, nm in numeros_normas if nm and nm.group(1) == numero]
    if len(candidatos) == 1:
        return candidatos[0].id
    return None  # 0 candidatos, o número ambiguo (>1 norma con el mismo número): no arriesgar


def resolver_identificador_norma(
    identificador: str, normas: list[Norma], umbral_difuso: float = 0.85
) -> tuple[str | None, str]:
    """Resuelve un identificador en texto libre (lo que el LLM extrajo, ej.
    'Ley Nº 21.210') a un `doc_id` del corpus. Tres niveles, en orden de
    costo y riesgo crecientes — el mismo principio de §4 ('barato y
    determinista primero'), con un nivel intermedio nuevo que ese pipeline
    no necesitaba porque los nombres de organismo no tienen números:

    1. Diccionario exacto (§4, `resolver_organismo`).
    2. Coincidencia por NÚMERO (`resolver_por_numero`, específico de
       identificadores legales).
    3. Similitud difusa genérica (§4, `resolver_organismo_difuso`) — el
       último recurso, con el riesgo ya documentado en §4.

    Devuelve `(doc_id o None, nivel)` para poder medir cuánto trabajo hizo
    cada nivel.
    """
    catalogo = [Organismo(id=n.id, nombre_oficial=n.identificador) for n in normas]
    exacto = resolver_organismo(identificador, catalogo)
    if exacto is not None:
        return exacto, "exacto"
    por_numero = resolver_por_numero(identificador, normas)
    if por_numero is not None:
        return por_numero, "numero"
    difuso, score = resolver_organismo_difuso(identificador, catalogo, umbral=umbral_difuso)
    if difuso is not None:
        return difuso, "difuso"
    return None, "sin_match"


# --------------------------------------------------------------------------- #
# §6 Vigencia temporal a nivel de ARTÍCULO, y bitemporalidad. Retoma el
# límite que 02 §9 dejó explícito: "documento reemplaza documento" es
# demasiado grueso porque una ley modifica UN artículo de otra, no la norma
# completa. `RelacionNormativa` (§2) ya vive a nivel de documento; acá se
# agrega el nivel de artículo que ese modelo no tenía.
# --------------------------------------------------------------------------- #
class ModificacionArticulo(BaseModel):
    """Una modificación a un artículo específico de una norma, con DOS
    fechas independientes:

    - `valido_desde`: VIGENCIA — desde cuándo la modificación rige
      legalmente (puede ser posterior a la publicación: vacancia legis).
    - `registrado_el`: REGISTRO — cuándo ESTA ontología incorporó el dato.

    Confundir las dos es el error bitemporal clásico: un sistema construido
    en una fecha puede documentar vigencias de años antes, y un sistema de
    auditoría que no distinga "vigente desde" de "lo supimos desde" no puede
    responder honestamente qué sabía en qué momento.
    """

    norma_modificadora: str  # doc_id
    norma_modificada: str  # doc_id
    articulo: str
    valido_desde: str  # fecha ISO de vigencia legal
    registrado_el: str  # fecha ISO en que esta ontología incorporó el dato
    fundamento: str


def texto_vigente(
    norma_base: str,
    articulo: str,
    modificaciones: list[ModificacionArticulo],
    fecha_consulta: str,
) -> tuple[str, str | None]:
    """¿Qué norma define el texto vigente de `articulo` de `norma_base` en
    `fecha_consulta`? Devuelve `(doc_id_fuente, valido_desde o None)`.

    Recorre SOLO las modificaciones registradas para ESE artículo específico
    (no para el documento completo) y toma la más reciente cuya vigencia ya
    empezó en la fecha consultada. Si ninguna aplica, el artículo sigue
    regido por su texto original — que es exactamente el caso que un modelo
    a nivel de documento (`02 §9`) no puede expresar: un documento marcado
    "no vigente" en su totalidad, cuando en realidad solo UN artículo suyo
    fue modificado y el resto sigue rigiendo tal como se publicó.
    """
    aplicables = [
        m for m in modificaciones
        if m.norma_modificada == norma_base and m.articulo == articulo
        and m.valido_desde <= fecha_consulta
    ]
    if not aplicables:
        return norma_base, None
    ultima = max(aplicables, key=lambda m: m.valido_desde)
    return ultima.norma_modificadora, ultima.valido_desde


def que_sabia_el_sistema(
    modificaciones: list[ModificacionArticulo], fecha_corte: str
) -> list[ModificacionArticulo]:
    """La pregunta BITEMPORAL: no '¿qué era vigente?' sino '¿qué sabía ESTE
    sistema en `fecha_corte`?' — filtra por `registrado_el`, no por
    `valido_desde`. Dos preguntas distintas con respuestas distintas: una
    norma puede llevar años vigente y el sistema haberla incorporado recién
    ahora (exactamente el caso de este corpus, construido de una vez en
    2026 sobre normas de 1974 en adelante)."""
    return [m for m in modificaciones if m.registrado_el <= fecha_corte]


# --------------------------------------------------------------------------- #
# §7 GraphRAG y su economía. Réplica minimalista del paso de indexación de
# GraphRAG (Microsoft, 2024): detectar comunidades del grafo y resumir cada
# una con un LLM, ANTES de responder ninguna consulta. Mide el costo real
# sobre este corpus, en vez de describirlo en abstracto.
# --------------------------------------------------------------------------- #
def comunidades_del_grafo(g: nx.DiGraph, seed: int = 7) -> list[set[str]]:
    """Detecta comunidades con Louvain sobre la versión NO dirigida del
    grafo (estándar en detección de comunidades: la dirección de MODIFICA
    vs CITA no importa para agrupar, solo la densidad de conexión)."""
    return [set(c) for c in nx.community.louvain_communities(g.to_undirected(), seed=seed)]


class ResumenComunidad(BaseModel):
    """La salida del paso de indexación de GraphRAG: un resumen en
    lenguaje natural de qué trata un grupo de normas conectadas."""

    tema: str
    resumen: str


_PROMPT_RESUMEN_COMUNIDAD = """\
Eres un analista jurídico. A continuación tienes un grupo de normas \
chilenas conectadas entre sí (mismo "vecindario" tematico en un grafo de \
citas). Resume en 2-3 frases de que trata este grupo en su conjunto, \
como lo haria un indice tematico.

NORMAS DEL GRUPO:
{normas}

RELACIONES DENTRO DEL GRUPO:
{relaciones}
"""


class GraphRAGIndexer:
    """Replica minimalista del paso de indexacion de GraphRAG: un resumen
    por comunidad, con LLM y cache en disco -- mismo patron que
    `LLMExtractor` (S5). Existe para MEDIR el costo de ese paso sobre este
    corpus, no para usarlo en produccion."""

    def __init__(self, model: str = "gpt-4o-mini", cache_path: Path | None = None) -> None:
        self.model = model
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache: dict[str, dict] = {}
        self.api_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self._load()

    def _load(self) -> None:
        import json as _json

        if self.cache_path and self.cache_path.exists():
            self._cache = _json.loads(self.cache_path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        import json as _json

        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            _json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def resumir_comunidad(
        self, normas: list[Norma], relaciones: list[RelacionNormativa]
    ) -> ResumenComunidad:
        import hashlib

        texto_normas = "\n".join(f"- {n.identificador}: {n.titulo}" for n in normas)
        texto_relaciones = "\n".join(
            f"- {r.origen} --[{r.tipo.value}]--> {r.destino}" for r in relaciones
        ) or "(ninguna relacion interna al grupo)"
        prompt = _PROMPT_RESUMEN_COMUNIDAD.format(normas=texto_normas, relaciones=texto_relaciones)

        k = hashlib.sha1(f"{self.model}\n{prompt}".encode("utf-8")).hexdigest()
        if k in self._cache:
            return ResumenComunidad.model_validate(self._cache[k])

        from dotenv import load_dotenv

        load_dotenv()
        from openai import OpenAI

        client = OpenAI()
        resp = client.chat.completions.parse(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format=ResumenComunidad,
            temperature=0.0,
        )
        resultado = resp.choices[0].message.parsed
        if resp.usage:
            self.tokens_in += resp.usage.prompt_tokens
            self.tokens_out += resp.usage.completion_tokens
        self.api_calls += 1
        self._cache[k] = resultado.model_dump(mode="json")
        self._save()
        return resultado
