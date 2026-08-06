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
    MemoriaExterna,
    MotivoCorte,
    OfflineCacheMiss,
    Partida,
    Percepcion,
    PoliticaGuionada,
    PoliticaLLM,
    SinCompactar,
    Tarea,
    ToolError,
    Trabajador,
    ToolRegistry,
    Trayectoria,
    VentanaConIndice,
    VentanaDeslizante,
    cargar_tareas,
    construir_herramientas,
    costo_esquema,
    contar_tokens,
    docs_mencionados,
    estimar_tokens,
    evaluar_trayectoria,
    harness_cache_key,
    herramienta_delegar,
    herramienta_memoria,
    llamadas_redundantes,
    presupuesto_contexto,
    recuperacion_tras_error,
    tokenizador_exacto,
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
    tray = Trayectoria(
        tarea_id="t", pregunta="p", harness="h", docs_citados=citados,
        motivo_corte=MotivoCorte.RESPONDIO,
    )
    res = evaluar_trayectoria(tray, tarea)
    assert res.acierto_exacto is acierto
    assert res.f1 == pytest.approx(f1)
    assert 0.0 <= res.precision <= 1.0 and 0.0 <= res.recall <= 1.0


@pytest.mark.parametrize(
    "motivo", [MotivoCorte.MAX_PASOS, MotivoCorte.SIN_PROGRESO, MotivoCorte.ERROR_POLITICA]
)
def test_quedarse_sin_pasos_no_cuenta_como_abstencion(motivo):
    """El agujero que la parte C de §2 destapó: una tarea de abstención
    también termina con cero citas cuando el bucle se quedó sin pasos. Sin
    esta condición, no responder cobraba acierto perfecto."""
    tarea = Tarea(
        id="t", familia="abstencion", dificultad="facil", pregunta="p",
        docs_esperados=[],
    )
    tray = Trayectoria(
        tarea_id="t", pregunta="p", harness="h", docs_citados=[], motivo_corte=motivo
    )
    res = evaluar_trayectoria(tray, tarea)
    assert res.acierto_exacto is False
    assert res.f1 == 0.0


def test_abstencion_explicita_si_cuenta():
    tarea = Tarea(
        id="t", familia="abstencion", dificultad="facil", pregunta="p",
        docs_esperados=[],
    )
    tray = Trayectoria(
        tarea_id="t", pregunta="p", harness="h", docs_citados=[],
        respuesta_final="No consta en el corpus.",
        motivo_corte=MotivoCorte.RESPONDIO,
    )
    assert evaluar_trayectoria(tray, tarea).acierto_exacto is True


def test_estimar_tokens_es_monotono_y_positivo():
    assert estimar_tokens("") >= 1
    assert estimar_tokens("a" * 400) > estimar_tokens("a" * 100)


# --------------------------------------------------------------------------- #
# §2 Presupuesto de contexto y compactación.
# --------------------------------------------------------------------------- #
def _historial(n_pares: int) -> list[dict]:
    mensajes = [
        {"role": "system", "content": "instrucciones"},
        {"role": "user", "content": "¿pregunta?"},
    ]
    for i in range(n_pares):
        mensajes.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "buscar_corpus", "arguments": "{}"},
                    }
                ],
            }
        )
        mensajes.append(
            {
                "role": "tool",
                "tool_call_id": f"c{i}",
                "content": f"resultado {i} en ley-0{i % 9}-alguna-norma.txt " + "x" * 200,
            }
        )
    return mensajes


def test_presupuesto_reparte_en_las_cinco_partidas():
    specs = [_tool_eco().spec_openai()]
    reparto = presupuesto_contexto(_historial(2), specs)
    assert set(reparto) == {p.value for p in Partida}
    assert reparto[Partida.SISTEMA.value] > 0
    assert reparto[Partida.PREGUNTA.value] > 0
    assert reparto[Partida.OBSERVACIONES.value] > 0
    assert reparto[Partida.HERRAMIENTAS.value] > 0


def test_los_esquemas_de_herramientas_se_cobran_aunque_no_sean_mensajes():
    """La partida que se olvida: no viaja como texto en los mensajes pero el
    proveedor la serializa y la cobra en cada iteración."""
    mensajes = _historial(1)
    sin = presupuesto_contexto(mensajes, [])[Partida.HERRAMIENTAS.value]
    con = presupuesto_contexto(mensajes, [_tool_eco().spec_openai()])[
        Partida.HERRAMIENTAS.value
    ]
    assert sin == 0 and con > 0


def test_la_historia_crece_y_el_prefijo_no():
    specs = [_tool_eco().spec_openai()]
    corto = presupuesto_contexto(_historial(1), specs)
    largo = presupuesto_contexto(_historial(4), specs)
    assert largo[Partida.OBSERVACIONES.value] > corto[Partida.OBSERVACIONES.value]
    assert largo[Partida.SISTEMA.value] == corto[Partida.SISTEMA.value]
    assert largo[Partida.HERRAMIENTAS.value] == corto[Partida.HERRAMIENTAS.value]


def test_sin_compactar_es_la_identidad():
    mensajes = _historial(3)
    assert SinCompactar().compactar(mensajes) == mensajes


@pytest.mark.parametrize("k", [1, 2, 3])
def test_ventana_conserva_los_ultimos_k_pares_y_el_encabezado(k):
    mensajes = _historial(5)
    salida = VentanaDeslizante(k=k).compactar(mensajes)
    assert salida[0]["role"] == "system"
    assert salida[1]["role"] == "user"
    assert len(salida) == 2 + 2 * k
    # El emparejamiento assistant/tool tiene que sobrevivir o la API rechaza.
    for asistente, herramienta in zip(salida[2::2], salida[3::2]):
        assert asistente["role"] == "assistant" and herramienta["role"] == "tool"
        assert herramienta["tool_call_id"] == asistente["tool_calls"][0]["id"]


def test_ventana_con_indice_archiva_y_deja_direccion():
    memoria = MemoriaExterna()
    salida = VentanaConIndice(memoria, k=2).compactar(_historial(5))
    indice = [m for m in salida if (m.get("content") or "").startswith("[contexto compactado]")]
    assert len(indice) == 1
    assert len(memoria) == 3  # 5 pares - 2 conservados
    # El índice tiene que nombrar los documentos: es lo que lo hace utilizable.
    assert "ley-00-alguna-norma.txt" in indice[0]["content"]
    assert "recuperar_memoria" in indice[0]["content"]


def test_ventana_con_indice_no_toca_historiales_cortos():
    memoria = MemoriaExterna()
    mensajes = _historial(2)
    assert VentanaConIndice(memoria, k=2).compactar(mensajes) == mensajes
    assert len(memoria) == 0


def test_compactar_ahorra_tokens():
    specs = [_tool_eco().spec_openai()]
    mensajes = _historial(6)
    base = sum(presupuesto_contexto(mensajes, specs).values())
    for pol in (VentanaDeslizante(k=2), VentanaConIndice(MemoriaExterna(), k=2)):
        assert sum(presupuesto_contexto(pol.compactar(mensajes), specs).values()) < base


def test_memoria_externa_guarda_recupera_y_falla_con_contrato():
    memoria = MemoriaExterna()
    memoria.guardar("p0", "contenido")
    assert memoria.recuperar("p0") == "contenido"
    with pytest.raises(ToolError):
        memoria.recuperar("p9")


def test_herramienta_memoria_se_integra_al_registry():
    memoria = MemoriaExterna()
    memoria.guardar("p0", "texto archivado")
    reg = ToolRegistry([herramienta_memoria(memoria)])
    assert reg.invocar("recuperar_memoria", {"clave": "p0"}, HarnessConfig()).texto == (
        "texto archivado"
    )
    fallo = reg.invocar("recuperar_memoria", {"clave": "zz"}, HarnessConfig())
    assert not fallo.ok


def test_docs_mencionados_extrae_ids_sin_repetir():
    texto = "ver ley-01-dl-825-iva-base.txt y ley-01-dl-825-iva-base.txt y otro.txt"
    assert docs_mencionados(texto) == ["ley-01-dl-825-iva-base.txt", "otro.txt"]


def test_el_bucle_aplica_el_compactador_al_enviar_y_conserva_la_historia():
    """El compactador afecta lo que se manda, no lo que se registra: si no,
    la trayectoria dejaría de ser auditable."""
    reg = ToolRegistry([_tool_eco()])
    guion = [
        Decision(accion="usar_herramienta", herramienta="eco", argumentos={"texto": str(i)})
        for i in range(5)
    ]
    traza: list[list[dict]] = []
    tray = AgentLoop(
        reg, PoliticaGuionada(guion), HarnessConfig(max_pasos=5),
        VentanaDeslizante(k=1), medir_contexto=True,
    ).correr("t", "p", traza=traza)
    assert tray.n_pasos == 5  # la trayectoria conserva todo
    assert all(len(m) <= 4 for m in traza)  # lo enviado va acotado
    assert all(p.contexto for p in tray.pasos)


def test_contar_tokens_degrada_sin_romperse():
    assert contar_tokens("una frase corta") > 0
    assert isinstance(tokenizador_exacto(), bool)


# --------------------------------------------------------------------------- #
# §5 Orquestador y trabajadores.
# --------------------------------------------------------------------------- #
def test_subconjunto_recorta_el_menu_sin_duplicar_herramientas():
    base = construir_herramientas(con_alcance=True)
    chico = base.subconjunto(["buscar_corpus", "responder"])
    assert chico.nombres == ["buscar_corpus", "responder"]
    assert chico.get("buscar_corpus") is base.get("buscar_corpus")
    with pytest.raises(KeyError):
        base.subconjunto(["no_existe"])


def test_el_error_avisa_cuando_recomienda_una_tool_ausente():
    """El fallo que §5 documenta: `vecinos_grafo` recomienda 'buscar_corpus'
    y el trabajador estructural no lo tiene. Sin el aviso, se le está
    pidiendo algo imposible."""
    base = construir_herramientas(con_alcance=True)
    sin_buscar = base.subconjunto(["vecinos_grafo", "responder"])
    args = {"doc_id": "ds-250", "tipo_relacion": "modifica"}

    ingenuo = sin_buscar.invocar(
        "vecinos_grafo", args,
        HarnessConfig(estilo_error="contrato", avisar_herramienta_ausente=False),
    ).texto
    consciente = sin_buscar.invocar(
        "vecinos_grafo", args,
        HarnessConfig(estilo_error="contrato", avisar_herramienta_ausente=True),
    ).texto

    assert "buscar_corpus" in ingenuo and "ATENCIÓN" not in ingenuo
    assert "ATENCIÓN" in consciente and "no está" in consciente


def test_sin_herramientas_ausentes_el_aviso_no_aparece():
    """El registro completo sí tiene 'buscar_corpus': el texto no cambia, y
    por eso los cachés de §1 y §3 siguen siendo válidos."""
    completo = construir_herramientas(con_alcance=True)
    texto = completo.invocar(
        "vecinos_grafo", {"doc_id": "ds-250", "tipo_relacion": "modifica"},
        HarnessConfig(estilo_error="contrato"),
    ).texto
    assert "ATENCIÓN" not in texto


def test_delegar_corre_un_bucle_anidado_y_devuelve_solo_el_resumen():
    base = construir_herramientas()
    trabajador = Trabajador(
        nombre="documental",
        descripcion="lee el corpus",
        registry=base.subconjunto(["buscar_corpus", "responder"]),
        config=HarnessConfig(max_pasos=3),
    )
    guion = [
        Decision(accion="usar_herramienta", herramienta="buscar_corpus",
                 argumentos={"consulta": "IVA digital"}),
        Decision(accion="responder", respuesta="La Ley 21.210.",
                 docs_citados=["circular-01-sii-iva-digital.txt"]),
    ]
    registro: list[Trayectoria] = []
    tool = herramienta_delegar([trabajador], PoliticaGuionada(guion), registro)
    obs = ToolRegistry([tool]).invocar(
        "delegar", {"trabajador": "documental", "subtarea": "¿qué ley?"},
        HarnessConfig(),
    )
    assert obs.ok
    assert "La Ley 21.210." in obs.texto
    assert "circular-01-sii-iva-digital.txt" in obs.texto
    # Lo que el trabajador observó NO cruza la frontera: eso es el aislamiento.
    assert "score" not in obs.texto
    assert len(registro) == 1 and registro[0].n_pasos == 2


def test_delegar_registra_la_subtrayectoria_para_que_el_costo_sea_visible():
    """Sin registro, el costo de un sistema multiagente es invisible — que es
    exactamente cómo se subestima en la práctica."""
    base = construir_herramientas()
    trabajador = Trabajador(
        nombre="doc", descripcion="", config=HarnessConfig(max_pasos=2),
        registry=base.subconjunto(["buscar_corpus", "responder"]),
    )
    registro: list[Trayectoria] = []
    guion = [
        Decision(accion="usar_herramienta", herramienta="buscar_corpus",
                 argumentos={"consulta": "x"})
    ] * 3
    tool = herramienta_delegar([trabajador], PoliticaGuionada(guion), registro)
    ToolRegistry([tool]).invocar(
        "delegar", {"trabajador": "doc", "subtarea": "s"}, HarnessConfig()
    )
    assert len(registro) == 1
    assert registro[0].motivo_corte is MotivoCorte.MAX_PASOS


def test_delegar_a_un_trabajador_inexistente_lista_los_disponibles():
    base = construir_herramientas()
    trabajador = Trabajador(
        nombre="doc", descripcion="", config=HarnessConfig(),
        registry=base.subconjunto(["responder"]),
    )
    tool = herramienta_delegar([trabajador], PoliticaGuionada([]), None)
    obs = ToolRegistry([tool]).invocar(
        "delegar", {"trabajador": "fantasma", "subtarea": "s"},
        HarnessConfig(estilo_error="contrato"),
    )
    assert not obs.ok and "doc" in obs.texto


def test_el_trabajador_que_no_concluye_lo_dice(monkeypatch):
    """Un resumen que oculta el fracaso es peor que uno que lo declara: el
    orquestador no puede diagnosticar lo que no ve."""
    base = construir_herramientas()
    trabajador = Trabajador(
        nombre="doc", descripcion="", config=HarnessConfig(max_pasos=1),
        registry=base.subconjunto(["buscar_corpus", "responder"]),
    )
    guion = [
        Decision(accion="usar_herramienta", herramienta="buscar_corpus",
                 argumentos={"consulta": "x"})
    ]
    tool = herramienta_delegar([trabajador], PoliticaGuionada(guion), None)
    obs = ToolRegistry([tool]).invocar(
        "delegar", {"trabajador": "doc", "subtarea": "s"}, HarnessConfig()
    )
    assert "sin conclusión" in obs.texto and "max_pasos" in obs.texto


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


def test_alcance_acotado_reproduce_los_goldens_multihop_de_05():
    """La herramienta de grano grueso de §3 no se diseñó a ojo: tiene que
    devolver exactamente lo que las competency questions multi-hop
    congeladas de `05` esperan. Si el grafo o el BFS cambian, esto grita."""
    import json as _json
    from pathlib import Path as _Path

    from harness_lib import alcance_acotado, cargar_grafo_normativo

    raiz = _Path(__file__).resolve().parent.parent
    golden = _json.loads(
        (raiz / "05-ontologias" / "examples" / "golden-ontology.json").read_text(
            encoding="utf-8"
        )
    )
    grafo = cargar_grafo_normativo()
    multihop = [i for i in golden["items"] if i["category"] == "multi_hop"]
    assert multihop, "el golden de 05 debería traer preguntas multi-hop"
    for item in multihop:
        obtenido = set(
            alcance_acotado(grafo, item["target_node"], item["max_hops"], "in")
        )
        assert obtenido == set(item["expected_doc_ids"]), item["id"]


def test_alcance_normativo_resuelve_en_una_llamada_lo_que_vecinos_no():
    """El argumento de granularidad, como invariante: la misma pregunta
    necesita una llamada con la herramienta gruesa y muchas con la fina."""
    reg = construir_herramientas(con_alcance=True)
    cfg = HarnessConfig(estilo_error="contrato")
    obs = reg.invocar(
        "alcance_normativo",
        {"doc_id": "decreto-03-reglamento-compras-publicas.txt",
         "max_saltos": 2, "direccion": "in"},
        cfg,
    )
    assert obs.ok
    for esperado in (
        "do-02-extracto-licitacion-publica.txt",
        "glosa-05-presupuesto-interior.txt",
        "oficio-02-contraloria-trato-directo.txt",
        "resolucion-01-chilecompra-compra-agil.txt",
    ):
        assert esperado in obs.texto


def test_alcance_normativo_valida_saltos_y_doc_id():
    reg = construir_herramientas(con_alcance=True)
    cfg = HarnessConfig(estilo_error="contrato")
    malo = reg.invocar("alcance_normativo", {"doc_id": "ds-250"}, cfg)
    assert not malo.ok and "Siguiente paso" in malo.texto
    saltos = reg.invocar(
        "alcance_normativo",
        {"doc_id": "ley-01-dl-825-iva-base.txt", "max_saltos": 99},
        cfg,
    )
    assert not saltos.ok


def test_alcance_no_se_expone_por_defecto():
    """Cada herramienta del menú cobra peaje en cada iteración (§3): el
    default no puede ser exponerlas todas."""
    assert "alcance_normativo" not in construir_herramientas().nombres
    assert "alcance_normativo" in construir_herramientas(con_alcance=True).nombres


def test_costo_esquema_es_positivo_y_ordenable():
    reg = construir_herramientas(con_alcance=True)
    costos = {n: costo_esquema(reg.get(n)) for n in reg.nombres}
    assert all(c > 0 for c in costos.values())
    # El menú completo tiene que costar la suma de sus partes.
    assert sum(costos.values()) == sum(costo_esquema(reg.get(n)) for n in reg.nombres)


def test_tareas_congeladas_son_coherentes():
    tareas = cargar_tareas()
    assert len(tareas) >= 10
    assert {t.familia for t in tareas} == {"recuperacion", "estructural", "abstencion"}
    for t in tareas:
        assert t.origen.get("golden"), f"{t.id} sin procedencia declarada"
        if t.familia == "abstencion":
            assert t.docs_esperados == []
