"""prod_lib — núcleo reutilizable de la masterclass 03-produccion.

Acumula los patrones de producción que las secciones introducen:

  §2  LLMClient (Protocol) + adaptadores Anthropic/OpenAI + RAGOrchestrator
       (esta sección).
  §4  LRUCache, ResponseCache, SemanticCache (a futuro).
  §6  TokenBucket, retry_with_backoff, CircuitBreaker (a futuro).
  §8  ModelRouter con shadow / canary / A/B (a futuro).
  §10 CostMeter (a futuro).

Diseño: cada componente es pequeño, sin estado global, testeable
in-process sin mock global. La idea es que la app HTTP (`02-fastapi-rag.py`)
no toque SDKs de proveedores directamente — los toca a través de los
adaptadores que viven aquí.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Tarifas públicas USD por 1M tokens (2026-Q2, aproximadas). Centralizadas
# para que el CostMeter de §10 y el cost en RAGAnswer salgan del mismo lugar.
PRICING_USD_PER_M_TOKENS: dict[str, dict[str, float]] = {
    "gpt-4o-mini":         {"in": 0.150, "out": 0.600},
    "gpt-4o":              {"in": 2.500, "out": 10.000},
    "claude-haiku-4-5":    {"in": 0.800, "out": 4.000},
    "claude-sonnet-4-6":   {"in": 3.000, "out": 15.000},
    "claude-opus-4-7":     {"in": 15.000, "out": 75.000},
}


def estimate_cost_usd(model: str, in_tokens: int, out_tokens: int) -> float:
    """Calcula costo en USD para un par (in, out, modelo). 0 si modelo desconocido."""
    p = PRICING_USD_PER_M_TOKENS.get(model)
    if not p:
        return 0.0
    return (in_tokens * p["in"] + out_tokens * p["out"]) / 1_000_000


# --------------------------------------------------------------------------- #
# Puertos y adaptadores: el handler de FastAPI nunca habla con el SDK del
# proveedor. Habla con un LLMClient. Cambiar de proveedor (o agregar shadow,
# canary, fallback) se vuelve trivial cuando todos los proveedores implementan
# la misma interfaz.
# --------------------------------------------------------------------------- #
@dataclass
class LLMResponse:
    """Respuesta normalizada de cualquier proveedor de LLM."""

    text: str
    in_tokens: int
    out_tokens: int
    latency_ms: float
    model: str
    cost_usd: float = 0.0

    def __post_init__(self) -> None:
        if self.cost_usd == 0.0:
            self.cost_usd = estimate_cost_usd(self.model, self.in_tokens, self.out_tokens)


@runtime_checkable
class LLMClient(Protocol):
    """Puerto: contrato mínimo que cualquier proveedor debe cumplir.

    Deliberadamente angosto. Streaming, function-calling y otras features
    avanzadas se agregan vía métodos opcionales en adaptadores específicos.
    El handler no las conoce; si las necesita, baja un nivel.
    """

    name: str

    def complete(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> LLMResponse: ...


class OpenAILLMClient:
    """Adaptador para clientes compatibles con la Chat Completions API.

    Acepta el client real o un mock para tests. La compatibilidad de OpenAI
    es lo bastante común que este adaptador sirve también para gateways
    locales con la misma interfaz (vLLM, LM Studio, etc.).
    """

    name = "openai"

    def __init__(self, client: Any = None, default_model: str = "gpt-4o-mini") -> None:
        self._client = client  # None: lazy instantiate al primer complete()
        self.default_model = default_model

    def _ensure(self) -> Any:
        if self._client is None:
            from shared.llm_clients import get_openai_client

            self._client = get_openai_client()
        return self._client

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> LLMResponse:
        mdl = model or self.default_model
        t0 = time.perf_counter()
        resp = self._ensure().chat.completions.create(
            model=mdl,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        dt = (time.perf_counter() - t0) * 1000
        return LLMResponse(
            text=(resp.choices[0].message.content or "").strip(),
            in_tokens=resp.usage.prompt_tokens,
            out_tokens=resp.usage.completion_tokens,
            latency_ms=dt,
            model=mdl,
        )


class AnthropicLLMClient:
    """Adaptador para la API de Anthropic (Messages)."""

    name = "anthropic"

    def __init__(self, client: Any = None, default_model: str = "claude-haiku-4-5") -> None:
        self._client = client
        self.default_model = default_model

    def _ensure(self) -> Any:
        if self._client is None:
            from shared.llm_clients import get_anthropic_client

            self._client = get_anthropic_client()
        return self._client

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> LLMResponse:
        mdl = model or self.default_model
        t0 = time.perf_counter()
        resp = self._ensure().messages.create(
            model=mdl,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        dt = (time.perf_counter() - t0) * 1000
        text = resp.content[0].text if resp.content else ""
        return LLMResponse(
            text=text.strip(),
            in_tokens=resp.usage.input_tokens,
            out_tokens=resp.usage.output_tokens,
            latency_ms=dt,
            model=mdl,
        )


class StaticLLMClient:
    """Adaptador de pruebas: devuelve una respuesta fija sin llamar a red.

    Útil para tests, modo demo offline y, en §6, mostrar fallback cuando el
    proveedor real falla. Aquí en §2 lo usamos en el demo si OPENAI_API_KEY
    no está disponible: el sistema sigue arrancando y respondiendo.
    """

    name = "static"
    default_model = "static-0"

    def __init__(self, fixed_text: str = "[respuesta de demo — sin LLM real]") -> None:
        self.fixed_text = fixed_text

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> LLMResponse:
        return LLMResponse(
            text=self.fixed_text,
            in_tokens=len(prompt.split()),
            out_tokens=len(self.fixed_text.split()),
            latency_ms=0.0,
            model=model or self.default_model,
        )


# --------------------------------------------------------------------------- #
# Orquestador: el RAG completo como un objeto invocable. El handler HTTP solo
# tiene que llamar `rag.query(text)` y obtener un RAGAnswer estructurado.
# --------------------------------------------------------------------------- #
DEFAULT_PROMPT_TEMPLATE = (
    "Eres un asistente especializado en normativa fiscal y regulatoria chilena. "
    "Responde la pregunta usando SOLO los fragmentos provistos. Cita los "
    "fragmentos por su número entre corchetes cuando uses información de ellos. "
    "Si la respuesta NO está en los fragmentos, dilo explícitamente "
    "(no inventes).\n\n"
    "FRAGMENTOS:\n{context}\n\n"
    "PREGUNTA: {query}\n\nRESPUESTA:"
)


@dataclass
class SourceCitation:
    """Resumen de un chunk que se entregó como contexto."""

    chunk_id: str
    doc_id: str
    score: float
    snippet: str  # primeros ~200 chars del chunk

    def as_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "score": round(self.score, 4),
            "snippet": self.snippet,
        }


@dataclass
class RAGAnswer:
    """Respuesta estructurada de un RAG completo. Lo que sale al cliente HTTP."""

    answer: str
    sources: list[SourceCitation]
    model: str
    in_tokens: int
    out_tokens: int
    latency_ms: float
    cost_usd: float
    trace_id: str
    metadata: dict = field(default_factory=dict)


class RAGOrchestrator:
    """Combina un retriever + un LLMClient + un template de prompt.

    Stateless dentro de la lifecycle del request: cada query produce un
    trace_id distinto y no comparte estado con otras queries (más allá del
    índice del retriever, que es read-only post-fit). Esto importa para
    correr en multi-thread/multi-worker sin sincronización.
    """

    def __init__(
        self,
        retriever,
        llm_client: LLMClient,
        prompt_template: str = DEFAULT_PROMPT_TEMPLATE,
        default_model: str | None = None,
        k_default: int = 3,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> None:
        self.retriever = retriever
        self.llm_client = llm_client
        self.prompt_template = prompt_template
        self.default_model = default_model
        self.k_default = k_default
        self.temperature = temperature
        self.max_tokens = max_tokens

    @staticmethod
    def _snippet(text: str, n: int = 220) -> str:
        flat = " ".join(text.split())
        return flat if len(flat) <= n else flat[: n - 1] + "…"

    def _build_prompt(self, query: str, chunks: list) -> str:
        ctx = "\n\n".join(
            f"[Fragmento {i + 1}]\n{c.text}" for i, c in enumerate(chunks)
        )
        return self.prompt_template.format(context=ctx, query=query)

    def query(self, query: str, *, k: int | None = None, model: str | None = None) -> RAGAnswer:
        k_eff = k or self.k_default
        t0 = time.perf_counter()
        results = self.retriever.search(query, k=k_eff)
        retr_ms = (time.perf_counter() - t0) * 1000

        chunks = [r.chunk for r in results]
        prompt = self._build_prompt(query, chunks)
        llm_resp = self.llm_client.complete(
            prompt,
            model=model or self.default_model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

        sources = [
            SourceCitation(
                chunk_id=r.chunk.chunk_id,
                doc_id=r.chunk.doc_id,
                score=float(r.score),
                snippet=self._snippet(r.chunk.text),
            )
            for r in results
        ]
        total_ms = (time.perf_counter() - t0) * 1000
        return RAGAnswer(
            answer=llm_resp.text,
            sources=sources,
            model=llm_resp.model,
            in_tokens=llm_resp.in_tokens,
            out_tokens=llm_resp.out_tokens,
            latency_ms=total_ms,
            cost_usd=llm_resp.cost_usd,
            trace_id=uuid.uuid4().hex,
            metadata={
                "retrieval_ms": retr_ms,
                "llm_ms": llm_resp.latency_ms,
                "k": k_eff,
                "client": self.llm_client.name,
            },
        )
