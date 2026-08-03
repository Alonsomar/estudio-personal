"""§2 — Batching, throughput y la latencia que no controlás.

Produce los números que cita `theory/02-batching.md`. Modelo analítico offline
y determinista: sin GPU, sin red.

    uv run python 04-economia/code/02-batching.py
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
    batch_curve,
    max_concurrent_sequences,
    queue_wait_ms,
)
from prod_lib import PRICING_USD_PER_M_TOKENS as PRICING  # noqa: E402

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)

RAG_OUTPUT_TOKENS = 60
BATCHES = (1, 2, 4, 8, 16, 32, 64, 128, 256)


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def demo_economia_de_escala() -> None:
    """El batching reparte el costo fijo de mover pesos: costo medio decreciente."""
    seccion("1. El batching como economía de escala (clase 8B, H100 80GB)")

    gpu, m = GPUS["H100-80"], MODELS["8B"]
    print(
        f"{'batch':>6} | {'tok/s total':>12} | {'tok/s por seq':>14} | "
        f"{'ms/token seq':>13} | {'$/M tokens':>11}"
    )
    print("-" * 72)
    for p in batch_curve(m, gpu, BATCHES):
        print(
            f"{p.batch:>6} | {p.tokens_per_s_total:>12,.0f} | "
            f"{p.tokens_per_s_per_seq:>14,.0f} | {p.ms_per_token_per_seq:>13.1f} | "
            f"{p.usd_per_m_tokens:>11.3f}"
        )

    pts = {p.batch: p for p in batch_curve(m, gpu, BATCHES)}
    print(
        f"\nDe batch 1 a batch 64, el costo por millón de tokens cae de "
        f"${pts[1].usd_per_m_tokens:.2f} a ${pts[64].usd_per_m_tokens:.3f}\n"
        f"({pts[1].usd_per_m_tokens / pts[64].usd_per_m_tokens:.0f}× más barato) "
        "SIN que la latencia por secuencia se mueva.\n"
        "Ese es todo el negocio de un proveedor de inferencia."
    )


def demo_vs_tarifa_real() -> None:
    """Contraste del costo modelado contra las tarifas que se cobran."""
    seccion("2. El costo modelado vs. la tarifa que pagás")

    gpu, m = GPUS["H100-80"], MODELS["8B"]
    pts = {p.batch: p for p in batch_curve(m, gpu, BATCHES)}
    barato = min(PRICING.items(), key=lambda kv: kv[1]["out"])

    print(f"Costo de GPU modelado (clase 8B, H100 a ${gpu.usd_per_hour}/h):")
    for b in (1, 32, 256):
        print(f"  batch {b:>3}: ${pts[b].usd_per_m_tokens:>8.3f} / M tokens de salida")
    print(f"\nTarifa pública más barata de prod_lib: {barato[0]} a ${barato[1]['out']:.3f} / M out")
    print(
        f"\nA batch 1 el costo de GPU (${pts[1].usd_per_m_tokens:.2f}) es "
        f"{pts[1].usd_per_m_tokens / barato[1]['out']:.0f}× la tarifa pública:\n"
        "servir de a una secuencia es ruinoso. Recién con batching agresivo el\n"
        "costo cae por debajo del precio de lista. El proveedor no te vende\n"
        "una GPU: te vende una fracción de una GPU muy ocupada."
    )


def demo_cola() -> None:
    """Por qué el p95 explota cerca de la saturación: teoría de colas."""
    seccion("3. La latencia que no controlás: utilización y cola")

    servicio_ms = 420.0  # latencia de servicio de la carga RAG (§1)
    print(f"Tiempo de servicio de una query del RAG chileno: {servicio_ms:.0f} ms (§1)\n")
    print(f"{'utilización':>12} | {'espera en cola':>15} | {'latencia total':>15} | {'vs vacío':>9}")
    print("-" * 60)
    for rho in (0.1, 0.3, 0.5, 0.7, 0.8, 0.9, 0.95, 0.99):
        w = queue_wait_ms(servicio_ms, rho)
        total = servicio_ms + w
        print(
            f"{rho:>11.0%} | {w:>12.0f} ms | {total:>12.0f} ms | "
            f"{total / servicio_ms:>8.1f}×"
        )

    print(
        "\nLa latencia no se degrada linealmente con la carga: explota cerca de\n"
        "la saturación. Al 50% de utilización esperás lo mismo que tardás; al 95%,\n"
        "diecinueve veces más. Y la utilización del proveedor la fija el tráfico\n"
        "de TODOS sus clientes, no el tuyo."
    )


def demo_que_controlas() -> None:
    """Qué palancas tiene el cliente de una API, cuantificadas."""
    seccion("4. Qué controlás vos como cliente de una API")

    gpu, m = GPUS["H100-80"], MODELS["8B"]
    filas = [
        ("max_tokens (§1: el output es la fase cara)", "alto", "directo y grande"),
        ("Streaming (time-to-first-token vs total)", "alto", "percepción, no costo"),
        ("Concurrencia propia / rate limit (03 §6)", "medio", "evita tu propia cola"),
        ("Tamaño del prompt", "medio", "barato en tiempo, lineal en $"),
        ("Utilización del proveedor", "NINGUNO", "la fija el tráfico de otros"),
        ("Tamaño del batch del proveedor", "NINGUNO", "decisión de infraestructura ajena"),
    ]
    print(f"{'palanca':>44} | {'control':>8} | {'efecto':>22}")
    print("-" * 80)
    for palanca, control, efecto in filas:
        print(f"{palanca:>44} | {control:>8} | {efecto:>22}")

    n = max_concurrent_sequences(m, gpu, 4_000)
    print(
        f"\nY la palanca que sí es tuya y se subestima: el rate limit propio de 03 §6.\n"
        f"Si mandás más requests concurrentes de los que el proveedor te sirve en\n"
        "paralelo, la cola extra es TUYA y te la comés en el p95. Autolimitarse no\n"
        "es solo cortesía con el proveedor: es la diferencia entre un p95 estable\n"
        f"y uno que explota. (Referencia: una H100 sostiene ~{n} secuencias de 4k.)"
    )


def grafico_batching() -> None:
    """Dos paneles: costo medio decreciente y explosión de la cola."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    gpu, m = GPUS["H100-80"], MODELS["8B"]
    pts = batch_curve(m, gpu, BATCHES)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))

    batches = [p.batch for p in pts]
    ax1.plot(batches, [p.usd_per_m_tokens for p in pts], "o-", lw=2, color="#2ecc71",
             label="costo de GPU por M tokens")
    barato = min(PRICING.values(), key=lambda p: p["out"])["out"]
    ax1.axhline(barato, ls="--", color="#e74c3c", lw=1.5,
                label=f"tarifa pública más barata (${barato:.2f}/M)")
    ax1.set_xscale("log", base=2)
    ax1.set_yscale("log")
    ax1.set_xlabel("tamaño de batch")
    ax1.set_ylabel("USD por millón de tokens de salida")
    ax1.set_title("Costo medio decreciente: la economía de escala", fontsize=11)
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3, which="both")

    rho = np.linspace(0.01, 0.97, 200)
    servicio = 420.0
    ax2.plot(rho * 100, [servicio + queue_wait_ms(servicio, r) for r in rho],
             lw=2, color="#e67e22")
    ax2.axhline(servicio, ls=":", color="#666", lw=1, label="servicio sin cola (420 ms)")
    ax2.axvline(80, ls="--", color="#c0392b", lw=1, label="80% de utilización")
    ax2.set_xlabel("utilización del sistema (%)")
    ax2.set_ylabel("latencia total (ms)")
    ax2.set_ylim(0, 6000)
    ax2.set_title("La latencia explota cerca de la saturación", fontsize=11)
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)

    fig.tight_layout()
    out = Path(__file__).resolve().parent.parent / "diagrams" / "batching-costo-cola.png"
    fig.savefig(out, dpi=130)
    print(f"\n[diagrama] {out.relative_to(ROOT)}")


if __name__ == "__main__":
    log.info("Modelo analítico — sin GPU, sin red.")
    demo_economia_de_escala()
    demo_vs_tarifa_real()
    demo_cola()
    demo_que_controlas()
    grafico_batching()
    print()
