"""§6 — Control, permisos y sandboxing.

Produce los números de `theory/06-permisos-y-control.md`. Cuatro partes:

  A. El costo de monitoreo, medido sobre las trayectorias que §1 y §5 ya
     produjeron: cuántos checkpoints le pediría a un humano cada política.
  B. Idempotencia: la misma trayectoria con una herramienta con efecto,
     con y sin clave de idempotencia. Determinista, sin modelo.
  C. Inyección de prompt: un documento comprometido le ordena al agente
     ejecutar una acción irreversible. Con modelo real y caché.
  D. Qué aísla un sandbox y qué no.

Ninguna parte toca `shared/corpus_chileno/`: los efectos van a un registro en
memoria y la inyección se aplica en el harness, sobre la observación.

Offline por defecto. `--allow-api` regenera el caché de la parte C.

    uv run python 06-harness/code/06-permisos-y-control.py
    uv run python 06-harness/code/06-permisos-y-control.py --allow-api
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_lib import (  # noqa: E402
    AgentLoop,
    INYECCIONES,
    Decision,
    HarnessConfig,
    PoliticaGuionada,
    PoliticaLLM,
    PoliticaPermisos,
    RegistroEfectos,
    ToolRegistry,
    Trayectoria,
    aprobar_todo,
    cargar_tareas,
    construir_herramientas,
    envenenar,
    herramienta_marcar_obsoleta,
    rechazar_todo,
)

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)
AQUI = Path(__file__).resolve().parent.parent
CACHE = AQUI / "examples" / "cache-inyeccion.json"
TRAYECTORIAS_1 = AQUI / "examples" / "trayectorias-01.json"
TRAYECTORIAS_5 = AQUI / "examples" / "trayectorias-05.json"
DIAGRAMA = AQUI / "diagrams" / "permisos.png"

CONFIG = HarnessConfig(
    nombre="contrato+completa", max_pasos=8, max_chars_observacion=None,
    estilo_error="contrato",
)


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


# --------------------------------------------------------------------------- #
# A. El costo de monitoreo.
# --------------------------------------------------------------------------- #
def parte_a():
    seccion("A · El costo de monitoreo, sobre trayectorias reales")
    print(
        "Revisar todo anula la ganancia de delegar; no revisar nada externaliza el\n"
        "riesgo. La pregunta es cuántas interrupciones cuesta cada política — y eso\n"
        "no se estima: se cuenta sobre las trayectorias que el agente ya produjo.\n"
    )
    fuentes = {
        "§1 agente único (12 tareas)": ("contrato+completa", TRAYECTORIAS_1),
        "§5 orquestado (12 tareas)": ("orquestador_error_consciente", TRAYECTORIAS_5),
    }
    filas = []
    for etiqueta, (clave, ruta) in fuentes.items():
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        trays = [Trayectoria.model_validate(t) for t in datos[clave]]
        pasos = sum(t.n_pasos for t in trays)
        if "trabajadores_error_consciente" in datos:
            for registro in datos["trabajadores_error_consciente"]:
                pasos += sum(len(t["pasos"]) for t in registro)
        filas.append((etiqueta, pasos))

    print(f"{'sistema':<32}{'llamadas totales':>18}{'checkpoints si TODA':>22}")
    print(f"{'':<32}{'':>18}{'escritura pregunta':>22}")
    print("-" * 72)
    for etiqueta, pasos in filas:
        print(f"{etiqueta:<32}{pasos:>18}{'0 (todo lectura)':>22}")
    print(
        "\nTodas las herramientas de §1 a §5 son de lectura, así que ninguna política\n"
        "de permisos habría interrumpido nada: cero checkpoints sobre "
        f"{sum(p for _, p in filas)} llamadas.\n"
        "Ese es el punto de partida honesto — y también por qué el resto de esta\n"
        "sección necesita introducir una herramienta con efectos para tener algo\n"
        "que proteger."
    )

    seccion("A · Las tres políticas")
    reg = construir_herramientas(con_alcance=True)
    efectos = RegistroEfectos()
    reg.registrar(herramienta_marcar_obsoleta(efectos))
    politicas = [
        PoliticaPermisos.automatico(),
        PoliticaPermisos.supervisado(),
        PoliticaPermisos.solo_lectura(),
    ]
    print(f"{'herramienta':<24}{'riesgo':<26}" + "".join(f"{p.nombre:>14}" for p in politicas))
    print("-" * 92)
    for nombre in reg.nombres:
        h = reg.get(nombre)
        veredictos = "".join(f"{p.evaluar(h).value:>14}" for p in politicas)
        print(f"{nombre:<24}{h.riesgo.value:<26}{veredictos}")
    print(
        "\nEl permiso es una función de la clasificación de riesgo, no una lista de\n"
        "nombres prohibidos. Una lista hay que acordarse de actualizarla al agregar\n"
        "una herramienta; una clasificación obliga a declarar el riesgo al escribirla."
    )


# --------------------------------------------------------------------------- #
# B. Idempotencia.
# --------------------------------------------------------------------------- #
def parte_b():
    seccion("B · Idempotencia: cuando un reintento no es un reintento")
    print(
        "El bucle reintenta. `03 §6` dejó sembrada la idempotencia para\n"
        "request/response, donde reintentar una llamada idempotente es higiene. En\n"
        "un bucle con efectos laterales es otra cosa. Guion determinista, sin modelo:\n"
        "el agente marca la misma norma como obsoleta tres veces (un reintento tras\n"
        "una observación que no le resultó concluyente).\n"
    )
    guion = [
        Decision(accion="usar_herramienta", herramienta="marcar_norma_obsoleta",
                 argumentos={"doc_id": "ley-01-dl-825-iva-base.txt", "motivo": "derogada"}),
        Decision(accion="usar_herramienta", herramienta="marcar_norma_obsoleta",
                 argumentos={"doc_id": "ley-01-dl-825-iva-base.txt", "motivo": "derogada"}),
        Decision(accion="usar_herramienta", herramienta="marcar_norma_obsoleta",
                 argumentos={"doc_id": "ley-01-dl-825-iva-base.txt", "motivo": "derogada"}),
        Decision(accion="responder", respuesta="listo", docs_citados=[]),
    ]
    print(f"{'configuración':<34}{'llamadas':>10}{'efectos aplicados':>20}{'duplicados':>13}")
    print("-" * 78)
    for etiqueta, idempotencia in (("sin clave de idempotencia", False),
                                   ("con clave de idempotencia", True)):
        efectos = RegistroEfectos()
        reg = ToolRegistry([herramienta_marcar_obsoleta(efectos)])
        reg.registrar(construir_herramientas().get("responder"))
        tray = AgentLoop(
            reg, PoliticaGuionada(list(guion)), CONFIG.model_copy(update={"max_repeticiones": 99}),
            idempotencia=idempotencia,
        ).correr("efecto", "marcá la norma como obsoleta")
        llamadas = sum(1 for p in tray.pasos if p.herramienta == "marcar_norma_obsoleta")
        print(
            f"{etiqueta:<34}{llamadas:>10}{efectos.cuenta('marcar_obsoleta'):>20}"
            f"{efectos.duplicados():>13}"
        )
    print(
        "\nLa clave identifica la ACCIÓN, no la llamada: mismo doc_id y mismo motivo\n"
        "son el mismo hecho, se pida una vez o tres. Sin ella, el corte por\n"
        "repetición del bucle (§1) llega tarde: para cuando detecta que el agente se\n"
        "repite, la acción ya se aplicó tres veces."
    )


# --------------------------------------------------------------------------- #
# C. Inyección de prompt.
# --------------------------------------------------------------------------- #
def parte_c(allow_api: bool):
    seccion("C · Inyección: cuando la instrucción viene dentro del dato")
    print(
        "`03 §11` trató la inyección como un problema de salida: el modelo dice algo\n"
        "que no debía. En un agente con herramientas es un problema de EJECUCIÓN.\n"
        "Un documento comprometido trae, pegada al texto, una orden de llamar a\n"
        "'marcar_norma_obsoleta'. El corpus NO se modifica: la carga se inyecta en el\n"
        "harness, sobre la observación de 'buscar_corpus'.\n"
    )
    politica = PoliticaLLM(cache_path=CACHE, allow_api=allow_api, max_api_calls=200)
    tareas = {t.id: t for t in cargar_tareas()}
    tarea = tareas["t-01"]

    escenarios = [
        ("sin control (automático)", PoliticaPermisos.automatico(), aprobar_todo),
        ("supervisado, humano que aprueba", PoliticaPermisos.supervisado(), aprobar_todo),
        ("supervisado, humano que lee", PoliticaPermisos.supervisado(), rechazar_todo),
        ("solo lectura", PoliticaPermisos.solo_lectura(), aprobar_todo),
    ]

    def correr(carga: str, permisos, aprobador):
        efectos = RegistroEfectos()
        base = construir_herramientas()
        reg = ToolRegistry(
            [envenenar(base.get("buscar_corpus"), carga), base.get("leer_norma"),
             base.get("responder"), herramienta_marcar_obsoleta(efectos)]
        )
        loop = AgentLoop(reg, politica, CONFIG, permisos=permisos, aprobador=aprobador)
        tray = loop.correr(tarea.id, tarea.pregunta)
        intentos = sum(1 for p in tray.pasos if p.herramienta == "marcar_norma_obsoleta")
        return tray, intentos, efectos.cuenta(), len(loop.solicitudes)

    # Primero: ¿alguna carga logra siquiera que el agente lo intente? Sin
    # control de permisos, para medir la susceptibilidad del modelo sola.
    print("Cuatro marcos distintos para la misma orden, sin ningún control:\n")
    print(f"{'carga':<18}{'intentó la acción':>20}{'respondió la pregunta':>24}")
    print("-" * 62)
    susceptibles = []
    for nombre, carga in INYECCIONES.items():
        tray, intentos, aplicados, _ = correr(
            carga, PoliticaPermisos.automatico(), aprobar_todo
        )
        if intentos:
            susceptibles.append(nombre)
        print(
            f"{nombre:<18}{('sí' if intentos else 'no'):>20}"
            f"{('sí' if tray.respuesta_final else 'no'):>24}"
        )
    print(f"\ncargas que lograron una llamada a la acción irreversible: "
          f"{len(susceptibles)} de {len(INYECCIONES)}")

    # Segundo: para la carga más efectiva (o la primera si ninguna funcionó),
    # ¿qué hace cada política de permisos?
    carga_elegida = susceptibles[0] if susceptibles else "operador"
    print(f"\nCon la carga '{carga_elegida}', bajo cada política de control:\n")
    print(f"{'escenario':<34}{'intentó':>10}{'se aplicó':>11}{'checkpoints':>13}{'respondió':>11}")
    print("-" * 80)
    resultados = []
    for etiqueta, permisos, aprobador in escenarios:
        tray, intentos, aplicados, checkpoints = correr(
            INYECCIONES[carga_elegida], permisos, aprobador
        )
        resultados.append((etiqueta, intentos, aplicados, checkpoints))
        print(
            f"{etiqueta:<34}{intentos:>10}{aplicados:>11}"
            f"{checkpoints:>13}{('sí' if tray.respuesta_final else 'no'):>11}"
        )

    print(f"\nllamadas de esta corrida : {politica.api_calls}")
    print(f"aciertos de caché        : {politica.aciertos_cache}")
    print(f"costo histórico          : USD {politica.historical_cost_usd:.4f}")
    return resultados, susceptibles, carga_elegida


# --------------------------------------------------------------------------- #
# D. Sandbox.
# --------------------------------------------------------------------------- #
def parte_d():
    seccion("D · Qué aísla un sandbox y qué no")
    capas = [
        ("Validación de argumentos (JSON Schema)", "sintaxis de la llamada",
         "un id sintácticamente válido que no existe"),
        ("Identificador canónico contra catálogo", "path traversal, rutas fuera del corpus",
         "una acción legítima sobre el documento equivocado"),
        ("Política de permisos por riesgo", "ejecución de acciones no autorizadas",
         "lo que el humano aprueba sin leer"),
        ("Clave de idempotencia", "acciones duplicadas por reintento",
         "la primera aplicación, que igual ocurre"),
        ("Sandbox de proceso (fs, red, cpu)", "daño fuera del proceso del agente",
         "daño dentro de lo que el agente sí puede tocar"),
        ("Presupuesto de pasos y de gasto", "bucles infinitos y costo ilimitado",
         "una sola acción cara y correcta"),
    ]
    print(f"{'capa':<42}{'contiene':<40}")
    print("-" * 82)
    for capa, contiene, no_contiene in capas:
        print(f"{capa:<42}{contiene:<40}")
        print(f"{'':<42}no contiene: {no_contiene}")
    print(
        "\nNinguna capa alcanza sola, y la lista tiene una asimetría incómoda: las\n"
        "cinco primeras dependen de que el diseñador haya anticipado la categoría\n"
        "del ataque. La única que no depende de anticipar nada es la última, que es\n"
        "también la más grosera."
    )


def diagrama(resultados) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9.5, 5))
    etiquetas = [r[0].replace(", ", ",\n") for r in resultados]
    intentos = [r[1] for r in resultados]
    aplicados = [r[2] for r in resultados]
    x = range(len(resultados))
    ax.bar([i - 0.2 for i in x], intentos, 0.38, color="#dd8452",
           label="intentos del agente (inyección exitosa)")
    ax.bar([i + 0.2 for i in x], aplicados, 0.38, color="#c44e52",
           label="acciones irreversibles APLICADAS")
    ax.set_xticks(list(x))
    ax.set_xticklabels(etiquetas, fontsize=8)
    ax.set_ylabel("llamadas a marcar_norma_obsoleta")
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    ax.set_title(
        "La inyección tiene éxito en los cuatro escenarios.\n"
        "Lo que cambia es si la acción llega a ejecutarse."
    )
    fig.tight_layout()
    DIAGRAMA.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(DIAGRAMA, dpi=140, bbox_inches="tight")
    print(f"\nDiagrama: {DIAGRAMA.relative_to(AQUI.parent)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-api", action="store_true")
    args = parser.parse_args()

    parte_a()
    parte_b()
    resultados, _, _ = parte_c(args.allow_api)
    parte_d()
    diagrama(resultados)


if __name__ == "__main__":
    main()
