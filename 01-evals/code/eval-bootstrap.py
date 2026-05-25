"""Estadística para sistemas estocásticos: bootstrap y CIs desde cero.

Implementa bootstrapping para métricas de eval sin dependencias
estadísticas externas. Demuestra:
  1. Bootstrap CI para una métrica (Recall@5)
  2. Bootstrap pareado para comparar dos sistemas
  3. Análisis de poder estadístico
  4. Trampas: comparaciones múltiples y significancia vs relevancia

Ejecutar con:
    uv run python 01-evals/code/eval-bootstrap.py

No requiere API keys.
"""

import math
import random
from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from shared.utils import get_logger

log = get_logger(__name__)
console = Console()

# Fijar seed para reproducibilidad
random.seed(42)


# ---------------------------------------------------------------------------
# Modelo de datos
# ---------------------------------------------------------------------------

@dataclass
class BootstrapResult:
    """Resultado de un análisis bootstrap."""
    statistic_name: str
    observed: float
    ci_lower: float
    ci_upper: float
    n_bootstrap: int
    n_samples: int


@dataclass
class ComparisonResult:
    """Resultado de comparación bootstrap pareada entre dos sistemas."""
    metric_name: str
    mean_a: float
    mean_b: float
    mean_diff: float
    ci_lower: float
    ci_upper: float
    significant: bool
    practically_relevant: bool


# ---------------------------------------------------------------------------
# Datos simulados: scores de retrieval para queries fiscales
# ---------------------------------------------------------------------------

SYSTEM_A_RECALL = [
    1.0, 0.5, 1.0, 0.0, 1.0, 0.5, 0.0, 1.0, 0.5, 1.0,
    0.0, 1.0, 0.5, 0.5, 1.0, 0.0, 0.5, 1.0, 0.0, 1.0,
    0.5, 1.0, 0.0, 0.5, 1.0, 0.5, 0.0, 1.0, 0.5, 1.0,
]

# Sistema B: mejora moderada (~+5pp) — nuevo chunking strategy
SYSTEM_B_RECALL = [
    1.0, 1.0, 1.0, 0.0, 1.0, 0.5, 0.5, 1.0, 0.5, 1.0,
    0.0, 1.0, 0.5, 1.0, 1.0, 0.0, 0.5, 1.0, 0.5, 1.0,
    0.5, 1.0, 0.0, 0.5, 1.0, 1.0, 0.0, 1.0, 0.5, 1.0,
]

# Sistema C: mejora trivial (~+1pp) — ajuste cosmético
SYSTEM_C_RECALL = [
    1.0, 0.5, 1.0, 0.0, 1.0, 0.5, 0.0, 1.0, 0.5, 1.0,
    0.0, 1.0, 0.5, 0.5, 1.0, 0.5, 0.5, 1.0, 0.0, 1.0,
    0.5, 1.0, 0.0, 0.5, 1.0, 0.5, 0.0, 1.0, 0.5, 1.0,
]

QUERY_LABELS = [
    "IVA digital tasa", "Sujetos pasivos lobby", "Presupuesto inmunizaciones",
    "Multa Ley Lobby", "Alumno prioritario USE", "Indicios servicio digital",
    "USE 5º vs 7º básico", "Reporte inmunizaciones", "Ley Transparencia multa",
    "Rango multas lobby", "SEP media ponderada", "Glosa salud primaria",
    "Declaración IVA plazo", "Lobby registro activo", "Subvención preferente",
    "IVA plataforma hosting", "Presupuesto medicamentos", "Lobby sanción reiterada",
    "SEP escuela nueva", "Circular SII impugnación", "USE prebásica valor",
    "Glosa 09 operación", "IVA B2B reverse charge", "Lobby reuniones acta",
    "Subvención concentrada", "Presupuesto APS", "IVA exportación servicio",
    "Lobby sujeto activo", "SEP plan mejoramiento", "Glosa 12 farmacia",
]


# ---------------------------------------------------------------------------
# Implementación de bootstrap desde cero
# ---------------------------------------------------------------------------

def bootstrap_ci(
    data: list[float],
    statistic: str = "mean",
    n_bootstrap: int = 10_000,
    confidence: float = 0.95,
) -> BootstrapResult:
    """Calcula un intervalo de confianza bootstrap por el método de percentil.

    Args:
        data: Observaciones originales.
        statistic: "mean" o "median".
        n_bootstrap: Número de muestras bootstrap.
        confidence: Nivel de confianza (0.95 = 95%).

    Returns:
        BootstrapResult con el CI calculado.
    """
    n = len(data)

    def calc_stat(sample: list[float]) -> float:
        if statistic == "mean":
            return sum(sample) / len(sample)
        elif statistic == "median":
            s = sorted(sample)
            mid = len(s) // 2
            if len(s) % 2 == 0:
                return (s[mid - 1] + s[mid]) / 2
            return s[mid]
        raise ValueError(f"Estadística desconocida: {statistic}")

    observed = calc_stat(data)

    # Generar B muestras bootstrap
    bootstrap_stats = []
    for _ in range(n_bootstrap):
        sample = random.choices(data, k=n)
        bootstrap_stats.append(calc_stat(sample))

    # Percentiles
    bootstrap_stats.sort()
    alpha = 1 - confidence
    lower_idx = int(n_bootstrap * alpha / 2)
    upper_idx = int(n_bootstrap * (1 - alpha / 2))

    return BootstrapResult(
        statistic_name=statistic,
        observed=observed,
        ci_lower=bootstrap_stats[lower_idx],
        ci_upper=bootstrap_stats[upper_idx],
        n_bootstrap=n_bootstrap,
        n_samples=n,
    )


def bootstrap_paired_comparison(
    scores_a: list[float],
    scores_b: list[float],
    n_bootstrap: int = 10_000,
    confidence: float = 0.95,
    min_relevant_delta: float = 0.02,
) -> ComparisonResult:
    """Compara dos sistemas con bootstrap pareado.

    Args:
        scores_a: Scores del sistema A (baseline).
        scores_b: Scores del sistema B (candidato).
        n_bootstrap: Número de muestras bootstrap.
        confidence: Nivel de confianza.
        min_relevant_delta: Mínima diferencia prácticamente relevante.

    Returns:
        ComparisonResult con CI de la diferencia y decisión.
    """
    n = len(scores_a)
    assert len(scores_b) == n, "Los sistemas deben evaluarse en las mismas queries"

    # Diferencias pareadas
    deltas = [b - a for a, b in zip(scores_a, scores_b)]
    observed_diff = sum(deltas) / n

    # Bootstrap sobre las diferencias
    bootstrap_diffs = []
    for _ in range(n_bootstrap):
        sample = random.choices(deltas, k=n)
        bootstrap_diffs.append(sum(sample) / len(sample))

    bootstrap_diffs.sort()
    alpha = 1 - confidence
    lower_idx = int(n_bootstrap * alpha / 2)
    upper_idx = int(n_bootstrap * (1 - alpha / 2))

    ci_lower = bootstrap_diffs[lower_idx]
    ci_upper = bootstrap_diffs[upper_idx]

    significant = ci_lower > 0 or ci_upper < 0  # CI no cruza 0
    practically_relevant = significant and abs(observed_diff) >= min_relevant_delta

    return ComparisonResult(
        metric_name="Recall@5",
        mean_a=sum(scores_a) / n,
        mean_b=sum(scores_b) / n,
        mean_diff=observed_diff,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        significant=significant,
        practically_relevant=practically_relevant,
    )


def power_analysis(
    sigma: float,
    delta: float,
    alpha: float = 0.05,
    power: float = 0.80,
) -> int:
    """Calcula el tamaño de muestra necesario para un test pareado.

    Usa la fórmula: n = (z_alpha + z_beta)^2 * sigma^2 / delta^2

    Args:
        sigma: Desviación estándar esperada de las diferencias.
        delta: Mínima diferencia que se quiere detectar.
        alpha: Nivel de significancia.
        power: Poder estadístico deseado.

    Returns:
        Tamaño de muestra mínimo.
    """
    # Aproximación de cuantiles normales usando fórmula racional
    # z_0.975 ≈ 1.96, z_0.80 ≈ 0.8416
    z_alpha = _normal_quantile(1 - alpha / 2)
    z_beta = _normal_quantile(power)

    n = math.ceil((z_alpha + z_beta) ** 2 * sigma ** 2 / delta ** 2)
    return n


def _normal_quantile(p: float) -> float:
    """Aproximación del cuantil de la normal estándar (Abramowitz & Stegun)."""
    # Para los valores que usamos, hardcodear es más transparente
    quantiles = {
        0.975: 1.960,
        0.995: 2.576,
        0.800: 0.842,
        0.900: 1.282,
    }
    if p in quantiles:
        return quantiles[p]
    # Fallback: aproximación de Beasley-Springer-Moro (simplificada)
    if p > 0.5:
        return -_normal_quantile(1 - p) if p != 1.0 else float('inf')
    t = math.sqrt(-2 * math.log(p))
    return -(t - (2.515517 + 0.802853 * t + 0.010328 * t**2) /
             (1 + 1.432788 * t + 0.189269 * t**2 + 0.001308 * t**3))


# ---------------------------------------------------------------------------
# Visualización
# ---------------------------------------------------------------------------

def show_single_bootstrap(data: list[float], label: str) -> BootstrapResult:
    """Muestra bootstrap CI para un solo sistema."""
    result = bootstrap_ci(data, statistic="mean", n_bootstrap=10_000)

    n = len(data)
    avg = result.observed
    std = (sum((x - avg) ** 2 for x in data) / (n - 1)) ** 0.5

    console.print(Panel(
        f"[bold]Bootstrap CI para {label}[/bold]\n\n"
        f"  n (queries):        {n}\n"
        f"  Media observada:    {avg:.4f}\n"
        f"  Desv. estándar:     {std:.4f}\n"
        f"  Bootstrap samples:  {result.n_bootstrap:,}\n\n"
        f"  CI 95%:  [{result.ci_lower:.4f}, {result.ci_upper:.4f}]\n"
        f"  Ancho:   {result.ci_upper - result.ci_lower:.4f}\n\n"
        f"  Interpretación: el Recall@5 real de este sistema\n"
        f"  está entre {result.ci_lower:.2%} y {result.ci_upper:.2%}\n"
        f"  con 95% de confianza.",
        style="blue",
    ))

    return result


def show_bootstrap_walkthrough(data: list[float]) -> None:
    """Muestra paso a paso las primeras muestras bootstrap."""
    console.print()
    console.print(Panel(
        "[bold]Walkthrough: 5 primeras muestras bootstrap[/bold]\n"
        "Cada fila = una muestra de n=30 con reemplazo",
        style="blue",
    ))

    table = Table(title="Primeras muestras bootstrap")
    table.add_column("Muestra", style="bold", justify="center")
    table.add_column("Valores seleccionados (primeros 8)", width=40)
    table.add_column("Media", justify="center")

    # Reset seed para walkthrough reproducible
    rng = random.Random(123)

    means = []
    for i in range(5):
        sample = rng.choices(data, k=len(data))
        mean = sum(sample) / len(sample)
        means.append(mean)
        vals_str = ", ".join(f"{v:.1f}" for v in sample[:8]) + ", ..."
        table.add_row(f"B{i+1}", vals_str, f"{mean:.4f}")

    console.print(table)

    observed = sum(data) / len(data)
    console.print(
        f"\n  Media original: {observed:.4f}"
        f"\n  Rango en 5 muestras: [{min(means):.4f}, {max(means):.4f}]"
        f"\n  Con 10,000 muestras, este rango define el CI."
    )


def show_paired_comparison(
    scores_a: list[float],
    scores_b: list[float],
    label_a: str,
    label_b: str,
    min_delta: float = 0.02,
) -> ComparisonResult:
    """Muestra comparación bootstrap pareada entre dos sistemas."""
    result = bootstrap_paired_comparison(
        scores_a, scores_b,
        n_bootstrap=10_000,
        min_relevant_delta=min_delta,
    )

    # Tabla de diferencias por query (muestra primeras 10)
    table = Table(
        title=f"Diferencias pareadas: {label_b} - {label_a} (primeras 10 queries)",
        show_lines=True,
    )
    table.add_column("#", style="bold", justify="center", width=4)
    table.add_column("Query", width=28)
    table.add_column(label_a, justify="center", width=8)
    table.add_column(label_b, justify="center", width=8)
    table.add_column("δ", justify="center", width=8)

    for i in range(min(10, len(scores_a))):
        delta = scores_b[i] - scores_a[i]
        delta_color = "green" if delta > 0 else "red" if delta < 0 else "dim"
        table.add_row(
            str(i + 1),
            QUERY_LABELS[i][:26] + ".." if len(QUERY_LABELS[i]) > 28 else QUERY_LABELS[i],
            f"{scores_a[i]:.1f}",
            f"{scores_b[i]:.1f}",
            f"[{delta_color}]{delta:+.1f}[/{delta_color}]",
        )

    console.print(table)

    # Resultado
    status = ""
    style = ""
    if result.significant and result.practically_relevant:
        status = "SIGNIFICATIVA y RELEVANTE → Deploy recomendado"
        style = "bold green"
    elif result.significant:
        status = "SIGNIFICATIVA pero TRIVIAL → Mejora real pero muy pequeña"
        style = "bold yellow"
    else:
        status = "NO SIGNIFICATIVA → No hay evidencia de mejora"
        style = "bold red"

    console.print(Panel(
        f"[bold]Resultado: {label_b} vs {label_a}[/bold]\n\n"
        f"  Media {label_a}:   {result.mean_a:.4f}\n"
        f"  Media {label_b}:   {result.mean_b:.4f}\n"
        f"  Diferencia:     {result.mean_diff:+.4f}\n\n"
        f"  CI 95% de δ:    [{result.ci_lower:+.4f}, {result.ci_upper:+.4f}]\n"
        f"  Incluye 0:      {'Sí' if not result.significant else 'No'}\n"
        f"  δ mínimo relevante: {min_delta:.2f}\n\n"
        f"  [{style}]→ {status}[/{style}]",
        style="blue",
    ))

    return result


def show_power_analysis() -> None:
    """Muestra tabla de tamaño de muestra para distintos escenarios."""
    console.print()
    console.print(Panel(
        "[bold]Poder estadístico: ¿cuántas queries necesitas?[/bold]\n\n"
        "Tamaño de muestra mínimo para detectar una mejora de δ\n"
        "con 80% de poder y α = 0.05.\n"
        "σ estimado a partir de varianza típica de Recall@5.",
        style="blue",
    ))

    # Estimar sigma de los datos reales
    deltas_real = [b - a for a, b in zip(SYSTEM_A_RECALL, SYSTEM_B_RECALL)]
    sigma_obs = (sum((d - sum(deltas_real)/len(deltas_real))**2 for d in deltas_real) / (len(deltas_real) - 1)) ** 0.5

    table = Table(title=f"Tamaño de muestra (σ observado = {sigma_obs:.3f})")
    table.add_column("δ mínimo", style="bold", justify="center")
    table.add_column("Descripción", width=30)
    table.add_column("n necesario", justify="center")
    table.add_column("¿Nuestro n=30?", justify="center")

    scenarios = [
        (0.15, "Mejora grande (15pp)"),
        (0.10, "Mejora notable (10pp)"),
        (0.05, "Mejora moderada (5pp)"),
        (0.02, "Mejora fina (2pp)"),
        (0.01, "Mejora mínima (1pp)"),
    ]

    for delta, desc in scenarios:
        n_needed = power_analysis(sigma_obs, delta)
        sufficient = "✓ Suficiente" if 30 >= n_needed else f"✗ Faltan {n_needed - 30}"
        color = "green" if 30 >= n_needed else "red"
        table.add_row(
            f"{delta:.2f}",
            desc,
            str(n_needed),
            f"[{color}]{sufficient}[/{color}]",
        )

    console.print(table)

    console.print(Panel(
        "[bold yellow]Lección:[/bold yellow]\n\n"
        f"  Con n=30 queries, solo podemos detectar mejoras > ~10pp.\n"
        f"  Para detectar mejoras de 5pp necesitaríamos ~{power_analysis(sigma_obs, 0.05)} queries.\n\n"
        f"  Esto conecta directamente con la sección 4 (golden datasets):\n"
        f"  el tamaño del golden dataset determina qué mejoras puedes medir.\n"
        f"  Un golden de 30 items es suficiente para desarrollo,\n"
        f"  pero no para decisiones de release con cambios sutiles.",
        style="yellow",
    ))


def show_multiple_comparisons() -> None:
    """Demuestra el problema de comparaciones múltiples."""
    console.print()
    console.print(Panel(
        "[bold]Trampa: comparaciones múltiples[/bold]\n\n"
        "Si comparas 5 variantes contra el baseline con α=0.05,\n"
        "la probabilidad de al menos un falso positivo es alta.",
        style="bold red",
    ))

    # Simular 5 "sistemas" que en realidad son iguales (ruido puro)
    rng = random.Random(99)
    baseline = SYSTEM_A_RECALL

    table = Table(title="5 'variantes' vs baseline (todas son ruido)")
    table.add_column("Variante", style="bold")
    table.add_column("Media", justify="center")
    table.add_column("Δ vs baseline", justify="center")
    table.add_column("CI 95% de δ", justify="center")
    table.add_column("p < 0.05?", justify="center")
    table.add_column("Bonferroni\n(α=0.01)?", justify="center")

    n_sig = 0
    for i in range(5):
        # Generar sistema con ruido aleatorio (sin mejora real)
        fake_system = []
        for score in baseline:
            noise = rng.choice([-0.5, 0, 0, 0, 0.5])
            fake_system.append(max(0.0, min(1.0, score + noise)))

        result = bootstrap_paired_comparison(
            baseline, fake_system,
            n_bootstrap=10_000,
        )

        sig = "Sí ⚠️" if result.significant else "No"
        sig_color = "red" if result.significant else "green"

        # Bonferroni: CI al 99% (α/5 = 0.01)
        result_bonf = bootstrap_paired_comparison(
            baseline, fake_system,
            n_bootstrap=10_000,
            confidence=0.99,
        )
        bonf = "Sí ⚠️" if result_bonf.significant else "No ✓"
        bonf_color = "red" if result_bonf.significant else "green"

        if result.significant:
            n_sig += 1

        table.add_row(
            f"V{i+1}",
            f"{sum(fake_system)/len(fake_system):.3f}",
            f"{result.mean_diff:+.3f}",
            f"[{result.ci_lower:+.3f}, {result.ci_upper:+.3f}]",
            f"[{sig_color}]{sig}[/{sig_color}]",
            f"[{bonf_color}]{bonf}[/{bonf_color}]",
        )

    console.print(table)

    console.print(
        f"\n  Falsos positivos sin corrección: {n_sig}/5"
        f"\n  P(≥1 falso positivo con 5 tests): {1 - 0.95**5:.1%}"
        f"\n  Corrección Bonferroni (α/5 = 0.01) reduce los falsos positivos."
    )


def show_summary() -> None:
    """Tabla resumen del protocolo recomendado."""
    console.print()
    table = Table(title="Protocolo estadístico para evals")
    table.add_column("Paso", style="bold", justify="center", width=5)
    table.add_column("Acción", width=35)
    table.add_column("Detalle", width=35)

    table.add_row("1", "Definir δ mínimo relevante",
                  "¿Qué mejora justifica un deploy?")
    table.add_row("2", "Calcular n necesario",
                  "n ≈ 7.85 × σ² / δ²")
    table.add_row("3", "Ejecutar eval en n queries",
                  "Mismas queries para A y B")
    table.add_row("4", "Bootstrap pareado (B=10,000)",
                  "CI 95% de la diferencia")
    table.add_row("5", "Verificar significancia",
                  "¿CI excluye 0?")
    table.add_row("6", "Verificar relevancia práctica",
                  "¿|δ| > umbral mínimo?")
    table.add_row("7", "Corregir por comparaciones\nmúltiples si aplica",
                  "Bonferroni si > 1 variante")

    console.print(table)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    console.print(Panel(
        "[bold]Estadística para sistemas estocásticos[/bold]\n\n"
        "Bootstrap, intervalos de confianza y poder estadístico\n"
        "para tomar decisiones de deploy con evidencia.",
        style="bold blue",
    ))

    # 1. Bootstrap CI para un sistema
    console.print(Panel(
        "[bold]Paso 1: Bootstrap CI para un solo sistema[/bold]\n\n"
        "30 queries del golden dataset evaluadas con Recall@5.\n"
        "¿Cuál es el intervalo de confianza de la métrica?",
        style="blue",
    ))

    show_bootstrap_walkthrough(SYSTEM_A_RECALL)
    ci_a = show_single_bootstrap(SYSTEM_A_RECALL, "Sistema A (baseline)")

    # 2. Comparación B vs A (mejora real)
    console.print()
    console.print(Panel(
        "[bold]Paso 2: ¿Sistema B es mejor que A?[/bold]\n\n"
        "Sistema B usa una nueva estrategia de chunking.\n"
        "Bootstrap pareado sobre las mismas 30 queries.",
        style="blue",
    ))

    comp_ba = show_paired_comparison(
        SYSTEM_A_RECALL, SYSTEM_B_RECALL,
        "A (baseline)", "B (nuevo chunking)",
        min_delta=0.02,
    )

    # 3. Comparación C vs A (mejora trivial)
    console.print()
    console.print(Panel(
        "[bold]Paso 3: ¿Sistema C es mejor que A?[/bold]\n\n"
        "Sistema C tiene un ajuste menor en el prompt.\n"
        "La diferencia es mínima — ¿vale un deploy?",
        style="blue",
    ))

    comp_ca = show_paired_comparison(
        SYSTEM_A_RECALL, SYSTEM_C_RECALL,
        "A (baseline)", "C (ajuste prompt)",
        min_delta=0.02,
    )

    # 4. Poder estadístico
    console.print()
    console.print(Panel(
        "[bold]Paso 4: ¿Nuestro golden dataset es suficiente?[/bold]\n\n"
        "Análisis de poder: ¿con n=30 podemos detectar mejoras relevantes?",
        style="blue",
    ))

    show_power_analysis()

    # 5. Comparaciones múltiples
    show_multiple_comparisons()

    # 6. Resumen
    show_summary()


if __name__ == "__main__":
    main()
