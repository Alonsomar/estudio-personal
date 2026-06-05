"""Sección 5 — Observabilidad: logs estructurados, métricas y tracing.

Demuestra las tres patas sobre el RAG:

  1. Logs estructurados (JSON con trace_id) vs print().
  2. Métricas (counter / gauge / histogram) sobre una carga con fallos
     inyectados → la lista priorizada de "qué medir": latencia p50/p95/p99,
     costo, tasa de fallos, hit rate de caché.
  3. Tracing: un request_id atraviesa retrieval → rerank → generación; el
     árbol de spans se exporta a examples/traces/.

Primitivas en `prod_lib.py` (StructuredLogger, MetricsRegistry, Tracer/Span),
hechas desde cero para ver qué hace OpenTelemetry por debajo.

Ejecutar:

    uv run python 03-produccion/code/05-tracing.py     # offline, gratis, determinista
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from prod_lib import (  # noqa: E402
    LLMResponse,
    MetricsRegistry,
    ResponseCache,
    StructuredLogger,
    Tracer,
)
from shared.utils import get_project_root  # noqa: E402

ROOT = get_project_root()
TRACES_DIR = ROOT / "03-produccion" / "examples" / "traces"
SEP = "=" * 72

QUERIES = [
    "¿Cuál es la tasa de IVA para servicios digitales extranjeros?",
    "¿Cuál es la multa máxima por infracción a la Ley de Lobby?",
    "¿Cuánto presupuesto tiene el Programa Nacional de Inmunizaciones 2024?",
    "¿Qué retención aplica a las boletas de honorarios?",
    "¿Cuáles son los tramos del impuesto único de segunda categoría?",
    "¿Cuál es el plazo para declarar la renta anual?",
    "¿Qué tasa tiene el impuesto de primera categoría en régimen Pro Pyme?",
    "¿Cuál es el monto exento del impuesto a la herencia?",
    "¿Qué requisitos hay para emitir factura electrónica?",
    "¿Cuál es la sanción por no emitir boleta?",
    "¿Dónde se publican oficialmente las leyes en Chile?",
    "¿Cuál es el plazo de prescripción de las deudas tributarias?",
]


def _prompt(q: str) -> str:
    return f"Responde sobre normativa chilena: {q}"


class SimLLM:
    """LLMClient simulado: latencia y tokens modelados, determinista por semilla."""

    name = "simulado"
    default_model = "gpt-4o-mini"

    def __init__(self, seed: int = 0):
        self._rng = np.random.default_rng(seed)

    def complete(self, prompt, *, model=None, temperature=0.0, max_tokens=512) -> LLMResponse:
        lat = float(self._rng.lognormal(mean=np.log(700.0), sigma=0.4))
        return LLMResponse(
            text="[simulado]",
            in_tokens=max(1, len(prompt.split()) * 12),
            out_tokens=int(self._rng.integers(18, 45)),
            latency_ms=lat,
            model=model or self.default_model,
        )


class ProviderError(RuntimeError):
    """Simula un 5xx del proveedor (se maneja de verdad en §6)."""


class FlakyLLM:
    """Envuelve un LLMClient y falla en una fracción de llamadas. Sirve para que
    el demo de métricas tenga una 'tasa de fallos' real que medir."""

    def __init__(self, base, fail_rate: float = 0.12, seed: int = 1):
        self.base = base
        self.name = base.name
        self.default_model = getattr(base, "default_model", None)
        self.fail_rate = fail_rate
        self._rng = np.random.default_rng(seed)

    def complete(self, prompt, *, model=None, temperature=0.0, max_tokens=512) -> LLMResponse:
        if self._rng.random() < self.fail_rate:
            raise ProviderError("503 Service Unavailable (proveedor)")
        return self.base.complete(
            prompt, model=model, temperature=temperature, max_tokens=max_tokens
        )


# --------------------------------------------------------------------------- #
# 1. Logs estructurados vs print.
# --------------------------------------------------------------------------- #
def demo_structured_logging() -> None:
    print(SEP)
    print("1. LOGS ESTRUCTURADOS — consultables, no prosa")
    print("\n  print() típico (¿cómo lo filtrás por modelo? ¿por trace?):")
    print('    Respondida query "iva" con gpt-4o-mini en 812ms ($0.000053)')

    log = StructuredLogger(service="rag-fiscal")
    tracer = Tracer()
    print("\n  StructuredLogger (una línea = un evento JSON con trace_id):")
    with tracer.trace("query", trace_id="7f3a9c2e", duration_ms=812.0):
        log.info("query_served", query="iva", model="gpt-4o-mini",
                 latency_ms=812, cost_usd=0.000053, from_cache=False, prompt_ref="rag-fiscal@v2")
    print("\n  → `event=query_served from_cache=false` se agrega y alerta sin regex.")


# --------------------------------------------------------------------------- #
# 2. Métricas sobre una carga con fallos.
# --------------------------------------------------------------------------- #
def demo_metrics() -> None:
    print("\n" + SEP)
    print("2. MÉTRICAS — qué medir, en orden de prioridad")

    # Cache alrededor del cliente flaky: un hit no llega a tocar al proveedor.
    llm = ResponseCache(FlakyLLM(SimLLM(seed=0), fail_rate=0.12, seed=1))
    m = MetricsRegistry()
    err_log = StructuredLogger(service="rag-fiscal", stream=io.StringIO())
    tracer = Tracer()

    rng = np.random.default_rng(7)
    n = 150
    sample_errors = []
    for i in range(n):
        q = QUERIES[int(rng.integers(len(QUERIES)))]  # repeticiones → cache hits
        with tracer.trace("query", trace_id=f"req-{i:03d}"):
            m.counter("requests_total").inc()
            try:
                resp = llm.complete(_prompt(q))
                if resp.from_cache:
                    m.counter("cache_hits_total").inc()
                else:
                    # Solo las llamadas REALES al proveedor entran a la latencia:
                    # mezclar hits de ~0ms tapa la latencia de generación real.
                    m.counter("provider_calls_total").inc()
                    m.histogram("latency_ms").observe(resp.latency_ms)
                m.histogram("cost_usd").observe(0.0 if resp.from_cache else resp.cost_usd)
            except ProviderError as e:
                m.counter("provider_calls_total").inc()  # llegó al proveedor y falló
                m.counter("errors_total").inc()
                rec = err_log.error("llm_error", query=q[:30], error=str(e))
                if len(sample_errors) < 2:
                    sample_errors.append(rec)
    m.gauge("cache_size").set(llm.cache.stats()["size"])

    snap = m.snapshot()
    reqs = snap["counters"]["requests_total"]
    errs = snap["counters"].get("errors_total", 0)
    hits = snap["counters"].get("cache_hits_total", 0)
    calls = snap["counters"].get("provider_calls_total", 0)
    lat = snap["histograms"]["latency_ms"]
    cost = snap["histograms"]["cost_usd"]

    print(f"\n  carga: {n} requests sobre {len(QUERIES)} queries; "
          f"{calls} llegaron al proveedor (el resto, cache)\n")
    print(f"  1. Latencia generación (ms): p50={lat['p50']:.0f}  p95={lat['p95']:.0f}  "
          f"p99={lat['p99']:.0f}   (n={lat['count']} llamadas reales; los hits son ~0ms)")
    print(f"  2. Costo (USD)     : total=${cost['sum']:.5f}  "
          f"medio=${cost['sum']/max(reqs,1):.6f}/req")
    print(f"  3. Tasa de fallos  : {errs}/{calls} = {100*errs/max(calls,1):.1f}% "
          "de las llamadas al proveedor (manejo real en §6)")
    print(f"  4. Hit rate caché  : {hits}/{reqs} = {100*hits/reqs:.0f}%  "
          f"(cache_size gauge={snap['gauges']['cache_size']:.0f})")
    print("  5. Calidad         : se mide con online-eval (§9) — no se infiere de logs")

    print("\n  muestra de logs de error (JSON, correlacionables por trace_id):")
    for rec in sample_errors:
        print("    " + json.dumps(rec, ensure_ascii=False, default=str))
    print("\n  snapshot crudo (lo que un scraper Prometheus leería):")
    print("    " + json.dumps(snap, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# 3. Tracing: el árbol de un request.
# --------------------------------------------------------------------------- #
def _stabilize_ids(node, _counter=None) -> None:
    """Reasigna span_id en preorden (s00, s01, …) para que el fixture exportado
    sea determinista (los uuid reales cambian en cada corrida)."""
    if _counter is None:
        _counter = [0]
    node.span_id = f"s{_counter[0]:02d}"
    _counter[0] += 1
    for child in node.children:
        _stabilize_ids(child, _counter)


def _print_tree(node: dict, prefix: str = "  ") -> None:
    attrs = " ".join(f"{k}={v}" for k, v in node["attributes"].items())
    bar = "▇" * max(1, int(node["duration_ms"] / 40))
    print(f"{prefix}{node['name']:<11} {node['duration_ms']:>7.1f}ms {bar}  {attrs}")
    for child in node["children"]:
        _print_tree(child, prefix + "    ")


def demo_tracing() -> dict:
    print("\n" + SEP)
    print("3. TRACING — dónde se fue el tiempo de un request")

    tracer = Tracer()
    # Trace MODELADO (duraciones explícitas) para que el fixture sea determinista.
    # En producción se omite duration_ms y cada span mide su propio wall-clock.
    with tracer.trace("POST /query", trace_id="trace-demo-001",
                      query="¿Tasa de IVA digital?", prompt_ref="rag-fiscal@v2") as root:
        with tracer.span("retrieval", duration_ms=4.8, k=3, retriever="hybrid-rrf"):
            with tracer.span("bm25", duration_ms=1.2):
                pass
            with tracer.span("dense", duration_ms=2.9, embedding_cache="hit"):
                pass
        with tracer.span("rerank", duration_ms=2.1, candidates=20):
            pass
        with tracer.span("llm", duration_ms=812.4, model="gpt-4o-mini",
                         from_cache=False, in_tokens=272, out_tokens=21):
            pass
    # La raíz: end-to-end = suma de hijos + overhead (modelado).
    root.duration_ms = 4.8 + 2.1 + 812.4 + 1.5

    _stabilize_ids(root)
    tree = root.to_dict()
    print(f"\n  trace_id={root.trace_id}  (end-to-end {tree['duration_ms']:.1f}ms)\n")
    _print_tree(tree)
    print("\n  El span 'llm' se come el 99% del tiempo — el tracing lo deja obvio.")
    print("  (con from_cache=true, ese span baja a ~0 y la raíz se desploma.)")

    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    out = TRACES_DIR / "trace-example.json"
    out.write_text(json.dumps(tree, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n  trace exportado a {out.relative_to(ROOT)}")
    return tree


# --------------------------------------------------------------------------- #
def main() -> None:
    demo_structured_logging()
    demo_metrics()
    demo_tracing()
    print("\n" + SEP)
    print("Anti-patrón: dashboards bonitos sin alertas. El valor de medir es")
    print("enterarte ANTES que el usuario. En producción esto emite a")
    print("OpenTelemetry → Tempo/Jaeger/Honeycomb; las primitivas son las mismas.")


if __name__ == "__main__":
    main()
