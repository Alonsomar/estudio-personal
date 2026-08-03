"""§4 — Self-hosting vs. API: la escala mínima eficiente.

Produce los números de `theory/04-self-hosting.md`. Modelo analítico offline.

    uv run python 04-economia/code/04-self-hosting.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "03-produccion" / "code"))

from econ_lib import (  # noqa: E402
    GPUS,
    HOURS_PER_MONTH,
    MODELS,
    breakeven_tokens,
    hosting_cost,
    quant_profile,
)
from prod_lib import PRICING_USD_PER_M_TOKENS as PRICING  # noqa: E402

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)

# Carga del RAG chileno (§1) y tarifa de referencia.
RAG_OUT = 60
API_MODEL = "gpt-4o-mini"
API_USD_M_OUT = PRICING[API_MODEL]["out"]

# Costo de operación mensual del self-hosting. NO es cero: guardia,
# actualizaciones de modelo, debugging del servidor de inferencia, y sobre todo
# el costo de oportunidad del tiempo propio. [dato estimado, deliberadamente
# conservador: media jornada semanal de un perfil senior]
OPS_USD_MONTH = 1_200.0


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def _throughput() -> tuple[float, str]:
    """Throughput sostenido por GPU con el mejor perfil de §3."""
    gpu, m = GPUS["H100-80"], MODELS["8B"]
    p = quant_profile(m, gpu, "int4", batch_objetivo=64)
    return p.tokens_per_s * p.batch_efectivo, f"{m.name} int4, batch {p.batch_efectivo}"


def demo_estructura_de_costos() -> None:
    """Costo fijo vs costo variable: dos estructuras distintas."""
    seccion("1. Dos estructuras de costos distintas")

    gpu = GPUS["H100-80"]
    tps, desc = _throughput()
    print(f"Configuración self-host: {desc} en {gpu.name} → {tps:,.0f} tok/s sostenidos")
    print(f"Costo fijo: ${gpu.usd_per_hour}/h × {HOURS_PER_MONTH:.0f} h = "
          f"${gpu.usd_per_hour * HOURS_PER_MONTH:,.0f}/mes por GPU")
    print(f"           + ${OPS_USD_MONTH:,.0f}/mes de operación [estimado]")
    print(f"\nAPI de referencia: {API_MODEL} a ${API_USD_M_OUT:.3f}/M tokens de salida")
    print("Costo fijo: $0. Costo variable: proporcional al uso.\n")

    print(f"{'queries/mes':>14} | {'tokens out':>12} | {'API $':>10} | "
          f"{'self-host $':>12} | {'gana':>10}")
    print("-" * 70)
    for q in (10_000, 100_000, 1_000_000, 10_000_000, 100_000_000):
        toks = q * RAG_OUT
        s = hosting_cost(toks, API_USD_M_OUT, gpu, tps, OPS_USD_MONTH)
        gana = "API" if s.api_usd < s.selfhost_total_usd else "self-host"
        print(
            f"{q:>14,} | {toks:>12,} | {s.api_usd:>10,.0f} | "
            f"{s.selfhost_total_usd:>12,.0f} | {gana:>10}"
        )


def demo_breakeven() -> None:
    """El punto de equilibrio y su lectura honesta."""
    seccion("2. El punto de equilibrio")

    gpu = GPUS["H100-80"]
    tps, _ = _throughput()

    for ops, etiqueta in ((0.0, "SIN contar operación (el cálculo ingenuo)"),
                          (OPS_USD_MONTH, "contando operación [estimado]")):
        be = breakeven_tokens(API_USD_M_OUT, gpu, tps, ops)
        if be == float("inf"):
            print(f"{etiqueta:>42}: nunca (excede la capacidad física)")
            continue
        queries = be / RAG_OUT
        print(
            f"{etiqueta:>42}:\n"
            f"{'':>44}{be / 1e6:,.0f} M tokens de salida/mes\n"
            f"{'':>44}= {queries / 1e6:,.1f} M queries/mes\n"
            f"{'':>44}= {queries / 30 / 86400:,.1f} queries/segundo sostenidas\n"
        )

    print(
        "Para calibrar esa magnitud: sostener esas queries por segundo, todo el mes,\n"
        "sin pausa nocturna ni fines de semana. Un producto B2B chileno con 50\n"
        "instituciones haciendo 200 consultas diarias cada una llega a ~300 mil\n"
        "queries al mes — tres órdenes de magnitud por debajo."
    )


def demo_utilizacion() -> None:
    """La variable que el cálculo ingenuo omite."""
    seccion("3. La utilización es la variable que decide")

    gpu = GPUS["H100-80"]
    tps, _ = _throughput()
    capacidad_mes = tps * 3600 * HOURS_PER_MONTH
    fijo = gpu.usd_per_hour * HOURS_PER_MONTH + OPS_USD_MONTH

    print(f"Capacidad de una GPU: {capacidad_mes / 1e9:,.1f} G tokens/mes")
    print(f"Costo fijo mensual:   ${fijo:,.0f}\n")
    print(f"{'utilización':>12} | {'tokens usados':>15} | {'$/M tokens':>12} | {'vs API':>10}")
    print("-" * 58)
    for u in (0.01, 0.05, 0.15, 0.30, 0.50, 0.70, 0.90):
        usados = capacidad_mes * u
        costo_m = fijo / (usados / 1e6)
        print(
            f"{u:>11.0%} | {usados / 1e6:>13,.0f} M | ${costo_m:>10.3f} | "
            f"{costo_m / API_USD_M_OUT:>9.1f}×"
        )

    print(
        "\nUna GPU al 1% de utilización cuesta ~100× lo que cuesta al 100%: el costo\n"
        "fijo se reparte entre muy pocos tokens. Y por §2, operar por encima del 80%\n"
        "hace explotar la latencia — así que la utilización REALISTA tiene techo.\n"
        "\nEse techo es el punto clave: no podés compensar un volumen bajo\n"
        "'exprimiendo' la GPU, porque exprimirla arruina el producto."
    )


def demo_costos_ocultos() -> None:
    """Lo que la comparación ingenua deja fuera."""
    seccion("4. Lo que la comparación ingenua omite")

    filas = [
        ("Horas de GPU ociosa (holgura obligatoria por §2)", "en el modelo", "20-30% de la capacidad"),
        ("Operación y guardia", "estimado", f"${OPS_USD_MONTH:,.0f}/mes"),
        ("Actualización a modelos nuevos", "NO modelado", "recurrente, cada pocos meses"),
        ("Redundancia (una GPU = un punto de falla)", "NO modelado", "×2 el costo fijo"),
        ("Cold start al escalar", "NO modelado", "minutos de carga de pesos"),
        ("Costo de oportunidad del tiempo propio", "NO modelado", "el más grande de todos"),
        ("Riesgo de quedarse atrás del estado del arte", "NO modelado", "difícil de valorar"),
    ]
    print(f"{'costo':>50} | {'tratamiento':>13} | {'magnitud':>26}")
    print("-" * 95)
    for c, t, m in filas:
        print(f"{c:>50} | {t:>13} | {m:>26}")

    print(
        "\nTodos los NO modelados empujan en la MISMA dirección: encarecen el\n"
        "self-hosting. El modelo de arriba ya es optimista con la opción propia,\n"
        "y aun así pierde por varios órdenes de magnitud en el escenario real."
    )


def grafico_breakeven() -> None:
    """Costo mensual de ambas opciones en función del volumen."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    gpu = GPUS["H100-80"]
    tps, _ = _throughput()
    fijo = gpu.usd_per_hour * HOURS_PER_MONTH + OPS_USD_MONTH

    fig, ax = plt.subplots(figsize=(9, 5))
    toks = np.logspace(6, 12, 300)  # de 1M a 1T tokens/mes
    ax.plot(toks, toks / 1e6 * API_USD_M_OUT, lw=2, color="#3498db",
            label=f"API ({API_MODEL}, ${API_USD_M_OUT:.2f}/M)")
    ax.axhline(fijo, lw=2, color="#e67e22",
               label=f"self-host 1 GPU (${fijo:,.0f}/mes fijo)")

    be = breakeven_tokens(API_USD_M_OUT, gpu, tps, OPS_USD_MONTH)
    if be != float("inf"):
        ax.axvline(be, ls="--", color="#c0392b", lw=1.5)
        ax.annotate(f"break-even\n{be / 1e9:.0f} G tokens/mes",
                    xy=(be, fijo * 3), fontsize=9, color="#c0392b")

    escenario = 300_000 * RAG_OUT  # producto B2B chileno realista
    ax.axvline(escenario, ls=":", color="#27ae60", lw=2)
    ax.annotate("escenario real\n(300k queries/mes)", xy=(escenario * 1.15, fijo * 0.15),
                fontsize=9, color="#27ae60")

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("tokens de salida por mes")
    ax.set_ylabel("costo mensual (USD)")
    ax.set_title("Self-hosting es costo fijo; la API es costo variable", fontsize=12)
    ax.legend(fontsize=9, loc="upper left")
    ax.grid(alpha=0.3, which="both")

    fig.tight_layout()
    out = Path(__file__).resolve().parent.parent / "diagrams" / "breakeven-selfhost.png"
    fig.savefig(out, dpi=130)
    print(f"\n[diagrama] {out.relative_to(ROOT)}")


def demo_veredicto() -> None:
    """El veredicto para el escenario concreto del proyecto."""
    seccion("5. Veredicto para el escenario del proyecto")

    gpu = GPUS["H100-80"]
    tps, _ = _throughput()
    queries_mes = 300_000
    toks = queries_mes * RAG_OUT
    s = hosting_cost(toks, API_USD_M_OUT, gpu, tps, OPS_USD_MONTH)

    print(f"Escenario: producto B2B chileno, {queries_mes:,} queries/mes")
    print(f"  tokens de salida:      {toks:,}")
    print(f"  costo API:             ${s.api_usd:>10,.2f}/mes")
    print(f"  costo self-host:       ${s.selfhost_total_usd:>10,.2f}/mes")
    print(f"  ratio:                 {s.selfhost_total_usd / s.api_usd:>10,.0f}× más caro")
    print(f"  utilización de la GPU: {s.utilization:>10.3%}")
    print(
        f"\nLa API cuesta ${s.api_usd:.2f} al mes. Es menos que el almuerzo de un día.\n"
        "El self-hosting no es una decisión difícil en este escenario: es un error\n"
        "de tres órdenes de magnitud, disfrazado de soberanía tecnológica."
    )


if __name__ == "__main__":
    log.info("Modelo analítico — sin GPU, sin red.")
    demo_estructura_de_costos()
    demo_breakeven()
    demo_utilizacion()
    demo_costos_ocultos()
    demo_veredicto()
    grafico_breakeven()
    print()
