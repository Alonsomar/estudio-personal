"""Taxonomía de evaluaciones: clasificador y tabla cruzada.

Demuestra los tres ejes de clasificación (granularidad, temporalidad, referencia)
con ejemplos concretos del dominio regulatorio/fiscal chileno. Genera una tabla
cruzada de combinaciones útiles y una visualización de cobertura.

Ejecutar con:
    uv run python 01-evals/code/taxonomia-evals.py

No requiere API keys.
"""

from dataclasses import dataclass
from enum import Enum

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from shared.utils import get_logger

log = get_logger(__name__)
console = Console()


# ---------------------------------------------------------------------------
# Modelo de datos: los tres ejes
# ---------------------------------------------------------------------------

class Granularity(str, Enum):
    UNIT = "unit"
    COMPONENT = "component"
    SYSTEM = "system"
    END_TO_END = "end-to-end"


class Temporality(str, Enum):
    OFFLINE = "offline"
    ONLINE = "online"


class ReferenceType(str, Enum):
    REFERENCE_BASED = "reference-based"
    REFERENCE_FREE = "reference-free"
    HUMAN_GROUNDED = "human-grounded"


@dataclass
class EvalSpec:
    """Especificación de una evaluación clasificada en los tres ejes."""
    name: str
    description: str
    granularity: Granularity
    temporality: Temporality
    reference: ReferenceType
    example_query: str
    metric: str
    frequency: str
    estimated_cost: str  # por ejecución


# ---------------------------------------------------------------------------
# Catálogo de evals para un RAG fiscal
# ---------------------------------------------------------------------------

EVAL_CATALOG: list[EvalSpec] = [
    EvalSpec(
        name="Chunker parse test",
        description="Verifica que el chunker separe correctamente las secciones de un decreto",
        granularity=Granularity.UNIT,
        temporality=Temporality.OFFLINE,
        reference=ReferenceType.REFERENCE_BASED,
        example_query="Input: decreto completo → Output: lista de chunks",
        metric="Exact match en número y títulos de chunks",
        frequency="Cada commit",
        estimated_cost="~$0 (sin LLM)",
    ),
    EvalSpec(
        name="Prompt format validation",
        description="Verifica que el prompt de generación produce JSON válido",
        granularity=Granularity.UNIT,
        temporality=Temporality.OFFLINE,
        reference=ReferenceType.REFERENCE_FREE,
        example_query="Input: contexto + query → Output: ¿JSON válido?",
        metric="Parse success rate",
        frequency="Cada commit",
        estimated_cost="~$0.01 (1 llamada LLM)",
    ),
    EvalSpec(
        name="Retriever recall",
        description="¿El retriever coloca el chunk correcto en top-k?",
        granularity=Granularity.COMPONENT,
        temporality=Temporality.OFFLINE,
        reference=ReferenceType.REFERENCE_BASED,
        example_query="¿Cuál es la tasa de IVA digital? → ¿chunk de Circular 42 en top-3?",
        metric="Recall@3, MRR",
        frequency="Cada PR",
        estimated_cost="~$0.05 (embeddings para 50 queries)",
    ),
    EvalSpec(
        name="Coherencia de chunks",
        description="¿Los chunks recuperados son temáticamente coherentes entre sí?",
        granularity=Granularity.COMPONENT,
        temporality=Temporality.OFFLINE,
        reference=ReferenceType.REFERENCE_FREE,
        example_query="Recupero 3 chunks para una query sobre IVA → ¿todos hablan de IVA?",
        metric="Coherencia score (LLM-as-judge)",
        frequency="Cada PR",
        estimated_cost="~$0.10 (judge por query × 50)",
    ),
    EvalSpec(
        name="System accuracy",
        description="¿La respuesta final coincide con la respuesta gold?",
        granularity=Granularity.SYSTEM,
        temporality=Temporality.OFFLINE,
        reference=ReferenceType.REFERENCE_BASED,
        example_query="¿Monto del Programa de Inmunizaciones? → Gold: $198.547.320 miles",
        metric="Accuracy, F1 sobre entidades extraídas",
        frequency="Pre-release",
        estimated_cost="~$1.00 (pipeline completo × 50 queries)",
    ),
    EvalSpec(
        name="Faithfulness check",
        description="¿La respuesta se apoya en los documentos recuperados?",
        granularity=Granularity.SYSTEM,
        temporality=Temporality.OFFLINE,
        reference=ReferenceType.REFERENCE_FREE,
        example_query="Respuesta sobre IVA → ¿cada claim tiene soporte en el contexto?",
        metric="Faithfulness score (LLM-as-judge)",
        frequency="Pre-release",
        estimated_cost="~$2.00 (judge detallado × 50 queries)",
    ),
    EvalSpec(
        name="Expert review",
        description="Un analista fiscal revisa respuestas y puntúa calidad",
        granularity=Granularity.SYSTEM,
        temporality=Temporality.OFFLINE,
        reference=ReferenceType.HUMAN_GROUNDED,
        example_query="20 respuestas sobre normativa → puntuación 1-5 por experto",
        metric="Promedio de puntuación humana, inter-annotator agreement",
        frequency="Mensual",
        estimated_cost="~$200 (2h de trabajo experto)",
    ),
    EvalSpec(
        name="Thumbs up/down",
        description="% de usuarios que valoran positivamente la respuesta",
        granularity=Granularity.END_TO_END,
        temporality=Temporality.ONLINE,
        reference=ReferenceType.HUMAN_GROUNDED,
        example_query="Cualquier query real → ¿el usuario hizo click en 👍?",
        metric="Tasa de aprobación, tendencia semanal",
        frequency="Continuo",
        estimated_cost="~$0 (solo logging)",
    ),
    EvalSpec(
        name="Reformulation rate",
        description="% de queries que el usuario reformula (señal de insatisfacción)",
        granularity=Granularity.END_TO_END,
        temporality=Temporality.ONLINE,
        reference=ReferenceType.REFERENCE_FREE,
        example_query="Query → respuesta → ¿nueva query en <30s?",
        metric="Tasa de reformulación por sesión",
        frequency="Continuo",
        estimated_cost="~$0 (solo logging)",
    ),
    EvalSpec(
        name="Shadow judge",
        description="LLM-as-judge evalúa muestra de tráfico real",
        granularity=Granularity.SYSTEM,
        temporality=Temporality.ONLINE,
        reference=ReferenceType.REFERENCE_FREE,
        example_query="Muestra 5% del tráfico → judge evalúa faithfulness",
        metric="Faithfulness score promedio en producción",
        frequency="Diario",
        estimated_cost="~$5/día (judge sobre muestra)",
    ),
]


# ---------------------------------------------------------------------------
# Visualización
# ---------------------------------------------------------------------------

def show_catalog() -> None:
    """Muestra el catálogo completo de evals clasificadas."""
    table = Table(
        title="Catálogo de evaluaciones para RAG fiscal",
        show_lines=True,
    )
    table.add_column("Eval", style="bold", width=20)
    table.add_column("Granularidad", width=12)
    table.add_column("Temporalidad", width=10)
    table.add_column("Referencia", width=16)
    table.add_column("Métrica", width=25)
    table.add_column("Frecuencia", width=12)
    table.add_column("Costo", width=10)

    granularity_colors = {
        Granularity.UNIT: "green",
        Granularity.COMPONENT: "blue",
        Granularity.SYSTEM: "yellow",
        Granularity.END_TO_END: "red",
    }

    for ev in EVAL_CATALOG:
        g_color = granularity_colors[ev.granularity]
        table.add_row(
            ev.name,
            f"[{g_color}]{ev.granularity.value}[/{g_color}]",
            ev.temporality.value,
            ev.reference.value,
            ev.metric,
            ev.frequency,
            ev.estimated_cost,
        )

    console.print(table)


def show_cross_table() -> None:
    """Muestra tabla cruzada granularidad × temporalidad con conteos."""
    console.print()
    table = Table(title="Tabla cruzada: Granularidad × Temporalidad")
    table.add_column("Granularidad", style="bold")
    table.add_column("Offline", justify="center")
    table.add_column("Online", justify="center")

    for g in Granularity:
        offline = sum(
            1 for e in EVAL_CATALOG
            if e.granularity == g and e.temporality == Temporality.OFFLINE
        )
        online = sum(
            1 for e in EVAL_CATALOG
            if e.granularity == g and e.temporality == Temporality.ONLINE
        )
        table.add_row(
            g.value,
            str(offline) if offline else "—",
            str(online) if online else "—",
        )

    console.print(table)


def show_pyramid() -> None:
    """Muestra la pirámide de evals con distribución del catálogo."""
    console.print()
    counts = {}
    for g in Granularity:
        counts[g] = sum(1 for e in EVAL_CATALOG if e.granularity == g)

    total = len(EVAL_CATALOG)

    console.print(Panel(
        "[bold]Pirámide de evaluaciones (distribución del catálogo)[/bold]\n\n"
        f"  [red]{'E2E':^40}[/red]  ← {counts[Granularity.END_TO_END]}/{total} "
        f"({counts[Granularity.END_TO_END] / total:.0%})\n"
        f"  [yellow]{'System':^40}[/yellow]  ← {counts[Granularity.SYSTEM]}/{total} "
        f"({counts[Granularity.SYSTEM] / total:.0%})\n"
        f"  [blue]{'Component':^40}[/blue]  ← {counts[Granularity.COMPONENT]}/{total} "
        f"({counts[Granularity.COMPONENT] / total:.0%})\n"
        f"  [green]{'Unit':^40}[/green]  ← {counts[Granularity.UNIT]}/{total} "
        f"({counts[Granularity.UNIT] / total:.0%})\n\n"
        "La base (unit/component) debería ser la más ancha.\n"
        "El catálogo muestra más system evals porque es donde está\n"
        "la complejidad interesante — pero en CI, los unit tests dominan.",
        style="bold",
    ))


def show_cost_summary() -> None:
    """Muestra un resumen de costos por frecuencia."""
    console.print()
    table = Table(title="Presupuesto de evaluación por ciclo")
    table.add_column("Frecuencia", style="bold")
    table.add_column("Evals", width=40)
    table.add_column("Costo estimado", justify="right")

    freq_groups: dict[str, list[EvalSpec]] = {}
    for ev in EVAL_CATALOG:
        freq_groups.setdefault(ev.frequency, []).append(ev)

    for freq in ["Cada commit", "Cada PR", "Pre-release", "Mensual",
                 "Diario", "Continuo"]:
        if freq in freq_groups:
            names = ", ".join(e.name for e in freq_groups[freq])
            costs = ", ".join(e.estimated_cost for e in freq_groups[freq])
            table.add_row(freq, names, costs)

    console.print(table)


def show_decision_guide() -> None:
    """Guía rápida de decisión para elegir tipo de eval."""
    console.print()
    console.print(Panel(
        "[bold]Guía rápida de decisión[/bold]\n\n"
        "1. ¿Estoy probando una función/prompt aislado?\n"
        "   → [green]Unit eval[/green], offline, reference-based si tengo gold\n\n"
        "2. ¿Estoy probando el retriever o el generador por separado?\n"
        "   → [blue]Component eval[/blue], offline, recall@k o faithfulness\n\n"
        "3. ¿Estoy validando el pipeline completo antes de release?\n"
        "   → [yellow]System eval[/yellow], offline, golden dataset + LLM-as-judge\n\n"
        "4. ¿Ya estoy en producción y quiero monitorear?\n"
        "   → [red]Online eval[/red], métricas proxy + sampling con judge\n\n"
        "5. ¿Necesito calibrar mis evals automáticas?\n"
        "   → Human-grounded, periódicamente, para verificar correlación",
        style="bold",
    ))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    console.print(Panel(
        "[bold]Taxonomía de evaluaciones para RAG fiscal[/bold]\n\n"
        "Tres ejes: granularidad × temporalidad × referencia\n"
        "Catálogo de 10 evals concretas clasificadas.",
        style="bold blue",
    ))

    show_catalog()
    show_cross_table()
    show_pyramid()
    show_cost_summary()
    show_decision_guide()


if __name__ == "__main__":
    main()
