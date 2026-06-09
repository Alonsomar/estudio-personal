"""Sección 2 — El RAG de 02-retrieval expuesto como servicio HTTP.

App FastAPI con dos endpoints útiles y dos de salud:

    POST /query     → corre el RAG y devuelve RAGAnswer estructurado
    GET  /healthz   → liveness: el proceso está vivo
    GET  /readyz    → readiness: el RAG está cargado y listo para servir
    GET  /info      → metadatos del despliegue (corpus, modelo, versión)

Aplica los patrones de §2:

- **Lifespan**: el RAG se construye UNA vez al startup (indexar 234 chunks +
  cargar caché de embeddings) y vive en `app.state`. Cada request reusa el
  índice — el costo de fit() se amortiza sobre todas las queries del proceso.
- **Puertos y adaptadores**: el handler no toca el SDK de OpenAI. Llama
  `rag.query()`. El cliente del LLM es inyectable; si falta OPENAI_API_KEY,
  cae a `StaticLLMClient` para que el servicio siga arrancando (degradado,
  pero arriba).
- **Stateless por request**: `RAGOrchestrator.query()` no muta estado
  compartido. Multi-worker/multi-thread funciona sin sincronización.
- **Response shape estructurado**: el cliente recibe respuesta + fuentes +
  metadata (latencia, costo, trace_id) — auditable y debuggable.

Ejecutar:

    # modo demo (default): arranca in-process, hace 3 queries, imprime.
    uv run python 03-produccion/code/02-fastapi-rag.py

    # modo server real: arranca uvicorn en localhost:8000
    uv run python 03-produccion/code/02-fastapi-rag.py --server

    # cURL desde otra terminal:
    curl -X POST http://localhost:8000/query -H 'content-type: application/json' \\
         -d '{"query": "¿Tasa IVA digital?", "k": 3}' | python -m json.tool
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

# Acceso a shared/ y a 02-retrieval/code para reusar los retrievers ya hechos.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "02-retrieval" / "code"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from prod_lib import (  # noqa: E402
    OpenAILLMClient,
    RAGAnswer,
    RAGOrchestrator,
    StaticLLMClient,
)
from retrieval_lib import (  # noqa: E402
    BM25Retriever,
    DenseRetriever,
    HybridRetriever,
    OpenAIEmbedder,
    load_corpus_chunks,
)
from shared.utils import get_project_root  # noqa: E402

ROOT = get_project_root()
CORPUS_DIR = ROOT / "shared" / "corpus_chileno"
EMB_CACHE = ROOT / "02-retrieval" / "examples" / "cache-embeddings" / "embeddings.npz"

logger = logging.getLogger("rag-service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

# Versión declarada del servicio. Cambia en cada release; sirve para reportar
# en /info y para que el cliente sepa contra qué backend está hablando.
SERVICE_VERSION = "0.1.0"


# --------------------------------------------------------------------------- #
# Lifespan: el costo caro (cargar corpus, fit índices, abrir caché de embeds)
# pasa UNA vez al startup; queda en app.state. Sin esto, el primer request
# del día pagaría 3-5 segundos de latencia.
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Cargar .env al inicio: secrets management formal en §7 reemplazará esto.
    from dotenv import load_dotenv

    load_dotenv()
    logger.info("startup: cargando corpus y construyendo índices…")
    chunks = load_corpus_chunks(CORPUS_DIR)
    embedder = OpenAIEmbedder(cache_path=EMB_CACHE)
    bm25 = BM25Retriever().fit(chunks)
    dense = DenseRetriever(embedder).fit(chunks)
    hybrid = HybridRetriever([bm25, dense], method="rrf", pool=20)

    # LLMClient inyectable. Si no hay API key, degrade graceful con Static.
    if os.environ.get("OPENAI_API_KEY"):
        llm = OpenAILLMClient(default_model="gpt-4o-mini")
        logger.info("LLMClient = OpenAI (gpt-4o-mini)")
    else:
        llm = StaticLLMClient(
            fixed_text="[demo] OPENAI_API_KEY no configurada — respuesta sintética."
        )
        logger.warning("OPENAI_API_KEY no configurada; usando StaticLLMClient.")

    app.state.rag = RAGOrchestrator(retriever=hybrid, llm_client=llm)
    app.state.corpus_chunks = len(chunks)
    app.state.corpus_docs = len({c.doc_id for c in chunks})
    logger.info(
        "ready: %d chunks / %d docs indexados; cliente=%s.",
        app.state.corpus_chunks, app.state.corpus_docs, llm.name,
    )
    yield
    logger.info("shutdown: liberando recursos.")


app = FastAPI(
    title="RAG Fiscal Chileno",
    description=(
        "Servicio HTTP del RAG de la masterclass 02-retrieval. "
        "Demo de los patrones de servicio de 03-produccion §2."
    ),
    version=SERVICE_VERSION,
    lifespan=lifespan,
)


# --------------------------------------------------------------------------- #
# Esquemas de entrada/salida: validados por Pydantic en el borde del sistema.
# El usuario que mande un body inválido recibe 422 con detalle; no llega al
# handler con un dict mal formado.
# --------------------------------------------------------------------------- #
class QueryRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=2000, description="Pregunta en español.")
    k: int = Field(3, ge=1, le=10, description="Cantidad de chunks a recuperar.")
    model: str | None = Field(None, description="Override del modelo (avanzado).")


class SourceOut(BaseModel):
    chunk_id: str
    doc_id: str
    score: float
    snippet: str


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceOut]
    model: str
    in_tokens: int
    out_tokens: int
    latency_ms: float
    cost_usd: float
    trace_id: str
    metadata: dict


def _to_response(a: RAGAnswer) -> QueryResponse:
    return QueryResponse(
        answer=a.answer,
        sources=[SourceOut(**s.as_dict()) for s in a.sources],
        model=a.model,
        in_tokens=a.in_tokens,
        out_tokens=a.out_tokens,
        latency_ms=round(a.latency_ms, 2),
        cost_usd=round(a.cost_usd, 6),
        trace_id=a.trace_id,
        metadata=a.metadata,
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/healthz", tags=["health"])
def healthz() -> dict:
    """Liveness: ¿el proceso responde? Trivial pero crítico para el load
    balancer / orquestador (Fly.io, k8s, etc.): un /healthz que falla saca
    la instancia del pool."""
    return {"status": "ok", "version": SERVICE_VERSION}


@app.get("/readyz", tags=["health"])
def readyz(request: Request) -> dict:
    """Readiness: ¿el RAG está cargado? Distinta de /healthz: el proceso
    puede estar vivo pero todavía cargando el índice. Mientras /readyz no
    devuelva 200, el orquestador no debería rutear tráfico."""
    rag = getattr(request.app.state, "rag", None)
    if rag is None:
        raise HTTPException(503, "RAG no cargado todavía.")
    return {
        "status": "ready",
        "corpus_chunks": request.app.state.corpus_chunks,
        "corpus_docs": request.app.state.corpus_docs,
        "llm_client": rag.llm_client.name,
    }


@app.get("/info", tags=["meta"])
def info(request: Request) -> dict:
    """Metadatos del despliegue: el cliente puede correlacionar el output
    con la versión del servicio. Si más adelante se versionan modelos
    o prompts (§3, §8), van acá."""
    rag = getattr(request.app.state, "rag", None)
    return {
        "service": "rag-fiscal-chileno",
        "version": SERVICE_VERSION,
        "corpus_chunks": getattr(request.app.state, "corpus_chunks", None),
        "corpus_docs": getattr(request.app.state, "corpus_docs", None),
        "llm_client": rag.llm_client.name if rag else None,
        "llm_default_model": getattr(rag.llm_client, "default_model", None) if rag else None,
    }


@app.post("/query", response_model=QueryResponse, tags=["rag"])
def query(req: QueryRequest, request: Request) -> QueryResponse:
    rag: RAGOrchestrator = request.app.state.rag
    answer = rag.query(req.query, k=req.k, model=req.model)
    logger.info(
        "query trace=%s model=%s lat=%dms cost=$%.6f",
        answer.trace_id, answer.model, answer.latency_ms, answer.cost_usd,
    )
    return _to_response(answer)


# --------------------------------------------------------------------------- #
# CLI: dos modos.
# --------------------------------------------------------------------------- #
def _demo() -> None:
    """Arranca el servicio in-process con TestClient y dispara queries.

    Mismo binding que en producción (lifespan corre, app.state se llena),
    sin necesidad de uvicorn ni puerto. Es la forma idiomática de hacer
    smoke tests de un servicio FastAPI.
    """
    from fastapi.testclient import TestClient

    queries = [
        "¿Cuál es la tasa de IVA para servicios digitales de proveedores extranjeros?",
        "¿Cuánto presupuesto se asigna al Programa Nacional de Inmunizaciones en 2024?",
        "¿Cuál es la multa máxima por infracción a la Ley de Lobby?",
    ]
    with TestClient(app) as client:
        print("=" * 72)
        print("readiness check:")
        r = client.get("/readyz")
        print(f"  GET /readyz → {r.status_code}: {r.json()}")
        print("\ninfo:")
        r = client.get("/info")
        print(f"  GET /info → {json.dumps(r.json(), ensure_ascii=False, indent=2)}")

        for i, q in enumerate(queries, 1):
            print("\n" + "=" * 72)
            print(f"[{i}/{len(queries)}] POST /query → {q!r}")
            r = client.post("/query", json={"query": q, "k": 3})
            if r.status_code != 200:
                print(f"  ERROR {r.status_code}: {r.text}")
                continue
            data = r.json()
            print(f"  trace_id : {data['trace_id'][:12]}…")
            print(f"  model    : {data['model']}  (cliente: {data['metadata']['client']})")
            print(f"  latencia : {data['latency_ms']:.0f} ms total  "
                  f"(retrieval {data['metadata']['retrieval_ms']:.0f}ms + "
                  f"LLM {data['metadata']['llm_ms']:.0f}ms)")
            print(f"  tokens   : {data['in_tokens']} in / {data['out_tokens']} out "
                  f"(${data['cost_usd']:.6f})")
            print(f"  answer   : {' '.join(data['answer'].split())[:160]}…")
            print("  sources  :")
            for s in data["sources"]:
                print(f"    - [{s['score']:.3f}] {s['chunk_id']:42s} {s['snippet'][:70]}…")

        # Una mala: validación de Pydantic.
        print("\n" + "=" * 72)
        print("validación de entrada (query demasiado corta):")
        r = client.post("/query", json={"query": "?", "k": 3})
        print(f"  status: {r.status_code}  "
              f"(esperado 422 — Pydantic rechaza min_length=3)")


def _serve(host: str | None = None, port: int | None = None) -> None:
    """Arranca uvicorn programáticamente. No usamos `uvicorn fastapi_rag:app`
    porque el nombre del archivo (`02-fastapi-rag.py`) empieza con dígito y
    no es importable como módulo Python.

    HOST/PORT salen del entorno (default local 127.0.0.1:8000). En el contenedor
    de §7 se setea HOST=0.0.0.0 para escuchar fuera del localhost del container."""
    import uvicorn

    host = host or os.environ.get("HOST", "127.0.0.1")
    port = port or int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    if "--server" in sys.argv:
        _serve()
    else:
        _demo()
