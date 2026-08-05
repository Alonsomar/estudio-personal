"""Tests de integración del servidor MCP del corpus chileno (06-harness §4).

Corren el protocolo de verdad —`Client` contra `MCPServer`— sobre transporte
en memoria: sin proceso hijo, sin socket y sin red, que es lo que exige el
repo. No son mocks: si el servidor rompe el contrato del protocolo, esto
falla.
"""

from __future__ import annotations

import pytest

from mcp import Client

from mcp_corpus_server import construir_servidor

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _texto(resultado) -> str:
    return "\n".join(
        getattr(b, "text", "") or "" for b in resultado.content
    )


def _texto_recurso(resultado) -> str:
    return "\n".join(
        getattr(c, "text", "") or "" for c in resultado.contents
    )


async def test_el_servidor_se_presenta_y_negocia_protocolo():
    async with Client(construir_servidor()) as cliente:
        assert cliente.server_info.name == "corpus-normativo-chileno"
        assert cliente.protocol_version


async def test_publica_las_cuatro_herramientas_del_corpus():
    async with Client(construir_servidor()) as cliente:
        nombres = {t.name for t in (await cliente.list_tools()).tools}
    assert nombres == {
        "buscar_corpus",
        "leer_norma",
        "vecinos_grafo",
        "alcance_normativo",
    }


async def test_no_publica_el_control_de_flujo_del_agente():
    """`responder` es una preocupación del harness, no del corpus. Un
    servidor que la exponga deja de ser reutilizable por otros clientes."""
    async with Client(construir_servidor()) as cliente:
        nombres = {t.name for t in (await cliente.list_tools()).tools}
    assert "responder" not in nombres


async def test_los_esquemas_llegan_al_cliente():
    async with Client(construir_servidor()) as cliente:
        tools = {t.name: t for t in (await cliente.list_tools()).tools}
    props = tools["alcance_normativo"].input_schema["properties"]
    assert set(props) >= {"doc_id", "max_saltos", "direccion"}
    assert tools["alcance_normativo"].description


async def test_buscar_devuelve_fragmentos_con_identificador():
    async with Client(construir_servidor()) as cliente:
        salida = _texto(
            await cliente.call_tool(
                "buscar_corpus", {"consulta": "IVA servicios digitales", "k": 2}
            )
        )
    assert "circular-01-sii-iva-digital.txt#" in salida


async def test_leer_norma_pagina_y_declara_el_total():
    async with Client(construir_servidor()) as cliente:
        salida = _texto(
            await cliente.call_tool(
                "leer_norma", {"doc_id": "ley-01-dl-825-iva-base.txt", "pagina": 1}
            )
        )
    assert "página 1 de" in salida
    assert "DECRETO LEY" in salida


async def test_vecinos_grafo_incluye_el_fundamento_literal():
    async with Client(construir_servidor()) as cliente:
        salida = _texto(
            await cliente.call_tool(
                "vecinos_grafo",
                {"doc_id": "ley-01-dl-825-iva-base.txt",
                 "tipo_relacion": "modifica", "direccion": "in"},
            )
        )
    assert "ley-02-ley-21210-modernizacion.txt" in salida
    assert "fundamento" in salida


async def test_alcance_normativo_responde_la_dependencia_transitiva():
    async with Client(construir_servidor()) as cliente:
        salida = _texto(
            await cliente.call_tool(
                "alcance_normativo",
                {"doc_id": "decreto-03-reglamento-compras-publicas.txt",
                 "max_saltos": 2, "direccion": "in"},
            )
        )
    for esperado in (
        "do-02-extracto-licitacion-publica.txt",
        "glosa-05-presupuesto-interior.txt",
        "oficio-02-contraloria-trato-directo.txt",
        "resolucion-01-chilecompra-compra-agil.txt",
    ):
        assert esperado in salida


@pytest.mark.parametrize(
    "nombre,args,fragmento",
    [
        ("leer_norma", {"doc_id": "ds-250"}, "no es un documento"),
        (
            "leer_norma",
            {"doc_id": "ley-01-dl-825-iva-base.txt", "pagina": 99},
            "fuera de rango",
        ),
        (
            "vecinos_grafo",
            {"doc_id": "ley-01-dl-825-iva-base.txt", "tipo_relacion": "invalida"},
            "Valores admitidos",
        ),
        (
            "alcance_normativo",
            {"doc_id": "ley-01-dl-825-iva-base.txt", "max_saltos": 99},
            "entre 1 y 5",
        ),
    ],
)
async def test_los_errores_dicen_que_hacer(nombre, args, fragmento):
    """Mismo contrato de error que §3: el fallo es un canal de enseñanza,
    también cuando viaja por el protocolo."""
    async with Client(construir_servidor()) as cliente:
        salida = _texto(await cliente.call_tool(nombre, args))
    assert fragmento in salida


@pytest.mark.parametrize(
    "doc_id",
    ["../../../etc/passwd", "/etc/passwd", "../pyproject.toml", "..%2f..%2fetc%2fpasswd"],
)
async def test_no_se_puede_salir_del_corpus(doc_id):
    """El identificador canónico se valida contra el catálogo, no contra el
    sistema de archivos: cualquier ruta que no esté en el catálogo no existe."""
    async with Client(construir_servidor()) as cliente:
        salida = _texto(await cliente.call_tool("leer_norma", {"doc_id": doc_id}))
    assert "ERROR" in salida
    assert "root:" not in salida


async def test_el_indice_es_un_resource_no_una_tool():
    """Descubrir el catálogo no debería gastar un turno del modelo."""
    async with Client(construir_servidor()) as cliente:
        uris = {str(r.uri) for r in (await cliente.list_resources()).resources}
        assert "corpus://indice" in uris
        lineas = _texto_recurso(
            await cliente.read_resource("corpus://indice")
        ).splitlines()
    assert len(lineas) >= 40
    assert all(ln.endswith(".txt") for ln in lineas)


async def test_plantilla_de_resource_sirve_documentos_reales():
    async with Client(construir_servidor()) as cliente:
        plantillas = (await cliente.list_resource_templates()).resource_templates
        assert any("{doc_id}" in str(p.uri_template) for p in plantillas)
        cuerpo = _texto_recurso(
            await cliente.read_resource("corpus://ley-01-dl-825-iva-base.txt")
        )
    assert "DECRETO LEY" in cuerpo and len(cuerpo) > 1_000


async def test_el_prompt_se_renderiza_del_lado_del_servidor():
    async with Client(construir_servidor()) as cliente:
        nombres = {p.name for p in (await cliente.list_prompts()).prompts}
        assert "auditar_dependencias" in nombres
        # Los argumentos de prompt son strings por protocolo, no llevan esquema.
        resultado = await cliente.get_prompt(
            "auditar_dependencias",
            {"doc_id": "ley-03-ley-19886-compras-publicas.txt", "max_saltos": "2"},
        )
    texto = getattr(resultado.messages[0].content, "text", "")
    assert "ley-03-ley-19886-compras-publicas.txt" in texto
    assert "alcance_normativo" in texto
