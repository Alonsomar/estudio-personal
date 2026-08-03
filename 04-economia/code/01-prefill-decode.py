"""§1 — Mecánica de la inferencia: prefill, decode y KV cache.

Produce los números que cita `theory/01-prefill-decode.md`. Todo es aritmética
sobre specs públicas: corre offline, determinista, sin GPU y sin red.

    uv run python 04-economia/code/01-prefill-decode.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "03-produccion" / "code"))

from econ_lib import (  # noqa: E402
    GPUS,
    MODELS,
    decode_bytes_per_token,
    decode_tokens_per_second,
    max_concurrent_sequences,
    prefill_seconds,
)
from prod_lib import PRICING_USD_PER_M_TOKENS as PRICING  # noqa: E402

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)

# Carga de referencia del RAG chileno (03 §2 / §10).
RAG_PROMPT_TOKENS = 272
RAG_OUTPUT_TOKENS = 60


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def demo_asimetria_prefill_decode() -> None:
    """El hecho central: procesar el prompt es barato, generar es caro."""
    seccion("1. Las dos fases sobre la carga real del RAG chileno (272 in / 60 out)")

    gpu = GPUS["H100-80"]
    print(f"GPU: {gpu.name} — {gpu.bandwidth_tb_s} TB/s, {gpu.bf16_tflops} TFLOP/s bf16\n")
    print(f"{'modelo':>10} | {'prefill 272 tok':>16} | {'decode 60 tok':>14} | {'decode/prefill':>14}")
    print("-" * 68)

    for key in ("8B", "70B"):
        m = MODELS[key]
        t_pre = prefill_seconds(m, RAG_PROMPT_TOKENS, gpu)
        tps = decode_tokens_per_second(m, gpu)
        t_dec = RAG_OUTPUT_TOKENS / tps
        print(
            f"{m.name:>10} | {t_pre * 1000:>13.1f} ms | {t_dec * 1000:>11.1f} ms | "
            f"{t_dec / t_pre:>13.0f}×"
        )

    print(
        "\nLeer: generar 60 tokens toma un orden de magnitud más que procesar\n"
        "los 272 del prompt. El prompt se procesa en paralelo; la salida, no."
    )


def demo_por_que_el_output_es_caro() -> None:
    """Bytes movidos por token: la razón física de la asimetría de precio."""
    seccion("2. Por qué el output cuesta varias veces el input")

    gpu = GPUS["H100-80"]
    print(f"{'modelo':>10} | {'bytes/token decode':>20} | {'tokens/s (batch 1)':>19}")
    print("-" * 56)
    for key in ("8B", "70B"):
        m = MODELS[key]
        b = decode_bytes_per_token(m)
        print(f"{m.name:>10} | {b / 1e9:>17.1f} GB | {decode_tokens_per_second(m, gpu):>19.0f}")

    m = MODELS["70B"]
    print(
        f"\nPara escribir UN token de un modelo {m.name}, hay que traer "
        f"{decode_bytes_per_token(m) / 1e9:.0f} GB\n"
        "de pesos desde memoria. Para procesar los 272 tokens del prompt, esos\n"
        f"mismos {decode_bytes_per_token(m) / 1e9:.0f} GB se leen UNA vez y sirven "
        f"a los {RAG_PROMPT_TOKENS} tokens a la vez.\n"
        f"\nEl prompt amortiza la lectura entre {RAG_PROMPT_TOKENS} tokens; la "
        "generación la paga entera\npor cada token. Esa es la asimetría que la "
        "tarifa refleja."
    )

    # Contraste con las tarifas reales: la predicción cualitativa del modelo
    # (output > input) se contrasta con lo que los proveedores efectivamente
    # cobran. Las tarifas salen de prod_lib, no se duplican acá.
    print("\nRatio de tarifa output/input que cobran los proveedores:")
    print(f"{'modelo':>20} | {'$/M in':>8} | {'$/M out':>8} | {'ratio':>6}")
    print("-" * 50)
    for nombre, p in sorted(PRICING.items(), key=lambda kv: kv[1]["in"]):
        print(f"{nombre:>20} | {p['in']:>8.3f} | {p['out']:>8.3f} | {p['out'] / p['in']:>5.1f}×")
    ratios = [p["out"] / p["in"] for p in PRICING.values()]
    print(
        f"\nEl ratio observado va de {min(ratios):.0f}× a {max(ratios):.0f}×. El modelo "
        "físico explica\nel SIGNO y el orden de magnitud, no el número exacto: la "
        "tarifa también\nincorpora margen, competencia y el batching de §2."
    )


def demo_kv_cache() -> None:
    """El KV cache: el recurso que realmente limita la concurrencia."""
    seccion("3. KV cache: el recurso escaso")

    for key in ("8B", "70B"):
        m = MODELS[key]
        print(f"\n{m.name}: {m.kv_bytes_per_token() / 1024:.1f} KB por token de contexto")
        print(f"{'contexto':>12} | {'KV por secuencia':>17}")
        print("-" * 33)
        for ctx in (272, 4_000, 32_000, 128_000):
            print(f"{ctx:>12,} | {m.kv_cache_gb(ctx) * 1024:>14.0f} MB")


def demo_concurrencia() -> None:
    """Cuánta gente cabe a la vez, que es lo que fija el costo por request."""
    seccion("4. Cuántas secuencias caben a la vez en una H100 80GB")

    gpu = GPUS["H100-80"]
    for key in ("8B", "70B"):
        m = MODELS[key]
        libre = gpu.memory_gb - m.weights_gb() - 2.0
        if libre <= 0:
            print(
                f"\n{m.name}: pesos {m.weights_gb():.0f} GB en bf16 → NO CABE en una "
                f"{gpu.name}.\n"
                f"  Antes de hablar de concurrencia hay que resolver que entre: "
                f"{-libre:.0f} GB de déficit.\n"
                f"  Salidas: repartirlo en {m.weights_gb() / (gpu.memory_gb - 2):.0f}+ GPUs "
                "(y pagar la interconexión),\n"
                "  o cuantizar a int8/int4 (§3). Esto no es un detalle de implementación:\n"
                "  es la razón económica por la que los modelos grandes se sirven "
                "cuantizados."
            )
            for dtype in ("int8", "int4"):
                w = m.weights_gb(dtype)
                n = max_concurrent_sequences(m, gpu, 4_000, dtype=dtype)
                print(
                    f"    {dtype:>5}: pesos {w:>5.0f} GB → "
                    f"{'cabe' if w < gpu.memory_gb - 2 else 'sigue sin caber':<16}"
                    f" ({n:>4,} secuencias a 4k de contexto)"
                )
            continue

        print(
            f"\n{m.name}: pesos {m.weights_gb():.0f} GB + overhead 2 GB "
            f"→ {libre:.0f} GB libres para KV"
        )
        print(f"{'contexto':>12} | {'secuencias simultáneas':>23}")
        print("-" * 39)
        for ctx in (272, 4_000, 32_000, 128_000):
            n = max_concurrent_sequences(m, gpu, ctx)
            print(f"{ctx:>12,} | {n:>23,}")

    m8 = MODELS["8B"]
    n4k = max_concurrent_sequences(m8, gpu, 4_000)
    n128k = max_concurrent_sequences(m8, gpu, 128_000)
    print(
        "\nLa consecuencia de producto: el contexto largo no solo cuesta tokens,\n"
        f"cuesta CONCURRENCIA. En el modelo {m8.name}, pasar de 4k a 128k de contexto\n"
        f"baja de {n4k} a {n128k} secuencias simultáneas ({n4k / n128k:.0f}× menos usuarios\n"
        "por GPU) — y ese costo lo paga el proveedor en su tarifa."
    )


def demo_consecuencia_rag() -> None:
    """Qué implica todo lo anterior para decisiones de diseño del RAG chileno."""
    seccion("5. Consecuencia de diseño para el RAG chileno")

    gpu = GPUS["H100-80"]
    m = MODELS["8B"]
    tps = decode_tokens_per_second(m, gpu)

    base_in, base_out = RAG_PROMPT_TOKENS, RAG_OUTPUT_TOKENS
    print(f"{'escenario':>34} | {'in':>6} | {'out':>5} | {'tiempo':>9}")
    print("-" * 62)
    escenarios = [
        ("base (5 chunks, respuesta corta)", base_in, base_out),
        ("20 chunks en el prompt", base_in * 4, base_out),
        ("respuesta 4× más larga", base_in, base_out * 4),
        ("ambos", base_in * 4, base_out * 4),
    ]
    for nombre, n_in, n_out in escenarios:
        t = prefill_seconds(m, n_in, gpu) + n_out / tps
        print(f"{nombre:>34} | {n_in:>6,} | {n_out:>5,} | {t * 1000:>6.0f} ms")

    t_base = prefill_seconds(m, base_in, gpu) + base_out / tps
    t_ctx = prefill_seconds(m, base_in * 4, gpu) + base_out / tps
    t_out = prefill_seconds(m, base_in, gpu) + base_out * 4 / tps
    print(
        f"\nCuadruplicar el CONTEXTO: +{(t_ctx / t_base - 1) * 100:.0f}% de tiempo.\n"
        f"Cuadruplicar la RESPUESTA: +{(t_out / t_base - 1) * 100:.0f}% de tiempo.\n"
        "\nRegla de diseño: en un RAG, recuperar de más es barato; pedir respuestas\n"
        "largas es caro. Ante la duda, más chunks y menos verborrea."
    )


def grafico_asimetria() -> None:
    """Dos paneles: la asimetría in/out y el colapso de concurrencia."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    gpu, m = GPUS["H100-80"], MODELS["8B"]
    tps = decode_tokens_per_second(m, gpu)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))

    # Panel 1: tiempo vs tokens, variando entrada u salida por separado.
    n = np.arange(0, 2001, 25)
    t_in = [prefill_seconds(m, int(x), gpu) * 1000 + RAG_OUTPUT_TOKENS / tps * 1000 for x in n]
    t_out = [prefill_seconds(m, RAG_PROMPT_TOKENS, gpu) * 1000 + x / tps * 1000 for x in n]
    ax1.plot(n, t_in, label="variando tokens de ENTRADA", lw=2, color="#2ecc71")
    ax1.plot(n, t_out, label="variando tokens de SALIDA", lw=2, color="#e74c3c")
    ax1.axvline(RAG_PROMPT_TOKENS, ls=":", color="#666", lw=1)
    ax1.annotate(f"carga RAG\n({RAG_PROMPT_TOKENS} in)", xy=(RAG_PROMPT_TOKENS, 200),
                 fontsize=8, color="#666")
    ax1.set_xlabel("tokens")
    ax1.set_ylabel("latencia (ms)")
    ax1.set_title(f"Entrada vs salida no cuestan igual ({m.name}, {gpu.name})", fontsize=11)
    ax1.legend(fontsize=9)
    ax1.grid(alpha=0.3)

    # Panel 2: concurrencia vs contexto (el KV cache como recurso escaso).
    ctxs = [512, 1_000, 2_000, 4_000, 8_000, 16_000, 32_000, 64_000, 128_000]
    seqs = [max_concurrent_sequences(m, gpu, c) for c in ctxs]
    ax2.plot(ctxs, seqs, "o-", lw=2, color="#3498db")
    ax2.set_xscale("log")
    ax2.set_yscale("log")
    ax2.set_xlabel("contexto por secuencia (tokens)")
    ax2.set_ylabel("secuencias simultáneas por GPU")
    ax2.set_title("El contexto largo cuesta concurrencia, no solo tokens", fontsize=11)
    ax2.grid(alpha=0.3, which="both")

    fig.tight_layout()
    out = Path(__file__).resolve().parent.parent / "diagrams" / "prefill-decode-asimetria.png"
    fig.savefig(out, dpi=130)
    print(f"\n[diagrama] {out.relative_to(ROOT)}")


if __name__ == "__main__":
    log.info("Modelo analítico sobre specs públicas — sin GPU, sin red.")
    demo_asimetria_prefill_decode()
    demo_por_que_el_output_es_caro()
    demo_kv_cache()
    demo_concurrencia()
    demo_consecuencia_rag()
    grafico_asimetria()
    print()
