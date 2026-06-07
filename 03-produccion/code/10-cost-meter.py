"""Sección 10 — Costo en producción (simulado desde tarifas públicas).

Mide y presupuesta el costo del LLM —la línea más volátil del P&L—:

  1. Costo por feature (CostMeter): cuánto cuesta una conversación promedio y P99.
  2. Cost-aware routing: enrutar cada query al modelo más barato que la resuelve.
  3. Tabla $/1000 queries sobre el corpus chileno, por arquitectura.
  4. BudgetGuard: proyección de quema y alerta por hora.

Y genera diagrams/pareto-costo-latencia.png. Todo offline, desde PRICING (tarifas
públicas 2026-Q2 en prod_lib); ninguna llamada real.

Ejecutar:

    uv run python 03-produccion/code/10-cost-meter.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from prod_lib import (  # noqa: E402
    BudgetGuard,
    CostMeter,
    estimate_cost_usd,
)
from shared.utils import get_project_root  # noqa: E402

ROOT = get_project_root()
DIAGRAMS = ROOT / "03-produccion" / "diagrams"
SEP = "=" * 72

# Tokens representativos (medidos en §2: ~272 in / 21 out; usamos 60 out para
# una respuesta algo más larga). Latencia p95 estimada por modelo (ms).
QUERY_IN, QUERY_OUT = 272, 60
LAT_P95 = {"bm25": 5, "gpt-4o-mini": 700, "claude-haiku-4-5": 650, "claude-sonnet-4-6": 1200}


# --------------------------------------------------------------------------- #
# 1. Costo por feature: conversación promedio vs P99.
# --------------------------------------------------------------------------- #
def demo_per_feature() -> None:
    print(SEP)
    print("1. COSTO POR FEATURE — promedio y P99 (modelo: claude-sonnet-4-6)")

    rng = np.random.default_rng(0)
    meter = CostMeter()
    # Cada feature tiene su perfil de tokens (in, out) con variabilidad.
    profiles = {
        "busqueda":  (250, 30),
        "chat":      (320, 130),
        "resumen":   (1500, 220),
    }
    for _ in range(2000):
        for feat, (mi, mo) in profiles.items():
            tin = max(1, int(rng.normal(mi, mi * 0.25)))
            tout = max(1, int(rng.normal(mo, mo * 0.4)))
            meter.record(estimate_cost_usd("claude-sonnet-4-6", tin, tout), label=feat)

    print(f"\n  {'feature':>10} | {'media $/req':>12} | {'p99 $/req':>11} | {'total $':>9}")
    print(f"  {'-'*10}-+-{'-'*12}-+-{'-'*11}-+-{'-'*9}")
    for feat in profiles:
        f = meter.feature(feat)
        print(f"  {feat:>10} | {f['mean']:>12.6f} | {f['p99']:>11.6f} | {f['total']:>9.4f}")
    print("\n  La P99 de 'resumen' es varias veces su media: presupuestar con la")
    print("  media subestima. El usuario que manda el documento largo cuesta como")
    print("  diez usuarios de búsqueda — eso hay que verlo por feature, no en bruto.")


# --------------------------------------------------------------------------- #
# 2. Cost-aware routing.
# --------------------------------------------------------------------------- #
def demo_routing() -> None:
    print("\n" + SEP)
    print("2. COST-AWARE ROUTING — el modelo más barato que ALCANZA")

    rng = np.random.default_rng(1)
    n = 10_000
    # 75% de las queries son simples (las resuelve haiku); 25% complejas (sonnet).
    is_complex = rng.random(n) < 0.25

    cost_router = 0.0
    cost_all_sonnet = 0.0
    routed = {"haiku": 0, "sonnet": 0}
    for c in is_complex:
        model = "claude-sonnet-4-6" if c else "claude-haiku-4-5"
        routed["sonnet" if c else "haiku"] += 1
        cost_router += estimate_cost_usd(model, QUERY_IN, QUERY_OUT)
        cost_all_sonnet += estimate_cost_usd("claude-sonnet-4-6", QUERY_IN, QUERY_OUT)

    print(f"\n  {n:,} queries: {routed['haiku']:,} simples→haiku, "
          f"{routed['sonnet']:,} complejas→sonnet")
    print(f"  costo router        : ${cost_router:.4f}")
    print(f"  costo todo-sonnet   : ${cost_all_sonnet:.4f}")
    print(f"  ahorro              : {100*(1-cost_router/cost_all_sonnet):.0f}%  "
          "(sin tocar la calidad de las complejas)")
    print("\n  La clave es el clasificador: mandar una query difícil a haiku ahorra")
    print("  centavos y arruina la respuesta. El patrón seguro es ESCALAR: probar")
    print("  el barato y, si la confianza es baja, reintentar con el caro.")


# --------------------------------------------------------------------------- #
# 3. Tabla $/1000 queries por arquitectura + Pareto.
# --------------------------------------------------------------------------- #
def architectures() -> list[dict]:
    def per_1k(model: str, hit_rate: float = 0.0) -> float:
        c = estimate_cost_usd(model, QUERY_IN, QUERY_OUT) * 1000
        return c * (1 - hit_rate)

    return [
        # BM25 solo es REFERENCIA (no genera respuesta): un piso, no un sustituto.
        # Se excluye de la frontera porque "dominaría" trivialmente sin responder.
        {"name": "BM25 solo (sin LLM)",   "model": "bm25", "ref": True,
         "cost_1k": 0.0,                          "lat": LAT_P95["bm25"]},
        {"name": "Hybrid + GPT-4o-mini",  "model": "gpt-4o-mini", "ref": False,
         "cost_1k": per_1k("gpt-4o-mini"),        "lat": LAT_P95["gpt-4o-mini"]},
        {"name": "Hybrid + mini + cache", "model": "gpt-4o-mini", "ref": False,
         "cost_1k": per_1k("gpt-4o-mini", 0.8),   "lat": LAT_P95["gpt-4o-mini"]},
        {"name": "Hybrid + Haiku 4.5",    "model": "claude-haiku-4-5", "ref": False,
         "cost_1k": per_1k("claude-haiku-4-5"),   "lat": LAT_P95["claude-haiku-4-5"]},
        {"name": "Hybrid + Haiku + cache","model": "claude-haiku-4-5", "ref": False,
         "cost_1k": per_1k("claude-haiku-4-5", 0.8), "lat": LAT_P95["claude-haiku-4-5"]},
        {"name": "Hybrid + Sonnet 4.6",   "model": "claude-sonnet-4-6", "ref": False,
         "cost_1k": per_1k("claude-sonnet-4-6"),  "lat": LAT_P95["claude-sonnet-4-6"]},
        {"name": "Hybrid + Sonnet + cache","model": "claude-sonnet-4-6", "ref": False,
         "cost_1k": per_1k("claude-sonnet-4-6", 0.8), "lat": LAT_P95["claude-sonnet-4-6"]},
    ]


def _pareto_frontier(archs: list[dict]) -> set[str]:
    """No-dominados en (costo, latencia) entre los que GENERAN respuesta: nadie es
    más barato Y más rápido. Las referencias (BM25) quedan fuera."""
    cands = [a for a in archs if not a.get("ref")]
    front = set()
    for a in cands:
        dominated = any(
            b is not a and b["cost_1k"] <= a["cost_1k"] and b["lat"] <= a["lat"]
            and (b["cost_1k"] < a["cost_1k"] or b["lat"] < a["lat"])
            for b in cands
        )
        if not dominated:
            front.add(a["name"])
    return front


def demo_table_and_pareto() -> None:
    print("\n" + SEP)
    print("3. $/1000 QUERIES POR ARQUITECTURA (corpus chileno, ~272 in / 60 out)")

    archs = architectures()
    front = _pareto_frontier(archs)
    print(f"\n  {'arquitectura':>26} | {'$/1000q':>9} | {'p95 ms':>7} | Pareto")
    print(f"  {'-'*26}-+-{'-'*9}-+-{'-'*7}-+-------")
    for a in archs:
        mark = "★" if a["name"] in front else ""
        print(f"  {a['name']:>26} | {a['cost_1k']:>9.3f} | {a['lat']:>7} | {mark}")
    print("\n  Sonnet cuesta ~3.7× Haiku por la misma query; el cache corta 5× el")
    print("  costo (80% hit). La frontera son mini+cache (lo más barato) y")
    print("  Haiku+cache (lo más rápido); Sonnet queda DOMINADO en costo/latencia.")
    print("  Pero esto IGNORA la calidad: Sonnet se justifica solo si responde")
    print("  mejor las complejas — el trade-off real es a 3 ejes (01-evals §10).")

    # Diagrama Pareto costo/latencia.
    fig, ax = plt.subplots(figsize=(8.5, 5))
    for a in archs:
        if a.get("ref"):
            color = "#95a5a6"   # gris: referencia, no genera
        elif a["name"] in front:
            color = "#2ecc71"   # verde: en la frontera
        else:
            color = "#e74c3c"   # rojo: dominado
        ax.scatter(a["cost_1k"], a["lat"], s=140, color=color,
                   edgecolor="#333", zorder=3, linewidth=0.8)
        ax.annotate(a["name"], (a["cost_1k"], a["lat"]),
                    textcoords="offset points", xytext=(8, 6), fontsize=8)
    # Línea de la frontera (ordenada por costo).
    fp = sorted([a for a in archs if a["name"] in front], key=lambda x: x["cost_1k"])
    ax.plot([a["cost_1k"] for a in fp], [a["lat"] for a in fp],
            "--", color="#2ecc71", alpha=0.7, zorder=2, label="frontera de Pareto")
    ax.set_xlabel("Costo ($/1000 queries)")
    ax.set_ylabel("Latencia p95 (ms)")
    ax.set_title("Pareto costo / latencia — arquitecturas del RAG fiscal\n"
                 "(verde = no dominado; la calidad es un tercer eje no mostrado)",
                 fontsize=11, fontweight="bold")
    ax.grid(alpha=0.3)
    ax.legend(loc="center right")
    ax.margins(0.18)
    fig.tight_layout()
    DIAGRAMS.mkdir(parents=True, exist_ok=True)
    out = DIAGRAMS / "pareto-costo-latencia.png"
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print(f"\n  diagrama guardado en {out.relative_to(ROOT)}")


# --------------------------------------------------------------------------- #
# 4. BudgetGuard: quema y alerta.
# --------------------------------------------------------------------------- #
def demo_budget() -> None:
    print("\n" + SEP)
    print("4. BUDGET GUARD — proyección de quema y alerta por hora")

    guard = BudgetGuard(monthly_budget_usd=300.0, warn_ratio=0.9)
    # El tráfico se duplica el día 10 (un cliente nuevo): la quema se acelera.
    print(f"\n  presupuesto: ${guard.budget:.0f}/mes. Tres momentos:\n")
    print(f"  {'momento':>22} | {'gastado':>8} | {'$/h':>6} | {'proy. mes':>10} | estado")
    print(f"  {'-'*22}-+-{'-'*8}-+-{'-'*6}-+-{'-'*10}-+--------")
    escenarios = [
        ("día 5 (ritmo normal)",   38.0, 120.0),
        ("día 10 (tráfico 2×)",    120.0, 240.0),
        ("día 15 (sin frenar)",    230.0, 360.0),
    ]
    for nombre, spent, hours in escenarios:
        level, p = guard.status(spent, hours)
        flag = {"ok": "ok", "warn": "⚠ warn", "over": "✗ OVER"}[level]
        print(f"  {nombre:>22} | {spent:>7.0f}$ | {p['burn_per_hour']:>5.2f} | "
              f"{p['projected_month']:>9.0f}$ | {flag}")
    print("\n  La alerta salta por la PROYECCIÓN, no por el gasto acumulado: en el")
    print("  día 10 sólo gastaste $120 de $300, pero al ritmo nuevo proyectás")
    print("  ~$365 — la alerta te avisa con 20 días de margen, no con la factura.")


def main() -> None:
    demo_per_feature()
    demo_routing()
    demo_table_and_pareto()
    demo_budget()
    print("\n" + SEP)
    print("El costo del LLM no se controla con buenos deseos: se mide por feature,")
    print("se enruta a lo más barato que alcanza, se cachea, y se alerta por quema.")


if __name__ == "__main__":
    main()
