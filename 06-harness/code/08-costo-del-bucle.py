"""§8 — Costo y latencia del bucle.

Produce los números de `theory/08-costo-del-bucle.md`. Todo se calcula sobre
las trayectorias que las secciones anteriores ya produjeron; cero llamadas a
la API.

  A. La distribución del costo por tarea. No la media: los percentiles.
  B. Caching de prefijo: cuánto del gasto de entrada es prefijo repetido y
     cuánto de eso es efectivamente cacheable con las reglas del proveedor.
  C. Reglas de corte: cuándo habría disparado cada una sobre las trayectorias
     reales, cuántos pasos habría ahorrado y a cuántas respuestas correctas
     les habría cortado la cabeza.

    uv run python 06-harness/code/08-costo-del-bucle.py
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_lib import (  # noqa: E402
    REGLAS_CORTE,
    AgentLoop,
    HarnessConfig,
    MotivoCorte,
    Partida,
    PoliticaLLM,
    ToolRegistry,
    Trayectoria,
    cargar_tareas,
    construir_herramientas,
    evaluar_trayectoria,
    herramienta_delegar,
)

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)
AQUI = Path(__file__).resolve().parent.parent
DIAGRAMA = AQUI / "diagrams" / "costo-del-bucle.png"

CONFIG = HarnessConfig(
    nombre="contrato+completa", max_pasos=8, max_chars_observacion=None,
    estilo_error="contrato",
)

# Tarifas de gpt-4o-mini, en USD por millón de tokens. El descuento de
# entrada cacheada y el umbral están verificados contra la documentación del
# proveedor y citados en la teoría.
TARIFA_IN = 0.15
TARIFA_OUT = 0.60
DESCUENTO_CACHE = 0.50
UMBRAL_CACHE_TOKENS = 1_024


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def _cargar_multiagente():
    import importlib.util

    ruta = Path(__file__).resolve().parent / "05-multiagente.py"
    spec = importlib.util.spec_from_file_location("multiagente", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sistemas(tareas):
    mod = _cargar_multiagente()
    salida = {}
    pol1 = PoliticaLLM(cache_path=AQUI / "examples" / "cache-bucle.json")
    reg1 = construir_herramientas()
    salida["agente único"] = [
        (AgentLoop(reg1, pol1, CONFIG, medir_contexto=True).correr(t.id, t.pregunta), [], t)
        for t in tareas
    ]
    pol3 = PoliticaLLM(cache_path=AQUI / "examples" / "cache-multiagente.json")
    base = construir_herramientas(con_alcance=True)
    filas = []
    for tarea in tareas:
        registro: list[Trayectoria] = []
        trabajadores = mod.construir_trabajadores(
            base, mod.CONFIG_TRABAJADOR_CONSCIENTE, estructural_busca=True
        )
        orq = ToolRegistry(
            [herramienta_delegar(trabajadores, pol3, registro), base.get("responder")]
        )
        tray = AgentLoop(orq, pol3, mod.CONFIG_ORQUESTADOR, medir_contexto=True).correr(
            tarea.id, tarea.pregunta
        )
        filas.append((tray, registro, tarea))
    salida["orquestado"] = filas
    return salida


def costo_usd(tokens_in: int, tokens_out: int) -> float:
    return tokens_in / 1e6 * TARIFA_IN + tokens_out / 1e6 * TARIFA_OUT


def percentil(valores: list[float], p: float) -> float:
    ordenados = sorted(valores)
    if not ordenados:
        return 0.0
    k = (len(ordenados) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(ordenados) - 1)
    return ordenados[lo] + (ordenados[hi] - ordenados[lo]) * (k - lo)


# --------------------------------------------------------------------------- #
# A. La distribución.
# --------------------------------------------------------------------------- #
def parte_a(corridas):
    seccion("A · El costo por tarea es una distribución, no un número")
    print(
        "Un agente no hace una llamada, hace N — y N es una variable aleatoria. La\n"
        "media es el número que se usa para planificar y la cola es la que rompe el\n"
        "plan.\n"
    )
    print(f"{'sistema':<16}{'media':>10}{'mediana':>10}{'p90':>10}{'p95':>10}"
          f"{'máx':>10}{'máx/mediana':>13}")
    print("-" * 79)
    distribuciones = {}
    for etiqueta, filas in corridas.items():
        costos = []
        for tray, subs, _ in filas:
            t_in = tray.tokens_in + sum(s.tokens_in for s in subs)
            t_out = tray.tokens_out + sum(s.tokens_out for s in subs)
            costos.append(costo_usd(t_in, t_out) * 1_000)  # milésimas de USD
        distribuciones[etiqueta] = costos
        mediana = statistics.median(costos)
        print(
            f"{etiqueta:<16}{statistics.mean(costos):>10.3f}{mediana:>10.3f}"
            f"{percentil(costos, 0.90):>10.3f}{percentil(costos, 0.95):>10.3f}"
            f"{max(costos):>10.3f}{max(costos) / mediana:>13.1f}×"
        )
    print("\n(costos en milésimas de USD por tarea, gpt-4o-mini)")

    seccion("A · Las llamadas por tarea, que es lo que gobierna la cola")
    print(f"{'sistema':<16}{'media':>10}{'mediana':>10}{'p90':>10}{'máx':>10}{'máx/mediana':>13}")
    print("-" * 69)
    llamadas_por_sistema = {}
    for etiqueta, filas in corridas.items():
        llamadas = [
            float(tray.n_pasos + sum(s.n_pasos for s in subs)) for tray, subs, _ in filas
        ]
        llamadas_por_sistema[etiqueta] = llamadas
        mediana = statistics.median(llamadas)
        print(
            f"{etiqueta:<16}{statistics.mean(llamadas):>10.2f}{mediana:>10.1f}"
            f"{percentil(llamadas, 0.90):>10.1f}{max(llamadas):>10.0f}"
            f"{max(llamadas) / mediana:>13.1f}×"
        )

    seccion("A · Qué le hace la cola al plan mensual de 04 §6")
    print(
        "`04 §6` cerró con márgenes brutos sobre 99% y una advertencia: con 15 pasos\n"
        "por consulta la holgura del plan caía de 7.975× a 12×. Este es el mismo\n"
        "cálculo con la distribución medida, no con un supuesto.\n"
    )
    consultas_mes = 1_000
    print(f"{'sistema':<16}{'si toda consulta':>20}{'si toda consulta':>20}{'razón':>9}")
    print(f"{'':<16}{'cuesta la mediana':>20}{'cuesta el p95':>20}{'':>9}")
    print("-" * 65)
    for etiqueta, costos in distribuciones.items():
        med = statistics.median(costos) / 1000 * consultas_mes
        p95 = percentil(costos, 0.95) / 1000 * consultas_mes
        print(f"{etiqueta:<16}{f'USD {med:.2f}':>20}{f'USD {p95:.2f}':>20}{p95 / med:>8.1f}×")
    print(
        f"\n({consultas_mes:,} consultas/mes). El costo absoluto sigue siendo ruido a "
        "esta escala,\nque es la conclusión de `04 §6`. Lo que cambia es la "
        "PREDICTIBILIDAD: presupuestar\ncon la media subestima sistemáticamente, y el "
        "error crece con la cola."
    )
    return distribuciones, llamadas_por_sistema


# --------------------------------------------------------------------------- #
# B. Caching de prefijo.
# --------------------------------------------------------------------------- #
def parte_b(corridas):
    seccion("B · Caching de prefijo: la optimización que el bucle regala")
    print(
        "§2 midió que el 51,2% del gasto de entrada es prefijo idéntico reenviado.\n"
        "El caching de prefijo ataca exactamente eso — pero no todo lo reenviado es\n"
        "cacheable, y las reglas del proveedor deciden cuánto.\n"
    )
    filas = corridas["agente único"]
    specs = construir_herramientas().specs_openai()

    total_in = 0
    prefijo_total = 0
    prefijo_elegible = 0
    iteraciones = 0
    bajo_umbral = 0
    for tray, _, _ in filas:
        for i, paso in enumerate(tray.pasos):
            if not paso.contexto:
                continue
            iteraciones += 1
            contexto = sum(paso.contexto.values())
            prefijo = (
                paso.contexto[Partida.SISTEMA.value]
                + paso.contexto[Partida.HERRAMIENTAS.value]
                + paso.contexto[Partida.PREGUNTA.value]
            )
            total_in += contexto
            if i > 0:  # la primera iteración escribe el caché, no lo lee
                prefijo_total += prefijo
                if contexto >= UMBRAL_CACHE_TOKENS:
                    prefijo_elegible += prefijo
                else:
                    bajo_umbral += 1
    del specs

    ahorro = prefijo_elegible * DESCUENTO_CACHE
    print(f"{'iteraciones medidas':<44}{iteraciones:>12,}")
    print(f"{'tokens de entrada totales':<44}{total_in:>12,}")
    print(f"{'prefijo reenviado (iteraciones 2..N)':<44}{prefijo_total:>12,}")
    print(f"{'de ese prefijo, elegible para caché':<44}{prefijo_elegible:>12,}")
    print(f"{'iteraciones por debajo del umbral de 1.024':<44}{bajo_umbral:>12,}")
    print(f"{'ahorro con 50% de descuento':<44}{ahorro:>12,.0f}")
    print(
        f"\nahorro sobre el gasto de entrada total: {100 * ahorro / total_in:.1f}%"
        f"  (USD {costo_usd(ahorro, 0):.5f} en estas 12 tareas)"
    )
    print(
        f"\nLa letra chica: el caché sólo se activa a partir de {UMBRAL_CACHE_TOKENS:,} "
        f"tokens de\ncontexto, y {bajo_umbral} de las {iteraciones} iteraciones quedan "
        "por debajo. Las trayectorias\ncortas —las baratas— son justamente las que no "
        "califican; el descuento llega\ndonde más se gasta, que es donde tiene que "
        "llegar, pero no antes."
    )

    seccion("B · Qué hace inútil al caché de prefijo")
    print(
        "El caché exige que el prefijo sea IDÉNTICO byte a byte. Tres cosas que lo\n"
        "rompen sin que nadie lo note:\n"
        "  · una marca de tiempo o un id de sesión en el prompt de sistema\n"
        "  · reordenar las herramientas entre llamadas (el orden del menú es parte\n"
        "    del prefijo: `ToolRegistry.specs_openai` ordena por nombre a propósito)\n"
        "  · inyectar el contexto recuperado ANTES de las instrucciones en vez de\n"
        "    después\n"
        "\nLas tres convierten el 51,2% de §2 en cero, y ninguna produce un síntoma\n"
        "visible: el sistema sigue funcionando, sólo cuesta el doble."
    )
    return ahorro, total_in


# --------------------------------------------------------------------------- #
# C. Reglas de corte.
# --------------------------------------------------------------------------- #
def parte_c(corridas):
    seccion("C · Cuándo cortar un bucle que no converge")
    print(
        "`t-08` tenía la respuesta completa en el paso 2 de §3 y gastó cinco pasos\n"
        "más; en §5 el orquestador gastó 48 llamadas en la misma tarea. La pregunta\n"
        "de diseño es qué regla, OBSERVABLE DESDE ADENTRO del bucle, habría cortado\n"
        "eso sin cortar nada bueno.\n"
    )
    for etiqueta, filas in corridas.items():
        print(f"\n--- {etiqueta}")
        print(f"{'regla':<24}{'disparó en':>12}{'pasos ahorrados':>18}"
              f"{'respuestas correctas':>22}")
        print(f"{'':<24}{'(de 12)':>12}{'':>18}{'que habría cortado':>22}")
        print("-" * 78)
        for nombre, regla in REGLAS_CORTE.items():
            disparos = ahorro = danio = 0
            for tray, _, tarea in filas:
                paso = regla(tray)
                if paso is None:
                    continue
                disparos += 1
                ahorro += tray.n_pasos - (paso + 1)
                res = evaluar_trayectoria(tray, tarea)
                # Daño: la trayectoria terminó bien y la regla habría cortado
                # ANTES de que respondiera.
                if res.acierto_exacto and paso + 1 < tray.n_pasos:
                    danio += 1
            print(f"{nombre:<24}{disparos:>12}{ahorro:>18}{danio:>22}")

    print(
        "\n'pasos ahorrados' cuenta lo que se habría dejado de gastar si el bucle se\n"
        "cortara en ese paso. 'respuestas correctas que habría cortado' es el costo:\n"
        "una regla que ahorra mucho y rompe respuestas buenas no sirve."
    )

    seccion("C · La regla que §1 tenía y por qué no alcanzaba")
    print(
        "El `AgentLoop` corta cuando la MISMA llamada exacta se repite tres veces. En\n"
        "las trayectorias del agente único casi nunca dispara, porque el agente varía\n"
        "algún argumento — en `t-08` de §1 recorrió los seis tipos de relación, todos\n"
        "distintos, todos inútiles. 'Sin evidencia nueva' captura eso: no le importa\n"
        "si la llamada cambió, le importa si trajo algo.\n"
    )


def diagrama(distribuciones, llamadas) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
    etiquetas = list(distribuciones)
    colores = ["#4c72b0", "#dd8452"]

    for etiqueta, color in zip(etiquetas, colores):
        datos = sorted(llamadas[etiqueta])
        ax1.plot(datos, [i / (len(datos) - 1) for i in range(len(datos))], "o-",
                 color=color, label=etiqueta, markersize=5)
    ax1.set_xlabel("llamadas al modelo en una tarea")
    ax1.set_ylabel("fracción acumulada de tareas")
    ax1.legend(fontsize=9)
    ax1.grid(alpha=0.3)
    ax1.set_title("La cola es el problema:\nla mediana no describe al sistema")

    x = range(len(etiquetas))
    ancho = 0.26
    for i, (fn, nombre, color) in enumerate(
        [
            (lambda v: statistics.median(v), "mediana", "#55a868"),
            (lambda v: percentil(v, 0.90), "p90", "#dd8452"),
            (lambda v: max(v), "máximo", "#c44e52"),
        ]
    ):
        ax2.bar([j + (i - 1) * ancho for j in x],
                [fn(distribuciones[e]) for e in etiquetas], ancho,
                label=nombre, color=color)
    ax2.set_xticks(list(x))
    ax2.set_xticklabels(etiquetas)
    ax2.set_ylabel("milésimas de USD por tarea")
    ax2.legend(fontsize=9)
    ax2.grid(axis="y", alpha=0.3)
    ax2.set_title("Presupuestar con la media\nsubestima sistemáticamente")

    fig.tight_layout()
    DIAGRAMA.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(DIAGRAMA, dpi=140, bbox_inches="tight")
    print(f"\nDiagrama: {DIAGRAMA.relative_to(AQUI.parent)}")


def main() -> None:
    tareas = cargar_tareas()
    corridas = sistemas(tareas)
    distribuciones, llamadas = parte_a(corridas)
    parte_b(corridas)
    parte_c(corridas)
    diagrama(distribuciones, llamadas)

    seccion("Motivos de corte observados")
    for etiqueta, filas in corridas.items():
        conteo: dict[str, int] = {}
        for tray, _, _ in filas:
            conteo[tray.motivo_corte.value] = conteo.get(tray.motivo_corte.value, 0) + 1
        print(f"{etiqueta:<16}{conteo}")
    assert MotivoCorte.RESPONDIO  # el enum se usa en la teoría


if __name__ == "__main__":
    main()
