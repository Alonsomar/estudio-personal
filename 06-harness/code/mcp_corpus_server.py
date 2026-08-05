"""Servidor MCP sobre el corpus regulatorio chileno.

Entregable de `06-harness §4` y criterio de aceptación de `B8`. Expone por el
protocolo estándar las capacidades que el repo ya construyó:

  tools      buscar_corpus      · BM25 de `02-retrieval`
             leer_norma         · lectura paginada del corpus real
             vecinos_grafo      · una arista del grafo normativo de `05`
             alcance_normativo  · dependencia transitiva acotada (`06 §3`)
  resources  corpus://{doc_id}  · el texto de cada norma, direccionable
             corpus://indice    · el catálogo, para descubrir sin adivinar
  prompts    auditar_dependencias · plantilla de la consulta más frecuente
                                    del dominio, versionada del lado del servidor

Decisión de diseño: **no expone `responder`**. Esa herramienta existe en
`harness_lib` porque el bucle necesita una señal de terminación, pero es una
preocupación del harness, no del corpus. Un servidor MCP publica capacidades
sobre un dominio; el control de flujo del agente se queda del lado del
cliente. Mezclarlos es el error que convierte un servidor reutilizable en el
backend de un solo agente.

Uso como servidor stdio (lo que configura un cliente MCP real):

    uv run python 06-harness/code/mcp_corpus_server.py

Configuración equivalente en un cliente:

    {
      "mcpServers": {
        "corpus-chileno": {
          "command": "uv",
          "args": ["run", "python", "06-harness/code/mcp_corpus_server.py"],
          "cwd": "/ruta/a/estudio-personal"
        }
      }
    }
"""

from __future__ import annotations

import sys
from difflib import get_close_matches
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server import MCPServer  # noqa: E402

from harness_lib import (  # noqa: E402
    CARACTERES_POR_PAGINA,
    CORPUS_DIR,
    alcance_acotado,
    cargar_grafo_normativo,
)
from ontology_lib import TipoRelacion  # noqa: E402
from retrieval_lib import BM25Retriever, load_corpus_chunks  # noqa: E402

INSTRUCCIONES = """Corpus de normativa y presupuesto público chileno: 40 documentos
(leyes, decretos, circulares del SII, glosas presupuestarias, dictámenes de
Contraloría, oficios) más un grafo normativo auditado de 38 normas y 69 relaciones
con cita literal de respaldo.

Los identificadores de documento son nombres de archivo (por ejemplo
'ley-01-dl-825-iva-base.txt'). Usá 'buscar_corpus' o el recurso corpus://indice
para descubrirlos; no los construyas a mano.

Este corpus es sintético y está construido para estudio: no lo uses como fuente
jurídica."""


def construir_servidor() -> MCPServer:
    """Arma el servidor. Es una función y no un módulo con estado global para
    que los tests puedan levantar instancias limpias en memoria."""
    servidor = MCPServer(
        name="corpus-normativo-chileno",
        title="Corpus normativo y presupuestario chileno",
        version="1.0.0",
        instructions=INSTRUCCIONES,
    )

    # El índice se construye una vez, al arrancar el proceso. Un servidor MCP
    # es de larga vida: pagar el índice por llamada sería el error obvio.
    chunks = load_corpus_chunks(CORPUS_DIR)
    bm25 = BM25Retriever().fit(chunks)
    grafo = cargar_grafo_normativo()
    documentos = sorted(p.name for p in CORPUS_DIR.glob("*.txt"))
    tipos_validos = sorted(t.value for t in TipoRelacion)

    def _sugerir(doc_id: str, universo: list[str]) -> str:
        cercanos = get_close_matches(doc_id, universo, n=3, cutoff=0.4)
        return (
            f" Documentos parecidos: {', '.join(cercanos)}."
            if cercanos
            else " Usá 'buscar_corpus' o el recurso corpus://indice para ubicarlo."
        )

    @servidor.tool(
        description=(
            "Busca fragmentos relevantes en el corpus normativo chileno por "
            "palabras clave (BM25). Devuelve hasta k fragmentos, cada uno con su "
            "identificador en formato 'archivo.txt#n'."
        )
    )
    def buscar_corpus(consulta: str, k: int = 3) -> str:
        resultados = bm25.search(consulta, k=max(1, min(k, 10)))
        if not resultados:
            return "Sin resultados para esa consulta."
        return "\n\n".join(
            f"[{chunks[r.index].chunk_id}] (score {r.score:.2f})\n"
            f"{chunks[r.index].text.strip()}"
            for r in resultados
        )

    @servidor.tool(
        description=(
            "Lee una página del texto completo de un documento del corpus. Cada "
            f"página son {CARACTERES_POR_PAGINA} caracteres. La respuesta indica "
            "qué página es y cuántas hay en total."
        )
    )
    def leer_norma(doc_id: str, pagina: int = 1) -> str:
        ruta = CORPUS_DIR / doc_id
        # El id canónico es el nombre de archivo, y esto además cierra el
        # path traversal: cualquier cosa fuera del catálogo no existe.
        if doc_id not in documentos or not ruta.exists():
            return (
                f"ERROR: '{doc_id}' no es un documento del corpus."
                + _sugerir(doc_id, documentos)
            )
        texto = ruta.read_text(encoding="utf-8")
        total = max(1, -(-len(texto) // CARACTERES_POR_PAGINA))
        if not 1 <= pagina <= total:
            return (
                f"ERROR: página {pagina} fuera de rango para '{doc_id}'. "
                f"Este documento tiene {total} página(s). Volvé a llamar con "
                f"pagina entre 1 y {total}."
            )
        inicio = (pagina - 1) * CARACTERES_POR_PAGINA
        return (
            f"[{doc_id} — página {pagina} de {total}]\n"
            + texto[inicio : inicio + CARACTERES_POR_PAGINA]
        )

    @servidor.tool(
        description=(
            "Devuelve las normas relacionadas con un documento por un tipo de "
            f"relación, en un salto. tipo_relacion ∈ {tipos_validos}. "
            "direccion='out' responde '¿a qué normas afecta este documento?'; "
            "'in', '¿qué normas afectan a este documento?'. Cada arista viene "
            "con la cita literal que la sustenta."
        )
    )
    def vecinos_grafo(
        doc_id: str, tipo_relacion: str, direccion: Literal["in", "out"] = "out"
    ) -> str:
        if doc_id not in grafo:
            return (
                f"ERROR: '{doc_id}' no está en el grafo normativo."
                + _sugerir(doc_id, sorted(grafo.nodes))
            )
        if tipo_relacion not in tipos_validos:
            return (
                f"ERROR: tipo_relacion '{tipo_relacion}' inválido. "
                f"Valores admitidos: {', '.join(tipos_validos)}."
            )
        aristas = (
            grafo.out_edges(doc_id, data=True)
            if direccion == "out"
            else grafo.in_edges(doc_id, data=True)
        )
        salida = [
            (v if direccion == "out" else u, data.get("fundamento", ""))
            for u, v, data in aristas
            if data.get("tipo") == tipo_relacion
        ]
        if not salida:
            return (
                f"Sin relaciones '{tipo_relacion}' con dirección '{direccion}' "
                f"para {doc_id}."
            )
        lineas = []
        for otro, fundamento in salida:
            arista = (
                f"{doc_id} --{tipo_relacion}--> {otro}"
                if direccion == "out"
                else f"{otro} --{tipo_relacion}--> {doc_id}"
            )
            lineas.append(arista + (f"\n    fundamento: «{fundamento}»" if fundamento else ""))
        return "\n".join(lineas)

    @servidor.tool(
        description=(
            "Devuelve de una sola llamada todos los documentos conectados a uno "
            "dado en hasta max_saltos saltos, por cualquier tipo de relación. "
            "direccion='in' responde '¿qué documentos dependen de este?' — la "
            "consulta de auditoría normativa más frecuente. Usalo en vez de "
            "encadenar llamadas a 'vecinos_grafo'."
        )
    )
    def alcance_normativo(
        doc_id: str, max_saltos: int = 2, direccion: Literal["in", "out"] = "in"
    ) -> str:
        if doc_id not in grafo:
            return (
                f"ERROR: '{doc_id}' no está en el grafo normativo."
                + _sugerir(doc_id, sorted(grafo.nodes))
            )
        if not 1 <= max_saltos <= 5:
            return f"ERROR: max_saltos debe estar entre 1 y 5, recibido {max_saltos}."
        alcanzados = alcance_acotado(grafo, doc_id, max_saltos, direccion)
        if not alcanzados:
            return f"Ningún documento conectado a {doc_id} en {max_saltos} saltos."
        encabezado = (
            f"{len(alcanzados)} documentos dependen de {doc_id}"
            if direccion == "in"
            else f"{doc_id} alcanza {len(alcanzados)} documentos"
        )
        return (
            f"{encabezado} en hasta {max_saltos} saltos:\n"
            + "\n".join(f"- {d}" for d in alcanzados)
        )

    # --- Resources -------------------------------------------------------- #
    # La distinción tool/resource no es cosmética: un resource es dato que el
    # cliente puede leer y cachear por su cuenta, sin gastar un turno del
    # modelo. Publicar el catálogo como resource en vez de como tool le ahorra
    # al agente el paso de preguntarlo.
    @servidor.resource(
        "corpus://indice",
        name="indice",
        title="Catálogo de documentos del corpus",
        description="Lista de los identificadores canónicos disponibles.",
        mime_type="text/plain",
    )
    def indice() -> str:
        return "\n".join(documentos)

    @servidor.resource(
        "corpus://{doc_id}",
        name="documento",
        title="Texto de una norma del corpus",
        description="Texto completo de un documento, por identificador canónico.",
        mime_type="text/plain",
    )
    def documento(doc_id: str) -> str:
        if doc_id not in documentos:
            raise ValueError(f"'{doc_id}' no es un documento del corpus")
        return (CORPUS_DIR / doc_id).read_text(encoding="utf-8")

    # --- Prompts ---------------------------------------------------------- #
    @servidor.prompt(
        name="auditar_dependencias",
        title="Auditar dependencias normativas",
        description=(
            "Plantilla para la consulta más frecuente del dominio: si una norma "
            "cambia, qué otros documentos quedan potencialmente desactualizados."
        ),
    )
    def auditar_dependencias(doc_id: str, max_saltos: str = "2") -> str:
        # Los argumentos de un prompt son strings por el protocolo: a
        # diferencia de las tools, no llevan JSON Schema. Es un contrato más
        # débil a propósito — un prompt es una plantilla de texto, no una
        # llamada tipada — y hay que validarlo a mano.
        try:
            saltos = max(1, min(int(max_saltos), 5))
        except ValueError:
            saltos = 2
        max_saltos = str(saltos)
        return (
            f"Si cambiara {doc_id}, ¿qué documentos del corpus quedarían "
            f"potencialmente desactualizados?\n\n"
            f"Usá 'alcance_normativo' con direccion='in' y max_saltos={max_saltos} "
            "para obtener el conjunto, y después 'vecinos_grafo' sobre los casos "
            "dudosos para ver la cita literal que sustenta cada dependencia. "
            "Respondé con la lista de documentos y el fundamento de cada uno."
        )

    return servidor


def main() -> None:
    construir_servidor().run(transport="stdio")


if __name__ == "__main__":
    main()
