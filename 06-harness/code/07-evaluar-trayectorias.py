"""§7 — Evaluar agentes: se evalúan trayectorias, no respuestas.

Produce los números de `theory/07-evaluar-trayectorias.md`. Criterio de
aceptación de `B8`: un agente evaluado con métricas de trayectoria sobre una
tarea del dominio, con el aparato estadístico de `01 §8`.

Cuatro sistemas, todos ya corridos en secciones anteriores y reconstruidos
acá desde caché (cero llamadas a la API):

  A · grano fino      §1, cuatro herramientas
  B · grano grueso    §3, con `alcance_normativo`
  C · orquestado      §5, orquestador + dos trabajadores
  D · sin partir      §5, con la dependencia de entity resolution reunida

Se miden con el mismo golden de 12 tareas y se comparan con bootstrap sobre
las 12 tareas (`01 §8`). El resultado y el proceso se reportan por separado
justamente porque no coinciden.

    uv run python 06-harness/code/07-evaluar-trayectorias.py
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_lib import (  # noqa: E402
    AgentLoop,
    HarnessConfig,
    MotivoCorte,
    PoliticaLLM,
    ToolRegistry,
    Trayectoria,
    cargar_tareas,
    construir_herramientas,
    docs_observados,
    evaluar_trayectoria,
    herramienta_delegar,
    metricas_trayectoria,
    normalizar_cita,
)
from retrieval_lib import bootstrap_ci  # noqa: E402

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)
AQUI = Path(__file__).resolve().parent.parent
DIAGRAMA = AQUI / "diagrams" / "trayectorias.png"
SALIDA = AQUI / "examples" / "metricas-trayectoria.json"

ETIQUETA_METRICA = {
    "acierto": "acierto exacto",
    "f1": "F1 de documentos",
    "eficiencia": "eficiencia",
    "pasos": "pasos del principal",
}

CONFIG = HarnessConfig(
    nombre="contrato+completa", max_pasos=8, max_chars_observacion=None,
    estilo_error="contrato",
)


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def sistemas(tareas):
    """Reconstruye los cuatro sistemas desde sus cachés. Ninguno llama a la
    API: las decisiones son exactamente las que cada sección registró."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    mod = _cargar_multiagente()

    salida = {}

    pol1 = PoliticaLLM(cache_path=AQUI / "examples" / "cache-bucle.json")
    reg1 = construir_herramientas()
    salida["A · grano fino"] = [
        (AgentLoop(reg1, pol1, CONFIG).correr(t.id, t.pregunta), [], t) for t in tareas
    ]

    pol2 = PoliticaLLM(cache_path=AQUI / "examples" / "cache-granularidad.json")
    reg2 = construir_herramientas(con_alcance=True)
    salida["B · grano grueso"] = [
        (AgentLoop(reg2, pol2, CONFIG).correr(t.id, t.pregunta), [], t) for t in tareas
    ]

    pol3 = PoliticaLLM(cache_path=AQUI / "examples" / "cache-multiagente.json")
    for etiqueta, partido in (("C · orquestado", False), ("D · sin partir", True)):
        base = construir_herramientas(con_alcance=True)
        filas = []
        for tarea in tareas:
            registro: list[Trayectoria] = []
            trabajadores = mod.construir_trabajadores(
                base, mod.CONFIG_TRABAJADOR_CONSCIENTE, estructural_busca=partido
            )
            orq = ToolRegistry(
                [herramienta_delegar(trabajadores, pol3, registro), base.get("responder")]
            )
            tray = AgentLoop(orq, pol3, mod.CONFIG_ORQUESTADOR).correr(
                tarea.id, tarea.pregunta
            )
            filas.append((tray, registro, tarea))
        salida[etiqueta] = filas
    return salida


def _cargar_multiagente():
    """`05-multiagente.py` no es un identificador Python válido, así que se
    carga por ruta en vez de duplicar la definición de los trabajadores."""
    import importlib.util

    ruta = Path(__file__).resolve().parent / "05-multiagente.py"
    spec = importlib.util.spec_from_file_location("multiagente", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    tareas = cargar_tareas()
    corridas = sistemas(tareas)

    seccion("§7 · Resultado contra proceso, sobre los mismos cuatro sistemas")
    print(
        "El resultado es una sola métrica y esconde todo lo demás. Estas son las\n"
        "dos vistas del mismo experimento, lado a lado.\n"
    )

    resumen: dict[str, dict] = {}
    for etiqueta, filas in corridas.items():
        res = [evaluar_trayectoria(t, tarea) for t, _, tarea in filas]
        met = [metricas_trayectoria(t, subs) for t, subs, _ in filas]
        pasos_totales = sum(m.pasos for m in met) + sum(
            s.n_pasos for _, subs, _ in filas for s in subs
        )
        resumen[etiqueta] = {
            "acierto": [float(r.acierto_exacto) for r in res],
            "f1": [r.f1 for r in res],
            "eficiencia": [m.eficiencia for m in met],
            "citas_fundadas": [m.citas_fundadas for m in met],
            "pasos": [float(m.pasos) for m in met],
            "invalidas": sum(m.llamadas_invalidas for m in met),
            "redundantes": sum(m.llamadas_redundantes for m in met),
            "fantasma": sum(m.citas_fantasma for m in met),
            "respondio": sum(m.respondio for m in met),
            "llamadas_totales": pasos_totales,
            "metricas": met,
            "resultados": res,
            "filas": filas,
        }

    print(f"{'sistema':<20}{'acierto':>9}{'F1':>8}{'  |':>3}{'eficiencia':>12}"
          f"{'citas fund.':>13}{'inválidas':>11}{'redund.':>9}{'llamadas':>10}")
    print("-" * 95)
    for etiqueta, d in resumen.items():
        print(
            f"{etiqueta:<20}{statistics.mean(d['acierto']):>9.3f}"
            f"{statistics.mean(d['f1']):>8.3f}{'  |':>3}"
            f"{statistics.mean(d['eficiencia']):>12.3f}"
            f"{statistics.mean(d['citas_fundadas']):>13.3f}"
            f"{d['invalidas']:>11}{d['redundantes']:>9}{d['llamadas_totales']:>10}"
        )
    print(
        "\nLa mitad izquierda es lo que ve una evaluación de resultado. La derecha es\n"
        "lo que costó producirlo — y es donde los sistemas de verdad se separan."
    )

    seccion("Deltas con IC bootstrap sobre las 12 tareas (01 §8)")
    base = "A · grano fino"
    print(f"{'comparación':<38}{'métrica':<22}{'delta':>9}{'IC 95%':>22}")
    print("-" * 86)
    for etiqueta in list(corridas)[1:]:
        for metrica in ("acierto", "f1", "eficiencia", "pasos"):
            pares = [
                b - a
                for a, b in zip(resumen[base][metrica], resumen[etiqueta][metrica])
            ]
            media, lo, hi = bootstrap_ci(pares, n_boot=2000, seed=7)
            signo = "" if lo <= 0 <= hi else "  ← excluye cero"
            print(
                f"{(etiqueta + ' vs ' + base):<38}{ETIQUETA_METRICA[metrica]:<22}{media:>9.3f}"
                f"{f'[{lo:.3f}; {hi:.3f}]':>22}{signo}"
            )
        print()

    seccion("La pregunta que el conjunto de documentos no puede responder")
    print(
        "¿El agente vio lo que citó? `docs_observados` reconstruye qué "
        "identificadores\npasaron efectivamente por su contexto. Dos preguntas "
        "distintas:\n"
        "  · citas sin respaldo  — citó algo que nunca apareció en una observación\n"
        "  · acierto sin mirar   — acertó sin que la evidencia esperada pasara por "
        "su contexto\n"
    )
    print(f"{'sistema':<20}{'citas sin respaldo':>20}{'acierto sin mirar':>20}")
    print("-" * 62)
    for etiqueta, d in resumen.items():
        suertudos = []
        for (tray, subs, tarea), res in zip(d["filas"], d["resultados"]):
            if not res.acierto_exacto or not tarea.docs_esperados:
                continue
            no_vistos = set(tarea.docs_esperados) - docs_observados(tray, subs)
            if no_vistos:
                suertudos.append(tarea.id)
        detalle = ", ".join(suertudos) if suertudos else "ninguna"
        print(f"{etiqueta:<20}{d['fantasma']:>20}{detalle:>20}")

    seccion("Las 'citas sin respaldo' de B, miradas de cerca")
    for (tray, subs, tarea), res in zip(
        resumen["B · grano grueso"]["filas"], resumen["B · grano grueso"]["resultados"]
    ):
        sin_respaldo = set(tray.docs_citados) - docs_observados(tray, subs)
        if not sin_respaldo:
            continue
        print(f"  {tarea.id}  acierto={res.acierto_exacto}")
        print(f"    citó     : {sorted(tray.docs_citados)}")
        print(f"    observó  : {sorted(docs_observados(tray, subs))}")
        print(
            "    → No son alucinaciones: son identificadores de FRAGMENTO "
            "('archivo.txt#12')\n      donde el contrato pedía el del documento. "
            "La evidencia estaba a la vista."
        )

    seccion("Sensibilidad de la métrica al formato de la cita")
    print(
        "Si se normalizan las citas al identificador de documento (se descarta el\n"
        "'#n' del fragmento), ¿cuánto cambia el acierto? Es la pregunta que hay que\n"
        "hacerle a cualquier métrica antes de creerle un delta.\n"
    )
    print(f"{'sistema':<20}{'acierto estricto':>18}{'acierto normalizado':>21}{'delta':>9}")
    print("-" * 70)
    for etiqueta, d in resumen.items():
        estricto = statistics.mean(d["acierto"])
        normalizados = []
        for (tray, _, tarea), _res in zip(d["filas"], d["resultados"]):
            if tray.motivo_corte is not MotivoCorte.RESPONDIO:
                normalizados.append(0.0)
                continue
            citados = {normalizar_cita(c) for c in tray.docs_citados}
            normalizados.append(float(citados == set(tarea.docs_esperados)))
        norm = statistics.mean(normalizados)
        print(f"{etiqueta:<20}{estricto:>18.3f}{norm:>21.3f}{norm - estricto:>9.3f}")
    print(
        "\nLa métrica principal del módulo se mantiene ESTRICTA y consistente en todas\n"
        "las secciones. Este cuadro no la reemplaza: mide cuánto de lo que llama\n"
        "error es en realidad un formato mal escrito, que es una información\n"
        "distinta y accionable — se arregla en el contrato de la herramienta, no\n"
        "cambiando de modelo."
    )

    seccion("Qué mide cada métrica y qué no puede ver")
    tabla = [
        ("acierto exacto", "si el conjunto citado es el esperado",
         "cómo se llegó; el costo; si se adivinó"),
        ("F1 de documentos", "acierto parcial", "lo mismo, con grano más fino"),
        ("eficiencia", "fracción de pasos sin error", "si los pasos sin error servían"),
        ("citas fundadas", "si el agente vio lo que citó", "si lo leyó de verdad"),
        ("llamadas redundantes", "trabajo repetido", "trabajo inútil pero distinto"),
        ("llamadas totales", "el costo real del sistema", "cómo se reparte entre tareas"),
        ("efectos no solicitados", "lo que el agente hizo y nadie pidió (§6)",
         "nada: es la métrica que salva el caso de §6"),
    ]
    print(f"{'métrica':<24}{'ve':<42}{'no ve'}")
    print("-" * 100)
    for m, ve, no_ve in tabla:
        print(f"{m:<24}{ve:<42}{no_ve}")

    SALIDA.write_text(
        json.dumps(
            {
                etiqueta: [m.model_dump(mode="json") for m in d["metricas"]]
                for etiqueta, d in resumen.items()
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nMétricas por tarea: {SALIDA.relative_to(AQUI.parent)}")
    diagrama(resumen)


def diagrama(resumen) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5))
    etiquetas = list(resumen)
    cortos = [e.split(" · ")[1] for e in etiquetas]
    colores = ["#4c72b0", "#55a868", "#dd8452", "#c44e52"]

    aciertos = [statistics.mean(resumen[e]["acierto"]) for e in etiquetas]
    llamadas = [resumen[e]["llamadas_totales"] for e in etiquetas]
    ax1.scatter(llamadas, aciertos, s=200, c=colores, zorder=3)
    # Los cuatro puntos caen de a pares casi encima; los offsets se alternan
    # para que las etiquetas no se pisen.
    offsets = [(0, 18), (0, -26), (0, 18), (0, -26)]
    for corto, x, y, off in zip(cortos, llamadas, aciertos, offsets):
        ax1.annotate(corto, (x, y), textcoords="offset points", xytext=off,
                     ha="center", fontsize=9)
    ax1.set_xlabel("llamadas al modelo (12 tareas)")
    ax1.set_ylabel("acierto exacto")
    ax1.set_ylim(0.3, 0.75)
    ax1.grid(alpha=0.3)
    ax1.set_title("Cuatro sistemas, casi el mismo resultado,\ncuatro costos distintos")

    x = range(len(etiquetas))
    ancho = 0.38
    ax2.bar([i - ancho / 2 for i in x],
            [statistics.mean(resumen[e]["eficiencia"]) for e in etiquetas],
            ancho, color="#55a868", label="eficiencia (pasos sin error)")
    ax2.bar([i + ancho / 2 for i in x],
            [statistics.mean(resumen[e]["citas_fundadas"]) for e in etiquetas],
            ancho, color="#4c72b0", label="citas fundadas (vio lo que citó)")
    ax2.set_xticks(list(x))
    ax2.set_xticklabels(cortos, fontsize=9)
    ax2.set_ylim(0, 1.1)
    ax2.legend(fontsize=9)
    ax2.grid(axis="y", alpha=0.3)
    ax2.set_title("Métricas de proceso:\nlo que el resultado no muestra")

    fig.tight_layout()
    DIAGRAMA.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(DIAGRAMA, dpi=140, bbox_inches="tight")
    print(f"\nDiagrama: {DIAGRAMA.relative_to(AQUI.parent)}")


if __name__ == "__main__":
    main()
