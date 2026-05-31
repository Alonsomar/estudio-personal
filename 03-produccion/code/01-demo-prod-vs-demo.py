"""Sección 1 — Demo del gap demo→producción.

Tres mini-experimentos sobre el RAG de 02-retrieval que cuantifican lo que
el notebook NO te enseña:

  A. Varianza estocástica: misma pregunta, mismo contexto, distintas salidas.
     Lo que en dev parecía "la respuesta" es realmente "una muestra de una
     distribución". Para temp=0.7 mostramos N=4 corridas con diff por
     pares.
  B. Costo proyectado: tu producto vive del LLM. Tabla de $/mes para tres
     escalas (100, 10.000 y 1.000.000 queries/mes) en tres modelos de
     calidad/precio distintos.
  C. Catálogo de modos de falla típicos en RAG con LLM, con el mapping a
     qué sección de esta masterclass los aborda.

Caché en disco para que re-ejecutar sea gratis y reproducible. Costo total
de la primera corrida: < $0.005.

Ejecutar:
    uv run python 03-produccion/code/01-demo-prod-vs-demo.py
"""

from __future__ import annotations

import difflib
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.llm_clients import get_openai_client  # noqa: E402
from shared.utils import get_project_root  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "02-retrieval" / "code"))
from retrieval_lib import (  # noqa: E402
    BM25Retriever,
    DenseRetriever,
    HybridRetriever,
    OpenAIEmbedder,
    load_corpus_chunks,
)

ROOT = get_project_root()
CORPUS_DIR = ROOT / "shared" / "corpus_chileno"
EMB_CACHE = ROOT / "02-retrieval" / "examples" / "cache-embeddings" / "embeddings.npz"
EXAMPLES = ROOT / "03-produccion" / "examples"
SAMPLE_CACHE = EXAMPLES / "01-variance-samples.json"


def hr(title: str = "") -> None:
    print("\n" + "=" * 86)
    if title:
        print(title)
        print("=" * 86)


# ---------------------------------------------------------------- #
# Tarifas públicas (USD por 1M tokens). 2026-Q2, aproximadas. Cambian.
# ---------------------------------------------------------------- #
PRICING = {
    "gpt-4o-mini":   {"in_per_M": 0.150,  "out_per_M": 0.600},
    "haiku-4.5":     {"in_per_M": 0.800,  "out_per_M": 4.000},
    "sonnet-4.6":    {"in_per_M": 3.000,  "out_per_M": 15.000},
}


def _key(prompt: str, model: str, temperature: float, run_tag: str) -> str:
    """La clave incluye un tag de corrida porque temp>0 produce salidas distintas
    cada vez; cacheamos cada muestra con su tag explícito."""
    return hashlib.sha1(
        f"{model}|{temperature}|{run_tag}|{prompt}".encode("utf-8")
    ).hexdigest()


def _load_cache() -> dict:
    if SAMPLE_CACHE.exists():
        return json.loads(SAMPLE_CACHE.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict) -> None:
    SAMPLE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_CACHE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def call_llm(
    client,
    prompt: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.0,
    run_tag: str = "0",
    cache: dict | None = None,
) -> dict:
    """Llamada con caché por (model, temperature, run_tag, prompt).

    Para temp>0, distintos run_tag fuerzan llamadas nuevas (muestras de la
    distribución). Si la entrada queda en caché, se reproduce gratis.
    """
    cache = cache if cache is not None else _load_cache()
    k = _key(prompt, model, temperature, run_tag)
    if k in cache:
        return cache[k]
    t0 = time.perf_counter()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    dt = (time.perf_counter() - t0) * 1000
    out = {
        "text": (resp.choices[0].message.content or "").strip(),
        "in_tokens": resp.usage.prompt_tokens,
        "out_tokens": resp.usage.completion_tokens,
        "latency_ms": dt,
        "model": model,
        "temperature": temperature,
        "run_tag": run_tag,
    }
    cache[k] = out
    _save_cache(cache)
    return out


def build_rag_prompt(query: str, chunks: list[str]) -> str:
    ctx = "\n\n".join(f"[Fragmento {i + 1}]\n{c}" for i, c in enumerate(chunks))
    return (
        "Eres un asistente especializado en normativa fiscal y regulatoria "
        "chilena. Responde la pregunta usando SOLO los fragmentos provistos. "
        "Si la respuesta no está en los fragmentos, dilo explícitamente.\n\n"
        f"FRAGMENTOS:\n{ctx}\n\n"
        f"PREGUNTA: {query}\n\n"
        "RESPUESTA:"
    )


def first_diff(a: str, b: str, n: int = 80) -> str:
    """Primera ventana de N caracteres alrededor del primer carácter distinto."""
    if a == b:
        return "(idénticos)"
    for i, (ca, cb) in enumerate(zip(a, b)):
        if ca != cb:
            start = max(0, i - 10)
            return f"@pos {i}: ...{a[start:start + n]!r} vs ...{b[start:start + n]!r}"
    # Una salida es prefijo de la otra
    return f"(prefijo) longitudes: {len(a)} vs {len(b)}"


def main() -> None:
    chunks = load_corpus_chunks(CORPUS_DIR)
    embedder = OpenAIEmbedder(cache_path=EMB_CACHE)
    bm25 = BM25Retriever().fit(chunks)
    dense = DenseRetriever(embedder).fit(chunks)
    hybrid = HybridRetriever([bm25, dense], method="rrf", pool=20)

    # ============================================================ #
    hr("DEMO A — Varianza estocástica: la 'respuesta' es una distribución")
    # Query con respuesta NATURALMENTE más larga: ahí la temperatura se nota.
    # (Una query 'cuánto es la tasa' satura el output a '19%' y no muestra varianza.)
    query = (
        "Explica brevemente cómo opera el régimen de IVA a servicios digitales "
        "de proveedores extranjeros en Chile y qué obligaciones tienen."
    )
    print(f"\n  Query: '{query}'")

    top = hybrid.search(query, k=3)
    context = [r.chunk.text for r in top]
    prompt = build_rag_prompt(query, context)
    print(
        f"  RAG: 3 chunks recuperados ({sum(len(c) for c in context)} chars "
        f"de contexto, prompt: {len(prompt)} chars)."
    )

    client = get_openai_client()
    cache = _load_cache()
    # Una determinista (temp=0) y tres estocásticas (temp=0.9, sin seed).
    runs: list[dict] = []
    runs.append(call_llm(client, prompt, temperature=0.0, run_tag="det", cache=cache))
    for tag in ["sto-1", "sto-2", "sto-3"]:
        runs.append(call_llm(client, prompt, temperature=0.9, run_tag=tag, cache=cache))

    print(f"\n  {'corrida':10s} {'T':>5s} {'in_tok':>7s} {'out_tok':>8s} {'lat_ms':>8s}  primeras 80 chars de la respuesta")
    print("  " + "-" * 130)
    for r in runs:
        label = "det" if r["temperature"] == 0.0 else r["run_tag"]
        snippet = " ".join(r["text"].split())[:80]
        print(
            f"  {label:10s} {r['temperature']:5.1f} {r['in_tokens']:7d} "
            f"{r['out_tokens']:8d} {r['latency_ms']:8.0f}  {snippet}…"
        )

    sto_texts = [r["text"] for r in runs[1:]]
    print("\n  Diff entre pares de corridas estocásticas (primera divergencia):")
    for a, b in [(0, 1), (0, 2), (1, 2)]:
        print(f"    sto-{a + 1} vs sto-{b + 1}: {first_diff(sto_texts[a], sto_texts[b])}")
    sims = [
        difflib.SequenceMatcher(None, sto_texts[a], sto_texts[b]).ratio()
        for a, b in [(0, 1), (0, 2), (1, 2)]
    ]
    print(
        f"\n  Similitud (Ratcliff/Obershelp) entre los 3 pares: "
        f"{sims[0]:.3f}, {sims[1]:.3f}, {sims[2]:.3f}  (1.0 = idénticos)."
    )
    out_lens = [r["out_tokens"] for r in runs[1:]]
    print(
        f"  Longitudes de salida (out_tokens): {out_lens}  → "
        f"std/mean = {(sum((x - sum(out_lens) / 3) ** 2 for x in out_lens) / 3) ** 0.5 / (sum(out_lens) / 3):.2%}"
    )
    print(
        "\n  Lectura: con T=0.9 y la MISMA query, MISMO contexto, MISMO prompt:\n"
        "  - dos muestras se ABSTUVIERON con textos distintos,\n"
        "  - una respondió la pregunta completa,\n"
        "  - la longitud de salida varió 3× (28-95 tokens).\n"
        "  La 'respuesta del sistema' no existe — existe una DISTRIBUCIÓN\n"
        "  sobre respuestas. Para el usuario que recibió la abstención, el\n"
        "  producto falló; para el siguiente con la misma query, funcionó.\n"
        "  La decisión de §3 (prompts) y §8 (modelos) es estabilizar esto:\n"
        "  bajar temperatura, agregar instrucción explícita de abstención,\n"
        "  medir con bootstrap (01-evals §8) y no con corrida única."
    )

    # ============================================================ #
    hr("DEMO B — Costo proyectado: el LLM es la línea más volátil del P&L")
    # Tomamos los tokens promedio observados en la demo A como base realista
    avg_in = sum(r["in_tokens"] for r in runs) / len(runs)
    avg_out = sum(r["out_tokens"] for r in runs) / len(runs)
    print(
        f"\n  Base: {avg_in:.0f} tokens entrada + {avg_out:.0f} tokens salida por query\n"
        f"  (medidos en la demo A: chunks RAG + prompt + respuesta)."
    )

    scales = [
        ("Producto en validación (β)",      100),
        ("Crecimiento (1k usuarios)",   10_000),
        ("Escala (10k usuarios activos)", 1_000_000),
    ]
    print(f"\n  $USD/mes por escala y modelo (tarifas 2026-Q2, [aprox.]):\n")
    print(f"  {'escenario':32s} {'queries/mes':>13s}  | " + " ".join(f"{m:>14s}" for m in PRICING))
    print("  " + "-" * 110)
    for label, qpm in scales:
        row = f"  {label:32s} {qpm:>13,d}  | "
        for model, p in PRICING.items():
            cost = (avg_in * qpm * p["in_per_M"] + avg_out * qpm * p["out_per_M"]) / 1_000_000
            row += f"{f'${cost:,.2f}':>14s} "
        print(row)
    print(
        "\n  Lectura: con 1M queries/mes, la diferencia entre gpt-4o-mini y\n"
        "  sonnet-4.6 son MILES de USD mensuales. La decisión 'qué modelo' es\n"
        "  una decisión presupuestaria, no estética. Y depende de cuántas\n"
        "  queries son resolubles por un modelo más barato — la base de §10."
    )

    # ============================================================ #
    hr("DEMO C — Catálogo de modos de falla de un RAG en producción")
    failures = [
        ("Proveedor del LLM caído (503)", "Anthropic devuelve 503 → tu endpoint queda colgado", "§6 reliability"),
        ("Rate limit del proveedor (429)", "Tu cliente excedió tokens/min → respuestas erróneas a usuarios", "§6 reliability"),
        ("Latencia tail (p99)", "Una de cada 50 queries tarda 30s; el promedio se ve bien", "§5 observabilidad + §6"),
        ("Costo desbocado", "Un usuario en loop, una tool que itera; nadie alerta hasta fin de mes", "§10 costo"),
        ("Regresión silenciosa de prompt", "Alguien cambia un prompt 'sin importancia'; el golden offline cae 8%", "§3 prompts + §9 online eval"),
        ("Versión del modelo cambió", "Anthropic actualiza 'claude-sonnet-4'; comportamiento cambia bajo tus pies", "§8 versionado"),
        ("Caché stale", "Cambiaste el corpus; el response cache sirve la respuesta vieja", "§4 caching"),
        ("Prompt injection desde corpus", "Un chunk contiene 'ignora instrucciones'; el LLM obedece", "§11 seguridad"),
        ("PII en logs", "Logueás el prompt completo; aparecen RUTs en tu sistema de logs", "§11 seguridad"),
        ("Drift del tráfico", "Tus queries cambiaron de distribución; tu golden ya no representa", "§9 online evals"),
        ("Alucinación masiva", "Cambio de modelo + golden no-detectado → 30% de respuestas inventadas", "§9 + §12"),
        ("Embedding cache corrupto", "Hash colisionó, vector incorrecto sirve a una query nueva", "§4 + §12 incidentes"),
    ]
    print(f"\n  {'modo de falla':40s} {'descripción':55s} {'cubierto en':18s}")
    print("  " + "-" * 115)
    for name, desc, where in failures:
        print(f"  {name:40s} {desc:55s} {where:18s}")
    print(
        "\n  Lectura: ninguno de estos modos aparece en tu notebook. Aparecen\n"
        "  el día 1 de producción y desde el día 1 hay que poder verlos y\n"
        "  reaccionar. La masterclass es el inventario de capas que evitan que\n"
        "  cada uno te sorprenda."
    )

    # ============================================================ #
    hr("Resumen")
    total_cost_demo = sum(
        (r["in_tokens"] * PRICING["gpt-4o-mini"]["in_per_M"]
         + r["out_tokens"] * PRICING["gpt-4o-mini"]["out_per_M"]) / 1_000_000
        for r in runs
    )
    print(
        f"\n  Costo total de esta demo (4 llamadas gpt-4o-mini): ${total_cost_demo:.5f}.\n"
        f"  Cacheado en {SAMPLE_CACHE.relative_to(ROOT)} — re-ejecutar es gratis.\n"
    )


if __name__ == "__main__":
    main()
