"""§5 — Extraer la ontología del corpus.

Produce los números de `theory/05-extraccion-llm.md`. Extrae relaciones
normativas de una muestra de 10 documentos con un LLM (gpt-4o-mini,
structured output, caché en disco), resuelve los identificadores de norma
extraídos a `doc_id` reutilizando el pipeline de §4, y mide precisión/
recall contra `examples/relaciones-manual.json` (§2).

Primera corrida: llama a la API (10 documentos, ~$0.01). Corridas
siguientes: lee de caché, reproducible sin API key.

    uv run python 05-ontologias/code/05-extraccion-llm.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "03-produccion" / "code"))

from ontology_lib import (  # noqa: E402
    LLMExtractor,
    Norma,
    RelacionNormativa,
    resolver_identificador_norma,
)
from prod_lib import PRICING_USD_PER_M_TOKENS as PRICING  # noqa: E402

from shared.utils import get_logger, get_project_root  # noqa: E402

log = get_logger(__name__)
CORPUS_DIR = get_project_root() / "shared" / "corpus_chileno"
DATA_PATH = Path(__file__).resolve().parent.parent / "examples" / "relaciones-manual.json"
CACHE_PATH = Path(__file__).resolve().parent.parent / "examples" / "cache-extraccion-llm.json"
MODEL = "gpt-4o-mini"

# Muestra de 10 documentos, cubriendo los cuatro clusters de B6 y los tres
# tipos de relación que más importan (modifica, reglamenta, aplica), no solo
# cita. No se corre sobre el corpus completo por costo — el objetivo es
# medir la tasa de error, no maximizar cobertura.
MUESTRA = [
    "ley-02-ley-21210-modernizacion.txt",
    "circular-01-sii-iva-digital.txt",
    "resolucion-01-chilecompra-compra-agil.txt",
    "oficio-02-contraloria-trato-directo.txt",
    "decreto-01-subvencion-escolar.txt",
    "oficio-05-contraloria-traspaso-slep.txt",
    "tabla-02-tasas-impuesto-renta-2024.txt",
    "decreto-04-modificacion-presupuestaria.txt",
    "resolucion-03-registro-lobbistas.txt",
    "decreto-06-reglamento-servicios-locales.txt",
]


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def cargar_ground_truth() -> tuple[list[Norma], set[tuple[str, str, str]]]:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    normas = [Norma(**n) for n in data["normas"]]
    gt = {(r["origen"], r["tipo"], r["destino"]) for r in data["relaciones"]}
    return normas, gt


def demo_extraccion(extractor: LLMExtractor, normas: list[Norma]) -> dict:
    """Extrae relaciones de la muestra y resuelve destinos a doc_id."""
    seccion("1. Extracción sobre 10 documentos (structured output + Pydantic)")

    resultados: dict[str, list[RelacionNormativa]] = {}
    niveles_resolucion = {"exacto": 0, "numero": 0, "difuso": 0, "sin_match": 0}
    sin_resolver: list[str] = []

    for doc_id in MUESTRA:
        texto = (CORPUS_DIR / doc_id).read_text(encoding="utf-8")
        extraccion = extractor.extraer(texto)
        relaciones_doc = []
        for rel in extraccion.relaciones:
            destino_id, nivel = resolver_identificador_norma(rel.identificador_destino, normas)
            niveles_resolucion[nivel] += 1
            if destino_id is None:
                sin_resolver.append(f"{doc_id} -> '{rel.identificador_destino}' ({rel.tipo.value})")
                continue
            relaciones_doc.append(
                RelacionNormativa(origen=doc_id, tipo=rel.tipo, destino=destino_id, fundamento=rel.fundamento)
            )
        resultados[doc_id] = relaciones_doc
        print(f"  {doc_id:>50}: {len(extraccion.relaciones):>2} relaciones crudas, "
              f"{len(relaciones_doc):>2} resueltas a doc_id")

    print(f"\nResolución de identificadores: {niveles_resolucion}")
    if sin_resolver:
        print("\nIdentificadores que NO resolvieron a ningún doc_id del corpus:")
        for s in sin_resolver:
            print(f"    {s}")

    return resultados


def demo_precision_recall(resultados: dict[str, list[RelacionNormativa]], gt: set) -> None:
    """Precisión y recall contra la verdad fundamental curada a mano."""
    seccion("2. Precisión y recall contra relaciones-manual.json (§2)")

    extraidas = {
        (r.origen, r.tipo.value, r.destino)
        for rels in resultados.values() for r in rels
    }
    gt_muestra = {t for t in gt if t[0] in MUESTRA}

    verdaderos_positivos = extraidas & gt_muestra
    falsos_positivos = extraidas - gt_muestra
    falsos_negativos = gt_muestra - extraidas

    precision = len(verdaderos_positivos) / len(extraidas) if extraidas else 0.0
    recall = len(verdaderos_positivos) / len(gt_muestra) if gt_muestra else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    print(f"Ground truth en la muestra (10 docs):  {len(gt_muestra)} relaciones")
    print(f"Extraídas y resueltas:                 {len(extraidas)} relaciones")
    print(f"Verdaderos positivos (match exacto):   {len(verdaderos_positivos)}")
    print(f"Falsos positivos:                      {len(falsos_positivos)}")
    print(f"Falsos negativos (no detectadas):      {len(falsos_negativos)}")
    print(f"\nPrecisión: {precision:.0%}   Recall: {recall:.0%}   F1: {f1:.0%}")

    if falsos_positivos:
        print("\nFalsos positivos (el LLM extrajo algo que no está en la verdad fundamental):")
        for fp in sorted(falsos_positivos):
            print(f"    {fp}")
    if falsos_negativos:
        print("\nFalsos negativos (relaciones reales que el LLM no detectó):")
        for fn in sorted(falsos_negativos):
            print(f"    {fn}")

    print(
        "\nOjo con la lectura de los falsos positivos: 'match exacto' exige que\n"
        "tipo Y destino coincidan con la anotación manual. Un falso positivo puede\n"
        "ser un error real del LLM, O una relación legítima que la curación manual\n"
        "de §2 simplemente no anotó (§2 fue explícito: 'curación manual, no\n"
        "exhaustiva'). Sin revisar caso por caso, la precisión reportada es una\n"
        "COTA INFERIOR, no el error real del extractor."
    )


def demo_costo(extractor: LLMExtractor) -> None:
    """Costo medido con la aritmética de 04 §1, no supuesto."""
    seccion("3. Costo, con la aritmética de 04 §1")

    precio = PRICING[MODEL]
    costo_in = extractor.tokens_in / 1e6 * precio["in"]
    costo_out = extractor.tokens_out / 1e6 * precio["out"]
    costo_total = costo_in + costo_out

    print(f"Llamadas a la API en esta corrida:  {extractor.api_calls}")
    print(f"Tokens de entrada:                  {extractor.tokens_in:,}")
    print(f"Tokens de salida:                   {extractor.tokens_out:,}")
    print(f"Costo ({MODEL}):                    ${costo_total:.4f}")
    if extractor.api_calls:
        print(f"Costo por documento:                ${costo_total / extractor.api_calls:.5f}")
        print(f"Costo proyectado, corpus completo (40 docs): "
              f"${costo_total / extractor.api_calls * 40:.4f}")
    else:
        print("(0 llamadas: esta corrida usó 100% caché — ver examples/cache-extraccion-llm.json)")

    print(
        "\nPara calibrar: el análisis de potencia de 04 §3 dijo que hacen falta ~683\n"
        "queries de golden para detectar una caída de calidad de 5 puntos. Extraer\n"
        "la ontología completa del corpus (40 docs) cuesta menos de un centavo con\n"
        "este modelo — el costo NUNCA es el obstáculo para escalar la extracción;\n"
        "la calidad de la resolución de identificadores (ver §2 de este script) sí."
    )


def demo_donde_el_humano_sigue_haciendo_falta() -> None:
    """Lo que el extractor, por diseño, no puede ver."""
    seccion("4. Dónde el humano sigue siendo necesario")

    print(
        "El prompt de extracción solo puede encontrar relaciones que el TEXTO\n"
        "declara explícitamente. Hay al menos dos categorías que se le escapan\n"
        "por diseño, no por un prompt insuficiente:\n"
        "\n"
        "1. RELACIONES IMPLÍCITAS: dos normas que regulan la misma materia sin\n"
        "   citarse. Ejemplo real del corpus: `norma-01` (Ley de Lobby, 20.730) y\n"
        "   `norma-02` (Ley de Probidad, 20.880) regulan materias contiguas\n"
        "   —ambas tocan conflictos de interés de funcionarios públicos— y NINGÚN\n"
        "   documento del corpus las conecta explícitamente. Un extractor de texto\n"
        "   nunca las va a relacionar porque no hay ninguna oración que lo diga.\n"
        "   Detectarlo requiere conocimiento de dominio: alguien que sepa que\n"
        "   'lobby' y 'declaración de intereses' son instrumentos de la misma\n"
        "   política de transparencia, aunque el corpus no lo declare.\n"
        "\n"
        "2. RELACIONES QUE DEPENDEN DE CONTEXTO DOCUMENTAL: la anáfora de §4\n"
        "   ('este Servicio') requiere saber quién emite el documento, información\n"
        "   que vive en el encabezado, no en la oración donde aparece la mención.\n"
        "   El prompt de esta sección no la resuelve — necesitaría el emisor como\n"
        "   input adicional, exactamente el insumo que §4 identificó como\n"
        "   necesario y que este extractor todavía no recibe."
    )


def grafico_efecto_resolucion() -> None:
    """El salto de precisión/recall al agregar el nivel 'número' (medido
    a mano en esta sección: 25/22/23 -> 38/52/44)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Valores tomados de las dos corridas de esta sección: solo diccionario
    # exacto + difuso (umbral 0.85) vs. con el nivel intermedio por número.
    metricas = ["Precisión", "Recall", "F1"]
    sin_numero = [0.25, 0.22, 0.23]
    con_numero = [0.38, 0.52, 0.44]

    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    x = range(len(metricas))
    w = 0.35
    ax.bar([i - w / 2 for i in x], sin_numero, width=w, label="sin nivel 'número'", color="#e74c3c")
    ax.bar([i + w / 2 for i in x], con_numero, width=w, label="con nivel 'número'", color="#2ecc71")
    ax.set_xticks(list(x))
    ax.set_xticklabels(metricas)
    ax.set_ylim(0, 0.6)
    ax.set_ylabel("proporción")
    ax.set_title(
        "El nivel de resolución 'número' (no la extracción) explica\n"
        "la mayor parte de la mejora", fontsize=11,
    )
    ax.legend(fontsize=9)
    for i, (a, b) in enumerate(zip(sin_numero, con_numero)):
        ax.text(i - w / 2, a + 0.01, f"{a:.0%}", ha="center", fontsize=8)
        ax.text(i + w / 2, b + 0.01, f"{b:.0%}", ha="center", fontsize=8)

    fig.tight_layout()
    out = Path(__file__).resolve().parent.parent / "diagrams" / "efecto-resolucion-numero.png"
    fig.savefig(out, dpi=130)
    print(f"\n[diagrama] {out.relative_to(ROOT)}")


if __name__ == "__main__":
    log.info(f"Extrayendo relaciones con {MODEL} (caché en {CACHE_PATH.name}).")
    normas, gt = cargar_ground_truth()
    extractor = LLMExtractor(model=MODEL, cache_path=CACHE_PATH)
    resultados = demo_extraccion(extractor, normas)
    demo_precision_recall(resultados, gt)
    demo_costo(extractor)
    demo_donde_el_humano_sigue_haciendo_falta()
    grafico_efecto_resolucion()
    print()
