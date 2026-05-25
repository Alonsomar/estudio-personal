"""Métricas de generación: Faithfulness, Answer Relevance y diagnóstico.

Implementa evaluación de generación para un RAG fiscal:
  1. Faithfulness: extracción de claims y verificación contra contexto
  2. Answer Relevance: evaluación directa query-respuesta
  3. ROUGE-L como baseline heurístico
  4. Cuadrante de diagnóstico combinando métricas
  5. Tabla de diagnóstico cruzado retrieval × generación

Ejecutar con:
    uv run python 01-evals/code/eval-metricas-generacion.py

No requiere API keys — simula el juicio del LLM para ilustrar el proceso.
Para una versión con LLM real, ver la nota al final del script.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from shared.utils import get_logger, get_project_root

log = get_logger(__name__)
console = Console()


# ---------------------------------------------------------------------------
# Modelo de datos
# ---------------------------------------------------------------------------

@dataclass
class Claim:
    """Una afirmación atómica extraída de la respuesta."""
    text: str
    supported: bool
    evidence: str  # fragmento del contexto que lo soporta (o explicación si no)


@dataclass
class GenerationEval:
    """Evaluación completa de la generación para una query."""
    query_id: str
    query: str
    context: str               # texto recuperado por el retriever
    generated_answer: str
    gold_answer: str

    # Métricas
    claims: list[Claim]
    faithfulness: float        # claims soportados / total claims
    answer_relevance: float    # 0-1, qué tan bien responde la pregunta
    rouge_l: float             # ROUGE-L F1 score
    recall_at_k: float        # del retrieval (para diagnóstico cruzado)

    # Diagnóstico
    quadrant: str              # Q1-Q4


# ---------------------------------------------------------------------------
# ROUGE-L desde cero
# ---------------------------------------------------------------------------

def _lcs_length(x: list[str], y: list[str]) -> int:
    """Longest Common Subsequence length (programación dinámica)."""
    m, n = len(x), len(y)
    table = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if x[i - 1] == y[j - 1]:
                table[i][j] = table[i - 1][j - 1] + 1
            else:
                table[i][j] = max(table[i - 1][j], table[i][j - 1])
    return table[m][n]


def rouge_l_f1(reference: str, hypothesis: str) -> float:
    """Calcula ROUGE-L F1 score entre referencia e hipótesis.

    ROUGE-L usa el Longest Common Subsequence (LCS) para medir
    la similitud entre dos textos.

    Returns:
        F1 score entre 0 y 1.
    """
    ref_tokens = reference.lower().split()
    hyp_tokens = hypothesis.lower().split()

    if not ref_tokens or not hyp_tokens:
        return 0.0

    lcs = _lcs_length(ref_tokens, hyp_tokens)

    precision = lcs / len(hyp_tokens) if hyp_tokens else 0
    recall = lcs / len(ref_tokens) if ref_tokens else 0

    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


# ---------------------------------------------------------------------------
# Dataset de evaluación simulado
# ---------------------------------------------------------------------------

def build_generation_evals() -> list[GenerationEval]:
    """Construye evaluaciones de generación simuladas.

    Cada caso ilustra un patrón diferente del cuadrante de diagnóstico.
    Los claims y scores están pre-anotados para simular lo que
    haría un LLM-as-judge.
    """
    evals = [
        # --- Q1: Ideal — fiel y relevante ---
        GenerationEval(
            query_id="ge-001",
            query="¿Cuál es la tasa de IVA para servicios digitales de proveedores extranjeros?",
            context="Quedan afectos a IVA, con tasa del 19%, los siguientes servicios cuando sean prestados de manera remota por proveedores extranjeros: intermediación, contenido digital, software/cloud, publicidad.",
            generated_answer="La tasa de IVA aplicable a los servicios digitales prestados por proveedores extranjeros en Chile es del 19%, según la Circular Nº 42 del SII.",
            gold_answer="19%, según Circular Nº 42 del SII (2020).",
            claims=[
                Claim("La tasa de IVA es del 19%", True, "'tasa del 19%' en contexto"),
                Claim("Aplica a servicios digitales de proveedores extranjeros", True, "'proveedores extranjeros' en contexto"),
                Claim("Según la Circular Nº 42 del SII", True, "Circular 42 es la fuente del contexto"),
            ],
            faithfulness=1.0,
            answer_relevance=0.95,
            rouge_l=0.0,  # se calculará
            recall_at_k=1.0,
            quadrant="Q1",
        ),

        # --- Q2: Hallucination relevante — responde pero inventa ---
        GenerationEval(
            query_id="ge-002",
            query="¿Cada cuánto deben declarar IVA los prestadores de servicios digitales extranjeros?",
            context="Declarar y pagar trimestralmente el IVA devengado, dentro de los primeros 20 días del mes siguiente al término del trimestre respectivo.",
            generated_answer="Los prestadores de servicios digitales extranjeros deben declarar y pagar IVA mensualmente, dentro de los primeros 15 días hábiles de cada mes.",
            gold_answer="Trimestralmente, dentro de los primeros 20 días del mes siguiente al término del trimestre.",
            claims=[
                Claim("Deben declarar y pagar IVA mensualmente", False, "El contexto dice 'trimestralmente', no mensualmente"),
                Claim("Dentro de los primeros 15 días hábiles", False, "El contexto dice '20 días', no '15 días hábiles'"),
            ],
            faithfulness=0.0,
            answer_relevance=0.90,
            rouge_l=0.0,
            recall_at_k=1.0,
            quadrant="Q2",
        ),

        # --- Q4: Fiel pero off-topic ---
        GenerationEval(
            query_id="ge-003",
            query="¿Cuál es la tasa de IVA para servicios digitales?",
            context="La Ley Nº 21.210, publicada en el Diario Oficial el 24 de febrero de 2020, introdujo modificaciones al Decreto Ley Nº 825, estableciendo la obligación de gravar con IVA los servicios prestados por medio electrónico. Quedan afectos a IVA, con tasa del 19%.",
            generated_answer="La Ley Nº 21.210 fue publicada en el Diario Oficial el 24 de febrero de 2020 e introdujo modificaciones al Decreto Ley Nº 825 de 1974 sobre impuestos a las ventas y servicios.",
            gold_answer="19%, según Circular Nº 42 del SII (2020).",
            claims=[
                Claim("La Ley 21.210 fue publicada el 24 de febrero de 2020", True, "Fecha explícita en contexto"),
                Claim("Modificó el DL 825 de 1974", True, "'Decreto Ley Nº 825' en contexto"),
                Claim("Sobre impuestos a las ventas y servicios", True, "Implícito en el contexto"),
            ],
            faithfulness=1.0,
            answer_relevance=0.25,
            rouge_l=0.0,
            recall_at_k=1.0,
            quadrant="Q4",
        ),

        # --- Q3: Basura — ni fiel ni relevante ---
        GenerationEval(
            query_id="ge-004",
            query="¿Cuánto presupuesto se asigna al Programa de Inmunizaciones?",
            context="Los sujetos pasivos deberán registrar en el Registro de Agenda Pública las audiencias y reuniones sostenidas que tengan por objeto el lobby.",
            generated_answer="El presupuesto asignado al Programa de Inmunizaciones es de aproximadamente $150.000.000 miles, destinados a vacunas del calendario nacional.",
            gold_answer="$198.547.320 miles, según Glosa 05 de la Partida 16.",
            claims=[
                Claim("El presupuesto es de $150.000.000 miles", False, "El contexto no habla de presupuestos — es sobre lobby"),
                Claim("Destinados a vacunas del calendario nacional", False, "No hay mención de vacunas en el contexto"),
            ],
            faithfulness=0.0,
            answer_relevance=0.80,
            rouge_l=0.0,
            recall_at_k=0.0,  # retrieval miss
            quadrant="Q3",
        ),

        # --- Q1 con matices: parcialmente fiel ---
        GenerationEval(
            query_id="ge-005",
            query="¿Qué criterios usa JUNAEB para determinar alumnos prioritarios?",
            context="La calidad de alumno prioritario será determinada anualmente por la JUNAEB, considerando: a) Pertenecer al Sistema de Protección Social Chile Solidario. b) Estar clasificado en el tramo A del FONASA. c) Pertenecer al tercil más vulnerable según la Ficha de Protección Social.",
            generated_answer="JUNAEB determina anualmente la calidad de alumno prioritario usando tres criterios en orden de prelación: pertenecer a Chile Solidario, estar en el tramo A de FONASA, o pertenecer al tercil más vulnerable de la Ficha de Protección Social.",
            gold_answer="Chile Solidario, tramo A FONASA, tercil más vulnerable FPS (Art. 2, en orden de prelación).",
            claims=[
                Claim("JUNAEB determina anualmente", True, "'determinada anualmente por la JUNAEB'"),
                Claim("Tres criterios en orden de prelación", True, "'en orden de prelación' implícito en la enumeración"),
                Claim("Pertenecer a Chile Solidario", True, "Literal a) del contexto"),
                Claim("Estar en tramo A de FONASA", True, "Literal b) del contexto"),
                Claim("Tercil más vulnerable de la FPS", True, "Literal c) del contexto"),
            ],
            faithfulness=1.0,
            answer_relevance=0.98,
            rouge_l=0.0,
            recall_at_k=1.0,
            quadrant="Q1",
        ),

        # --- Hallucination normativa: cita artículo que no existe ---
        GenerationEval(
            query_id="ge-006",
            query="¿Qué pasa con establecimientos En Recuperación que no mejoran?",
            context="Los establecimientos clasificados En Recuperación que no logren mejorar sus resultados dentro de un plazo de cuatro años perderán el reconocimiento oficial del Estado.",
            generated_answer="Según el Artículo 45 bis de la Ley SEP, los establecimientos En Recuperación que no mejoren en cuatro años perderán el reconocimiento oficial y deberán devolver las subvenciones recibidas durante ese período.",
            gold_answer="Pierden el reconocimiento oficial del Estado en 4 años (Art. 5 del Decreto).",
            claims=[
                Claim("Establecimientos En Recuperación que no mejoren en 4 años", True, "Explícito en contexto"),
                Claim("Perderán el reconocimiento oficial", True, "Explícito en contexto"),
                Claim("Según el Artículo 45 bis de la Ley SEP", False, "El contexto no menciona 'Art. 45 bis' — hallucination normativa"),
                Claim("Deberán devolver las subvenciones recibidas", False, "No mencionado en el contexto — invención"),
            ],
            faithfulness=0.5,
            answer_relevance=0.90,
            rouge_l=0.0,
            recall_at_k=1.0,
            quadrant="Q2",
        ),

        # --- Abstención correcta (faithfulness alta por no afirmar nada falso) ---
        GenerationEval(
            query_id="ge-007",
            query="¿Cuál es la multa por infracción a la Ley de Transparencia de 2022?",
            context="Las infracciones a las obligaciones establecidas en esta ley serán sancionadas con multa de 10 a 50 UTM. En caso de reincidencia, hasta 100 UTM.",
            generated_answer="No puedo responder con certeza. El contexto disponible describe multas de la Ley de Lobby (10-50 UTM), pero la pregunta es sobre la Ley de Transparencia, que es una norma distinta no incluida en los documentos disponibles.",
            gold_answer="Fuera de scope — la pregunta mezcla leyes distintas.",
            claims=[
                Claim("El contexto describe multas de la Ley de Lobby", True, "El contexto es efectivamente de la Ley de Lobby"),
                Claim("Las multas son de 10-50 UTM", True, "Explícito en contexto"),
                Claim("La Ley de Transparencia es una norma distinta", True, "Correcto — son leyes diferentes"),
                Claim("No está incluida en los documentos disponibles", True, "Correcto para el scope del corpus"),
            ],
            faithfulness=1.0,
            answer_relevance=0.85,
            rouge_l=0.0,
            recall_at_k=0.0,
            quadrant="Q1",
        ),

        # --- Omisión material: correcto pero incompleto ---
        GenerationEval(
            query_id="ge-008",
            query="¿Quiénes deben pagar IVA por servicios digitales en Chile?",
            context="Estableciendo la obligación de gravar con IVA los servicios prestados por medio electrónico por parte de prestadores domiciliados o residentes en el extranjero, cuando sean utilizados en el territorio nacional.",
            generated_answer="Los proveedores de servicios digitales deben pagar IVA del 19% en Chile.",
            gold_answer="Solo proveedores NO domiciliados ni residentes en Chile, cuando los servicios sean utilizados en territorio nacional.",
            claims=[
                Claim("Los proveedores de servicios digitales deben pagar IVA", False, "Omite la condición clave: solo proveedores EXTRANJEROS. El claim es misleading por omisión."),
                Claim("La tasa es del 19%", False, "El contexto de este fragmento no menciona la tasa — viene de otra sección"),
            ],
            faithfulness=0.0,
            answer_relevance=0.70,
            rouge_l=0.0,
            recall_at_k=1.0,
            quadrant="Q2",
        ),
    ]

    # Calcular ROUGE-L para todos
    for ev in evals:
        ev.rouge_l = rouge_l_f1(ev.gold_answer, ev.generated_answer)

    return evals


# ---------------------------------------------------------------------------
# Visualización
# ---------------------------------------------------------------------------

def show_faithfulness_walkthrough(ev: GenerationEval) -> None:
    """Muestra el proceso de evaluación de faithfulness paso a paso."""
    console.print(Panel(
        f"[bold]Faithfulness walkthrough[/bold]\n"
        f"Query: {ev.query}\n"
        f"ID: {ev.query_id}",
        style="blue",
    ))

    console.print(f"\n[dim]Contexto:[/dim] {ev.context[:120]}...")
    console.print(f"[dim]Respuesta:[/dim] {ev.generated_answer[:120]}...")

    table = Table(title="Extracción y verificación de claims")
    table.add_column("#", width=3)
    table.add_column("Claim", width=40)
    table.add_column("¿Soportado?", justify="center", width=12)
    table.add_column("Evidencia", width=35)

    for i, claim in enumerate(ev.claims, 1):
        color = "green" if claim.supported else "red"
        symbol = "✅" if claim.supported else "❌"
        table.add_row(
            str(i),
            claim.text,
            f"[{color}]{symbol}[/{color}]",
            claim.evidence[:33] + ".." if len(claim.evidence) > 35 else claim.evidence,
        )

    supported = sum(1 for c in ev.claims if c.supported)
    total = len(ev.claims)
    table.add_section()
    table.add_row("", f"[bold]Faithfulness = {supported}/{total}[/bold]",
                   f"[bold]{ev.faithfulness:.2f}[/bold]", "")

    console.print(table)


def show_all_evals(evals: list[GenerationEval]) -> None:
    """Tabla resumen de todas las evaluaciones."""
    console.print()
    table = Table(title="Métricas de generación por query", show_lines=True)
    table.add_column("ID", style="bold", width=7)
    table.add_column("Query", width=30)
    table.add_column("Faith.", justify="center", width=7)
    table.add_column("Relev.", justify="center", width=7)
    table.add_column("ROUGE-L", justify="center", width=8)
    table.add_column("Recall", justify="center", width=7)
    table.add_column("Quad.", justify="center", width=5)
    table.add_column("Diagnóstico", width=25)

    quadrant_diagnoses = {
        "Q1": "Ideal",
        "Q2": "Hallucination relevante",
        "Q3": "Todo roto",
        "Q4": "Fiel pero off-topic",
    }

    for ev in evals:
        f_color = "green" if ev.faithfulness >= 0.8 else "yellow" if ev.faithfulness > 0.3 else "red"
        r_color = "green" if ev.answer_relevance >= 0.8 else "yellow" if ev.answer_relevance > 0.3 else "red"
        q_color = "green" if ev.quadrant == "Q1" else "yellow" if ev.quadrant == "Q4" else "red"

        table.add_row(
            ev.query_id,
            ev.query[:28] + ".." if len(ev.query) > 30 else ev.query,
            f"[{f_color}]{ev.faithfulness:.2f}[/{f_color}]",
            f"[{r_color}]{ev.answer_relevance:.2f}[/{r_color}]",
            f"{ev.rouge_l:.2f}",
            f"{ev.recall_at_k:.2f}",
            f"[{q_color}]{ev.quadrant}[/{q_color}]",
            quadrant_diagnoses.get(ev.quadrant, "?"),
        )

    # Promedios
    n = len(evals)
    avg_f = sum(e.faithfulness for e in evals) / n
    avg_r = sum(e.answer_relevance for e in evals) / n
    avg_rouge = sum(e.rouge_l for e in evals) / n
    avg_recall = sum(e.recall_at_k for e in evals) / n

    table.add_section()
    table.add_row(
        "", "[bold]Promedio[/bold]",
        f"[bold]{avg_f:.2f}[/bold]",
        f"[bold]{avg_r:.2f}[/bold]",
        f"[bold]{avg_rouge:.2f}[/bold]",
        f"[bold]{avg_recall:.2f}[/bold]",
        "", "",
    )

    console.print(table)


def show_quadrant_distribution(evals: list[GenerationEval]) -> None:
    """Distribución de queries por cuadrante de diagnóstico."""
    console.print()
    counts = {"Q1": 0, "Q2": 0, "Q3": 0, "Q4": 0}
    for ev in evals:
        counts[ev.quadrant] += 1

    total = len(evals)

    table = Table(title="Distribución por cuadrante de diagnóstico")
    table.add_column("Cuadrante", style="bold", width=5)
    table.add_column("Descripción", width=30)
    table.add_column("Cuenta", justify="center", width=8)
    table.add_column("Proporción", justify="center", width=10)
    table.add_column("Acción", width=30)

    quadrant_info = {
        "Q1": ("Ideal: fiel + relevante", "green", "Ship"),
        "Q2": ("Hallucination relevante", "red", "Mejorar grounding / prompt"),
        "Q3": ("Ni fiel ni relevante", "red", "Revisar pipeline completo"),
        "Q4": ("Fiel pero off-topic", "yellow", "Mejorar prompt de generación"),
    }

    for q in ["Q1", "Q2", "Q3", "Q4"]:
        desc, color, action = quadrant_info[q]
        bar = "█" * int(counts[q] / total * 20) if total > 0 else ""
        table.add_row(
            f"[{color}]{q}[/{color}]",
            desc,
            str(counts[q]),
            f"{counts[q]/total:.0%}",
            action,
        )

    console.print(table)


def show_rouge_vs_llm(evals: list[GenerationEval]) -> None:
    """Compara ROUGE-L con faithfulness para mostrar cuándo divergen."""
    console.print()
    table = Table(title="ROUGE-L vs Faithfulness: cuándo divergen")
    table.add_column("ID", style="bold", width=7)
    table.add_column("ROUGE-L", justify="center", width=8)
    table.add_column("Faith.", justify="center", width=8)
    table.add_column("Divergen?", justify="center", width=10)
    table.add_column("Explicación", width=40)

    for ev in evals:
        # Consideramos divergencia si difieren en >0.3
        diverge = abs(ev.rouge_l - ev.faithfulness) > 0.3
        if diverge:
            if ev.rouge_l < ev.faithfulness:
                expl = "ROUGE baja por reformulación, pero es fiel al contexto"
            else:
                expl = "ROUGE alta por overlap léxico, pero no es fiel"
        else:
            expl = "Consistentes"

        d_color = "yellow" if diverge else "green"

        table.add_row(
            ev.query_id,
            f"{ev.rouge_l:.2f}",
            f"{ev.faithfulness:.2f}",
            f"[{d_color}]{'Sí' if diverge else 'No'}[/{d_color}]",
            expl,
        )

    console.print()
    console.print(Panel(
        "[bold]Conclusión:[/bold] ROUGE-L y Faithfulness miden cosas diferentes.\n\n"
        "ROUGE mide overlap léxico con la gold answer.\n"
        "Faithfulness mide si los claims están soportados en el contexto.\n\n"
        "Una respuesta puede tener ROUGE bajo (usa sinónimos) y faithfulness\n"
        "alto (todo está en el contexto), o ROUGE alto (repite palabras del\n"
        "gold) y faithfulness bajo (inventa datos adicionales).\n\n"
        "ROUGE es útil como check rápido en CI; faithfulness es la métrica\n"
        "que importa para calidad real.",
        style="yellow",
    ))


def show_cross_diagnostic(evals: list[GenerationEval]) -> None:
    """Tabla de diagnóstico cruzado retrieval × generación."""
    console.print()
    table = Table(title="Diagnóstico cruzado: Retrieval × Generación")
    table.add_column("Recall", justify="center", width=8)
    table.add_column("Faithfulness", justify="center", width=12)
    table.add_column("Relevance", justify="center", width=10)
    table.add_column("Diagnóstico", width=30)
    table.add_column("Queries", width=15)

    patterns: dict[tuple, list[str]] = {}
    for ev in evals:
        r_cat = "Alto" if ev.recall_at_k >= 0.5 else "Bajo"
        f_cat = "Alta" if ev.faithfulness >= 0.5 else "Baja"
        a_cat = "Alta" if ev.answer_relevance >= 0.5 else "Baja"
        key = (r_cat, f_cat, a_cat)
        patterns.setdefault(key, []).append(ev.query_id)

    diagnoses = {
        ("Alto", "Alta", "Alta"): "Sistema funcionando bien",
        ("Alto", "Baja", "Alta"): "Retrieval OK, generador hallucina",
        ("Alto", "Alta", "Baja"): "Retrieval OK, generador off-topic",
        ("Bajo", "Baja", "Alta"): "Retrieval malo + hallucination",
        ("Bajo", "Baja", "Baja"): "Todo roto — empezar por retrieval",
        ("Bajo", "Alta", "Alta"): "Peligroso: parece OK sin base documental",
    }

    for key, ids in sorted(patterns.items()):
        r, f, a = key
        diag = diagnoses.get(key, "Investigar")
        r_color = "green" if r == "Alto" else "red"
        f_color = "green" if f == "Alta" else "red"
        a_color = "green" if a == "Alta" else "red"

        table.add_row(
            f"[{r_color}]{r}[/{r_color}]",
            f"[{f_color}]{f}[/{f_color}]",
            f"[{a_color}]{a}[/{a_color}]",
            diag,
            ", ".join(ids),
        )

    console.print(table)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    console.print(Panel(
        "[bold]Métricas de generación: Faithfulness y Answer Relevance[/bold]\n\n"
        "Evaluación de 8 respuestas de un RAG fiscal con:\n"
        "  - Faithfulness (claims soportados en contexto)\n"
        "  - Answer Relevance (¿responde la pregunta?)\n"
        "  - ROUGE-L (baseline heurístico)\n"
        "  - Cuadrante de diagnóstico\n"
        "  - Diagnóstico cruzado retrieval × generación",
        style="bold blue",
    ))

    evals = build_generation_evals()

    # 1. Walkthrough de faithfulness para dos casos contrastantes
    show_faithfulness_walkthrough(evals[0])  # ge-001: ideal
    console.print()
    show_faithfulness_walkthrough(evals[1])  # ge-002: hallucination

    # 2. Tabla resumen
    show_all_evals(evals)

    # 3. Distribución por cuadrante
    show_quadrant_distribution(evals)

    # 4. ROUGE vs Faithfulness
    show_rouge_vs_llm(evals)

    # 5. Diagnóstico cruzado
    show_cross_diagnostic(evals)

    # Nota sobre implementación real
    console.print()
    console.print(Panel(
        "[bold]Nota: implementación con LLM real[/bold]\n\n"
        "Este script simula el juicio del LLM para ser ejecutable sin API keys.\n"
        "Para una implementación real, reemplazar build_generation_evals() por:\n\n"
        "  1. Extraer claims con: client.messages.create(prompt=EXTRACT_CLAIMS)\n"
        "  2. Verificar cada claim con: client.messages.create(prompt=VERIFY_CLAIM)\n"
        "  3. Evaluar relevance con: client.messages.create(prompt=JUDGE_RELEVANCE)\n\n"
        "Los prompts del juez se tratan en detalle en la sección 7 (LLM-as-judge).",
        style="dim",
    ))


if __name__ == "__main__":
    main()
