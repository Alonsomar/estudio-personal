"""Sección 12 — Incidentes y postmortems.

Los cinco modos de falla de un RAG en producción, el detector que los reconoce a
partir de las señales de las secciones previas (§5/§6/§9/§10), y por qué la
métrica que vale es el MTTD (Mean Time To Detect), no el MTTR.

  1. Detección: un snapshot de señales → modo de falla + runbook (primeros 5 min).
  2. El embudo del incidente: bajar el MTTD encoge el radio de impacto.

Genera diagrams/incident-funnel.png. Offline, determinista, gratis.

Ejecutar:

    uv run python 03-produccion/code/12-incident-runbooks.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from prod_lib import IncidentDetector  # noqa: E402
from shared.utils import get_project_root  # noqa: E402

ROOT = get_project_root()
DIAGRAMS = ROOT / "03-produccion" / "diagrams"
SEP = "=" * 72

# Señal "sana" de base; cada escenario rompe UNA.
HEALTHY = {"provider_error_rate": 0.01, "latency_p95_ms": 950, "online_pass_rate": 0.95,
           "avg_retrieval_score": 0.55, "budget_ratio": 0.8}

SCENARIOS = {
    "Proveedor caído (503)":      {**HEALTHY, "provider_error_rate": 0.45},
    "Latencia explotada":         {**HEALTHY, "latency_p95_ms": 8200},
    "Alucinación masiva":         {**HEALTHY, "online_pass_rate": 0.52},
    "Retrieval roto":             {**HEALTHY, "avg_retrieval_score": 0.12},
    "Costo desbocado":            {**HEALTHY, "budget_ratio": 2.3},
}


# --------------------------------------------------------------------------- #
# 1. Detección + runbook por modo de falla.
# --------------------------------------------------------------------------- #
def demo_runbooks() -> None:
    print(SEP)
    print("1. LOS CINCO MODOS DE FALLA — detección y runbook")
    det = IncidentDetector()

    # Sanity: con todo sano, no hay incidente.
    print(f"\n  sistema sano → {det.check(HEALTHY) or 'sin incidentes ✓'}")

    for nombre, signals in SCENARIOS.items():
        inc = det.check(signals)[0]
        print(f"\n  ── {nombre}  [{inc.severity}]  ({inc.signal}) ───────────")
        print(f"     modo detectado: {inc.mode}")
        print("     runbook (primeros 5 minutos):")
        for i, step in enumerate(inc.runbook, 1):
            print(f"       {i}. {step}")


# --------------------------------------------------------------------------- #
# 2. El embudo del incidente: MTTD vs MTTR.
# --------------------------------------------------------------------------- #
def demo_mttd() -> None:
    print("\n" + SEP)
    print("2. MTTD > MTTR — el tiempo de DETECTAR es el que da margen")

    qps = 5.0  # requests/seg afectados durante el incidente
    # MTTR es parecido (mitigar lleva lo mismo); lo que cambia es el MTTD.
    casos = [
        ("Con observabilidad (§5/§9/§10)",  3.0, 12.0),   # alerta automática
        ("Sin observabilidad (te avisa el usuario)", 90.0, 12.0),
    ]

    print(f"\n  tráfico afectado: {qps:.0f} req/s. Radio de impacto = (MTTD+MTTR)·qps\n")
    print(f"  {'escenario':>42} | {'MTTD':>6} | {'MTTR':>6} | requests afectados")
    print(f"  {'-'*42}-+-{'-'*6}-+-{'-'*6}-+-{'-'*18}")
    rows = []
    for nombre, mttd, mttr in casos:
        afect = int((mttd + mttr) * 60 * qps)
        rows.append((nombre, mttd, mttr, afect))
        print(f"  {nombre:>42} | {mttd:>4.0f}m | {mttr:>4.0f}m | {afect:>10,}")
    factor = rows[1][3] / rows[0][3]
    print(f"\n  Mismo MTTR (12m), mismo bug. El MTTD lleva el impacto de "
          f"{rows[0][3]:,} a\n  {rows[1][3]:,} requests — {factor:.0f}× más. "
          "Bajar el MTTD (alertas, no paneles) es\n  lo que de verdad acota el daño; el MTTR es casi igual en ambos.")

    _plot_funnel(rows, qps)


def _plot_funnel(rows: list[tuple], qps: float) -> None:
    fig, ax = plt.subplots(figsize=(9.5, 4.2))
    labels = [r[0] for r in rows]
    y = range(len(rows))
    mttd = [r[1] for r in rows]
    mttr = [r[2] for r in rows]
    ax.barh(y, mttd, color="#e74c3c", edgecolor="#333", label="MTTD (detectar)")
    ax.barh(y, mttr, left=mttd, color="#3498db", edgecolor="#333", label="MTTR (resolver)")
    for i, r in enumerate(rows):
        total = r[1] + r[2]
        ax.text(total + 1.5, i, f"{r[3]:,} req afectados", va="center", fontsize=9)
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel("Minutos desde que ocurre el incidente")
    ax.set_title("Embudo del incidente — el MTTD domina el radio de impacto\n"
                 "(mismo bug, mismo MTTR; solo cambia cuándo te enterás)",
                 fontsize=11, fontweight="bold")
    ax.legend(loc="lower right")
    ax.set_xlim(0, 120)
    ax.margins(y=0.3)
    fig.tight_layout()
    DIAGRAMS.mkdir(parents=True, exist_ok=True)
    out = DIAGRAMS / "incident-funnel.png"
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print(f"\n  diagrama guardado en {out.relative_to(ROOT)}")


# --------------------------------------------------------------------------- #
def main() -> None:
    demo_runbooks()
    demo_mttd()
    print("\n" + SEP)
    print("El postmortem honesto (examples/incidents/) no pregunta 'quién falló'")
    print("sino 'qué nos faltó para detectarlo antes'. La respuesta casi siempre")
    print("es una señal que ya teníamos y no estábamos mirando. Fin de 03-produccion.")


if __name__ == "__main__":
    main()
