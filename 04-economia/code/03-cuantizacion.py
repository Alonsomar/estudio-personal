"""§3 — Cuantización y destilación: cambiar el modelo, no el uso.

Produce los números de `theory/03-cuantizacion.md`. Lo que ES derivable
(memoria, velocidad, concurrencia, costo) se calcula; lo que NO lo es
(calidad) se declara no medido y se muestra el protocolo que lo mediría.

    uv run python 04-economia/code/03-cuantizacion.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "02-retrieval" / "code"))

from econ_lib import GPUS, MODELS, min_golden_size, quant_profile  # noqa: E402
from retrieval_lib import bootstrap_ci  # noqa: E402

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)

DTYPES = ("fp32", "bf16", "fp8", "int8", "int4")


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def demo_perfil_cuantizacion() -> None:
    """Lo que la cuantización SÍ da, y es derivable de §1-§2."""
    seccion("1. Qué compra la cuantización (lo derivable)")

    gpu = GPUS["H100-80"]
    for key in ("8B", "70B"):
        m = MODELS[key]
        print(f"\n{m.name} en {gpu.name}:")
        print(
            f"{'dtype':>7} | {'pesos':>8} | {'cabe':>5} | {'tok/s':>7} | "
            f"{'seqs 4k':>8} | {'batch ef.':>10} | {'$/M':>9}"
        )
        print("-" * 72)
        for dt in DTYPES:
            p = quant_profile(m, gpu, dt)
            cabe = "sí" if p.fits_in_gpu else "NO"
            tps = f"{p.tokens_per_s:,.0f}" if p.fits_in_gpu else "—"
            seqs = f"{p.max_seqs_4k:,}" if p.fits_in_gpu else "—"
            bef = f"{p.batch_efectivo:,}" if p.fits_in_gpu else "—"
            usd = f"{p.usd_per_m_tokens:.4f}" if p.fits_in_gpu else "—"
            print(
                f"{dt:>7} | {p.weights_gb:>5.0f} GB | {cabe:>5} | {tps:>7} | "
                f"{seqs:>8} | {bef:>10} | {usd:>9}"
            )

    m = MODELS["70B"]
    p8 = quant_profile(m, gpu, "fp8")
    p4 = quant_profile(m, gpu, "int4")
    print(
        f"\n(Batch objetivo 32, pero limitado por la memoria: no sirve querer batch 32\n"
        f" si en el KV cache entran {p8.max_seqs_4k}.)\n"
        f"\nEl salto discreto: {m.name} en bf16 NO cabe en una {gpu.name}; en int4 sí.\n"
        "No es 'un poco más barato': es la diferencia entre necesitar dos GPUs y una.\n"
        f"\nY el efecto compuesto con §2: de fp8 a int4 la velocidad por secuencia solo\n"
        f"se duplica ({p8.tokens_per_s:.0f}→{p4.tokens_per_s:.0f} tok/s), pero el batch "
        f"alcanzable salta de {p8.batch_efectivo} a {p4.batch_efectivo},\n"
        f"y el costo por millón cae {p8.usd_per_m_tokens / p4.usd_per_m_tokens:.0f}× "
        f"(${p8.usd_per_m_tokens:.3f} → ${p4.usd_per_m_tokens:.3f}). El ahorro real viene\n"
        "del batch que la memoria liberada habilita, no de la velocidad bruta."
    )


def demo_triple_efecto() -> None:
    """Por qué el efecto es más que proporcional."""
    seccion("2. Por qué el ahorro es más que proporcional")

    gpu, m = GPUS["H100-80"], MODELS["8B"]
    base = quant_profile(m, gpu, "bf16")
    q4 = quant_profile(m, gpu, "int4")

    print("clase 8B, de bf16 a int4 (4× menos bits por peso):")
    print(f"  pesos:        {base.weights_gb:>6.0f} GB → {q4.weights_gb:>6.0f} GB  "
          f"({base.weights_gb / q4.weights_gb:.0f}× menos)")
    print(f"  tokens/s:     {base.tokens_per_s:>6.0f}    → {q4.tokens_per_s:>6.0f}     "
          f"({q4.tokens_per_s / base.tokens_per_s:.0f}× más)")
    print(f"  seqs a 4k:    {base.max_seqs_4k:>6,}    → {q4.max_seqs_4k:>6,}     "
          f"({q4.max_seqs_4k / base.max_seqs_4k:.1f}× más)")
    print(
        "\nTres efectos que se componen:\n"
        "  1. Menos bytes por token → decode más rápido (§1, memory-bound).\n"
        "  2. Pesos más chicos → más memoria libre para KV cache.\n"
        "  3. Más KV cache → batch más grande alcanzable → costo/token más bajo (§2).\n"
        "\nPor eso cuantizar es la palanca de costo más potente del lado del modelo.\n"
        "Y por eso hay que sospechar: nada que dé tanto sale gratis."
    )


def demo_protocolo_medicion() -> None:
    """Lo que NO es derivable: la calidad. Acá va el protocolo, no un número."""
    seccion("3. Lo que NO se puede derivar: cuánta calidad se pierde")

    print(
        "NO MEDIDO. Esta masterclass no puede correr un modelo cuantizado (sin GPU),\n"
        "así que no hay ningún número de degradación acá. Los benchmarks agregados\n"
        "que publican los proveedores de cuantización tampoco sirven: miden MMLU o\n"
        "HellaSwag, no tu corpus normativo chileno.\n"
        "\nLo que sí se puede dar es el PROTOCOLO y su costo estadístico."
    )

    print("\nPregunta previa: ¿cuántas queries de golden hacen falta para detectar")
    print("una caída de X puntos, con α=0.05 y potencia 0.80?\n")
    print(f"{'pass rate base':>15} | {'caída a detectar':>17} | {'n mínimo':>10}")
    print("-" * 48)
    for base in (0.90, 0.80):
        for delta in (0.01, 0.02, 0.05, 0.10, 0.15):
            n = min_golden_size(base, delta)
            print(f"{base:>14.0%} | {delta:>16.0%} | {n:>10,}")

    n27 = min_golden_size(0.90, 0.05)
    print(
        f"\nEl golden chunk-level de 02 §8 tiene 27 queries. Para detectar una caída\n"
        f"de 5 puntos desde 90% harían falta ~{n27:,}. Con 27 queries, cuantizar y\n"
        "'no notar diferencia' NO es evidencia de que no degradó: es evidencia de\n"
        "que el instrumento no tenía resolución.\n"
        "\nEsto es exactamente el límite que 02 §8 ya había marcado para comparar\n"
        "retrievers, y que motiva B6 (expandir el corpus)."
    )


def demo_ilustracion_ic() -> None:
    """Ilustración del aparato de 01 §8 aplicado a una decisión de cuantizar."""
    seccion("4. Ilustración del protocolo (datos SINTÉTICOS, no una medición)")

    print(
        "Los datos de abajo son SINTÉTICOS y sirven solo para mostrar cómo se lee\n"
        "el resultado. No son una medición de ningún modelo cuantizado real.\n"
    )

    import numpy as np

    rng = np.random.default_rng(42)
    for n, verdadera in ((27, 0.05), (300, 0.05)):
        base = rng.binomial(1, 0.90, n).astype(float)
        quant = rng.binomial(1, 0.90 - verdadera, n).astype(float)
        delta = base - quant
        media, lo, hi = bootstrap_ci(list(delta), n_boot=2000, seed=7)
        signif = "SÍ" if lo > 0 else "no"
        print(
            f"n={n:>3} queries | delta observado {media:>+6.1%} | "
            f"IC95% [{lo:>+6.1%}, {hi:>+6.1%}] | ¿significativo? {signif}"
        )

    print(
        "\nMisma degradación verdadera (5 puntos) en los dos casos. Con n=27 el IC\n"
        "cruza el cero y no podés concluir nada; con n=300 se detecta. El aparato es\n"
        "el mismo bootstrap de 01 §8 — literalmente la misma función importada de\n"
        "retrieval_lib."
    )


if __name__ == "__main__":
    log.info("Modelo analítico + protocolo estadístico — sin GPU, sin red.")
    demo_perfil_cuantizacion()
    demo_triple_efecto()
    demo_protocolo_medicion()
    demo_ilustracion_ic()
    print()
