"""Tests de `05-ontologias/code/ontology_lib.py` y de la ontología curada.

Corren sin API keys ni red. Dos familias:

1. **Invariantes del dataset curado** (`examples/relaciones-manual.json`). El
   más importante es que cada `fundamento` sea una **cita literal** del
   documento origen: es lo que hace auditable la ontología y lo que una
   auditoría de 2026-08-04 encontró incumplido en la v1 del archivo (0 de 47
   fundamentos eran literales; todos eran paráfrasis del curador).
2. **Casos borde de las funciones de resolución y vigencia**, incluidos los
   que los datos actuales no ejercitan pero que rompen con entradas apenas
   distintas.
"""

import json
import importlib.util
import re
import subprocess
import sys
import unicodedata
from datetime import date
from pathlib import Path

import networkx as nx
import pytest
from ontology_lib import (
    ModificacionArticulo,
    NivelClasificador,
    Norma,
    RelacionNormativa,
    TipoRelacion,
    TipoCambioArticulo,
    EstadoVigencia,
    CompetencyQuestion,
    ExtraccionDocumento,
    LLMExtractor,
    OfflineCacheMiss,
    alcance_transitivo,
    build_grafo_normativo,
    comunidades_del_grafo,
    monto_total,
    menciones_normativas_catalogo,
    resolver_identificador_norma,
    resolver_por_numero,
    nodos_por_nivel,
    parse_clasificador_presupuestario,
    catalogo_organismos_corpus,
    normalizar_nombre,
    llm_cache_key,
    texto_vigente,
)

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "shared" / "corpus_chileno"
DATASET = ROOT / "05-ontologias" / "examples" / "relaciones-manual.json"
GOLDEN = ROOT / "05-ontologias" / "examples" / "golden-ontology.json"


def _normalizar(texto: str) -> str:
    """Minúsculas, sin acentos, espacios colapsados.

    El corpus corta líneas a ~72 columnas; una cita literal de más de una
    línea no coincide como subcadena sin colapsar los saltos primero.
    """
    desc = unicodedata.normalize("NFKD", texto)
    sin_acentos = "".join(c for c in desc if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sin_acentos.lower()).strip()


@pytest.fixture(scope="module")
def dataset() -> dict:
    return json.loads(DATASET.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def normas(dataset) -> list[Norma]:
    return [Norma(**n) for n in dataset["normas"]]


@pytest.fixture(scope="module")
def relaciones(dataset) -> list[RelacionNormativa]:
    return [RelacionNormativa(**r) for r in dataset["relaciones"]]


@pytest.fixture(scope="module")
def grafo(normas, relaciones) -> nx.DiGraph:
    return build_grafo_normativo(normas, relaciones)


# --------------------------------------------------------------------------- #
# 1. Invariantes del dataset curado
# --------------------------------------------------------------------------- #
def test_todo_fundamento_es_cita_literal_del_origen(dataset):
    """El invariante que hace auditable la ontología.

    Si este test falla, el `fundamento` dejó de ser verificable contra la
    fuente y la trazabilidad que §2 promete es falsa.
    """
    textos = {p.name: _normalizar(p.read_text(encoding="utf-8")) for p in CORPUS.glob("*.txt")}
    no_literales = [
        (r["origen"], r["fundamento"])
        for r in dataset["relaciones"]
        if _normalizar(r["fundamento"]) not in textos.get(r["origen"], "")
    ]
    assert not no_literales, f"{len(no_literales)} fundamentos no son literales: {no_literales[:3]}"


def test_normas_y_relaciones_apuntan_a_documentos_reales(dataset):
    en_disco = {p.name for p in CORPUS.glob("*.txt")}
    ids = {n["id"] for n in dataset["normas"]}
    assert ids <= en_disco, f"normas sin documento: {sorted(ids - en_disco)}"
    for rel in dataset["relaciones"]:
        assert rel["origen"] in ids, f"origen fuera del catálogo: {rel['origen']}"
        assert rel["destino"] in ids, f"destino fuera del catálogo: {rel['destino']}"


def test_metadata_coincide_con_el_contenido(dataset):
    assert dataset["metadata"]["n_normas"] == len(dataset["normas"])
    assert dataset["metadata"]["n_relaciones"] == len(dataset["relaciones"])


def test_sin_relaciones_duplicadas_ni_autorreferencias(dataset):
    vistas = set()
    for rel in dataset["relaciones"]:
        clave = (rel["origen"], rel["tipo"], rel["destino"])
        assert rel["origen"] != rel["destino"], f"autorreferencia: {rel['origen']}"
        assert clave not in vistas, f"relación duplicada: {clave}"
        vistas.add(clave)


def test_toda_mencion_numerica_al_catalogo_tiene_arista(dataset, normas):
    """Red de seguridad para omisiones de curación.

    No intenta descubrir relaciones implícitas: exige una arista cuando el
    texto menciona explícitamente el género y número de otra norma que sí
    pertenece al catálogo.
    """
    aristas = {(r["origen"], r["destino"]) for r in dataset["relaciones"]}
    faltantes = []
    for norma in normas:
        texto = (CORPUS / norma.id).read_text(encoding="utf-8")
        for destino in menciones_normativas_catalogo(texto, normas, origen_id=norma.id):
            if (norma.id, destino) not in aristas:
                faltantes.append((norma.id, destino))
    assert not faltantes, f"menciones explícitas sin arista: {sorted(faltantes)}"


def test_modifica_no_tiene_ciclos(relaciones):
    """Consistencia lógica: A no puede modificar a B si B modifica a A."""
    solo_modifica = nx.DiGraph(
        [(r.origen, r.destino) for r in relaciones if r.tipo == TipoRelacion.MODIFICA]
    )
    assert list(nx.simple_cycles(solo_modifica)) == []


def test_golden_ontology_tiene_18_preguntas_y_categorias_fijadas():
    raw = json.loads(GOLDEN.read_text(encoding="utf-8"))
    preguntas = [CompetencyQuestion(**item) for item in raw["items"]]
    assert len(preguntas) == 18
    assert len({q.id for q in preguntas}) == 18
    assert sum(q.category == "one_hop" for q in preguntas) == 8
    assert sum(q.category == "multi_hop" for q in preguntas) == 7
    assert sum(q.category == "negative" for q in preguntas) == 3
    assert all(not q.expected_doc_ids for q in preguntas if q.category == "negative")
    assert all(
        any(len(path) == 3 for path in q.witness_paths)
        for q in preguntas
        if q.category == "multi_hop"
    )


def test_caminos_testigo_existen_y_respetan_tipos(grafo):
    raw = json.loads(GOLDEN.read_text(encoding="utf-8"))
    preguntas = [CompetencyQuestion(**item) for item in raw["items"]]
    for q in preguntas:
        permitidos = set(q.relation_types)
        cubiertos = set()
        for path in q.witness_paths:
            assert 1 <= len(path) - 1 <= q.max_hops
            for origen, destino in zip(path, path[1:]):
                assert grafo.has_edge(origen, destino), (q.id, origen, destino)
                assert grafo.edges[origen, destino]["tipo"] in permitidos
            cubiertos.add(path[-1] if q.direction == "out" else path[0])
        assert cubiertos == set(q.expected_doc_ids)


# --------------------------------------------------------------------------- #
# 2. Casos borde del código
# --------------------------------------------------------------------------- #
def test_comunidades_son_deterministas_y_ordenadas(grafo):
    """El orden debe ser estable entre procesos: la caché de §7 hashea el
    prompt, que se construye recorriendo la comunidad. Si el orden dependiera
    del `PYTHONHASHSEED`, cada corrida sería un fallo de caché y una llamada
    real a la API.
    """
    a = comunidades_del_grafo(grafo)
    b = comunidades_del_grafo(grafo)
    assert a == b
    assert all(list(c) == sorted(c) for c in a), "cada comunidad debe venir ordenada"


def test_comunidades_estables_entre_hash_seeds():
    codigo = """
import json, sys
sys.path.insert(0, '05-ontologias/code')
from ontology_lib import Norma, RelacionNormativa, build_grafo_normativo, comunidades_del_grafo
d=json.load(open('05-ontologias/examples/relaciones-manual.json'))
g=build_grafo_normativo([Norma(**n) for n in d['normas']], [RelacionNormativa(**r) for r in d['relaciones']])
print(json.dumps(comunidades_del_grafo(g)))
"""
    salidas = []
    for seed in ("1", "777"):
        env = {**__import__("os").environ, "PYTHONHASHSEED": seed}
        salidas.append(
            subprocess.check_output(
                [sys.executable, "-c", codigo], cwd=ROOT, env=env, text=True
            )
        )
    assert salidas[0] == salidas[1]


def test_catalogo_no_duplica_variantes_normalizadas():
    for organismo in catalogo_organismos_corpus():
        formas = [organismo.nombre_oficial, *organismo.variantes]
        normalizadas = [normalizar_nombre(forma) for forma in formas]
        assert len(normalizadas) == len(set(normalizadas))


def test_cache_key_invalida_prompt_esquema_temperatura_y_replica():
    base = dict(
        model="gpt-4o-mini",
        prompt="prompt-a",
        schema=ExtraccionDocumento,
        schema_version="v1",
        temperature=0.0,
        replica=0,
    )
    key = llm_cache_key(**base)
    for campo, valor in (
        ("prompt", "prompt-b"),
        ("schema_version", "v2"),
        ("temperature", 0.1),
        ("replica", 1),
    ):
        variante = {**base, campo: valor}
        assert llm_cache_key(**variante) != key


def test_extractor_cache_miss_offline_no_llama_api(tmp_path):
    extractor = LLMExtractor(cache_path=tmp_path / "cache.json")
    with pytest.raises(OfflineCacheMiss, match="cache miss"):
        extractor.extraer("texto no cacheado")
    assert extractor.api_calls == 0


def test_caches_v2_tienen_usage_y_respetan_limite_global():
    esperados = {
        "cache-extraccion-llm.json": 10,
        "cache-graphrag-comunidades.json": 5,
        "cache-foso-llm-crudo.json": 54,
    }
    costo = 0.0
    total = 0
    for nombre, cantidad in esperados.items():
        raw = json.loads((ROOT / "05-ontologias" / "examples" / nombre).read_text())
        assert raw["format_version"] == 2
        assert len(raw["entries"]) == cantidad
        total += cantidad
        for entry in raw["entries"].values():
            assert len(entry["prompt_sha256"]) == 64
            assert entry["model_requested"] == "gpt-4o-mini"
            assert entry["tokens_input"] > 0
            assert entry["tokens_output"] > 0
            costo += entry["historical_cost_usd"]
    assert total == 10 + 5 + 54
    assert costo <= 1.0


def test_parser_cubre_tabla_monto_en_subtitulo_y_reconciliacion():
    g = parse_clasificador_presupuestario(CORPUS)
    educacion = next(
        n
        for n in nodos_por_nivel(g, NivelClasificador.PARTIDA)
        if n.codigo == "09"
    )
    asignaciones = [
        g.nodes[n]["data"]
        for n in nx.descendants(g, educacion.id)
        if g.nodes[n]["data"].nivel == NivelClasificador.ASIGNACION
    ]
    assert len(asignaciones) == 6
    programa_20 = next(
        n
        for n in nodos_por_nivel(g, NivelClasificador.PROGRAMA)
        if n.doc_id == educacion.doc_id and n.codigo == "20"
    )
    assert monto_total(g, programa_20.id) == 10_928_003_100
    assert programa_20.monto_reportado_miles == 10_928_003_100

    obras = next(
        n
        for n in nodos_por_nivel(g, NivelClasificador.PARTIDA)
        if n.codigo == "12"
    )
    assert monto_total(g, obras.id) == 834_605_363


def test_monto_total_no_duplica_padre_e_hijo():
    g = nx.DiGraph()
    padre = "p"
    hijo = "h"
    from ontology_lib import NodoClasificador

    g.add_node(
        padre,
        data=NodoClasificador(
            id=padre,
            nivel=NivelClasificador.SUBTITULO,
            codigo="24",
            nombre="Transferencias",
            doc_id="fixture.txt",
            monto_miles=150,
        ),
    )
    g.add_node(
        hijo,
        data=NodoClasificador(
            id=hijo,
            nivel=NivelClasificador.ASIGNACION,
            codigo="001",
            nombre="Hoja",
            doc_id="fixture.txt",
            monto_miles=100,
        ),
    )
    g.add_edge(padre, hijo, tipo="CONTIENE")
    assert monto_total(g, padre) == 100


def test_resolver_por_numero_ignora_numeros_de_articulo(normas):
    """El número de artículo precede al de la ley en el lenguaje jurídico.

    Tomar el primer número del string resolvía 'el art. 12 del DL 825' a la
    Partida 12 del presupuesto. El identificador debe extraerse del número que
    va detrás del designador de la norma, no del primero que aparezca.
    """
    assert resolver_por_numero("el art. 12 del DL 825", normas) == "ley-01-dl-825-iva-base.txt"
    assert resolver_por_numero("artículo 71 del reglamento", normas) is None
    assert (
        resolver_por_numero("el artículo 9º de la Ley Nº 20.248", normas)
        == "ley-08-ley-20248-subvencion-preferencial.txt"
    )


def test_resolver_por_numero_tolera_formato(normas):
    """Puntuación final y separador de miles son ruido de formato, no identidad."""
    esperado = "ley-02-ley-21210-modernizacion.txt"
    for variante in ("Ley Nº 21.210", "Ley N° 21.210.", "LEY 21210", "ley 21.210,"):
        assert resolver_por_numero(variante, normas) == esperado, variante


def test_resolver_identificador_permite_ablacion_numerica(normas):
    identificador = "Decreto Ley Nº 825"
    assert resolver_identificador_norma(identificador, normas, usar_numero=True) == (
        "ley-01-dl-825-iva-base.txt",
        "numero",
    )
    assert resolver_identificador_norma(
        identificador, normas, usar_numero=False
    ) == (None, "sin_match")


def test_resolver_no_confunde_leyes_de_numero_parecido(normas):
    """18.695 y 18.575 comparten 0.846 de similitud de caracteres y son leyes
    distintas. La Ley 18.695 no está en el corpus: lo correcto es no resolver.
    """
    assert resolver_identificador_norma("Ley Nº 18.695", normas) == (None, "sin_match")


def test_texto_vigente_distingue_no_existe_de_texto_original():
    """Un artículo creado por una modificación posterior no tiene 'texto
    original' al que volver: antes de su vigencia simplemente no existe.
    """
    mods = [
        ModificacionArticulo(
            norma_modificadora="ley-04",
            norma_modificada="ley-03",
            articulo="7 bis",
            valido_desde="2024-12-11",
            registrado_el="2026-08-03",
            fundamento="Incorpórase un nuevo artículo 7º bis",
            tipo_cambio=TipoCambioArticulo.CREA,
        )
    ]
    antes = texto_vigente("ley-03", "7 bis", mods, "2024-06-01")
    despues = texto_vigente("ley-03", "7 bis", mods, "2025-01-01")
    assert antes.estado == EstadoVigencia.NO_EXISTE
    assert despues.estado == EstadoVigencia.ORIGINAL
    assert despues.fuente_doc_id == "ley-04"
    assert despues.vigente_desde == date(2024, 12, 11)


def test_texto_vigente_rechaza_fechas_no_iso():
    """Las fechas se comparaban como strings sin validar: '01/06/2025' pasaba
    silenciosamente y devolvía el texto original.
    """
    mods = [
        ModificacionArticulo(
            norma_modificadora="ley-04",
            norma_modificada="ley-03",
            articulo="5",
            valido_desde="2023-12-11",
            registrado_el="2026-08-03",
            fundamento="Sustitúyese en el artículo 5º",
        )
    ]
    with pytest.raises(ValueError):
        texto_vigente("ley-03", "5", mods, "01/06/2025")


def test_texto_vigente_modela_modificacion_derogacion_y_bitemporalidad():
    mods = [
        ModificacionArticulo(
            norma_modificadora="ley-b",
            norma_modificada="ley-a",
            articulo="1",
            valido_desde="2020-01-01",
            registrado_el="2022-01-01",
            fundamento="modifica",
        ),
        ModificacionArticulo(
            norma_modificadora="ley-c",
            norma_modificada="ley-a",
            articulo="1",
            valido_desde="2023-01-01",
            registrado_el="2024-01-01",
            fundamento="deroga",
            tipo_cambio=TipoCambioArticulo.DEROGA,
        ),
    ]
    original = texto_vigente("ley-a", "1", mods, date(2019, 1, 1))
    modificado = texto_vigente("ley-a", "1", mods, date(2021, 1, 1))
    derogado = texto_vigente("ley-a", "1", mods, date(2024, 1, 1))
    assert original.estado == EstadoVigencia.ORIGINAL
    assert modificado.estado == EstadoVigencia.MODIFICADO
    assert derogado.estado == EstadoVigencia.DEROGADO
    assert derogado.fuente_doc_id == "ley-c"


def test_alcance_transitivo_soporta_ciclos():
    """Un ciclo de CITA es posible en el corpus real (una nota editorial en el
    texto refundido puede referirse a la ley que lo modificó). El recorrido no
    debe colgarse ni duplicar nodos.
    """
    g = nx.DiGraph()
    for nid in ("a", "b", "c"):
        g.add_node(nid)
    g.add_edge("a", "b", tipo=TipoRelacion.CITA)
    g.add_edge("b", "a", tipo=TipoRelacion.CITA)
    g.add_edge("c", "a", tipo=TipoRelacion.CITA)
    assert alcance_transitivo(g, "a", direccion="in") == {"b", "c"}
    assert alcance_transitivo(g, "a", direccion="out") == {"b"}


def test_alcance_transitivo_nodo_aislado_o_inexistente(grafo):
    assert alcance_transitivo(grafo, "no-existe.txt") == set()
    solo_modifica = [TipoRelacion.MODIFICA]
    # un documento sin ninguna arista MODIFICA no alcanza nada por ese tipo
    assert alcance_transitivo(grafo, "do-01-extracto-decreto-aranceles.txt", solo_modifica) == set()


def test_graph_retriever_promueve_vecino_y_sin_vecinos_conserva_ranking():
    spec = importlib.util.spec_from_file_location(
        "evaluar_ontologia", ROOT / "05-ontologias" / "code" / "08-evaluar-con-ontologia.py"
    )
    modulo = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modulo)
    from retrieval_lib import Chunk, ScoredDoc

    class Base:
        def search(self, query, k):
            del query
            ids = ["semilla", "resto-1", "resto-2", "vecino"]
            return [
                ScoredDoc(i, 4 - i, Chunk(f"c{i}", doc_id, doc_id))
                for i, doc_id in enumerate(ids[:k])
            ]

    grafo = nx.DiGraph()
    grafo.add_edge("semilla", "vecino", tipo=TipoRelacion.MODIFICA)
    retriever = modulo.GraphExpandedRetriever(
        Base(), grafo, [TipoRelacion.MODIFICA], n_semillas=1
    )
    assert [r.chunk.doc_id for r in retriever.search("q", k=3)] == [
        "semilla",
        "vecino",
        "resto-1",
    ]

    sin_vecinos = modulo.GraphExpandedRetriever(
        Base(), nx.DiGraph(), [TipoRelacion.MODIFICA], n_semillas=1
    )
    assert [r.chunk.doc_id for r in sin_vecinos.search("q", k=3)] == [
        "semilla",
        "resto-1",
        "resto-2",
    ]
