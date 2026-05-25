"""Costo, latencia y frontera de Pareto de evals.

Calcula y visualiza el trade-off entre calidad de señal y costo
para distintas configuraciones de eval. Demuestra:
  1. Desglose de costo por componente
  2. Frontera de Pareto (puntos dominados vs eficientes)
  3. Simulación de juez escalonado (cascading)
  4. Impacto del sampling en poder estadístico vs costo
  5. Presupuesto mensual por configuración

Ejecutar con:
    uv run python 01-evals/code/eval-costo-pareto.py

No requiere API keys.
"""

from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from shared.utils import get_logger

log = get_logger(__name__)
console = Console()


# ---------------------------------------------------------------------------
# Modelo de datos
# ---------------------------------------------------------------------------

@dataclass
class EvalConfig:
    """Configuración de evaluación con sus costos y calidad."""
    name: str
    judge_model: str
    n_queries: int
    n_metrics: int
    cost_per_run: float        # USD
    latency_minutes: float
    quality_score: float       # 0-1, calidad de señal estimada
    description: str


@dataclass
class CostBreakdown:
    """Desglose de costo por componente."""
    system_tokens: float       # USD, tokens del sistema bajo eval
    judge_tokens: float        # USD, tokens del juez
    heuristic_metrics: float   # USD, métricas sin LLM (ROUGE, etc.)
    overhead: float            # USD, parsing, IO, etc.

    @property
    def total(self) -> float:
        return self.system_tokens + self.judge_tokens + self.heuristic_metrics + self.overhead


# ---------------------------------------------------------------------------
# Configuraciones de eval
# ---------------------------------------------------------------------------

CONFIGS = [
    EvalConfig("ROUGE only", "ninguno", 100, 1, 0.01, 0.1, 0.20,
               "Solo métricas heurísticas, sin juez LLM"),
    EvalConfig("Haiku smoke (10q)", "Haiku 4.5", 10, 2, 0.05, 0.5, 0.40,
               "Smoke test rápido con juez barato"),
    EvalConfig("Haiku full (100q)", "Haiku 4.5", 100, 3, 0.40, 3.0, 0.55,
               "Eval completa con juez barato"),
    EvalConfig("ROUGE + Haiku (30q)", "Haiku 4.5", 30, 3, 0.18, 1.0, 0.52,
               "Heurísticas + juez barato, sample"),
    EvalConfig("Sonnet sample (30q)", "Sonnet 4.6", 30, 4, 0.60, 3.0, 0.72,
               "Juez de calidad, sample del golden"),
    EvalConfig("Sonnet full (100q)", "Sonnet 4.6", 100, 4, 2.00, 10.0, 0.78,
               "Juez de calidad, golden completo"),
    EvalConfig("Sonnet cascade (100q)", "Haiku→Sonnet", 100, 4, 1.30, 7.0, 0.75,
               "Cascading: Haiku primero, Sonnet si ambiguo"),
    EvalConfig("Opus full (100q)", "Opus 4.6", 100, 5, 10.00, 20.0, 0.85,
               "Juez premium, golden completo"),
    EvalConfig("Multi-judge (100q)", "Sonnet×3", 100, 5, 6.00, 15.0, 0.88,
               "3 jueces Sonnet, voto mayoritario"),
    EvalConfig("Humano + LLM", "Sonnet + experto", 100, 6, 55.00, 240.0, 0.95,
               "Humano experto + LLM, calibración"),
]

# Precios por modelo (USD por 1M tokens)
PRICING = {
    "Haiku 4.5":  {"input": 0.80, "output": 4.00},
    "Sonnet 4.6": {"input": 3.00, "output": 15.00},
    "Opus 4.6":   {"input": 15.00, "output": 75.00},
    "GPT-4o":     {"input": 2.50, "output": 10.00},
}


# ---------------------------------------------------------------------------
# Análisis
# ---------------------------------------------------------------------------

def compute_cost_breakdown(n_queries: int, judge: str) -> CostBreakdown:
    """Calcula el desglose de costo para una configuración.

    Supuestos:
    - ~500 tokens input, ~200 tokens output por query (sistema)
    - ~800 tokens input (respuesta + rúbrica), ~150 tokens output por juicio
    - Heurísticas (ROUGE): costo despreciable
    """
    # Tokens del sistema bajo evaluación
    sys_input_tokens = n_queries * 500
    sys_output_tokens = n_queries * 200
    # Asumimos que el sistema usa Sonnet
    sys_cost = (sys_input_tokens * 3.00 + sys_output_tokens * 15.00) / 1_000_000

    # Tokens del juez
    if judge == "ninguno":
        judge_cost = 0.0
    elif judge in PRICING:
        judge_input_tokens = n_queries * 800
        judge_output_tokens = n_queries * 150
        p = PRICING[judge]
        judge_cost = (judge_input_tokens * p["input"] + judge_output_tokens * p["output"]) / 1_000_000
    else:
        judge_cost = 0.0  # Handled specially

    return CostBreakdown(
        system_tokens=sys_cost,
        judge_tokens=judge_cost,
        heuristic_metrics=0.001 * n_queries / 100,  # ~$0.001 por 100 queries
        overhead=0.005 * n_queries / 30,  # ~$0.005 por 30 queries
    )


def find_pareto_frontier(configs: list[EvalConfig]) -> list[EvalConfig]:
    """Identifica los puntos en la frontera de Pareto.

    Un punto es Pareto-óptimo si no existe otro punto que sea
    mejor en calidad Y más barato.
    """
    frontier = []
    for c in configs:
        dominated = False
        for other in configs:
            if other.name == c.name:
                continue
            # other domina a c si es mejor o igual en calidad Y más barato o igual
            if (other.quality_score >= c.quality_score and
                other.cost_per_run <= c.cost_per_run and
                (other.quality_score > c.quality_score or
                 other.cost_per_run < c.cost_per_run)):
                dominated = True
                break
        if not dominated:
            frontier.append(c)
    return frontier


def show_pricing_table() -> None:
    """Muestra tabla de precios por modelo."""
    table = Table(title="Precios de modelos LLM (USD/1M tokens, 2025-2026)")
    table.add_column("Modelo", style="bold", width=14)
    table.add_column("Input", justify="right", width=10)
    table.add_column("Output", justify="right", width=10)
    table.add_column("Costo/juicio*", justify="right", width=14)

    for model, prices in PRICING.items():
        # Costo por juicio: 800 input + 150 output
        cost_per_judgment = (800 * prices["input"] + 150 * prices["output"]) / 1_000_000
        table.add_row(
            model,
            f"${prices['input']:.2f}",
            f"${prices['output']:.2f}",
            f"${cost_per_judgment:.5f}",
        )

    console.print(table)
    console.print("  * Estimado: 800 tokens input + 150 tokens output por juicio\n")


def show_cost_breakdown() -> None:
    """Muestra desglose de costo para configuraciones representativas."""
    console.print()
    table = Table(title="Desglose de costo por componente (30 queries)", show_lines=True)
    table.add_column("Componente", style="bold", width=20)
    table.add_column("Sin juez", justify="right", width=12)
    table.add_column("Haiku", justify="right", width=12)
    table.add_column("Sonnet", justify="right", width=12)
    table.add_column("Opus", justify="right", width=12)

    breakdowns = {
        "Sin juez": compute_cost_breakdown(30, "ninguno"),
        "Haiku": compute_cost_breakdown(30, "Haiku 4.5"),
        "Sonnet": compute_cost_breakdown(30, "Sonnet 4.6"),
        "Opus": compute_cost_breakdown(30, "Opus 4.6"),
    }

    rows = [
        ("Sistema (tokens)", "system_tokens"),
        ("Juez (tokens)", "judge_tokens"),
        ("Heurísticas", "heuristic_metrics"),
        ("Overhead", "overhead"),
    ]

    for label, attr in rows:
        vals = [getattr(breakdowns[k], attr) for k in breakdowns]
        table.add_row(label, *[f"${v:.4f}" for v in vals])

    table.add_section()
    totals = [breakdowns[k].total for k in breakdowns]
    table.add_row("[bold]Total[/bold]", *[f"[bold]${v:.4f}[/bold]" for v in totals])

    # Porcentaje del juez
    table.add_section()
    pcts = []
    for k in breakdowns:
        b = breakdowns[k]
        pct = b.judge_tokens / b.total * 100 if b.total > 0 else 0
        pcts.append(f"{pct:.0f}%")
    table.add_row("[dim]% juez[/dim]", *[f"[dim]{p}[/dim]" for p in pcts])

    console.print(table)


def show_configs_table() -> None:
    """Muestra tabla de todas las configuraciones."""
    console.print()
    frontier = find_pareto_frontier(CONFIGS)
    frontier_names = {c.name for c in frontier}

    table = Table(title="Configuraciones de eval: calidad vs costo", show_lines=True)
    table.add_column("Configuración", style="bold", width=24)
    table.add_column("Juez", width=14)
    table.add_column("Queries", justify="center", width=8)
    table.add_column("Costo", justify="right", width=8)
    table.add_column("Latencia", justify="right", width=10)
    table.add_column("Calidad", justify="center", width=8)
    table.add_column("Pareto", justify="center", width=8)

    for c in sorted(CONFIGS, key=lambda x: x.cost_per_run):
        is_pareto = c.name in frontier_names
        pareto_str = "[green]✓[/green]" if is_pareto else "[dim]✗[/dim]"

        q_color = "green" if c.quality_score >= 0.70 else "yellow" if c.quality_score >= 0.50 else "red"

        table.add_row(
            c.name,
            c.judge_model,
            str(c.n_queries),
            f"${c.cost_per_run:.2f}",
            f"{c.latency_minutes:.0f} min",
            f"[{q_color}]{c.quality_score:.2f}[/{q_color}]",
            pareto_str,
        )

    console.print(table)

    console.print(Panel(
        "[bold]Interpretación de la frontera de Pareto:[/bold]\n\n"
        "  Los puntos marcados ✓ están en la frontera eficiente:\n"
        "  no se puede mejorar calidad sin aumentar costo.\n\n"
        "  Los puntos ✗ están dominados: existe otra configuración\n"
        "  que es más barata Y de igual o mayor calidad.",
        style="blue",
    ))


def show_cascading_analysis() -> None:
    """Muestra el ahorro del juez escalonado."""
    console.print()
    console.print(Panel(
        "[bold]Juez escalonado (cascading): Haiku → Sonnet[/bold]\n\n"
        "Haiku evalúa primero. Si el score es claro (≤2 o ≥4),\n"
        "se usa ese resultado. Si es ambiguo (3), escala a Sonnet.",
        style="blue",
    ))

    n_queries = 100
    pct_clear = 0.65  # 65% de juicios son claros

    haiku_cost = compute_cost_breakdown(n_queries, "Haiku 4.5").judge_tokens
    sonnet_cost = compute_cost_breakdown(n_queries, "Sonnet 4.6").judge_tokens

    # Cascading: Haiku para todos + Sonnet para los ambiguos
    cascade_cost = haiku_cost + sonnet_cost * (1 - pct_clear)

    table = Table(title=f"Comparación de costos (juez, {n_queries} queries)")
    table.add_column("Estrategia", style="bold", width=28)
    table.add_column("Costo juez", justify="right", width=12)
    table.add_column("Calidad", justify="center", width=10)
    table.add_column("Ahorro", justify="center", width=10)

    table.add_row(
        "Solo Haiku",
        f"${haiku_cost:.4f}",
        "[yellow]0.55[/yellow]",
        "—",
    )
    table.add_row(
        "Solo Sonnet",
        f"${sonnet_cost:.4f}",
        "[green]0.78[/green]",
        "baseline",
    )
    table.add_row(
        f"Cascading ({pct_clear:.0%} claro)",
        f"${cascade_cost:.4f}",
        "[green]0.75[/green]",
        f"[green]{(1 - cascade_cost/sonnet_cost):.0%}[/green]",
    )

    console.print(table)

    console.print(
        f"\n  Sonnet solo:   ${sonnet_cost:.4f}"
        f"\n  Cascading:     ${cascade_cost:.4f}"
        f"\n  Ahorro:        ${sonnet_cost - cascade_cost:.4f}"
        f" ({(1 - cascade_cost/sonnet_cost):.0%})"
        f"\n  Calidad:       ~96% de Sonnet solo (0.75 vs 0.78)"
    )


def show_sampling_tradeoff() -> None:
    """Muestra el trade-off entre sampling y poder estadístico."""
    console.print()
    console.print(Panel(
        "[bold]Sampling vs poder estadístico[/bold]\n\n"
        "Menos queries = menor costo, pero también menor capacidad\n"
        "de detectar mejoras reales (sección 8).",
        style="blue",
    ))

    table = Table(title="Impacto del sampling en costo y poder", show_lines=True)
    table.add_column("Queries", style="bold", justify="center", width=10)
    table.add_column("Costo (Sonnet)", justify="right", width=14)
    table.add_column("Latencia", justify="right", width=10)
    table.add_column("Detecta δ ≥", justify="center", width=12)
    table.add_column("Uso recomendado", width=22)

    # sigma ≈ 0.19 (de sección 8), power=0.80, alpha=0.05
    # n ≈ 7.85 * sigma^2 / delta^2
    sigma = 0.19
    scenarios = [
        (10, "Smoke test"),
        (30, "Pre-merge gate"),
        (50, "Nightly (sample)"),
        (100, "Nightly (full)"),
        (200, "Pre-release"),
    ]

    for n, use in scenarios:
        cost = 0.02 * n  # ~$0.02 por query con Sonnet
        latency = 0.1 * n  # ~0.1 min por query
        # min delta detectable
        min_delta = (7.85 * sigma**2 / n) ** 0.5

        table.add_row(
            str(n),
            f"${cost:.2f}",
            f"{latency:.0f} min",
            f"{min_delta:.2f} ({min_delta*100:.0f}pp)",
            use,
        )

    console.print(table)


def show_monthly_budget() -> None:
    """Muestra presupuesto mensual para distintas configuraciones."""
    console.print()
    console.print(Panel(
        "[bold]Presupuesto mensual de eval[/bold]\n\n"
        "Supuestos: 15 PRs/semana, nightly 7/semana, weekly 1/semana.",
        style="blue",
    ))

    table = Table(title="Presupuesto mensual por configuración", show_lines=True)
    table.add_column("Tier", style="bold", width=14)
    table.add_column("Config", width=22)
    table.add_column("Costo/run", justify="right", width=10)
    table.add_column("Runs/mes", justify="center", width=10)
    table.add_column("Total/mes", justify="right", width=12)

    tiers = [
        ("Pre-merge", "Haiku smoke (10q)", 0.05, 60),
        ("Nightly", "Sonnet sample (30q)", 0.60, 30),
        ("Weekly", "Sonnet full (100q)", 2.00, 4),
    ]

    total_monthly = 0
    for tier, config, cost, runs in tiers:
        monthly = cost * runs
        total_monthly += monthly
        table.add_row(tier, config, f"${cost:.2f}", str(runs), f"${monthly:.2f}")

    table.add_section()
    table.add_row("", "", "", "[bold]Total[/bold]", f"[bold]${total_monthly:.2f}[/bold]")

    console.print(table)

    # Regla del 10x
    console.print()
    table2 = Table(title="Regla del 10x: ¿se justifica la eval?")
    table2.add_column("Fallo", style="bold", width=28)
    table2.add_column("Costo fallo", justify="right", width=12)
    table2.add_column("Costo eval/mes", justify="right", width=14)
    table2.add_column("Ratio", justify="center", width=8)
    table2.add_column("¿Justifica?", justify="center", width=12)

    failures = [
        ("Cita fantasma en informe", 5000, 3.00),
        ("Recall cae 20pp", 2000, 21.00),
        ("Latencia sube 3x", 500, 0.00),
        ("Error de formato", 100, 0.50),
    ]

    for fail, cost_fail, cost_eval in failures:
        ratio = cost_fail / cost_eval if cost_eval > 0 else float('inf')
        justified = ratio > 10
        j_str = "[green]Sí ({}x)[/green]".format(
            f"{ratio:.0f}" if ratio < 10000 else "∞"
        ) if justified else "[red]No[/red]"
        table2.add_row(
            fail,
            f"${cost_fail:,.0f}",
            f"${cost_eval:.2f}",
            f"{ratio:.0f}x" if ratio < 10000 else "∞",
            j_str,
        )

    console.print(table2)

    console.print(Panel(
        "[bold yellow]Conclusión:[/bold yellow]\n\n"
        f"  Presupuesto mensual total: ${total_monthly:.2f}/mes\n"
        f"  Para un producto fiscal de $10K+/mes, es ~{total_monthly/10000*100:.1f}% de ingresos.\n\n"
        f"  Todas las evals pasan la regla del 10x:\n"
        f"  el costo de no evaluar supera ampliamente el costo de evaluar.\n"
        f"  Sub-evaluar es más caro que sobre-evaluar en dominio fiscal.",
        style="yellow",
    ))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    console.print(Panel(
        "[bold]Costo, latencia y frontera de Pareto de evals[/bold]\n\n"
        "Análisis económico del pipeline de evaluación:\n"
        "cuánto cuesta, qué optimizar, y cómo elegir configuración.",
        style="bold blue",
    ))

    # 1. Precios de modelos
    show_pricing_table()

    # 2. Desglose de costo
    show_cost_breakdown()

    # 3. Frontera de Pareto
    show_configs_table()

    # 4. Juez escalonado
    show_cascading_analysis()

    # 5. Sampling vs poder
    show_sampling_tradeoff()

    # 6. Presupuesto mensual
    show_monthly_budget()


if __name__ == "__main__":
    main()
