"""§4 — MCP como estándar: el servidor del corpus, ejercitado de verdad.

Produce los números de `theory/04-mcp.md`. No llama a ningún modelo: todo lo
que hace es hablar el protocolo contra
[`mcp_corpus_server.py`](mcp_corpus_server.py) y medir lo que sale.

  A. Descubrimiento: qué publica el servidor (tools, resources, prompts) y
     qué dice de sí mismo.
  B. Las cuatro herramientas ejercitadas contra el corpus real, incluidos
     los caminos de error.
  C. Resources y prompts, las dos primitivas que se ignoran y que cambian
     el reparto de trabajo entre cliente y servidor.
  D. Economía del estándar: N×M contra N+M, y el peaje por iteración que
     §3 midió, aplicado al tamaño del menú de este servidor.

    uv run python 06-harness/code/04-mcp.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp import Client  # noqa: E402
from mcp.types import LATEST_PROTOCOL_VERSION  # noqa: E402

from harness_lib import contar_tokens  # noqa: E402
from mcp_corpus_server import construir_servidor  # noqa: E402

from shared.utils import get_logger  # noqa: E402

log = get_logger(__name__)
AQUI = Path(__file__).resolve().parent.parent
DIAGRAMA = AQUI / "diagrams" / "mcp-n-por-m.png"


def seccion(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def texto_de(resultado) -> str:
    """Extrae el texto de un `CallToolResult`, que trae bloques de contenido."""
    partes = []
    for bloque in resultado.content:
        partes.append(getattr(bloque, "text", "") or "")
    return "\n".join(p for p in partes if p)


async def demo() -> dict[str, int]:
    servidor = construir_servidor()

    # Transporte en memoria: el mismo protocolo, sin proceso ni socket. Es lo
    # que permite testear un servidor MCP sin red (regla del repo) y lo que
    # hace de este script un test de integración de verdad.
    async with Client(servidor, raise_exceptions=True) as cliente:
        seccion("A · Descubrimiento")
        info = cliente.server_info
        print(f"servidor            : {info.name} v{info.version}")
        print(f"versión de protocolo: {cliente.protocol_version}")
        print(f"SDK compilado contra: {LATEST_PROTOCOL_VERSION}")

        tools = (await cliente.list_tools()).tools
        recursos = (await cliente.list_resources()).resources
        plantillas = (await cliente.list_resource_templates()).resource_templates
        prompts = (await cliente.list_prompts()).prompts
        print(
            f"\npublica: {len(tools)} tools, {len(recursos)} resources, "
            f"{len(plantillas)} plantillas de resource, {len(prompts)} prompts"
        )
        print()
        costos = {}
        for t in tools:
            costos[t.name] = contar_tokens(
                json.dumps(
                    {"name": t.name, "description": t.description,
                     "parameters": t.input_schema},
                    ensure_ascii=False,
                )
            )
            params = ", ".join(t.input_schema.get("properties", {}))
            print(f"  {t.name:<20} ({params})")

        seccion("B · Las cuatro herramientas contra el corpus real")
        casos = [
            ("buscar_corpus", {"consulta": "IVA servicios digitales", "k": 2}),
            ("leer_norma", {"doc_id": "ley-01-dl-825-iva-base.txt", "pagina": 1}),
            ("vecinos_grafo", {"doc_id": "ley-01-dl-825-iva-base.txt",
                               "tipo_relacion": "modifica", "direccion": "in"}),
            ("alcance_normativo", {"doc_id": "decreto-03-reglamento-compras-publicas.txt",
                                   "max_saltos": 2, "direccion": "in"}),
        ]
        for nombre, args in casos:
            salida = texto_de(await cliente.call_tool(nombre, args))
            print(f"\n--- {nombre}({json.dumps(args, ensure_ascii=False)})")
            recorte = salida if len(salida) <= 320 else salida[:320] + " […]"
            print("\n".join("    " + ln for ln in recorte.splitlines()))

        seccion("B · Los caminos de error, que son parte del contrato")
        errores = [
            ("leer_norma", {"doc_id": "ds-250"}),
            ("leer_norma", {"doc_id": "ley-01-dl-825-iva-base.txt", "pagina": 99}),
            ("vecinos_grafo", {"doc_id": "ley-01-dl-825-iva-base.txt",
                               "tipo_relacion": "invalida"}),
            ("leer_norma", {"doc_id": "../../../etc/passwd"}),
        ]
        for nombre, args in errores:
            salida = texto_de(await cliente.call_tool(nombre, args))
            print(f"\n--- {nombre}({json.dumps(args, ensure_ascii=False)})")
            print("    " + salida.splitlines()[0][:200])

        seccion("C · Resources y prompts")
        indice = await cliente.read_resource("corpus://indice")
        lineas = texto_de_recurso(indice).splitlines()
        print(f"corpus://indice          → {len(lineas)} identificadores")
        print(f"                           primeros: {', '.join(lineas[:3])}")

        doc = await cliente.read_resource("corpus://ley-01-dl-825-iva-base.txt")
        cuerpo = texto_de_recurso(doc)
        print(f"corpus://{{doc_id}}         → {len(cuerpo):,} caracteres del texto real")

        plantilla = await cliente.get_prompt(
            "auditar_dependencias",
            {"doc_id": "ley-03-ley-19886-compras-publicas.txt", "max_saltos": "2"},
        )
        mensaje = plantilla.messages[0].content
        print("\nprompt 'auditar_dependencias' renderizado del lado del servidor:")
        print("\n".join("    " + ln for ln in getattr(mensaje, "text", "").splitlines()))

        return costos


def texto_de_recurso(resultado) -> str:
    partes = []
    for contenido in resultado.contents:
        partes.append(getattr(contenido, "text", "") or "")
    return "\n".join(p for p in partes if p)


def parte_d(costos: dict[str, int]) -> None:
    seccion("D · La economía del estándar")
    clientes, fuentes = 5, 6
    print(
        "Sin protocolo, conectar N clientes a M fuentes son N×M integraciones,\n"
        "cada una con su autenticación, su formato y su mantenimiento.\n"
    )
    print(f"{'N clientes':>12}{'M fuentes':>12}{'sin protocolo':>16}{'con protocolo':>16}{'razón':>10}")
    print("-" * 66)
    for n, m in ((2, 2), (3, 4), (clientes, fuentes), (10, 20)):
        print(f"{n:>12}{m:>12}{n * m:>16}{n + m:>16}{n * m / (n + m):>10.1f}×")

    seccion("D · Pero el menú se paga en cada iteración")
    total = sum(costos.values())
    print(f"{'herramienta':<22}{'tokens de esquema':>20}")
    print("-" * 42)
    for nombre, costo in sorted(costos.items(), key=lambda kv: -kv[1]):
        print(f"{nombre:<22}{costo:>20,}")
    print(f"{'TOTAL':<22}{total:>20,}")
    print(
        f"\nEste servidor publica 4 herramientas por {total:,} tokens. Con el "
        f"multiplicador\nde reenvío de §2 (3,32 iteraciones por tarea), son "
        f"~{total * 3.32:,.0f} tokens por tarea solo\nen tener el menú a la vista.\n"
        f"\nUn servidor de 40 herramientas del mismo tamaño promedio costaría "
        f"~{total / len(costos) * 40:,.0f}\ntokens de prefijo — más que todo el "
        "contexto de una tarea típica de este módulo,\nantes de que el agente "
        "haga nada."
    )


def diagrama(costos: dict[str, int]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4.8))

    ns = list(range(1, 13))
    m = 6
    ax1.plot(ns, [n * m for n in ns], "o-", color="#c44e52",
             label=f"sin protocolo: N×{m} integraciones")
    ax1.plot(ns, [n + m for n in ns], "s-", color="#55a868",
             label=f"con protocolo: N+{m} implementaciones")
    ax1.set_xlabel("clientes que consumen el corpus (N)")
    ax1.set_ylabel("piezas a construir y mantener")
    ax1.legend(fontsize=9)
    ax1.grid(alpha=0.3)
    ax1.set_title(f"El argumento del estándar\n(M = {m} fuentes de datos)")

    n_tools = list(range(1, 41))
    medio = sum(costos.values()) / len(costos)
    ax2.plot(n_tools, [k * medio for k in n_tools], color="#4c72b0",
             label="prefijo por iteración")
    ax2.plot(n_tools, [k * medio * 3.32 for k in n_tools], color="#dd8452",
             label="costo por tarea (×3,32 de §2)")
    ax2.axvline(len(costos), color="#55a868", ls="--", lw=1)
    ax2.annotate(f"este servidor\n({len(costos)} tools)", (len(costos), medio * 40 * 0.55),
                 textcoords="offset points", xytext=(8, 0), fontsize=8, color="#55a868")
    ax2.set_xlabel("herramientas publicadas por el servidor")
    ax2.set_ylabel("tokens")
    ax2.legend(fontsize=9)
    ax2.grid(alpha=0.3)
    ax2.set_title("El contraargumento\n(el menú viaja en cada llamada)")

    fig.tight_layout()
    DIAGRAMA.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(DIAGRAMA, dpi=140, bbox_inches="tight")
    print(f"\nDiagrama: {DIAGRAMA.relative_to(AQUI.parent)}")


def main() -> None:
    costos = asyncio.run(demo())
    parte_d(costos)
    diagrama(costos)


if __name__ == "__main__":
    main()
