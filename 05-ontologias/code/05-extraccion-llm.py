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
import argparse
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


def demo_extraccion(
    extractor: LLMExtractor, normas: list[Norma], *, usar_numero: bool
) -> dict:
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
            destino_id, nivel = resolver_identificador_norma(
                rel.identificador_destino, normas, usar_numero=usar_numero
            )
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


def calcular_metricas(
    resultados: dict[str, list[RelacionNormativa]], gt: set
) -> dict[str, float | int | set]:
    """Precisión y recall contra la verdad fundamental curada a mano."""
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

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "gt": len(gt_muestra),
        "extraidas": len(extraidas),
        "tp": verdaderos_positivos,
        "fp": falsos_positivos,
        "fn": falsos_negativos,
    }


def demo_precision_recall(metricas: dict) -> None:
    seccion("2. Precisión y recall contra relaciones-manual.json (§2)")
    print(f"Ground truth en la muestra (10 docs):  {metricas['gt']} relaciones")
    print(f"Extraídas y resueltas:                 {metricas['extraidas']} relaciones")
    print(f"Verdaderos positivos (match exacto):   {len(metricas['tp'])}")
    print(f"Falsos positivos:                      {len(metricas['fp'])}")
    print(f"Falsos negativos (no detectadas):      {len(metricas['fn'])}")
    print(
        f"\nPrecisión: {metricas['precision']:.0%}   "
        f"Recall: {metricas['recall']:.0%}   F1: {metricas['f1']:.0%}"
    )

    if metricas["fp"]:
        print("\nFalsos positivos (el LLM extrajo algo que no está en la verdad fundamental):")
        for fp in sorted(metricas["fp"]):
            print(f"    {fp}")
    if metricas["fn"]:
        print("\nFalsos negativos (relaciones reales que el LLM no detectó):")
        for fn in sorted(metricas["fn"]):
            print(f"    {fn}")

    print(
        "\nOjo con la lectura de los falsos positivos: 'match exacto' exige que\n"
        "tipo Y destino coincidan con la ontología v2 auditada. La literalidad y\n"
        "cobertura del ground truth ya están comprobadas; un falso positivo es\n"
        "por tanto un error medido del pipeline bajo este criterio explícito."
    )


def demo_costo(extractor: LLMExtractor) -> None:
    """Costo medido con la aritmética de 04 §1, no supuesto."""
    seccion("3. Costo, con la aritmética de 04 §1")

    precio = PRICING[MODEL]
    costo_in = extractor.tokens_in / 1e6 * precio["in"]
    costo_out = extractor.tokens_out / 1e6 * precio["out"]
    costo_total = costo_in + costo_out
    hist_in, hist_out = extractor.historical_tokens

    print(f"Llamadas a la API en esta corrida:  {extractor.api_calls}")
    print(f"Tokens actuales in/out:             {extractor.tokens_in:,} / {extractor.tokens_out:,}")
    print(f"Costo de esta corrida ({MODEL}):    ${costo_total:.4f}")
    print(f"Uso histórico del caché in/out:     {hist_in:,} / {hist_out:,}")
    print(f"Costo histórico persistido:         ${extractor.historical_cost_usd:.4f}")
    if extractor.api_calls:
        print(f"Costo por documento:                ${costo_total / extractor.api_calls:.5f}")
        print(f"Costo proyectado, corpus completo (40 docs): "
              f"${costo_total / extractor.api_calls * 40:.4f}")
    else:
        print("(0 llamadas: esta corrida usó 100% caché — ver examples/cache-extraccion-llm.json)")

    print(
        "\nPara calibrar: el análisis de potencia de 04 §3 dijo que hacen falta ~683\n"
        "queries de golden para detectar una caída de calidad de 5 puntos. Extraer\n"
        "la ontología completa del corpus (40 docs) cuesta cerca de dos centavos con\n"
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
        "1. OMISIONES EXPLÍCITAS: la relación Lobby–Probidad sí aparece en el\n"
        "   texto de la Ley 20.880. El extractor no la recuperó y la primera\n"
        "   curación manual tampoco la había anotado. Es un falso negativo de\n"
        "   ambos procesos, corregido en el ground truth v2. Las relaciones\n"
        "   verdaderamente implícitas —materias afines sin referencia textual—\n"
        "   siguen siendo una limitación conceptual, pero este experimento no las\n"
        "   mide ni inventa aristas para representarlas.\n"
        "\n"
        "2. RELACIONES QUE DEPENDEN DE CONTEXTO DOCUMENTAL: la anáfora de §4\n"
        "   ('este Servicio') requiere saber quién emite el documento, información\n"
        "   que vive en el encabezado, no en la oración donde aparece la mención.\n"
        "   El prompt de esta sección no la resuelve — necesitaría el emisor como\n"
        "   input adicional, exactamente el insumo que §4 identificó como\n"
        "   necesario y que este extractor todavía no recibe."
    )


def grafico_efecto_resolucion(sin_numero: dict, con_numero: dict) -> None:
    """El salto de precisión/recall al agregar el nivel 'número' (medido
    a mano en esta sección: 25/22/23 -> 38/52/44)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    metricas = ["Precisión", "Recall", "F1"]
    sin_vals = [sin_numero["precision"], sin_numero["recall"], sin_numero["f1"]]
    con_vals = [con_numero["precision"], con_numero["recall"], con_numero["f1"]]

    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    x = range(len(metricas))
    w = 0.35
    ax.bar([i - w / 2 for i in x], sin_vals, width=w, label="sin nivel 'número'", color="#e74c3c")
    ax.bar([i + w / 2 for i in x], con_vals, width=w, label="con nivel 'número'", color="#2ecc71")
    ax.set_xticks(list(x))
    ax.set_xticklabels(metricas)
    ax.set_ylim(0, 0.6)
    ax.set_ylabel("proporción")
    ax.set_title(
        "El nivel de resolución 'número' (no la extracción) explica\n"
        "la mayor parte de la mejora", fontsize=11,
    )
    ax.legend(fontsize=9)
    for i, (a, b) in enumerate(zip(sin_vals, con_vals)):
        ax.text(i - w / 2, a + 0.01, f"{a:.0%}", ha="center", fontsize=8)
        ax.text(i + w / 2, b + 0.01, f"{b:.0%}", ha="center", fontsize=8)

    fig.tight_layout()
    out = Path(__file__).resolve().parent.parent / "diagrams" / "efecto-resolucion-numero.png"
    fig.savefig(out, dpi=130)
    print(f"\n[diagrama] {out.relative_to(ROOT)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-api", action="store_true")
    args = parser.parse_args()
    log.info(f"Extrayendo relaciones con {MODEL} (caché en {CACHE_PATH.name}).")
    normas, gt = cargar_ground_truth()
    extractor = LLMExtractor(
        model=MODEL, cache_path=CACHE_PATH, allow_api=args.allow_api, max_api_calls=10
    )
    resultados_con = demo_extraccion(extractor, normas, usar_numero=True)
    resultados_sin = demo_extraccion(extractor, normas, usar_numero=False)
    metricas_con = calcular_metricas(resultados_con, gt)
    metricas_sin = calcular_metricas(resultados_sin, gt)
    demo_precision_recall(metricas_con)
    demo_costo(extractor)
    demo_donde_el_humano_sigue_haciendo_falta()
    grafico_efecto_resolucion(metricas_sin, metricas_con)
    print()
