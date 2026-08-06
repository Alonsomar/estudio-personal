"""§9 — El harness como práctica personal.

Produce los números de `theory/09-harness-personal.md`. El objeto de estudio
cambia: ya no es el agente sobre el corpus chileno, es **este repositorio**
como harness de un humano que trabaja con agentes.

  A. El presupuesto de contexto del harness personal, con el mismo
     tokenizador de §2: cuánto pesan `AGENTS.md`, `CLAUDE.md` y `BACKLOG.md`
     en cada sesión.
  B. Auditoría de reglas: cuáles están **verificadas por una máquina** y
     cuáles son aspiracionales. La clasificación está congelada y anotada a
     mano en `examples/reglas-harness.json`, con el mecanismo de cada una.
  C. El mapa entre las piezas de la práctica personal y las categorías que
     el módulo construyó.

Sin modelo y sin red: sólo lee archivos del repo.

    uv run python 06-harness/code/09-harness-personal.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_lib import contar_tokens  # noqa: E402

from shared.utils import get_project_root  # noqa: E402

RAIZ = get_project_root()
AQUI = Path(__file__).resolve().parent.parent
REGLAS = AQUI / "examples" / "reglas-harness.json"
DIAGRAMA = AQUI / "diagrams" / "harness-personal.png"


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def parte_a():
    seccion("A · El presupuesto de contexto del harness personal")
    print(
        "Los archivos de instrucciones son el prompt de sistema de la colaboración,\n"
        "y se pagan en cada sesión igual que la partida 'sistema' de §2.\n"
    )
    archivos = [
        ("AGENTS.md", RAIZ / "AGENTS.md", "convenciones del repo"),
        ("CLAUDE.md", RAIZ / "CLAUDE.md", "puntero a AGENTS.md"),
        ("BACKLOG.md", RAIZ / "BACKLOG.md", "cola de trabajo con IDs estables"),
        ("README.md", RAIZ / "README.md", "portada y marco de capas"),
    ]
    print(f"{'archivo':<16}{'líneas':>9}{'tokens':>9}   {'qué es'}")
    print("-" * 74)
    total = 0
    for nombre, ruta, que_es in archivos:
        if not ruta.exists():
            continue
        texto = ruta.read_text(encoding="utf-8")
        tokens = contar_tokens(texto)
        total += tokens
        print(f"{nombre:<16}{len(texto.splitlines()):>9}{tokens:>9}   {que_es}")
    print("-" * 74)
    print(f"{'TOTAL':<16}{'':>9}{total:>9}")
    # Referencia medida en §2: 85.654 tokens de entrada para las 12 tareas del
    # agente único, es decir ~7.138 por tarea.
    por_tarea = 85_654 / 12
    print(
        f"\nEl conjunto son {total:,} tokens: aproximadamente lo que el agente de §1 "
        f"gasta\nen UNA tarea ({por_tarea:,.0f} tokens de entrada por tarea). Leer el "
        "harness personal\nentero al empezar una sesión cuesta una consulta del "
        "agente. Es barato; lo caro\nes no tenerlo."
    )

    seccion("A · Por qué BACKLOG.md no se lee entero y está bien")
    backlog = (RAIZ / "BACKLOG.md").read_text(encoding="utf-8")
    print(
        f"`BACKLOG.md` son {contar_tokens(backlog):,} tokens y crece con cada tarea "
        "cerrada.\nNo es contexto: es MEMORIA EXTERNA direccionable, exactamente el "
        "patrón de §2.\nLos IDs estables (B1, B2…) son las claves; el agente recupera "
        "la tarea que\nnecesita en vez de arrastrar el historial completo. Un backlog "
        "sin IDs sería\nun historial sin índice — la `VentanaDeslizante` pelada que "
        "§2 midió perdiendo\nel 20% de la evidencia."
    )
    return total


def parte_b():
    seccion("B · Qué reglas verifica una máquina y cuáles son aspiracionales")
    datos = json.loads(REGLAS.read_text(encoding="utf-8"))
    reglas = datos["reglas"]
    print(datos["metadata"]["descripcion"] + "\n")

    for estado in ("verificada", "parcial", "aspiracional"):
        grupo = [r for r in reglas if r["estado"] == estado]
        print(f"\n{estado.upper()} ({len(grupo)} de {len(reglas)})")
        print("-" * 78)
        for r in grupo:
            print(f"  · {r['regla']}")
            print(f"      mecanismo: {r['mecanismo']}")

    n_verif = sum(1 for r in reglas if r["estado"] == "verificada")
    n_parc = sum(1 for r in reglas if r["estado"] == "parcial")
    print(
        f"\n\n{n_verif} de {len(reglas)} reglas tienen un mecanismo que las hace "
        f"cumplir sin que\nnadie se acuerde; {n_parc} lo tienen a medias. El resto "
        "depende de que el agente\nlas lea y las respete — que es exactamente el "
        "'siguiente paso' irrealizable\nde §5, a escala de proceso de trabajo."
    )
    return reglas


def parte_c():
    seccion("C · El mapa: cada pieza de la práctica, en la categoría del módulo")
    mapa = [
        ("CLAUDE.md global + AGENTS.md", "prompt de sistema",
         "§2: partida fija, se paga en cada sesión"),
        ("BACKLOG.md con IDs estables", "memoria externa direccionable",
         "§2: recuperar por clave en vez de arrastrar"),
        ("hook SessionStart", "el paso 'percibir', automatizado",
         "§1: el agente no elige si mirar el estado, lo recibe"),
        ("hook SessionEnd (DIARIO.md)", "registro de trayectoria",
         "§7: se evalúa el proceso, no sólo el resultado"),
        ("skills (/cierre, /nuevo-repo)", "herramientas de grano grueso",
         "§3: una llamada por intención, no N por paso"),
        ("permissions allow/deny", "política de permisos por riesgo",
         "§6: el default seguro y los checkpoints"),
        ("subagentes", "aislamiento de contexto",
         "§5: contexto propio, resumen al volver"),
        ("Claude Code + Codex", "orquestador / trabajador",
         "§5: la frontera no corta una dependencia"),
        ("`uv run pytest` antes de cerrar", "regla verificable",
         "§3: el contrato que la máquina puede chequear"),
    ]
    print(f"{'pieza de la práctica':<32}{'qué es en el módulo':<34}{'dónde'}")
    print("-" * 100)
    for pieza, categoria, donde in mapa:
        print(f"{pieza:<32}{categoria:<34}{donde}")


def diagrama(reglas, tokens_total) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    estados = ["verificada", "parcial", "aspiracional"]
    conteos = [sum(1 for r in reglas if r["estado"] == e) for e in estados]
    colores = ["#55a868", "#dd8452", "#c44e52"]
    ax1.bar(estados, conteos, color=colores, width=0.55)
    for i, c in enumerate(conteos):
        ax1.text(i, c + 0.15, str(c), ha="center", fontsize=11)
    ax1.set_ylabel("reglas del harness personal")
    ax1.set_ylim(0, max(conteos) * 1.25)
    ax1.grid(axis="y", alpha=0.3)
    ax1.set_title("Una regla sin mecanismo es una intención\n(auditoría de este repo)")

    archivos = [
        ("AGENTS.md", RAIZ / "AGENTS.md"),
        ("CLAUDE.md", RAIZ / "CLAUDE.md"),
        ("BACKLOG.md", RAIZ / "BACKLOG.md"),
        ("README.md", RAIZ / "README.md"),
    ]
    nombres = [n for n, p in archivos if p.exists()]
    tokens = [contar_tokens(p.read_text(encoding="utf-8")) for n, p in archivos if p.exists()]
    ax2.barh(range(len(nombres)), tokens, color="#4c72b0")
    ax2.set_yticks(range(len(nombres)))
    ax2.set_yticklabels(nombres)
    ax2.invert_yaxis()
    ax2.set_xlabel("tokens")
    ax2.grid(axis="x", alpha=0.3)
    ax2.set_title(
        f"El harness personal son {tokens_total:,} tokens\n"
        "— lo que el agente de §1 gasta en una tarea"
    )

    fig.tight_layout()
    DIAGRAMA.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(DIAGRAMA, dpi=140, bbox_inches="tight")
    print(f"\nDiagrama: {DIAGRAMA.relative_to(AQUI.parent)}")


def main() -> None:
    total = parte_a()
    reglas = parte_b()
    parte_c()
    diagrama(reglas, total)


if __name__ == "__main__":
    main()
