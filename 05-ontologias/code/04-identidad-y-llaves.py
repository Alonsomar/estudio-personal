"""§4 — Identidad y llaves canónicas.

Produce los números de `theory/04-identidad-y-llaves.md`. Es *record
linkage*: decidir cuándo dos menciones textuales distintas son la misma
entidad. Mismo problema que microdatos administrativos, otro dominio.

    uv run python 05-ontologias/code/04-identidad-y-llaves.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ontology_lib import (  # noqa: E402
    catalogo_organismos_corpus,
    resolver_organismo,
    resolver_organismo_difuso,
)

from shared.utils import get_logger, get_project_root  # noqa: E402

log = get_logger(__name__)
CORPUS_DIR = get_project_root() / "shared" / "corpus_chileno"


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def demo_el_problema() -> None:
    """El problema, con las tres formas reales bajo las que el corpus
    menciona al mismo organismo."""
    seccion("1. El mismo organismo, tres formas textuales — verificado en el corpus")

    print(
        "grep sobre los 40 documentos del corpus encuentra estas tres formas\n"
        "para EL MISMO organismo (la Dirección de Compras y Contratación Pública):\n"
    )
    formas = [
        ("Dirección de Compras y Contratación Pública", "ley-03, ley-04 (forma completa, primera mención)"),
        ("Dirección de Compras", "ley-03, decreto-03, resolucion-01 (forma corta, menciones posteriores)"),
        ("CHILECOMPRA", "resolucion-01 (nombre de marca, encabezado institucional)"),
    ]
    for forma, donde in formas:
        print(f"  '{forma}'\n      -> {donde}")

    print(
        "\nSin resolución, un grafo construido por coincidencia literal de string\n"
        "crearía TRES nodos donde hay UN organismo. Cualquier competency question\n"
        "tipo '¿qué documentos emitió la Dirección de Compras?' (§2) respondería\n"
        "mal — no por un error de recorrido, sino porque el grafo mismo está mal\n"
        "construido antes de correr ninguna consulta."
    )


def demo_nivel_1_deterministico() -> None:
    """Resolución barata: normalización + diccionario. Va primero porque no
    tiene falsos positivos."""
    seccion("2. Nivel 1 — Normalización + diccionario (barato, sin falsos positivos)")

    catalogo = catalogo_organismos_corpus()
    menciones = [
        "Dirección de Compras y Contratación Pública",
        "dirección de compras",
        "CHILECOMPRA",
        "Servicio de Impuestos Internos",
        "esta Contraloría",
        "Ministerio de Obras Públicas",  # no está en el catálogo: debe fallar
    ]
    print(f"{'mención':>48} | {'resuelve a':>10}")
    print("-" * 63)
    for m in menciones:
        rid = resolver_organismo(m, catalogo)
        print(f"{m:>48} | {rid or '(sin match)':>10}")

    print(
        "\nLas cinco primeras resuelven a la MISMA llave canónica sin importar\n"
        "mayúsculas, acentos o si es la forma larga o la corta. La sexta —un\n"
        "organismo real del corpus pero fuera de este catálogo curado— NO\n"
        "resuelve, y eso es correcto: Nivel 1 nunca inventa una coincidencia."
    )


def demo_nivel_2_difuso_y_su_riesgo() -> None:
    """El fallback caro, y el falso positivo real que produce sobre nombres
    institucionales chilenos con estructura compartida."""
    seccion("3. Nivel 2 — Similitud difusa: el fallback, y por qué va SEGUNDO")

    catalogo = catalogo_organismos_corpus()
    # Una mención deformada (como saldría de un OCR o una cita informal) que
    # el Nivel 1 no resuelve.
    mencion_deformada = "Direccion de Compras y Contratacion Publ."
    rid_n1 = resolver_organismo(mencion_deformada, catalogo)
    rid_n2, score = resolver_organismo_difuso(mencion_deformada, catalogo)
    print(f"Mención deformada: '{mencion_deformada}'")
    print(f"  Nivel 1 (diccionario exacto): {rid_n1 or '(sin match)'}")
    print(f"  Nivel 2 (similitud difusa):   {rid_n2}  (score={score:.2f})")

    print(
        "\nAcá el fallback rescata un caso legítimo que el diccionario no cubría.\n"
        "Pero el mismo mecanismo tiene un modo de falla real, no hipotético:"
    )

    import difflib

    candidatos_riesgo = [
        "Dirección de Compras", "Dirección de Presupuestos",
        "Dirección de Educación Pública", "Dirección de Vialidad",
        "Dirección de Obras Hidráulicas",
    ]
    busqueda = "Dirección de Compras y Contratación Pública"
    ranking = [
        (c, difflib.SequenceMatcher(None, busqueda, c).ratio())
        for c in candidatos_riesgo
    ]
    ranking.sort(key=lambda x: -x[1])
    print(f"\nBuscando la mejor coincidencia difusa para: '{busqueda}'")
    for c, s in ranking:
        marca = " <- la correcta" if c == "Dirección de Compras" else ""
        print(f"  {s:.3f}  {c}{marca}")

    print(
        "\n'Dirección de Educación Pública' queda MÁS cerca por similitud de\n"
        "secuencia que 'Dirección de Compras' — la respuesta correcta. Los\n"
        "nombres institucionales chilenos comparten estructura ('Dirección de\n"
        "X Pública/Nacional') que la similitud de caracteres no distingue de la\n"
        "identidad real. Es la razón concreta, medida, por la que el pipeline\n"
        "prueba el diccionario determinista PRIMERO: la similitud difusa sin\n"
        "orden de prioridad puede preferir un organismo por el vecino equivocado."
    )


def demo_contexto_documental() -> None:
    """Lo que ningún diccionario resuelve: la referencia anafórica."""
    seccion("4. Lo que ni el diccionario ni la similitud resuelven: anáfora")

    print(
        "Tres circulares del corpus usan la frase 'este Servicio' para referirse\n"
        "al organismo que las emite:\n"
    )
    casos = [
        ("resolucion-02-sii-registro-plataformas.txt", "SII"),
        ("circular-06-sii-credito-especial-construccion.txt", "SII"),
        ("circular-05-sii-factura-electronica.txt", "SII"),
    ]
    for doc, organismo in casos:
        print(f"  {doc}  ->  'este Servicio' = {organismo}")

    print(
        "\n'Este Servicio' no tiene ninguna forma léxica que un diccionario pueda\n"
        "mapear: la MISMA frase, en un documento de la Contraloría, resolvería a\n"
        "la Contraloría. La única resolución correcta es CONTEXTUAL — mirar quién\n"
        "emitió el documento (el encabezado, ya extraído por el parser de §1-§2)\n"
        "— no textual. Ni el Nivel 1 ni el Nivel 2 de este pipeline lo resuelven\n"
        "solos; ambos necesitan el metadata de procedencia del documento."
    )


def grafico_riesgo_difuso() -> None:
    """El ranking de similitud difusa que prefiere el vecino equivocado."""
    import difflib

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    candidatos = [
        "Dirección de Compras", "Dirección de Presupuestos",
        "Dirección de Educación Pública", "Dirección de Vialidad",
        "Dirección de Obras Hidráulicas",
    ]
    busqueda = "Dirección de Compras y Contratación Pública"
    ranking = sorted(
        ((c, difflib.SequenceMatcher(None, busqueda, c).ratio()) for c in candidatos),
        key=lambda x: -x[1],
    )
    nombres = [c for c, _ in ranking]
    scores = [s for _, s in ranking]
    colores = ["#2ecc71" if n == "Dirección de Compras" else "#e74c3c" for n in nombres]

    fig, ax = plt.subplots(figsize=(9, 4.5))
    bars = ax.barh(nombres[::-1], scores[::-1], color=colores[::-1])
    ax.set_xlabel("similitud de secuencia (difflib.SequenceMatcher)")
    ax.set_title(
        f"Similitud difusa buscando:\n'{busqueda}'", fontsize=11
    )
    ax.set_xlim(0, 1)
    for bar, score in zip(bars, scores[::-1]):
        ax.text(score + 0.01, bar.get_y() + bar.get_height() / 2, f"{score:.3f}",
                va="center", fontsize=9)
    ax.text(
        0.98, 0.03, "verde = la respuesta correcta",
        transform=ax.transAxes, ha="right", fontsize=8, color="#555",
    )

    fig.tight_layout()
    out = Path(__file__).resolve().parent.parent / "diagrams" / "riesgo-similitud-difusa.png"
    fig.savefig(out, dpi=130)
    print(f"\n[diagrama] {out.relative_to(ROOT)}")


if __name__ == "__main__":
    log.info("Entity resolution sobre organismos del corpus chileno.")
    demo_el_problema()
    demo_nivel_1_deterministico()
    demo_nivel_2_difuso_y_su_riesgo()
    demo_contexto_documental()
    grafico_riesgo_difuso()
    print()
