"""§2 — El contexto como problema de asignación.

Produce los números de `theory/02-contexto-escaso.md`. Tres partes:

  A. El presupuesto medido sobre las trayectorias reales de §1: cuánto pesa
     cada partida y cómo se mueve el reparto a medida que el bucle avanza.
  B. Costo contrafáctico de tres políticas de compactación sobre esas mismas
     trayectorias. Es contabilidad, no comportamiento: dice cuántos tokens se
     habrían enviado, no qué habría respondido el modelo.
  C. La corrida real con compactación y memoria externa, que es lo único que
     puede decir si la política además conserva la calidad.

Offline por defecto. `--allow-api` regenera el caché de la parte C.

    uv run python 06-harness/code/02-contexto-escaso.py
    uv run python 06-harness/code/02-contexto-escaso.py --allow-api
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
    MemoriaExterna,
    MotivoCorte,
    Partida,
    PoliticaLLM,
    SinCompactar,
    Tarea,
    VentanaConIndice,
    VentanaDeslizante,
    cargar_tareas,
    construir_herramientas,
    contar_tokens,
    docs_mencionados,
    evaluar_trayectoria,
    herramienta_memoria,
    presupuesto_contexto,
    tokenizador_exacto,
)

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)
AQUI = Path(__file__).resolve().parent.parent
CACHE = AQUI / "examples" / "cache-bucle.json"
CACHE_MEMORIA = AQUI / "examples" / "cache-compactacion.json"
DIAGRAMA = AQUI / "diagrams" / "presupuesto-contexto.png"

BASE = HarnessConfig(
    nombre="base", max_pasos=8, max_chars_observacion=None, estilo_error="contrato"
)
VENTANA_K = 2


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


# --------------------------------------------------------------------------- #
# A. El presupuesto medido.
# --------------------------------------------------------------------------- #
def parte_a(politica: PoliticaLLM, tareas: list[Tarea]):
    registry = construir_herramientas()
    specs = registry.specs_openai()
    trayectorias, trazas = [], []
    for tarea in tareas:
        traza: list[list[dict]] = []
        loop = AgentLoop(registry, politica, BASE, medir_contexto=True)
        trayectorias.append(loop.correr(tarea.id, tarea.pregunta, traza=traza))
        trazas.append(traza)

    seccion("A · Las cinco partidas del presupuesto de contexto")
    print(f"tokenizador exacto (tiktoken): {tokenizador_exacto()}")

    # Reparto en la primera y en la última iteración de cada tarea.
    primera = {p.value: [] for p in Partida}
    ultima = {p.value: [] for p in Partida}
    for tray in trayectorias:
        pasos = [p for p in tray.pasos if p.contexto]
        if not pasos:
            continue
        for clave in primera:
            primera[clave].append(pasos[0].contexto[clave])
            ultima[clave].append(pasos[-1].contexto[clave])

    print(
        f"\n{'partida':<16}{'iteración 1':>14}{'última iteración':>20}{'crece':>10}"
    )
    print("-" * 60)
    for p in Partida:
        a = statistics.mean(primera[p.value])
        b = statistics.mean(ultima[p.value])
        print(f"{p.value:<16}{a:>14.0f}{b:>20.0f}{'sí' if b > a + 1 else 'no':>10}")
    tot_a = sum(statistics.mean(primera[p.value]) for p in Partida)
    tot_b = sum(statistics.mean(ultima[p.value]) for p in Partida)
    print(f"{'TOTAL':<16}{tot_a:>14.0f}{tot_b:>20.0f}{'×%.1f' % (tot_b / tot_a):>10}")

    # El multiplicador de reenvío.
    seccion("A · El multiplicador de reenvío")
    print(
        "Cada iteración manda de nuevo todo el historial. El costo de una tarea\n"
        "no es el tamaño de su contexto final: es la suma de todos los contextos.\n"
    )
    print(f"{'tarea':<8}{'pasos':>7}{'contexto final':>16}{'enviado total':>16}{'multiplicador':>15}")
    print("-" * 62)
    multiplicadores = []
    fijos_totales = enviados_totales = 0
    for tray, traza in zip(trayectorias, trazas):
        if not traza:
            continue
        enviado = sum(sum(presupuesto_contexto(m, specs).values()) for m in traza)
        final = sum(presupuesto_contexto(traza[-1], specs).values())
        mult = enviado / final if final else 0
        multiplicadores.append(mult)
        enviados_totales += enviado
        fijo = presupuesto_contexto(traza[0], specs)
        fijos_totales += (
            fijo[Partida.SISTEMA.value]
            + fijo[Partida.HERRAMIENTAS.value]
            + fijo[Partida.PREGUNTA.value]
        ) * len(traza)
        print(f"{tray.tarea_id:<8}{len(traza):>7}{final:>16,}{enviado:>16,}{mult:>15.2f}")
    print("-" * 62)
    print(f"{'media':<8}{'':>7}{'':>16}{'':>16}{statistics.mean(multiplicadores):>15.2f}")
    print(
        f"\nDel total enviado ({enviados_totales:,} tokens), el prefijo fijo "
        f"(sistema + esquemas + pregunta)\nse reenvió {fijos_totales:,} veces-token: "
        f"{100 * fijos_totales / enviados_totales:.1f}% de todo el gasto de entrada."
    )
    return trayectorias, trazas, specs


# --------------------------------------------------------------------------- #
# B. Costo contrafáctico de las políticas de compactación.
# --------------------------------------------------------------------------- #
def parte_b(trayectorias, trazas, specs) -> dict[str, dict[str, float]]:
    seccion("B · Qué habría costado cada política de compactación")
    print(
        "Contabilidad, no comportamiento: se recompacta el historial que el agente\n"
        "efectivamente produjo. Con otro contexto el modelo pudo haber decidido otra\n"
        "cosa; la parte C corre esa versión de verdad.\n"
    )

    # `VentanaConIndice` no es gratis en la partida de herramientas: obliga a
    # exponer 'recuperar_memoria', cuyo esquema viaja en cada iteración. Una
    # contabilidad que compare políticas con el mismo juego de herramientas
    # le regala esa diferencia — y era el error de la primera versión.
    specs_memoria = specs + [herramienta_memoria(MemoriaExterna()).spec_openai()]
    politicas = [
        (SinCompactar(), specs),
        (VentanaDeslizante(k=VENTANA_K), specs),
        (VentanaConIndice(MemoriaExterna(), k=VENTANA_K), specs_memoria),
    ]
    filas = {}
    for pol, specs_pol in politicas:
        total = 0
        retenidos = esperados = 0
        for tray, traza in zip(trayectorias, trazas):
            for mensajes in traza:
                total += sum(
                    presupuesto_contexto(pol.compactar(mensajes), specs_pol).values()
                )
            if traza and tray.docs_citados:
                visible = " ".join(
                    (m.get("content") or "") for m in pol.compactar(traza[-1])
                )
                mencionados = set(docs_mencionados(visible))
                esperados += len(tray.docs_citados)
                retenidos += sum(1 for d in tray.docs_citados if d in mencionados)
        filas[pol.nombre] = {
            "tokens": total,
            "retencion": retenidos / esperados if esperados else 0.0,
        }

    base = filas["sin compactar"]["tokens"]
    print(f"{'política':<22}{'tokens de entrada':>20}{'ahorro':>10}{'retención evidencia':>22}")
    print("-" * 74)
    for nombre, datos in filas.items():
        ahorro = 1 - datos["tokens"] / base
        print(
            f"{nombre:<22}{datos['tokens']:>20,}{ahorro:>9.1%}{datos['retencion']:>22.3f}"
        )
    print(
        "\nretención = de los documentos que el agente terminó citando, qué fracción\n"
        "seguía siendo visible en el contexto de la última iteración."
    )
    return filas


# --------------------------------------------------------------------------- #
# C. La corrida real con memoria externa.
# --------------------------------------------------------------------------- #
def parte_c(tareas: list[Tarea], allow_api: bool, base_trays):
    seccion("C · La corrida real: compactar y recuperar bajo demanda")
    politica = PoliticaLLM(
        cache_path=CACHE_MEMORIA, allow_api=allow_api, max_api_calls=400
    )
    resultados, trayectorias = [], []
    usos_memoria = 0
    for tarea in tareas:
        memoria = MemoriaExterna()
        registry = construir_herramientas()
        registry.registrar(herramienta_memoria(memoria))
        loop = AgentLoop(
            registry, politica, BASE, VentanaConIndice(memoria, k=VENTANA_K),
            medir_contexto=True,
        )
        tray = loop.correr(tarea.id, tarea.pregunta)
        usos_memoria += sum(1 for p in tray.pasos if p.herramienta == "recuperar_memoria")
        trayectorias.append(tray)
        resultados.append(evaluar_trayectoria(tray, tarea))

    base_res = [evaluar_trayectoria(t, ta) for t, ta in zip(base_trays, tareas)]
    filas = [
        ("acierto exacto", lambda rs: sum(r.acierto_exacto for r in rs) / len(rs), "{:.3f}"),
        ("F1 de docs citados", lambda rs: statistics.mean(r.f1 for r in rs), "{:.3f}"),
        ("pasos promedio", lambda rs: statistics.mean(r.n_pasos for r in rs), "{:.2f}"),
        ("tokens de entrada", lambda rs: sum(r.tokens_in for r in rs), "{:,.0f}"),
        ("costo USD", lambda rs: sum(r.costo_usd for r in rs), "{:.4f}"),
        (
            "tareas sin respuesta",
            lambda rs: sum(r.motivo_corte is not MotivoCorte.RESPONDIO for r in rs),
            "{:.0f}",
        ),
    ]
    print(f"{'métrica':<24}{'sin compactar':>18}{'ventana+índice':>18}{'delta':>14}")
    print("-" * 74)
    for etiqueta, fn, fmt in filas:
        a, b = fn(base_res), fn(resultados)
        print(f"{etiqueta:<24}{fmt.format(a):>18}{fmt.format(b):>18}{fmt.format(b - a):>14}")
    print(f"\nllamadas a 'recuperar_memoria': {usos_memoria}")
    print(f"llamadas de esta corrida       : {politica.api_calls}")
    print(f"aciertos de caché              : {politica.aciertos_cache}")
    print(f"costo histórico del caché      : USD {politica.historical_cost_usd:.4f}")
    return resultados, base_res


# --------------------------------------------------------------------------- #
# D. ¿A partir de qué largo de trayectoria paga compactar?
# --------------------------------------------------------------------------- #
def parte_d(trayectorias, specs, trazas) -> tuple[int, float, float]:
    """Punto de equilibrio, con las constantes medidas en la parte A.

    Sin compactar, el contexto de la iteración i es `P + i·h`: prefijo fijo
    más historia acumulada. El total de una trayectoria de N pasos es
    entonces `N·P + h·N(N-1)/2` — cuadrático en N. Compactando a ventana k,
    el contexto de cada iteración es aproximadamente constante, `P' + k·h`,
    y el total es lineal: `N·(P' + k·h)`.

    Una función cuadrática cruza a una lineal en algún N. Antes de ese punto
    compactar cuesta plata; después la ahorra. El error de esta sección fue
    aplicar la política sin calcular el cruce.
    """
    prefijo = statistics.mean(
        sum(v for k, v in p.contexto.items() if k in
            (Partida.SISTEMA.value, Partida.HERRAMIENTAS.value, Partida.PREGUNTA.value))
        for t in trayectorias for p in t.pasos if p.contexto
    )
    # Tokens que cada paso agrega a la historia.
    incrementos = []
    for traza in trazas:
        for antes, despues in zip(traza, traza[1:]):
            incrementos.append(
                sum(presupuesto_contexto(despues, specs).values())
                - sum(presupuesto_contexto(antes, specs).values())
            )
    h = statistics.mean(incrementos)

    # Sobrecosto de compactar, medido y no supuesto: el esquema de
    # 'recuperar_memoria' viaja en cada iteración y el índice ocupa lugar en
    # el contexto. Los dos se pagan siempre; el ahorro solo llega después.
    esquema_memoria = contar_tokens(
        json.dumps(herramienta_memoria(MemoriaExterna()).spec_openai(), ensure_ascii=False)
    )
    indices = []
    for traza in trazas:
        compactador = VentanaConIndice(MemoriaExterna(), k=VENTANA_K)
        for mensajes in traza:
            for m in compactador.compactar(mensajes):
                if (m.get("content") or "").startswith("[contexto compactado]"):
                    indices.append(contar_tokens(m["content"]))
    indice_medio = statistics.mean(indices) if indices else 0.0
    sobrecosto = esquema_memoria + indice_medio

    seccion("D · A partir de qué largo de trayectoria conviene compactar")
    print(f"prefijo fijo por iteración (P) : {prefijo:,.0f} tokens")
    print(f"historia agregada por paso (h) : {h:,.0f} tokens")
    print(
        f"sobrecosto de compactar        : {sobrecosto:,.0f} tokens/iteración "
        f"({esquema_memoria:,.0f} de esquema + {indice_medio:,.0f} de índice)"
    )
    print(f"ventana                        : k = {VENTANA_K}\n")

    print(f"{'N pasos':>9}{'sin compactar':>16}{'ventana+índice':>17}{'ahorro':>10}")
    print("-" * 52)
    cruce = None
    for n in (4, 6, 8, 12, 16, 24, 32, 48):
        sin = n * prefijo + h * n * (n - 1) / 2
        con = n * (prefijo + sobrecosto + VENTANA_K * h)
        if cruce is None and con < sin:
            cruce = n
        print(f"{n:>9}{sin:>16,.0f}{con:>17,.0f}{1 - con / sin:>9.1%}")

    # N exacto donde se cruzan: h·(N-1)/2 = sobrecosto + k·h
    n_estrella = 1 + 2 * (sobrecosto + VENTANA_K * h) / h
    largo_medio = statistics.mean(len(t.pasos) for t in trayectorias)
    print(f"\npunto de equilibrio analítico: N* ≈ {n_estrella:.1f} pasos")
    print(f"las trayectorias de este módulo promedian {largo_medio:.1f} pasos.")
    del cruce
    return prefijo, h, sobrecosto, n_estrella, largo_medio


def diagrama(trayectorias, filas_b, equilibrio) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(16.5, 4.8))

    # Panel 1: cómo se mueve el reparto a lo largo del bucle.
    max_pasos = max(len([p for p in t.pasos if p.contexto]) for t in trayectorias)
    series = {p.value: [] for p in Partida}
    for i in range(max_pasos):
        for p in Partida:
            valores = [
                t.pasos[i].contexto[p.value]
                for t in trayectorias
                if len(t.pasos) > i and t.pasos[i].contexto
            ]
            series[p.value].append(statistics.mean(valores) if valores else 0)
    x = range(1, max_pasos + 1)
    ax1.stackplot(
        x,
        *[series[p.value] for p in Partida],
        labels=[p.value for p in Partida],
        colors=["#4c72b0", "#dd8452", "#55a868", "#c44e52", "#937860"],
    )
    ax1.set_xlabel("iteración del bucle")
    ax1.set_ylabel("tokens enviados (promedio entre tareas)")
    ax1.set_title("Dos partidas crecen y tres no:\npor eso el contexto es un problema dinámico")
    ax1.legend(fontsize=8, loc="upper left")
    ax1.grid(alpha=0.3)

    # Panel 2: ahorro contra retención.
    nombres = list(filas_b)
    base = filas_b["sin compactar"]["tokens"]
    ahorros = [100 * (1 - filas_b[n]["tokens"] / base) for n in nombres]
    retenciones = [filas_b[n]["retencion"] for n in nombres]
    colores = ["#4c72b0", "#c44e52", "#55a868"]
    ax2.scatter(ahorros, retenciones, s=180, c=colores, zorder=3)
    for nombre, a, r in zip(nombres, ahorros, retenciones):
        ax2.annotate(nombre, (a, r), textcoords="offset points", xytext=(0, 14),
                     ha="center", fontsize=9)
    ax2.set_xlabel("ahorro de tokens de entrada (%)")
    ax2.set_ylabel("retención de la evidencia citada")
    ax2.set_ylim(-0.08, 1.25)
    ax2.set_xlim(-8, max(ahorros) * 1.3 + 5)
    ax2.grid(alpha=0.3)
    ax2.set_title("El índice es lo que hace segura\nla compactación")

    # Panel 3: el punto de equilibrio.
    prefijo, h, sobrecosto, n_estrella, largo_medio = equilibrio
    ns = list(range(2, 41))
    sin = [n * prefijo + h * n * (n - 1) / 2 for n in ns]
    con = [n * (prefijo + sobrecosto + VENTANA_K * h) for n in ns]
    ax3.plot(ns, sin, color="#4c72b0", label="sin compactar (cuadrático)")
    ax3.plot(ns, con, color="#55a868", label="ventana+índice (lineal)")
    ax3.axvline(n_estrella, color="#c44e52", ls="--", lw=1)
    ax3.annotate(
        f"N* ≈ {n_estrella:.1f}", (n_estrella, max(sin) * 0.72),
        textcoords="offset points", xytext=(8, 0), color="#c44e52", fontsize=9,
    )
    ax3.axvline(largo_medio, color="#937860", ls=":", lw=1)
    ax3.annotate(
        f"este módulo\n{largo_medio:.1f} pasos", (largo_medio, max(sin) * 0.30),
        textcoords="offset points", xytext=(8, 0), color="#937860", fontsize=8,
    )
    ax3.set_xlabel("pasos de la trayectoria (N)")
    ax3.set_ylabel("tokens de entrada por tarea")
    ax3.legend(fontsize=8)
    ax3.grid(alpha=0.3)
    ax3.set_title("Compactar paga recién a partir de N*\n(y estas trayectorias no llegan)")

    fig.tight_layout()
    DIAGRAMA.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(DIAGRAMA, dpi=140, bbox_inches="tight")
    print(f"\nDiagrama: {DIAGRAMA.relative_to(AQUI.parent)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-api", action="store_true")
    args = parser.parse_args()

    tareas = cargar_tareas()
    politica_base = PoliticaLLM(cache_path=CACHE, allow_api=False)

    trayectorias, trazas, specs = parte_a(politica_base, tareas)
    filas_b = parte_b(trayectorias, trazas, specs)
    parte_c(tareas, args.allow_api, trayectorias)
    equilibrio = parte_d(trayectorias, specs, trazas)
    diagrama(trayectorias, filas_b, equilibrio)


if __name__ == "__main__":
    main()
