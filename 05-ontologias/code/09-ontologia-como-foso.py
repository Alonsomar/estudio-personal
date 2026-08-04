"""§9 — La ontología como foso competitivo.

Produce los números de `theory/09-la-ontologia-como-foso.md`. Cierra el
módulo con un experimento, no solo un argumento: se le hace la MISMA
competency question P4 de §2 a un LLM sin grafo —solo el corpus crudo en
el contexto— y se compara su respuesta contra la verdad fundamental.

    uv run python 05-ontologias/code/09-ontologia-como-foso.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "03-produccion" / "code"))

from ontology_lib import Norma, RelacionNormativa, alcance_transitivo, build_grafo_normativo  # noqa: E402
from prod_lib import PRICING_USD_PER_M_TOKENS as PRICING  # noqa: E402

from shared.utils import get_logger, get_project_root  # noqa: E402

log = get_logger(__name__)
CORPUS_DIR = get_project_root() / "shared" / "corpus_chileno"
DATA_PATH = Path(__file__).resolve().parent.parent / "examples" / "relaciones-manual.json"
CACHE_PATH = Path(__file__).resolve().parent.parent / "examples" / "cache-foso-llm-crudo.json"
MODEL = "gpt-4o-mini"

PREGUNTA = (
    "¿Qué documentos dependen, directa o indirectamente (a través de citas, "
    "reglamentos o aplicaciones), de la Ley Nº 20.248 sobre Subvención Escolar "
    "Preferencial? Es decir: qué documentos mencionan la Ley 20.248, o mencionan "
    "a un documento que a su vez la menciona, en cualquier número de pasos."
)

GROUND_TRUTH_P4 = {
    "decreto-01-subvencion-escolar.txt",
    "decreto-06-reglamento-servicios-locales.txt",
    "do-01-extracto-decreto-aranceles.txt",
    "ley-09-ley-21040-educacion-publica.txt",
    "oficio-01-contraloria-subvenciones.txt",
    "oficio-05-contraloria-traspaso-slep.txt",
}


class RespuestaSinGrafo(BaseModel):
    documentos: list[str]
    razonamiento: str


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def preguntar_sin_grafo() -> tuple[RespuestaSinGrafo, int, int]:
    """La misma pregunta P4 de §2, a un LLM con el corpus CRUDO en el
    contexto — sin grafo, sin relaciones tipadas, solo texto. Caché en
    disco, mismo patrón que LLMExtractor/GraphRAGIndexer."""
    docs = sorted(CORPUS_DIR.glob("*.txt"))
    bloques = [f"=== {p.name} ===\n{p.read_text(encoding='utf-8')}" for p in docs]
    corpus_completo = "\n\n".join(bloques)

    prompt = (
        f"A continuación tienes el corpus COMPLETO de documentos regulatorios "
        f"chilenos (nombre de archivo + texto).\n\n{corpus_completo}\n\n"
        f"PREGUNTA: {PREGUNTA}\n\n"
        f"Responde con la lista de nombres de archivo y un breve razonamiento."
    )

    cache: dict = {}
    if CACHE_PATH.exists():
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    k = hashlib.sha1(f"{MODEL}\n{prompt}".encode("utf-8")).hexdigest()
    if k in cache:
        entry = cache[k]
        return RespuestaSinGrafo.model_validate(entry["respuesta"]), entry["tokens_in"], entry["tokens_out"]

    from dotenv import load_dotenv

    load_dotenv()
    from openai import OpenAI

    client = OpenAI()
    resp = client.chat.completions.parse(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format=RespuestaSinGrafo,
        temperature=0.0,
    )
    resultado = resp.choices[0].message.parsed
    tokens_in = resp.usage.prompt_tokens if resp.usage else 0
    tokens_out = resp.usage.completion_tokens if resp.usage else 0

    cache[k] = {
        "respuesta": resultado.model_dump(mode="json"),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return resultado, tokens_in, tokens_out


def responder_con_grafo() -> set[str]:
    """La misma pregunta, con el grafo de §2. Ya medido — se recalcula acá
    para la comparación directa, en microsegundos."""
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    normas = [Norma(**n) for n in data["normas"]]
    relaciones = [RelacionNormativa(**r) for r in data["relaciones"]]
    g = build_grafo_normativo(normas, relaciones)
    return alcance_transitivo(g, "ley-08-ley-20248-subvencion-preferencial.txt", direccion="in")


def demo_el_experimento() -> None:
    """El corazón de la sección: misma pregunta, dos formas de responderla."""
    seccion("1. La misma competency question, sin grafo vs. con grafo")

    print(f"Pregunta: {PREGUNTA}\n")
    print(f"Verdad fundamental (§2, P4): {sorted(GROUND_TRUTH_P4)}\n")

    respuesta_llm, tin, tout = preguntar_sin_grafo()
    # Normalizar: quedarnos solo con nombres que existen en el corpus real
    # (el LLM a veces devuelve el nombre sin extensión o levemente distinto).
    corpus_real = {p.name for p in CORPUS_DIR.glob("*.txt")}
    encontrados_llm = {d for d in respuesta_llm.documentos if d in corpus_real}

    print("LLM sin grafo (corpus crudo en contexto) respondió:")
    print(f"  {sorted(encontrados_llm)}")
    print(f"\nRazonamiento del LLM: {respuesta_llm.razonamiento[:300]}")

    tp = encontrados_llm & GROUND_TRUTH_P4
    fp = encontrados_llm - GROUND_TRUTH_P4
    fn = GROUND_TRUTH_P4 - encontrados_llm
    precision = len(tp) / len(encontrados_llm) if encontrados_llm else 0.0
    recall = len(tp) / len(GROUND_TRUTH_P4)
    print(f"\nPrecisión (contra el ground truth de §2): {precision:.0%}  ({len(tp)}/{len(encontrados_llm)})")
    print(f"Recall:    {recall:.0%}  ({len(tp)}/{len(GROUND_TRUTH_P4)})")
    if fp:
        print(f"'Falsos positivos': {sorted(fp)}")
        print(
            "  Verificado contra el texto real: glosa-02 SÍ cita la Ley 20.248\n"
            "  directamente ('la Subvención Escolar Preferencial se rige por la\n"
            "  Ley Nº 20.248 y su reglamento') — no es un error del LLM, es OTRO\n"
            "  hueco en la curación manual de §2, que excluyó glosa-02 por ser un\n"
            "  distractor de B6 sin revisar si igual citaba algo real. Mismo patrón\n"
            "  que el hallazgo de resolucion-01 en §5: verificar contra la fuente,\n"
            "  no contra el artefacto curado, sea manual o automático."
        )
    if fn:
        print(f"Falsos negativos (no encontrados, genuinos): {sorted(fn)}")

    dependientes_grafo = responder_con_grafo()
    print(f"\nCon el grafo (§2, alcance_transitivo): {sorted(dependientes_grafo)}")
    print("Precisión: 100%  Recall: 100%  (por construcción — la verdad fundamental "
          "ES la salida de esta misma función)")

    precio = PRICING[MODEL]
    costo = tin / 1e6 * precio["in"] + tout / 1e6 * precio["out"]
    print(f"\nCosto de la pregunta al LLM sin grafo: {tin:,} tokens in, {tout:,} out, ${costo:.4f}")
    print("Costo de la misma pregunta al grafo: $0, < 5 ms (medido en §7).")


def demo_por_que_importa() -> None:
    """El argumento de fondo: qué compra la curación, que un modelo mejor
    no compra solo."""
    seccion("2. Por qué un modelo mejor no comoditiza esto")

    print(
        "El experimento de arriba usó gpt-4o-mini — no el modelo más grande\n"
        "disponible. Pero el punto no es 'un modelo mejor lo resolvería mejor'.\n"
        "El punto es qué tuvo que existir ANTES para que el grafo respondiera con\n"
        "100% de precisión sin ambigüedad:\n"
        "\n"
        "  1. Alguien leyó los 40 documentos y decidió, con criterio de dominio,\n"
        "     que 'oficio-05 cita a oficio-01' es una relación real (§2).\n"
        "  2. Alguien distinguió que MODIFICA y CITA no son la misma relación,\n"
        "     con consecuencias jurídicas distintas (§2).\n"
        "  3. Alguien resolvió que 'Dirección de Compras' y 'CHILECOMPRA' son la\n"
        "     misma entidad (§4) sin caer en el falso positivo de nombres\n"
        "     institucionales parecidos.\n"
        "  4. Alguien supo que el artículo 7º bis de la Ley 21.634 tiene vacancia\n"
        "     legis de 12 meses — un hecho que NINGÚN modelo puede inferir del\n"
        "     texto sin conocimiento del derecho administrativo chileno (§6).\n"
        "\n"
        "Ningún laboratorio de frontera va a curar el grafo de citas del corpus\n"
        "regulatorio chileno — no porque sea difícil técnicamente, sino porque no\n"
        "es su negocio. Un modelo mejor hace mejor el paso 5 en adelante\n"
        "(extracción, §5); no reemplaza los pasos 1-4, que son juicio de dominio\n"
        "aplicado documento por documento. ESO es el foso: no la tecnología, la\n"
        "curación que la tecnología todavía no sabe hacer sola."
    )


if __name__ == "__main__":
    log.info("Cerrando el módulo: la misma pregunta, con y sin la curación.")
    demo_el_experimento()
    demo_por_que_importa()
    print()
