"""Sección 8 — Versionado de modelos: shadow, A/B y canary.

Cambiar de modelo sin susto. Tres patrones sobre un caso concreto: evaluar si se
puede migrar de gpt-4o (estable, caro) a gpt-4o-mini (candidato, ~16× más
barato) sin perder calidad.

  1. Shadow  — el candidato corre en sombra; el usuario siempre recibe el
               estable. Comparamos costo/latencia/acuerdo offline, sin riesgo.
  2. Canary  — rampa 1% → 5% → 25% → 100% del tráfico al candidato, ruteo
               sticky por usuario, con rollback automático si se degrada.
  3. A/B     — caso particular del canary con fraction fija (0.5).

Offline, determinista, gratis. Modelos simulados con costo real (de PRICING).

Ejecutar:

    uv run python 03-produccion/code/08-model-routing.py
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from prod_lib import (  # noqa: E402
    CanaryLLMClient,
    LLMResponse,
    ShadowLLMClient,
    Tracer,
    TransientLLMError,
)

SEP = "=" * 72


class SimModel:
    """Modelo simulado. Costo real (LLMResponse lo deriva de PRICING por nombre);
    latencia modelada; puede fallar a `fail_rate` (TransientLLMError)."""

    def __init__(self, name: str, model: str, latency_ms: float,
                 fail_rate: float = 0.0, seed: int = 0):
        self.name = name
        self.default_model = model
        self.latency_ms = latency_ms
        self.fail_rate = fail_rate
        self._rng = np.random.default_rng(seed)

    def complete(self, prompt, *, model=None, temperature=0.0, max_tokens=512) -> LLMResponse:
        if self._rng.random() < self.fail_rate:
            raise TransientLLMError(f"503 ({self.default_model})")
        return LLMResponse(
            text=f"[{self.default_model}]", in_tokens=max(1, len(prompt.split()) * 12),
            out_tokens=30, latency_ms=self.latency_ms, model=model or self.default_model,
        )


QUERIES = [f"consulta fiscal número {i}" for i in range(40)]


# --------------------------------------------------------------------------- #
# 1. Shadow.
# --------------------------------------------------------------------------- #
def demo_shadow() -> None:
    print(SEP)
    print("1. SHADOW — el candidato corre en sombra, sin tocar al usuario")

    stable = SimModel("openai", "gpt-4o", latency_ms=1100.0)
    candidate = SimModel("openai", "gpt-4o-mini", latency_ms=650.0)

    # agree_fn simulado: el candidato coincide con el estable ~82% de las veces.
    coin = random.Random(0)
    shadow = ShadowLLMClient(
        stable, candidate,
        agree_fn=lambda a, b: coin.random() < 0.82,
    )
    cmps = []
    shadow.on_compare = cmps.append

    served_models = set()
    for q in QUERIES:
        resp = shadow.complete(q)
        served_models.add(resp.model)

    agree = sum(c.agree for c in cmps) / len(cmps)
    cost_stable = sum(c.primary_cost for c in cmps)
    cost_cand = sum(c.candidate_cost for c in cmps)
    lat_stable = np.mean([c.primary_ms for c in cmps])
    lat_cand = np.mean([c.candidate_ms for c in cmps])

    print(f"\n  el usuario SIEMPRE recibió: {served_models}  (cero riesgo)")
    print(f"  acuerdo candidato↔estable : {100*agree:.0f}%")
    print(f"  costo (la ventana shadow) : estable ${cost_stable:.5f}  "
          f"candidato ${cost_cand:.5f}  → {100*(1-cost_cand/cost_stable):.0f}% más barato")
    print(f"  latencia media            : estable {lat_stable:.0f}ms  candidato {lat_cand:.0f}ms")
    print(f"\n  Veredicto: el candidato es mucho más barato y rápido, con {100*agree:.0f}%")
    print("  de acuerdo. El shadow lo dice SIN exponer a un solo usuario. El paso")
    print("  siguiente (canary) lleva una fracción real de tráfico al candidato.")
    print("  Nota: shadow cuesta 2× (dos llamadas); se usa por una ventana, no fijo.")


# --------------------------------------------------------------------------- #
# 2. Canary: rampa + stickiness.
# --------------------------------------------------------------------------- #
def demo_canary_ramp() -> None:
    print("\n" + SEP)
    print("2. CANARY — rampa de tráfico al candidato, ruteo sticky por usuario")

    stable = SimModel("openai", "gpt-4o", latency_ms=1100.0)
    candidate = SimModel("openai", "gpt-4o-mini", latency_ms=650.0)
    canary = CanaryLLMClient(stable, candidate, fraction=0.0)
    tracer = Tracer()
    n_users = 4000

    print(f"\n  {n_users} usuarios distintos por etapa; share observado del candidato:\n")
    print(f"  {'fraction':>9} | {'share candidato':>16}")
    print(f"  {'-'*9}-+-{'-'*16}")
    for frac in (0.01, 0.05, 0.25, 1.0):
        canary.fraction = frac
        cand = 0
        for u in range(n_users):
            with tracer.trace("r", trace_id=f"user-{u}"):
                if canary.complete("q").model == "gpt-4o-mini":
                    cand += 1
        print(f"  {frac:>9.2f} | {cand/n_users:>15.3f}  (≈ {frac})")

    # Stickiness: un usuario, muchas requests, una sola variante.
    canary.fraction = 0.25
    for who in ("ana", "beto", "caro"):
        with tracer.trace("r", trace_id=who):
            variants = {canary.complete("q").model for _ in range(8)}
        print(f"  stickiness '{who}': {variants}  → "
              f"{'estable' if len(variants) == 1 else 'PARPADEA'}")
    print("\n  El share sigue la fracción, y cada usuario ve UNA variante (no")
    print("  parpadea entre modelos request a request). A/B = esto con fraction=0.5.")


# --------------------------------------------------------------------------- #
# 3. Canary: rollback automático.
# --------------------------------------------------------------------------- #
def demo_canary_rollback() -> None:
    print("\n" + SEP)
    print("3. CANARY — rollback automático si el candidato se degrada")

    stable = SimModel("openai", "gpt-4o", latency_ms=1100.0)
    bad_candidate = SimModel("openai", "gpt-4o-mini", latency_ms=650.0, fail_rate=0.5, seed=1)
    events = []
    canary = CanaryLLMClient(
        stable, bad_candidate, fraction=0.25,
        max_error_rate=0.20, min_calls=20,
        on_rollback=lambda s: events.append(s),
    )
    tracer = Tracer()

    print("\n  candidato malo (50% de error). umbral=20% sobre ≥20 llamadas.\n")
    user_errors = 0
    rolled_at = None
    for u in range(600):
        with tracer.trace("r", trace_id=f"user-{u}"):
            try:
                canary.complete("q")
            except TransientLLMError:
                user_errors += 1
        if canary.rolled_back and rolled_at is None:
            rolled_at = u

    print(f"  rollback disparó en la request ~{rolled_at}  (fraction → {canary.fraction})")
    print(f"  al rollback: {events[0] if events else None}")
    print(f"  tras el rollback, todo el tráfico volvió al estable "
          f"(routed final: {canary.routed})")
    print(f"  errores de usuario totales: {user_errors} — acotados por el rollback;")
    print("  sin la guardia, el 25% del tráfico habría seguido fallando 50%.")
    print("\n  El criterio de rollback es estadístico: la decisión 'el candidato es")
    print("  peor' se toma con IC (01-evals §8), no con el primer fallo aislado.")


def main() -> None:
    demo_shadow()
    demo_canary_ramp()
    demo_canary_rollback()
    print("\n" + SEP)
    print("El modelo es config (§7): shadow/canary lo cambian para una fracción del")
    print("tráfico sin redeploy. El proveedor también lo versiona: pineá snapshots.")


if __name__ == "__main__":
    main()
