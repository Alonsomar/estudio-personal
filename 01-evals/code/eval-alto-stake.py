"""Evals para dominios alto-stake: legal y fiscal chileno.

Implementa evaluaciones específicas para RAG sobre normativa:
  1. Verificación de citas normativas (4 niveles)
  2. Completitud de obligaciones fiscales
  3. Abstención calibrada (missed vs false)
  4. Precisión numérica (montos, plazos, tasas)
  5. Formato y estructura profesional
  6. Gates diferenciados vs dominio genérico

Ejecutar con:
    uv run python 01-evals/code/eval-alto-stake.py

No requiere API keys.
"""

import re
from dataclasses import dataclass, field

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from shared.utils import get_logger

log = get_logger(__name__)
console = Console()


# ---------------------------------------------------------------------------
# Modelo de datos
# ---------------------------------------------------------------------------

@dataclass
class CitationCheck:
    """Resultado de verificación de una cita normativa."""
    citation_text: str         # Lo que el sistema citó
    doc_retrieved: bool        # ¿El doc fue recuperado?
    article_exists: bool       # ¿El artículo existe en el doc?
    content_matches: bool      # ¿El contenido citado coincide?
    interpretation_faithful: bool  # ¿La interpretación es fiel?
    level: str                 # "verified", "wrong_content", "wrong_article", "ghost"


@dataclass
class ObligationCheck:
    """Resultado de verificación de completitud de obligaciones."""
    query: str
    elements_required: list[str]
    elements_mentioned: list[str]
    elements_missing: list[str]
    completeness: float


@dataclass
class AbstentionCheck:
    """Resultado de verificación de abstención."""
    query: str
    should_abstain: bool       # ¿Debería el sistema abstenerse?
    did_abstain: bool          # ¿Se abstuvo?
    category: str              # "correct_abstention", "missed_abstention",
                               # "false_abstention", "correct_response"


@dataclass
class FormatCheck:
    """Resultado de verificación de formato profesional."""
    query: str
    response: str
    has_exact_citation: bool   # "Art. 3 inc. 2 Ley 20.730"
    has_unit: bool             # "10-50 UTM", "19%"
    has_specific_deadline: bool  # "día 12 del mes siguiente"
    has_disclaimer: bool       # "consulte a un profesional"
    mentions_exceptions: bool  # "salvo cuando..."
    format_score: float


@dataclass
class HighStakeEvalResult:
    """Resultado completo de eval alto-stake."""
    citation_accuracy: float
    ghost_citation_count: int
    obligation_completeness: float
    abstention_correct: float
    missed_abstention_rate: float
    false_abstention_rate: float
    format_compliance: float
    numerical_precision: float
    overall_pass: bool


# ---------------------------------------------------------------------------
# Datos de evaluación simulados
# ---------------------------------------------------------------------------

EVAL_CASES = [
    # Caso 1: Cita correcta y completa
    {
        "query": "¿Cuál es la multa por infracción a la Ley de Lobby?",
        "response": "Según el Art. 8 de la Ley 20.730, la multa por infracción "
                    "es de 10 a 50 UTM. En caso de reincidencia, la multa puede "
                    "duplicarse conforme al Art. 9.",
        "retrieved_docs": ["norma-01-ley-lobby.txt"],
        "citations": [
            {"text": "Art. 8 de la Ley 20.730", "doc": "norma-01-ley-lobby.txt",
             "exists": True, "content_ok": True, "interp_ok": True},
            {"text": "Art. 9", "doc": "norma-01-ley-lobby.txt",
             "exists": True, "content_ok": True, "interp_ok": True},
        ],
        "obligations": {
            "required": ["monto multa", "unidad (UTM)", "rango (10-50)", "reincidencia"],
            "mentioned": ["monto multa", "unidad (UTM)", "rango (10-50)", "reincidencia"],
        },
        "should_abstain": False,
        "did_abstain": False,
        "format": {"citation": True, "unit": True, "deadline": False, "disclaimer": False, "exception": True},
        "numerical": {"has_number": True, "correct": True},
    },
    # Caso 2: Cita fantasma — artículo inventado
    {
        "query": "¿Qué plazo tiene el SII para fiscalizar IVA digital?",
        "response": "De acuerdo con el Art. 15 de la Circular Nº 42, el SII "
                    "dispone de un plazo de 3 años para fiscalizar el cumplimiento "
                    "del IVA por servicios digitales.",
        "retrieved_docs": ["circular-01-sii-iva-digital.txt"],
        "citations": [
            {"text": "Art. 15 de la Circular Nº 42", "doc": "circular-01-sii-iva-digital.txt",
             "exists": False, "content_ok": False, "interp_ok": False},
        ],
        "obligations": {
            "required": ["plazo fiscalización", "referencia normativa"],
            "mentioned": ["plazo fiscalización"],
        },
        "should_abstain": False,
        "did_abstain": False,
        "format": {"citation": True, "unit": False, "deadline": True, "disclaimer": False, "exception": False},
        "numerical": {"has_number": True, "correct": False},
    },
    # Caso 3: Debería abstenerse (fuera de dominio) pero responde
    {
        "query": "¿Cómo hago una demanda por pensión alimenticia?",
        "response": "Para presentar una demanda por pensión alimenticia, debe "
                    "acudir al Tribunal de Familia de su jurisdicción con los "
                    "siguientes documentos...",
        "retrieved_docs": [],
        "citations": [],
        "obligations": {
            "required": [],
            "mentioned": [],
        },
        "should_abstain": True,
        "did_abstain": False,
        "format": {"citation": False, "unit": False, "deadline": False, "disclaimer": False, "exception": False},
        "numerical": {"has_number": False, "correct": True},
    },
    # Caso 4: Correctamente se abstiene
    {
        "query": "¿Cuál es el tipo de cambio dólar-peso hoy?",
        "response": "Esta consulta está fuera del alcance de este sistema. "
                    "Para información sobre tipos de cambio, consulte el sitio "
                    "del Banco Central de Chile.",
        "retrieved_docs": [],
        "citations": [],
        "obligations": {
            "required": [],
            "mentioned": [],
        },
        "should_abstain": True,
        "did_abstain": True,
        "format": {"citation": False, "unit": False, "deadline": False, "disclaimer": False, "exception": False},
        "numerical": {"has_number": False, "correct": True},
    },
    # Caso 5: Respuesta correcta pero sin formato profesional
    {
        "query": "¿Cuál es la tasa de IVA para servicios digitales?",
        "response": "La tasa es del diecinueve por ciento para servicios "
                    "digitales prestados desde el extranjero.",
        "retrieved_docs": ["circular-01-sii-iva-digital.txt"],
        "citations": [],
        "obligations": {
            "required": ["tasa", "sujeto obligado", "tipo servicio"],
            "mentioned": ["tasa", "tipo servicio"],
        },
        "should_abstain": False,
        "did_abstain": False,
        "format": {"citation": False, "unit": False, "deadline": False, "disclaimer": False, "exception": False},
        "numerical": {"has_number": False, "correct": False},  # Dice "diecinueve" en vez de "19%"
    },
    # Caso 6: Respuesta completa con disclaimer
    {
        "query": "¿Puedo deducir como gasto el pago a una plataforma digital extranjera?",
        "response": "Según la Circular Nº 42 del SII, los pagos a plataformas "
                    "digitales extranjeras están sujetos a IVA del 19%. Respecto "
                    "a la deducción como gasto, dependerá de la naturaleza del "
                    "servicio y su relación con la actividad generadora de renta "
                    "(Art. 31 Ley de Renta). Se recomienda consultar con un "
                    "asesor tributario para su caso específico.",
        "retrieved_docs": ["circular-01-sii-iva-digital.txt"],
        "citations": [
            {"text": "Circular Nº 42 del SII", "doc": "circular-01-sii-iva-digital.txt",
             "exists": True, "content_ok": True, "interp_ok": True},
            {"text": "Art. 31 Ley de Renta", "doc": None,
             "exists": True, "content_ok": True, "interp_ok": True},
        ],
        "obligations": {
            "required": ["tasa IVA", "condición deducción", "referencia normativa"],
            "mentioned": ["tasa IVA", "condición deducción", "referencia normativa"],
        },
        "should_abstain": False,
        "did_abstain": False,
        "format": {"citation": True, "unit": True, "deadline": False, "disclaimer": True, "exception": False},
        "numerical": {"has_number": True, "correct": True},
    },
    # Caso 7: Contenido transpuesto (cita correcta, contenido de otra norma)
    {
        "query": "¿Qué información debe publicarse según la Ley de Lobby?",
        "response": "Según el Art. 4 de la Ley 20.730, los sujetos pasivos "
                    "deben publicar trimestralmente un informe con el presupuesto "
                    "ejecutado en inmunizaciones y programas de salud.",
        "retrieved_docs": ["norma-01-ley-lobby.txt", "glosa-01-presupuesto-salud.txt"],
        "citations": [
            {"text": "Art. 4 de la Ley 20.730", "doc": "norma-01-ley-lobby.txt",
             "exists": True, "content_ok": False, "interp_ok": False},
        ],
        "obligations": {
            "required": ["audiencias", "viajes", "donativos", "registro público"],
            "mentioned": [],
        },
        "should_abstain": False,
        "did_abstain": False,
        "format": {"citation": True, "unit": False, "deadline": True, "disclaimer": False, "exception": False},
        "numerical": {"has_number": False, "correct": True},
    },
    # Caso 8: Abstención innecesaria (false abstention)
    {
        "query": "¿Cuántas USE recibe un alumno prioritario de 1º básico?",
        "response": "No dispongo de información suficiente para responder "
                    "esta consulta con precisión. Le sugiero consultar "
                    "directamente el Decreto de Subvención Escolar.",
        "retrieved_docs": ["decreto-01-subvencion-escolar.txt"],
        "citations": [],
        "obligations": {
            "required": ["valor USE", "nivel educativo", "condición prioritario"],
            "mentioned": [],
        },
        "should_abstain": False,
        "did_abstain": True,
        "format": {"citation": False, "unit": False, "deadline": False, "disclaimer": True, "exception": False},
        "numerical": {"has_number": False, "correct": False},
    },
    # Caso 9: Respuesta perfecta
    {
        "query": "¿Cuál es el presupuesto de la Glosa 05 para atención primaria?",
        "response": "Según la Glosa 05 de la Partida 16 (Ministerio de Salud), "
                    "Ley de Presupuestos 2024, el presupuesto asignado a atención "
                    "primaria de salud es de $2.145.891 millones. Estos recursos "
                    "se distribuyen entre los servicios de salud conforme a los "
                    "criterios del Art. 56 del DFL Nº 1/2005.",
        "retrieved_docs": ["glosa-01-presupuesto-salud.txt"],
        "citations": [
            {"text": "Glosa 05 Partida 16, Ley de Presupuestos 2024",
             "doc": "glosa-01-presupuesto-salud.txt",
             "exists": True, "content_ok": True, "interp_ok": True},
        ],
        "obligations": {
            "required": ["monto", "partida", "año presupuestario"],
            "mentioned": ["monto", "partida", "año presupuestario"],
        },
        "should_abstain": False,
        "did_abstain": False,
        "format": {"citation": True, "unit": True, "deadline": False, "disclaimer": False, "exception": False},
        "numerical": {"has_number": True, "correct": True},
    },
    # Caso 10: Monto incorrecto
    {
        "query": "¿Cuál es el rango de multas de la Ley de Lobby en pesos?",
        "response": "La Ley 20.730 establece multas de 10 a 50 UTM. "
                    "Al valor actual de la UTM ($65.000 aproximadamente), "
                    "esto equivale a $650.000 a $3.500.000.",
        "retrieved_docs": ["norma-01-ley-lobby.txt"],
        "citations": [
            {"text": "Ley 20.730", "doc": "norma-01-ley-lobby.txt",
             "exists": True, "content_ok": True, "interp_ok": True},
        ],
        "obligations": {
            "required": ["rango multa", "unidad", "conversión"],
            "mentioned": ["rango multa", "unidad", "conversión"],
        },
        "should_abstain": False,
        "did_abstain": False,
        "format": {"citation": True, "unit": True, "deadline": False, "disclaimer": False, "exception": False},
        "numerical": {"has_number": True, "correct": False},  # 50×65000=3.250.000, no 3.500.000
    },
]


# ---------------------------------------------------------------------------
# Evaluaciones
# ---------------------------------------------------------------------------

def eval_citations(cases: list[dict]) -> tuple[list[CitationCheck], float, int]:
    """Evalúa la precisión de citas normativas."""
    checks = []
    for case in cases:
        for cit in case["citations"]:
            if not cit.get("doc") or cit["doc"] not in case["retrieved_docs"]:
                # Doc no recuperado pero la cita puede ser a norma externa conocida
                if cit["exists"]:
                    level = "verified"  # Referencia externa válida
                else:
                    level = "ghost"
            elif not cit["exists"]:
                level = "wrong_article"
            elif not cit["content_ok"]:
                level = "wrong_content"
            elif not cit["interp_ok"]:
                level = "wrong_content"
            else:
                level = "verified"

            checks.append(CitationCheck(
                citation_text=cit["text"],
                doc_retrieved=cit.get("doc") in case["retrieved_docs"] if cit.get("doc") else False,
                article_exists=cit["exists"],
                content_matches=cit["content_ok"],
                interpretation_faithful=cit["interp_ok"],
                level=level,
            ))

    verified = sum(1 for c in checks if c.level == "verified")
    ghosts = sum(1 for c in checks if c.level == "ghost")
    accuracy = verified / len(checks) if checks else 1.0

    return checks, accuracy, ghosts


def eval_obligations(cases: list[dict]) -> tuple[list[ObligationCheck], float]:
    """Evalúa completitud de obligaciones fiscales."""
    checks = []
    for case in cases:
        req = case["obligations"]["required"]
        mentioned = case["obligations"]["mentioned"]
        if not req:
            continue

        missing = [r for r in req if r not in mentioned]
        completeness = len(mentioned) / len(req) if req else 1.0

        checks.append(ObligationCheck(
            query=case["query"],
            elements_required=req,
            elements_mentioned=mentioned,
            elements_missing=missing,
            completeness=completeness,
        ))

    avg_completeness = sum(c.completeness for c in checks) / len(checks) if checks else 0
    return checks, avg_completeness


def eval_abstention(cases: list[dict]) -> tuple[list[AbstentionCheck], dict]:
    """Evalúa la calibración de abstención."""
    checks = []
    for case in cases:
        should = case["should_abstain"]
        did = case["did_abstain"]

        if should and did:
            category = "correct_abstention"
        elif should and not did:
            category = "missed_abstention"
        elif not should and did:
            category = "false_abstention"
        else:
            category = "correct_response"

        checks.append(AbstentionCheck(
            query=case["query"],
            should_abstain=should,
            did_abstain=did,
            category=category,
        ))

    n = len(checks)
    stats = {
        "correct_abstention": sum(1 for c in checks if c.category == "correct_abstention"),
        "missed_abstention": sum(1 for c in checks if c.category == "missed_abstention"),
        "false_abstention": sum(1 for c in checks if c.category == "false_abstention"),
        "correct_response": sum(1 for c in checks if c.category == "correct_response"),
        "total": n,
    }

    return checks, stats


def eval_format(cases: list[dict]) -> tuple[list[FormatCheck], float]:
    """Evalúa formato y estructura profesional."""
    checks = []
    for case in cases:
        if case["did_abstain"] or case["should_abstain"]:
            continue

        fmt = case["format"]
        weights = {"citation": 0.25, "unit": 0.20, "deadline": 0.20,
                   "disclaimer": 0.15, "exception": 0.20}

        score = sum(
            weights[k] for k, v in [
                ("citation", fmt["citation"]),
                ("unit", fmt["unit"]),
                ("deadline", fmt["deadline"]),
                ("disclaimer", fmt["disclaimer"]),
                ("exception", fmt["exception"]),
            ] if v
        )

        checks.append(FormatCheck(
            query=case["query"],
            response=case["response"][:80] + "...",
            has_exact_citation=fmt["citation"],
            has_unit=fmt["unit"],
            has_specific_deadline=fmt["deadline"],
            has_disclaimer=fmt["disclaimer"],
            mentions_exceptions=fmt["exception"],
            format_score=score,
        ))

    avg_score = sum(c.format_score for c in checks) / len(checks) if checks else 0
    return checks, avg_score


def eval_numerical(cases: list[dict]) -> tuple[int, int, float]:
    """Evalúa precisión numérica (montos, tasas, plazos)."""
    total_with_numbers = 0
    correct = 0
    for case in cases:
        num = case["numerical"]
        if num["has_number"]:
            total_with_numbers += 1
            if num["correct"]:
                correct += 1

    precision = correct / total_with_numbers if total_with_numbers > 0 else 1.0
    return correct, total_with_numbers, precision


# ---------------------------------------------------------------------------
# Visualización
# ---------------------------------------------------------------------------

def show_citation_results(checks: list[CitationCheck], accuracy: float, ghosts: int) -> None:
    """Muestra resultados de verificación de citas."""
    table = Table(title="Verificación de citas normativas", show_lines=True)
    table.add_column("Cita", width=32)
    table.add_column("Doc recup.", justify="center", width=10)
    table.add_column("Art. existe", justify="center", width=10)
    table.add_column("Contenido OK", justify="center", width=11)
    table.add_column("Nivel", justify="center", width=14)

    for c in checks:
        level_color = {
            "verified": "green", "wrong_content": "yellow",
            "wrong_article": "red", "ghost": "red",
        }[c.level]
        level_label = {
            "verified": "Verificada", "wrong_content": "Contenido mal",
            "wrong_article": "Art. no existe", "ghost": "FANTASMA",
        }[c.level]

        table.add_row(
            c.citation_text[:30] + ".." if len(c.citation_text) > 32 else c.citation_text,
            "Sí" if c.doc_retrieved else "[red]No[/red]",
            "Sí" if c.article_exists else "[red]No[/red]",
            "Sí" if c.content_matches else "[red]No[/red]",
            f"[{level_color}]{level_label}[/{level_color}]",
        )

    console.print(table)

    acc_color = "green" if accuracy >= 0.95 else "yellow" if accuracy >= 0.80 else "red"
    ghost_color = "green" if ghosts == 0 else "red"

    console.print(
        f"\n  Citation accuracy: [{acc_color}]{accuracy:.1%}[/{acc_color}]"
        f" (umbral fiscal: ≥ 95%)"
        f"\n  Citas fantasma: [{ghost_color}]{ghosts}[/{ghost_color}]"
        f" (umbral fiscal: = 0)"
    )


def show_obligation_results(checks: list[ObligationCheck], avg: float) -> None:
    """Muestra resultados de completitud de obligaciones."""
    console.print()
    table = Table(title="Completitud de obligaciones fiscales", show_lines=True)
    table.add_column("Query", width=36)
    table.add_column("Requeridos", justify="center", width=10)
    table.add_column("Mencionados", justify="center", width=11)
    table.add_column("Faltantes", width=24)
    table.add_column("Score", justify="center", width=8)

    for c in checks:
        score_color = "green" if c.completeness >= 0.80 else "yellow" if c.completeness >= 0.50 else "red"
        missing = ", ".join(c.elements_missing) if c.elements_missing else "—"
        table.add_row(
            c.query[:34] + ".." if len(c.query) > 36 else c.query,
            str(len(c.elements_required)),
            str(len(c.elements_mentioned)),
            missing[:22] + ".." if len(missing) > 24 else missing,
            f"[{score_color}]{c.completeness:.0%}[/{score_color}]",
        )

    console.print(table)

    avg_color = "green" if avg >= 0.80 else "yellow" if avg >= 0.60 else "red"
    console.print(f"\n  Completitud promedio: [{avg_color}]{avg:.1%}[/{avg_color}] (umbral fiscal: ≥ 80%)")


def show_abstention_results(checks: list[AbstentionCheck], stats: dict) -> None:
    """Muestra resultados de abstención calibrada."""
    console.print()
    table = Table(title="Abstención calibrada", show_lines=True)
    table.add_column("Query", width=38)
    table.add_column("Debía\nabstenerse", justify="center", width=10)
    table.add_column("Se\nabstuvo", justify="center", width=8)
    table.add_column("Resultado", justify="center", width=18)

    for c in checks:
        cat_style = {
            "correct_abstention": "[green]Abstención correcta[/green]",
            "missed_abstention": "[red]MISSED abstention[/red]",
            "false_abstention": "[yellow]False abstention[/yellow]",
            "correct_response": "[green]Respuesta correcta[/green]",
        }[c.category]

        table.add_row(
            c.query[:36] + ".." if len(c.query) > 38 else c.query,
            "Sí" if c.should_abstain else "No",
            "Sí" if c.did_abstain else "No",
            cat_style,
        )

    console.print(table)

    n = stats["total"]
    missed = stats["missed_abstention"]
    false_abs = stats["false_abstention"]

    missed_color = "green" if missed / n < 0.02 else "red"
    false_color = "green" if false_abs / n < 0.15 else "yellow"

    console.print(Panel(
        f"[bold]Métricas de abstención:[/bold]\n\n"
        f"  Correct abstention:  {stats['correct_abstention']}/{n}\n"
        f"  Correct response:    {stats['correct_response']}/{n}\n"
        f"  [{missed_color}]Missed abstention: {missed}/{n} ({missed/n:.0%})[/{missed_color}]"
        f"  ← umbral fiscal: < 2%\n"
        f"  [{false_color}]False abstention:  {false_abs}/{n} ({false_abs/n:.0%})[/{false_color}]"
        f"  ← umbral fiscal: < 15%\n\n"
        f"  {'[red]ALERTA: missed abstention > 2%[/red]' if missed/n >= 0.02 else '[green]Missed abstention dentro del umbral[/green]'}",
        style="blue",
    ))


def show_format_results(checks: list[FormatCheck], avg: float) -> None:
    """Muestra resultados de formato profesional."""
    console.print()
    table = Table(title="Formato y estructura profesional", show_lines=True)
    table.add_column("Query", width=30)
    table.add_column("Cita\nexacta", justify="center", width=6)
    table.add_column("Unidad", justify="center", width=7)
    table.add_column("Plazo", justify="center", width=6)
    table.add_column("Discl.", justify="center", width=6)
    table.add_column("Excep.", justify="center", width=6)
    table.add_column("Score", justify="center", width=8)

    for c in checks:
        def yn(v: bool) -> str:
            return "[green]Sí[/green]" if v else "[red]No[/red]"

        score_color = "green" if c.format_score >= 0.60 else "yellow" if c.format_score >= 0.35 else "red"
        table.add_row(
            c.query[:28] + ".." if len(c.query) > 30 else c.query,
            yn(c.has_exact_citation),
            yn(c.has_unit),
            yn(c.has_specific_deadline),
            yn(c.has_disclaimer),
            yn(c.mentions_exceptions),
            f"[{score_color}]{c.format_score:.0%}[/{score_color}]",
        )

    console.print(table)
    avg_color = "green" if avg >= 0.75 else "yellow" if avg >= 0.50 else "red"
    console.print(f"\n  Format compliance promedio: [{avg_color}]{avg:.1%}[/{avg_color}] (umbral fiscal: ≥ 75%)")


def show_gates_comparison() -> None:
    """Muestra comparación de gates genéricos vs fiscales."""
    console.print()
    table = Table(title="Gates: dominio genérico vs dominio fiscal", show_lines=True)
    table.add_column("Métrica", style="bold", width=24)
    table.add_column("Umbral\ngenérico", justify="center", width=10)
    table.add_column("Umbral\nfiscal", justify="center", width=10)
    table.add_column("Razón del cambio", width=28)

    gates = [
        ("Faithfulness", "≥ 0.50", "≥ 0.70", "Cada claim debe tener respaldo"),
        ("Citation accuracy", "≥ 0.80", "≥ 0.95", "Citas incorrectas = responsabilidad"),
        ("Ghost citations", "< 5%", "= 0", "Zero tolerance"),
        ("Missed abstention", "< 10%", "< 2%", "Error asimétrico (alto costo)"),
        ("Obligation completeness", "≥ 0.60", "≥ 0.80", "Omisiones = costo legal"),
        ("Format compliance", "≥ 0.50", "≥ 0.75", "Usuario profesional exige"),
        ("Numerical precision", "≥ 0.80", "≥ 0.95", "Montos erróneos = decisión errónea"),
        ("Recall@5", "≥ 0.60", "≥ 0.60", "Igual (ya es estricto)"),
    ]

    for metric, gen, fiscal, reason in gates:
        table.add_row(metric, gen, f"[bold]{fiscal}[/bold]", reason)

    console.print(table)


def show_overall_report(result: HighStakeEvalResult) -> None:
    """Muestra reporte final consolidado."""
    console.print()
    table = Table(title="Reporte consolidado: eval alto-stake", show_lines=True)
    table.add_column("Métrica", style="bold", width=26)
    table.add_column("Valor", justify="center", width=10)
    table.add_column("Umbral", justify="center", width=10)
    table.add_column("Estado", justify="center", width=10)

    metrics = [
        ("Citation accuracy", result.citation_accuracy, 0.95),
        ("Ghost citations", float(result.ghost_citation_count), 0.0),
        ("Obligation completeness", result.obligation_completeness, 0.80),
        ("Missed abstention", result.missed_abstention_rate, 0.02),
        ("False abstention", result.false_abstention_rate, 0.15),
        ("Format compliance", result.format_compliance, 0.75),
        ("Numerical precision", result.numerical_precision, 0.95),
    ]

    all_pass = True
    for name, value, threshold in metrics:
        # Ghost citations y missed abstention: lower is better
        if name in ("Ghost citations", "Missed abstention", "False abstention"):
            passed = value <= threshold
        else:
            passed = value >= threshold

        if not passed:
            all_pass = False

        status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"

        if name == "Ghost citations":
            val_str = str(int(value))
            thr_str = "= 0"
        elif name in ("Missed abstention", "False abstention"):
            val_str = f"{value:.0%}"
            thr_str = f"< {threshold:.0%}"
        else:
            val_str = f"{value:.1%}"
            thr_str = f"≥ {threshold:.0%}"

        table.add_row(name, val_str, thr_str, status)

    table.add_section()
    overall = "[green]PASS[/green]" if all_pass else "[red]FAIL[/red]"
    table.add_row("", "", "[bold]OVERALL[/bold]", f"[bold]{overall}[/bold]")

    console.print(table)

    if not all_pass:
        console.print(Panel(
            "[bold red]El sistema NO pasa los gates de dominio fiscal.[/bold red]\n\n"
            "Acciones requeridas:\n"
            "  1. Revisar citas fantasma y corregir pipeline de generación\n"
            "  2. Mejorar detección de queries fuera de dominio\n"
            "  3. Agregar precisión numérica al prompt/rúbrica\n"
            "  4. No hacer deploy hasta resolver los FAIL",
            style="red",
        ))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    console.print(Panel(
        "[bold]Evals para dominios alto-stake: legal y fiscal[/bold]\n\n"
        "Evaluaciones específicas para RAG sobre normativa chilena.\n"
        "10 casos de evaluación con 5 dimensiones de calidad.",
        style="bold blue",
    ))

    # 1. Verificación de citas
    console.print(Panel(
        "[bold]Eval 1: Verificación de citas normativas[/bold]\n\n"
        "¿Las normas citadas existen y dicen lo que el sistema afirma?",
        style="blue",
    ))

    citation_checks, citation_accuracy, ghosts = eval_citations(EVAL_CASES)
    show_citation_results(citation_checks, citation_accuracy, ghosts)

    # 2. Completitud de obligaciones
    console.print()
    console.print(Panel(
        "[bold]Eval 2: Completitud de obligaciones[/bold]\n\n"
        "¿La respuesta menciona todos los elementos obligatorios?",
        style="blue",
    ))

    obligation_checks, obligation_avg = eval_obligations(EVAL_CASES)
    show_obligation_results(obligation_checks, obligation_avg)

    # 3. Abstención calibrada
    console.print()
    console.print(Panel(
        "[bold]Eval 3: Abstención calibrada[/bold]\n\n"
        "¿El sistema dice 'no sé' cuando debe, y responde cuando puede?",
        style="blue",
    ))

    abstention_checks, abstention_stats = eval_abstention(EVAL_CASES)
    show_abstention_results(abstention_checks, abstention_stats)

    # 4. Formato profesional
    console.print()
    console.print(Panel(
        "[bold]Eval 4: Formato y estructura profesional[/bold]\n\n"
        "¿La respuesta tiene el formato que un analista fiscal espera?",
        style="blue",
    ))

    format_checks, format_avg = eval_format(EVAL_CASES)
    show_format_results(format_checks, format_avg)

    # 5. Precisión numérica
    console.print()
    console.print(Panel(
        "[bold]Eval 5: Precisión numérica[/bold]\n\n"
        "¿Los montos, tasas y plazos son correctos?",
        style="blue",
    ))

    num_correct, num_total, num_precision = eval_numerical(EVAL_CASES)
    num_color = "green" if num_precision >= 0.95 else "yellow" if num_precision >= 0.80 else "red"
    console.print(
        f"  Respuestas con números: {num_total}"
        f"\n  Números correctos: {num_correct}/{num_total}"
        f"\n  Precisión numérica: [{num_color}]{num_precision:.1%}[/{num_color}]"
        f" (umbral fiscal: ≥ 95%)"
    )

    # 6. Comparación de gates
    show_gates_comparison()

    # 7. Reporte consolidado
    n = abstention_stats["total"]
    result = HighStakeEvalResult(
        citation_accuracy=citation_accuracy,
        ghost_citation_count=ghosts,
        obligation_completeness=obligation_avg,
        abstention_correct=(abstention_stats["correct_abstention"] + abstention_stats["correct_response"]) / n,
        missed_abstention_rate=abstention_stats["missed_abstention"] / n,
        false_abstention_rate=abstention_stats["false_abstention"] / n,
        format_compliance=format_avg,
        numerical_precision=num_precision,
        overall_pass=False,  # Calculated in show_overall_report
    )
    show_overall_report(result)


if __name__ == "__main__":
    main()
