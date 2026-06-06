"""Sección 6 — Reliability: rate limit, retries, circuit breaker, fallback.

Trata la API del LLM como red externa flaky y la envuelve en cuatro capas
(todas LLMClient componibles, de prod_lib):

  1. TokenBucket          — autolimitarse antes del 429.
  2. retry_with_backoff   — reintentar lo transitorio con backoff + jitter.
  3. CircuitBreaker       — dejar de pegarle al proveedor caído.
  4. FallbackLLMClient    — degradar visible en vez de explotar.

Y mide el pago: con la pila puesta, un proveedor que se cae 15 requests NO
genera errores de cara al usuario, y el breaker evita martillar al proveedor
caído. Todo offline, determinista (reloj y sleep inyectados), gratis.

Ejecutar:

    uv run python 03-produccion/code/06-reliability.py
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from prod_lib import (  # noqa: E402
    CircuitBreaker,
    CircuitBreakingLLMClient,
    CircuitOpenError,
    ClientLLMError,
    FallbackLLMClient,
    LLMResponse,
    RetryingLLMClient,
    TokenBucket,
    TransientLLMError,
    retry_with_backoff,
)

SEP = "=" * 72


class SimBackend:
    """LLMClient simulado con un interruptor `down` para provocar la caída."""

    def __init__(self, name: str, model: str, latency_ms: float = 600.0, seed: int = 0):
        self.name = name
        self.default_model = model
        self.latency_ms = latency_ms
        self.calls = 0
        self.down = False
        self._rng = np.random.default_rng(seed)

    def complete(self, prompt, *, model=None, temperature=0.0, max_tokens=512) -> LLMResponse:
        self.calls += 1
        if self.down:
            raise TransientLLMError(f"503 Service Unavailable ({self.name})")
        return LLMResponse(
            text="[ok]", in_tokens=120, out_tokens=30,
            latency_ms=self.latency_ms, model=model or self.default_model,
        )


# --------------------------------------------------------------------------- #
# 1. Token bucket: autolimitarse antes del 429.
# --------------------------------------------------------------------------- #
def demo_token_bucket() -> None:
    print(SEP)
    print("1. TOKEN BUCKET — rate limit de cliente")
    clock = [0.0]
    tb = TokenBucket(rate=2.0, capacity=5, clock=lambda: clock[0])
    print("  capacity=5, rate=2 tokens/s. Llega una ráfaga de 8 requests en t=0:")
    burst = [tb.try_acquire() for _ in range(8)]
    ok = sum(burst)
    print(f"    {['✓' if b else '✗' for b in burst]}  → {ok} pasan, {8-ok} se frenan")
    print("  Esperamos 1s (se reponen 2 tokens) y llegan 3 más:")
    clock[0] = 1.0
    burst2 = [tb.try_acquire() for _ in range(3)]
    print(f"    {['✓' if b else '✗' for b in burst2]}  → {sum(burst2)} pasan")
    print("  Frenar acá es barato; que el proveedor te tire 429 cuesta backoff")
    print("  forzado y, repetido, baneos. El bucket reparte el tráfico parejo.")


# --------------------------------------------------------------------------- #
# 2. Retry con backoff + jitter.
# --------------------------------------------------------------------------- #
def demo_retry() -> None:
    print("\n" + SEP)
    print("2. RETRY — backoff exponencial + jitter")

    # Falla 3 veces (transitorio) y a la 4ta responde.
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] <= 3:
            raise TransientLLMError("503")
        return "ok"

    print("\n  sin jitter (determinista): delays = base·2^intento")
    delays: list[float] = []
    retry_with_backoff(flaky, base_delay=0.5, jitter=False,
                       sleep=lambda d: delays.append(d))
    print(f"    {[round(d, 2) for d in delays]}  → 0.5, 1.0, 2.0 (exponencial)")

    print("\n  con jitter (full jitter, semillas distintas = secuencias distintas):")
    for seed in (1, 2):
        state["n"] = 0
        dl: list[float] = []
        retry_with_backoff(flaky, base_delay=0.5, jitter=True,
                           rng=random.Random(seed), sleep=lambda d: dl.append(d))
        print(f"    seed={seed}: {[round(d, 3) for d in dl]}")
    print("  El jitter desincroniza a mil clientes que cayeron juntos: sin él,")
    print("  todos reintentan al mismo tiempo y vuelven a tumbar al proveedor.")

    print("\n  un 4xx del cliente NO se reintenta (reintentar no arregla un 400):")
    tries = {"n": 0}

    def bad_request():
        tries["n"] += 1
        raise ClientLLMError("400 Bad Request")

    try:
        retry_with_backoff(bad_request, sleep=lambda d: None)
    except ClientLLMError:
        print(f"    ✗ relanzado tras {tries['n']} intento (sin reintentos)")


# --------------------------------------------------------------------------- #
# 3. Circuit breaker.
# --------------------------------------------------------------------------- #
def demo_circuit_breaker() -> None:
    print("\n" + SEP)
    print("3. CIRCUIT BREAKER — dejar de pegarle al proveedor caído")

    clock = [0.0]
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=2.0, clock=lambda: clock[0])
    backend = SimBackend("openai", "gpt-4o-mini")
    backend.down = True

    print("\n  proveedor caído. failure_threshold=3, recovery_timeout=2s:")
    for i in range(6):
        clock[0] += 0.3
        try:
            cb.call(lambda: backend.complete("x"))
            outcome = "ok"
        except CircuitOpenError:
            outcome = "RECHAZADO sin llamar (circuito abierto)"
        except TransientLLMError:
            outcome = "fallo del proveedor (contado)"
        print(f"    req {i} (t={clock[0]:.1f}s)  estado={cb.state:<9}  {outcome}")

    print(f"\n  llamadas REALES al proveedor caído: {backend.calls} (no 6: el breaker")
    print("  cortó tras abrirse). Ahora el proveedor 'se recupera' y pasa el tiempo:")
    backend.down = False
    clock[0] += 2.1  # supera recovery_timeout
    try:
        cb.call(lambda: backend.complete("x"))
        print(f"    req prueba (t={clock[0]:.1f}s)  estado={cb.state}  → half-open→closed ✓")
    except Exception as e:  # noqa: BLE001
        print(f"    inesperado: {e}")


# --------------------------------------------------------------------------- #
# 4. La pila completa: el pago.
# --------------------------------------------------------------------------- #
def demo_full_stack() -> None:
    print("\n" + SEP)
    print("4. LA PILA COMPLETA — Fallback(CircuitBreaking(Retrying(primary)))")

    # Cronograma: 20 requests; el primary se cae en [5, 15).
    n, outage = 20, range(5, 15)

    def run(use_breaker: bool):
        clock = [0.0]
        primary = SimBackend("openai", "gpt-4o-mini")
        secondary = SimBackend("anthropic", "claude-haiku-4-5")
        fallbacks: list[str] = []
        retrying = RetryingLLMClient(primary, max_retries=2, base_delay=0.2,
                                     sleep=lambda d: None, rng=random.Random(0))
        inner = retrying
        if use_breaker:
            breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0,
                                     clock=lambda: clock[0])
            inner = CircuitBreakingLLMClient(retrying, breaker)
        client = FallbackLLMClient(inner, secondary,
                                   on_fallback=lambda e: fallbacks.append(type(e).__name__))
        served = {"primary": 0, "secondary": 0}
        user_errors = 0
        for i in range(n):
            clock[0] += 0.2
            primary.down = i in outage
            try:
                resp = client.complete("¿tasa de IVA?")
                served["secondary" if resp.model == "claude-haiku-4-5" else "primary"] += 1
            except Exception:  # noqa: BLE001
                user_errors += 1
        return primary.calls, served, user_errors, len(fallbacks)

    print(f"\n  {n} requests; el proveedor primario está caído en requests {outage.start}-{outage.stop-1}.\n")
    print(f"  {'configuración':>22} | prim.calls | servido prim/sec | errores usuario")
    print(f"  {'-'*22}-+-{'-'*10}-+-{'-'*16}-+-{'-'*15}")
    for label, ub in [("Retry + Fallback", False), ("Retry + Breaker + Fallback", True)]:
        calls, served, errs, fb = run(ub)
        print(f"  {label:>22} | {calls:>10} | {served['primary']:>6}/{served['secondary']:<9} | "
              f"{errs:>15}")
    print("\n  Lectura:")
    print("  - errores de cara al usuario: 0 en ambas — el fallback al secundario")
    print("    (claude-haiku) atrapa la caída; el usuario ve respuesta, no un 500.")
    print("  - prim.calls: SIN breaker se martilla al proveedor caído (cada request")
    print("    reintenta 3×); CON breaker, tras abrirse deja de llamarlo → menos daño")
    print("    al proveedor que intenta recuperarse, y respuestas más rápidas.")


def main() -> None:
    demo_token_bucket()
    demo_retry()
    demo_circuit_breaker()
    demo_full_stack()
    print("\n" + SEP)
    print("La tasa de fallos de §5 es lo que calibra estos umbrales; el response")
    print("cache de §4 es otro fallback posible (servir lo cacheado si el LLM cae).")


if __name__ == "__main__":
    main()
