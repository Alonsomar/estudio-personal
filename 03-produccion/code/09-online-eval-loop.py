"""Sección 9 — Online evals: el ciclo cerrado producción → golden.

No es un dashboard, es un loop:

  1. Sampling estratificado — no se evalúa el 100% del tráfico; se muestrea, con
     reglas (errores siempre, queries raras upsampleadas).
  2. Auto-eval con judge por CONFIANZA — lo que el judge sabe juzgar (out-of-scope)
     se mide solo; lo que no (afirmaciones factuales) va a revisión humana.
  3. Llevar las fallas al golden — las queries que fallan en prod son el mejor
     inventario para crecer el golden de 01-evals.
  4. Drift detection — PSI sobre una feature avisa si la distribución de queries
     se movió.

Offline, determinista, gratis. El judge es simulado (en prod es el LLM-judge de
01-evals §7).

Ejecutar:

    uv run python 03-produccion/code/09-online-eval-loop.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from prod_lib import (  # noqa: E402
    DriftDetector,
    OnlineEvalLoop,
    TraceSampler,
)
from shared.utils import get_project_root  # noqa: E402

ROOT = get_project_root()
OUT_DIR = ROOT / "03-produccion" / "examples" / "online-evals"
SEP = "=" * 72


# --------------------------------------------------------------------------- #
# Stream sintético de producción.
# --------------------------------------------------------------------------- #
def make_stream(n: int, seed: int = 0) -> list[dict]:
    """Genera n registros de producción. qtype con distribución sesgada; algunos
    errores; out-of-scope que el sistema a veces NO abstiene (falla)."""
    rng = random.Random(seed)
    types = ["factual"] * 70 + ["out_of_scope"] * 20 + ["rara"] * 10
    stream = []
    for i in range(n):
        qtype = rng.choice(types)
        error = rng.random() < 0.04
        # out-of-scope: el sistema debería abstenerse; a veces falla y "inventa".
        abstained = rng.random() < 0.8 if qtype == "out_of_scope" else False
        stream.append({
            "trace_id": f"req-{i:04d}",
            "qtype": qtype,
            "query": f"[{qtype}] consulta {i}",
            "error": error,
            "abstained": abstained,
            "retrieval_score": rng.gauss(0.5, 0.15),
        })
    return stream


# --------------------------------------------------------------------------- #
# Judge simulado: confía en lo que sabe, se abstiene en lo que no.
# --------------------------------------------------------------------------- #
def judge(rec: dict) -> dict:
    if rec["error"]:
        return {"pass": False, "confidence": 0.99, "reason": "request falló (sin respuesta)"}
    if rec["qtype"] == "out_of_scope":
        # El judge SABE juzgar esto: ¿se abstuvo ante algo fuera de scope?
        ok = rec["abstained"]
        return {"pass": ok, "confidence": 0.95,
                "reason": "se abstuvo correctamente" if ok else "alucinó fuera de scope"}
    # Afirmación factual sobre normativa: el judge NO puede verificarla solo.
    return {"pass": True, "confidence": 0.45, "reason": "factual: requiere verificación humana"}


# --------------------------------------------------------------------------- #
# 1 + 2 + 3. Sampling, loop y golden.
# --------------------------------------------------------------------------- #
def demo_loop() -> None:
    print(SEP)
    print("1-3. SAMPLING ESTRATIFICADO + LOOP + GOLDEN")

    stream = make_stream(2000, seed=0)

    sampler = (
        TraceSampler(base_rate=0.05, rng=random.Random(7))
        .always_sample_if(lambda r: r["error"])          # los errores SIEMPRE
        .stratify(lambda r: r["qtype"],
                  {"factual": 0.05, "out_of_scope": 0.30, "rara": 0.60})
    )
    loop = OnlineEvalLoop(sampler, judge, min_confidence=0.7)

    # Conteo por estrato para mostrar el sesgo del sampling.
    by_type_seen: dict[str, int] = {}
    by_type_sampled: dict[str, int] = {}
    for rec in stream:
        by_type_seen[rec["qtype"]] = by_type_seen.get(rec["qtype"], 0) + 1
        before = sampler.sampled
        loop.observe(rec)
        if sampler.sampled > before:
            by_type_sampled[rec["qtype"]] = by_type_sampled.get(rec["qtype"], 0) + 1

    print(f"\n  {len(stream)} requests; muestreados {sampler.sampled} "
          f"({100*sampler.sampled/sampler.seen:.0f}% del total)\n")
    print(f"  {'estrato':>13} | {'visto':>6} | {'muestreado':>10} | tasa")
    print(f"  {'-'*13}-+-{'-'*6}-+-{'-'*10}-+------")
    for t in ("factual", "out_of_scope", "rara"):
        seen, smp = by_type_seen.get(t, 0), by_type_sampled.get(t, 0)
        print(f"  {t:>13} | {seen:>6} | {smp:>10} | {100*smp/max(seen,1):.0f}%")
    print("  → tasas base 60% (rara) vs 5% (común); los errores entran SIEMPRE,")
    print("    encima de su estrato (por eso 'factual' observado supera el 5% base).")
    print("    Sin estratificar, las queries raras nunca caerían en la muestra.")

    print("\n  auto-eval (judge por confianza):")
    print(f"    evaluado automáticamente : {loop.evaluated}  "
          f"(pass rate online {100*loop.pass_rate:.0f}%)")
    print(f"    a revisión humana        : {len(loop.human_review)}  "
          "(factuales: el judge no las verifica solo)")
    print(f"    candidatos a golden      : {len(loop.golden_candidates)}  "
          "(fallas que el judge SÍ supo juzgar)")

    print("\n  muestra de candidatos a golden (las fallas crecen el golden):")
    for gc in loop.golden_candidates[:4]:
        print(f"    {gc.trace_id}  {gc.query:<24}  → {gc.reason}")

    # Exportar el inventario de golden candidates (el artefacto del loop).
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "golden-candidates.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for gc in loop.golden_candidates:
            f.write(json.dumps({"trace_id": gc.trace_id, "query": gc.query,
                                "reason": gc.reason}, ensure_ascii=False) + "\n")
    print(f"\n  inventario exportado a {out.relative_to(ROOT)} "
          f"({len(loop.golden_candidates)} queries para sumar al golden de 01-evals)")


# --------------------------------------------------------------------------- #
# 4. Drift detection.
# --------------------------------------------------------------------------- #
def demo_drift() -> None:
    print("\n" + SEP)
    print("4. DRIFT DETECTION — ¿se movió la distribución de queries?")

    # Baseline: feature de las queries históricas (p.ej. distancia al centroide
    # del corpus). En prod es embedding-based; acá un escalar.
    rng = np.random.default_rng(0)
    baseline = rng.normal(0.0, 1.0, 3000)
    dd = DriftDetector(baseline, watch=0.1, drift=0.25)

    # Llegan ventanas de tráfico; a partir de la ventana 3 entra un tema nuevo
    # (una ley nueva, otra distribución de queries) que desplaza la feature.
    print("\n  PSI de cada ventana vs baseline (>0.1 watch, >0.25 drift):\n")
    print(f"  {'ventana':>8} | {'shift':>6} | {'PSI':>6} | estado")
    print(f"  {'-'*8}-+-{'-'*6}-+-{'-'*6}-+--------")
    for w, shift in enumerate([0.0, 0.2, 0.35, 0.5, 0.9, 1.5]):
        batch = np.random.default_rng(100 + w).normal(shift, 1.0, 500)
        level, score = dd.status(batch)
        flag = {"ok": "ok", "watch": "⚠ watch", "drift": "✗ DRIFT"}[level]
        print(f"  {w:>8} | {shift:>6.1f} | {score:>6.3f} | {flag}")
    print("\n  El PSI sube con el desplazamiento. Un 'watch' dispara revisar el")
    print("  corpus/queries; un 'drift' dice que el golden ya no representa al")
    print("  tráfico — hay que re-muestrear queries reales y re-evaluar.")


def main() -> None:
    demo_loop()
    demo_drift()
    print("\n" + SEP)
    print("El loop se cierra: prod → sampling → judge → golden → mejor sistema →")
    print("prod. El online eval no es mirar un panel; es alimentar al golden.")


if __name__ == "__main__":
    main()
