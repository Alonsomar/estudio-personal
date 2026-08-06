"""§5 — Arquitecturas multiagente: cuándo gana un solo agente.

Produce los números de `theory/05-multiagente.md`.

Dos arquitecturas sobre las mismas 12 tareas, el mismo modelo y las mismas
capacidades subyacentes:

  agente único  · cinco herramientas a la vista, un solo contexto que crece
  orquestador   · sólo puede 'delegar' y 'responder'; dos trabajadores
                  especializados (documental y estructural) resuelven en
                  bucles anidados con contexto propio

La comparación mide las dos caras: el orquestador mantiene su contexto chico
—que es el argumento a favor— y paga coordinación en tokens totales —que es
el argumento en contra—. Los dos números salen de la misma corrida.

Offline por defecto. `--allow-api` regenera el caché.

    uv run python 06-harness/code/05-multiagente.py
    uv run python 06-harness/code/05-multiagente.py --allow-api
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_lib import (  # noqa: E402
    AgentLoop,
    HarnessConfig,
    MotivoCorte,
    Partida,
    PoliticaLLM,
    ResultadoTarea,
    ToolRegistry,
    Trabajador,
    Trayectoria,
    cargar_tareas,
    construir_herramientas,
    costo_esquema,
    evaluar_trayectoria,
    herramienta_delegar,
)

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)
AQUI = Path(__file__).resolve().parent.parent
CACHE = AQUI / "examples" / "cache-multiagente.json"
CACHE_UNICO = AQUI / "examples" / "cache-granularidad.json"
TRAYECTORIAS = AQUI / "examples" / "trayectorias-05.json"
DIAGRAMA = AQUI / "diagrams" / "multiagente.png"

CONFIG = HarnessConfig(
    nombre="contrato+completa", max_pasos=8, max_chars_observacion=None,
    estilo_error="contrato",
)
# El trabajador con el error "ingenuo": su `siguiente_paso` puede mandarlo a
# usar una herramienta que su menú no tiene. Reproduce el fallo que esta
# sección documenta.
CONFIG_TRABAJADOR = HarnessConfig(
    nombre="trabajador", max_pasos=6, max_chars_observacion=None,
    estilo_error="contrato", avisar_herramienta_ausente=False,
)
# El mismo trabajador con el error consciente de su propio menú.
CONFIG_TRABAJADOR_CONSCIENTE = CONFIG_TRABAJADOR.model_copy(
    update={"nombre": "trabajador-consciente", "avisar_herramienta_ausente": True}
)
CONFIG_ORQUESTADOR = CONFIG.model_copy(update={"nombre": "orquestador"})


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def construir_trabajadores(
    base: ToolRegistry,
    config: HarnessConfig = CONFIG_TRABAJADOR,
    *,
    estructural_busca: bool = False,
) -> list[Trabajador]:
    """Dos trabajadores, divididos por la frontera natural del dominio: el
    texto de las normas por un lado, la estructura de relaciones por el otro.

    La división no es arbitraria — es la misma que separa a `02-retrieval` de
    `05-ontologias`, y por eso cada trabajador tiene un menú coherente."""
    return [
        Trabajador(
            nombre="documental",
            descripcion=(
                "busca y lee el texto de las normas; responde qué dice el corpus"
            ),
            registry=base.subconjunto(["buscar_corpus", "leer_norma", "responder"]),
            config=config,
            instrucciones=(
                "Sos un especialista en el texto del corpus. Buscá y leé los "
                "documentos necesarios y respondé la subtarea con precisión, "
                "citando los archivos que la sustentan."
            ),
        ),
        Trabajador(
            nombre="estructural",
            descripcion=(
                "recorre el grafo normativo; responde qué norma modifica, "
                "reglamenta, deroga o depende de cuál"
            ),
            # Resolver "DS 250" a su identificador canónico es un prerrequisito
            # de recorrer el grafo. `estructural_busca=True` no cambia la
            # división del trabajo: le devuelve al trabajador la capacidad que
            # su propia tarea necesitaba y que la frontera le había cortado.
            registry=base.subconjunto(
                ["vecinos_grafo", "alcance_normativo", "responder"]
                + (["buscar_corpus"] if estructural_busca else [])
            ),
            config=config,
            instrucciones=(
                "Sos un especialista en relaciones entre normas. Usá el grafo "
                "normativo para responder. Si te dan el nombre de una norma y no "
                "su identificador de archivo, decilo en la respuesta en vez de "
                "adivinar."
            ),
        ),
    ]


def correr_unico(politica, tareas):
    registry = construir_herramientas(con_alcance=True)
    resultados, trayectorias = [], []
    for tarea in tareas:
        tray = AgentLoop(registry, politica, CONFIG, medir_contexto=True).correr(
            tarea.id, tarea.pregunta
        )
        trayectorias.append(tray)
        resultados.append(evaluar_trayectoria(tray, tarea))
    return resultados, trayectorias, []


def correr_orquestado(
    politica, tareas, config_trabajador=CONFIG_TRABAJADOR, *, estructural_busca=False
):
    base = construir_herramientas(con_alcance=True)
    resultados, trayectorias, subtrayectorias = [], [], []
    for tarea in tareas:
        registro: list[Trayectoria] = []
        trabajadores = construir_trabajadores(
            base, config_trabajador, estructural_busca=estructural_busca
        )
        orquestador = ToolRegistry(
            [
                herramienta_delegar(trabajadores, politica, registro),
                base.get("responder"),
            ]
        )
        tray = AgentLoop(
            orquestador, politica, CONFIG_ORQUESTADOR, medir_contexto=True
        ).correr(tarea.id, tarea.pregunta)
        trayectorias.append(tray)
        subtrayectorias.append(registro)
        resultados.append(evaluar_trayectoria(tray, tarea))
    return resultados, trayectorias, subtrayectorias


def tokens_totales(trayectorias, subtrayectorias) -> int:
    total = sum(t.tokens_in for t in trayectorias)
    for registro in subtrayectorias:
        total += sum(t.tokens_in for t in registro)
    return total


def pasos_totales(trayectorias, subtrayectorias) -> int:
    return sum(t.n_pasos for t in trayectorias) + sum(
        t.n_pasos for r in subtrayectorias for t in r
    )


def contexto_maximo(trayectorias) -> float:
    """El contexto más grande que tuvo que procesar el modelo en una sola
    llamada. Es la métrica que el multiagente pretende mejorar."""
    picos = [
        max((sum(p.contexto.values()) for p in t.pasos if p.contexto), default=0)
        for t in trayectorias
    ]
    return statistics.mean(picos)


def por_familia(resultados: list[ResultadoTarea]) -> dict[str, float]:
    familias: dict[str, list[ResultadoTarea]] = {}
    for r in resultados:
        familias.setdefault(r.familia, []).append(r)
    return {f: sum(r.acierto_exacto for r in rs) / len(rs) for f, rs in sorted(familias.items())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-api", action="store_true")
    args = parser.parse_args()

    tareas = cargar_tareas()
    pol_unico = PoliticaLLM(cache_path=CACHE_UNICO, allow_api=False)
    pol_multi = PoliticaLLM(cache_path=CACHE, allow_api=args.allow_api, max_api_calls=600)

    seccion("§5 · Un agente contra orquestador + dos trabajadores")
    base = construir_herramientas(con_alcance=True)
    trabajadores = construir_trabajadores(base)
    registro_vacio: list[Trayectoria] = []
    orq = ToolRegistry(
        [herramienta_delegar(trabajadores, pol_multi, registro_vacio), base.get("responder")]
    )
    print("agente único  : " + ", ".join(base.nombres))
    print("orquestador   : " + ", ".join(orq.nombres))
    for t in trabajadores:
        print(f"  trabajador '{t.nombre:<12}': " + ", ".join(t.registry.nombres))

    print(f"\n{'menú':<26}{'tokens de esquema':>20}")
    print("-" * 46)
    print(f"{'agente único (5 tools)':<26}{sum(costo_esquema(base.get(n)) for n in base.nombres):>20,}")
    print(f"{'orquestador (2 tools)':<26}{sum(costo_esquema(orq.get(n)) for n in orq.nombres):>20,}")
    for t in trabajadores:
        etiqueta = f"trabajador {t.nombre}"
        print(f"{etiqueta:<26}{sum(costo_esquema(t.registry.get(n)) for n in t.registry.nombres):>20,}")

    res_u, tray_u, sub_u = correr_unico(pol_unico, tareas)
    res_m, tray_m, sub_m = correr_orquestado(pol_multi, tareas)

    seccion("Resultado")
    filas = [
        ("acierto exacto", lambda r, t, s: sum(x.acierto_exacto for x in r) / len(r), "{:.3f}"),
        ("F1 de docs citados", lambda r, t, s: statistics.mean(x.f1 for x in r), "{:.3f}"),
        ("tareas sin respuesta",
         lambda r, t, s: sum(x.motivo_corte is not MotivoCorte.RESPONDIO for x in r), "{:.0f}"),
        ("pasos del agente principal", lambda r, t, s: sum(x.n_pasos for x in t), "{:.0f}"),
        ("pasos de trabajadores",
         lambda r, t, s: sum(x.n_pasos for reg in s for x in reg), "{:.0f}"),
        ("pasos totales", lambda r, t, s: pasos_totales(t, s), "{:.0f}"),
        ("llamadas al modelo (=pasos)", lambda r, t, s: pasos_totales(t, s), "{:.0f}"),
        ("tokens de entrada TOTALES", lambda r, t, s: tokens_totales(t, s), "{:,.0f}"),
        ("contexto máximo por llamada", lambda r, t, s: contexto_maximo(t), "{:,.0f}"),
    ]
    print(f"{'métrica':<30}{'agente único':>18}{'orquestado':>16}{'delta':>14}")
    print("-" * 78)
    for etiqueta, fn, fmt in filas:
        a = fn(res_u, tray_u, sub_u)
        b = fn(res_m, tray_m, sub_m)
        print(f"{etiqueta:<30}{fmt.format(a):>18}{fmt.format(b):>16}{fmt.format(b - a):>14}")

    seccion("Acierto por familia")
    fam_u, fam_m = por_familia(res_u), por_familia(res_m)
    print(f"{'familia':<16}{'agente único':>16}{'orquestado':>14}{'delta':>10}")
    print("-" * 56)
    for f in sorted(fam_u):
        print(f"{f:<16}{fam_u[f]:>16.3f}{fam_m[f]:>14.3f}{fam_m[f] - fam_u[f]:>10.3f}")

    seccion("El aislamiento de contexto, medido")
    reparto_u = {p.value: 0 for p in Partida}
    reparto_m = {p.value: 0 for p in Partida}
    for t in tray_u:
        for p in t.pasos:
            for k, v in p.contexto.items():
                reparto_u[k] += v
    for t in tray_m:
        for p in t.pasos:
            for k, v in p.contexto.items():
                reparto_m[k] += v
    print(f"{'partida':<16}{'agente único':>16}{'orquestador':>16}")
    print("-" * 48)
    for p in Partida:
        print(f"{p.value:<16}{reparto_u[p.value]:>16,}{reparto_m[p.value]:>16,}")
    print(
        "\nLas observaciones del orquestador son sólo los resúmenes que le devuelven\n"
        "los trabajadores. Todo lo que el trabajador leyó murió en su propio bucle:\n"
        "eso es el aislamiento, y se ve en esa fila."
    )

    seccion("Dónde se rompe la delegación")
    for tarea, tray, registro in zip(tareas, tray_m, sub_m):
        if not registro:
            continue
        for sub in registro:
            if sub.motivo_corte is not MotivoCorte.RESPONDIO or not sub.docs_citados:
                print(
                    f"  {tarea.id} → '{sub.tarea_id}' ({sub.n_pasos} pasos, "
                    f"{sub.motivo_corte.value}) citó {sub.docs_citados or '(nada)'}"
                )
                print(f"      subtarea: {sub.pregunta[:100]}")

    seccion("El arreglo: que el error conozca el menú del trabajador")
    print(
        "El error de 'vecinos_grafo' dice «usá 'buscar_corpus' para ubicar el\n"
        "documento», y el trabajador estructural no tiene 'buscar_corpus'. El\n"
        "registro ahora lo detecta y lo avisa. Mismo modelo, misma división del\n"
        "trabajo, mismas herramientas: sólo cambia el texto del error.\n"
    )
    res_f, tray_f, sub_f = correr_orquestado(
        pol_multi, tareas, CONFIG_TRABAJADOR_CONSCIENTE
    )
    print(f"{'métrica':<30}{'error ingenuo':>18}{'error consciente':>18}{'delta':>12}")
    print("-" * 78)
    for etiqueta, fn, fmt in filas:
        a = fn(res_m, tray_m, sub_m)
        b = fn(res_f, tray_f, sub_f)
        print(f"{etiqueta:<30}{fmt.format(a):>18}{fmt.format(b):>18}{fmt.format(b - a):>12}")

    fallidos = lambda s: sum(  # noqa: E731
        1 for reg in s for x in reg if x.motivo_corte is not MotivoCorte.RESPONDIO
    )
    print(
        f"\nsubtareas que el trabajador no concluyó: "
        f"{fallidos(sub_m)} → {fallidos(sub_f)}"
    )

    seccion("El arreglo de fondo: no partir una dependencia")
    print(
        "Resolver 'DS 250' a su identificador canónico es un prerrequisito de\n"
        "recorrer el grafo, y la frontera departamental lo dejó del otro lado.\n"
        "Acá el trabajador estructural recupera 'buscar_corpus': mismo esquema de\n"
        "delegación, una herramienta más en el menú del subagente.\n"
    )
    res_d, tray_d, sub_d = correr_orquestado(
        pol_multi, tareas, CONFIG_TRABAJADOR_CONSCIENTE, estructural_busca=True
    )
    print(f"{'métrica':<30}{'agente único':>16}{'partido':>12}{'sin partir':>14}")
    print("-" * 74)
    for etiqueta, fn, fmt in filas:
        print(
            f"{etiqueta:<30}{fmt.format(fn(res_u, tray_u, sub_u)):>16}"
            f"{fmt.format(fn(res_f, tray_f, sub_f)):>12}"
            f"{fmt.format(fn(res_d, tray_d, sub_d)):>14}"
        )
    print(
        f"\nsubtareas sin concluir: {fallidos(sub_f)} → {fallidos(sub_d)}"
    )
    fam_d = por_familia(res_d)
    print(f"\n{'familia':<16}{'agente único':>16}{'partido':>12}{'sin partir':>14}")
    print("-" * 60)
    for f in sorted(fam_u):
        print(f"{f:<16}{fam_u[f]:>16.3f}{por_familia(res_f)[f]:>12.3f}{fam_d[f]:>14.3f}")

    TRAYECTORIAS.write_text(
        json.dumps(
            {
                "orquestador": [t.model_dump(mode="json") for t in tray_m],
                "trabajadores": [
                    [t.model_dump(mode="json") for t in registro] for registro in sub_m
                ],
                "orquestador_error_consciente": [
                    t.model_dump(mode="json") for t in tray_f
                ],
                "trabajadores_error_consciente": [
                    [t.model_dump(mode="json") for t in registro] for registro in sub_f
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nTrayectorias: {TRAYECTORIAS.relative_to(AQUI.parent)}")

    seccion("Uso de API")
    print(f"llamadas de esta corrida : {pol_multi.api_calls}")
    print(f"aciertos de caché        : {pol_multi.aciertos_cache}")
    print(f"costo histórico          : USD {pol_multi.historical_cost_usd:.4f}")

    diagrama(res_u, res_m, tray_u, tray_m, sub_u, sub_m)


def diagrama(res_u, res_m, tray_u, tray_m, sub_u, sub_m) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    etiquetas = ["agente único", "orquestado"]
    colores = ["#4c72b0", "#dd8452"]

    picos = [contexto_maximo(tray_u), contexto_maximo(tray_m)]
    totales = [tokens_totales(tray_u, sub_u), tokens_totales(tray_m, sub_m)]
    x = range(2)
    ax1.bar([i - 0.2 for i in x], picos, 0.38, color="#55a868",
            label="contexto máximo por llamada")
    ax1.set_ylabel("tokens en la llamada más grande", color="#55a868")
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(etiquetas)
    ax1b = ax1.twinx()
    ax1b.bar([i + 0.2 for i in x], totales, 0.38, color="#c44e52",
             label="tokens de entrada totales")
    ax1b.set_ylabel("tokens de entrada totales", color="#c44e52")
    ax1.set_title("Las dos caras del multiagente:\ncontexto más chico, gasto más grande")
    ax1.grid(axis="y", alpha=0.3)

    fam_u, fam_m = por_familia(res_u), por_familia(res_m)
    familias = sorted(fam_u)
    xf = range(len(familias))
    ax2.bar([i - 0.19 for i in xf], [fam_u[f] for f in familias], 0.38,
            label="agente único", color=colores[0])
    ax2.bar([i + 0.19 for i in xf], [fam_m[f] for f in familias], 0.38,
            label="orquestado", color=colores[1])
    ax2.set_xticks(list(xf))
    ax2.set_xticklabels(familias)
    ax2.set_ylim(0, 1.05)
    ax2.set_ylabel("acierto exacto")
    ax2.legend(fontsize=9)
    ax2.grid(axis="y", alpha=0.3)
    ax2.set_title("¿Y qué compró ese gasto?")

    fig.tight_layout()
    DIAGRAMA.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(DIAGRAMA, dpi=140, bbox_inches="tight")
    print(f"\nDiagrama: {DIAGRAMA.relative_to(AQUI.parent)}")


if __name__ == "__main__":
    main()
