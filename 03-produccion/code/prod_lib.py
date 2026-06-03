"""prod_lib — núcleo reutilizable de la masterclass 03-produccion.

Acumula los patrones de producción que las secciones introducen:

  §2  LLMClient (Protocol) + adaptadores Anthropic/OpenAI + RAGOrchestrator.
  §3  PromptTemplate + PromptRegistry + render_safe (esta sección).
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

import hashlib
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
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
# §3 Gestión de prompts: el prompt es código. Va versionado, con hash de
# contenido, validado al cargar y renderizado de forma SEGURA contra inyección
# desde el corpus (un chunk que contiene metacaracteres de template no debe
# poder alterar la plantilla).
#
# Decisión de templating: un renderizador propio de una sola pasada con
# placeholders estilo `{{ var }}`. Se eligió por encima de:
#   - str.format(): frágil — un chunk con un `{` crudo lanza ValueError, y un
#     `{query}` dentro del chunk se re-sustituye (doble inyección).
#   - string.Template ($var): choca con los `$` de montos en pesos del dominio
#     fiscal ("$1.000.000" rompería el parseo).
# El renderizador de una pasada inserta los valores LITERALMENTE: lo que venga
# en `context` nunca se re-evalúa como template. Esa es toda la defensa.
# --------------------------------------------------------------------------- #
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class PromptError(ValueError):
    """Prompt inválido: faltan variables requeridas, versión mal formada, etc."""


def render_safe(body: str, values: dict[str, str]) -> str:
    """Renderiza `body` sustituyendo `{{ var }}` por values[var], en UNA pasada.

    Clave de seguridad: `re.sub` con función reemplaza cada match llamando a
    `repl` una vez; el string devuelto se inserta tal cual y NO se vuelve a
    escanear. Por eso un valor que contenga `{{ query }}` o `{` queda literal
    en la salida — el corpus no puede inyectar instrucciones en la plantilla.
    """

    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise PromptError(f"variable sin valor al renderizar: {{{{ {key} }}}}")
        return str(values[key])

    return _PLACEHOLDER.sub(repl, body)


@dataclass(frozen=True)
class PromptTemplate:
    """Un prompt versionado e inmutable.

    Identidad = (name, version, content_hash). El hash sobre el cuerpo permite
    detectar cambios fuera de banda: si alguien edita el prompt en producción
    sin subir versión, el hash cambia y los logs lo delatan.
    """

    name: str
    version: str
    body: str
    required_vars: tuple[str, ...] = ("context", "query")
    description: str = ""

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()[:12]

    @property
    def ref(self) -> str:
        """Referencia citable que viaja en logs y en la respuesta del RAG."""
        return f"{self.name}@{self.version}#{self.content_hash}"

    def declared_vars(self) -> set[str]:
        return set(_PLACEHOLDER.findall(self.body))

    def validate(self) -> None:
        """Test obligatorio que corre al registrar: variables requeridas presentes."""
        missing = set(self.required_vars) - self.declared_vars()
        if missing:
            raise PromptError(
                f"prompt {self.name}@{self.version} no declara {sorted(missing)}; "
                f"declara {sorted(self.declared_vars())}"
            )

    def render(self, **values: str) -> str:
        return render_safe(self.body, values)


def _version_key(version: str) -> tuple[int, str]:
    """Ordena 'v2' > 'v10' correctamente (numérico cuando se puede)."""
    m = re.fullmatch(r"v(\d+)", version)
    return (int(m.group(1)), version) if m else (-1, version)


class PromptRegistry:
    """Carga prompts versionados desde un directorio y los sirve por nombre.

    Convención de archivos: `<name>.<version>.txt`, p. ej. `rag-fiscal.v2.txt`.
    El cuerpo del archivo ES el prompt (el hash se calcula sobre él, sin
    metadata, para que sea estable). Validar al cargar es la política de
    "tests obligatorios": un prompt inválido jamás entra al registry.
    """

    _FILE = re.compile(r"^(?P<name>[a-z0-9-]+)\.(?P<version>v\d+)\.txt$")

    def __init__(
        self,
        root: str | Path,
        required_vars: tuple[str, ...] = ("context", "query"),
    ) -> None:
        self.root = Path(root)
        self.required_vars = required_vars
        self._by_name: dict[str, dict[str, PromptTemplate]] = {}
        self._load()

    def _load(self) -> None:
        if not self.root.is_dir():
            raise FileNotFoundError(f"directorio de prompts inexistente: {self.root}")
        for f in sorted(self.root.glob("*.txt")):
            m = self._FILE.match(f.name)
            if not m:
                continue  # archivos que no siguen la convención se ignoran
            tmpl = PromptTemplate(
                name=m["name"],
                version=m["version"],
                body=f.read_text(encoding="utf-8"),
                required_vars=self.required_vars,
            )
            tmpl.validate()  # rechaza prompts inválidos AL CARGAR, no en runtime
            self._by_name.setdefault(tmpl.name, {})[tmpl.version] = tmpl

    def names(self) -> list[str]:
        return sorted(self._by_name)

    def versions(self, name: str) -> list[str]:
        return sorted(self._by_name.get(name, {}), key=_version_key)

    def get(self, name: str, version: str | None = None) -> PromptTemplate:
        versions = self._by_name.get(name)
        if not versions:
            raise PromptError(f"prompt desconocido: {name!r} (hay: {self.names()})")
        if version is None:
            version = self.versions(name)[-1]  # latest
        if version not in versions:
            raise PromptError(
                f"{name} no tiene versión {version} (hay: {self.versions(name)})"
            )
        return versions[version]


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
        prompt_template: str | PromptTemplate = DEFAULT_PROMPT_TEMPLATE,
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
        # §3: si es un PromptTemplate, render seguro ({{ }}, una pasada). Si es
        # un str legado (§2), str.format. El template versionado es lo correcto
        # en prod: el chunk no puede inyectar instrucciones en la plantilla.
        if isinstance(self.prompt_template, PromptTemplate):
            return self.prompt_template.render(context=ctx, query=query)
        return self.prompt_template.format(context=ctx, query=query)

    @property
    def prompt_ref(self) -> str | None:
        """Referencia del prompt versionado (None si se usa el str legado)."""
        if isinstance(self.prompt_template, PromptTemplate):
            return self.prompt_template.ref
        return None

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
                "prompt_ref": self.prompt_ref,  # None con el template legado
            },
        )
