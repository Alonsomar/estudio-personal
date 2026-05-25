"""Eval harness: orquestación de evaluaciones con gates y reporting.

Simula un pipeline completo de evaluación:
  1. Carga del golden dataset
  2. Ejecución del sistema (simulada)
  3. Cálculo de métricas con bootstrap CIs
  4. Comparación contra baseline
  5. Gate decision: PASS / WARN / FAIL
  6. Reporte estructurado (JSON + consola)

Ejecutar con:
    uv run python 01-evals/code/eval-harness.py

No requiere API keys.
"""

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from shared.utils import get_logger, get_project_root

log = get_logger(__name__)
console = Console()

random.seed(42)


# ---------------------------------------------------------------------------
# Modelo de datos
# ---------------------------------------------------------------------------

@dataclass
class MetricResult:
    """Resultado de una métrica con intervalo de confianza."""
    name: str
    mean: float
    ci_lower: float
    ci_upper: float
    threshold_abs: float | None = None
    threshold_rel: float | None = None
    baseline: float | None = None


@dataclass
class GateResult:
    """Resultado de un gate individual."""
    name: str
    condition: str
    passed: bool
    value: float
    threshold: float
    severity: str  # "critical", "high", "warn"


@dataclass
class EvalRunReport:
    """Reporte completo de una ejecución de eval."""
    run_id: str
    commit: str
    timestamp: str
    tier: str  # "pre-merge", "nightly", "weekly"
    metrics: list[MetricResult]
    gates: list[GateResult]
    overall: str  # "PASS", "WARN", "FAIL"
    queries_failed: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Baseline histórico (simulado)
# ---------------------------------------------------------------------------

BASELINE = {
    "recall_at_5": 0.68,
    "mrr": 0.62,
    "ndcg_at_5": 0.55,
    "faithfulness": 0.78,
    "answer_relevance": 0.82,
    "ghost_citations": 0,
    "latency_p95_ms": 3200,
}


# ---------------------------------------------------------------------------
# Simulación del sistema bajo evaluación
# ---------------------------------------------------------------------------

def simulate_eval_run(
    n_queries: int = 30,
    scenario: str = "normal",
) -> dict:
    """Simula una ejecución de eval con resultados por query.

    Args:
        n_queries: Número de queries a evaluar.
        scenario: "normal", "regression", "improvement", "ghost_citation".

    Returns:
        Dict con scores por métrica (lista de floats por query).
    """
    rng = random.Random(42)

    # Parámetros base por escenario
    params = {
        "normal": {
            "recall_mean": 0.70, "recall_std": 0.35,
            "faith_mean": 0.80, "faith_std": 0.15,
            "relev_mean": 0.83, "relev_std": 0.12,
            "ghost_prob": 0.00, "latency_base": 2800,
        },
        "regression": {
            "recall_mean": 0.55, "recall_std": 0.40,
            "faith_mean": 0.65, "faith_std": 0.20,
            "relev_mean": 0.75, "relev_std": 0.15,
            "ghost_prob": 0.00, "latency_base": 3500,
        },
        "improvement": {
            "recall_mean": 0.80, "recall_std": 0.30,
            "faith_mean": 0.88, "faith_std": 0.10,
            "relev_mean": 0.90, "relev_std": 0.08,
            "ghost_prob": 0.00, "latency_base": 2500,
        },
        "ghost_citation": {
            "recall_mean": 0.72, "recall_std": 0.30,
            "faith_mean": 0.75, "faith_std": 0.18,
            "relev_mean": 0.85, "relev_std": 0.10,
            "ghost_prob": 0.10, "latency_base": 2900,
        },
    }[scenario]

    def clamp(v: float) -> float:
        return max(0.0, min(1.0, v))

    recall_scores = [clamp(rng.gauss(params["recall_mean"], params["recall_std"])) for _ in range(n_queries)]
    # Binarizar recall (0 o 1 para Recall@k simulado)
    recall_scores = [1.0 if s > 0.5 else 0.0 if s < 0.3 else 0.5 for s in recall_scores]

    faith_scores = [clamp(rng.gauss(params["faith_mean"], params["faith_std"])) for _ in range(n_queries)]
    relev_scores = [clamp(rng.gauss(params["relev_mean"], params["relev_std"])) for _ in range(n_queries)]
    ghost_citations = [1 if rng.random() < params["ghost_prob"] else 0 for _ in range(n_queries)]
    latencies = [max(500, rng.gauss(params["latency_base"], 600)) for _ in range(n_queries)]

    return {
        "recall_at_5": recall_scores,
        "faithfulness": faith_scores,
        "answer_relevance": relev_scores,
        "ghost_citations": ghost_citations,
        "latency_ms": latencies,
    }


# ---------------------------------------------------------------------------
# Bootstrap (reutilizado de sección 8, simplificado)
# ---------------------------------------------------------------------------

def bootstrap_ci(
    data: list[float],
    n_bootstrap: int = 5_000,
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    """Retorna (media, ci_lower, ci_upper)."""
    n = len(data)
    observed = sum(data) / n

    means = []
    for _ in range(n_bootstrap):
        sample = random.choices(data, k=n)
        means.append(sum(sample) / len(sample))

    means.sort()
    alpha = 1 - confidence
    lo = means[int(n_bootstrap * alpha / 2)]
    hi = means[int(n_bootstrap * (1 - alpha / 2))]
    return observed, lo, hi


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------

def evaluate_gates(results: dict, baseline: dict) -> list[GateResult]:
    """Evalúa todos los gates y retorna resultados."""
    gates = []

    # Gate 1: Recall@5 absoluto (crítico)
    recall_mean = sum(results["recall_at_5"]) / len(results["recall_at_5"])
    gates.append(GateResult(
        name="Recall@5 mínimo",
        condition="Recall@5 ≥ 0.60",
        passed=recall_mean >= 0.60,
        value=recall_mean,
        threshold=0.60,
        severity="critical",
    ))

    # Gate 2: Faithfulness absoluto (crítico)
    faith_mean = sum(results["faithfulness"]) / len(results["faithfulness"])
    gates.append(GateResult(
        name="Faithfulness mínimo",
        condition="Faithfulness ≥ 0.50",
        passed=faith_mean >= 0.50,
        value=faith_mean,
        threshold=0.50,
        severity="critical",
    ))

    # Gate 3: Citas fantasma (crítico, zero tolerance)
    ghost_count = sum(results["ghost_citations"])
    gates.append(GateResult(
        name="Citas fantasma",
        condition="Ghost citations = 0",
        passed=ghost_count == 0,
        value=float(ghost_count),
        threshold=0.0,
        severity="critical",
    ))

    # Gate 4: No regresión en Recall (estadístico)
    recall_mean, recall_lo, recall_hi = bootstrap_ci(results["recall_at_5"])
    delta_lo = recall_lo - baseline["recall_at_5"]
    gates.append(GateResult(
        name="No regresión Recall",
        condition="CI95(Recall_new - baseline) > -0.05",
        passed=delta_lo > -0.05,
        value=delta_lo,
        threshold=-0.05,
        severity="high",
    ))

    # Gate 5: Latencia p95 (warning)
    sorted_lat = sorted(results["latency_ms"])
    p95_idx = int(len(sorted_lat) * 0.95)
    latency_p95 = sorted_lat[min(p95_idx, len(sorted_lat) - 1)]
    gates.append(GateResult(
        name="Latencia p95",
        condition="Latencia p95 < 4000ms",
        passed=latency_p95 < 4000,
        value=latency_p95,
        threshold=4000.0,
        severity="warn",
    ))

    return gates


def determine_overall(gates: list[GateResult]) -> str:
    """Determina el resultado global basado en los gates."""
    critical_fail = any(not g.passed for g in gates if g.severity == "critical")
    high_fail = any(not g.passed for g in gates if g.severity == "high")
    warn_fail = any(not g.passed for g in gates if g.severity == "warn")

    if critical_fail or high_fail:
        return "FAIL"
    if warn_fail:
        return "WARN"
    return "PASS"


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def build_report(
    results: dict,
    gates: list[GateResult],
    overall: str,
    scenario: str,
    n_queries: int,
) -> EvalRunReport:
    """Construye el reporte completo."""
    metrics = []
    for metric_name in ["recall_at_5", "faithfulness", "answer_relevance"]:
        mean, lo, hi = bootstrap_ci(results[metric_name])
        metrics.append(MetricResult(
            name=metric_name,
            mean=mean,
            ci_lower=lo,
            ci_upper=hi,
            baseline=BASELINE.get(metric_name),
        ))

    # Queries que fallaron (recall = 0)
    failed = [f"q-{i+1:03d}" for i, s in enumerate(results["recall_at_5"]) if s == 0.0]

    return EvalRunReport(
        run_id=f"eval-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{scenario}",
        commit="d415070",
        timestamp=datetime.now(timezone.utc).isoformat(),
        tier="nightly",
        metrics=metrics,
        gates=gates,
        overall=overall,
        queries_failed=failed,
    )


def show_metrics_table(report: EvalRunReport) -> None:
    """Muestra tabla de métricas con CIs y comparación al baseline."""
    table = Table(title="Métricas con intervalos de confianza", show_lines=True)
    table.add_column("Métrica", style="bold", width=20)
    table.add_column("Media", justify="center", width=8)
    table.add_column("CI 95%", justify="center", width=18)
    table.add_column("Baseline", justify="center", width=10)
    table.add_column("Δ", justify="center", width=8)
    table.add_column("Estado", justify="center", width=10)

    for m in report.metrics:
        delta = m.mean - m.baseline if m.baseline else 0
        if delta > 0.02:
            status = "[green]↑ Mejora[/green]"
        elif delta < -0.02:
            status = "[red]↓ Peor[/red]"
        else:
            status = "[dim]≈ Igual[/dim]"

        delta_color = "green" if delta > 0 else "red" if delta < 0 else "dim"

        table.add_row(
            m.name,
            f"{m.mean:.3f}",
            f"[{m.ci_lower:.3f}, {m.ci_upper:.3f}]",
            f"{m.baseline:.3f}" if m.baseline else "—",
            f"[{delta_color}]{delta:+.3f}[/{delta_color}]",
            status,
        )

    console.print(table)


def show_gates_table(gates: list[GateResult], overall: str) -> None:
    """Muestra tabla de gates con resultado."""
    table = Table(title="Gates de calidad", show_lines=True)
    table.add_column("Gate", style="bold", width=22)
    table.add_column("Condición", width=32)
    table.add_column("Valor", justify="center", width=10)
    table.add_column("Umbral", justify="center", width=10)
    table.add_column("Sev.", justify="center", width=10)
    table.add_column("Resultado", justify="center", width=10)

    for g in gates:
        if g.passed:
            result = "[green]PASS[/green]"
        elif g.severity == "warn":
            result = "[yellow]WARN[/yellow]"
        else:
            result = "[red]FAIL[/red]"

        sev_color = {"critical": "red", "high": "yellow", "warn": "dim"}[g.severity]

        table.add_row(
            g.name,
            g.condition,
            f"{g.value:.2f}" if g.value < 100 else f"{g.value:.0f}",
            f"{g.threshold:.2f}" if g.threshold < 100 else f"{g.threshold:.0f}",
            f"[{sev_color}]{g.severity}[/{sev_color}]",
            result,
        )

    # Overall
    table.add_section()
    overall_style = {"PASS": "bold green", "WARN": "bold yellow", "FAIL": "bold red"}[overall]
    table.add_row("", "", "", "", "[bold]OVERALL[/bold]", f"[{overall_style}]{overall}[/{overall_style}]")

    console.print(table)


def show_failed_queries(report: EvalRunReport) -> None:
    """Muestra queries que fallaron."""
    if not report.queries_failed:
        console.print("  [green]Todas las queries pasaron.[/green]\n")
        return

    console.print(f"  [red]Queries con Recall@5 = 0 ({len(report.queries_failed)}):[/red]")
    for qid in report.queries_failed[:10]:
        console.print(f"    - {qid}")
    if len(report.queries_failed) > 10:
        console.print(f"    ... y {len(report.queries_failed) - 10} más")
    console.print()


def export_report(report: EvalRunReport) -> Path:
    """Exporta el reporte a JSON."""
    output_dir = get_project_root() / "01-evals" / "examples"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{report.run_id}.json"

    report_dict = {
        "run_id": report.run_id,
        "commit": report.commit,
        "timestamp": report.timestamp,
        "tier": report.tier,
        "overall": report.overall,
        "metrics": {
            m.name: {
                "mean": round(m.mean, 4),
                "ci_lower": round(m.ci_lower, 4),
                "ci_upper": round(m.ci_upper, 4),
                "baseline": m.baseline,
            }
            for m in report.metrics
        },
        "gates": {
            g.name: {
                "condition": g.condition,
                "passed": g.passed,
                "value": round(g.value, 4),
                "threshold": g.threshold,
                "severity": g.severity,
            }
            for g in report.gates
        },
        "queries_failed": report.queries_failed,
    }

    output_path.write_text(json.dumps(report_dict, indent=2, ensure_ascii=False))
    return output_path


# ---------------------------------------------------------------------------
# Escenarios de demostración
# ---------------------------------------------------------------------------

def run_scenario(scenario: str, description: str, n_queries: int = 30) -> None:
    """Ejecuta un escenario completo de evaluación."""
    console.print(Panel(
        f"[bold]Escenario: {description}[/bold]\n"
        f"Simulando {n_queries} queries, comparando contra baseline.",
        style="blue",
    ))

    # 1. Ejecutar sistema
    results = simulate_eval_run(n_queries=n_queries, scenario=scenario)

    # 2. Evaluar gates
    gates = evaluate_gates(results, BASELINE)

    # 3. Determinar resultado global
    overall = determine_overall(gates)

    # 4. Construir reporte
    report = build_report(results, gates, overall, scenario, n_queries)

    # 5. Mostrar resultados
    show_metrics_table(report)
    show_gates_table(gates, overall)
    show_failed_queries(report)

    # 6. Exportar
    path = export_report(report)
    console.print(f"  Reporte exportado: {path.relative_to(get_project_root())}\n")


def show_pipeline_summary() -> None:
    """Muestra resumen del pipeline CI recomendado."""
    console.print()
    table = Table(title="Pipeline de evals recomendado para RAG fiscal")
    table.add_column("Etapa", style="bold", width=14)
    table.add_column("Frecuencia", width=14)
    table.add_column("Queries", justify="center", width=10)
    table.add_column("Gates", width=30)
    table.add_column("Costo", justify="center", width=10)

    table.add_row(
        "Pre-merge",
        "Cada PR",
        "10-15",
        "Absolutos (recall, faith, ghosts)",
        "< $0.50",
    )
    table.add_row(
        "Nightly",
        "Diario",
        "30-100",
        "Abs + estadísticos (bootstrap CI)",
        "< $5",
    )
    table.add_row(
        "Weekly",
        "Semanal",
        "100+",
        "Todos + tendencias + error analysis",
        "< $10",
    )

    console.print(table)


def show_regression_protocol() -> None:
    """Muestra el protocolo ante regresiones."""
    console.print()
    table = Table(title="Protocolo ante regresiones")
    table.add_column("Severidad", style="bold", width=12)
    table.add_column("Criterio", width=35)
    table.add_column("Acción", width=30)
    table.add_column("Plazo", justify="center", width=12)

    table.add_row(
        "[red]Crítica[/red]",
        "Ghost citations > 0 o Faith < 0.40",
        "Rollback + post-mortem",
        "Inmediato",
    )
    table.add_row(
        "[yellow]Alta[/yellow]",
        "Recall cae > 10pp fuera del CI",
        "Bloquear deploys, investigar",
        "24 horas",
    )
    table.add_row(
        "[dim]Media[/dim]",
        "Regresión significativa < 10pp",
        "Ticket prioritario",
        "1 semana",
    )
    table.add_row(
        "[dim]Baja[/dim]",
        "Tendencia negativa sin significancia",
        "Monitorear",
        "2 semanas",
    )

    console.print(table)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    console.print(Panel(
        "[bold]Eval Harness: regresiones y CI[/bold]\n\n"
        "Pipeline de evaluación con gates, bootstrap CIs,\n"
        "y decisión automática PASS/WARN/FAIL.\n"
        "Simulamos 4 escenarios para ilustrar el harness.",
        style="bold blue",
    ))

    # Escenario 1: Todo normal (PASS)
    run_scenario(
        "normal",
        "Sistema normal — sin cambios significativos",
    )

    # Escenario 2: Regresión (FAIL)
    run_scenario(
        "regression",
        "Regresión — nuevo prompt empeora retrieval y faithfulness",
    )

    # Escenario 3: Mejora (PASS)
    run_scenario(
        "improvement",
        "Mejora — nuevo chunking mejora todas las métricas",
    )

    # Escenario 4: Citas fantasma (FAIL crítico)
    run_scenario(
        "ghost_citation",
        "Citas fantasma — métricas OK pero cita artículos inexistentes",
    )

    # Resumen del pipeline
    show_pipeline_summary()

    # Protocolo ante regresiones
    show_regression_protocol()


if __name__ == "__main__":
    main()
