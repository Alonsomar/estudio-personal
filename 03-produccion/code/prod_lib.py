"""prod_lib — núcleo reutilizable de la masterclass 03-produccion.

Acumula los patrones de producción que las secciones introducen:

  §2  LLMClient (Protocol) + adaptadores Anthropic/OpenAI + RAGOrchestrator.
  §3  PromptTemplate + PromptRegistry + render_safe.
  §4  LRUCache, ResponseCache, SemanticCache.
  §5  StructuredLogger + MetricsRegistry + Tracer/Span.
  §6  TokenBucket, retry_with_backoff, CircuitBreaker + wrappers LLMClient
       (RateLimited / Retrying / CircuitBreaking / Fallback).
  §7  ServiceSettings + scan_for_secrets / redact_secrets (esta sección).
  §8  ModelRouter con shadow / canary / A/B (a futuro).
  §10 CostMeter (a futuro).

Diseño: cada componente es pequeño, sin estado global, testeable
in-process sin mock global. La idea es que la app HTTP (`02-fastapi-rag.py`)
no toque SDKs de proveedores directamente — los toca a través de los
adaptadores que viven aquí.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import random
import re
import sys
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol, runtime_checkable

import numpy as np
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    from_cache: bool = False  # §4: True si salió de un cache (no se pagó la API)

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
# §4 Caching multinivel. Tres niveles cubren casi todo el valor:
#   1. Embedding cache  → ya vive en OpenAIEmbedder (02-retrieval).
#   2. Response cache   → exacto: hash(prompt+modelo+temp) → respuesta. Acá.
#   3. Semantic cache   → por similitud de la query; atrapa paráfrasis. Acá.
# El LRU con TTL es la primitiva debajo de los tres.
# --------------------------------------------------------------------------- #
class LRUCache:
    """Cache LRU con TTL opcional, desde cero y thread-safe.

    `OrderedDict` da el orden de uso: `move_to_end` marca "recién usado",
    `popitem(last=False)` desaloja el menos usado. El TTL se guarda por entrada
    como timestamp de expiración y se chequea perezosamente en `get()` (no hay
    hilo de limpieza; una entrada vencida se descarta cuando alguien la pide).

    Los valores cacheados se asumen no-None (en este proyecto, LLMResponse o
    RAGAnswer); por eso `get` usa None como "miss", sin sentinela.
    """

    def __init__(self, maxsize: int = 1024, ttl_s: float | None = None) -> None:
        self.maxsize = maxsize
        self.ttl_s = ttl_s
        self._data: OrderedDict[str, tuple[Any, float | None]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.expirations = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            value, expiry = item
            if expiry is not None and time.time() > expiry:
                del self._data[key]
                self.expirations += 1
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            expiry = time.time() + self.ttl_s if self.ttl_s else None
            self._data[key] = (value, expiry)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)
                self.evictions += 1

    def __len__(self) -> int:
        return len(self._data)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def stats(self) -> dict:
        return {
            "size": len(self._data),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 4),
            "evictions": self.evictions,
            "expirations": self.expirations,
        }


class ResponseCache:
    """Cache EXACTO de respuestas LLM. Implementa el Protocol LLMClient, así que
    se compone con los adaptadores de §2: `ResponseCache(OpenAILLMClient())` es
    a su vez un LLMClient y entra donde sea que el handler espere uno.

    Clave = hash(model, temperature, max_tokens, prompt). Si dos requests son
    idénticos, el segundo no paga la API. En un hit se devuelve la respuesta
    con `from_cache=True` y la latencia del lookup (≈0); `cost_usd` conserva el
    costo NOMINAL (lo que habría costado) — el ahorro es no haberlo incurrido.

    Cuidado con temperature > 0: cachear sirve UNA muestra para todos los
    requests idénticos. Correcto y deseable con temp=0 (determinista); con
    temp>0 es decisión de producto (consistencia vs diversidad).
    """

    def __init__(self, base: LLMClient, maxsize: int = 2048, ttl_s: float | None = None) -> None:
        self.base = base
        self.name = base.name
        self.cache = LRUCache(maxsize=maxsize, ttl_s=ttl_s)

    @property
    def default_model(self) -> str | None:
        return getattr(self.base, "default_model", None)

    @staticmethod
    def _key(prompt: str, model: str | None, temperature: float, max_tokens: int) -> str:
        raw = f"{model}|{temperature}|{max_tokens}|{prompt}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> LLMResponse:
        key = self._key(prompt, model, temperature, max_tokens)
        t0 = time.perf_counter()
        cached = self.cache.get(key)
        if cached is not None:
            lookup_ms = (time.perf_counter() - t0) * 1000
            return replace(cached, latency_ms=lookup_ms, from_cache=True)
        resp = self.base.complete(
            prompt, model=model, temperature=temperature, max_tokens=max_tokens
        )
        self.cache.put(key, resp)
        return resp


class SemanticCache:
    """Cache por similitud semántica de la QUERY (no del prompt completo).

    Embebe la query entrante; si su coseno con alguna query previa supera
    `threshold`, devuelve la respuesta cacheada. Atrapa paráfrasis que el cache
    exacto no ve ("tasa de IVA digital" ~ "qué IVA pagan los servicios
    digitales extranjeros"). El riesgo es servir la respuesta de una query NO
    equivalente: por eso el umbral va alto (0.9+) y conviene auditar los hits.

    `embed_fn` se inyecta para no acoplar prod_lib a un embedder concreto.
    Implementación de scan lineal: suficiente para cientos de entradas; a
    escala, esto vive en el vector store (pgvector) con el mismo principio.
    """

    def __init__(
        self,
        embed_fn: Callable[[str], np.ndarray],
        threshold: float = 0.92,
        maxsize: int = 512,
        ttl_s: float | None = None,
    ) -> None:
        self.embed_fn = embed_fn
        self.threshold = threshold
        self.maxsize = maxsize
        self.ttl_s = ttl_s
        self._queries: list[str] = []
        self._emb: list[np.ndarray] = []
        self._val: list[tuple[Any, float | None]] = []
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _norm(v: np.ndarray) -> np.ndarray:
        v = np.asarray(v, dtype=np.float32)
        n = float(np.linalg.norm(v))
        return v / n if n else v

    def get(self, query: str) -> tuple[Any | None, float]:
        """Devuelve (valor_o_None, mejor_similitud)."""
        emb = self._norm(self.embed_fn(query))
        with self._lock:
            if not self._emb:
                self.misses += 1
                return None, 0.0
            sims = np.array([float(emb @ e) for e in self._emb])
            i = int(sims.argmax())
            best = float(sims[i])
            if best >= self.threshold:
                value, expiry = self._val[i]
                if expiry is not None and time.time() > expiry:
                    self.misses += 1
                    return None, best
                self.hits += 1
                return value, best
            self.misses += 1
            return None, best

    def put(self, query: str, value: Any) -> None:
        emb = self._norm(self.embed_fn(query))
        expiry = time.time() + self.ttl_s if self.ttl_s else None
        with self._lock:
            self._queries.append(query)
            self._emb.append(emb)
            self._val.append((value, expiry))
            while len(self._emb) > self.maxsize:
                self._queries.pop(0)
                self._emb.pop(0)
                self._val.pop(0)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def stats(self) -> dict:
        return {
            "size": len(self._emb),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 4),
            "threshold": self.threshold,
        }


# --------------------------------------------------------------------------- #
# §5 Observabilidad: las tres patas son logs estructurados, métricas y traces.
# Acá están las primitivas desde cero (para entender qué hace OpenTelemetry por
# debajo). El trace_id se propaga por contextvars: un request lo fija una vez y
# todo lo que emite (logs, spans) lo hereda sin pasarlo a mano por cada función.
# --------------------------------------------------------------------------- #
_trace_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "trace_id", default=None
)
_span_ctx: contextvars.ContextVar["Span | None"] = contextvars.ContextVar(
    "current_span", default=None
)


def current_trace_id() -> str | None:
    """trace_id del request en curso (None fuera de un trace)."""
    return _trace_id_ctx.get()


class StructuredLogger:
    """Emite un evento = una línea JSON. La diferencia con `print` no es estética:
    un log estructurado es **consultable** (`event=query_error model=...`), se
    parsea sin regex frágiles y se agrega por campos. El trace_id se inyecta solo
    desde el contexto, así cada línea es correlacionable con su request y su trace.
    """

    def __init__(self, service: str = "rag", stream: Any = None,
                 redact: bool = True) -> None:
        self.service = service
        self.stream = stream if stream is not None else sys.stdout
        # redact=True (default) pasa cada línea por redact_secrets (§7): aunque
        # alguien loguee un campo con un api key por error, no llega al archivo.
        self.redact = redact
        self._lock = threading.Lock()

    def emit(self, level: str, event: str, **fields: Any) -> dict:
        record = {
            "ts": round(time.time(), 3),
            "level": level,
            "service": self.service,
            "event": event,
            "trace_id": current_trace_id(),
            **fields,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        if self.redact:
            line = redact_secrets(line)
        with self._lock:
            self.stream.write(line + "\n")
            self.stream.flush()
        return record

    def info(self, event: str, **fields: Any) -> dict:
        return self.emit("INFO", event, **fields)

    def warning(self, event: str, **fields: Any) -> dict:
        return self.emit("WARNING", event, **fields)

    def error(self, event: str, **fields: Any) -> dict:
        return self.emit("ERROR", event, **fields)


class Counter:
    """Monótono creciente: requests, hits, errores. Solo sube."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.value = 0
        self._lock = threading.Lock()

    def inc(self, n: int = 1) -> None:
        with self._lock:
            self.value += n


class Gauge:
    """Valor instantáneo que sube y baja: tamaño de cache, conexiones abiertas."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.value = 0.0

    def set(self, v: float) -> None:
        self.value = float(v)


class Histogram:
    """Distribución de una medición (latencia, costo). Guarda muestras y calcula
    percentiles. Para producción de alto volumen se usan buckets/HDR para no
    guardar todo; a esta escala, las muestras crudas son exactas y simples.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._samples: list[float] = []
        self._lock = threading.Lock()

    def observe(self, v: float) -> None:
        with self._lock:
            self._samples.append(float(v))

    @property
    def count(self) -> int:
        return len(self._samples)

    @property
    def sum(self) -> float:
        return float(sum(self._samples))

    def percentile(self, p: float) -> float:
        if not self._samples:
            return 0.0
        return float(np.percentile(self._samples, p))

    def summary(self) -> dict:
        return {
            "count": self.count,
            "sum": round(self.sum, 6),
            "p50": round(self.percentile(50), 3),
            "p95": round(self.percentile(95), 3),
            "p99": round(self.percentile(99), 3),
        }


class MetricsRegistry:
    """Registro central. `counter('x')` crea-o-devuelve; `snapshot()` exporta todo
    en una estructura que un scraper (Prometheus) o un print leen igual."""

    def __init__(self) -> None:
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}
        self._lock = threading.Lock()

    def counter(self, name: str) -> Counter:
        with self._lock:
            return self._counters.setdefault(name, Counter(name))

    def gauge(self, name: str) -> Gauge:
        with self._lock:
            return self._gauges.setdefault(name, Gauge(name))

    def histogram(self, name: str) -> Histogram:
        with self._lock:
            return self._histograms.setdefault(name, Histogram(name))

    def snapshot(self) -> dict:
        return {
            "counters": {n: c.value for n, c in self._counters.items()},
            "gauges": {n: g.value for n, g in self._gauges.items()},
            "histograms": {n: h.summary() for n, h in self._histograms.items()},
        }


@dataclass
class Span:
    """Un tramo de trabajo dentro de un trace. Los spans anidan: el span hijo
    (LLM) vive dentro del padre (request). El árbol cuenta la historia de dónde
    se fue el tiempo de un request."""

    name: str
    trace_id: str
    span_id: str
    parent_id: str | None = None
    duration_ms: float = 0.0
    attributes: dict = field(default_factory=dict)
    children: list["Span"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "span_id": self.span_id,
            "duration_ms": round(self.duration_ms, 2),
            "attributes": self.attributes,
            "children": [c.to_dict() for c in self.children],
        }


class Tracer:
    """Crea traces y spans anidados, propagando el contexto por contextvars.

    `duration_ms` opcional permite construir traces MODELADOS (fixtures, demos
    deterministas); en producción se omite y el span mide su propio wall-clock.
    """

    @contextmanager
    def trace(self, name: str, trace_id: str | None = None,
              duration_ms: float | None = None, **attrs: Any) -> Iterator["Span"]:
        tid = trace_id or uuid.uuid4().hex
        root = Span(name=name, trace_id=tid, span_id=uuid.uuid4().hex[:8], attributes=dict(attrs))
        t_tok = _trace_id_ctx.set(tid)
        s_tok = _span_ctx.set(root)
        start = time.perf_counter()
        try:
            yield root
        finally:
            root.duration_ms = duration_ms if duration_ms is not None else (
                time.perf_counter() - start) * 1000
            _span_ctx.reset(s_tok)
            _trace_id_ctx.reset(t_tok)

    @contextmanager
    def span(self, name: str, duration_ms: float | None = None,
             **attrs: Any) -> Iterator["Span"]:
        parent = _span_ctx.get()
        sp = Span(
            name=name,
            trace_id=current_trace_id() or "",
            span_id=uuid.uuid4().hex[:8],
            parent_id=parent.span_id if parent else None,
            attributes=dict(attrs),
        )
        if parent is not None:
            parent.children.append(sp)
        tok = _span_ctx.set(sp)
        start = time.perf_counter()
        try:
            yield sp
        finally:
            sp.duration_ms = duration_ms if duration_ms is not None else (
                time.perf_counter() - start) * 1000
            _span_ctx.reset(tok)


# --------------------------------------------------------------------------- #
# §6 Reliability. Las APIs de LLM son red externa flaky, no servicios infalibles.
# El cliente se defiende con cuatro capas, cada una un LLMClient componible:
#   RateLimited → se autolimita ANTES del 429 del proveedor.
#   Retrying    → reintenta lo transitorio con backoff exponencial + jitter.
#   CircuitBreaking → deja de pegarle al proveedor caído (closed/open/half-open).
#   Fallback    → cuando todo falla, degrada visiblemente en vez de explotar.
# --------------------------------------------------------------------------- #
class LLMError(Exception):
    """Base de errores de proveedor LLM, normalizados sobre cualquier SDK."""


class TransientLLMError(LLMError):
    """5xx, timeout, conexión cortada: vale la pena reintentar."""


class RateLimitLLMError(TransientLLMError):
    """429: too many requests. Reintentar respetando retry_after si lo hay."""

    def __init__(self, message: str = "rate limited", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ClientLLMError(LLMError):
    """4xx del cliente (prompt inválido, auth): reintentar NO ayuda. No se retenta."""


class CircuitOpenError(LLMError):
    """El circuit breaker está abierto: ni se intentó llamar al proveedor."""


def is_retryable(exc: Exception) -> bool:
    """Solo lo transitorio se reintenta. Un 4xx o un circuito abierto, no."""
    return isinstance(exc, TransientLLMError)


class TokenBucket:
    """Rate limiter de cliente desde cero. Se rellena a `rate` tokens/seg hasta
    `capacity`; cada request consume uno. Autolimitarse ANTES de que el proveedor
    devuelva 429 evita el castigo (backoff forzado, baneos) y reparte el tráfico.

    `clock` es inyectable para tests deterministas (sin esperar tiempo real).
    """

    def __init__(self, rate: float, capacity: float, clock: Callable[[], float] = time.monotonic):
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._clock = clock
        self._updated = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    def try_acquire(self, n: int = 1) -> bool:
        """No bloqueante: True si había tokens, False si no (el caller decide)."""
        with self._lock:
            self._refill()
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    @property
    def tokens(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens


def retry_with_backoff(
    fn: Callable[[], Any],
    *,
    max_retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    jitter: bool = True,
    retryable: Callable[[Exception], bool] = is_retryable,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    on_retry: Callable[[int, Exception, float], None] | None = None,
) -> Any:
    """Reintenta `fn` ante errores retryables con backoff exponencial + jitter.

    El jitter (full jitter, estilo AWS: delay uniforme en [0, tope]) evita el
    *thundering herd*: sin él, mil clientes que fallan al mismo tiempo
    reintentan al mismo tiempo y vuelven a tumbar al proveedor que recién se
    recuperaba. Lo NO retryable (4xx) se relanza de inmediato.
    """
    _rng = rng or random
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:
            if not retryable(e) or attempt >= max_retries:
                raise
            cap = min(max_delay, base_delay * (2 ** attempt))
            # retry_after del proveedor (429) manda sobre el backoff calculado.
            retry_after = getattr(e, "retry_after", None)
            delay = retry_after if retry_after is not None else (
                _rng.uniform(0, cap) if jitter else cap
            )
            if on_retry is not None:
                on_retry(attempt + 1, e, delay)
            sleep(delay)
            attempt += 1


class CircuitBreaker:
    """Máquina de tres estados que corta el tráfico hacia un proveedor caído.

        closed     → todo pasa; cuenta fallos consecutivos.
        open       → rechaza al instante (CircuitOpenError) sin llamar al
                     proveedor; tras `recovery_timeout` pasa a half-open.
        half-open  → deja pasar UNA prueba; si va bien cierra, si falla reabre.

    Sin breaker, un proveedor caído recibe todos tus reintentos y agrava su
    incidente (y te cuelga workers esperando timeouts). El breaker te saca de
    esa trampa. `clock` inyectable para tests.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._clock = clock
        self.state = "closed"
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    def _allow(self) -> bool:
        with self._lock:
            if self.state == "open":
                if self._clock() - (self._opened_at or 0) >= self.recovery_timeout:
                    self.state = "half-open"  # dejamos pasar una prueba
                    return True
                return False
            return True  # closed o half-open

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self.state = "closed"
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self.state == "half-open" or self._failures >= self.failure_threshold:
                self.state = "open"
                self._opened_at = self._clock()

    def call(self, fn: Callable[[], Any]) -> Any:
        if not self._allow():
            raise CircuitOpenError("circuit breaker abierto; no se llamó al proveedor")
        try:
            result = fn()
        except Exception:
            self.record_failure()
            raise
        else:
            self.record_success()
            return result


# --- Wrappers: cada uno ES un LLMClient y envuelve a otro. Se apilan. --------- #
class RateLimitedLLMClient:
    """Autolimita con un TokenBucket. Sin token: RateLimitLLMError (retryable),
    así la capa de retry de arriba la absorbe con backoff."""

    def __init__(self, base: LLMClient, bucket: TokenBucket) -> None:
        self.base = base
        self.name = base.name
        self.bucket = bucket

    @property
    def default_model(self) -> str | None:
        return getattr(self.base, "default_model", None)

    def complete(self, prompt: str, *, model: str | None = None,
                 temperature: float = 0.0, max_tokens: int = 512) -> LLMResponse:
        if not self.bucket.try_acquire():
            raise RateLimitLLMError("rate limit de cliente (token bucket vacío)")
        return self.base.complete(prompt, model=model, temperature=temperature,
                                  max_tokens=max_tokens)


class RetryingLLMClient:
    """Reintenta lo transitorio con backoff + jitter."""

    def __init__(self, base: LLMClient, *, max_retries: int = 3,
                 base_delay: float = 0.5, max_delay: float = 8.0,
                 sleep: Callable[[float], None] = time.sleep,
                 rng: random.Random | None = None,
                 on_retry: Callable[[int, Exception, float], None] | None = None) -> None:
        self.base = base
        self.name = base.name
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._sleep = sleep
        self._rng = rng
        self._on_retry = on_retry

    @property
    def default_model(self) -> str | None:
        return getattr(self.base, "default_model", None)

    def complete(self, prompt: str, *, model: str | None = None,
                 temperature: float = 0.0, max_tokens: int = 512) -> LLMResponse:
        return retry_with_backoff(
            lambda: self.base.complete(prompt, model=model, temperature=temperature,
                                       max_tokens=max_tokens),
            max_retries=self.max_retries, base_delay=self.base_delay,
            max_delay=self.max_delay, sleep=self._sleep, rng=self._rng,
            on_retry=self._on_retry,
        )


class CircuitBreakingLLMClient:
    """Enruta las llamadas por un CircuitBreaker."""

    def __init__(self, base: LLMClient, breaker: CircuitBreaker) -> None:
        self.base = base
        self.name = base.name
        self.breaker = breaker

    @property
    def default_model(self) -> str | None:
        return getattr(self.base, "default_model", None)

    def complete(self, prompt: str, *, model: str | None = None,
                 temperature: float = 0.0, max_tokens: int = 512) -> LLMResponse:
        return self.breaker.call(
            lambda: self.base.complete(prompt, model=model, temperature=temperature,
                                       max_tokens=max_tokens)
        )


class FallbackLLMClient:
    """Intenta `primary`; si lanza un LLMError (incluido circuito abierto), cae a
    `secondary`. El secondary puede ser otro modelo (GPT-4o-mini), o un
    StaticLLMClient de "estamos en mantención": degradar visible > 500."""

    def __init__(self, primary: LLMClient, secondary: LLMClient,
                 on_fallback: Callable[[Exception], None] | None = None) -> None:
        self.primary = primary
        self.secondary = secondary
        self.name = primary.name
        self._on_fallback = on_fallback

    @property
    def default_model(self) -> str | None:
        return getattr(self.primary, "default_model", None)

    def complete(self, prompt: str, *, model: str | None = None,
                 temperature: float = 0.0, max_tokens: int = 512) -> LLMResponse:
        try:
            return self.primary.complete(prompt, model=model, temperature=temperature,
                                         max_tokens=max_tokens)
        except LLMError as e:
            if self._on_fallback is not None:
                self._on_fallback(e)
            return self.secondary.complete(prompt, model=model, temperature=temperature,
                                           max_tokens=max_tokens)


# --------------------------------------------------------------------------- #
# §7 Configuración y secretos. La config es entrada del entorno, no constantes
# en el código. ServiceSettings centraliza TODOS los knobs de §§2-6 con tipos
# validados y defaults; los secretos son SecretStr (no se imprimen). Y dos
# utilidades para que un secreto nunca termine en logs ni en el repo.
# --------------------------------------------------------------------------- #
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{12,}"),                  # Anthropic
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),                      # OpenAI-style
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),                  # GitHub token
    re.compile(r"(?P<scheme>postgres(?:ql)?|redis|mysql)://[^:/\s]+:[^@\s]+@"),  # url con pass
    re.compile(r"AKIA[0-9A-Z]{16}"),                            # AWS access key
]


def scan_for_secrets(text: str) -> list[str]:
    """Devuelve los secretos detectados en `text`. Vacío = limpio. Pensado para
    correr en CI sobre diffs, logs de ejemplo y dumps de config (falla el build
    si encuentra algo)."""
    found = []
    for pat in _SECRET_PATTERNS:
        for m in pat.finditer(text):
            found.append(m.group(0))
    return found


def redact_secrets(text: str) -> str:
    """Reemplaza secretos por un marcador. La URL con password conserva el scheme
    y el host (útil para debug) pero borra las credenciales."""
    out = text
    for pat in _SECRET_PATTERNS:
        if "scheme" in pat.groupindex:
            out = pat.sub(lambda m: f"{m.group('scheme')}://***:***@", out)
        else:
            out = pat.sub("***REDACTED***", out)
    return out


class ServiceSettings(BaseSettings):
    """Config del servicio desde entorno / .env. Un solo lugar tipado y validado
    en vez de os.environ[...] regado por el código. Los secretos son SecretStr:
    su repr es '**********', así no se filtran en un print o un stack trace.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore",
                                      case_sensitive=False)

    # --- proveedor LLM (§2) ---
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    # --- estado externo (§7): secretos, nunca hardcodeados ---
    database_url: SecretStr | None = None      # postgres+pgvector (Supabase)
    redis_url: SecretStr | None = None         # cache compartido multi-réplica
    # --- knobs del RAG (§2/§3) ---
    k_default: int = Field(3, ge=1, le=20)
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(512, ge=1, le=4096)
    prompt_name: str = "rag-fiscal"
    prompt_version: str | None = None          # None = última
    # --- caché (§4) ---
    response_cache_size: int = Field(2048, ge=0)
    response_cache_ttl_s: float | None = 3600.0
    semantic_threshold: float = Field(0.7, ge=0.0, le=1.0)
    # --- reliability (§6) ---
    rate_limit_rps: float = Field(10.0, gt=0)
    rate_limit_burst: int = Field(20, ge=1)
    max_retries: int = Field(3, ge=0, le=10)
    breaker_failure_threshold: int = Field(5, ge=1)
    breaker_recovery_timeout_s: float = Field(30.0, gt=0)
    llm_timeout_s: float = Field(30.0, gt=0)   # cierra el anti-patrón "sin timeout" de §2
    # --- ops (§5) ---
    log_level: str = "INFO"
    service_version: str = "0.1.0"

    def public_dict(self) -> dict:
        """Dump con los secretos redactados — apto para loguear en el startup y
        para que /info muestre la config sin filtrar credenciales."""
        out = {}
        for name, value in self.model_dump().items():
            field = type(self).model_fields[name]
            if field.annotation is not None and "SecretStr" in str(field.annotation):
                out[name] = "***SET***" if getattr(self, name) is not None else None
            else:
                out[name] = value
        return out


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
                "from_cache": llm_resp.from_cache,  # §4: ¿salió del response cache?
            },
        )
