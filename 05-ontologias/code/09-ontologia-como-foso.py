"""§9 — Ontología curada y competency questions compiladas.

Compara 18 preguntas estructurales congeladas con tres réplicas de un LLM
que recibe el corpus completo. La salida del grafo es una comprobación de
consistencia contra el golden, no una estimación independiente.

    uv run python 05-ontologias/code/09-ontologia-como-foso.py
    uv run python 05-ontologias/code/09-ontologia-como-foso.py --allow-api
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ontology_lib import (  # noqa: E402
    DEFAULT_TARIFF_USD_PER_M,
    CompetencyQuestion,
    LLMCacheEntry,
    Norma,
    OfflineCacheMiss,
    RelacionNormativa,
    build_grafo_normativo,
    historical_cost,
    llm_cache_key,
    load_versioned_cache,
    prompt_sha256,
    save_versioned_cache,
)
from shared.utils import get_logger, get_project_root  # noqa: E402

log = get_logger(__name__)
CORPUS_DIR = get_project_root() / "shared" / "corpus_chileno"
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
DATA_PATH = EXAMPLES / "relaciones-manual.json"
GOLDEN_PATH = EXAMPLES / "golden-ontology.json"
CACHE_PATH = EXAMPLES / "cache-foso-llm-crudo.json"
MODEL = "gpt-4o-mini"
N_REPLICAS = 3
MAX_CALLS = 54
MAX_COST_USD = 1.0


class RespuestaSinGrafo(BaseModel):
    documentos: list[str]
    razonamiento: str


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def cargar_datos():
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    normas = [Norma(**n) for n in raw["normas"]]
    relaciones = [RelacionNormativa(**r) for r in raw["relaciones"]]
    golden_raw = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    preguntas = [CompetencyQuestion(**item) for item in golden_raw["items"]]
    return build_grafo_normativo(normas, relaciones), preguntas


def compilar_pregunta(grafo, pregunta: CompetencyQuestion) -> set[str]:
    """Ejecuta la CQ sin leer ``expected_doc_ids``."""
    permitidos = set(pregunta.relation_types)
    vistos = {pregunta.target_node}
    frontera = {pregunta.target_node}
    respuesta: set[str] = set()
    for _ in range(pregunta.max_hops):
        siguiente: set[str] = set()
        for nodo in sorted(frontera):
            edges = (
                grafo.out_edges(nodo, data=True)
                if pregunta.direction == "out"
                else grafo.in_edges(nodo, data=True)
            )
            for origen, destino, data in edges:
                if data["tipo"] not in permitidos:
                    continue
                vecino = destino if pregunta.direction == "out" else origen
                if vecino not in vistos:
                    siguiente.add(vecino)
        respuesta.update(siguiente)
        vistos.update(siguiente)
        frontera = siguiente
    return respuesta


def corpus_completo() -> str:
    return "\n\n".join(
        f"=== {path.name} ===\n{path.read_text(encoding='utf-8')}"
        for path in sorted(CORPUS_DIR.glob("*.txt"))
    )


def construir_prompt(pregunta: CompetencyQuestion, corpus: str) -> str:
    return (
        "Tienes el corpus regulatorio chileno completo, con el nombre de archivo "
        "antes de cada documento. Responde la pregunta usando solo relaciones "
        "explícitas del texto. Devuelve exclusivamente nombres de archivo en "
        "`documentos`; si no hay ninguno, devuelve una lista vacía. No inventes "
        "nombres.\n\n"
        f"{corpus}\n\nPREGUNTA ({pregunta.id}): {pregunta.question}\n"
        f"Máximo de saltos: {pregunta.max_hops}."
    )


class BenchmarkLLM:
    def __init__(self, *, allow_api: bool) -> None:
        self.allow_api = allow_api
        self.entries = load_versioned_cache(CACHE_PATH)
        self.api_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0

    def preguntar(
        self, pregunta: CompetencyQuestion, corpus: str, replica: int
    ) -> RespuestaSinGrafo:
        prompt = construir_prompt(pregunta, corpus)
        key = llm_cache_key(
            model=MODEL,
            prompt=prompt,
            schema=RespuestaSinGrafo,
            schema_version="respuesta-cq-v1",
            temperature=0.0,
            replica=replica,
        )
        if key in self.entries:
            return RespuestaSinGrafo.model_validate(self.entries[key].response)
        if not self.allow_api:
            raise OfflineCacheMiss(
                f"cache miss en {pregunta.id}, réplica {replica}; ejecute una vez "
                "con --allow-api para poblar las 54 respuestas"
            )
        if self.api_calls >= MAX_CALLS:
            raise RuntimeError(f"límite de {MAX_CALLS} llamadas alcanzado")
        estimado = historical_cost(max(len(prompt) // 4, 1), 1_000)
        actual = historical_cost(self.tokens_in, self.tokens_out)
        if actual + estimado > MAX_COST_USD:
            raise RuntimeError("presupuesto de USD 1,00 excedido antes de una nueva llamada")

        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        from openai import OpenAI

        response = OpenAI().chat.completions.parse(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format=RespuestaSinGrafo,
            temperature=0.0,
        )
        parsed = response.choices[0].message.parsed
        tokens_in = response.usage.prompt_tokens if response.usage else 0
        tokens_out = response.usage.completion_tokens if response.usage else 0
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.api_calls += 1
        self.entries[key] = LLMCacheEntry(
            response=parsed.model_dump(mode="json"),
            model_requested=MODEL,
            model_returned=response.model,
            prompt_version="cq-corpus-completo-v1",
            schema_version="respuesta-cq-v1",
            temperature=0.0,
            prompt_sha256=prompt_sha256(prompt),
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            tariff_usd_per_m=DEFAULT_TARIFF_USD_PER_M,
            historical_cost_usd=historical_cost(tokens_in, tokens_out),
            replica=replica,
        )
        save_versioned_cache(CACHE_PATH, self.entries)
        return parsed


def metricas(predichos: set[str], esperados: set[str]) -> dict[str, float]:
    tp = len(predichos & esperados)
    precision = tp / len(predichos) if predichos else (1.0 if not esperados else 0.0)
    recall = tp / len(esperados) if esperados else (1.0 if not predichos else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "exact_set": float(predichos == esperados),
    }


def bootstrap_media(valores: list[float], n: int = 5_000) -> tuple[float, float, float]:
    rng = random.Random(7)
    medias = sorted(
        sum(rng.choice(valores) for _ in valores) / len(valores) for _ in range(n)
    )
    return statistics.mean(valores), medias[int(0.025 * n)], medias[int(0.975 * n)]


def ejecutar(allow_api: bool) -> None:
    grafo, preguntas = cargar_datos()
    corpus = corpus_completo()
    cliente = BenchmarkLLM(allow_api=allow_api)

    seccion("1. Ontología curada + competency questions compiladas")
    inconsistencias = []
    for q in preguntas:
        obtenido = compilar_pregunta(grafo, q)
        esperado = set(q.expected_doc_ids)
        if obtenido != esperado:
            inconsistencias.append((q.id, sorted(obtenido ^ esperado)))
    print(f"Preguntas compiladas: {len(preguntas)}")
    print(f"Inconsistencias grafo↔golden: {len(inconsistencias)}")
    if inconsistencias:
        raise RuntimeError(inconsistencias)
    print("El 100% comprueba consistencia interna; no es una estimación independiente.")

    seccion("2. LLM sobre corpus completo: 18 preguntas × 3 réplicas")
    promedios_pregunta: list[dict[str, float]] = []
    variaciones: list[float] = []
    corpus_ids = {p.name for p in CORPUS_DIR.glob("*.txt")}
    for q in preguntas:
        por_replica = []
        for replica in range(N_REPLICAS):
            respuesta = cliente.preguntar(q, corpus, replica)
            predichos = set(respuesta.documentos)
            # Los nombres inexistentes permanecen: cuentan en el denominador
            # de precisión como falsos positivos.
            por_replica.append(metricas(predichos, set(q.expected_doc_ids)))
            inexistentes = sorted(predichos - corpus_ids)
            if inexistentes:
                print(f"  {q.id}/r{replica}: nombres inexistentes={inexistentes}")
        promedio = {
            metrica: statistics.mean(r[metrica] for r in por_replica)
            for metrica in ("precision", "recall", "f1", "exact_set")
        }
        promedios_pregunta.append(promedio)
        variaciones.append(statistics.pstdev(r["f1"] for r in por_replica))
        print(
            f"  {q.id} {q.category:>9}: P={promedio['precision']:.3f} "
            f"R={promedio['recall']:.3f} F1={promedio['f1']:.3f} "
            f"exact={promedio['exact_set']:.3f}"
        )

    seccion("3. Agregación sobre 18 preguntas, no sobre 54 llamadas")
    for nombre in ("precision", "recall", "f1", "exact_set"):
        valores = [p[nombre] for p in promedios_pregunta]
        media, lo, hi = bootstrap_media(valores)
        print(f"{nombre:>10}: {media:.3f}  IC95% [{lo:.3f}, {hi:.3f}]")
    deltas_f1 = [p["f1"] - 1.0 for p in promedios_pregunta]
    delta, lo, hi = bootstrap_media(deltas_f1)
    print(f"\nΔF1 LLM−conocimiento curado: {delta:+.3f}  IC95% [{lo:+.3f}, {hi:+.3f}]")
    print(f"Variación media entre réplicas (DE de F1): {statistics.mean(variaciones):.3f}")
    if hi < 0:
        print("Hay una brecha detectable de recuperación respecto del conocimiento curado.")
    else:
        print("El IC incluye cero: el experimento no demuestra una diferencia detectable.")

    hist_in = sum(e.tokens_input for e in cliente.entries.values())
    hist_out = sum(e.tokens_output for e in cliente.entries.values())
    hist_cost = sum(e.historical_cost_usd for e in cliente.entries.values())
    print(f"\nLlamadas de esta corrida: {cliente.api_calls}")
    print(f"Tokens actuales in/out: {cliente.tokens_in:,}/{cliente.tokens_out:,}")
    print(f"Uso histórico cacheado: {hist_in:,}/{hist_out:,}; costo=${hist_cost:.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-api", action="store_true")
    args = parser.parse_args()
    log.info("Evaluando 18 competency questions con tres réplicas.")
    ejecutar(args.allow_api)
