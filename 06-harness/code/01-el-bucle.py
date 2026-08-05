"""§1 — El bucle percibir → decidir → actuar → observar.

Produce los números de `theory/01-que-es-un-harness.md`.

Diseño del experimento: **factorial 2×2**. El modelo, las herramientas, el
prompt de sistema, el tope de pasos y las 12 tareas son idénticos en los
cuatro brazos. Lo único que varía son dos reglas del entorno:

  factor A — estilo del error      : opaco   | contrato
  factor B — tamaño de observación : completa| acotada a 1.200 caracteres

Cruzarlos es lo que permite atribuir: con dos brazos en vez de cuatro, un
delta agregado no distingue si lo produjo el error o el truncado. Un quinto
brazo repite el peor caso con más presupuesto de pasos, para separar el
efecto del truncado del efecto de quedarse sin iteraciones.

Offline por defecto (lee el caché versionado). `--allow-api` solo para
regenerar el caché en una corrida controlada.

    uv run python 06-harness/code/01-el-bucle.py
    uv run python 06-harness/code/01-el-bucle.py --allow-api
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
    PoliticaLLM,
    ResultadoTarea,
    Tarea,
    Trayectoria,
    cargar_tareas,
    construir_herramientas,
    evaluar_trayectoria,
    llamadas_redundantes,
    recuperacion_tras_error,
)

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)
AQUI = Path(__file__).resolve().parent.parent
CACHE = AQUI / "examples" / "cache-bucle.json"
TRAYECTORIAS = AQUI / "examples" / "trayectorias-01.json"
DIAGRAMA = AQUI / "diagrams" / "harness-factorial.png"

MAX_CHARS = 1_200
MAX_PASOS = 8

BRAZOS = [
    HarnessConfig(
        nombre="opaco+completa",
        max_pasos=MAX_PASOS,
        max_chars_observacion=None,
        estilo_error="opaco",
    ),
    HarnessConfig(
        nombre="contrato+completa",
        max_pasos=MAX_PASOS,
        max_chars_observacion=None,
        estilo_error="contrato",
    ),
    HarnessConfig(
        nombre="opaco+acotada",
        max_pasos=MAX_PASOS,
        max_chars_observacion=MAX_CHARS,
        estilo_error="opaco",
    ),
    HarnessConfig(
        nombre="contrato+acotada",
        max_pasos=MAX_PASOS,
        max_chars_observacion=MAX_CHARS,
        estilo_error="contrato",
    ),
]

# Quinto brazo: el mismo entorno acotado, con presupuesto de pasos amplio.
# Sirve para separar "truncar pierde información" de "truncar exige más
# iteraciones y el tope no las daba".
BRAZO_HOLGADO = HarnessConfig(
    nombre="contrato+acotada (16 pasos)",
    max_pasos=16,
    max_chars_observacion=MAX_CHARS,
    estilo_error="contrato",
)


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def correr_brazo(
    config: HarnessConfig, politica: PoliticaLLM, tareas: list[Tarea]
) -> tuple[list[ResultadoTarea], list[Trayectoria]]:
    registry = construir_herramientas()
    resultados, trayectorias = [], []
    for tarea in tareas:
        tray = AgentLoop(registry, politica, config).correr(tarea.id, tarea.pregunta)
        trayectorias.append(tray)
        resultados.append(evaluar_trayectoria(tray, tarea))
    return resultados, trayectorias


def resumen(
    resultados: list[ResultadoTarea], trayectorias: list[Trayectoria]
) -> dict[str, float]:
    oportunidades, tasa = recuperacion_tras_error(trayectorias)
    return {
        "acierto": sum(r.acierto_exacto for r in resultados) / len(resultados),
        "f1": statistics.mean(r.f1 for r in resultados),
        "pasos": statistics.mean(r.n_pasos for r in resultados),
        "errores": sum(r.n_errores for r in resultados),
        "recup_n": oportunidades,
        "recup": tasa,
        "redundantes": sum(llamadas_redundantes(t) for t in trayectorias),
        "sin_respuesta": sum(
            r.motivo_corte is not MotivoCorte.RESPONDIO for r in resultados
        ),
        "tokens_in": sum(r.tokens_in for r in resultados),
        "costo": sum(r.costo_usd for r in resultados),
    }


FILAS = [
    ("acierto exacto", "acierto", "{:.3f}"),
    ("F1 de docs citados", "f1", "{:.3f}"),
    ("pasos promedio", "pasos", "{:.2f}"),
    ("pasos con error", "errores", "{:.0f}"),
    ("recuperación tras error", "recup", "{:.3f}"),
    ("llamadas redundantes", "redundantes", "{:.0f}"),
    ("tareas sin respuesta", "sin_respuesta", "{:.0f}"),
    ("tokens de entrada", "tokens_in", "{:.0f}"),
    ("costo USD", "costo", "{:.4f}"),
]


def tabla(resumenes: dict[str, dict[str, float]]) -> None:
    nombres = list(resumenes)
    print(f"{'métrica':<26}" + "".join(f"{n:>20}" for n in nombres))
    print("-" * (26 + 20 * len(nombres)))
    for etiqueta, clave, fmt in FILAS:
        print(
            f"{etiqueta:<26}"
            + "".join(f"{fmt.format(resumenes[n][clave]):>20}" for n in nombres)
        )


def efectos_principales(resumenes: dict[str, dict[str, float]]) -> None:
    """En un factorial 2×2, el efecto principal de un factor es el promedio
    de su efecto en los dos niveles del otro factor. Es lo que un diseño de
    dos brazos no puede calcular."""
    print(f"{'métrica':<26} {'efecto contrato':>18} {'efecto acotar':>18}")
    print("-" * 64)
    for etiqueta, clave, fmt in FILAS:
        contrato = (
            (resumenes["contrato+completa"][clave] - resumenes["opaco+completa"][clave])
            + (resumenes["contrato+acotada"][clave] - resumenes["opaco+acotada"][clave])
        ) / 2
        acotar = (
            (resumenes["opaco+acotada"][clave] - resumenes["opaco+completa"][clave])
            + (resumenes["contrato+acotada"][clave] - resumenes["contrato+completa"][clave])
        ) / 2
        print(f"{etiqueta:<26} {fmt.format(contrato):>18} {fmt.format(acotar):>18}")


def por_familia(resultados: list[ResultadoTarea]) -> dict[str, float]:
    familias: dict[str, list[ResultadoTarea]] = {}
    for r in resultados:
        familias.setdefault(r.familia, []).append(r)
    return {
        f: sum(r.acierto_exacto for r in rs) / len(rs)
        for f, rs in sorted(familias.items())
    }


def diagrama(
    resumenes: dict[str, dict[str, float]], nombres: list[str], holgado: str
) -> None:
    """Dos paneles, uno por hallazgo.

    Izquierda: el contrato de error mueve el proceso (pasos perdidos en
    error, recuperación) sin mover el resultado. Derecha: acotar la
    observación no pierde información — la recupera iterando, al doble de
    tokens.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5))
    colores = ["#c44e52", "#dd8452", "#937860", "#4c72b0"]
    etiquetas = [n.replace("+", "\n+") for n in nombres]

    x = range(len(nombres))
    ax1.bar(x, [resumenes[n]["errores"] for n in nombres], 0.55, color=colores,
            label="pasos perdidos en error")
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(etiquetas, fontsize=8)
    ax1.set_ylabel("pasos perdidos en error (12 tareas)")
    ax1.grid(axis="y", alpha=0.3)

    ax1b = ax1.twinx()
    ax1b.plot(list(x), [resumenes[n]["recup"] for n in nombres], "o--",
              color="#2b2b2b", label="recuperación tras error")
    ax1b.plot(list(x), [resumenes[n]["acierto"] for n in nombres], "s-",
              color="#55a868", label="acierto exacto")
    ax1b.set_ylim(0, 1.08)
    ax1b.set_ylabel("tasa")
    ax1b.legend(fontsize=8, loc="lower right")
    ax1.set_ylim(0, max(resumenes[n]["errores"] for n in nombres) * 1.18)
    ax1.set_title(
        "El contrato de error mueve el proceso,\nno el resultado", fontsize=11
    )

    todos = nombres + [holgado]
    aciertos = [resumenes[n]["acierto"] for n in todos]
    tokens = [resumenes[n]["tokens_in"] / 1000 for n in todos]
    ax2.bar(range(len(todos)), tokens, 0.55, color=colores + ["#55a868"])
    ax2.set_xticks(range(len(todos)))
    ax2.set_xticklabels(
        [n.replace("+", "\n+") for n in nombres] + ["contrato\n+acotada\n(16 pasos)"],
        fontsize=8,
    )
    ax2.set_ylabel("tokens de entrada acumulados (miles)")
    ax2.grid(axis="y", alpha=0.3)
    for i, (tok, ac) in enumerate(zip(tokens, aciertos)):
        ax2.text(i, tok + 3, f"acierto {ac:.3f}", ha="center", fontsize=8)
    ax2.set_ylim(0, max(tokens) * 1.2)
    ax2.set_title(
        "Acotar no pierde información:\nla recupera iterando, al doble de tokens",
        fontsize=11,
    )

    fig.tight_layout()
    DIAGRAMA.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(DIAGRAMA, dpi=140, bbox_inches="tight")
    print(f"\nDiagrama: {DIAGRAMA.relative_to(AQUI.parent)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-api", action="store_true", help="regenera el caché")
    args = parser.parse_args()

    tareas = cargar_tareas()
    politica = PoliticaLLM(cache_path=CACHE, allow_api=args.allow_api, max_api_calls=600)

    seccion("§1 · Factorial 2×2 de harness sobre doce tareas del corpus chileno")
    print(f"tareas: {len(tareas)}  |  modelo: {politica.model}  |  temperatura: 0.0")
    print(f"tope de pasos: {MAX_PASOS}  |  observación acotada: {MAX_CHARS} caracteres")
    print("constantes en los cuatro brazos: modelo, herramientas, prompt de sistema, tareas")

    resumenes: dict[str, dict[str, float]] = {}
    por_brazo: dict[str, list[ResultadoTarea]] = {}
    trayectorias_todas: dict[str, list[Trayectoria]] = {}
    for config in BRAZOS:
        res, tray = correr_brazo(config, politica, tareas)
        resumenes[config.nombre] = resumen(res, tray)
        por_brazo[config.nombre] = res
        trayectorias_todas[config.nombre] = tray

    seccion("Los cuatro brazos")
    tabla(resumenes)

    seccion("Efectos principales (promedio sobre los dos niveles del otro factor)")
    efectos_principales(resumenes)

    seccion("Acierto exacto por familia de tarea")
    print(f"{'brazo':<26}" + "".join(f"{f:>16}" for f in sorted(por_familia(por_brazo[BRAZOS[0].nombre]))))
    print("-" * 74)
    for nombre, res in por_brazo.items():
        fam = por_familia(res)
        print(f"{nombre:<26}" + "".join(f"{fam[f]:>16.3f}" for f in sorted(fam)))

    seccion("Por qué terminó el bucle")
    for nombre, res in por_brazo.items():
        cortes: dict[str, int] = {}
        for r in res:
            cortes[r.motivo_corte.value] = cortes.get(r.motivo_corte.value, 0) + 1
        print(f"{nombre:<26} {json.dumps(cortes, ensure_ascii=False)}")

    seccion("Control: el mismo entorno acotado con presupuesto de pasos amplio")
    res_h, tray_h = correr_brazo(BRAZO_HOLGADO, politica, tareas)
    resumenes[BRAZO_HOLGADO.nombre] = resumen(res_h, tray_h)
    trayectorias_todas[BRAZO_HOLGADO.nombre] = tray_h
    tabla(
        {
            "contrato+acotada": resumenes["contrato+acotada"],
            BRAZO_HOLGADO.nombre: resumenes[BRAZO_HOLGADO.nombre],
        }
    )

    seccion("Uso de API")
    ti, to = politica.historical_tokens
    print(f"llamadas de esta corrida : {politica.api_calls}")
    print(f"aciertos de caché        : {politica.aciertos_cache}")
    print(f"tokens históricos in/out : {ti:,} / {to:,}")
    print(f"costo histórico          : USD {politica.historical_cost_usd:.4f}")

    TRAYECTORIAS.write_text(
        json.dumps(
            {
                nombre: [t.model_dump(mode="json") for t in trays]
                for nombre, trays in trayectorias_todas.items()
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nTrayectorias: {TRAYECTORIAS.relative_to(AQUI.parent)}")
    diagrama(resumenes, [c.nombre for c in BRAZOS], BRAZO_HOLGADO.nombre)


if __name__ == "__main__":
    main()
