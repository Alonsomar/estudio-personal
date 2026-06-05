"""Sección 4 — Caching multinivel: response cache + semantic cache.

Mide, sobre una misma carga sintética, el efecto de cada nivel de caché:

    sin caché  →  response cache (exacto)  →  + semantic cache (paráfrasis)

en tres ejes: costo USD, tokens facturados y latencia p50/p95.

Niveles (def. en `prod_lib.py`):
  1. Embedding cache  → ya vive en OpenAIEmbedder (02-retrieval); no se re-mide.
  2. Response cache   → hash(prompt+modelo+temp) → respuesta. Atrapa repeticiones
     EXACTAS ("todos preguntan lo mismo").
  3. Semantic cache   → coseno de la query; atrapa PARÁFRASIS que el exacto no ve.

Por defecto corre OFFLINE: un LLM simulado (latencia/tokens modelados, semilla
fija) y embeddings simulados por intención. Es gratis, determinista y genera
`diagrams/caching-niveles.png`. Con --live valida con embeddings REALES que las
paráfrasis superan el umbral y las queries no relacionadas no.

Ejecutar:

    uv run python 03-produccion/code/04-caching.py           # offline, gratis
    uv run python 03-produccion/code/04-caching.py --live     # valida con API real
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "02-retrieval" / "code"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from prod_lib import (  # noqa: E402
    LLMResponse,
    LRUCache,
    ResponseCache,
    SemanticCache,
)
from shared.utils import get_project_root  # noqa: E402

ROOT = get_project_root()
DIAGRAMS = ROOT / "03-produccion" / "diagrams"
SEP = "=" * 72


# --------------------------------------------------------------------------- #
# Carga sintética: intenciones fiscales con variantes (paráfrasis). Las hot
# se repiten mucho — patrón real de un RAG fiscal ("todos preguntan el IVA").
# --------------------------------------------------------------------------- #
INTENTS: dict[str, list[str]] = {
    "iva-digital": [
        "¿Cuál es la tasa de IVA para servicios digitales de proveedores extranjeros?",
        "¿Qué IVA pagan los servicios digitales extranjeros en Chile?",
        "Tasa de IVA aplicable a plataformas digitales foráneas",
    ],
    "lobby-multa": [
        "¿Cuál es la multa máxima por infracción a la Ley de Lobby?",
        "¿Cuánto es la sanción más alta de la Ley de Lobby?",
    ],
    "pni-presupuesto": [
        "¿Cuánto presupuesto se asigna al Programa Nacional de Inmunizaciones en 2024?",
        "Monto del presupuesto 2024 para el Programa Nacional de Inmunizaciones",
    ],
    "renta-tramos": [
        "¿Cuáles son los tramos del impuesto único de segunda categoría?",
        "Tramos del impuesto a la renta de las personas",
    ],
    "boleta-honorarios": [
        "¿Qué retención aplica a las boletas de honorarios en 2024?",
    ],
}
# Probabilidad de cada intención (skew: dos intenciones acaparan el tráfico).
INTENT_WEIGHTS = {
    "iva-digital": 0.38,
    "lobby-multa": 0.27,
    "pni-presupuesto": 0.16,
    "renta-tramos": 0.12,
    "boleta-honorarios": 0.07,
}


def make_workload(n: int, seed: int = 7) -> list[tuple[str, str]]:
    """Genera n requests (texto, intención). Hot intents se repiten; dentro de
    cada intención se elige una variante (a veces exacta, a veces paráfrasis)."""
    rng = np.random.default_rng(seed)
    intents = list(INTENT_WEIGHTS)
    probs = np.array([INTENT_WEIGHTS[i] for i in intents])
    stream = []
    for _ in range(n):
        intent = intents[int(rng.choice(len(intents), p=probs))]
        variant = INTENTS[intent][int(rng.integers(len(INTENTS[intent])))]
        stream.append((variant, intent))
    return stream


def _prompt(query: str) -> str:
    """Prompt mínimo. Lo que importa para el cache exacto es que el MISMO texto
    de query produzca el MISMO prompt → misma clave."""
    return f"Responde sobre normativa chilena: {query}"


# --------------------------------------------------------------------------- #
# LLM y embedder simulados (offline). Deterministas dada la semilla.
# --------------------------------------------------------------------------- #
class SimulatedLLMClient:
    """Implementa el Protocol LLMClient sin red. Latencia y tokens modelados.

    La latencia se MODELA (no se duerme): lognormal centrada en ~700ms, que es
    un rango realista para gpt-4o-mini con respuestas cortas. Determinista por
    semilla → el png es reproducible sin API.
    """

    name = "simulado"
    default_model = "gpt-4o-mini"

    def __init__(self, seed: int = 0, base_latency_ms: float = 700.0, sigma: float = 0.35):
        self._rng = np.random.default_rng(seed)
        self.base = base_latency_ms
        self.sigma = sigma

    def complete(self, prompt, *, model=None, temperature=0.0, max_tokens=512) -> LLMResponse:
        lat = float(self._rng.lognormal(mean=np.log(self.base), sigma=self.sigma))
        in_tok = max(1, len(prompt.split()) * 12)  # ~12 tokens/word incluyendo contexto RAG
        out_tok = int(self._rng.integers(18, 45))
        return LLMResponse(
            text="[simulado]",
            in_tokens=in_tok,
            out_tokens=out_tok,
            latency_ms=lat,
            model=model or self.default_model,
        )


def make_sim_embedder(workload: list[tuple[str, str]], seed: int = 3):
    """embed_fn simulado: cada intención tiene un vector base; toda variante de
    esa intención cae cerca (coseno ~0.99), y distintas intenciones quedan
    casi ortogonales. Simula lo que un embedder real hace con paráfrasis."""
    rng = np.random.default_rng(seed)
    dim = 64
    base = {intent: rng.standard_normal(dim) for intent in INTENTS}
    text2intent = {text: intent for text, intent in workload}

    def embed_fn(query: str) -> np.ndarray:
        intent = text2intent[query]
        jitter = rng.standard_normal(dim) * 0.04  # pequeño → variantes muy cercanas
        return base[intent] + jitter

    return embed_fn


# --------------------------------------------------------------------------- #
# 1. Mecánica del LRU: eviction + TTL.
# --------------------------------------------------------------------------- #
def demo_lru() -> None:
    print(SEP)
    print("1. LRU + TTL desde cero — mecánica")
    c = LRUCache(maxsize=3, ttl_s=None)
    for k in ["iva", "lobby", "pni", "renta"]:  # 4 entradas en cache de 3
        c.put(k, k.upper())
    print(f"  maxsize=3, inserté 4 → 'iva' fue desalojado: {c.get('iva') is None}")
    print(f"  'renta' sigue (más reciente): {c.get('renta')!r}")
    ttl = LRUCache(maxsize=10, ttl_s=0.15)
    ttl.put("vigente", 1)
    hit_antes = ttl.get("vigente")
    time.sleep(0.2)
    hit_despues = ttl.get("vigente")
    print(f"  TTL=0.15s → antes={hit_antes}, después de 0.2s={hit_despues}")
    print(f"  stats: {ttl.stats()}")


# --------------------------------------------------------------------------- #
# 2. Benchmark de los tres niveles sobre la misma carga.
# --------------------------------------------------------------------------- #
def _measure(records: list[dict]) -> dict:
    lat = np.array([r["latency_ms"] for r in records])
    return {
        "requests": len(records),
        "hits": sum(r["hit"] for r in records),
        "hit_rate": sum(r["hit"] for r in records) / len(records),
        "cost_usd": sum(r["billed_cost"] for r in records),
        "billed_tokens": sum(r["billed_tokens"] for r in records),
        "p50_ms": float(np.percentile(lat, 50)),
        "p95_ms": float(np.percentile(lat, 95)),
    }


def run_baseline(workload, llm) -> dict:
    recs = []
    for query, _ in workload:
        resp = llm.complete(_prompt(query))
        recs.append({
            "latency_ms": resp.latency_ms, "hit": False,
            "billed_cost": resp.cost_usd, "billed_tokens": resp.in_tokens + resp.out_tokens,
        })
    return _measure(recs)


def run_response_cache(workload, llm) -> tuple[dict, ResponseCache]:
    rc = ResponseCache(llm)
    recs = []
    for query, _ in workload:
        resp = rc.complete(_prompt(query))
        hit = resp.from_cache
        recs.append({
            "latency_ms": resp.latency_ms, "hit": hit,
            "billed_cost": 0.0 if hit else resp.cost_usd,
            "billed_tokens": 0 if hit else resp.in_tokens + resp.out_tokens,
        })
    return _measure(recs), rc


def run_semantic_cache(workload, llm, embed_fn, threshold=0.92) -> tuple[dict, SemanticCache]:
    rc = ResponseCache(llm)              # nivel 2 detrás
    sc = SemanticCache(embed_fn, threshold=threshold)  # nivel 3 adelante
    recs = []
    for query, _ in workload:
        t0 = time.perf_counter()
        val, _sim = sc.get(query)
        if val is not None:              # hit semántico: ni LLM ni response cache
            lookup_ms = (time.perf_counter() - t0) * 1000
            recs.append({"latency_ms": lookup_ms, "hit": True,
                         "billed_cost": 0.0, "billed_tokens": 0})
            continue
        resp = rc.complete(_prompt(query))   # puede ser hit exacto del nivel 2
        hit = resp.from_cache
        sc.put(query, resp)
        recs.append({
            "latency_ms": resp.latency_ms, "hit": hit,
            "billed_cost": 0.0 if hit else resp.cost_usd,
            "billed_tokens": 0 if hit else resp.in_tokens + resp.out_tokens,
        })
    return _measure(recs), sc


def demo_benchmark() -> dict:
    print("\n" + SEP)
    print("2. BENCHMARK — misma carga, tres configuraciones")
    n = 60
    workload = make_workload(n)
    uniques_text = len({t for t, _ in workload})
    uniques_intent = len({i for _, i in workload})
    print(f"  carga: {n} requests, {uniques_text} textos únicos, "
          f"{uniques_intent} intenciones.")

    # Mismo LLM simulado (misma semilla) para que el COSTO POR MISS sea idéntico
    # entre configs y la diferencia sea solo el caché.
    base = run_baseline(workload, SimulatedLLMClient(seed=0))
    resp, rc = run_response_cache(workload, SimulatedLLMClient(seed=0))
    sem, sc = run_semantic_cache(workload, SimulatedLLMClient(seed=0),
                                 make_sim_embedder(workload))

    rows = {"sin caché": base, "response cache": resp, "+ semantic cache": sem}
    print(f"\n  {'config':>18} | {'hit%':>5} | {'costo USD':>10} | "
          f"{'tokens':>8} | {'p50':>6} | {'p95':>6}")
    print(f"  {'-'*18}-+-{'-'*5}-+-{'-'*10}-+-{'-'*8}-+-{'-'*6}-+-{'-'*6}")
    for name, m in rows.items():
        print(f"  {name:>18} | {100*m['hit_rate']:>4.0f}% | {m['cost_usd']:>10.5f} | "
              f"{m['billed_tokens']:>8} | {m['p50_ms']:>5.0f} | {m['p95_ms']:>5.0f}")

    base_cost = base["cost_usd"]
    print("\n  ahorro de costo vs sin caché:")
    print(f"    response cache  : {100*(1-resp['cost_usd']/base_cost):.0f}%  "
          f"(repeticiones exactas; {rc.cache.stats()['hit_rate']:.0%} hit)")
    print(f"    + semantic      : {100*(1-sem['cost_usd']/base_cost):.0f}%  "
          f"(suma paráfrasis de la misma intención)")
    print(f"  semantic cache stats: {sc.stats()}")
    return rows


# --------------------------------------------------------------------------- #
# 3. Diagrama.
# --------------------------------------------------------------------------- #
def plot_niveles(rows: dict, out: Path) -> None:
    names = list(rows)
    colors = ["#e74c3c", "#f39c12", "#2ecc71"]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2))

    metrics = [
        ("cost_usd", "Costo total (USD)", lambda v: f"${v:.4f}"),
        ("billed_tokens", "Tokens facturados", lambda v: f"{v:,}"),
        ("p95_ms", "Latencia p95 (ms)", lambda v: f"{v:.0f}"),
    ]
    for ax, (key, title, fmt) in zip(axes, metrics):
        vals = [rows[n][key] for n in names]
        bars = ax.bar(names, vals, color=colors, edgecolor="#333", linewidth=0.8)
        ax.set_title(title, fontsize=11, fontweight="bold")
        ax.tick_params(axis="x", labelrotation=15, labelsize=8)
        ax.grid(axis="y", alpha=0.3)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, fmt(v),
                    ha="center", va="bottom", fontsize=8)
        ax.margins(y=0.18)

    # Anotar hit-rate bajo cada config en el primer panel.
    hr = " · ".join(f"{n}: {100*rows[n]['hit_rate']:.0f}% hit" for n in names[1:])
    fig.suptitle(f"Caching multinivel — {hr}", fontsize=12, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print(f"\n  diagrama guardado en {out.relative_to(ROOT)}")


# --------------------------------------------------------------------------- #
# 4. Validación --live: embeddings REALES separan paráfrasis de no-relacionadas.
# --------------------------------------------------------------------------- #
def demo_live() -> None:
    print("\n" + SEP)
    print("4. VALIDACIÓN --live — ¿el supuesto del semantic cache se sostiene?")

    import os

    from dotenv import load_dotenv

    load_dotenv()
    if not os.environ.get("OPENAI_API_KEY"):
        print("  OPENAI_API_KEY no configurada; salteando validación live.")
        return

    from retrieval_lib import OpenAIEmbedder

    emb_cache = ROOT / "03-produccion" / "examples" / "cache-embeddings-queries.npz"
    embedder = OpenAIEmbedder(cache_path=emb_cache)

    def embed_fn(q: str) -> np.ndarray:
        return embedder.embed([q])[0]

    # Umbral calibrado a embeddings REALES. Ojo: las paráfrasis genuinas caen
    # ~0.74-0.76 con text-embedding-3, MUY por debajo del "0.9 de manual". Usar
    # 0.9 con embeddings reales no cachearía casi nada.
    threshold = 0.70
    sc = SemanticCache(embed_fn, threshold=threshold)
    canon = INTENTS["iva-digital"][0]
    sc.put(canon, "respuesta-cacheada")
    print(f"  cacheada: {canon!r}")
    print(f"  umbral calibrado: {threshold}\n")

    pruebas = [
        ("paráfrasis", INTENTS["iva-digital"][1]),
        ("paráfrasis", INTENTS["iva-digital"][2]),
        ("otra intención", INTENTS["lobby-multa"][0]),
        ("no relacionada", "¿Cuál es la capital de Australia?"),
    ]
    print(f"  {'tipo':>16} | {'sim':>5} | hit | query")
    print(f"  {'-'*16}-+-{'-'*5}-+-----+-{'-'*40}")
    for tipo, q in pruebas:
        val, sim = sc.get(q)
        print(f"  {tipo:>16} | {sim:>5.3f} | {'sí' if val else 'no':>3} | {q[:42]}")
    print("\n  Lección de calibración: las paráfrasis reales (~0.75) están lejos del")
    print("  0.9 de manual pero MUY por encima de otra-intención (~0.3). El umbral")
    print("  se calibra contra ESTOS números, con un golden (01-evals), no a ojo:")
    print("  muy bajo sirve respuestas equivocadas; muy alto pierde paráfrasis.")


# --------------------------------------------------------------------------- #
def main() -> None:
    demo_lru()
    rows = demo_benchmark()
    plot_niveles(rows, DIAGRAMS / "caching-niveles.png")
    if "--live" in sys.argv:
        demo_live()
    print("\n" + SEP)
    print("Listo. El caché no cambia la lógica del RAG; cambia cuánto la pagás.")


if __name__ == "__main__":
    main()
