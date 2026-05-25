"""LLM-as-judge: demostración de sesgos y protocolo de calibración.

Simula el comportamiento de un LLM-as-judge para demostrar:
  1. Position bias: cómo el orden de presentación afecta el juicio
  2. Verbosity bias: preferencia por respuestas más largas
  3. Protocolo de mitigación: swap + promedio
  4. Calibración: correlación juez-humano
  5. Multi-judge: reducción de varianza

Ejecutar con:
    uv run python 01-evals/code/eval-judge-sesgos.py

No requiere API keys — simula los sesgos con distribuciones probabilísticas
para ilustrar los conceptos. Con API keys reales, se podría demostrar
el sesgo empíricamente.
"""

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
class JudgmentPair:
    """Par de respuestas evaluadas por un juez."""
    query: str
    response_a: str
    response_b: str
    true_better: str        # cuál es realmente mejor (A, B, o tie)
    judge_ab: str           # juicio con A primero
    judge_ba: str           # juicio con B primero (swap)
    consistent: bool        # ¿el juez dio el mismo veredicto?


@dataclass
class PointwiseJudgment:
    """Evaluación pointwise de una respuesta."""
    query: str
    response: str
    human_score: int        # 1-5 dado por humano
    judge_score: int        # 1-5 dado por LLM
    response_length: int    # en palabras


# ---------------------------------------------------------------------------
# Simulación de position bias
# ---------------------------------------------------------------------------

def simulate_position_bias(n_pairs: int = 20) -> list[JudgmentPair]:
    """Simula evaluaciones pareadas con position bias.

    El juez tiene un position bias del 15%: cuando las respuestas son
    similares en calidad, prefiere la que ve primero con P=0.575
    en lugar de P=0.5.
    """
    queries_and_responses = [
        ("¿Cuál es la tasa de IVA digital?",
         "La tasa es del 19% según la Circular 42 del SII.",
         "Conforme a la Circular Nº 42, la tasa de IVA aplicable es del 19%.",
         "tie"),
        ("¿Quiénes son sujetos pasivos de la Ley de Lobby?",
         "Presidente, ministros, subsecretarios, jefes de servicio, directores regionales, intendentes, gobernadores, embajadores, consejeros, diputados, senadores, alcaldes y concejales (Art. 3).",
         "Según el artículo 3 de la Ley 20.730, los sujetos pasivos incluyen al Presidente de la República, ministros de Estado, y otras autoridades listadas en la norma.",
         "A"),
        ("¿Qué presupuesto tiene inmunizaciones?",
         "$198.547.320 miles, según Glosa 05.",
         "El presupuesto asignado al Programa Nacional de Inmunizaciones para el año 2024 alcanza la suma de $198.547.320 miles de pesos.",
         "tie"),
        ("¿Multa por infracción a la Ley de Lobby?",
         "10 a 50 UTM, hasta 100 en reincidencia (Art. 11).",
         "Las multas van de 10 a 50 unidades tributarias mensuales. En caso de reincidencia, pueden llegar a 100 UTM.",
         "tie"),
        ("¿Criterios para alumno prioritario?",
         "Chile Solidario, tramo A FONASA, tercil vulnerable FPS.",
         "Los tres criterios en orden de prelación son: (1) pertenecer a Chile Solidario, (2) tramo A del FONASA, (3) tercil más vulnerable según la Ficha de Protección Social. Estos criterios son evaluados anualmente por la JUNAEB conforme al Artículo 2 del Decreto Exento Nº 1.423.",
         "B"),
    ]

    pairs = []
    # Repetir queries para tener n_pairs
    for i in range(n_pairs):
        q, ra, rb, true_better = queries_and_responses[i % len(queries_and_responses)]

        # Simular position bias: 57.5% de preferencia por el primero en ties
        position_bias = 0.575

        if true_better == "tie":
            # En empates, el sesgo posicional decide
            judge_ab = "A" if random.random() < position_bias else "B"
            judge_ba = "B" if random.random() < position_bias else "A"
        elif true_better == "A":
            # A es realmente mejor; el juez lo nota 85% del tiempo
            judge_ab = "A" if random.random() < 0.85 else "B"
            judge_ba = "A" if random.random() < 0.70 else "B"  # Menos claro cuando A va segundo
        else:  # B is better
            judge_ab = "B" if random.random() < 0.70 else "A"
            judge_ba = "B" if random.random() < 0.85 else "A"

        consistent = judge_ab == judge_ba
        pairs.append(JudgmentPair(q, ra, rb, true_better, judge_ab, judge_ba, consistent))

    return pairs


def show_position_bias(pairs: list[JudgmentPair]) -> None:
    """Muestra el análisis de position bias."""
    console.print(Panel(
        "[bold]Sesgo 1: Position bias[/bold]\n\n"
        "El juez evalúa 20 pares en ambos órdenes (A,B) y (B,A).\n"
        "Si no hay position bias, el juicio debería ser idéntico.",
        style="blue",
    ))

    table = Table(title="Evaluaciones pareadas con position bias", show_lines=True)
    table.add_column("#", width=3)
    table.add_column("Query", width=30)
    table.add_column("Real", justify="center", width=6)
    table.add_column("Juicio\n(A,B)", justify="center", width=7)
    table.add_column("Juicio\n(B,A)", justify="center", width=7)
    table.add_column("Consistente", justify="center", width=11)

    for i, p in enumerate(pairs[:10], 1):  # Mostrar primeros 10
        c_color = "green" if p.consistent else "red"
        table.add_row(
            str(i),
            p.query[:28] + ".." if len(p.query) > 30 else p.query,
            p.true_better,
            p.judge_ab,
            p.judge_ba,
            f"[{c_color}]{'Sí' if p.consistent else 'No'}[/{c_color}]",
        )

    console.print(table)

    # Estadísticas
    n = len(pairs)
    consistent = sum(1 for p in pairs if p.consistent)
    first_preferred = sum(1 for p in pairs if p.judge_ab == "A") + \
                      sum(1 for p in pairs if p.judge_ba == "B")
    total_judgments = n * 2

    # Accuracy del juez
    correct_ab = sum(1 for p in pairs if p.judge_ab == p.true_better or p.true_better == "tie")
    correct_ba = sum(1 for p in pairs if p.judge_ba == p.true_better or p.true_better == "tie")

    console.print()
    console.print(Panel(
        f"[bold]Resultados ({n} pares):[/bold]\n\n"
        f"  Consistencia (mismo juicio en ambos órdenes): "
        f"{consistent}/{n} = {consistent/n:.0%}\n"
        f"  Preferencia por el primero: "
        f"{first_preferred}/{total_judgments} = {first_preferred/total_judgments:.0%}\n"
        f"  (esperado sin sesgo: 50%)\n\n"
        f"[bold]Mitigación:[/bold]\n"
        f"  Solo usar juicios consistentes: {consistent} pares de {n}\n"
        f"  Descartar inconsistentes: {n - consistent} pares (revisión humana)",
        style="yellow",
    ))


# ---------------------------------------------------------------------------
# Simulación de verbosity bias
# ---------------------------------------------------------------------------

def simulate_verbosity_bias(n: int = 15) -> list[PointwiseJudgment]:
    """Simula evaluaciones pointwise con verbosity bias.

    El juez agrega +0.3 a +0.8 por cada 50 palabras adicionales
    sobre un baseline de 20 palabras.
    """
    cases = [
        # (query, response, human_score, word_count)
        ("¿Tasa de IVA digital?",
         "19% (Circular 42).", 5, 5),
        ("¿Tasa de IVA digital?",
         "La tasa de IVA aplicable a servicios digitales prestados por proveedores no domiciliados en Chile es del 19%, según lo establecido en la Circular Nº 42 del SII de 2020.",
         5, 30),
        ("¿Tasa de IVA digital?",
         "Conforme a las modificaciones introducidas por la Ley Nº 21.210 al Decreto Ley Nº 825, la tasa del Impuesto al Valor Agregado aplicable a los servicios prestados por medios electrónicos por parte de prestadores domiciliados o residentes en el extranjero, cuando dichos servicios sean utilizados en el territorio nacional chileno, corresponde a un porcentaje del diecinueve por ciento, tal como fue instruido en la Circular Nº 42 del año 2020 emanada del Servicio de Impuestos Internos.",
         4, 80),  # Humano le baja por verbosidad innecesaria
        ("¿Multa Ley de Lobby?",
         "10-50 UTM, reincidencia hasta 100 UTM.", 5, 8),
        ("¿Multa Ley de Lobby?",
         "Las infracciones a la Ley 20.730 se sancionan con multas entre 10 y 50 unidades tributarias mensuales. En reincidencia, puede elevarse hasta 100 UTM.", 5, 25),
        ("¿Multa Ley de Lobby?",
         "De acuerdo con las disposiciones contempladas en el Artículo 11 de la Ley Nº 20.730, que regula el lobby y las gestiones que representen intereses particulares ante las autoridades y funcionarios públicos, las infracciones serán sancionadas con una multa cuyo rango oscila entre las 10 y las 50 unidades tributarias mensuales, pudiendo elevarse hasta las 100 UTM en caso de reincidencia del infractor.",
         4, 60),
        ("¿Presupuesto inmunizaciones?",
         "$198.547.320 miles.", 5, 3),
        ("¿Presupuesto inmunizaciones?",
         "El presupuesto asignado al Programa Nacional de Inmunizaciones en 2024 es de $198.547.320 miles, según la Glosa 05 de la Partida 16.", 5, 22),
        ("¿Criterios alumno prioritario?",
         "Chile Solidario, FONASA tramo A, FPS tercil vulnerable.", 4, 8),
        ("¿Criterios alumno prioritario?",
         "JUNAEB determina anualmente usando: (1) Chile Solidario, (2) tramo A FONASA, (3) tercil más vulnerable FPS. En orden de prelación.", 5, 20),
        ("¿Criterios alumno prioritario?",
         "La Junta Nacional de Auxilio Escolar y Becas, conocida por su sigla JUNAEB, es el organismo encargado de determinar, con periodicidad anual, la condición de alumno prioritario. Para ello, emplea tres criterios que se aplican en estricto orden de prelación, a saber: primero, la pertenencia al Sistema de Protección Social denominado Chile Solidario; segundo, la clasificación en el tramo A del Fondo Nacional de Salud; y tercero, la pertenencia al tercil más vulnerable según la Ficha de Protección Social.",
         4, 75),
    ]

    judgments = []
    for query, response, human_score, word_count in cases:
        # Simular verbosity bias: el juez agrega score por longitud
        base_score = human_score
        verbosity_bonus = min(1.5, (word_count - 15) * 0.02)  # +0.02 por palabra extra
        judge_raw = base_score + verbosity_bonus + random.uniform(-0.3, 0.3)
        judge_score = max(1, min(5, round(judge_raw)))

        judgments.append(PointwiseJudgment(
            query, response[:60] + "..." if len(response) > 60 else response,
            human_score, judge_score, word_count,
        ))

    return judgments


def show_verbosity_bias(judgments: list[PointwiseJudgment]) -> None:
    """Muestra la correlación entre longitud y sesgo del juez."""
    console.print()
    console.print(Panel(
        "[bold]Sesgo 2: Verbosity bias[/bold]\n\n"
        "Misma pregunta, respuestas de distinta longitud.\n"
        "El humano puntúa por calidad; el juez infla respuestas largas.",
        style="blue",
    ))

    table = Table(title="Verbosity bias: humano vs juez", show_lines=True)
    table.add_column("Query", width=22)
    table.add_column("Respuesta", width=25)
    table.add_column("Palabras", justify="center", width=9)
    table.add_column("Humano", justify="center", width=7)
    table.add_column("Juez", justify="center", width=6)
    table.add_column("Δ", justify="center", width=5)

    for j in judgments:
        delta = j.judge_score - j.human_score
        d_color = "green" if delta == 0 else "yellow" if delta > 0 else "red"
        d_str = f"+{delta}" if delta > 0 else str(delta)

        table.add_row(
            j.query[:20] + ".." if len(j.query) > 22 else j.query,
            j.response[:23] + ".." if len(j.response) > 25 else j.response,
            str(j.response_length),
            str(j.human_score),
            str(j.judge_score),
            f"[{d_color}]{d_str}[/{d_color}]",
        )

    # Correlación longitud-delta
    deltas = [j.judge_score - j.human_score for j in judgments]
    lengths = [j.response_length for j in judgments]
    avg_delta_short = sum(d for d, l in zip(deltas, lengths) if l <= 20) / max(1, sum(1 for l in lengths if l <= 20))
    avg_delta_long = sum(d for d, l in zip(deltas, lengths) if l > 40) / max(1, sum(1 for l in lengths if l > 40))

    console.print()
    console.print(Panel(
        f"[bold]Patrón detectado:[/bold]\n\n"
        f"  Δ promedio (respuestas ≤20 palabras): {avg_delta_short:+.1f}\n"
        f"  Δ promedio (respuestas >40 palabras):  {avg_delta_long:+.1f}\n\n"
        f"El juez infla sistemáticamente las respuestas verbosas.\n"
        f"Un humano experto en dominio fiscal prefiere respuestas concisas\n"
        f"con cita específica sobre explicaciones largas redundantes.",
        style="yellow",
    ))


# ---------------------------------------------------------------------------
# Simulación de calibración
# ---------------------------------------------------------------------------

def simulate_calibration(n: int = 30) -> list[PointwiseJudgment]:
    """Simula un dataset de calibración humano vs juez."""
    judgments = []
    for i in range(n):
        human = random.choice([2, 3, 3, 4, 4, 4, 5, 5])
        # Juez correlaciona pero con noise y leniency
        noise = random.gauss(0, 0.7)
        leniency = 0.4  # sesgo positivo sistemático
        judge_raw = human + leniency + noise
        judge = max(1, min(5, round(judge_raw)))

        judgments.append(PointwiseJudgment(
            f"Query {i+1:03d}", f"Respuesta {i+1:03d}",
            human, judge, random.randint(10, 80),
        ))
    return judgments


def show_calibration(judgments: list[PointwiseJudgment]) -> None:
    """Muestra análisis de calibración juez-humano."""
    console.print()
    console.print(Panel(
        "[bold]Calibración: correlación juez-humano[/bold]\n\n"
        f"Dataset de calibración: {len(judgments)} evaluaciones\n"
        "Mismas respuestas evaluadas por humano experto y LLM-judge.",
        style="blue",
    ))

    # Calcular métricas de correlación
    n = len(judgments)
    human_scores = [j.human_score for j in judgments]
    judge_scores = [j.judge_score for j in judgments]

    # Pearson's r (simplificado)
    h_mean = sum(human_scores) / n
    j_mean = sum(judge_scores) / n
    num = sum((h - h_mean) * (j - j_mean) for h, j in zip(human_scores, judge_scores))
    den_h = sum((h - h_mean) ** 2 for h in human_scores) ** 0.5
    den_j = sum((j - j_mean) ** 2 for j in judge_scores) ** 0.5
    pearson_r = num / (den_h * den_j) if den_h * den_j > 0 else 0

    # Acuerdo exacto
    exact_agree = sum(1 for h, j in zip(human_scores, judge_scores) if h == j)

    # Acuerdo ±1
    close_agree = sum(1 for h, j in zip(human_scores, judge_scores) if abs(h - j) <= 1)

    # Leniency
    leniency = j_mean - h_mean

    # Confusion matrix simplificada
    console.print()
    table = Table(title="Distribución de scores")
    table.add_column("", style="bold")
    table.add_column("Humano (media)", justify="center")
    table.add_column("Juez (media)", justify="center")
    table.add_column("Diferencia", justify="center")

    table.add_row("Media", f"{h_mean:.2f}", f"{j_mean:.2f}",
                   f"[yellow]{leniency:+.2f}[/yellow] (leniency)")

    h_std = (sum((h - h_mean) ** 2 for h in human_scores) / n) ** 0.5
    j_std = (sum((j - j_mean) ** 2 for j in judge_scores) / n) ** 0.5
    table.add_row("Desv. est.", f"{h_std:.2f}", f"{j_std:.2f}", "")

    console.print(table)

    # Métricas de acuerdo
    console.print()
    table2 = Table(title="Métricas de calibración")
    table2.add_column("Métrica", style="bold", width=25)
    table2.add_column("Valor", justify="center", width=10)
    table2.add_column("Umbral", justify="center", width=10)
    table2.add_column("Estado", justify="center", width=10)

    def check(val: float, threshold: float) -> str:
        return "[green]OK[/green]" if val >= threshold else "[red]BAJO[/red]"

    table2.add_row("Pearson's r", f"{pearson_r:.3f}", "≥0.70", check(pearson_r, 0.70))
    table2.add_row("Acuerdo exacto", f"{exact_agree/n:.0%}", "≥40%", check(exact_agree/n, 0.40))
    table2.add_row("Acuerdo ±1", f"{close_agree/n:.0%}", "≥80%", check(close_agree/n, 0.80))
    table2.add_row("Leniency bias", f"{leniency:+.2f}", "<±0.5",
                   "[green]OK[/green]" if abs(leniency) < 0.5 else "[yellow]SESGO[/yellow]")

    console.print(table2)

    # Interpretación
    console.print()
    console.print(Panel(
        f"[bold]Interpretación:[/bold]\n\n"
        f"Pearson's r = {pearson_r:.3f}: "
        f"{'correlación aceptable' if pearson_r >= 0.7 else 'correlación insuficiente — revisar rubric'}.\n\n"
        f"Leniency = {leniency:+.2f}: el juez es "
        f"{'levemente generoso' if leniency > 0 else 'levemente estricto'}.\n"
        f"{'Corregible restando el offset al score.' if abs(leniency) < 0.8 else 'Offset grande — revisar rubric.'}\n\n"
        f"Acuerdo ±1 = {close_agree/n:.0%}: "
        f"{'aceptable para uso en CI/pre-release' if close_agree/n >= 0.8 else 'necesita mejora antes de confiar'}.",
        style="yellow",
    ))


# ---------------------------------------------------------------------------
# Protocolo de mitigación completo
# ---------------------------------------------------------------------------

def show_mitigation_protocol() -> None:
    """Muestra el protocolo completo de mitigación de sesgos."""
    console.print()
    table = Table(title="Protocolo de mitigación de sesgos")
    table.add_column("Sesgo", style="bold", width=18)
    table.add_column("Mitigación", width=30)
    table.add_column("Costo extra", justify="center", width=12)
    table.add_column("Efectividad", justify="center", width=12)

    table.add_row(
        "Position bias",
        "Swap (evaluar en ambos órdenes) + descartar inconsistentes",
        "2x calls",
        "[green]Alta[/green]",
    )
    table.add_row(
        "Verbosity bias",
        "Rubric con anchors explícitos + penalizar redundancia",
        "~0 (cambio de prompt)",
        "[yellow]Media[/yellow]",
    )
    table.add_row(
        "Self-preference",
        "Usar juez de modelo distinto al generador",
        "~0 (cambio de modelo)",
        "[green]Alta[/green]",
    )
    table.add_row(
        "Anchoring",
        "Rotar orden de criterios o evaluar cada uno por separado",
        "Nx calls (N criterios)",
        "[yellow]Media[/yellow]",
    )
    table.add_row(
        "Leniency",
        "Anchors explícitos por nivel + calibración contra humano",
        "Calibración inicial",
        "[yellow]Media[/yellow]",
    )
    table.add_row(
        "Todos",
        "Multi-judge (2-3 jueces) + escalar desacuerdos a humano",
        "2-3x calls",
        "[green]Alta[/green]",
    )

    console.print(table)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    console.print(Panel(
        "[bold]LLM-as-judge: sesgos y calibración[/bold]\n\n"
        "Demostración de los sesgos principales y protocolos\n"
        "de mitigación para evaluación con LLMs como jueces.",
        style="bold blue",
    ))

    # 1. Position bias
    pairs = simulate_position_bias(n_pairs=20)
    show_position_bias(pairs)

    # 2. Verbosity bias
    judgments = simulate_verbosity_bias()
    show_verbosity_bias(judgments)

    # 3. Calibración
    cal_judgments = simulate_calibration(n=30)
    show_calibration(cal_judgments)

    # 4. Protocolo de mitigación
    show_mitigation_protocol()


if __name__ == "__main__":
    main()
