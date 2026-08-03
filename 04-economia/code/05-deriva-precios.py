"""§5 — Deriva de precios: escribir un modelo de costos que no caduque.

Produce los números de `theory/05-deriva-precios.md`. Análisis de escenarios
y de sensibilidad, offline y determinista.

    uv run python 04-economia/code/05-deriva-precios.py
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
    MODELS,
    breakeven_tokens,
    price_after,
    quant_profile,
    spend_trajectory,
)
from prod_lib import PRICING_USD_PER_M_TOKENS as PRICING  # noqa: E402

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)

RAG_OUT = 60
QUERIES_MES = 300_000
API_MODEL = "gpt-4o-mini"

# Escenarios de caída anual de tarifa. NO son predicciones: son escenarios
# parametrizados para hacer análisis de sensibilidad. [supuesto, fechado 2026-08]
ESCENARIOS = {
    "conservador": 0.30,
    "central": 0.60,
    "agresivo": 0.80,
}


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def demo_caducidad() -> None:
    """Qué tan rápido caduca un número escrito hoy."""
    seccion("1. Un modelo de costos escrito hoy, leído en 3 años")

    p0 = PRICING[API_MODEL]["out"]
    print(f"Tarifa de {API_MODEL} hoy: ${p0:.3f}/M tokens de salida\n")
    print(f"{'escenario':>14} | {'caída/año':>10} | " + " | ".join(f"{f'año {t}':>8}" for t in range(4)))
    print("-" * 66)
    for nombre, caida in ESCENARIOS.items():
        precios = [price_after(p0, caida, t) for t in range(4)]
        fila = " | ".join(f"${p:>7.4f}" for p in precios)
        print(f"{nombre:>14} | {caida:>9.0%} | {fila}")

    p3 = price_after(p0, ESCENARIOS["central"], 3)
    print(
        f"\nEn el escenario central, la tarifa de hoy es {p0 / p3:.0f}× la de dentro de "
        "3 años.\nCualquier documento que cite un costo absoluto sin fecharlo estará "
        "equivocado\npor un factor grande, y peor: parecerá correcto."
    )


def demo_jevons() -> None:
    """El precio cae, el consumo sube, ¿y el gasto?"""
    seccion("2. La paradoja de Jevons: el precio cae y el gasto sube")

    p0 = PRICING[API_MODEL]["out"]
    tokens_hoy = QUERIES_MES * RAG_OUT

    casos = [
        ("consumo constante", 0.0),
        ("consumo +50%/año", 0.5),
        ("consumo ×2/año (agéntico)", 1.0),
        ("consumo ×3/año", 2.0),
    ]
    print(f"Tarifa cayendo {ESCENARIOS['central']:.0%}/año en los cuatro casos.\n")
    print(f"{'crecimiento del consumo':>28} | " + " | ".join(f"{f'año {t}':>9}" for t in range(5)))
    print("-" * 82)
    for nombre, crec in casos:
        filas = spend_trajectory(tokens_hoy, p0, ESCENARIOS["central"], crec, anios=4)
        gastos = " | ".join(f"${g:>8,.2f}" for _, _, _, g in filas)
        print(f"{nombre:>28} | {gastos}")

    print(
        "\nCon la tarifa cayendo 60% anual, el gasto solo baja si el consumo crece\n"
        "menos que eso. Duplicar el consumo por año (el escenario agéntico: más\n"
        "contexto, más pasos por query, más reintentos) casi compensa la caída.\n"
        "\n'Va a bajar de precio' no es un plan de costos."
    )


def demo_ratios_vs_absolutos() -> None:
    """Qué conclusiones sobreviven a la deriva y cuáles no."""
    seccion("3. Qué conclusiones sobreviven (análisis de sensibilidad)")

    gpu, m = GPUS["H100-80"], MODELS["8B"]
    p = quant_profile(m, gpu, "int4", batch_objetivo=64)
    tps = p.tokens_per_s * p.batch_efectivo
    p0 = PRICING[API_MODEL]["out"]
    ops = 1_200.0

    print("Break-even de self-hosting (§4) bajo distintos escenarios de precio.")
    print("Supuesto adicional: la GPU/hora cae la MITAD de rápido que la tarifa,")
    print("porque el hardware es un bien físico y la tarifa incorpora software.\n")
    print(f"{'año':>5} | {'tarifa API':>11} | {'GPU $/h':>9} | {'break-even queries/mes':>23}")
    print("-" * 56)
    for t in range(4):
        tarifa = price_after(p0, ESCENARIOS["central"], t)
        gpu_t = GPUS["H100-80"].__class__(
            gpu.name, gpu.memory_gb, gpu.bandwidth_tb_s, gpu.bf16_tflops,
            usd_per_hour=price_after(gpu.usd_per_hour, ESCENARIOS["central"] / 2, t),
        )
        be = breakeven_tokens(tarifa, gpu_t, tps, ops)
        q = be / RAG_OUT if be != float("inf") else float("inf")
        print(f"{t:>5} | ${tarifa:>10.4f} | ${gpu_t.usd_per_hour:>8.2f} | {q / 1e6:>21,.0f} M")

    print(
        "\nEl break-even se ALEJA con el tiempo: si la tarifa cae más rápido que el\n"
        "hardware, self-hostear es cada vez peor negocio. La conclusión de §4 no\n"
        "solo sobrevive a la deriva — se refuerza."
    )


def demo_que_sobrevive() -> None:
    """Clasificación explícita de las conclusiones del módulo."""
    seccion("4. Inventario de conclusiones: robustas vs. perecederas")

    filas = [
        ("El output cuesta más que el input (§1)", "ROBUSTA", "es física, no precio"),
        ("Recuperar de más es barato, responder largo caro (§1)", "ROBUSTA", "ratio, no absoluto"),
        ("El batching da costo medio decreciente (§2)", "ROBUSTA", "estructura de costos"),
        ("La latencia explota cerca de la saturación (§2)", "ROBUSTA", "teoría de colas"),
        ("Cuantizar exige medir en tu golden (§3)", "ROBUSTA", "método, no número"),
        ("La API gana al self-hosting en este escenario (§4)", "ROBUSTA+", "se refuerza con el tiempo"),
        ("El break-even está en ~92 M queries/mes (§4)", "PERECEDERA", "se mueve con ambos precios"),
        ("La API cuesta $10.80/mes (§4)", "PERECEDERA", "caduca en meses"),
        ("$/M tokens de cada modelo (todo el repo)", "PERECEDERA", "centralizada en prod_lib"),
        ("Un 70B bf16 no cabe en 80 GB (§1, §3)", "SEMI", "cambia si cambia el hardware"),
    ]
    print(f"{'conclusión':>54} | {'clase':>11} | {'por qué':>26}")
    print("-" * 97)
    for c, k, w in filas:
        print(f"{c:>54} | {k:>11} | {w:>26}")

    robustas = sum(1 for _, k, _ in filas if k.startswith("ROBUSTA"))
    print(
        f"\n{robustas} de {len(filas)} conclusiones son robustas a la deriva. Todas ellas\n"
        "son ratios, estructuras o métodos. Las perecederas son, sin excepción,\n"
        "NIVELES ABSOLUTOS en dólares.\n"
        "\nRegla para escribir: razoná en ratios, guardá los absolutos en un solo\n"
        "lugar parametrizado, y fechá todo supuesto."
    )


def grafico_deriva() -> None:
    """Tarifa vs gasto total bajo distintos crecimientos de consumo."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    p0 = PRICING[API_MODEL]["out"]
    tokens_hoy = QUERIES_MES * RAG_OUT

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))

    anios = list(range(6))
    for nombre, caida in ESCENARIOS.items():
        ax1.plot(anios, [price_after(p0, caida, t) for t in anios], "o-", lw=2,
                 label=f"{nombre} (−{caida:.0%}/año)")
    ax1.set_yscale("log")
    ax1.set_xlabel("años desde 2026")
    ax1.set_ylabel("USD por millón de tokens de salida")
    ax1.set_title("La tarifa cae rápido (escenarios, no predicciones)", fontsize=11)
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3, which="both")

    for nombre, crec, color in [
        ("consumo constante", 0.0, "#2ecc71"),
        ("consumo ×2/año", 1.0, "#e67e22"),
        ("consumo ×3/año", 2.0, "#e74c3c"),
    ]:
        filas = spend_trajectory(tokens_hoy, p0, ESCENARIOS["central"], crec, anios=5)
        ax2.plot([t for t, _, _, _ in filas], [g for _, _, _, g in filas], "o-", lw=2,
                 color=color, label=nombre)
    ax2.set_xlabel("años desde 2026")
    ax2.set_ylabel("gasto mensual (USD)")
    ax2.set_title("Tarifa −60%/año: el gasto depende del consumo", fontsize=11)
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)

    fig.tight_layout()
    out = Path(__file__).resolve().parent.parent / "diagrams" / "deriva-precios.png"
    fig.savefig(out, dpi=130)
    print(f"\n[diagrama] {out.relative_to(ROOT)}")


if __name__ == "__main__":
    log.info("Escenarios parametrizados — no son predicciones.")
    demo_caducidad()
    demo_jevons()
    demo_ratios_vs_absolutos()
    demo_que_sobrevive()
    grafico_deriva()
    print()
