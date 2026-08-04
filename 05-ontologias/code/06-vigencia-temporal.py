"""§6 — Vigencia temporal y versionado normativo.

Produce los números de `theory/06-vigencia-temporal.md`. Retoma el límite
que `02 §9` dejó abierto ("doc reemplaza doc" es demasiado grueso) y lo
resuelve a nivel de artículo, con bitemporalidad real: las fechas de
`registrado_el` son las fechas de commit reales en que cada norma entró al
corpus (`git log`), no fechas inventadas para la demo.

    uv run python 05-ontologias/code/06-vigencia-temporal.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "02-retrieval" / "code"))

from ontology_lib import (  # noqa: E402
    ModificacionArticulo,
    que_sabia_el_sistema,
    texto_vigente,
)
from retrieval_lib import DOC_TEMPORAL, _in_range  # noqa: E402

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)

# Fechas de VIGENCIA: leídas del texto real de cada norma (ver theory/06).
# Fechas de REGISTRO: fechas de commit REALES en que cada norma entró al
# corpus (`git log --format=%ad -- shared/corpus_chileno/<archivo>`), no
# inventadas para la demo.
MODIFICACIONES = [
    ModificacionArticulo(
        norma_modificadora="ley-02-ley-21210-modernizacion.txt",
        norma_modificada="ley-01-dl-825-iva-base.txt",
        articulo="8",
        valido_desde="2020-02-24",
        registrado_el="2026-05-27",
        fundamento="Incorpórase en el artículo 8º una nueva letra n)",
    ),
    ModificacionArticulo(
        norma_modificadora="ley-02-ley-21210-modernizacion.txt",
        norma_modificada="ley-05-dl-824-renta-base.txt",
        articulo="14",
        valido_desde="2020-02-24",
        registrado_el="2026-08-03",  # ley-05 entró recién en B6
        fundamento="Sustitúyese el artículo 14 de la Ley sobre Impuesto a la Renta",
    ),
    ModificacionArticulo(
        norma_modificadora="ley-04-ley-21634-moderniza-compras.txt",
        norma_modificada="ley-03-ley-19886-compras-publicas.txt",
        articulo="4",
        valido_desde="2023-12-11",  # publicación; sin vacancia legis para este artículo
        registrado_el="2026-08-03",
        fundamento="Incorpórase en el artículo 4º un nuevo inciso (inhabilidad Ley 20.393/20.880)",
    ),
    ModificacionArticulo(
        norma_modificadora="ley-04-ley-21634-moderniza-compras.txt",
        norma_modificada="ley-03-ley-19886-compras-publicas.txt",
        articulo="5",
        valido_desde="2023-12-11",
        registrado_el="2026-08-03",
        fundamento="Sustitúyese en el artículo 5º el umbral de licitación pública obligatoria",
    ),
    ModificacionArticulo(
        norma_modificadora="ley-04-ley-21634-moderniza-compras.txt",
        norma_modificada="ley-03-ley-19886-compras-publicas.txt",
        articulo="7 bis",
        valido_desde="2024-12-11",  # vacancia legis de 12 meses, EXPLÍCITA en el texto
        registrado_el="2026-08-03",
        fundamento="Artículo segundo: compra ágil entra en vigencia a los 12 meses de publicada la ley",
    ),
]


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def demo_el_limite_de_02_9() -> None:
    """El modelo de 02 §9 (documento completo) reproducido, y su error."""
    seccion("1. El modelo de 02 §9: documento completo, y dónde se equivoca")

    ley01 = DOC_TEMPORAL["ley-01-dl-825-iva-base.txt"]
    print("DOC_TEMPORAL (02 §9) para ley-01 (DL 825):")
    print(f"  vigencia_desde={ley01['vigencia_desde']}  vigencia_hasta={ley01['vigencia_hasta']}\n")

    fecha = "2024-06-01"
    vigente = _in_range(fecha, ley01["vigencia_desde"], ley01["vigencia_hasta"])
    print(f"Consultando ley-01 completa en {fecha}: ¿vigente? -> {vigente}")
    print(
        "\nEl modelo de documento dice 'NO' para TODO el DL 825 a partir del\n"
        "2020-02-23 — correcto para el artículo 8º (modificado por la Ley 21.210),\n"
        "pero FALSO para cualquier otro artículo que la 21.210 nunca tocó. El\n"
        "artículo 12º (exenciones, citado por circular-04) es uno de esos: sigue\n"
        "rigiendo el texto de 1974, y el modelo de documento lo declara 'no\n"
        "vigente' igual, porque no distingue artículos."
    )


def demo_texto_vigente_por_articulo() -> None:
    """La misma consulta, resuelta a nivel de artículo."""
    seccion("2. La misma consulta, a nivel de artículo (esta sección)")

    fecha = "2024-06-01"
    for articulo in ("8", "12"):
        fuente, desde = texto_vigente("ley-01-dl-825-iva-base.txt", articulo, MODIFICACIONES, fecha)
        etiqueta = f"modificado, vigente desde {desde}" if desde else "texto original, sin modificar"
        print(f"  Art. {articulo:>3} del DL 825 en {fecha}: fuente={fuente}  ({etiqueta})")

    print(
        "\nMismo documento base, misma fecha de consulta, DOS respuestas distintas\n"
        "según el artículo. Es exactamente la distinción que 02 §9 pidió y que el\n"
        "modelo de documento no puede dar."
    )

    print("\nAhora la pregunta con la que 02 §9 abrió el caso: '¿qué regía en 2018?'")
    fecha_2018 = "2018-06-30"
    for articulo in ("8", "12"):
        fuente, desde = texto_vigente("ley-01-dl-825-iva-base.txt", articulo, MODIFICACIONES, fecha_2018)
        etiqueta = f"modificado, vigente desde {desde}" if desde else "texto original"
        print(f"  Art. {articulo:>3} del DL 825 en {fecha_2018}: fuente={fuente}  ({etiqueta})")
    print(
        "\nEn 2018 ambos artículos están regidos por el texto original — ninguna\n"
        "modificación tenía vigencia todavía. Coincide con 02 §9 en este caso\n"
        "simple; la diferencia aparece cuando la fecha SÍ cruza la vigencia de\n"
        "una modificación puntual, como en el ejemplo de arriba."
    )


def demo_ley_04_vigencias_desparejas() -> None:
    """Un solo documento modificador, tres artículos, DOS fechas de vigencia."""
    seccion("3. Un solo documento, tres artículos, vigencias distintas (ley-04)")

    print("Ley Nº 21.634 publicada 2023-12-11. Vigencia por artículo:\n")
    print(f"{'artículo':>10} | {'valido_desde':>13} | {'fundamento':>55}")
    print("-" * 84)
    for m in MODIFICACIONES:
        if m.norma_modificadora == "ley-04-ley-21634-moderniza-compras.txt":
            print(f"{m.articulo:>10} | {m.valido_desde:>13} | {m.fundamento[:53]:>55}")

    for fecha in ("2024-06-01", "2025-01-01"):
        print(f"\nConsultando ley-03 (Ley 19.886) en {fecha}:")
        for articulo in ("4", "5"):
            fuente, desde = texto_vigente("ley-03-ley-19886-compras-publicas.txt", articulo, MODIFICACIONES, fecha)
            print(f"  Art. {articulo:>5}: {fuente}  (desde {desde})")
        # El art. 7 bis es un artículo NUEVO (no existía antes de la ley-04):
        # antes de su valido_desde no hay "texto original" al que caer.
        mod_7bis = next(m for m in MODIFICACIONES if m.articulo == "7 bis")
        if fecha >= mod_7bis.valido_desde:
            print(f"  Art. 7 bis: {mod_7bis.norma_modificadora}  (desde {mod_7bis.valido_desde})")
        else:
            print("  Art. 7 bis: NO EXISTE todavía — artículo nuevo, sin texto previo al que volver")

    print(
        "\nEn 2024-06-01 la compra ágil (art. 7 bis) legalmente no existe aún,\n"
        "aunque la ley que la crea ya está publicada y ya modificó otros dos\n"
        "artículos de la misma norma. Un modelo a nivel de documento no puede\n"
        "expresar 'esta ley ya rige para dos cosas y no para una tercera'."
    )


def demo_bitemporalidad() -> None:
    """Vigencia legal vs. cuándo ESTE sistema se enteró — con fechas de git,
    no inventadas."""
    seccion("4. Bitemporalidad: vigencia vs. registro (fechas reales de git)")

    def _corto(doc_id: str) -> str:
        partes = doc_id.split("-")
        return f"{partes[0]}-{partes[1]}"

    print(f"{'modificación':>45} | {'vigente desde':>13} | {'registrado el':>13}")
    print("-" * 76)
    for m in MODIFICACIONES:
        etiqueta = f"{_corto(m.norma_modificadora)} art.{m.articulo} -> {_corto(m.norma_modificada)}"
        print(f"{etiqueta:>45} | {m.valido_desde:>13} | {m.registrado_el:>13}")

    print(
        "\n`registrado_el` no es una fecha inventada para la demo: es la fecha de\n"
        "commit real en que cada archivo entró al corpus (`git log`). El artículo\n"
        "8º del DL 825 lleva vigente desde 2020-02-24, pero ESTA ontología no supo\n"
        "nada de él hasta el 2026-05-27, cuando el corpus se expandió por primera\n"
        "vez — más de seis años de brecha entre vigencia y registro."
    )

    print("\n¿Qué sabía el sistema el 2026-06-01 (antes de B6)?")
    for m in que_sabia_el_sistema(MODIFICACIONES, "2026-06-01"):
        print(f"  {m.norma_modificadora} modifica art. {m.articulo} de {m.norma_modificada}")
    print("\n¿Qué sabe el sistema hoy (después de B6, 2026-08-03)?")
    for m in que_sabia_el_sistema(MODIFICACIONES, "2026-08-03"):
        print(f"  {m.norma_modificadora} modifica art. {m.articulo} de {m.norma_modificada}")

    print(
        "\nSon preguntas DISTINTAS con respuestas DISTINTAS. 'Qué era vigente en\n"
        "2020' y 'qué sabía este sistema en 2020' no son la misma pregunta —de\n"
        "hecho, en 2020 este sistema ni existía—. Confundirlas es el error\n"
        "bitemporal clásico: un sistema de auditoría que reporta 'siempre lo\n"
        "supimos' cuando en realidad el dato se cargó mucho después."
    )


def grafico_bitemporal() -> None:
    """Línea de tiempo: vigencia legal vs. fecha de registro, para cada
    modificación. La brecha entre los dos puntos ES el argumento de la
    sección, visualizado."""
    import matplotlib
    import matplotlib.dates as mdates

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from datetime import date

    fig, ax = plt.subplots(figsize=(10, 4.5))

    etiquetas = []
    for i, m in enumerate(MODIFICACIONES):
        partes_mod = m.norma_modificadora.split("-")
        partes_base = m.norma_modificada.split("-")
        etiqueta = f"{partes_mod[0]}-{partes_mod[1]} art.{m.articulo} → {partes_base[0]}-{partes_base[1]}"
        etiquetas.append(etiqueta)
        y = len(MODIFICACIONES) - i
        d_vigente = date.fromisoformat(m.valido_desde)
        d_registro = date.fromisoformat(m.registrado_el)
        ax.plot([d_vigente, d_registro], [y, y], color="#bdc3c7", lw=2, zorder=1)
        ax.scatter([d_vigente], [y], color="#2ecc71", s=90, zorder=2, label="vigente desde" if i == 0 else None)
        ax.scatter([d_registro], [y], color="#3498db", s=90, zorder=2, label="registrado el" if i == 0 else None)

    ax.set_yticks(range(1, len(MODIFICACIONES) + 1))
    ax.set_yticklabels(etiquetas[::-1], fontsize=8)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    for label in ax.get_xticklabels():
        label.set_rotation(0)
    ax.set_xlabel("fecha")
    ax.set_title(
        "Vigencia legal vs. fecha de registro en esta ontología\n"
        "(fechas de registro = commits reales de B6/git, no inventadas)",
        fontsize=11,
    )
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(axis="x", alpha=0.3)
    ax.set_ylim(0.3, len(MODIFICACIONES) + 0.9)

    fig.tight_layout()
    out = Path(__file__).resolve().parent.parent / "diagrams" / "bitemporalidad.png"
    fig.savefig(out, dpi=130)
    print(f"\n[diagrama] {out.relative_to(ROOT)}")


if __name__ == "__main__":
    log.info("Vigencia a nivel de artículo, sobre el caso Ley 21.210 / DL 825 y Ley 21.634.")
    demo_el_limite_de_02_9()
    demo_texto_vigente_por_articulo()
    demo_ley_04_vigencias_desparejas()
    demo_bitemporalidad()
    grafico_bitemporal()
    print()
