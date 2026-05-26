"""Online evals: logging, sampling, A/B testing y feedback loop.

Simula el pipeline de evaluación en producción:
  1. Generación de tráfico simulado (queries reales)
  2. Logging con redacción de PII
  3. Auto-eval por sampling (aleatorio + dirigido)
  4. A/B testing: comparación de dos sistemas
  5. Feedback loop: selección de candidatos para golden dataset
  6. Dashboard de métricas online

Ejecutar con:
    uv run python 01-evals/code/eval-online.py

No requiere API keys.
"""

import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from shared.utils import get_logger

log = get_logger(__name__)
console = Console()

random.seed(42)


# ---------------------------------------------------------------------------
# Modelo de datos
# ---------------------------------------------------------------------------

@dataclass
class RequestLog:
    """Log de un request en producción."""
    request_id: str
    session_id: str
    timestamp: str
    query: str
    query_redacted: str
    retrieved_docs: list[dict]
    response: str
    latency_ms: int
    model: str
    ab_group: str
    feedback: str | None       # "up", "down", None
    reformulated: bool
    auto_eval_score: float | None


@dataclass
class OnlineMetrics:
    """Métricas agregadas de online eval."""
    period: str
    total_requests: int
    thumbs_up_rate: float
    thumbs_down_rate: float
    no_feedback_rate: float
    reformulation_rate: float
    avg_latency_ms: float
    p95_latency_ms: float
    auto_eval_mean: float
    auto_eval_sampled: int


# ---------------------------------------------------------------------------
# Simulación de tráfico
# ---------------------------------------------------------------------------

QUERY_TEMPLATES = [
    "¿Cuál es la tasa de IVA para servicios digitales prestados desde el extranjero?",
    "¿Qué multa corresponde por infracción a la Ley de Lobby?",
    "¿Cuántas USE recibe un alumno prioritario de {nivel}?",
    "¿Qué presupuesto se asigna a inmunizaciones en 2024?",
    "¿Quiénes son sujetos pasivos de la Ley 20.730?",
    "¿Cuál es el plazo para declarar IVA por servicios digitales?",
    "¿Qué obligaciones tiene un prestador digital con RUT {rut}?",
    "¿Cuánto es la subvención preferente para el colegio de mi hijo {nombre}?",
    "¿Qué dice la Glosa 09 sobre operaciones en atención primaria?",
    "¿Cuál es el rango de multas en UTM por lobby no registrado?",
    "¿Cómo se calcula la USE para educación media?",
    "¿Qué reportes trimestrales debe entregar el Ministerio de Salud?",
    "¿Aplica IVA a servicios de hosting contratados por mi empresa RUT {rut}?",
    "¿Cuánto presupuesto tiene la Glosa 12 de farmacia?",
    "¿Puedo impugnar una circular del SII sobre IVA digital?",
    # Queries fuera de dominio (distribution shift)
    "¿Cuál es el tipo de cambio dólar-peso hoy?",
    "¿Cómo hago una demanda por pensión alimenticia?",
    "¿Qué vacunas necesita mi perro?",
]

NIVELES = ["1º básico", "3º básico", "5º básico", "7º básico", "1º medio", "3º medio"]
RUTS = ["12.345.678-9", "98.765.432-1", "11.111.111-1", "22.333.444-5"]
NOMBRES = ["Juanito", "María José", "Pedro", "Catalina"]


def generate_traffic(n_requests: int, ab_split: float = 0.5) -> list[RequestLog]:
    """Genera tráfico simulado de producción."""
    rng = random.Random(42)
    logs = []
    base_time = datetime(2026, 5, 26, 8, 0, 0, tzinfo=timezone.utc)

    for i in range(n_requests):
        # Query
        template = rng.choice(QUERY_TEMPLATES)
        query = template.format(
            nivel=rng.choice(NIVELES),
            rut=rng.choice(RUTS),
            nombre=rng.choice(NOMBRES),
        )

        # Redacción PII
        query_redacted = redact_pii(query)

        # Session (algunas queries comparten sesión)
        session_id = f"sess-{rng.randint(1, n_requests // 3):04d}"

        # A/B group
        ab_group = "A" if rng.random() < ab_split else "B"

        # Simular calidad según grupo y tipo de query
        is_out_of_domain = any(ood in query for ood in ["tipo de cambio", "demanda por pensión", "vacunas"])
        is_group_b = ab_group == "B"

        # Latencia (B es ligeramente más lento pero más preciso)
        base_latency = 2200 if not is_group_b else 2600
        latency = max(800, int(rng.gauss(base_latency, 500)))

        # Feedback simulado
        if is_out_of_domain:
            feedback_prob = {"up": 0.10, "down": 0.50, "none": 0.40}
        elif is_group_b:
            feedback_prob = {"up": 0.45, "down": 0.10, "none": 0.45}
        else:
            feedback_prob = {"up": 0.35, "down": 0.15, "none": 0.50}

        r = rng.random()
        if r < feedback_prob["up"]:
            feedback = "up"
        elif r < feedback_prob["up"] + feedback_prob["down"]:
            feedback = "down"
        else:
            feedback = None

        # Reformulación (más probable si feedback es down o no hay feedback)
        reformulated = rng.random() < (0.50 if feedback == "down" else 0.15 if feedback is None else 0.05)

        # Retrieved docs
        if is_out_of_domain:
            retrieved = [{"id": "norma-01-ley-lobby.txt", "score": 0.25}]
        else:
            retrieved = [
                {"id": f"doc-{rng.randint(1,4):03d}.txt", "score": round(rng.uniform(0.5, 0.95), 2)}
                for _ in range(rng.randint(2, 5))
            ]

        # Auto-eval score (solo para muestra)
        auto_eval = None  # Se llena después en el sampling

        timestamp = base_time + timedelta(minutes=i * 2 + rng.randint(0, 3))

        logs.append(RequestLog(
            request_id=f"req-{i+1:05d}",
            session_id=session_id,
            timestamp=timestamp.isoformat(),
            query=query,
            query_redacted=query_redacted,
            retrieved_docs=retrieved,
            response=f"[respuesta simulada para: {query_redacted[:50]}...]",
            latency_ms=latency,
            model="claude-sonnet-4-6",
            ab_group=ab_group,
            feedback=feedback,
            reformulated=reformulated,
            auto_eval_score=auto_eval,
        ))

    return logs


def redact_pii(text: str) -> str:
    """Redacta datos sensibles (RUT, montos, nombres conocidos)."""
    text = re.sub(r'\d{1,2}\.\d{3}\.\d{3}-[\dkK]', '[RUT]', text)
    text = re.sub(r'\$[\d\.]+', '[MONTO]', text)
    for name in NOMBRES:
        text = text.replace(name, '[NOMBRE]')
    return text


# ---------------------------------------------------------------------------
# Auto-eval por sampling
# ---------------------------------------------------------------------------

def auto_eval_sample(logs: list[RequestLog], sample_rate: float = 0.10) -> list[RequestLog]:
    """Aplica auto-eval a una muestra del tráfico.

    Simula un juez Haiku evaluando calidad de la respuesta.
    Sampling combinado: aleatorio + dirigido (thumbs-down).
    """
    rng = random.Random(99)
    sampled = []

    for log_entry in logs:
        # Sample aleatorio (5%)
        random_sample = rng.random() < 0.05

        # Sample dirigido: thumbs-down o reformulación (siempre)
        directed_sample = log_entry.feedback == "down" or log_entry.reformulated

        if random_sample or directed_sample:
            # Simular auto-eval score
            is_ood = any(ood in log_entry.query for ood in ["tipo de cambio", "demanda por pensión", "vacunas"])
            if is_ood:
                score = max(0.0, rng.gauss(0.25, 0.15))
            elif log_entry.feedback == "down":
                score = max(0.0, min(1.0, rng.gauss(0.45, 0.20)))
            else:
                score = max(0.0, min(1.0, rng.gauss(0.78, 0.12)))

            log_entry.auto_eval_score = round(score, 2)
            sampled.append(log_entry)

    return sampled


# ---------------------------------------------------------------------------
# Análisis
# ---------------------------------------------------------------------------

def compute_online_metrics(logs: list[RequestLog], period: str = "día") -> OnlineMetrics:
    """Calcula métricas agregadas de online eval."""
    n = len(logs)
    with_feedback = [l for l in logs if l.feedback is not None]
    n_feedback = len(with_feedback)

    thumbs_up = sum(1 for l in logs if l.feedback == "up")
    thumbs_down = sum(1 for l in logs if l.feedback == "down")
    no_feedback = sum(1 for l in logs if l.feedback is None)
    reformulated = sum(1 for l in logs if l.reformulated)

    latencies = [l.latency_ms for l in logs]
    sorted_lat = sorted(latencies)
    p95_idx = int(len(sorted_lat) * 0.95)

    auto_evals = [l.auto_eval_score for l in logs if l.auto_eval_score is not None]

    return OnlineMetrics(
        period=period,
        total_requests=n,
        thumbs_up_rate=thumbs_up / n if n > 0 else 0,
        thumbs_down_rate=thumbs_down / n if n > 0 else 0,
        no_feedback_rate=no_feedback / n if n > 0 else 0,
        reformulation_rate=reformulated / n if n > 0 else 0,
        avg_latency_ms=sum(latencies) / n if n > 0 else 0,
        p95_latency_ms=sorted_lat[min(p95_idx, len(sorted_lat)-1)] if sorted_lat else 0,
        auto_eval_mean=sum(auto_evals) / len(auto_evals) if auto_evals else 0,
        auto_eval_sampled=len(auto_evals),
    )


# ---------------------------------------------------------------------------
# Visualización
# ---------------------------------------------------------------------------

def show_pii_redaction(logs: list[RequestLog]) -> None:
    """Muestra ejemplo de redacción de PII."""
    console.print(Panel(
        "[bold]Redacción de PII en logging[/bold]\n\n"
        "Antes de almacenar queries, se redactan datos sensibles.",
        style="blue",
    ))

    table = Table(title="Ejemplos de redacción", show_lines=True)
    table.add_column("#", style="bold", justify="center", width=4)
    table.add_column("Query original", width=40)
    table.add_column("Query redactada", width=40)

    shown = 0
    for log_entry in logs:
        if log_entry.query != log_entry.query_redacted and shown < 5:
            table.add_row(
                log_entry.request_id[-3:],
                log_entry.query[:38] + ".." if len(log_entry.query) > 40 else log_entry.query,
                log_entry.query_redacted[:38] + ".." if len(log_entry.query_redacted) > 40 else log_entry.query_redacted,
            )
            shown += 1

    if shown == 0:
        table.add_row("—", "Sin queries con PII en esta muestra", "—")

    console.print(table)


def show_online_dashboard(metrics: OnlineMetrics) -> None:
    """Muestra dashboard de métricas online."""
    console.print()
    console.print(Panel(
        f"[bold]Dashboard online — {metrics.period}[/bold]\n"
        f"Total requests: {metrics.total_requests}",
        style="blue",
    ))

    table = Table(title="Métricas de producción", show_lines=True)
    table.add_column("Métrica", style="bold", width=24)
    table.add_column("Valor", justify="center", width=12)
    table.add_column("Baseline", justify="center", width=12)
    table.add_column("Estado", justify="center", width=12)

    rows = [
        ("Thumbs-up rate", f"{metrics.thumbs_up_rate:.1%}", "55-65%",
         "green" if metrics.thumbs_up_rate >= 0.55 else "yellow" if metrics.thumbs_up_rate >= 0.45 else "red"),
        ("Thumbs-down rate", f"{metrics.thumbs_down_rate:.1%}", "< 15%",
         "green" if metrics.thumbs_down_rate < 0.15 else "yellow" if metrics.thumbs_down_rate < 0.25 else "red"),
        ("Sin feedback", f"{metrics.no_feedback_rate:.1%}", "40-60%", "dim"),
        ("Reformulación", f"{metrics.reformulation_rate:.1%}", "25-35%",
         "green" if metrics.reformulation_rate < 0.35 else "yellow" if metrics.reformulation_rate < 0.45 else "red"),
        ("Latencia avg", f"{metrics.avg_latency_ms:.0f} ms", "< 3000",
         "green" if metrics.avg_latency_ms < 3000 else "yellow" if metrics.avg_latency_ms < 4000 else "red"),
        ("Latencia p95", f"{metrics.p95_latency_ms:.0f} ms", "< 5000",
         "green" if metrics.p95_latency_ms < 5000 else "yellow" if metrics.p95_latency_ms < 7000 else "red"),
        ("Auto-eval (sample)", f"{metrics.auto_eval_mean:.2f}", "> 0.65",
         "green" if metrics.auto_eval_mean >= 0.65 else "yellow" if metrics.auto_eval_mean >= 0.50 else "red"),
        ("Auto-eval (n sampled)", str(metrics.auto_eval_sampled), f"~{metrics.total_requests * 10 // 100}", "dim"),
    ]

    for name, val, baseline, color in rows:
        status = "[green]OK[/green]" if color == "green" else "[yellow]WARN[/yellow]" if color == "yellow" else "[red]ALERT[/red]" if color == "red" else "[dim]—[/dim]"
        table.add_row(name, val, baseline, status)

    console.print(table)


def show_ab_comparison(logs: list[RequestLog]) -> None:
    """Muestra comparación A/B entre los dos grupos."""
    console.print()
    console.print(Panel(
        "[bold]A/B Test: Sistema A (actual) vs Sistema B (candidato)[/bold]\n\n"
        "Sistema B: nuevo modelo de reranking.\n"
        "Más preciso pero ligeramente más lento.",
        style="blue",
    ))

    group_a = [l for l in logs if l.ab_group == "A"]
    group_b = [l for l in logs if l.ab_group == "B"]

    metrics_a = compute_online_metrics(group_a, "Grupo A")
    metrics_b = compute_online_metrics(group_b, "Grupo B")

    table = Table(title="Comparación A/B", show_lines=True)
    table.add_column("Métrica", style="bold", width=22)
    table.add_column("Grupo A", justify="center", width=12)
    table.add_column("Grupo B", justify="center", width=12)
    table.add_column("Δ", justify="center", width=10)
    table.add_column("Significativo?", justify="center", width=14)

    comparisons = [
        ("Thumbs-up rate", metrics_a.thumbs_up_rate, metrics_b.thumbs_up_rate, True),
        ("Thumbs-down rate", metrics_a.thumbs_down_rate, metrics_b.thumbs_down_rate, False),
        ("Reformulación", metrics_a.reformulation_rate, metrics_b.reformulation_rate, False),
        ("Latencia avg (ms)", metrics_a.avg_latency_ms, metrics_b.avg_latency_ms, False),
        ("Auto-eval mean", metrics_a.auto_eval_mean, metrics_b.auto_eval_mean, True),
    ]

    for name, va, vb, higher_better in comparisons:
        delta = vb - va
        if name.endswith("(ms)"):
            va_str = f"{va:.0f}"
            vb_str = f"{vb:.0f}"
            delta_str = f"{delta:+.0f}"
        else:
            va_str = f"{va:.1%}" if va < 1 else f"{va:.2f}"
            vb_str = f"{vb:.1%}" if vb < 1 else f"{vb:.2f}"
            delta_str = f"{delta:+.1%}" if abs(delta) < 1 else f"{delta:+.2f}"

        # Significancia simplificada (n pequeño, solo indicativo)
        n_a, n_b = len(group_a), len(group_b)
        # Necesitaríamos ~1500/grupo para 5pp; con ~50/grupo, solo diferencias enormes
        sig = "Insuficiente n" if n_a < 200 else ("Sí" if abs(delta) > 0.10 else "No")

        is_better = (delta > 0 and higher_better) or (delta < 0 and not higher_better)
        delta_color = "green" if is_better else "red" if not is_better and abs(delta) > 0.02 else "dim"

        table.add_row(name, va_str, vb_str, f"[{delta_color}]{delta_str}[/{delta_color}]", sig)

    console.print(table)

    console.print(Panel(
        f"[bold yellow]Nota:[/bold yellow]\n\n"
        f"  Con n={len(group_a)} + {len(group_b)} requests, no hay suficientes\n"
        f"  datos para significancia estadística.\n\n"
        f"  Para detectar Δ = 5pp en thumbs-up (baseline ~60%):\n"
        f"  n ≈ 1,507 por grupo (sección 8).\n"
        f"  Con 500 queries/día: ~6 días de A/B test.\n\n"
        f"  Tendencia observable: B tiene mejor thumbs-up y auto-eval,\n"
        f"  pero mayor latencia. Continuar el test.",
        style="yellow",
    ))


def show_feedback_loop(logs: list[RequestLog]) -> None:
    """Muestra candidatos para incorporar al golden dataset."""
    console.print()
    console.print(Panel(
        "[bold]Feedback loop: candidatos para golden dataset[/bold]\n\n"
        "Seleccionar queries problemáticas para anotación experta\n"
        "e incorporación al golden dataset.",
        style="blue",
    ))

    # Candidatos: thumbs-down, reformulaciones, auto-eval bajo
    candidates = []
    for l in logs:
        reasons = []
        if l.feedback == "down":
            reasons.append("thumbs-down")
        if l.reformulated:
            reasons.append("reformulación")
        if l.auto_eval_score is not None and l.auto_eval_score < 0.40:
            reasons.append(f"auto-eval={l.auto_eval_score:.2f}")

        if reasons:
            candidates.append((l, reasons))

    # Ordenar por número de señales (más señales = más prioritario)
    candidates.sort(key=lambda x: len(x[1]), reverse=True)

    table = Table(title=f"Top candidatos para golden dataset ({len(candidates)} encontrados)", show_lines=True)
    table.add_column("#", style="bold", justify="center", width=4)
    table.add_column("Query (redactada)", width=38)
    table.add_column("Señales", width=24)
    table.add_column("Prioridad", justify="center", width=10)

    for i, (l, reasons) in enumerate(candidates[:12]):
        n_signals = len(reasons)
        priority = "[red]Alta[/red]" if n_signals >= 2 else "[yellow]Media[/yellow]"
        table.add_row(
            str(i + 1),
            l.query_redacted[:36] + ".." if len(l.query_redacted) > 38 else l.query_redacted,
            ", ".join(reasons),
            priority,
        )

    console.print(table)

    # Resumen
    high_priority = sum(1 for _, r in candidates if len(r) >= 2)
    console.print(
        f"\n  Total candidatos: {len(candidates)}"
        f"\n  Alta prioridad (2+ señales): {high_priority}"
        f"\n  Recomendación: anotar top 10-20 esta semana"
        f"\n  y agregar al golden dataset con source=production"
    )


def show_distribution_shift(logs: list[RequestLog]) -> None:
    """Detecta queries fuera de dominio (distribution shift)."""
    console.print()
    ood_keywords = ["tipo de cambio", "demanda por pensión", "vacunas"]
    ood_queries = [l for l in logs if any(k in l.query for k in ood_keywords)]
    in_domain = [l for l in logs if not any(k in l.query for k in ood_keywords)]

    ood_pct = len(ood_queries) / len(logs) * 100

    console.print(Panel(
        f"[bold]Detección de distribution shift[/bold]\n\n"
        f"  Queries en dominio:   {len(in_domain)}\n"
        f"  Queries fuera dominio: {len(ood_queries)} ({ood_pct:.1f}%)\n\n"
        f"  {'[red]ALERTA: > 20% fuera de dominio[/red]' if ood_pct > 20 else '[green]Dentro de lo esperado (< 20%)[/green]'}",
        style="blue",
    ))

    if ood_queries:
        table = Table(title="Queries fuera de dominio detectadas")
        table.add_column("Query", width=50)
        table.add_column("Feedback", justify="center", width=10)
        table.add_column("Auto-eval", justify="center", width=10)

        for l in ood_queries[:5]:
            fb = l.feedback or "—"
            ae = f"{l.auto_eval_score:.2f}" if l.auto_eval_score is not None else "—"
            table.add_row(l.query_redacted[:48] + ".." if len(l.query_redacted) > 50 else l.query_redacted, fb, ae)

        console.print(table)
        console.print("  → Acción: verificar si el sistema responde 'fuera de alcance'")


def show_sample_size_calculator() -> None:
    """Muestra calculadora de tamaño de muestra para A/B."""
    console.print()
    console.print(Panel(
        "[bold]Calculadora: tamaño de muestra para A/B[/bold]\n\n"
        "¿Cuántos requests necesitas para detectar una mejora?",
        style="blue",
    ))

    table = Table(title="Tamaño de muestra para A/B test (α=0.05, poder=80%)")
    table.add_column("Baseline\nthumbs-up", style="bold", justify="center", width=12)
    table.add_column("Δ = 3pp", justify="center", width=10)
    table.add_column("Δ = 5pp", justify="center", width=10)
    table.add_column("Δ = 10pp", justify="center", width=10)
    table.add_column("Δ = 15pp", justify="center", width=10)

    for baseline in [0.50, 0.60, 0.70]:
        row = [f"{baseline:.0%}"]
        for delta in [0.03, 0.05, 0.10, 0.15]:
            p = baseline
            # n ≈ 2 * (z_a + z_b)^2 * p(1-p) / delta^2
            n = int(2 * 7.85 * p * (1 - p) / delta**2)
            row.append(f"{n:,}")
        table.add_row(*row)

    console.print(table)
    console.print("  Valores por grupo. Total = 2 × n. Divide por queries/día para obtener duración.\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    console.print(Panel(
        "[bold]Online evals: monitoreo en producción[/bold]\n\n"
        "Simulación de logging, auto-eval, A/B testing\n"
        "y feedback loop para un RAG fiscal en producción.",
        style="bold blue",
    ))

    # 1. Generar tráfico
    console.print(Panel(
        "[bold]Paso 1: Tráfico simulado[/bold]\n\n"
        "100 requests, A/B split 50/50.\n"
        "Incluye queries en dominio y fuera de dominio.",
        style="blue",
    ))

    logs = generate_traffic(n_requests=100, ab_split=0.5)
    console.print(f"  Generados {len(logs)} request logs.\n")

    # 2. Redacción PII
    show_pii_redaction(logs)

    # 3. Auto-eval por sampling
    console.print()
    console.print(Panel(
        "[bold]Paso 2: Auto-eval por sampling[/bold]\n\n"
        "Sample combinado: 5% aleatorio + 100% thumbs-down/reformulaciones.\n"
        "Juez simulado (Haiku) evalúa calidad.",
        style="blue",
    ))

    sampled = auto_eval_sample(logs)
    console.print(f"  Sampled: {len(sampled)}/{len(logs)} requests ({len(sampled)/len(logs):.0%})")
    console.print(f"  Costo estimado (Haiku): ~${len(sampled) * 0.005:.2f}\n")

    # 4. Dashboard
    metrics = compute_online_metrics(logs, "simulación (100 requests)")
    show_online_dashboard(metrics)

    # 5. A/B comparison
    show_ab_comparison(logs)

    # 6. Distribution shift
    show_distribution_shift(logs)

    # 7. Feedback loop
    show_feedback_loop(logs)

    # 8. Calculadora de sample size
    show_sample_size_calculator()


if __name__ == "__main__":
    main()
