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

import hashlib
import json
import re
import unicodedata
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import networkx as nx
from pydantic import BaseModel


CACHE_FORMAT_VERSION = 2
DEFAULT_TARIFF_USD_PER_M = {"input": 0.15, "output": 0.60}


class OfflineCacheMiss(RuntimeError):
    """La operación requería API, pero el modo offline es el predeterminado."""


class LLMCacheEntry(BaseModel):
    """Contrato auditable compartido por §5, §7 y §9."""

    response: dict[str, Any]
    model_requested: str
    model_returned: str
    prompt_version: str
    schema_version: str
    temperature: float
    prompt_sha256: str
    tokens_input: int
    tokens_output: int
    tariff_usd_per_m: dict[str, float]
    historical_cost_usd: float
    replica: int = 0


def llm_cache_key(
    *,
    model: str,
    prompt: str,
    schema: type[BaseModel],
    schema_version: str,
    temperature: float,
    replica: int = 0,
) -> str:
    """Clave estable: cualquier cambio material invalida el caché."""
    payload = {
        "model": model,
        "prompt": prompt,
        "schema": schema.model_json_schema(),
        "schema_version": schema_version,
        "temperature": temperature,
        "replica": replica,
    }
    serializado = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def historical_cost(tokens_input: int, tokens_output: int) -> float:
    return (
        tokens_input / 1_000_000 * DEFAULT_TARIFF_USD_PER_M["input"]
        + tokens_output / 1_000_000 * DEFAULT_TARIFF_USD_PER_M["output"]
    )


def load_versioned_cache(path: Path | None) -> dict[str, LLMCacheEntry]:
    if path is None or not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("format_version") != CACHE_FORMAT_VERSION:
        raise ValueError(
            f"caché incompatible en {path}: se requiere formato v{CACHE_FORMAT_VERSION}"
        )
    return {
        key: LLMCacheEntry.model_validate(value)
        for key, value in raw.get("entries", {}).items()
    }


def save_versioned_cache(path: Path | None, entries: dict[str, LLMCacheEntry]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format_version": CACHE_FORMAT_VERSION,
        "entries": {
            key: entries[key].model_dump(mode="json") for key in sorted(entries)
        },
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


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
    monto_reportado_miles: float | None = None
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
_FILA_TABLA_RE = re.compile(
    r"^(?P<codigo>\d{3})\s+(?P<glosa>\d{2})\s+"
    r"(?P<nombre>.+?)\s{2,}(?P<monto>[\d.]+)$"
)
_TOTAL_PROGRAMA_RE = re.compile(r"^TOTAL\s+Programa\s+\d+\s+([\d.]+)$", re.IGNORECASE)

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

        # Algunas glosas presupuestarias expresan Asignaciones en una tabla
        # de ancho fijo, bajo el Subtítulo activo. Son exactamente los mismos
        # nodos del clasificador que la forma lineal; cambia solo la sintaxis.
        m = _FILA_TABLA_RE.match(line)
        if m:
            codigo = m.group("codigo")
            padre_id = ids_activos.get(NivelClasificador.SUBTITULO)
            if padre_id is None:
                continue
            idx = _ORDEN.index(NivelClasificador.ASIGNACION)
            ruta = [
                codigos_activos[n]
                for n in _ORDEN[:idx]
                if n in codigos_activos
            ]
            ruta.append(codigo)
            node_id = f"{doc_id}::{'/'.join(ruta)}"
            monto = float(m.group("monto").replace(".", ""))
            g.add_node(
                node_id,
                data=NodoClasificador(
                    id=node_id,
                    nivel=NivelClasificador.ASIGNACION,
                    codigo=codigo,
                    nombre=m.group("nombre").strip(),
                    doc_id=doc_id,
                    monto_miles=monto,
                    glosa_num=m.group("glosa"),
                ),
            )
            g.add_edge(padre_id, node_id, tipo="CONTIENE")
            continue

        m = _TOTAL_PROGRAMA_RE.match(line)
        if m:
            programa_id = ids_activos.get(NivelClasificador.PROGRAMA)
            if programa_id and programa_id in g.nodes:
                g.nodes[programa_id]["data"].monto_reportado_miles = float(
                    m.group(1).replace(".", "")
                )
            continue

        m = _MONTO_RE.match(line)
        if m:
            monto = float(m.group(1).replace(".", ""))
            # El corpus también reporta un monto directamente en Subtítulo
            # cuando no existe Ítem ni Asignación. Se asigna al nivel activo
            # más profundo, sea cual sea, en lugar de descartarlo.
            receptor_id = next(
                (ids_activos[n] for n in reversed(_ORDEN) if n in ids_activos),
                None,
            )
            if receptor_id and receptor_id in g.nodes:
                g.nodes[receptor_id]["data"].monto_miles = monto
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
    """Suma las hojas monetarias bajo un nodo, sin duplicar agregados.

    Un monto puede vivir en Asignación o directamente en Subtítulo. Si un
    nodo con monto tuviera descendientes monetarios, prevalecen las hojas;
    ``monto_reportado_miles`` nunca entra en la suma: solo reconcilia el
    agregado calculado contra el total declarado por la fuente.
    """
    if node_id not in g:
        return 0.0

    def subtotal(nid: str) -> float:
        hijos = list(g.successors(nid))
        subtotales = [subtotal(hijo) for hijo in hijos]
        if any(valor != 0.0 for valor in subtotales):
            return sum(subtotales)
        return g.nodes[nid]["data"].monto_miles or 0.0

    return subtotal(node_id)


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

    `fundamento` no es adorno: es la **cita literal** del documento origen
    que sustenta la arista, y es lo que permite auditarla contra la fuente
    (la misma disciplina de trazabilidad de la doctrina del portfolio). El
    invariante lo verifica `tests/test_ontology_lib.py`, que compara cada
    fundamento contra el texto real del corpus tras normalizar espacios y
    acentos — el corpus corta líneas, la cita no.

    `§5` compara la extracción automática contra este dataset por
    `(origen, tipo, destino)`; el fundamento sirve para auditar a mano cada
    caso, no entra en el cálculo de precisión/recall.
    """

    origen: str  # Norma.id
    tipo: TipoRelacion
    destino: str  # Norma.id
    fundamento: str


class CompetencyQuestion(BaseModel):
    """Pregunta estructural con esperados congelados y caminos auditables."""

    id: str
    question: str
    category: Literal["one_hop", "multi_hop", "negative"]
    target_node: str
    direction: Literal["in", "out"]
    relation_types: list[TipoRelacion]
    max_hops: int
    expected_doc_ids: list[str]
    witness_paths: list[list[str]]


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
            variantes=["Dirección de Compras", "CHILECOMPRA"],
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
        self,
        model: str = "gpt-4o-mini",
        cache_path: Path | None = None,
        *,
        allow_api: bool = False,
        max_api_calls: int = 10,
        max_cost_usd: float = 1.0,
    ) -> None:
        self.model = model
        self.cache_path = Path(cache_path) if cache_path else None
        self.allow_api = allow_api
        self.max_api_calls = max_api_calls
        self.max_cost_usd = max_cost_usd
        self._cache: dict[str, LLMCacheEntry] = {}
        self.api_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self._load()

    def _load(self) -> None:
        self._cache = load_versioned_cache(self.cache_path)

    def _save(self) -> None:
        save_versioned_cache(self.cache_path, self._cache)

    @property
    def historical_tokens(self) -> tuple[int, int]:
        return (
            sum(e.tokens_input for e in self._cache.values()),
            sum(e.tokens_output for e in self._cache.values()),
        )

    @property
    def historical_cost_usd(self) -> float:
        return sum(e.historical_cost_usd for e in self._cache.values())

    def extraer(self, texto: str) -> ExtraccionDocumento:
        prompt = _PROMPT_EXTRACCION.format(texto=texto)
        k = llm_cache_key(
            model=self.model,
            prompt=prompt,
            schema=ExtraccionDocumento,
            schema_version="extraccion-documento-v1",
            temperature=0.0,
        )
        if k in self._cache:
            return ExtraccionDocumento.model_validate(self._cache[k].response)

        if not self.allow_api:
            raise OfflineCacheMiss(
                f"cache miss de extracción ({k[:12]}); repita con allow_api=True "
                "solo durante la corrida controlada"
            )
        if self.api_calls >= self.max_api_calls:
            raise RuntimeError(f"límite de llamadas alcanzado: {self.max_api_calls}")
        costo_estimado = historical_cost(max(len(prompt) // 4, 1), 2_000)
        costo_actual = historical_cost(self.tokens_in, self.tokens_out)
        if costo_actual + costo_estimado > self.max_cost_usd:
            raise RuntimeError(f"presupuesto API excedido antes de llamar: USD {self.max_cost_usd}")

        from dotenv import load_dotenv

        load_dotenv()
        from openai import OpenAI

        client = OpenAI()
        resp = client.chat.completions.parse(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format=ExtraccionDocumento,
            temperature=0.0,
        )
        resultado = resp.choices[0].message.parsed
        tokens_in = resp.usage.prompt_tokens if resp.usage else 0
        tokens_out = resp.usage.completion_tokens if resp.usage else 0
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.api_calls += 1
        self._cache[k] = LLMCacheEntry(
            response=resultado.model_dump(mode="json"),
            model_requested=self.model,
            model_returned=resp.model,
            prompt_version="extraccion-v1",
            schema_version="extraccion-documento-v1",
            temperature=0.0,
            prompt_sha256=prompt_sha256(prompt),
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            tariff_usd_per_m=DEFAULT_TARIFF_USD_PER_M,
            historical_cost_usd=historical_cost(tokens_in, tokens_out),
        )
        self._save()
        return resultado


# El número de una norma chilena va SIEMPRE detrás de su designador de género
# ("Ley", "DL", "Decreto Supremo", "Circular"...), opcionalmente separado por
# alguna variante de "Nº". Anclar la extracción al designador es lo que
# distingue el número de la NORMA del número de un ARTÍCULO, que en el
# lenguaje jurídico lo precede casi siempre ("el artículo 9º de la Ley Nº
# 20.248"). Una regex que tomara el primer número del string devolvería "9".
_DESIGNADOR = (
    r"(?:ley(?:\s+n[uú]mero)?|dfl|d\.?f\.?l\.?|decreto\s+ley|d\.?l\.?|"
    r"decreto\s+supremo|d\.?s\.?|decreto(?:\s+exento)?|circular|"
    r"resoluci[oó]n(?:\s+exenta)?|oficio(?:\s+circular)?|dictamen)"
)
_NUMERO_NORMA_RE = re.compile(
    rf"\b{_DESIGNADOR}\s*(?:n\s*[º°ªo]?\.?\s*|n[úu]m\.?\s*)?(\d[\d.]*\d|\d)",
    re.IGNORECASE,
)

_REFERENCIA_NORMA_RE = re.compile(
    r"\b(?P<designador>ley|dfl|d\.?f\.?l\.?|decreto\s+ley|d\.?l\.?|"
    r"decreto\s+supremo|d\.?s\.?|decreto\s+exento|decreto|circular|"
    r"resoluci[oó]n\s+exenta|resoluci[oó]n|oficio\s+circular|oficio|dictamen)"
    r"\s*(?:n\s*[º°ªo]?\.?\s*|n[úu]m\.?\s*)?"
    r"(?P<numero>\d[\d.]*\d|\d)",
    re.IGNORECASE,
)


def _tipo_designador(texto: str) -> str:
    """Agrupa variantes tipográficas del género de una norma.

    El número por sí solo no identifica una norma: ``DFL Nº 2`` y
    ``Decreto Supremo Nº 2`` son instrumentos distintos. Esta clave se usa
    en el escáner de cobertura del ground truth para no fabricar aristas por
    una coincidencia numérica accidental.
    """
    t = strip_accents(texto.lower()).replace(".", "")
    if t.startswith(("decreto ley", "dl")):
        return "dl"
    if t.startswith(("decreto supremo", "ds")):
        return "ds"
    if t.startswith("decreto exento"):
        return "decreto_exento"
    if t.startswith("decreto"):
        return "decreto"
    if t.startswith("dfl"):
        return "dfl"
    if t.startswith("ley"):
        return "ley"
    if t.startswith("circular"):
        return "circular"
    if t.startswith("resolucion"):
        return "resolucion"
    if t.startswith("dictamen"):
        return "dictamen"
    return "oficio"


def menciones_normativas_catalogo(
    texto: str,
    normas: list[Norma],
    *,
    origen_id: str | None = None,
) -> set[str]:
    """Resuelve referencias explícitas del texto contra el catálogo.

    Es una red de seguridad para la curación manual, no un extractor
    semántico: solo cubre menciones que incluyen designador y número. Las
    glosas, tablas y extractos del Diario Oficial se excluyen como destinos
    porque su ``identificador`` suele contener el número de la ley de la que
    forman parte y no constituye una identidad normativa propia.
    """
    claves: dict[tuple[str, str], str] = {}
    for norma in normas:
        if norma.tipo in {TipoNorma.GLOSA, TipoNorma.TABLA, TipoNorma.DIARIO_OFICIAL}:
            continue
        match = _REFERENCIA_NORMA_RE.search(norma.identificador)
        if not match:
            continue
        clave = (
            _tipo_designador(match.group("designador")),
            match.group("numero").replace(".", ""),
        )
        if clave in claves and claves[clave] != norma.id:
            raise ValueError(f"identificador normativo ambiguo en catálogo: {clave}")
        claves[clave] = norma.id

    encontrados: set[str] = set()
    for match in _REFERENCIA_NORMA_RE.finditer(texto):
        clave = (
            _tipo_designador(match.group("designador")),
            match.group("numero").replace(".", ""),
        )
        destino = claves.get(clave)
        if destino is not None and destino != origen_id:
            encontrados.add(destino)
    return encontrados


def _numero_canonico(texto: str) -> str | None:
    """El número de norma que aparece en `texto`, sin separadores de miles.

    'Ley Nº 21.210', 'LEY 21210' y 'ley 21.210,' devuelven todos '21210': el
    punto es ruido tipográfico, no parte de la identidad. Es la misma lección
    de `§1` (UNSPSC es un código) y `§4` (llave canónica, nunca texto libre),
    un nivel más abajo.
    """
    m = _NUMERO_NORMA_RE.search(texto)
    if not m:
        return None
    return m.group(1).replace(".", "").rstrip(".")


def resolver_por_numero(identificador: str, normas: list[Norma]) -> str | None:
    """Nivel intermedio, específico del dominio: extrae el número de la norma
    ('Decreto Ley Nº 825' -> '825') y lo matchea contra el número de los
    identificadores del catálogo.

    Dos precisiones que esta función aprendió a golpes (auditoría 2026-08-04):

    1. **El número se ancla al designador**, no a la primera cifra del string.
       Con la versión anterior, 'el art. 12 del DL 825' resolvía a la Partida
       12 del presupuesto — un falso positivo silencioso y con etiqueta de
       nivel 'numero', o sea, presentado como resolución confiable.
    2. **El separador de miles se descarta** antes de comparar: 'Ley 21210' y
       'Ley Nº 21.210' son la misma norma escritas por dos redactores.

    Sigue siendo más seguro que la similitud de caracteres para este dominio
    —'Ley Nº 18.695' y 'Ley Nº 18.575' comparten 0.846 de similitud y son
    leyes distintas, pero 18695 != 18575 sin ambigüedad de umbral—, con la
    salvedad de que NO cubre por sí solo ese riesgo: el nivel difuso corre
    después igual (ver `resolver_identificador_norma`).
    """
    numero = _numero_canonico(identificador)
    if numero is None:
        return None
    candidatos = [n for n in normas if _numero_canonico(n.identificador) == numero]
    if len(candidatos) == 1:
        return candidatos[0].id
    return None  # 0 candidatos, o número ambiguo (>1 norma con el mismo número): no arriesgar


def resolver_identificador_norma(
    identificador: str,
    normas: list[Norma],
    umbral_difuso: float = 0.85,
    *,
    usar_numero: bool = True,
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
    if usar_numero:
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
class TipoCambioArticulo(str, Enum):
    CREA = "crea"
    MODIFICA = "modifica"
    DEROGA = "deroga"


class EstadoVigencia(str, Enum):
    NO_EXISTE = "no_existe"
    ORIGINAL = "original"
    MODIFICADO = "modificado"
    DEROGADO = "derogado"


class VersionArticulo(BaseModel):
    estado: EstadoVigencia
    fuente_doc_id: str | None
    vigente_desde: date | None


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
    valido_desde: date
    registrado_el: date
    fundamento: str
    tipo_cambio: TipoCambioArticulo = TipoCambioArticulo.MODIFICA


def _fecha_tipificada(fecha: str | date) -> date:
    if isinstance(fecha, date):
        return fecha
    try:
        return date.fromisoformat(fecha)
    except ValueError as exc:
        raise ValueError(f"fecha no ISO-8601: {fecha!r}") from exc


def texto_vigente(
    norma_base: str,
    articulo: str,
    modificaciones: list[ModificacionArticulo],
    fecha_consulta: str | date,
) -> VersionArticulo:
    """¿Qué norma define el texto vigente de `articulo` de `norma_base` en
    `fecha_consulta`? Devuelve `(doc_id_fuente, valido_desde)`.

    Recorre SOLO las modificaciones registradas para ESE artículo específico
    (no para el documento completo) y toma la más reciente cuya vigencia ya
    empezó en la fecha consultada. Tres respuestas posibles, no dos:

    - `(norma_modificadora, valido_desde)`: rige un texto modificado.
    - `(norma_base, None)`: rige el texto original — el caso que un modelo a
      nivel de documento (`02 §9`) no puede expresar, porque marca "no
      vigente" el archivo entero cuando solo un artículo cambió.
    - `(None, None)`: el artículo **no existe** en esa fecha. Ocurre cuando
      la única modificación que lo menciona lo crea (`crea_articulo=True`) y
      su vigencia todavía no empezó: el caso del art. 7º bis de la Ley 19.886
      antes del 11-12-2024, con la ley que lo crea ya publicada.
    """
    fecha = _fecha_tipificada(fecha_consulta)
    del_articulo = [
        m for m in modificaciones
        if m.norma_modificada == norma_base and m.articulo == articulo
    ]
    aplicables = [m for m in del_articulo if m.valido_desde <= fecha]
    if aplicables:
        ultima = max(aplicables, key=lambda m: m.valido_desde)
        if ultima.tipo_cambio == TipoCambioArticulo.DEROGA:
            return VersionArticulo(
                estado=EstadoVigencia.DEROGADO,
                fuente_doc_id=ultima.norma_modificadora,
                vigente_desde=ultima.valido_desde,
            )
        return VersionArticulo(
            estado=(
                EstadoVigencia.MODIFICADO
                if ultima.tipo_cambio == TipoCambioArticulo.MODIFICA
                else EstadoVigencia.ORIGINAL
            ),
            fuente_doc_id=ultima.norma_modificadora,
            vigente_desde=ultima.valido_desde,
        )
    if del_articulo and all(
        m.tipo_cambio == TipoCambioArticulo.CREA for m in del_articulo
    ):
        return VersionArticulo(
            estado=EstadoVigencia.NO_EXISTE,
            fuente_doc_id=None,
            vigente_desde=None,
        )
    return VersionArticulo(
        estado=EstadoVigencia.ORIGINAL,
        fuente_doc_id=norma_base,
        vigente_desde=None,
    )


def que_sabia_el_sistema(
    modificaciones: list[ModificacionArticulo], fecha_corte: str | date
) -> list[ModificacionArticulo]:
    """La pregunta BITEMPORAL: no '¿qué era vigente?' sino '¿qué sabía ESTE
    sistema en `fecha_corte`?' — filtra por `registrado_el`, no por
    `valido_desde`. Dos preguntas distintas con respuestas distintas: una
    norma puede llevar años vigente y el sistema haberla incorporado recién
    ahora (exactamente el caso de este corpus, construido de una vez en
    2026 sobre normas de 1974 en adelante)."""
    fecha = _fecha_tipificada(fecha_corte)
    return [m for m in modificaciones if m.registrado_el <= fecha]


# --------------------------------------------------------------------------- #
# §7 GraphRAG y su economía. Réplica minimalista del paso de indexación de
# GraphRAG (Microsoft, 2024): detectar comunidades del grafo y resumir cada
# una con un LLM, ANTES de responder ninguna consulta. Mide el costo real
# sobre este corpus, en vez de describirlo en abstracto.
# --------------------------------------------------------------------------- #
def comunidades_del_grafo(g: nx.DiGraph, seed: int = 7) -> list[list[str]]:
    """Detecta comunidades con Louvain sobre la versión NO dirigida del
    grafo (estándar en detección de comunidades: la dirección de MODIFICA
    vs CITA no importa para agrupar, solo la densidad de conexión).

    Devuelve **listas ordenadas**, no conjuntos, y en orden estable. No es
    cosmética: `GraphRAGIndexer` hashea el prompt para cachear el resumen de
    cada comunidad, y el prompt se construye recorriendo esta estructura. Con
    `set[str]`, el orden de iteración dependía del `PYTHONHASHSEED` —
    aleatorio por proceso—, así que cada corrida generaba una clave distinta,
    fallaba la caché y gastaba llamadas reales a la API. El caché comiteado
    llegó a tener 13 entradas para 7 comunidades por esta causa.
    """
    comunidades = nx.community.louvain_communities(g.to_undirected(), seed=seed)
    return sorted((sorted(c) for c in comunidades), key=lambda c: (-len(c), c[0]))


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

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        cache_path: Path | None = None,
        *,
        allow_api: bool = False,
        max_api_calls: int = 10,
        max_cost_usd: float = 1.0,
    ) -> None:
        self.model = model
        self.cache_path = Path(cache_path) if cache_path else None
        self.allow_api = allow_api
        self.max_api_calls = max_api_calls
        self.max_cost_usd = max_cost_usd
        self._cache: dict[str, LLMCacheEntry] = {}
        self.api_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self._load()

    def _load(self) -> None:
        self._cache = load_versioned_cache(self.cache_path)

    def _save(self) -> None:
        save_versioned_cache(self.cache_path, self._cache)

    @property
    def historical_tokens(self) -> tuple[int, int]:
        return (
            sum(e.tokens_input for e in self._cache.values()),
            sum(e.tokens_output for e in self._cache.values()),
        )

    @property
    def historical_cost_usd(self) -> float:
        return sum(e.historical_cost_usd for e in self._cache.values())

    def resumir_comunidad(
        self, normas: list[Norma], relaciones: list[RelacionNormativa]
    ) -> ResumenComunidad:
        normas = sorted(normas, key=lambda n: n.id)
        relaciones = sorted(relaciones, key=lambda r: (r.origen, r.tipo.value, r.destino))
        texto_normas = "\n".join(f"- {n.identificador}: {n.titulo}" for n in normas)
        texto_relaciones = "\n".join(
            f"- {r.origen} --[{r.tipo.value}]--> {r.destino}" for r in relaciones
        ) or "(ninguna relacion interna al grupo)"
        prompt = _PROMPT_RESUMEN_COMUNIDAD.format(normas=texto_normas, relaciones=texto_relaciones)

        k = llm_cache_key(
            model=self.model,
            prompt=prompt,
            schema=ResumenComunidad,
            schema_version="resumen-comunidad-v1",
            temperature=0.0,
        )
        if k in self._cache:
            return ResumenComunidad.model_validate(self._cache[k].response)

        if not self.allow_api:
            raise OfflineCacheMiss(
                f"cache miss de GraphRAG ({k[:12]}); use allow_api=True solo "
                "durante la corrida controlada"
            )
        if self.api_calls >= self.max_api_calls:
            raise RuntimeError(f"límite de llamadas alcanzado: {self.max_api_calls}")
        costo_estimado = historical_cost(max(len(prompt) // 4, 1), 1_000)
        if historical_cost(self.tokens_in, self.tokens_out) + costo_estimado > self.max_cost_usd:
            raise RuntimeError(f"presupuesto API excedido antes de llamar: USD {self.max_cost_usd}")

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
        tokens_in = resp.usage.prompt_tokens if resp.usage else 0
        tokens_out = resp.usage.completion_tokens if resp.usage else 0
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.api_calls += 1
        self._cache[k] = LLMCacheEntry(
            response=resultado.model_dump(mode="json"),
            model_requested=self.model,
            model_returned=resp.model,
            prompt_version="resumen-comunidad-v1",
            schema_version="resumen-comunidad-v1",
            temperature=0.0,
            prompt_sha256=prompt_sha256(prompt),
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            tariff_usd_per_m=DEFAULT_TARIFF_USD_PER_M,
            historical_cost_usd=historical_cost(tokens_in, tokens_out),
        )
        self._save()
        return resultado
