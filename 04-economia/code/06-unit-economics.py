"""§6 — Unit economics de un SaaS regulatorio.

Produce los números de `theory/06-unit-economics.md`. Cierra el módulo: de
costo por query a margen por cliente. Offline y determinista.

    uv run python 04-economia/code/06-unit-economics.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "03-produccion" / "code"))

from econ_lib import Plan, breakeven_queries, client_unit_economics  # noqa: E402
from prod_lib import PRICING_USD_PER_M_TOKENS as PRICING  # noqa: E402

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)

MODEL = "gpt-4o-mini"
P_IN = PRICING[MODEL]["in"]
P_OUT = PRICING[MODEL]["out"]
CACHE_HIT = 0.60  # hit rate del caché de 03 §4 [supuesto conservador]

# Planes de un SaaS sobre normativa chilena. Precios de referencia para
# instituciones públicas y estudios jurídicos medianos. [supuesto ilustrativo]
PLANES = [
    Plan("Básico", 49.0, queries_mes_media=200),
    Plan("Profesional", 199.0, queries_mes_media=1_500),
    Plan("Institucional", 799.0, queries_mes_media=8_000),
]


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def demo_margen_por_plan() -> None:
    """El margen bruto de LLM por plan, en el uso medio."""
    seccion("1. Margen por plan en el uso MEDIO")

    print(f"Modelo {MODEL} · caché al {CACHE_HIT:.0%} (03 §4) · 272 in / 60 out por query\n")
    print(f"{'plan':>15} | {'precio':>8} | {'q/mes':>7} | {'costo LLM':>10} | "
          f"{'margen':>9} | {'margen %':>9}")
    print("-" * 72)
    for p in PLANES:
        u = client_unit_economics(p, p.queries_mes_media, P_IN, P_OUT, cache_hit_rate=CACHE_HIT)
        print(
            f"{u.plan:>15} | ${u.ingreso_usd:>7,.0f} | {u.queries:>7,.0f} | "
            f"${u.costo_efectivo_usd:>9.3f} | ${u.margen_usd:>8,.2f} | {u.margen_pct:>8.2%}"
        )

    print(
        "\nEn el uso medio, el costo del LLM es ruido: márgenes brutos sobre 99% en\n"
        "los tres planes. Si el análisis terminara acá, la conclusión sería que el\n"
        "costo de inferencia no importa. Es la conclusión que la media produce, y\n"
        "es la equivocada."
    )


def demo_cliente_marginal() -> None:
    """Dónde deja de dar margen cada plan: el número que importa."""
    seccion("2. ¿Cuántas queries hacen falta para destruir el margen?")

    print(f"{'plan':>15} | {'precio':>8} | {'q/mes media':>12} | "
          f"{'break-even q/mes':>17} | {'múltiplo':>9}")
    print("-" * 74)
    for p in PLANES:
        be = breakeven_queries(p, P_IN, P_OUT, cache_hit_rate=CACHE_HIT)
        print(
            f"{p.nombre:>15} | ${p.precio_mes_usd:>7,.0f} | {p.queries_mes_media:>12,.0f} | "
            f"{be:>17,.0f} | {be / p.queries_mes_media:>8,.0f}×"
        )

    p0 = PLANES[0]
    mult = breakeven_queries(p0, P_IN, P_OUT, cache_hit_rate=CACHE_HIT) / p0.queries_mes_media
    print(
        f"\nUn cliente del plan {p0.nombre} tendría que hacer ~{mult:,.0f}× su uso medio\n"
        "para que el plan pierda plata. Con este modelo y esta carga, la tarifa\n"
        "plana es SEGURA: el margen de maniobra es de tres a cuatro órdenes de\n"
        "magnitud."
    )


def demo_cuando_deja_de_ser_seguro() -> None:
    """El mismo análisis con las cargas que §5 anticipó."""
    seccion("3. Cuándo la tarifa plana deja de ser segura")

    p = PLANES[0]
    escenarios = [
        ("hoy: RAG simple", 272, 60, CACHE_HIT, MODEL),
        ("contexto largo (50 chunks)", 2_720, 60, CACHE_HIT, MODEL),
        ("respuestas largas", 272, 600, CACHE_HIT, MODEL),
        ("modelo premium", 272, 60, CACHE_HIT, "claude-sonnet-4-6"),
        ("agéntico: 15 pasos por query", 4_080, 900, 0.20, MODEL),
        ("agéntico + premium", 4_080, 900, 0.20, "claude-sonnet-4-6"),
    ]
    print(f"Plan {p.nombre} (${p.precio_mes_usd:,.0f}/mes, uso medio "
          f"{p.queries_mes_media:,} q/mes)\n")
    print(f"{'escenario':>30} | {'$/query':>9} | {'break-even q/mes':>17} | {'holgura':>9}")
    print("-" * 74)
    for nombre, t_in, t_out, cache, modelo in escenarios:
        pin, pout = PRICING[modelo]["in"], PRICING[modelo]["out"]
        be = breakeven_queries(p, pin, pout, t_in, t_out, cache)
        costo_q = p.precio_mes_usd / be if be else float("inf")
        print(
            f"{nombre:>30} | ${costo_q:>8.4f} | {be:>17,.0f} | "
            f"{be / p.queries_mes_media:>8,.0f}×"
        )

    h_hoy = breakeven_queries(p, P_IN, P_OUT, 272, 60, CACHE_HIT) / p.queries_mes_media
    pin_s, pout_s = PRICING["claude-sonnet-4-6"]["in"], PRICING["claude-sonnet-4-6"]["out"]
    h_ag = breakeven_queries(p, pin_s, pout_s, 4_080, 900, 0.20) / p.queries_mes_media
    print(
        f"\nEl escenario agéntico con modelo premium reduce la holgura de {h_hoy:,.0f}× a\n"
        f"{h_ag:,.0f}×: de cuatro órdenes de magnitud a uno. Ahí la tarifa plana deja de\n"
        "ser segura y los límites por plan pasan de ser burocracia a ser el\n"
        "instrumento que sostiene el negocio.\n"
        "\nEs la trayectoria que §5 anticipó: el consumo por query sube más rápido\n"
        "de lo que baja la tarifa."
    )


def demo_distribucion() -> None:
    """La media miente: el 5% intensivo define el margen de la cartera."""
    seccion("4. La media miente (03 §10 llevada al P&L)")

    import numpy as np

    rng = np.random.default_rng(7)
    p = PLANES[1]  # Profesional
    n = 200

    # Uso lognormal: la distribución típica de uso de un SaaS. Muy asimétrica.
    uso = rng.lognormal(mean=np.log(p.queries_mes_media), sigma=1.4, size=n)

    # Escenario agéntico premium, donde el margen sí está en juego.
    pin, pout = PRICING["claude-sonnet-4-6"]["in"], PRICING["claude-sonnet-4-6"]["out"]
    costo_q = (4_080 / 1e6 * pin + 900 / 1e6 * pout) * (1 - 0.20)
    costos = uso * costo_q
    margenes = p.precio_mes_usd - costos

    print(f"Cartera de {n} clientes del plan {p.nombre} (${p.precio_mes_usd:,.0f}/mes),")
    print("uso lognormal, escenario agéntico + modelo premium.\n")
    print(f"  uso mediano:          {np.median(uso):>10,.0f} queries/mes")
    print(f"  uso medio:            {uso.mean():>10,.0f} queries/mes")
    print(f"  uso p95:              {np.percentile(uso, 95):>10,.0f} queries/mes")
    print(f"  uso máximo:           {uso.max():>10,.0f} queries/mes\n")
    print(f"  ingreso total:        ${n * p.precio_mes_usd:>10,.0f}")
    print(f"  costo total:          ${costos.sum():>10,.0f}")
    print(f"  margen total:         ${margenes.sum():>10,.0f} ({margenes.sum() / (n * p.precio_mes_usd):.1%})")
    print(f"\n  clientes con margen negativo: {(margenes < 0).sum()} de {n} "
          f"({(margenes < 0).mean():.1%})")

    top5 = int(n * 0.05)
    idx = np.argsort(uso)[-top5:]
    print(f"  el top 5% ({top5} clientes) consume {uso[idx].sum() / uso.sum():.0%} del costo total")

    sin_top = margenes[np.argsort(uso)[:-top5]]
    print(
        f"  margen sin ese 5%:    ${sin_top.sum():>10,.0f} "
        f"({sin_top.sum() / ((n - top5) * p.precio_mes_usd):.1%})"
    )
    print(
        "\nLa media de uso no dice nada sobre el riesgo. Un puñado de clientes\n"
        "define el margen de toda la cartera — exactamente la lección de 03 §10\n"
        "(reportá media Y p99), ahora en el P&L en vez de en el dashboard."
    )


def demo_palancas() -> None:
    """Cierre del módulo: qué palanca mueve el margen de verdad."""
    seccion("5. Cierre: las palancas del módulo, ordenadas por retorno")

    p = PLANES[0]
    pin, pout = PRICING["claude-sonnet-4-6"]["in"], PRICING["claude-sonnet-4-6"]["out"]
    base = breakeven_queries(p, pin, pout, 4_080, 900, 0.20)

    palancas = [
        ("(base) agéntico + premium, caché 20%", 4_080, 900, 0.20, "claude-sonnet-4-6"),
        ("Límite por plan", 4_080, 900, 0.20, "claude-sonnet-4-6"),
        ("Caché al 60% (03 §4)", 4_080, 900, 0.60, "claude-sonnet-4-6"),
        ("Rutear lo simple a barato (03 §10)", 4_080, 900, 0.20, MODEL),
        ("Acortar respuestas (§1)", 4_080, 300, 0.20, "claude-sonnet-4-6"),
        ("Menos pasos agénticos", 1_360, 300, 0.20, "claude-sonnet-4-6"),
        ("Todo junto", 1_360, 300, 0.60, MODEL),
    ]
    print(f"Plan {p.nombre}, medido como break-even de queries/mes (más alto = mejor)\n")
    print(f"{'palanca':>38} | {'break-even q/mes':>17} | {'mejora':>8}")
    print("-" * 70)
    for nombre, t_in, t_out, cache, modelo in palancas:
        mpin, mpout = PRICING[modelo]["in"], PRICING[modelo]["out"]
        be = breakeven_queries(p, mpin, mpout, t_in, t_out, cache)
        marca = "—" if nombre.startswith("(base)") else f"{be / base:.1f}×"
        if nombre == "Límite por plan":
            print(f"{nombre:>38} | {'(corta la cola)':>17} | {'∞':>8}")
            continue
        print(f"{nombre:>38} | {be:>17,.0f} | {marca:>8}")

    print(
        "\nOrden de retorno: el límite por plan es cualitativamente distinto —no\n"
        "mejora el margen medio, ELIMINA la cola que lo destruye—. Después vienen\n"
        "el modelo barato y el caché, y recién al final las optimizaciones de\n"
        "prompt. Nada de esto importa hasta que el escenario agéntico llegue."
    )


if __name__ == "__main__":
    log.info("Unit economics — supuestos comerciales ilustrativos, fechados 2026-08.")
    demo_margen_por_plan()
    demo_cliente_marginal()
    demo_cuando_deja_de_ser_seguro()
    demo_distribucion()
    demo_palancas()
    print()
