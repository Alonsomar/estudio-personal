"""Construcción de golden datasets para RAG fiscal.

Demuestra el proceso completo:
  1. Generación de items desde documentos del corpus (simulando fase manual)
  2. Validación de cobertura contra la matriz de dimensiones
  3. Análisis de balance y dificultad
  4. Cálculo de tamaño mínimo necesario para distintos escenarios
  5. Exportación en formato JSON versionado

Ejecutar con:
    uv run python 01-evals/code/eval-golden-dataset.py

No requiere API keys — genera un golden dataset de ejemplo a partir
del corpus existente.
"""

import json
import math
from dataclasses import dataclass, field, asdict
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
class GoldenItem:
    """Un item del golden dataset."""
    id: str
    query: str
    expected_answer: str
    expected_docs: list[str]
    difficulty: str           # easy, medium, hard
    query_type: str           # factual, numerico, entidad, multi-doc, scope
    doc_type: str             # decreto, circular, glosa, norma, ninguno
    requires_abstention: bool
    reasoning: str            # por qué esta es la respuesta correcta
    created_by: str
    version: int = 1


@dataclass
class CoverageCell:
    """Una celda de la matriz de cobertura."""
    doc_type: str
    query_type: str
    target: int
    actual: int = 0

    @property
    def covered(self) -> bool:
        return self.actual >= self.target


# ---------------------------------------------------------------------------
# Golden dataset de ejemplo
# ---------------------------------------------------------------------------

def build_golden_dataset() -> list[GoldenItem]:
    """Construye un golden dataset de ejemplo sobre el corpus chileno.

    Sigue la estrategia híbrida:
    - Fase manual simulada: items cuidadosamente diseñados
    - Cobertura de la matriz completa de doc_type × query_type
    - Distribución de dificultad calibrada
    """
    items = [
        # === CIRCULAR SII — IVA DIGITAL ===

        # Easy / factual
        GoldenItem("gd-001",
            "¿Qué ley introdujo el IVA a los servicios digitales en Chile?",
            "La Ley Nº 21.210, publicada el 24 de febrero de 2020, modificó el DL 825 para gravar servicios digitales de proveedores extranjeros.",
            ["circular-01-sii-iva-digital.txt"], "easy", "factual", "circular", False,
            "Respuesta directa en sección I de la Circular 42.", "alonso"),

        # Easy / numerico
        GoldenItem("gd-002",
            "¿Cuál es la tasa de IVA para servicios digitales de proveedores extranjeros en Chile?",
            "19%, según la sección II de la Circular Nº 42 del SII (2020).",
            ["circular-01-sii-iva-digital.txt"], "easy", "numerico", "circular", False,
            "Tasa explícita en sección II.", "alonso"),

        # Medium / factual
        GoldenItem("gd-003",
            "¿Qué indicios se usan para determinar si un servicio digital se utiliza en Chile?",
            "En orden de prelación: (1) dirección IP o geolocalización del dispositivo, (2) dirección del medio de pago, (3) dirección de facturación o contrato, (4) SIM del teléfono móvil.",
            ["circular-01-sii-iva-digital.txt"], "medium", "factual", "circular", False,
            "Requiere listar los 4 indicios en orden. Sección III.", "alonso"),

        # Medium / entidad
        GoldenItem("gd-004",
            "¿Quién es responsable del IVA cuando un servicio digital se contrata a través de una plataforma de intermediación?",
            "La plataforma de intermediación, en calidad de agente retenedor, sin perjuicio de su derecho a repetir contra el prestador efectivo.",
            ["circular-01-sii-iva-digital.txt"], "medium", "entidad", "circular", False,
            "Sección V de la Circular. Concepto de responsabilidad sustituta.", "alonso"),

        # Hard / factual
        GoldenItem("gd-005",
            "Un proveedor de SaaS con sede en Irlanda vende a empresas chilenas. ¿Debe registrarse ante el SII? ¿Cada cuánto declara?",
            "Sí, debe registrarse en el portal simplificado del SII (sección IV.a). Declara y paga trimestralmente, dentro de los primeros 20 días del mes siguiente al término del trimestre (sección IV.b).",
            ["circular-01-sii-iva-digital.txt"], "hard", "factual", "circular", False,
            "Requiere sintetizar dos obligaciones de sección IV.", "alonso"),

        # === DECRETO SUBVENCIÓN ESCOLAR ===

        # Easy / numerico
        GoldenItem("gd-006",
            "¿Cuántas USE recibe un alumno prioritario de 3º básico por subvención escolar preferencial?",
            "1,694 USE mensuales, según el Decreto Exento Nº 1.423 (literal a: alumnos de 1º a 6º básico).",
            ["decreto-01-subvencion-escolar.txt"], "easy", "numerico", "decreto", False,
            "3º básico cae en el rango 1º-6º del literal a).", "alonso"),

        # Medium / numerico
        GoldenItem("gd-007",
            "¿Cuál es la diferencia en USE entre un alumno prioritario de 5º básico y uno de 7º básico?",
            "0,564 USE. El alumno de 5º básico recibe 1,694 USE (literal a), el de 7º básico recibe 1,130 USE (literal b). Diferencia: 1,694 - 1,130 = 0,564 USE.",
            ["decreto-01-subvencion-escolar.txt"], "medium", "numerico", "decreto", False,
            "Requiere identificar los dos tramos y calcular la diferencia.", "alonso"),

        # Easy / entidad
        GoldenItem("gd-008",
            "¿Qué organismo determina anualmente la calidad de alumno prioritario?",
            "La Junta Nacional de Auxilio Escolar y Becas (JUNAEB), según el Artículo 2º del Decreto.",
            ["decreto-01-subvencion-escolar.txt"], "easy", "entidad", "decreto", False,
            "Mención directa en Art. 2.", "alonso"),

        # Hard / factual
        GoldenItem("gd-009",
            "¿Qué consecuencia enfrenta un establecimiento clasificado En Recuperación que no mejora sus resultados?",
            "Pierde el reconocimiento oficial del Estado si no mejora dentro de un plazo de cuatro años (Art. 5).",
            ["decreto-01-subvencion-escolar.txt"], "hard", "factual", "decreto", False,
            "Requiere conectar clasificación (Art. 4) con consecuencia (Art. 5).", "alonso"),

        # Medium / factual
        GoldenItem("gd-010",
            "¿Qué porcentaje mínimo de matrícula prioritaria necesita un establecimiento para acceder a la SEP?",
            "Al menos el 15% de su matrícula debe corresponder a alumnos prioritarios (Art. 1).",
            ["decreto-01-subvencion-escolar.txt"], "medium", "factual", "decreto", False,
            "Art. 1 del Decreto.", "alonso"),

        # === GLOSA PRESUPUESTARIA SALUD ===

        # Easy / numerico
        GoldenItem("gd-011",
            "¿Cuánto presupuesto se asigna al Programa Nacional de Inmunizaciones en 2024?",
            "$198.547.320 miles, según la Glosa 05, Asignación 104, Subtítulo 24 de la Partida 16.",
            ["glosa-01-presupuesto-salud.txt"], "easy", "numerico", "glosa", False,
            "Monto explícito antes de la Glosa 05.", "alonso"),

        # Medium / numerico
        GoldenItem("gd-012",
            "¿Cuánto es el Aporte Fiscal Libre de FONASA para 2024?",
            "$4.892.157.000 miles, según la Glosa 09, Capítulo 02, Programa 01 de la Partida 16.",
            ["glosa-01-presupuesto-salud.txt"], "medium", "numerico", "glosa", False,
            "Requiere navegar hasta Capítulo 02 del documento.", "alonso"),

        # Medium / factual
        GoldenItem("gd-013",
            "¿Qué información debe reportar trimestralmente el Ministerio de Salud sobre inmunizaciones?",
            "Debe informar a la Comisión Especial Mixta de Presupuestos: (i) dosis adquiridas por tipo, (ii) costo unitario promedio, (iii) cobertura por grupo etario, (iv) inventario en stock.",
            ["glosa-01-presupuesto-salud.txt"], "medium", "factual", "glosa", False,
            "Glosa 05, 4 ítems de reporte.", "alonso"),

        # Hard / numerico
        GoldenItem("gd-014",
            "¿Cuál es la razón máxima de beneficiarios por cama para que una comuna no sea considerada en déficit de oferta?",
            "1.000 personas por cama hospitalaria pública. Comunas que superan esa razón se consideran en déficit (Glosa 09).",
            ["glosa-01-presupuesto-salud.txt"], "hard", "numerico", "glosa", False,
            "Dato anidado dentro de la definición de déficit en Glosa 09.", "alonso"),

        # Hard / factual
        GoldenItem("gd-015",
            "¿Qué criterios definen equipamiento crítico según la Glosa 12 de Salud?",
            "Equipamiento con vida útil residual inferior a 2 años o con tasa de falla superior al 15% mensual.",
            ["glosa-01-presupuesto-salud.txt"], "hard", "factual", "glosa", False,
            "Definición técnica dentro de Glosa 12.", "alonso"),

        # Easy / factual
        GoldenItem("gd-016",
            "¿A qué programa corresponde el PRAIS en el presupuesto de Salud?",
            "Al Programa 01 de la Subsecretaría de Salud Pública, Asignación 112, con un presupuesto de $27.834.560 miles.",
            ["glosa-01-presupuesto-salud.txt"], "easy", "factual", "glosa", False,
            "Mención directa antes de Glosa 06.", "alonso"),

        # Multi-doc / glosa
        GoldenItem("gd-017",
            "¿Son sumables los presupuestos de inmunizaciones y equipamiento hospitalario en una misma cifra?",
            "No directamente. Son asignaciones diferentes: inmunizaciones ($198.547.320 miles) es transferencia corriente al sector privado (Subtítulo 24), mientras que equipamiento ($156.789.000 miles) es transferencia de capital (Subtítulo 25). Corresponden a diferentes clasificaciones presupuestarias.",
            ["glosa-01-presupuesto-salud.txt"], "hard", "multi-doc", "glosa", False,
            "Requiere entender clasificación presupuestaria. Subtítulos diferentes.", "alonso"),

        # === NORMA — LEY DE LOBBY ===

        # Easy / factual
        GoldenItem("gd-018",
            "¿Cuál es la diferencia entre lobby y gestión de intereses particulares según la Ley 20.730?",
            "El lobby es remunerado; la gestión de intereses particulares no es remunerada. Ambas buscan influir en decisiones de sujetos pasivos (Art. 2).",
            ["norma-01-ley-lobby.txt"], "easy", "factual", "norma", False,
            "Definiciones 1) y 2) del Art. 2.", "alonso"),

        # Easy / entidad
        GoldenItem("gd-019",
            "¿Qué autoridades son sujetos pasivos de la Ley de Lobby?",
            "Presidente, ministros, subsecretarios, jefes de servicio, directores regionales, intendentes, gobernadores, embajadores, consejeros de órganos constitucionales, diputados, senadores, alcaldes y concejales (Art. 3).",
            ["norma-01-ley-lobby.txt"], "easy", "entidad", "norma", False,
            "Lista directa del Art. 3.", "alonso"),

        # Medium / numerico
        GoldenItem("gd-020",
            "¿Cuál es el rango de multas por infracción a la Ley de Lobby?",
            "10 a 50 UTM. En caso de reincidencia, hasta 100 UTM (Art. 11).",
            ["norma-01-ley-lobby.txt"], "medium", "numerico", "norma", False,
            "Art. 11, dos tramos según reincidencia.", "alonso"),

        # Medium / factual
        GoldenItem("gd-021",
            "¿Qué debe publicar mensualmente un sujeto pasivo en el Registro de Agenda Pública?",
            "Dentro de los primeros 10 días de cada mes: (a) audiencias de lobby o gestión de intereses, (b) viajes en ejercicio de funciones, (c) donativos oficiales y protocolares (Art. 5).",
            ["norma-01-ley-lobby.txt"], "medium", "factual", "norma", False,
            "Art. 5, 3 ítems con plazo.", "alonso"),

        # Hard / entidad
        GoldenItem("gd-022",
            "¿Quién administra el registro público de lobbistas y qué información debe contener la inscripción?",
            "El Consejo para la Transparencia. La inscripción debe contener: individualización del lobbista, domicilio y contacto, e individualización de los representados. Se renueva anualmente (Art. 8).",
            ["norma-01-ley-lobby.txt"], "hard", "entidad", "norma", False,
            "Art. 8. Requiere sintetizar administrador + contenido + renovación.", "alonso"),

        # === MULTI-DOC ===

        GoldenItem("gd-023",
            "¿Un concejal que recibe una invitación de un proveedor de software educacional debe registrarla? ¿Bajo qué norma?",
            "Sí. Los concejales son sujetos pasivos de la Ley de Lobby (Art. 3.e, Ley 20.730) y deben registrar audiencias de lobby en el Registro de Agenda Pública (Art. 5). Si el proveedor es remunerado por un tercero para gestionar, es lobby; si actúa por cuenta propia, es gestión de intereses particulares.",
            ["norma-01-ley-lobby.txt"], "hard", "multi-doc", "norma", False,
            "Requiere aplicar definiciones del Art. 2 al caso concreto.", "alonso"),

        GoldenItem("gd-024",
            "¿Qué obligación trimestral comparten los prestadores de servicios digitales extranjeros y el Ministerio de Salud respecto a inmunizaciones?",
            "Ambos tienen obligaciones de reporte trimestral: los prestadores de servicios digitales deben declarar y pagar IVA trimestralmente (Circular 42, sección IV.b), y el Ministerio debe informar trimestralmente sobre el programa de inmunizaciones (Glosa 05, Partida 16). Son obligaciones independientes bajo normativas distintas.",
            ["circular-01-sii-iva-digital.txt", "glosa-01-presupuesto-salud.txt"],
            "hard", "multi-doc", "circular", False,
            "Cruza dos documentos para encontrar un patrón común.", "alonso"),

        # === SCOPE / ABSTENCIÓN ===

        GoldenItem("gd-025",
            "¿Cuál es la multa por infracción a la Ley de Transparencia de 2022?",
            "No es posible responder. El corpus no contiene la Ley de Transparencia. La Ley de Lobby (20.730) tiene multas de 10-50 UTM, pero es una norma distinta.",
            [], "hard", "scope", "ninguno", True,
            "La query mezcla dos leyes. El sistema debe identificar que no tiene la fuente.", "alonso"),

        GoldenItem("gd-026",
            "¿Qué dice el DFL 3 sobre subvenciones especiales para educación rural?",
            "No es posible responder. El corpus contiene el Decreto Exento Nº 1.423 sobre subvención escolar preferencial (Ley 20.248), pero no el DFL 3.",
            [], "medium", "scope", "ninguno", True,
            "Documento fuera del corpus. El sistema debe abstenerse.", "alonso"),

        GoldenItem("gd-027",
            "¿Cuál será el presupuesto de Salud para 2025?",
            "No es posible responder. El corpus solo contiene la Ley de Presupuestos 2024. No se dispone de información sobre el presupuesto 2025.",
            [], "easy", "scope", "ninguno", True,
            "Pregunta temporal fuera del alcance del corpus.", "alonso"),

        # === MÁS COBERTURA PARA CELDAS FALTANTES ===

        # Decreto / multi-doc
        GoldenItem("gd-028",
            "¿Las subvenciones escolares están afectas a IVA?",
            "La subvención escolar preferencial es una transferencia del Estado (DL 825 exime transferencias fiscales). Los servicios digitales sí están afectos a IVA al 19% (Circular 42). Son regímenes tributarios distintos.",
            ["decreto-01-subvencion-escolar.txt", "circular-01-sii-iva-digital.txt"],
            "hard", "multi-doc", "decreto", False,
            "Requiere conocimiento implícito de régimen tributario + cruce de documentos.", "alonso"),

        # Norma / numerico adicional
        GoldenItem("gd-029",
            "¿En qué plazo debe un sujeto pasivo registrar sus actuaciones en la Agenda Pública?",
            "Dentro de los primeros 10 días de cada mes, respecto de las actuaciones del mes inmediatamente anterior (Art. 5, Ley 20.730).",
            ["norma-01-ley-lobby.txt"], "easy", "numerico", "norma", False,
            "Plazo explícito en Art. 5.", "alonso"),

        # Glosa / entidad
        GoldenItem("gd-030",
            "¿Qué organismo elabora el ranking de priorización de equipamiento hospitalario?",
            "La Subsecretaría de Redes Asistenciales, debiendo informar a la Dirección de Presupuestos (Glosa 12).",
            ["glosa-01-presupuesto-salud.txt"], "medium", "entidad", "glosa", False,
            "Dos entidades mencionadas en Glosa 12.", "alonso"),
    ]
    return items


# ---------------------------------------------------------------------------
# Análisis de cobertura
# ---------------------------------------------------------------------------

DOC_TYPES = ["decreto", "circular", "glosa", "norma", "ninguno"]
QUERY_TYPES = ["factual", "numerico", "entidad", "multi-doc", "scope"]

# Targets mínimos por celda (de la teoría)
COVERAGE_TARGETS = {
    ("decreto", "factual"): 3, ("decreto", "numerico"): 3,
    ("decreto", "entidad"): 2, ("decreto", "multi-doc"): 1,
    ("decreto", "scope"): 1,
    ("circular", "factual"): 3, ("circular", "numerico"): 3,
    ("circular", "entidad"): 2, ("circular", "multi-doc"): 1,
    ("circular", "scope"): 1,
    ("glosa", "factual"): 2, ("glosa", "numerico"): 4,
    ("glosa", "entidad"): 1, ("glosa", "multi-doc"): 2,
    ("glosa", "scope"): 1,
    ("norma", "factual"): 3, ("norma", "numerico"): 2,
    ("norma", "entidad"): 2, ("norma", "multi-doc"): 1,
    ("norma", "scope"): 1,
    ("ninguno", "scope"): 3,
}


def analyze_coverage(items: list[GoldenItem]) -> list[CoverageCell]:
    """Analiza la cobertura del golden dataset contra la matriz objetivo."""
    cells: list[CoverageCell] = []

    for dt in DOC_TYPES:
        for qt in QUERY_TYPES:
            target = COVERAGE_TARGETS.get((dt, qt), 0)
            if target == 0:
                continue
            actual = sum(1 for i in items if i.doc_type == dt and i.query_type == qt)
            cells.append(CoverageCell(dt, qt, target, actual))

    return cells


def show_coverage_matrix(cells: list[CoverageCell]) -> None:
    """Muestra la matriz de cobertura."""
    table = Table(title="Matriz de cobertura: actual / objetivo")
    table.add_column("Doc type", style="bold")
    for qt in QUERY_TYPES:
        table.add_column(qt, justify="center")

    for dt in DOC_TYPES:
        row = [dt]
        for qt in QUERY_TYPES:
            cell = next((c for c in cells if c.doc_type == dt and c.query_type == qt), None)
            if cell is None:
                row.append("—")
            else:
                color = "green" if cell.covered else "red"
                row.append(f"[{color}]{cell.actual}/{cell.target}[/{color}]")
        table.add_row(*row)

    covered = sum(1 for c in cells if c.covered)
    total_cells = len(cells)
    table.add_section()
    table.add_row(
        "[bold]Cobertura[/bold]", "", "", "",
        f"[bold]{covered}/{total_cells} ({covered/total_cells:.0%})[/bold]", ""
    )

    console.print(table)


# ---------------------------------------------------------------------------
# Análisis de balance y dificultad
# ---------------------------------------------------------------------------

def show_difficulty_distribution(items: list[GoldenItem]) -> None:
    """Muestra distribución de dificultad."""
    console.print()
    table = Table(title="Distribución de dificultad")
    table.add_column("Dificultad", style="bold")
    table.add_column("Cuenta", justify="center")
    table.add_column("Proporción", justify="center")
    table.add_column("Objetivo", justify="center")
    table.add_column("", width=25)

    targets = {"easy": 0.30, "medium": 0.45, "hard": 0.25}
    total = len(items)

    for diff in ["easy", "medium", "hard"]:
        count = sum(1 for i in items if i.difficulty == diff)
        actual_pct = count / total
        target_pct = targets[diff]
        color = "green" if abs(actual_pct - target_pct) < 0.10 else "yellow"
        bar = "█" * int(actual_pct * 30)
        table.add_row(
            diff, str(count), f"{actual_pct:.0%}",
            f"{target_pct:.0%}", f"[{color}]{bar}[/{color}]"
        )

    console.print(table)


def show_doc_type_distribution(items: list[GoldenItem]) -> None:
    """Muestra distribución por tipo de documento."""
    console.print()
    table = Table(title="Distribución por tipo de documento")
    table.add_column("Doc type", style="bold")
    table.add_column("Cuenta", justify="center")
    table.add_column("Proporción", justify="center")
    table.add_column("", width=25)

    total = len(items)
    for dt in DOC_TYPES:
        count = sum(1 for i in items if i.doc_type == dt)
        if count == 0:
            continue
        pct = count / total
        bar = "█" * int(pct * 30)
        table.add_row(dt, str(count), f"{pct:.0%}", bar)

    console.print(table)


def show_abstention_stats(items: list[GoldenItem]) -> None:
    """Muestra estadísticas de items que requieren abstención."""
    console.print()
    abs_items = [i for i in items if i.requires_abstention]
    total = len(items)

    table = Table(title="Items de abstención (scope queries)")
    table.add_column("ID", style="bold")
    table.add_column("Query", width=50)
    table.add_column("Dificultad")

    for item in abs_items:
        table.add_row(item.id, item.query, item.difficulty)

    table.add_section()
    table.add_row("", f"[bold]Total: {len(abs_items)}/{total} ({len(abs_items)/total:.0%})[/bold]", "")

    console.print(table)


# ---------------------------------------------------------------------------
# Cálculo de tamaño mínimo
# ---------------------------------------------------------------------------

def sample_size_for_difference(p1: float, p2: float,
                                alpha: float = 0.05,
                                power: float = 0.80) -> int:
    """Calcula n mínimo para detectar diferencia entre dos proporciones.

    Usa aproximación normal (z-test de dos proporciones, dos colas).
    """
    from math import ceil

    # Valores z para alpha y beta
    z_alpha = 1.96 if alpha == 0.05 else 2.576  # 95% o 99%
    z_beta = 0.842 if power == 0.80 else 1.282  # 80% o 90%

    numerator = (z_alpha + z_beta) ** 2 * (p1 * (1 - p1) + p2 * (1 - p2))
    denominator = (p1 - p2) ** 2

    return ceil(numerator / denominator)


def ci_width(n: int, p: float = 0.5) -> float:
    """Calcula el ancho del IC 95% para una proporción."""
    return 2 * 1.96 * math.sqrt(p * (1 - p) / n)


def show_sample_size_analysis() -> None:
    """Muestra análisis de tamaño de muestra."""
    console.print()

    # Tabla 1: n para detectar diferencias
    table = Table(title="Tamaño mínimo para detectar mejoras (80% poder, 95% confianza)")
    table.add_column("Baseline", style="bold", justify="center")
    table.add_column("Mejora 3pp", justify="center")
    table.add_column("Mejora 5pp", justify="center")
    table.add_column("Mejora 10pp", justify="center")

    for baseline in [0.60, 0.70, 0.80]:
        row = [f"{baseline:.0%}"]
        for delta in [0.03, 0.05, 0.10]:
            n = sample_size_for_difference(baseline, baseline + delta)
            row.append(str(n))
        table.add_row(*row)

    console.print(table)

    # Tabla 2: ancho del IC según n
    console.print()
    table2 = Table(title="Precisión del IC 95% según tamaño del golden dataset")
    table2.add_column("n", style="bold", justify="center")
    table2.add_column("Ancho IC (peor caso)", justify="center")
    table2.add_column("Interpretación", width=40)

    interpretations = {
        50: "No distingues 65% de 79% → insuficiente",
        100: "Detectas cambios de ~10pp → mínimo útil",
        200: "Detectas cambios de ~7pp → recomendado",
        300: "Detectas cambios de ~6pp → sólido",
        500: "Detectas cambios de ~4pp → ideal",
    }

    for n in [50, 100, 200, 300, 500]:
        width = ci_width(n)
        interp = interpretations[n]
        table2.add_row(str(n), f"±{width/2:.1%}", interp)

    console.print(table2)


# ---------------------------------------------------------------------------
# Exportación
# ---------------------------------------------------------------------------

def export_golden_dataset(items: list[GoldenItem]) -> Path:
    """Exporta el golden dataset como JSON."""
    output_dir = get_project_root() / "01-evals" / "examples"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "golden-dataset-rag-fiscal.json"

    data = {
        "metadata": {
            "name": "Golden dataset RAG fiscal chileno",
            "version": 1,
            "created_at": "2026-05-25",
            "total_items": len(items),
            "description": "Dataset de evaluación para sistema RAG sobre corpus regulatorio y fiscal chileno.",
        },
        "items": [asdict(i) for i in items],
    }

    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------

def show_dataset_quality_scorecard(items: list[GoldenItem],
                                     cells: list[CoverageCell]) -> None:
    """Scorecard de calidad del golden dataset."""
    console.print()

    total = len(items)
    covered = sum(1 for c in cells if c.covered)
    total_cells = len(cells)
    coverage_pct = covered / total_cells

    # Balance: max/min ratio por doc_type
    doc_counts = {}
    for dt in DOC_TYPES:
        c = sum(1 for i in items if i.doc_type == dt)
        if c > 0:
            doc_counts[dt] = c
    balance_ratio = max(doc_counts.values()) / min(doc_counts.values())

    # Dificultad
    hard_pct = sum(1 for i in items if i.difficulty == "hard") / total

    # Abstención
    abstention_pct = sum(1 for i in items if i.requires_abstention) / total

    table = Table(title="Scorecard de calidad del golden dataset")
    table.add_column("Métrica", style="bold", width=30)
    table.add_column("Valor", justify="center", width=15)
    table.add_column("Umbral", justify="center", width=15)
    table.add_column("Estado", justify="center", width=10)

    def check(val: float, threshold: float, higher_better: bool = True) -> str:
        if higher_better:
            return "[green]OK[/green]" if val >= threshold else "[red]BAJO[/red]"
        return "[green]OK[/green]" if val <= threshold else "[yellow]ALTO[/yellow]"

    table.add_row("Total items", str(total), "≥100", check(total, 100))
    table.add_row("Cobertura de matriz", f"{coverage_pct:.0%}", "≥80%", check(coverage_pct, 0.80))
    table.add_row("Balance (max/min ratio)", f"{balance_ratio:.1f}:1", "≤3:1", check(balance_ratio, 3, False))
    table.add_row("% items hard", f"{hard_pct:.0%}", "20-30%",
                   "[green]OK[/green]" if 0.20 <= hard_pct <= 0.30 else "[yellow]AJUSTAR[/yellow]")
    table.add_row("% items abstención", f"{abstention_pct:.0%}", "~10%",
                   "[green]OK[/green]" if 0.05 <= abstention_pct <= 0.15 else "[yellow]AJUSTAR[/yellow]")

    console.print(table)

    # Recomendaciones
    recs = []
    if total < 100:
        recs.append(f"Expandir de {total} a ≥100 items (usar estrategia LLM-asistida)")
    if coverage_pct < 0.80:
        gaps = [f"{c.doc_type}/{c.query_type}" for c in cells if not c.covered]
        recs.append(f"Cubrir celdas faltantes: {', '.join(gaps[:5])}")
    if balance_ratio > 3:
        recs.append("Balancear distribución por tipo de documento")

    if recs:
        console.print()
        console.print(Panel(
            "[bold]Recomendaciones para mejorar el golden dataset:[/bold]\n\n" +
            "\n".join(f"  {i+1}. {r}" for i, r in enumerate(recs)),
            style="yellow",
        ))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    console.print(Panel(
        "[bold]Construcción de Golden Dataset para RAG fiscal[/bold]\n\n"
        "Genera un golden dataset de 30 items sobre el corpus chileno,\n"
        "analiza cobertura, balance, dificultad y tamaño mínimo,\n"
        "y exporta en formato JSON versionado.",
        style="bold blue",
    ))

    # Construir dataset
    items = build_golden_dataset()
    console.print(f"\n[bold]Golden dataset:[/bold] {len(items)} items generados\n")

    # Cobertura
    cells = analyze_coverage(items)
    show_coverage_matrix(cells)

    # Distribuciones
    show_difficulty_distribution(items)
    show_doc_type_distribution(items)
    show_abstention_stats(items)

    # Tamaño mínimo
    show_sample_size_analysis()

    # Scorecard
    show_dataset_quality_scorecard(items, cells)

    # Exportar
    output_path = export_golden_dataset(items)
    console.print(f"\n[dim]Golden dataset exportado a: {output_path}[/dim]")


if __name__ == "__main__":
    main()
