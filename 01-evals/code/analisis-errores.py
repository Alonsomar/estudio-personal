"""Análisis de errores: protocolo de revisión y análisis de patrones.

Toma outputs de un RAG simulado, los anota con la taxonomía de errores
(R1-R5, G1-G6, B1-B4), y produce:
  1. Distribución de frecuencias por tipo de error
  2. Análisis de Pareto (frecuencia × severidad)
  3. Co-ocurrencia de errores
  4. Segmentación por tipo de documento y tipo de query
  5. Exporta anotaciones en formato JSONL

Ejecutar con:
    uv run python 01-evals/code/analisis-errores.py

No requiere API keys — usa datos simulados para ilustrar el protocolo.
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

SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}

ERROR_CATALOG = {
    "OK":  {"name": "Correcto", "category": "ok", "severity": None},
    "R1":  {"name": "Total miss", "category": "retrieval", "severity": "critical"},
    "R2":  {"name": "Partial miss", "category": "retrieval", "severity": "medium"},
    "R3":  {"name": "Wrong chunk", "category": "retrieval", "severity": "high"},
    "R4":  {"name": "Stale retrieval", "category": "retrieval", "severity": "medium"},
    "R5":  {"name": "Distractor dominance", "category": "retrieval", "severity": "medium"},
    "G1":  {"name": "Hallucination factual", "category": "generation", "severity": "critical"},
    "G2":  {"name": "Hallucination normativa", "category": "generation", "severity": "critical"},
    "G3":  {"name": "Misreading", "category": "generation", "severity": "high"},
    "G4":  {"name": "Omisión material", "category": "generation", "severity": "high"},
    "G5":  {"name": "Fusión indebida", "category": "generation", "severity": "high"},
    "G6":  {"name": "Respuesta genérica", "category": "generation", "severity": "medium"},
    "B1":  {"name": "Falsa confianza", "category": "behavior", "severity": "critical"},
    "B2":  {"name": "Abstinencia excesiva", "category": "behavior", "severity": "medium"},
    "B3":  {"name": "Format failure", "category": "behavior", "severity": "low"},
    "B4":  {"name": "Inconsistencia", "category": "behavior", "severity": "high"},
}


@dataclass
class AnnotatedOutput:
    """Un output del RAG con anotación de error."""
    query_id: str
    query_text: str
    generated_answer: str
    correct_answer: str
    retrieved_docs: list[str]
    correct_doc: str
    error_codes: list[str]  # puede tener múltiples errores
    doc_type: str            # decreto, circular, glosa, norma
    query_type: str          # factual, numerico, entidad, multi-doc
    annotator: str


# ---------------------------------------------------------------------------
# Dataset simulado de 30 outputs anotados
# ---------------------------------------------------------------------------

def build_annotated_dataset() -> list[AnnotatedOutput]:
    """Genera un dataset simulado de 30 outputs anotados.

    Los datos ilustran patrones realistas:
    - Queries numéricas fallan más que factuales
    - Glosas presupuestarias son más difíciles que decretos
    - R1 co-ocurre con G1 y B1
    """
    outputs = [
        # --- OK: 18/30 = 60% ---
        AnnotatedOutput("q001", "¿Cuál es la tasa de IVA para servicios digitales?",
            "19%, según Circular Nº 42 del SII", "19%", ["circular-01-sii-iva-digital.txt"],
            "circular-01-sii-iva-digital.txt", ["OK"], "circular", "numerico", "alonso"),
        AnnotatedOutput("q002", "¿Quiénes son sujetos pasivos de la Ley de Lobby?",
            "El Presidente, ministros, subsecretarios...", "Art. 3 Ley 20.730",
            ["norma-01-ley-lobby.txt"], "norma-01-ley-lobby.txt", ["OK"], "norma", "entidad", "alonso"),
        AnnotatedOutput("q003", "¿Qué porcentaje del AFL debe FONASA destinar a libre elección?",
            "Al menos 5%", "5%, Glosa 09", ["glosa-01-presupuesto-salud.txt"],
            "glosa-01-presupuesto-salud.txt", ["OK"], "glosa", "numerico", "alonso"),
        AnnotatedOutput("q004", "¿Qué tipo de establecimientos pueden acceder a la SEP?",
            "Establecimientos subvencionados con al menos 15% de matrícula prioritaria",
            "Art. 1 del Decreto", ["decreto-01-subvencion-escolar.txt"],
            "decreto-01-subvencion-escolar.txt", ["OK"], "decreto", "factual", "alonso"),
        AnnotatedOutput("q005", "¿Qué criterios usa JUNAEB para determinar alumnos prioritarios?",
            "Chile Solidario, tramo A de FONASA, tercil más vulnerable de la FPS",
            "Art. 2 del Decreto", ["decreto-01-subvencion-escolar.txt"],
            "decreto-01-subvencion-escolar.txt", ["OK"], "decreto", "entidad", "alonso"),
        AnnotatedOutput("q006", "¿Cuál es la multa por infracción a la Ley de Lobby?",
            "10 a 50 UTM, hasta 100 UTM en reincidencia", "Art. 11 Ley 20.730",
            ["norma-01-ley-lobby.txt"], "norma-01-ley-lobby.txt", ["OK"], "norma", "numerico", "alonso"),
        AnnotatedOutput("q007", "¿Qué servicios digitales están gravados con IVA?",
            "Intermediación, contenido digital, software/cloud, publicidad",
            "Sección II Circular 42", ["circular-01-sii-iva-digital.txt"],
            "circular-01-sii-iva-digital.txt", ["OK"], "circular", "factual", "alonso"),
        AnnotatedOutput("q008", "¿Qué áreas debe cubrir el Plan de Mejoramiento Educativo?",
            "Gestión del currículum, liderazgo escolar, convivencia, gestión de recursos",
            "Art. 3 del Decreto", ["decreto-01-subvencion-escolar.txt"],
            "decreto-01-subvencion-escolar.txt", ["OK"], "decreto", "entidad", "alonso"),
        AnnotatedOutput("q009", "¿Cada cuánto declaran IVA los prestadores extranjeros?",
            "Trimestralmente, dentro de los primeros 20 días del mes siguiente",
            "Sección IV Circular 42", ["circular-01-sii-iva-digital.txt"],
            "circular-01-sii-iva-digital.txt", ["OK"], "circular", "factual", "alonso"),
        AnnotatedOutput("q010", "¿Qué debe publicar un sujeto pasivo en el Registro de Agenda Pública?",
            "Audiencias de lobby, viajes en funciones, donativos oficiales",
            "Art. 5 Ley 20.730", ["norma-01-ley-lobby.txt"],
            "norma-01-ley-lobby.txt", ["OK"], "norma", "factual", "alonso"),
        AnnotatedOutput("q011", "¿Qué es equipamiento crítico según la glosa 12?",
            "Aquel con vida útil residual <2 años o tasa de falla >15% mensual",
            "Glosa 12, Cap 02", ["glosa-01-presupuesto-salud.txt"],
            "glosa-01-presupuesto-salud.txt", ["OK"], "glosa", "factual", "alonso"),
        AnnotatedOutput("q012", "¿Qué categorías de desempeño establece la Agencia de Calidad?",
            "Autónomo, Emergente y En Recuperación", "Art. 4 del Decreto",
            ["decreto-01-subvencion-escolar.txt"], "decreto-01-subvencion-escolar.txt",
            ["OK"], "decreto", "entidad", "alonso"),
        AnnotatedOutput("q013", "¿Cómo se determina si un servicio digital se usa en Chile?",
            "IP del dispositivo, dirección de medio de pago, dirección de facturación, SIM",
            "Sección III Circular 42", ["circular-01-sii-iva-digital.txt"],
            "circular-01-sii-iva-digital.txt", ["OK"], "circular", "factual", "alonso"),
        AnnotatedOutput("q014", "¿Qué información debe contener la inscripción de un lobbista?",
            "Individualización, domicilio, datos de contacto, representados",
            "Art. 8 Ley 20.730", ["norma-01-ley-lobby.txt"],
            "norma-01-ley-lobby.txt", ["OK"], "norma", "factual", "alonso"),
        AnnotatedOutput("q015", "¿Cuánto recibe un alumno preferente adicional sobre la subvención base?",
            "0,226 USE", "Decreto, sección CONSIDERANDO, literal d)",
            ["decreto-01-subvencion-escolar.txt"], "decreto-01-subvencion-escolar.txt",
            ["OK"], "decreto", "numerico", "alonso"),
        AnnotatedOutput("q016", "¿Qué es el PRAIS?",
            "Programa de Reparación y Atención Integral de Salud, Ley 19.123",
            "Glosa 06", ["glosa-01-presupuesto-salud.txt"],
            "glosa-01-presupuesto-salud.txt", ["OK"], "glosa", "factual", "alonso"),
        AnnotatedOutput("q017", "¿Quién es responsable del IVA en servicios por plataforma?",
            "La plataforma de intermediación como agente retenedor",
            "Sección V Circular 42", ["circular-01-sii-iva-digital.txt"],
            "circular-01-sii-iva-digital.txt", ["OK"], "circular", "entidad", "alonso"),
        AnnotatedOutput("q018", "¿Qué pasa con establecimientos En Recuperación que no mejoran?",
            "Pierden el reconocimiento oficial del Estado en 4 años",
            "Art. 5 del Decreto", ["decreto-01-subvencion-escolar.txt"],
            "decreto-01-subvencion-escolar.txt", ["OK"], "decreto", "factual", "alonso"),

        # --- R1 + G1 (retrieval miss → hallucination): 3/30 = 10% ---
        AnnotatedOutput("q019", "¿Cuánto presupuesto se asigna al Programa de Inmunizaciones?",
            "Aproximadamente $150.000.000 miles", "$198.547.320 miles, Glosa 05",
            ["norma-01-ley-lobby.txt"], "glosa-01-presupuesto-salud.txt",
            ["R1", "G1"], "glosa", "numerico", "alonso"),
        AnnotatedOutput("q020", "¿Cuánto es el Aporte Fiscal Libre de FONASA?",
            "El aporte fiscal asciende a $3.500.000.000 miles",
            "$4.892.157.000 miles, Glosa 09", ["decreto-01-subvencion-escolar.txt"],
            "glosa-01-presupuesto-salud.txt", ["R1", "G1"], "glosa", "numerico", "alonso"),
        AnnotatedOutput("q021", "¿Cuánto se destina a equipamiento hospitalario?",
            "Se destinan $120.000.000 miles a equipamiento",
            "$156.789.000 miles, Glosa 12", ["circular-01-sii-iva-digital.txt"],
            "glosa-01-presupuesto-salud.txt", ["R1", "G1"], "glosa", "numerico", "alonso"),

        # --- R3 (wrong chunk): 2/30 = 7% ---
        AnnotatedOutput("q022", "¿Cuánto recibe un alumno prioritario de 7º básico?",
            "1,694 USE mensuales", "1,130 USE (no 1,694)",
            ["decreto-01-subvencion-escolar.txt"], "decreto-01-subvencion-escolar.txt",
            ["R3", "G3"], "decreto", "numerico", "alonso"),
        AnnotatedOutput("q023", "¿Cuál es la subvención para alumnos prioritarios de media?",
            "1,694 USE por alumno", "1,130 USE",
            ["decreto-01-subvencion-escolar.txt"], "decreto-01-subvencion-escolar.txt",
            ["R3", "G3"], "decreto", "numerico", "alonso"),

        # --- B1 (falsa confianza): 2/30 = 7% ---
        AnnotatedOutput("q024", "¿Cuál es la multa por infracción a la Ley de Transparencia de 2022?",
            "Las infracciones se sancionan con multas de 20 a 100 UTM",
            "Fuera de scope — la pregunta mezcla leyes", ["norma-01-ley-lobby.txt"],
            "ninguno", ["B1", "G1"], "norma", "factual", "alonso"),
        AnnotatedOutput("q025", "¿Qué dice el DFL 3 sobre subvenciones especiales?",
            "El DFL 3 establece que las subvenciones especiales...",
            "Fuera de scope — el corpus no tiene el DFL 3",
            ["decreto-01-subvencion-escolar.txt"], "ninguno",
            ["B1"], "decreto", "factual", "alonso"),

        # --- G4 (omisión material): 2/30 = 7% ---
        AnnotatedOutput("q026", "¿Quiénes deben pagar IVA por servicios digitales?",
            "Los proveedores de servicios digitales deben pagar IVA del 19%",
            "Solo proveedores NO domiciliados en Chile", ["circular-01-sii-iva-digital.txt"],
            "circular-01-sii-iva-digital.txt", ["G4"], "circular", "factual", "alonso"),
        AnnotatedOutput("q027", "¿Cómo se clasifica un alumno prioritario?",
            "Mediante la Ficha de Protección Social",
            "Hay 3 criterios en orden de prelación, no solo uno",
            ["decreto-01-subvencion-escolar.txt"], "decreto-01-subvencion-escolar.txt",
            ["G4"], "decreto", "factual", "alonso"),

        # --- G6 (respuesta genérica): 1/30 = 3% ---
        AnnotatedOutput("q028", "¿Cómo funciona la fiscalización de la SEP?",
            "La fiscalización depende de varios factores y organismos competentes",
            "La Agencia de Calidad clasifica en Autónomo/Emergente/En Recuperación",
            ["decreto-01-subvencion-escolar.txt"], "decreto-01-subvencion-escolar.txt",
            ["G6"], "decreto", "factual", "alonso"),

        # --- B3 (format failure): 1/30 = 3% ---
        AnnotatedOutput("q029", "¿Qué dice la glosa sobre inmunizaciones?",
            "Se financian vacunas COVID, influenza y calendario infantil, con informes trimestrales",
            "Correcto pero no cita Glosa 05 ni Partida 16",
            ["glosa-01-presupuesto-salud.txt"], "glosa-01-presupuesto-salud.txt",
            ["B3"], "glosa", "factual", "alonso"),

        # --- G5 (fusión indebida): 1/30 = 3% ---
        AnnotatedOutput("q030", "¿Cuánto gasta Salud en inmunizaciones y equipamiento?",
            "El presupuesto total de Salud para estos programas es de $355.336.320 miles",
            "Son dos asignaciones separadas: $198.547.320 y $156.789.000. No deben sumarse sin contexto",
            ["glosa-01-presupuesto-salud.txt"], "glosa-01-presupuesto-salud.txt",
            ["G5"], "glosa", "multi-doc", "alonso"),
    ]
    return outputs


# ---------------------------------------------------------------------------
# Análisis
# ---------------------------------------------------------------------------

def frequency_distribution(outputs: list[AnnotatedOutput]) -> dict[str, int]:
    """Cuenta la frecuencia de cada código de error."""
    counts: dict[str, int] = {}
    for o in outputs:
        for code in o.error_codes:
            counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def show_frequency_table(counts: dict[str, int], total: int) -> None:
    """Muestra tabla de distribución de frecuencias."""
    table = Table(title=f"Distribución de errores ({total} outputs anotados)")
    table.add_column("Código", style="bold", width=6)
    table.add_column("Nombre", width=25)
    table.add_column("Categoría", width=12)
    table.add_column("Severidad", width=10)
    table.add_column("Cuenta", justify="center", width=8)
    table.add_column("Proporción", justify="center", width=10)
    table.add_column("Barra", width=20)

    for code, count in counts.items():
        info = ERROR_CATALOG[code]
        pct = count / total
        bar_len = int(pct * 40)
        bar = "█" * bar_len

        severity = info["severity"] or "—"
        sev_color = {
            "critical": "red", "high": "yellow",
            "medium": "cyan", "low": "blue",
        }.get(severity, "white")

        table.add_row(
            code,
            info["name"],
            info["category"],
            f"[{sev_color}]{severity}[/{sev_color}]",
            str(count),
            f"{pct:.0%}",
            f"[{sev_color}]{bar}[/{sev_color}]",
        )

    console.print(table)


def show_pareto(counts: dict[str, int], total: int) -> None:
    """Análisis de Pareto: frecuencia × severidad."""
    console.print()

    # Calcular impact score: frecuencia × peso de severidad
    impact: list[tuple[str, float]] = []
    for code, count in counts.items():
        if code == "OK":
            continue
        info = ERROR_CATALOG[code]
        sev_weight = SEVERITY_ORDER.get(info["severity"], 0)
        score = (count / total) * sev_weight
        impact.append((code, score))

    impact.sort(key=lambda x: -x[1])

    table = Table(title="Análisis de Pareto: impacto = frecuencia × severidad")
    table.add_column("Código", style="bold")
    table.add_column("Nombre", width=25)
    table.add_column("Freq", justify="center")
    table.add_column("Severidad", justify="center")
    table.add_column("Impacto", justify="center")
    table.add_column("Acumulado", justify="center")
    table.add_column("", width=20)

    total_impact = sum(s for _, s in impact)
    cumulative = 0.0

    for code, score in impact:
        info = ERROR_CATALOG[code]
        cumulative += score
        cum_pct = cumulative / total_impact if total_impact > 0 else 0

        marker = "← 80%" if cum_pct >= 0.8 and (cumulative - score) / total_impact < 0.8 else ""
        bar_len = int((score / total_impact) * 30) if total_impact > 0 else 0

        table.add_row(
            code,
            info["name"],
            f"{counts[code]}/{total}",
            info["severity"],
            f"{score:.3f}",
            f"{cum_pct:.0%}",
            "█" * bar_len + f" {marker}",
        )

    console.print(table)


def show_cooccurrence(outputs: list[AnnotatedOutput]) -> None:
    """Muestra co-ocurrencia de errores."""
    console.print()

    # Encontrar outputs con múltiples errores
    multi_error = [o for o in outputs if len(o.error_codes) > 1 and "OK" not in o.error_codes]

    table = Table(title="Co-ocurrencia de errores")
    table.add_column("Query", width=45)
    table.add_column("Errores", width=20)
    table.add_column("Patrón", width=30)

    patterns: dict[str, int] = {}
    for o in multi_error:
        codes = " + ".join(o.error_codes)
        patterns[codes] = patterns.get(codes, 0) + 1
        table.add_row(
            o.query_text[:42] + "..." if len(o.query_text) > 45 else o.query_text,
            codes,
            _explain_pattern(o.error_codes),
        )

    console.print(table)

    # Resumen de patrones
    console.print()
    ptable = Table(title="Patrones de co-ocurrencia")
    ptable.add_column("Patrón", style="bold")
    ptable.add_column("Frecuencia", justify="center")
    ptable.add_column("Implicación", width=40)

    pattern_implications = {
        "R1 + G1": "Retrieval miss causa hallucination → priorizar retrieval",
        "R3 + G3": "Wrong chunk causa misreading → mejorar chunking",
        "B1 + G1": "Falsa confianza + hallucination → calibrar abstención",
    }
    for pattern, count in sorted(patterns.items(), key=lambda x: -x[1]):
        impl = pattern_implications.get(pattern, "Investigar causalidad")
        ptable.add_row(pattern, str(count), impl)

    console.print(ptable)


def _explain_pattern(codes: list[str]) -> str:
    if "R1" in codes and "G1" in codes:
        return "Sin doc → LLM inventa"
    if "R3" in codes and "G3" in codes:
        return "Chunk equivocado → lee mal"
    if "B1" in codes and "G1" in codes:
        return "Fuera de scope → inventa con confianza"
    return "Patrón a investigar"


def show_segmentation(outputs: list[AnnotatedOutput]) -> None:
    """Segmentación por tipo de documento y tipo de query."""
    console.print()

    # Por tipo de doc
    doc_stats: dict[str, dict[str, int]] = {}
    for o in outputs:
        if o.doc_type not in doc_stats:
            doc_stats[o.doc_type] = {"total": 0, "ok": 0}
        doc_stats[o.doc_type]["total"] += 1
        if o.error_codes == ["OK"]:
            doc_stats[o.doc_type]["ok"] += 1

    table = Table(title="Segmentación por tipo de documento")
    table.add_column("Tipo de doc", style="bold")
    table.add_column("Total", justify="center")
    table.add_column("OK", justify="center")
    table.add_column("Accuracy", justify="center")
    table.add_column("", width=20)

    for doc_type in sorted(doc_stats.keys()):
        s = doc_stats[doc_type]
        acc = s["ok"] / s["total"]
        color = "green" if acc >= 0.7 else "yellow" if acc >= 0.5 else "red"
        bar = "█" * int(acc * 20)
        table.add_row(doc_type, str(s["total"]), str(s["ok"]),
                       f"[{color}]{acc:.0%}[/{color}]", f"[{color}]{bar}[/{color}]")

    console.print(table)

    # Por tipo de query
    query_stats: dict[str, dict[str, int]] = {}
    for o in outputs:
        if o.query_type not in query_stats:
            query_stats[o.query_type] = {"total": 0, "ok": 0}
        query_stats[o.query_type]["total"] += 1
        if o.error_codes == ["OK"]:
            query_stats[o.query_type]["ok"] += 1

    console.print()
    table2 = Table(title="Segmentación por tipo de query")
    table2.add_column("Tipo de query", style="bold")
    table2.add_column("Total", justify="center")
    table2.add_column("OK", justify="center")
    table2.add_column("Accuracy", justify="center")
    table2.add_column("", width=20)

    for qt in sorted(query_stats.keys()):
        s = query_stats[qt]
        acc = s["ok"] / s["total"]
        color = "green" if acc >= 0.7 else "yellow" if acc >= 0.5 else "red"
        bar = "█" * int(acc * 20)
        table2.add_row(qt, str(s["total"]), str(s["ok"]),
                        f"[{color}]{acc:.0%}[/{color}]", f"[{color}]{bar}[/{color}]")

    console.print(table2)


def show_sample_size_table() -> None:
    """Tabla de probabilidad de observar errores según tamaño de muestra."""
    console.print()
    table = Table(title="P(observar ≥1 error) según frecuencia real y tamaño de muestra")
    table.add_column("Frecuencia real", style="bold")
    for n in [50, 100, 150, 200]:
        table.add_column(f"n={n}", justify="center")

    for p in [0.10, 0.05, 0.02, 0.01]:
        row = [f"{p:.0%}"]
        for n in [50, 100, 150, 200]:
            prob = 1 - (1 - p) ** n
            row.append(f"{prob:.1%}")
        table.add_row(*row)

    console.print(table)
    console.print("[dim]Fórmula: P(X ≥ 1) = 1 - (1-p)^n  (distribución binomial)[/dim]")


def export_annotations(outputs: list[AnnotatedOutput]) -> Path:
    """Exporta anotaciones en formato JSONL."""
    output_dir = get_project_root() / "01-evals" / "examples"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "anotaciones-errores.jsonl"

    with output_path.open("w", encoding="utf-8") as f:
        for o in outputs:
            record = {
                "query_id": o.query_id,
                "query_text": o.query_text,
                "generated_answer": o.generated_answer,
                "correct_answer": o.correct_answer,
                "retrieved_docs": o.retrieved_docs,
                "correct_doc": o.correct_doc,
                "error_codes": o.error_codes,
                "doc_type": o.doc_type,
                "query_type": o.query_type,
                "annotator": o.annotator,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return output_path


def show_key_findings(outputs: list[AnnotatedOutput]) -> None:
    """Resumen de hallazgos clave del análisis."""
    total = len(outputs)
    ok = sum(1 for o in outputs if o.error_codes == ["OK"])

    # Queries numéricas
    num_total = sum(1 for o in outputs if o.query_type == "numerico")
    num_ok = sum(1 for o in outputs if o.query_type == "numerico" and o.error_codes == ["OK"])

    # Glosas
    glosa_total = sum(1 for o in outputs if o.doc_type == "glosa")
    glosa_ok = sum(1 for o in outputs if o.doc_type == "glosa" and o.error_codes == ["OK"])

    console.print()
    console.print(Panel(
        f"[bold]Hallazgos clave del análisis de errores[/bold]\n\n"
        f"1. [bold]Accuracy general:[/bold] {ok}/{total} = {ok/total:.0%}\n\n"
        f"2. [bold]Patrón dominante:[/bold] R1→G1 (retrieval miss → hallucination)\n"
        f"   Aparece en {sum(1 for o in outputs if 'R1' in o.error_codes)}/{total} outputs.\n"
        f"   Mejorar el retriever reduciría ~3 tipos de error simultáneamente.\n\n"
        f"3. [bold]Queries numéricas:[/bold] {num_ok}/{num_total} = "
        f"{num_ok/num_total:.0%} accuracy\n"
        f"   vs queries factuales/entidad que son significativamente mejores.\n"
        f"   Los números son frágiles — requieren extracción precisa del chunk.\n\n"
        f"4. [bold]Glosas presupuestarias:[/bold] {glosa_ok}/{glosa_total} = "
        f"{glosa_ok/glosa_total:.0%} accuracy\n"
        f"   El peor tipo de documento. Estructura tabular difícil de chunkear.\n\n"
        f"5. [bold]Implicación para golden dataset:[/bold]\n"
        f"   - Sobrerrepresentar queries numéricas sobre glosas\n"
        f"   - Incluir queries de scope (fuera del corpus) para testear abstención\n"
        f"   - Incluir queries que requieran discriminar entre chunks similares",
        style="bold yellow",
    ))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    console.print(Panel(
        "[bold]Análisis de errores: protocolo de revisión[/bold]\n\n"
        "Simula el proceso de anotar 30 outputs de un RAG fiscal\n"
        "con la taxonomía de errores R1-R5, G1-G6, B1-B4.\n"
        "Produce distribución, Pareto, co-ocurrencia y segmentación.",
        style="bold blue",
    ))

    outputs = build_annotated_dataset()
    total = len(outputs)
    counts = frequency_distribution(outputs)

    # 1. Distribución
    show_frequency_table(counts, total)

    # 2. Pareto
    show_pareto(counts, total)

    # 3. Co-ocurrencia
    show_cooccurrence(outputs)

    # 4. Segmentación
    show_segmentation(outputs)

    # 5. Tabla de tamaño de muestra
    show_sample_size_table()

    # 6. Hallazgos clave
    show_key_findings(outputs)

    # 7. Exportar
    output_path = export_annotations(outputs)
    console.print(f"\n[dim]Anotaciones exportadas a: {output_path}[/dim]")


if __name__ == "__main__":
    main()
