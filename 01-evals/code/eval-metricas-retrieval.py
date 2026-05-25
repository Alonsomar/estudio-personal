"""Métricas de retrieval: Recall@k, MRR, nDCG desde cero.

Implementa las tres métricas fundamentales de retrieval sin depender
de librerías de evaluación. Usa el golden dataset generado en la
sección 4 y un retriever simulado para producir resultados numéricos
paso a paso.

Ejecutar con:
    uv run python 01-evals/code/eval-metricas-retrieval.py

No requiere API keys.
"""

import json
import math
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
class RetrievalResult:
    """Resultado de retrieval para una query."""
    query_id: str
    query: str
    relevant_docs: list[str]       # docs que DEBERÍAN aparecer (del golden)
    retrieved_docs: list[str]      # docs que el retriever DEVOLVIÓ (top-k)
    relevance_scores: list[int]    # score de relevancia graduada por posición (para nDCG)


# ---------------------------------------------------------------------------
# Retriever simulado
# ---------------------------------------------------------------------------

def simulate_retrieval(k: int = 5) -> list[RetrievalResult]:
    """Simula resultados de retrieval para 10 queries del golden dataset.

    Los resultados están diseñados para ilustrar los matices de cada métrica:
    - Queries con recall perfecto pero MRR bajo (doc relevante abajo)
    - Queries con MRR perfecto pero recall parcial (solo encuentra 1 de 2)
    - Queries con nDCG alto vs bajo dependiendo del orden
    """
    results = [
        # Q1: Perfecto — doc relevante en posición 1
        RetrievalResult("gd-002",
            "¿Cuál es la tasa de IVA para servicios digitales?",
            ["circular-01-sii-iva-digital.txt"],
            ["circular-01-sii-iva-digital.txt", "decreto-01-subvencion-escolar.txt",
             "norma-01-ley-lobby.txt", "glosa-01-presupuesto-salud.txt",
             "decreto-01-subvencion-escolar.txt"],
            [3, 0, 0, 0, 0]),

        # Q2: Recall ok, MRR ok, pero nDCG subóptimo (doc más relevante en pos 3)
        RetrievalResult("gd-003",
            "¿Qué indicios determinan uso de servicio digital en Chile?",
            ["circular-01-sii-iva-digital.txt"],
            ["norma-01-ley-lobby.txt", "decreto-01-subvencion-escolar.txt",
             "circular-01-sii-iva-digital.txt", "glosa-01-presupuesto-salud.txt",
             "norma-01-ley-lobby.txt"],
            [0, 0, 3, 0, 0]),

        # Q3: Total miss — doc relevante no aparece
        RetrievalResult("gd-006",
            "¿Cuántas USE recibe un alumno prioritario de 3º básico?",
            ["decreto-01-subvencion-escolar.txt"],
            ["circular-01-sii-iva-digital.txt", "norma-01-ley-lobby.txt",
             "glosa-01-presupuesto-salud.txt", "circular-01-sii-iva-digital.txt",
             "norma-01-ley-lobby.txt"],
            [0, 0, 0, 0, 0]),

        # Q4: Múltiples docs relevantes — encuentra ambos
        RetrievalResult("gd-019",
            "¿Qué autoridades son sujetos pasivos de la Ley de Lobby?",
            ["norma-01-ley-lobby.txt"],
            ["norma-01-ley-lobby.txt", "norma-01-ley-lobby.txt",
             "decreto-01-subvencion-escolar.txt", "circular-01-sii-iva-digital.txt",
             "glosa-01-presupuesto-salud.txt"],
            [3, 2, 0, 0, 0]),

        # Q5: Doc relevante en última posición
        RetrievalResult("gd-011",
            "¿Cuánto presupuesto se asigna a inmunizaciones en 2024?",
            ["glosa-01-presupuesto-salud.txt"],
            ["decreto-01-subvencion-escolar.txt", "norma-01-ley-lobby.txt",
             "circular-01-sii-iva-digital.txt", "decreto-01-subvencion-escolar.txt",
             "glosa-01-presupuesto-salud.txt"],
            [0, 0, 0, 0, 3]),

        # Q6: Parcialmente relevante — docs de contexto pero no el principal
        RetrievalResult("gd-007",
            "¿Diferencia en USE entre alumno de 5º y 7º básico?",
            ["decreto-01-subvencion-escolar.txt"],
            ["decreto-01-subvencion-escolar.txt", "decreto-01-subvencion-escolar.txt",
             "glosa-01-presupuesto-salud.txt", "norma-01-ley-lobby.txt",
             "circular-01-sii-iva-digital.txt"],
            [2, 1, 0, 0, 0]),

        # Q7: Perfecto con relevancia graduada
        RetrievalResult("gd-013",
            "¿Qué información debe reportar Salud sobre inmunizaciones?",
            ["glosa-01-presupuesto-salud.txt"],
            ["glosa-01-presupuesto-salud.txt", "glosa-01-presupuesto-salud.txt",
             "decreto-01-subvencion-escolar.txt", "norma-01-ley-lobby.txt",
             "circular-01-sii-iva-digital.txt"],
            [3, 1, 0, 0, 0]),

        # Q8: Scope query — debería no encontrar nada relevante (y no encuentra)
        RetrievalResult("gd-025",
            "¿Cuál es la multa por infracción a la Ley de Transparencia?",
            [],  # No hay doc relevante
            ["norma-01-ley-lobby.txt", "decreto-01-subvencion-escolar.txt",
             "circular-01-sii-iva-digital.txt", "glosa-01-presupuesto-salud.txt",
             "norma-01-ley-lobby.txt"],
            [1, 0, 0, 0, 0]),  # El de lobby es distractor, parcialmente relevante

        # Q9: Multi-doc — necesita cruzar fuentes
        RetrievalResult("gd-024",
            "¿Qué obligación trimestral comparten prestadores digitales y Min. Salud?",
            ["circular-01-sii-iva-digital.txt", "glosa-01-presupuesto-salud.txt"],
            ["circular-01-sii-iva-digital.txt", "norma-01-ley-lobby.txt",
             "glosa-01-presupuesto-salud.txt", "decreto-01-subvencion-escolar.txt",
             "circular-01-sii-iva-digital.txt"],
            [3, 0, 3, 0, 1]),

        # Q10: Correcto pero con distractores fuertes
        RetrievalResult("gd-020",
            "¿Cuál es el rango de multas por la Ley de Lobby?",
            ["norma-01-ley-lobby.txt"],
            ["norma-01-ley-lobby.txt", "circular-01-sii-iva-digital.txt",
             "decreto-01-subvencion-escolar.txt", "norma-01-ley-lobby.txt",
             "glosa-01-presupuesto-salud.txt"],
            [3, 0, 0, 1, 0]),
    ]
    return results


# ---------------------------------------------------------------------------
# Implementación de métricas desde cero
# ---------------------------------------------------------------------------

def recall_at_k(relevant: list[str], retrieved: list[str], k: int) -> float:
    """Calcula Recall@k.

    Args:
        relevant: Lista de documentos relevantes (ground truth).
        retrieved: Lista de documentos recuperados (ordenados por ranking).
        k: Número de resultados a considerar.

    Returns:
        Recall@k entre 0 y 1. Si no hay docs relevantes, retorna 1.0
        (no hay nada que encontrar, así que "encontró todo").
    """
    if not relevant:
        return 1.0  # Vacuous truth: no hay nada que buscar

    relevant_set = set(relevant)
    retrieved_top_k = retrieved[:k]
    found = relevant_set.intersection(set(retrieved_top_k))
    return len(found) / len(relevant_set)


def reciprocal_rank(relevant: list[str], retrieved: list[str], k: int) -> float:
    """Calcula Reciprocal Rank (para MRR).

    Args:
        relevant: Lista de documentos relevantes.
        retrieved: Lista de documentos recuperados.
        k: Número máximo de posiciones a considerar.

    Returns:
        1/rank del primer documento relevante, o 0 si no hay ninguno en top-k.
    """
    if not relevant:
        return 0.0

    relevant_set = set(relevant)
    for i, doc in enumerate(retrieved[:k]):
        if doc in relevant_set:
            return 1.0 / (i + 1)
    return 0.0


def dcg_at_k(relevance_scores: list[int], k: int) -> float:
    """Calcula DCG@k (Discounted Cumulative Gain).

    Args:
        relevance_scores: Score de relevancia por posición.
        k: Número de posiciones a considerar.

    Returns:
        DCG@k.
    """
    dcg = 0.0
    for i, rel in enumerate(relevance_scores[:k]):
        if rel > 0:
            dcg += rel / math.log2(i + 2)  # +2 porque i empieza en 0
    return dcg


def ndcg_at_k(relevance_scores: list[int], k: int) -> float:
    """Calcula nDCG@k (Normalized Discounted Cumulative Gain).

    Args:
        relevance_scores: Score de relevancia por posición en el ranking real.
        k: Número de posiciones a considerar.

    Returns:
        nDCG@k entre 0 y 1.
    """
    dcg = dcg_at_k(relevance_scores, k)
    # Ideal: ordenar por relevancia descendente
    ideal_scores = sorted(relevance_scores, reverse=True)
    idcg = dcg_at_k(ideal_scores, k)

    if idcg == 0:
        return 0.0
    return dcg / idcg


# ---------------------------------------------------------------------------
# Visualización
# ---------------------------------------------------------------------------

def show_per_query_table(results: list[RetrievalResult], k: int) -> None:
    """Muestra métricas por query con cálculo detallado."""
    table = Table(title=f"Métricas de retrieval por query (k={k})", show_lines=True)
    table.add_column("ID", style="bold", width=7)
    table.add_column("Query", width=35)
    table.add_column(f"Recall@{k}", justify="center", width=10)
    table.add_column("RR", justify="center", width=8)
    table.add_column(f"nDCG@{k}", justify="center", width=10)
    table.add_column("Detalle", width=30)

    recalls, rrs, ndcgs = [], [], []

    for r in results:
        rec = recall_at_k(r.relevant_docs, r.retrieved_docs, k)
        rr = reciprocal_rank(r.relevant_docs, r.retrieved_docs, k)
        ndcg = ndcg_at_k(r.relevance_scores, k)

        recalls.append(rec)
        rrs.append(rr)
        ndcgs.append(ndcg)

        # Detalle del cálculo
        if not r.relevant_docs:
            detail = "Scope query (no relevant docs)"
        elif rec == 0:
            detail = "Total miss — doc no encontrado"
        elif rr < 1.0 and rec > 0:
            pos = next(
                (i+1 for i, d in enumerate(r.retrieved_docs[:k])
                 if d in set(r.relevant_docs)),
                0
            )
            detail = f"Encontrado en posición {pos}"
        else:
            detail = "Top-1 relevante"

        rec_color = "green" if rec >= 0.8 else "yellow" if rec > 0 else "red"
        rr_color = "green" if rr >= 0.8 else "yellow" if rr > 0 else "red"
        ndcg_color = "green" if ndcg >= 0.8 else "yellow" if ndcg > 0 else "red"

        table.add_row(
            r.query_id,
            r.query[:33] + "..." if len(r.query) > 35 else r.query,
            f"[{rec_color}]{rec:.3f}[/{rec_color}]",
            f"[{rr_color}]{rr:.3f}[/{rr_color}]",
            f"[{ndcg_color}]{ndcg:.3f}[/{ndcg_color}]",
            detail,
        )

    # Promedios
    avg_rec = sum(recalls) / len(recalls)
    avg_rr = sum(rrs) / len(rrs)
    avg_ndcg = sum(ndcgs) / len(ndcgs)

    table.add_section()
    table.add_row(
        "", "[bold]Promedio[/bold]",
        f"[bold]{avg_rec:.3f}[/bold]",
        f"[bold]{avg_rr:.3f}[/bold]",
        f"[bold]{avg_ndcg:.3f}[/bold]",
        "",
    )

    console.print(table)
    return recalls, rrs, ndcgs


def show_ndcg_walkthrough(result: RetrievalResult, k: int) -> None:
    """Muestra el cálculo paso a paso de nDCG para una query."""
    console.print()
    console.print(Panel(
        f"[bold]Cálculo paso a paso de nDCG@{k}[/bold]\n"
        f"Query: {result.query}",
        style="blue",
    ))

    # DCG real
    table = Table(title="Ranking real → DCG")
    table.add_column("Pos (i)", justify="center")
    table.add_column("Documento", width=30)
    table.add_column("rel", justify="center")
    table.add_column("log₂(i+1)", justify="center")
    table.add_column("rel/log₂(i+1)", justify="center")

    dcg = 0.0
    for i, (doc, rel) in enumerate(
        zip(result.retrieved_docs[:k], result.relevance_scores[:k])
    ):
        log_val = math.log2(i + 2)
        gain = rel / log_val if rel > 0 else 0
        dcg += gain
        table.add_row(
            str(i + 1),
            doc[:28] + ".." if len(doc) > 30 else doc,
            str(rel),
            f"{log_val:.3f}",
            f"{gain:.3f}",
        )
    table.add_section()
    table.add_row("", "", "", "[bold]DCG =[/bold]", f"[bold]{dcg:.3f}[/bold]")
    console.print(table)

    # IDCG (ideal)
    ideal_scores = sorted(result.relevance_scores[:k], reverse=True)
    idcg_table = Table(title="Ranking ideal → IDCG")
    idcg_table.add_column("Pos (i)", justify="center")
    idcg_table.add_column("rel (ordenado)", justify="center")
    idcg_table.add_column("log₂(i+1)", justify="center")
    idcg_table.add_column("rel/log₂(i+1)", justify="center")

    idcg = 0.0
    for i, rel in enumerate(ideal_scores):
        log_val = math.log2(i + 2)
        gain = rel / log_val if rel > 0 else 0
        idcg += gain
        idcg_table.add_row(str(i + 1), str(rel), f"{log_val:.3f}", f"{gain:.3f}")
    idcg_table.add_section()
    idcg_table.add_row("", "", "[bold]IDCG =[/bold]", f"[bold]{idcg:.3f}[/bold]")
    console.print(idcg_table)

    ndcg = dcg / idcg if idcg > 0 else 0
    console.print(f"\n[bold]nDCG@{k} = DCG / IDCG = {dcg:.3f} / {idcg:.3f} = {ndcg:.3f}[/bold]")


def show_k_sensitivity(results: list[RetrievalResult]) -> None:
    """Muestra cómo cambian las métricas con distintos valores de k."""
    console.print()
    table = Table(title="Sensibilidad al valor de k")
    table.add_column("k", style="bold", justify="center")
    table.add_column("Recall@k", justify="center")
    table.add_column("MRR@k", justify="center")
    table.add_column("nDCG@k", justify="center")

    for k in [1, 2, 3, 5]:
        avg_rec = sum(recall_at_k(r.relevant_docs, r.retrieved_docs, k) for r in results) / len(results)
        avg_rr = sum(reciprocal_rank(r.relevant_docs, r.retrieved_docs, k) for r in results) / len(results)
        avg_ndcg = sum(ndcg_at_k(r.relevance_scores, k) for r in results) / len(results)

        table.add_row(str(k), f"{avg_rec:.3f}", f"{avg_rr:.3f}", f"{avg_ndcg:.3f}")

    console.print(table)


def show_distribution_analysis(recalls: list[float], metric_name: str) -> None:
    """Muestra que el promedio esconde la distribución."""
    console.print()

    n = len(recalls)
    sorted_vals = sorted(recalls)
    avg = sum(recalls) / n
    median = sorted_vals[n // 2]
    zeros = sum(1 for v in recalls if v == 0)
    ones = sum(1 for v in recalls if v >= 1.0)

    console.print(Panel(
        f"[bold]Distribución de {metric_name} (no solo el promedio)[/bold]\n\n"
        f"  Promedio:        {avg:.3f}\n"
        f"  Mediana:         {median:.3f}\n"
        f"  Queries con 0.0: {zeros}/{n} ({zeros/n:.0%}) ← completamente rotas\n"
        f"  Queries con 1.0: {ones}/{n} ({ones/n:.0%}) ← perfectas\n"
        f"  p10:             {sorted_vals[max(0, n//10)]:.3f}\n"
        f"  p90:             {sorted_vals[min(n-1, 9*n//10)]:.3f}\n\n"
        f"{'Distribución bimodal' if zeros > 0 and ones > 0 else 'Distribución uniforme'}:\n"
        f"  la mayoría de queries o funcionan perfecto o fallan completamente.\n"
        f"  El promedio de {avg:.3f} esconde esta realidad.",
        style="bold yellow",
    ))


def show_metric_comparison() -> None:
    """Tabla resumen comparando las tres métricas."""
    console.print()
    table = Table(title="¿Qué métrica usar?")
    table.add_column("Escenario", style="bold", width=40)
    table.add_column("Métrica", width=12)
    table.add_column("Por qué", width=35)

    table.add_row(
        "RAG básico, contexto = concat(top-k)",
        "Recall@k",
        "Solo importa que el doc esté, no dónde",
    )
    table.add_row(
        "Usuario ve lista de resultados",
        "MRR",
        "El primer resultado importa más",
    )
    table.add_row(
        "Pipeline con reranker",
        "nDCG + Recall",
        "Recall para cobertura, nDCG para orden",
    )
    table.add_row(
        "Golden dataset con anotación binaria",
        "Recall + MRR",
        "No tienes scores graduados para nDCG",
    )
    table.add_row(
        "Golden dataset con relevancia graduada",
        "Las tres",
        "Máxima información disponible",
    )

    console.print(table)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    console.print(Panel(
        "[bold]Métricas de retrieval: Recall@k, MRR, nDCG[/bold]\n\n"
        "Implementación desde cero con 10 queries sobre corpus fiscal.\n"
        "Cálculo paso a paso, sensibilidad a k, análisis de distribución.",
        style="bold blue",
    ))

    results = simulate_retrieval(k=5)
    k = 5

    # 1. Tabla por query
    recalls, rrs, ndcgs = show_per_query_table(results, k)

    # 2. Walkthrough de nDCG para query multi-doc
    show_ndcg_walkthrough(results[8], k)  # Q9: multi-doc

    # 3. Sensibilidad a k
    show_k_sensitivity(results)

    # 4. Distribución (el promedio miente)
    show_distribution_analysis(recalls, "Recall@5")

    # 5. Guía de selección
    show_metric_comparison()


if __name__ == "__main__":
    main()
