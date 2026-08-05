"""§3 — La herramienta como contrato.

Produce los números de `theory/03-tool-como-contrato.md`. Cuatro partes:

  A. Granularidad: el mismo agente con `vecinos_grafo` (un salto, un tipo de
     relación por llamada) contra el mismo agente que además tiene
     `alcance_normativo` (la dependencia transitiva de una sola llamada). Es
     el tratamiento de la falla que §1 dejó abierta en `t-08`.
  B. El precio de estar en el menú: cuánto cuesta el esquema de cada
     herramienta, se use o no, y a partir de qué ahorro se paga sola.
  C. El error como canal de enseñanza, desagregado por tipo de fallo sobre
     las trayectorias que §1 ya produjo.
  D. Paginación: qué pasaría si `leer_norma` devolviera el documento entero.

Offline por defecto. `--allow-api` regenera el caché de la parte A.

    uv run python 06-harness/code/03-tool-como-contrato.py
    uv run python 06-harness/code/03-tool-como-contrato.py --allow-api
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_lib import (  # noqa: E402
    CARACTERES_POR_PAGINA,
    CORPUS_DIR,
    AgentLoop,
    EstadoPaso,
    HarnessConfig,
    MotivoCorte,
    PoliticaLLM,
    ResultadoTarea,
    Tarea,
    Trayectoria,
    cargar_tareas,
    construir_herramientas,
    contar_tokens,
    costo_esquema,
    evaluar_trayectoria,
    recuperacion_tras_error,
)

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)
AQUI = Path(__file__).resolve().parent.parent
CACHE_BASE = AQUI / "examples" / "cache-bucle.json"
CACHE_ALCANCE = AQUI / "examples" / "cache-granularidad.json"
TRAYECTORIAS_1 = AQUI / "examples" / "trayectorias-01.json"
DIAGRAMA = AQUI / "diagrams" / "granularidad-herramientas.png"

CONFIG = HarnessConfig(
    nombre="contrato+completa", max_pasos=8, max_chars_observacion=None,
    estilo_error="contrato",
)


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def correr(
    politica: PoliticaLLM, tareas: list[Tarea], *, con_alcance: bool
) -> tuple[list[ResultadoTarea], list[Trayectoria]]:
    registry = construir_herramientas(con_alcance=con_alcance)
    resultados, trayectorias = [], []
    for tarea in tareas:
        tray = AgentLoop(registry, politica, CONFIG, medir_contexto=True).correr(
            tarea.id, tarea.pregunta
        )
        trayectorias.append(tray)
        resultados.append(evaluar_trayectoria(tray, tarea))
    return resultados, trayectorias


def por_familia(resultados: list[ResultadoTarea]) -> dict[str, float]:
    familias: dict[str, list[ResultadoTarea]] = {}
    for r in resultados:
        familias.setdefault(r.familia, []).append(r)
    return {f: sum(r.acierto_exacto for r in rs) / len(rs) for f, rs in sorted(familias.items())}


# --------------------------------------------------------------------------- #
# A. Granularidad.
# --------------------------------------------------------------------------- #
def parte_a(tareas, allow_api):
    seccion("A · Granularidad: la herramienta como unidad de delegación")
    print(
        "Misma capacidad subyacente (el grafo normativo de 05), misma información,\n"
        "mismo modelo. Lo único que cambia es en qué unidades se ofrece.\n"
    )
    base = PoliticaLLM(cache_path=CACHE_BASE, allow_api=False)
    res_a, tray_a = correr(base, tareas, con_alcance=False)

    alcance = PoliticaLLM(cache_path=CACHE_ALCANCE, allow_api=allow_api, max_api_calls=300)
    res_b, tray_b = correr(alcance, tareas, con_alcance=True)

    filas = [
        ("acierto exacto", lambda rs: sum(r.acierto_exacto for r in rs) / len(rs), "{:.3f}"),
        ("F1 de docs citados", lambda rs: statistics.mean(r.f1 for r in rs), "{:.3f}"),
        ("pasos promedio", lambda rs: statistics.mean(r.n_pasos for r in rs), "{:.2f}"),
        ("tokens de entrada", lambda rs: sum(r.tokens_in for r in rs), "{:,.0f}"),
        ("costo USD", lambda rs: sum(r.costo_usd for r in rs), "{:.4f}"),
        ("tareas sin respuesta",
         lambda rs: sum(r.motivo_corte is not MotivoCorte.RESPONDIO for r in rs), "{:.0f}"),
    ]
    print(f"{'métrica':<24}{'grano fino':>16}{'+ grano grueso':>18}{'delta':>14}")
    print("-" * 72)
    for etiqueta, fn, fmt in filas:
        x, y = fn(res_a), fn(res_b)
        print(f"{etiqueta:<24}{fmt.format(x):>16}{fmt.format(y):>18}{fmt.format(y - x):>14}")

    seccion("A · Acierto por familia: dónde se concentra la diferencia")
    fam_a, fam_b = por_familia(res_a), por_familia(res_b)
    print(f"{'familia':<16}{'grano fino':>14}{'+ grano grueso':>18}{'delta':>10}")
    print("-" * 58)
    for f in sorted(fam_a):
        print(f"{f:<16}{fam_a[f]:>14.3f}{fam_b[f]:>18.3f}{fam_b[f] - fam_a[f]:>10.3f}")

    seccion("A · Las cuatro tareas estructurales, una por una")
    print(f"{'tarea':<7}{'grano fino':>28}{'+ grano grueso':>28}")
    print("-" * 64)
    for ra, rb, ta, tb in zip(res_a, res_b, tray_a, tray_b):
        if ra.familia != "estructural":
            continue
        ea = f"{'ok' if ra.acierto_exacto else 'falla'} ({ra.n_pasos}p, {ta.motivo_corte.value})"
        eb = f"{'ok' if rb.acierto_exacto else 'falla'} ({rb.n_pasos}p, {tb.motivo_corte.value})"
        print(f"{ra.tarea_id:<7}{ea:>28}{eb:>28}")

    print("\nTrayectoria de t-08 con la herramienta de grano grueso:")
    t08 = next(t for t in tray_b if t.tarea_id == "t-08")
    for p in t08.pasos:
        args = json.dumps(p.argumentos, ensure_ascii=False)
        print(f"  {p.indice} {p.herramienta}({args[:74]}) -> {p.estado.value}")

    print(f"\nllamadas de esta corrida : {alcance.api_calls}")
    print(f"aciertos de caché        : {alcance.aciertos_cache}")
    print(f"costo histórico          : USD {alcance.historical_cost_usd:.4f}")
    return res_a, res_b, tray_a, tray_b


# --------------------------------------------------------------------------- #
# B. El precio de estar en el menú.
# --------------------------------------------------------------------------- #
def parte_b(res_a, res_b, tray_a, tray_b):
    seccion("B · El precio de estar en el menú")
    reg = construir_herramientas(con_alcance=True)
    costos = {n: costo_esquema(reg.get(n)) for n in reg.nombres}
    print(f"{'herramienta':<22}{'tokens de esquema':>20}")
    print("-" * 42)
    for nombre, costo in sorted(costos.items(), key=lambda kv: -kv[1]):
        print(f"{nombre:<22}{costo:>20,}")
    print(f"{'TOTAL (5 tools)':<22}{sum(costos.values()):>20,}")
    print(f"{'TOTAL sin alcance':<22}{sum(costos.values()) - costos['alcance_normativo']:>20,}")

    # El esquema viaja en cada iteración de cada tarea, se use o no.
    iteraciones_b = sum(r.n_pasos for r in res_b)
    peaje = costos["alcance_normativo"] * iteraciones_b
    ahorro = sum(r.tokens_in for r in res_a) - sum(r.tokens_in for r in res_b)
    usos = sum(
        1 for t in tray_b for p in t.pasos if p.herramienta == "alcance_normativo"
    )
    print(
        f"\nEl esquema de 'alcance_normativo' son {costos['alcance_normativo']} tokens que "
        f"viajan en\ncada una de las {iteraciones_b} iteraciones del brazo con la "
        f"herramienta: {peaje:,} tokens de peaje,\npagados también en las tareas de "
        f"recuperación y abstención que nunca la llaman.\n"
        f"Se usó {usos} veces y el brazo gastó {ahorro:,} tokens de entrada MENOS "
        f"que el base:\nel peaje se recupera {ahorro / peaje:.1f} veces."
    )
    print(
        "\nRegla: una herramienta se paga sola si los pasos que ahorra valen más que\n"
        "su esquema multiplicado por TODAS las iteraciones de TODAS las tareas —\n"
        "incluidas aquellas donde no se usa."
    )
    return costos


# --------------------------------------------------------------------------- #
# C. El error, por tipo.
# --------------------------------------------------------------------------- #
def parte_c():
    seccion("C · El error como canal de enseñanza, por tipo de fallo")
    datos = json.loads(TRAYECTORIAS_1.read_text(encoding="utf-8"))
    for brazo in ("opaco+completa", "contrato+completa"):
        trays = [Trayectoria.model_validate(t) for t in datos[brazo]]
        oportunidades, tasa = recuperacion_tras_error(trays)
        conteo: dict[str, int] = {}
        for t in trays:
            for p in t.pasos:
                if p.estado is not EstadoPaso.OK:
                    conteo[p.estado.value] = conteo.get(p.estado.value, 0) + 1
        print(
            f"{brazo:<20} errores={sum(conteo.values()):>3}  "
            f"recuperación={tasa:.3f} (n={oportunidades})  {json.dumps(conteo, ensure_ascii=False)}"
        )
    print(
        "\nLos tres tipos no son intercambiables: 'herramienta desconocida' se corrige\n"
        "con la lista de nombres, 'argumentos inválidos' con el campo que falló, y\n"
        "'error de ejecución' solo con un mensaje que diga qué valor era válido."
    )


# --------------------------------------------------------------------------- #
# D. Paginación.
# --------------------------------------------------------------------------- #
def parte_d(tray_a):
    seccion("D · Paginación: qué pasaría sin ella")
    docs = sorted(CORPUS_DIR.glob("*.txt"))
    tam = {d.name: len(d.read_text(encoding="utf-8")) for d in docs}
    tokens_corpus = sum(contar_tokens(d.read_text(encoding="utf-8")) for d in docs)
    mayor = max(tam, key=lambda k: tam[k])
    print(f"documentos del corpus            : {len(docs)}")
    print(f"caracteres totales               : {sum(tam.values()):,}")
    print(f"tokens totales                   : {tokens_corpus:,}")
    print(f"documento más largo              : {mayor} ({tam[mayor]:,} caracteres)")
    print(f"tamaño de página de 'leer_norma' : {CARACTERES_POR_PAGINA:,} caracteres")

    lecturas = [p for t in tray_a for p in t.pasos if p.herramienta == "leer_norma"]
    print(f"\nllamadas a 'leer_norma' en el brazo base: {len(lecturas)}")
    print(
        "En este corpus la paginación casi no muerde: el documento más largo son "
        f"{tam[mayor]:,}\ncaracteres, apenas {tam[mayor] / CARACTERES_POR_PAGINA:.1f} "
        "páginas. La regla se justifica igual, y el motivo\nes de contrato, no de "
        "tamaño: una herramienta cuyo tamaño de salida depende del\ndato de entrada "
        "no tiene contrato de salida."
    )
    print(
        f"\nContrafáctico: una herramienta 'volcar_corpus' sin paginar metería "
        f"{tokens_corpus:,}\ntokens en una sola observación — más de {tokens_corpus / 757:.0f} "
        "veces el prefijo fijo de §2 —\ny se reenviarían en cada iteración posterior."
    )


def diagrama(res_a, res_b, costos) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    fam_a, fam_b = por_familia(res_a), por_familia(res_b)
    familias = sorted(fam_a)
    x = range(len(familias))
    ancho = 0.36
    ax1.bar([i - ancho / 2 for i in x], [fam_a[f] for f in familias], ancho,
            label="grano fino (vecinos_grafo)", color="#c44e52")
    ax1.bar([i + ancho / 2 for i in x], [fam_b[f] for f in familias], ancho,
            label="+ grano grueso (alcance_normativo)", color="#55a868")
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(familias)
    ax1.set_ylim(0, 1.05)
    ax1.set_ylabel("acierto exacto")
    ax1.legend(fontsize=8)
    ax1.grid(axis="y", alpha=0.3)
    ax1.set_title("La granularidad correcta es la de la pregunta,\nno la del dato")

    nombres = sorted(costos, key=lambda k: -costos[k])
    ax2.barh(range(len(nombres)), [costos[n] for n in nombres], color="#4c72b0")
    ax2.set_yticks(range(len(nombres)))
    ax2.set_yticklabels(nombres, fontsize=9)
    ax2.invert_yaxis()
    ax2.set_xlabel("tokens de esquema, pagados en cada iteración")
    ax2.grid(axis="x", alpha=0.3)
    ax2.set_title("El menú se paga entero en cada llamada,\nse use o no")

    fig.tight_layout()
    DIAGRAMA.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(DIAGRAMA, dpi=140, bbox_inches="tight")
    print(f"\nDiagrama: {DIAGRAMA.relative_to(AQUI.parent)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-api", action="store_true")
    args = parser.parse_args()

    tareas = cargar_tareas()
    res_a, res_b, tray_a, tray_b = parte_a(tareas, args.allow_api)
    costos = parte_b(res_a, res_b, tray_a, tray_b)
    parte_c()
    parte_d(tray_a)
    diagrama(res_a, res_b, costos)


if __name__ == "__main__":
    main()
