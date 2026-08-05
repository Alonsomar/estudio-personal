"""Smoke tests de `harness_lib` (06-harness).

Reglas del repo: sin API keys ni red. `PoliticaLLM` se testea solo en su
comportamiento offline (un *cache miss* debe ser error explícito, nunca una
llamada silenciosa); el bucle se ejercita con `PoliticaGuionada`.

Los tests cubren invariantes, no valores exactos: cuántos documentos tiene
el corpus o qué devuelve BM25 puede cambiar sin que estos tests mientan.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from harness_lib import (
    AgentLoop,
    Decision,
    EstadoPaso,
    Herramienta,
    HarnessConfig,
    MotivoCorte,
    OfflineCacheMiss,
    Percepcion,
    PoliticaGuionada,
    PoliticaLLM,
    Tarea,
    ToolError,
    ToolRegistry,
    Trayectoria,
    cargar_tareas,
    construir_herramientas,
    estimar_tokens,
    evaluar_trayectoria,
    harness_cache_key,
    llamadas_redundantes,
    recuperacion_tras_error,
)


class ArgsEco(BaseModel):
    texto: str
    veces: int = 1


def _tool_eco(salida: str = "") -> Herramienta:
    return Herramienta(
        nombre="eco",
        descripcion="Repite el texto recibido.",
        args_model=ArgsEco,
        fn=lambda args: salida or args.texto * args.veces,
    )


def _tool_que_falla() -> Herramienta:
    def explota(args: ArgsEco) -> str:
        raise ToolError(
            esperado="un texto no vacío",
            recibido=args.texto,
            siguiente_paso="volvé a llamar con texto",
        )

    return Herramienta(
        nombre="falla", descripcion="Siempre falla.", args_model=ArgsEco, fn=explota
    )


# --------------------------------------------------------------------------- #
# ToolRegistry: los tres modos de fallo y el truncado.
# --------------------------------------------------------------------------- #
def test_registry_despacha_y_devuelve_salida():
    reg = ToolRegistry([_tool_eco()])
    obs = reg.invocar("eco", {"texto": "ab", "veces": 3}, HarnessConfig())
    assert obs.ok and obs.texto == "ababab"
    assert obs.estado is EstadoPaso.OK


@pytest.mark.parametrize("estilo", ["opaco", "contrato"])
def test_herramienta_desconocida_no_rompe_el_bucle(estilo):
    reg = ToolRegistry([_tool_eco()])
    obs = reg.invocar("ecoo", {}, HarnessConfig(estilo_error=estilo))
    assert not obs.ok
    assert obs.estado is EstadoPaso.ERROR_HERRAMIENTA_DESCONOCIDA


@pytest.mark.parametrize("estilo", ["opaco", "contrato"])
def test_argumentos_invalidos_se_clasifican_aparte(estilo):
    reg = ToolRegistry([_tool_eco()])
    obs = reg.invocar("eco", {"veces": 2}, HarnessConfig(estilo_error=estilo))
    assert obs.estado is EstadoPaso.ERROR_ARGUMENTOS


def test_el_contrato_de_error_informa_y_el_opaco_no():
    """El invariante que sostiene §1: el error con contrato nombra el campo
    que falló y el opaco no. Si esto deja de valer, el experimento del
    factorial deja de medir lo que dice medir."""
    reg = ToolRegistry([_tool_eco()])
    opaco = reg.invocar("eco", {"veces": 2}, HarnessConfig(estilo_error="opaco")).texto
    contrato = reg.invocar(
        "eco", {"veces": 2}, HarnessConfig(estilo_error="contrato")
    ).texto
    assert "texto" not in opaco
    assert "texto" in contrato and "Siguiente paso" in contrato
    assert len(contrato) > len(opaco)


def test_tool_error_expone_los_tres_campos_en_modo_contrato():
    reg = ToolRegistry([_tool_que_falla()])
    obs = reg.invocar("falla", {"texto": ""}, HarnessConfig(estilo_error="contrato"))
    assert obs.estado is EstadoPaso.ERROR_EJECUCION
    for fragmento in ("Esperado:", "Recibido:", "Siguiente paso:"):
        assert fragmento in obs.texto


def test_excepcion_inesperada_no_propaga():
    """Una herramienta que revienta con algo que nadie previó tiene que
    volver como observación, no tumbar el bucle."""

    def revienta(args: ArgsEco) -> str:
        raise ZeroDivisionError("boom")

    reg = ToolRegistry(
        [Herramienta(nombre="x", descripcion="", args_model=ArgsEco, fn=revienta)]
    )
    obs = reg.invocar("x", {"texto": "a"}, HarnessConfig())
    assert not obs.ok and obs.estado is EstadoPaso.ERROR_EJECUCION


def test_truncado_avisa_y_conserva_el_tamano_original():
    reg = ToolRegistry([_tool_eco("x" * 5_000)])
    obs = reg.invocar("eco", {"texto": "a"}, HarnessConfig(max_chars_observacion=100))
    assert obs.truncado
    assert obs.caracteres_originales == 5_000
    assert "truncado" in obs.texto  # truncar sin avisar es peor que no truncar
    assert len(obs.texto) < 5_000


def test_sin_limite_la_observacion_entra_entera():
    reg = ToolRegistry([_tool_eco("y" * 3_000)])
    obs = reg.invocar("eco", {"texto": "a"}, HarnessConfig(max_chars_observacion=None))
    assert not obs.truncado and len(obs.texto) == 3_000


def test_spec_openai_deriva_del_esquema_pydantic():
    spec = _tool_eco().spec_openai()
    assert spec["function"]["name"] == "eco"
    props = spec["function"]["parameters"]["properties"]
    assert set(props) == {"texto", "veces"}
    assert spec["function"]["parameters"]["required"] == ["texto"]


# --------------------------------------------------------------------------- #
# AgentLoop: cortes y contabilidad.
# --------------------------------------------------------------------------- #
def test_bucle_termina_cuando_la_politica_responde():
    reg = ToolRegistry([_tool_eco()])
    guion = [
        Decision(accion="usar_herramienta", herramienta="eco", argumentos={"texto": "a"}),
        Decision(accion="responder", respuesta="listo", docs_citados=["d.txt"]),
    ]
    tray = AgentLoop(reg, PoliticaGuionada(guion), HarnessConfig()).correr("t", "p")
    assert tray.motivo_corte is MotivoCorte.RESPONDIO
    assert tray.docs_citados == ["d.txt"]
    assert tray.n_pasos == 2


def test_bucle_corta_en_max_pasos():
    reg = ToolRegistry([_tool_eco()])
    guion = [
        Decision(
            accion="usar_herramienta", herramienta="eco", argumentos={"texto": str(i)}
        )
        for i in range(20)
    ]
    tray = AgentLoop(reg, PoliticaGuionada(guion), HarnessConfig(max_pasos=4)).correr(
        "t", "p"
    )
    assert tray.motivo_corte is MotivoCorte.MAX_PASOS
    assert tray.n_pasos == 4


def test_bucle_corta_por_llamada_repetida():
    """Repetir la llamada idéntica N veces no va a devolver algo distinto."""
    reg = ToolRegistry([_tool_eco()])
    guion = [
        Decision(accion="usar_herramienta", herramienta="eco", argumentos={"texto": "a"})
        for _ in range(10)
    ]
    tray = AgentLoop(
        reg, PoliticaGuionada(guion), HarnessConfig(max_pasos=10, max_repeticiones=3)
    ).correr("t", "p")
    assert tray.motivo_corte is MotivoCorte.SIN_PROGRESO
    assert tray.n_pasos == 3


def test_fallo_de_la_politica_no_propaga():
    class PoliticaRota:
        def decidir(self, percepcion: Percepcion) -> Decision:
            raise RuntimeError("modelo caído")

    tray = AgentLoop(ToolRegistry([_tool_eco()]), PoliticaRota(), HarnessConfig()).correr(
        "t", "p"
    )
    assert tray.motivo_corte is MotivoCorte.ERROR_POLITICA


def test_trayectoria_serializa_y_vuelve():
    reg = ToolRegistry([_tool_eco()])
    guion = [Decision(accion="responder", respuesta="ok")]
    tray = AgentLoop(reg, PoliticaGuionada(guion), HarnessConfig()).correr("t", "p")
    vuelta = Trayectoria.model_validate(json.loads(tray.model_dump_json()))
    assert vuelta == tray


# --------------------------------------------------------------------------- #
# Métricas.
# --------------------------------------------------------------------------- #
def _tray(estados: list[EstadoPaso]) -> Trayectoria:
    from harness_lib import Paso

    return Trayectoria(
        tarea_id="t",
        pregunta="p",
        harness="h",
        pasos=[
            Paso(
                indice=i,
                herramienta="eco",
                argumentos={"i": i},
                estado=e,
                observacion="",
            )
            for i, e in enumerate(estados)
        ],
    )


def test_recuperacion_tras_error_solo_cuenta_errores_con_siguiente():
    tray = _tray([EstadoPaso.ERROR_ARGUMENTOS, EstadoPaso.OK, EstadoPaso.ERROR_EJECUCION])
    oportunidades, tasa = recuperacion_tras_error([tray])
    assert oportunidades == 1  # el último error no tiene paso siguiente
    assert tasa == 1.0


def test_recuperacion_es_cero_si_el_error_se_repite():
    tray = _tray([EstadoPaso.ERROR_EJECUCION] * 4)
    oportunidades, tasa = recuperacion_tras_error([tray])
    assert oportunidades == 3 and tasa == 0.0


def test_llamadas_redundantes_cuenta_repeticiones_exactas():
    from harness_lib import Paso

    pasos = [
        Paso(indice=0, herramienta="eco", argumentos={"a": 1}, estado=EstadoPaso.OK, observacion=""),
        Paso(indice=1, herramienta="eco", argumentos={"a": 1}, estado=EstadoPaso.OK, observacion=""),
        Paso(indice=2, herramienta="eco", argumentos={"a": 2}, estado=EstadoPaso.OK, observacion=""),
    ]
    tray = Trayectoria(tarea_id="t", pregunta="p", harness="h", pasos=pasos)
    assert llamadas_redundantes(tray) == 1


@pytest.mark.parametrize(
    "esperados,citados,acierto,f1",
    [
        (["a.txt"], ["a.txt"], True, 1.0),
        (["a.txt", "b.txt"], ["a.txt"], False, 2 / 3),
        (["a.txt"], ["a.txt", "b.txt"], False, 2 / 3),
        ([], [], True, 1.0),  # abstención correcta
        ([], ["a.txt"], False, 0.0),  # alucinó una cita
    ],
)
def test_evaluar_trayectoria_metrica_de_conjuntos(esperados, citados, acierto, f1):
    tarea = Tarea(
        id="t", familia="recuperacion", dificultad="facil", pregunta="p",
        docs_esperados=esperados,
    )
    tray = Trayectoria(tarea_id="t", pregunta="p", harness="h", docs_citados=citados)
    res = evaluar_trayectoria(tray, tarea)
    assert res.acierto_exacto is acierto
    assert res.f1 == pytest.approx(f1)
    assert 0.0 <= res.precision <= 1.0 and 0.0 <= res.recall <= 1.0


def test_estimar_tokens_es_monotono_y_positivo():
    assert estimar_tokens("") >= 1
    assert estimar_tokens("a" * 400) > estimar_tokens("a" * 100)


# --------------------------------------------------------------------------- #
# Caché y modo offline.
# --------------------------------------------------------------------------- #
def test_cache_miss_offline_es_error_explicito(tmp_path):
    """Nunca una llamada silenciosa a la red: el default es offline."""
    politica = PoliticaLLM(cache_path=tmp_path / "vacio.json", allow_api=False)
    percepcion = Percepcion(pregunta="p", mensajes=[{"role": "user", "content": "p"}],
                            herramientas=[], paso=0)
    with pytest.raises(OfflineCacheMiss):
        politica.decidir(percepcion)


def test_clave_de_cache_cambia_si_cambia_el_historial():
    base = dict(model="m", herramientas=[], temperature=0.0)
    k1 = harness_cache_key(mensajes=[{"role": "user", "content": "a"}], **base)
    k2 = harness_cache_key(mensajes=[{"role": "user", "content": "b"}], **base)
    assert k1 != k2


def test_clave_de_cache_cambia_si_cambia_el_esquema_de_una_tool():
    """Si cambia el contrato de una herramienta, el caché no puede devolver
    una decisión que el modelo tomó bajo el contrato viejo."""
    base = dict(model="m", mensajes=[{"role": "user", "content": "a"}], temperature=0.0)
    k1 = harness_cache_key(herramientas=[_tool_eco().spec_openai()], **base)

    class ArgsOtro(BaseModel):
        texto: str
        veces: int = 1
        idioma: str = "es"

    otra = Herramienta(nombre="eco", descripcion="Repite el texto recibido.",
                       args_model=ArgsOtro, fn=lambda a: a.texto)
    k2 = harness_cache_key(herramientas=[otra.spec_openai()], **base)
    assert k1 != k2


# --------------------------------------------------------------------------- #
# Herramientas del corpus y tareas: se construyen sin red.
# --------------------------------------------------------------------------- #
def test_herramientas_del_corpus_se_construyen_offline():
    reg = construir_herramientas()
    assert {"buscar_corpus", "leer_norma", "responder", "vecinos_grafo"} <= set(reg.nombres)


def test_sin_grafo_no_expone_la_travesia():
    reg = construir_herramientas(con_grafo=False)
    assert "vecinos_grafo" not in reg.nombres
    assert "buscar_corpus" in reg.nombres


def test_leer_norma_pagina_y_rechaza_ids_inventados():
    reg = construir_herramientas()
    cfg = HarnessConfig(estilo_error="contrato")
    ok = reg.invocar("leer_norma", {"doc_id": "ley-01-dl-825-iva-base.txt"}, cfg)
    assert ok.ok and "página 1 de" in ok.texto
    malo = reg.invocar("leer_norma", {"doc_id": "ds-250"}, cfg)
    assert not malo.ok and malo.estado is EstadoPaso.ERROR_EJECUCION


def test_vecinos_grafo_devuelve_el_fundamento_literal():
    """La trazabilidad a la fuente no es opcional: la observación tiene que
    llevar la cita que sustenta la arista (doctrina #5 y `05 §2`)."""
    reg = construir_herramientas()
    obs = reg.invocar(
        "vecinos_grafo",
        {"doc_id": "ley-01-dl-825-iva-base.txt", "tipo_relacion": "modifica",
         "direccion": "in"},
        HarnessConfig(),
    )
    assert obs.ok
    assert "ley-02-ley-21210-modernizacion.txt" in obs.texto
    assert "fundamento" in obs.texto


def test_tareas_congeladas_son_coherentes():
    tareas = cargar_tareas()
    assert len(tareas) >= 10
    assert {t.familia for t in tareas} == {"recuperacion", "estructural", "abstencion"}
    for t in tareas:
        assert t.origen.get("golden"), f"{t.id} sin procedencia declarada"
        if t.familia == "abstencion":
            assert t.docs_esperados == []
