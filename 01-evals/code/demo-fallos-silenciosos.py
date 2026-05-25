"""Demostración de fallos silenciosos en un sistema RAG naive.

Este script simula un mini-pipeline RAG sobre el corpus regulatorio chileno
y muestra cómo, sin evaluaciones, los fallos son invisibles.

Ejecutar con:
    uv run python 01-evals/code/demo-fallos-silenciosos.py

No requiere API keys — usa retrieval simulado para ilustrar el concepto.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from shared.utils import get_logger, get_project_root

log = get_logger(__name__)
console = Console()


# ---------------------------------------------------------------------------
# Datos: corpus y chunks simulados
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    """Un fragmento de documento del corpus."""
    doc_id: str
    chunk_id: str
    text: str
    source: str  # archivo de origen


@dataclass
class QueryResult:
    """Resultado de una query al sistema RAG simulado."""
    query: str
    retrieved_chunks: list[Chunk]
    generated_answer: str
    # Campos de evaluación (en un sistema real no los tendrías sin evals)
    correct_answer: str
    correct_doc: str
    failure_type: str  # ok, retrieval_miss, wrong_chunk, hallucination, etc.


def load_corpus_chunks() -> list[Chunk]:
    """Carga documentos del corpus y los divide en chunks simples."""
    corpus_dir = get_project_root() / "shared" / "corpus_chileno"
    chunks: list[Chunk] = []

    for filepath in sorted(corpus_dir.glob("*.txt")):
        text = filepath.read_text(encoding="utf-8")
        # Chunking naive: dividir por doble salto de línea
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        for i, para in enumerate(paragraphs):
            chunks.append(Chunk(
                doc_id=filepath.stem,
                chunk_id=f"{filepath.stem}_chunk_{i:03d}",
                text=para,
                source=filepath.name,
            ))

    return chunks


# ---------------------------------------------------------------------------
# Sistema RAG simulado (intencionalmente imperfecto)
# ---------------------------------------------------------------------------

# Simulamos resultados predefinidos para ilustrar cada tipo de fallo.
# En un sistema real, estos vendrían del retriever + LLM.

SIMULATED_RESULTS: list[dict] = [
    {
        "query": "¿Cuál es la tasa de IVA para servicios digitales de proveedores extranjeros en Chile?",
        "retrieved_doc": "circular-01-sii-iva-digital.txt",
        "retrieved_chunk_keywords": "tasa del 19%",
        "generated_answer": "La tasa de IVA aplicable a los servicios digitales prestados por proveedores extranjeros en Chile es del 19%, según la Circular Nº 42 del SII de 2020.",
        "correct_answer": "19%, según Circular Nº 42 del SII (2020), que implementa modificaciones de la Ley Nº 21.210.",
        "correct_doc": "circular-01-sii-iva-digital.txt",
        "failure_type": "ok",
    },
    {
        "query": "¿Qué monto recibe un alumno prioritario de 7º básico por subvención escolar preferencial?",
        "retrieved_doc": "decreto-01-subvencion-escolar.txt",
        "retrieved_chunk_keywords": "CONSIDERANDO",
        "generated_answer": "Los alumnos prioritarios de 7º básico reciben una subvención de 1,694 USE mensuales.",
        "correct_answer": "1,130 USE (no 1,694 — esa es la tasa de 1º a 6º básico).",
        "correct_doc": "decreto-01-subvencion-escolar.txt",
        "failure_type": "wrong_chunk",
    },
    {
        "query": "¿Cuánto presupuesto se asigna al Programa Nacional de Inmunizaciones en 2024?",
        "retrieved_doc": "norma-01-ley-lobby.txt",
        "retrieved_chunk_keywords": "lobby",
        "generated_answer": "El presupuesto asignado al Programa Nacional de Inmunizaciones para 2024 es de aproximadamente $150.000.000 miles, destinados a la adquisición de vacunas.",
        "correct_answer": "$198.547.320 miles, según Glosa 05 de la Partida 16, Capítulo 01.",
        "correct_doc": "glosa-01-presupuesto-salud.txt",
        "failure_type": "retrieval_miss",
    },
    {
        "query": "¿Quiénes son sujetos pasivos de la Ley de Lobby?",
        "retrieved_doc": "norma-01-ley-lobby.txt",
        "retrieved_chunk_keywords": "sujetos pasivos",
        "generated_answer": "Según el Artículo 3º de la Ley Nº 20.730, son sujetos pasivos: el Presidente de la República, ministros, subsecretarios, jefes de servicio, directores regionales, intendentes, gobernadores, embajadores, consejeros de diversos órganos, diputados, senadores, alcaldes y concejales.",
        "correct_answer": "Los listados en Art. 3º de la Ley 20.730 (la respuesta es correcta).",
        "correct_doc": "norma-01-ley-lobby.txt",
        "failure_type": "ok",
    },
    {
        "query": "¿Cuál es la multa por infracción a la Ley de Transparencia de 2022?",
        "retrieved_doc": "norma-01-ley-lobby.txt",
        "retrieved_chunk_keywords": "multa",
        "generated_answer": "Según la Ley de Transparencia, las infracciones se sancionan con multas de 20 a 100 UTM, pudiendo duplicarse en caso de reincidencia.",
        "correct_answer": "La query mezcla dos leyes distintas. La Ley de Lobby (20.730) tiene multas de 10 a 50 UTM (Art. 11). El sistema no debería responder sobre una 'Ley de Transparencia de 2022' que no está en el corpus.",
        "correct_doc": "ninguno — la pregunta es sobre un documento fuera del corpus",
        "failure_type": "false_confidence",
    },
    {
        "query": "¿Qué porcentaje del Aporte Fiscal Libre debe FONASA destinar a libre elección?",
        "retrieved_doc": "glosa-01-presupuesto-salud.txt",
        "retrieved_chunk_keywords": "Aporte Fiscal Libre",
        "generated_answer": "FONASA debe destinar al menos el 5% de los recursos del Aporte Fiscal Libre a financiar la modalidad de libre elección para prestaciones de baja complejidad en comunas con déficit de oferta pública.",
        "correct_answer": "Al menos 5%, según Glosa 09 del Capítulo 02 de la Partida 16.",
        "correct_doc": "glosa-01-presupuesto-salud.txt",
        "failure_type": "ok",
    },
]


def simulate_rag_results() -> list[QueryResult]:
    """Genera resultados simulados del pipeline RAG."""
    chunks = load_corpus_chunks()
    results: list[QueryResult] = []

    for sim in SIMULATED_RESULTS:
        # Buscar chunks que coincidan con el doc simulado
        doc_chunks = [c for c in chunks if c.source == sim["retrieved_doc"]]
        # Tomar los primeros 3 chunks del documento (simulando top-k=3)
        retrieved = doc_chunks[:3] if doc_chunks else [
            Chunk("unknown", "unknown_0", "[no document found]", "none")
        ]

        results.append(QueryResult(
            query=sim["query"],
            retrieved_chunks=retrieved,
            generated_answer=sim["generated_answer"],
            correct_answer=sim["correct_answer"],
            correct_doc=sim["correct_doc"],
            failure_type=sim["failure_type"],
        ))

    return results


# ---------------------------------------------------------------------------
# Visualización: la perspectiva SIN evals vs CON evals
# ---------------------------------------------------------------------------

FAILURE_COLORS = {
    "ok": "green",
    "retrieval_miss": "red",
    "wrong_chunk": "yellow",
    "hallucination": "red",
    "false_confidence": "red",
}

FAILURE_LABELS = {
    "ok": "Correcto",
    "retrieval_miss": "Retrieval miss — doc equivocado",
    "wrong_chunk": "Wrong chunk — chunk equivocado del doc correcto",
    "hallucination": "Alucinación — inventó datos",
    "false_confidence": "Falsa confianza — respondió algo fuera de scope",
}


def show_without_evals(results: list[QueryResult]) -> None:
    """Muestra lo que ve un equipo SIN evaluaciones: todo parece ok."""
    console.print()
    console.print(Panel(
        "[bold]Perspectiva SIN evaluaciones[/bold]\n"
        "Solo ves las respuestas. Todas suenan profesionales y seguras.",
        style="blue",
    ))

    for i, r in enumerate(results, 1):
        console.print(f"\n[bold]Q{i}:[/bold] {r.query}")
        console.print(f"[dim]→ {r.generated_answer}[/dim]")
        console.print("[green]✓ La respuesta suena bien[/green]")


def show_with_evals(results: list[QueryResult]) -> None:
    """Muestra lo que ve un equipo CON evaluaciones: los fallos son visibles."""
    console.print()
    console.print(Panel(
        "[bold]Perspectiva CON evaluaciones[/bold]\n"
        "Ahora comparas contra respuestas de referencia y verificas el retrieval.",
        style="yellow",
    ))

    for i, r in enumerate(results, 1):
        color = FAILURE_COLORS.get(r.failure_type, "white")
        label = FAILURE_LABELS.get(r.failure_type, r.failure_type)

        console.print(f"\n[bold]Q{i}:[/bold] {r.query}")
        console.print(f"  [dim]Respuesta:[/dim] {r.generated_answer}")
        console.print(f"  [dim]Referencia:[/dim] {r.correct_answer}")
        console.print(f"  [{color}]→ {label}[/{color}]")


def show_summary(results: list[QueryResult]) -> None:
    """Muestra un resumen estadístico de los resultados."""
    console.print()

    table = Table(title="Resumen de evaluación")
    table.add_column("Tipo de fallo", style="bold")
    table.add_column("Cuenta", justify="center")
    table.add_column("Proporción", justify="center")

    # Contar fallos por tipo
    counts: dict[str, int] = {}
    for r in results:
        counts[r.failure_type] = counts.get(r.failure_type, 0) + 1

    total = len(results)
    for ftype, count in sorted(counts.items()):
        color = FAILURE_COLORS.get(ftype, "white")
        label = FAILURE_LABELS.get(ftype, ftype)
        pct = f"{count / total:.0%}"
        table.add_row(f"[{color}]{label}[/{color}]", str(count), pct)

    table.add_section()
    ok_count = counts.get("ok", 0)
    accuracy = f"{ok_count / total:.0%}"
    table.add_row("[bold]Accuracy total[/bold]", f"{ok_count}/{total}", accuracy)

    console.print(table)

    # El punto clave
    console.print()
    console.print(Panel(
        f"[bold]Sin evals:[/bold] \"El sistema funciona bien, probé unas preguntas.\"\n"
        f"[bold]Con evals:[/bold] \"Accuracy = {accuracy}. "
        f"El {(total - ok_count) / total:.0%} de las queries tienen fallos, "
        f"dominados por retrieval miss y falsa confianza.\"\n\n"
        f"Con solo {total} queries ya tienes una imagen completamente diferente.\n"
        f"Imagina qué descubrirías con 200.",
        title="La diferencia",
        style="bold",
    ))


def export_results_json(results: list[QueryResult]) -> Path:
    """Exporta resultados como JSON para uso en secciones posteriores."""
    output_dir = get_project_root() / "01-evals" / "examples"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "resultados-demo-fallos.json"

    data = []
    for r in results:
        data.append({
            "query": r.query,
            "generated_answer": r.generated_answer,
            "correct_answer": r.correct_answer,
            "correct_doc": r.correct_doc,
            "failure_type": r.failure_type,
            "retrieved_docs": [c.source for c in r.retrieved_chunks],
        })

    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    console.print(Panel(
        "[bold]Demo: Fallos silenciosos en RAG[/bold]\n\n"
        "Este script demuestra por qué las evaluaciones son imprescindibles.\n"
        "Simula 6 queries a un RAG sobre normativa chilena y muestra la\n"
        "diferencia entre verlo sin evals (todo parece ok) y con evals\n"
        "(los fallos se hacen visibles).",
        style="bold blue",
    ))

    # Cargar y ejecutar
    results = simulate_rag_results()

    # Vista sin evals
    show_without_evals(results)

    console.print("\n" + "═" * 70 + "\n")

    # Vista con evals
    show_with_evals(results)

    # Resumen
    show_summary(results)

    # Exportar para uso posterior
    output_path = export_results_json(results)
    console.print(f"\n[dim]Resultados exportados a: {output_path}[/dim]")


if __name__ == "__main__":
    main()
