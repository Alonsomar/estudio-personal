"""harness_lib — núcleo reutilizable de la masterclass 06-harness.

Acumula los componentes que las secciones introducen:

  §1  El bucle percibir → decidir → actuar → observar: `Herramienta`,
      `ToolRegistry`, `AgentLoop`, `Trayectoria` y las dos políticas de
      decisión (`PoliticaLLM` con caché, `PoliticaGuionada` para tests).
      `HarnessConfig` es el objeto central del módulo: reúne en un solo
      lugar las reglas del entorno cuyo efecto se mide.

Diseño: sin framework de agentes de terceros. El bucle son ~120 líneas y
esconderlas detrás de la abstracción de un tercero haría el módulo menos
didáctico y más frágil (decisión 1 del plan). Las herramientas del agente
son las capacidades ya construidas en el repo: buscar es el BM25 de `02`,
leer es el corpus real, recorrer es el grafo normativo de `05`.

El contrato de caché (`LLMCacheEntry`, `load_versioned_cache`,
`save_versioned_cache`) se importa de `ontology_lib`, no se duplica.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from difflib import get_close_matches
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ValidationError

_ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (_ROOT, _ROOT / "02-retrieval" / "code", _ROOT / "05-ontologias" / "code"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from ontology_lib import (  # noqa: E402
    DEFAULT_TARIFF_USD_PER_M,
    LLMCacheEntry,
    Norma,
    OfflineCacheMiss,
    RelacionNormativa,
    TipoRelacion,
    build_grafo_normativo,
    historical_cost,
    load_versioned_cache,
    prompt_sha256,
    save_versioned_cache,
)
from retrieval_lib import BM25Retriever, load_corpus_chunks  # noqa: E402

from shared.utils import get_project_root  # noqa: E402


# --------------------------------------------------------------------------- #
# §1 Contratos del bucle.
#
# Un agente son cuatro objetos, no un framework: herramientas con esquema,
# un registro que las despacha, una política que decide y un bucle que
# registra lo que pasó. El cuarto —el registro de lo que pasó— es el que
# casi todos los tutoriales tratan como logging y es el insumo de §7.
# --------------------------------------------------------------------------- #

#: Aproximación de tokens por carácter para español administrativo. Se usa
#: SOLO para contabilidad offline del contexto (§2). Donde hay número real
#: de la API —`LLMCacheEntry.tokens_input`— se usa ese y se dice cuál es cuál.
CHARS_POR_TOKEN = 4.0


def estimar_tokens(texto: str) -> int:
    """Estimación de tokens por longitud. Es una aproximación declarada, no
    una medición: el número real de la API vive en el caché."""
    return max(1, round(len(texto) / CHARS_POR_TOKEN))


class ToolError(Exception):
    """Fallo de una herramienta expresado como contrato, no como excepción.

    Los tres campos son la tesis de §3: un error útil dice qué se esperaba,
    qué llegó y qué hacer a continuación. Un `KeyError: 'doc_id'` tiene la
    misma información de diagnóstico y ninguna capacidad de corrección — el
    modelo no puede deducir de él cuál era el valor válido.
    """

    def __init__(self, *, esperado: str, recibido: str, siguiente_paso: str) -> None:
        super().__init__(f"esperado={esperado!r} recibido={recibido!r}")
        self.esperado = esperado
        self.recibido = recibido
        self.siguiente_paso = siguiente_paso


class EstadoPaso(str, Enum):
    """Clasificación del resultado de un paso. Es la base de las métricas de
    trayectoria de §7: no es lo mismo fallar por inventar una herramienta que
    por pedir una página que no existe."""

    OK = "ok"
    ERROR_HERRAMIENTA_DESCONOCIDA = "error_herramienta_desconocida"
    ERROR_ARGUMENTOS = "error_argumentos"
    ERROR_EJECUCION = "error_ejecucion"


class MotivoCorte(str, Enum):
    """Por qué terminó el bucle. `MAX_PASOS` y `SIN_PROGRESO` son fracasos de
    distinto tipo y §8 los trata por separado."""

    RESPONDIO = "respondio"
    MAX_PASOS = "max_pasos"
    SIN_PROGRESO = "sin_progreso"
    ERROR_POLITICA = "error_politica"


@dataclass
class Herramienta:
    """Una herramienta es un contrato: nombre, esquema de argumentos,
    descripción en prosa y una función que la ejecuta.

    Los atributos `idempotente`, `reversible` y `destructiva` no se usan en
    §1 — son metadata que §6 convierte en política de permisos. Van acá
    porque son propiedades de la herramienta, no del control: quien la
    escribe es quien sabe si repetirla dos veces es inocuo.
    """

    nombre: str
    descripcion: str
    args_model: type[BaseModel]
    fn: Callable[[Any], str]
    idempotente: bool = True
    reversible: bool = True
    destructiva: bool = False

    def spec_openai(self) -> dict[str, Any]:
        """Esquema en el formato de tool-calling de la API. El JSON Schema
        sale del modelo Pydantic: una sola fuente de verdad para lo que la
        función acepta y para lo que el modelo cree que acepta."""
        return {
            "type": "function",
            "function": {
                "name": self.nombre,
                "description": self.descripcion,
                "parameters": self.args_model.model_json_schema(),
            },
        }


class HarnessConfig(BaseModel):
    """Las reglas del entorno. Es el objeto que este módulo manipula.

    Cambiar cualquiera de estos campos deja el modelo, las herramientas y la
    tarea intactos y altera solo lo que el agente percibe. Todo delta medido
    en §1, §3 y §8 es un delta de este objeto.
    """

    nombre: str = "base"
    max_pasos: int = 8
    #: `None` = la observación entra completa al contexto, como en la
    #: implementación ingenua. Un entero trunca y avisa cómo seguir.
    max_chars_observacion: int | None = None
    #: "opaco" = el error dice que falló; "contrato" = dice qué esperaba,
    #: qué llegó y qué hacer.
    estilo_error: Literal["opaco", "contrato"] = "opaco"
    #: Corta el bucle si el agente repite la misma llamada N veces.
    max_repeticiones: int = 3
    instrucciones_extra: str = ""


class Observacion(BaseModel):
    """Lo que el agente ve después de actuar. `texto` es literalmente lo que
    entra al contexto; `caracteres_originales` es lo que la herramienta
    produjo. La brecha entre ambos es el objeto de estudio de §2."""

    ok: bool
    texto: str
    truncado: bool = False
    caracteres_originales: int = 0
    estado: EstadoPaso = EstadoPaso.OK


@dataclass
class Percepcion:
    """Lo que la política recibe para decidir. Deliberadamente explícito:
    en un agente real esto es *todo* lo que el modelo sabe del mundo."""

    pregunta: str
    mensajes: list[dict[str, Any]]
    herramientas: list[dict[str, Any]]
    paso: int


@dataclass
class Decision:
    """Lo que la política devuelve. `mensaje_asistente` y `tool_call_id`
    permiten reconstruir el historial en el formato de la API sin que el
    bucle tenga que conocer los detalles del proveedor."""

    accion: Literal["usar_herramienta", "responder", "abandonar"]
    herramienta: str | None = None
    argumentos: dict[str, Any] = field(default_factory=dict)
    respuesta: str | None = None
    docs_citados: list[str] = field(default_factory=list)
    mensaje_asistente: dict[str, Any] | None = None
    tool_call_id: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    costo_usd: float = 0.0


class Paso(BaseModel):
    """Una iteración completa del bucle, serializable. La trayectoria es la
    unidad de evaluación de §7, así que tiene que persistir a disco."""

    indice: int
    herramienta: str | None
    argumentos: dict[str, Any]
    estado: EstadoPaso
    observacion: str
    truncado: bool = False
    caracteres_originales: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    costo_usd: float = 0.0


class Trayectoria(BaseModel):
    """El registro completo de una tarea. Contiene lo que el agente hizo,
    no solo lo que respondió — que es la diferencia entre evaluar proceso y
    evaluar resultado (§7)."""

    tarea_id: str
    pregunta: str
    harness: str
    pasos: list[Paso] = []
    respuesta_final: str | None = None
    docs_citados: list[str] = []
    motivo_corte: MotivoCorte = MotivoCorte.MAX_PASOS

    @property
    def n_pasos(self) -> int:
        return len(self.pasos)

    @property
    def tokens_in(self) -> int:
        return sum(p.tokens_in for p in self.pasos)

    @property
    def tokens_out(self) -> int:
        return sum(p.tokens_out for p in self.pasos)

    @property
    def costo_usd(self) -> float:
        return sum(p.costo_usd for p in self.pasos)

    @property
    def n_errores(self) -> int:
        return sum(1 for p in self.pasos if p.estado is not EstadoPaso.OK)


class ToolRegistry:
    """Despacha llamadas a herramientas y traduce fallos según el estilo del
    harness. Es el punto donde el diseño del entorno se vuelve texto que el
    modelo lee — y por eso es donde §1 y §3 intervienen."""

    def __init__(self, herramientas: list[Herramienta] | None = None) -> None:
        self._tools: dict[str, Herramienta] = {}
        for h in herramientas or []:
            self.registrar(h)

    def registrar(self, herramienta: Herramienta) -> "ToolRegistry":
        self._tools[herramienta.nombre] = herramienta
        return self

    def __contains__(self, nombre: object) -> bool:
        return nombre in self._tools

    @property
    def nombres(self) -> list[str]:
        return sorted(self._tools)

    def get(self, nombre: str) -> Herramienta:
        return self._tools[nombre]

    def specs_openai(self) -> list[dict[str, Any]]:
        return [self._tools[n].spec_openai() for n in self.nombres]

    def invocar(
        self, nombre: str, argumentos: dict[str, Any], config: HarnessConfig
    ) -> Observacion:
        """Percibir el resultado de actuar. Los tres modos de fallo se
        distinguen porque §7 los mide por separado."""
        contrato = config.estilo_error == "contrato"

        if nombre not in self._tools:
            if contrato:
                sugerencia = get_close_matches(nombre, self.nombres, n=1)
                extra = f" ¿Quisiste decir '{sugerencia[0]}'?" if sugerencia else ""
                texto = (
                    f"ERROR herramienta_desconocida. Recibido: '{nombre}'. "
                    f"Herramientas disponibles: {', '.join(self.nombres)}.{extra} "
                    "Siguiente paso: llamá a una de las herramientas disponibles."
                )
            else:
                texto = "Error: herramienta no encontrada"
            return Observacion(
                ok=False,
                texto=texto,
                estado=EstadoPaso.ERROR_HERRAMIENTA_DESCONOCIDA,
                caracteres_originales=len(texto),
            )

        tool = self._tools[nombre]
        try:
            args = tool.args_model.model_validate(argumentos)
        except ValidationError as exc:
            if contrato:
                detalles = "; ".join(
                    f"campo '{'.'.join(str(x) for x in e['loc']) or '(raíz)'}': {e['msg']}"
                    for e in exc.errors()
                )
                requeridos = ", ".join(
                    sorted(tool.args_model.model_json_schema().get("required", []))
                )
                texto = (
                    f"ERROR argumentos_invalidos en '{nombre}'. {detalles}. "
                    f"Recibido: {json.dumps(argumentos, ensure_ascii=False)}. "
                    f"Campos obligatorios: {requeridos or '(ninguno)'}. "
                    "Siguiente paso: volvé a llamar la herramienta con los "
                    "campos corregidos."
                )
            else:
                texto = "Error: argumentos inválidos"
            return Observacion(
                ok=False,
                texto=texto,
                estado=EstadoPaso.ERROR_ARGUMENTOS,
                caracteres_originales=len(texto),
            )

        try:
            salida = tool.fn(args)
        except ToolError as exc:
            texto = (
                f"ERROR en '{nombre}'. Esperado: {exc.esperado}. "
                f"Recibido: {exc.recibido}. Siguiente paso: {exc.siguiente_paso}"
                if contrato
                else f"Error: {type(exc).__name__}"
            )
            return Observacion(
                ok=False,
                texto=texto,
                estado=EstadoPaso.ERROR_EJECUCION,
                caracteres_originales=len(texto),
            )
        except Exception as exc:  # noqa: BLE001 — el harness no puede caerse por una tool
            texto = (
                f"ERROR inesperado en '{nombre}': {type(exc).__name__}: {exc}. "
                "Siguiente paso: probá otra herramienta o respondé con lo que tengas."
                if contrato
                else f"Error: {type(exc).__name__}"
            )
            return Observacion(
                ok=False,
                texto=texto,
                estado=EstadoPaso.ERROR_EJECUCION,
                caracteres_originales=len(texto),
            )

        return self._acotar(salida, config)

    @staticmethod
    def _acotar(salida: str, config: HarnessConfig) -> Observacion:
        """Truncado con aviso de continuación.

        Truncar sin decirlo es peor que no truncar: el agente cree que vio
        el documento completo y responde sobre un fragmento. El aviso es lo
        que convierte una pérdida de información en una acción disponible.
        """
        limite = config.max_chars_observacion
        original = len(salida)
        if limite is None or original <= limite:
            return Observacion(ok=True, texto=salida, caracteres_originales=original)
        recorte = salida[:limite]
        aviso = (
            f"\n\n[...truncado: se muestran {limite} de {original} caracteres. "
            "Si necesitás el resto, pedí la página siguiente con el parámetro "
            "'pagina' o acotá la consulta.]"
        )
        return Observacion(
            ok=True,
            texto=recorte + aviso,
            truncado=True,
            caracteres_originales=original,
        )


class Politica(Protocol):
    """Quien decide. El modelo es una implementación de esto, no el centro
    del sistema — que es exactamente la tesis del módulo."""

    def decidir(self, percepcion: Percepcion) -> Decision: ...


PROMPT_SISTEMA = """Sos un asistente experto en normativa y presupuesto público chileno.
Respondés únicamente con información encontrada en el corpus mediante las herramientas
disponibles; no usás conocimiento propio sobre normas chilenas.

Método:
1. Usá las herramientas para localizar la evidencia.
2. Cuando tengas la evidencia, llamá a 'responder' con la respuesta y la lista de
   archivos del corpus que la sustentan.
3. Si el corpus no contiene la información, llamá a 'responder' diciendo que no
   consta en el corpus, con docs_citados vacío. Es una respuesta correcta, no un fallo.

Los identificadores de documento son nombres de archivo del corpus
(por ejemplo 'ley-01-dl-825-iva-base.txt'). No inventes identificadores."""


class AgentLoop:
    """Percibir → decidir → actuar → observar.

    Todo lo que el módulo estudia pasa acá adentro, y son cuarenta líneas.
    Lo que cambia entre un agente que funciona y uno que no, no está en este
    bucle: está en `config`, en el diseño de las herramientas y en el texto
    de las observaciones.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        politica: Politica,
        config: HarnessConfig | None = None,
    ) -> None:
        self.registry = registry
        self.politica = politica
        self.config = config or HarnessConfig()

    def _sistema(self) -> str:
        base = PROMPT_SISTEMA
        if self.config.instrucciones_extra:
            base = f"{base}\n\n{self.config.instrucciones_extra}"
        return base

    def correr(self, tarea_id: str, pregunta: str) -> Trayectoria:
        cfg = self.config
        tray = Trayectoria(tarea_id=tarea_id, pregunta=pregunta, harness=cfg.nombre)
        mensajes: list[dict[str, Any]] = [
            {"role": "system", "content": self._sistema()},
            {"role": "user", "content": pregunta},
        ]
        specs = self.registry.specs_openai()
        firmas: list[str] = []

        for i in range(cfg.max_pasos):
            percepcion = Percepcion(
                pregunta=pregunta, mensajes=mensajes, herramientas=specs, paso=i
            )
            try:
                decision = self.politica.decidir(percepcion)
            except OfflineCacheMiss:
                raise
            except Exception as exc:  # noqa: BLE001
                tray.motivo_corte = MotivoCorte.ERROR_POLITICA
                tray.respuesta_final = f"[fallo de la política: {type(exc).__name__}: {exc}]"
                return tray

            if decision.accion == "responder":
                tray.pasos.append(
                    Paso(
                        indice=i,
                        herramienta="responder",
                        argumentos={},
                        estado=EstadoPaso.OK,
                        observacion="",
                        tokens_in=decision.tokens_in,
                        tokens_out=decision.tokens_out,
                        costo_usd=decision.costo_usd,
                    )
                )
                tray.respuesta_final = decision.respuesta
                tray.docs_citados = decision.docs_citados
                tray.motivo_corte = MotivoCorte.RESPONDIO
                return tray

            if decision.accion == "abandonar":
                tray.motivo_corte = MotivoCorte.SIN_PROGRESO
                return tray

            nombre = decision.herramienta or ""
            obs = self.registry.invocar(nombre, decision.argumentos, cfg)
            tray.pasos.append(
                Paso(
                    indice=i,
                    herramienta=nombre,
                    argumentos=decision.argumentos,
                    estado=obs.estado,
                    observacion=obs.texto,
                    truncado=obs.truncado,
                    caracteres_originales=obs.caracteres_originales,
                    tokens_in=decision.tokens_in,
                    tokens_out=decision.tokens_out,
                    costo_usd=decision.costo_usd,
                )
            )

            if decision.mensaje_asistente is not None:
                mensajes.append(decision.mensaje_asistente)
                mensajes.append(
                    {
                        "role": "tool",
                        "tool_call_id": decision.tool_call_id,
                        "content": obs.texto,
                    }
                )
            else:  # política sin formato de proveedor (tests, §5)
                mensajes.append(
                    {"role": "assistant", "content": f"{nombre}({decision.argumentos})"}
                )
                mensajes.append({"role": "user", "content": obs.texto})

            # Corte por bucle estéril: la misma llamada repetida N veces no
            # va a empezar a devolver algo distinto (§8 lo desarrolla).
            firma = f"{nombre}:{json.dumps(decision.argumentos, sort_keys=True, ensure_ascii=False)}"
            firmas.append(firma)
            if firmas.count(firma) >= cfg.max_repeticiones:
                tray.motivo_corte = MotivoCorte.SIN_PROGRESO
                return tray

        tray.motivo_corte = MotivoCorte.MAX_PASOS
        return tray


# --------------------------------------------------------------------------- #
# Políticas de decisión.
# --------------------------------------------------------------------------- #
class PoliticaGuionada:
    """Política determinista que ejecuta un guion fijo de decisiones.

    No simula a un modelo y no pretende decir nada sobre capacidad de
    razonamiento (regla 3 del plan). Existe para dos cosas: testear el bucle
    sin red, y construir contraejemplos estructurales donde hace falta una
    secuencia de acciones exacta (§6).
    """

    def __init__(self, guion: list[Decision]) -> None:
        self.guion = list(guion)
        self.llamadas = 0

    def decidir(self, percepcion: Percepcion) -> Decision:
        if self.llamadas >= len(self.guion):
            return Decision(accion="responder", respuesta="[guion agotado]")
        decision = self.guion[self.llamadas]
        self.llamadas += 1
        return decision


CACHE_SCHEMA_VERSION = "decision-tool-call-v1"


def harness_cache_key(
    *,
    model: str,
    mensajes: list[dict[str, Any]],
    herramientas: list[dict[str, Any]],
    temperature: float,
    replica: int = 0,
) -> str:
    """Clave estable de la llamada. Incluye el historial completo y los
    esquemas de herramientas: si el harness cambia el texto de una
    observación o el esquema de una tool, la clave cambia y el caché no
    puede devolver una respuesta que el modelo nunca dio en ese contexto.
    """
    payload = {
        "model": model,
        "mensajes": mensajes,
        "herramientas": herramientas,
        "temperature": temperature,
        "replica": replica,
    }
    serializado = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


class PoliticaLLM:
    """Política respaldada por un modelo real, con caché versionado.

    Offline por defecto: un *cache miss* es error, no una llamada silenciosa
    a la red. `allow_api=True` solo durante la corrida controlada que genera
    el caché, con tope de llamadas y de gasto.

    Usa tool-calling nativo, no salida estructurada con un saco de
    argumentos: el esquema por herramienta es justamente el contrato que §3
    estudia, y aplanarlo lo destruiría.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        cache_path: Path | None = None,
        *,
        allow_api: bool = False,
        temperature: float = 0.0,
        replica: int = 0,
        max_api_calls: int = 200,
        max_cost_usd: float = 0.50,
    ) -> None:
        self.model = model
        self.cache_path = Path(cache_path) if cache_path else None
        self.allow_api = allow_api
        self.temperature = temperature
        self.replica = replica
        self.max_api_calls = max_api_calls
        self.max_cost_usd = max_cost_usd
        self._cache: dict[str, LLMCacheEntry] = load_versioned_cache(self.cache_path)
        self.api_calls = 0
        self.aciertos_cache = 0
        self.tokens_in = 0
        self.tokens_out = 0

    @property
    def historical_cost_usd(self) -> float:
        return sum(e.historical_cost_usd for e in self._cache.values())

    @property
    def historical_tokens(self) -> tuple[int, int]:
        return (
            sum(e.tokens_input for e in self._cache.values()),
            sum(e.tokens_output for e in self._cache.values()),
        )

    def decidir(self, percepcion: Percepcion) -> Decision:
        clave = harness_cache_key(
            model=self.model,
            mensajes=percepcion.mensajes,
            herramientas=percepcion.herramientas,
            temperature=self.temperature,
            replica=self.replica,
        )
        if clave in self._cache:
            self.aciertos_cache += 1
            entrada = self._cache[clave]
            return self._decision_desde(entrada.response, entrada)

        if not self.allow_api:
            raise OfflineCacheMiss(
                f"cache miss del bucle ({clave[:12]}); repetí con --allow-api "
                "solo durante la corrida controlada que regenera el caché"
            )
        if self.api_calls >= self.max_api_calls:
            raise RuntimeError(f"límite de llamadas alcanzado: {self.max_api_calls}")
        if historical_cost(self.tokens_in, self.tokens_out) > self.max_cost_usd:
            raise RuntimeError(f"presupuesto API excedido: USD {self.max_cost_usd}")

        from dotenv import load_dotenv

        load_dotenv()
        from openai import OpenAI

        resp = OpenAI().chat.completions.create(
            model=self.model,
            messages=percepcion.mensajes,
            tools=percepcion.herramientas,
            temperature=self.temperature,
        )
        mensaje = resp.choices[0].message
        tokens_in = resp.usage.prompt_tokens if resp.usage else 0
        tokens_out = resp.usage.completion_tokens if resp.usage else 0
        self.api_calls += 1
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out

        crudo = {
            "content": mensaje.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                }
                for tc in (mensaje.tool_calls or [])
            ],
        }
        entrada = LLMCacheEntry(
            response=crudo,
            model_requested=self.model,
            model_returned=resp.model,
            prompt_version="harness-bucle-v1",
            schema_version=CACHE_SCHEMA_VERSION,
            temperature=self.temperature,
            prompt_sha256=prompt_sha256(
                json.dumps(percepcion.mensajes, ensure_ascii=False, sort_keys=True)
            ),
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            tariff_usd_per_m=DEFAULT_TARIFF_USD_PER_M,
            historical_cost_usd=historical_cost(tokens_in, tokens_out),
            replica=self.replica,
        )
        self._cache[clave] = entrada
        save_versioned_cache(self.cache_path, self._cache)
        return self._decision_desde(crudo, entrada)

    @staticmethod
    def _decision_desde(crudo: dict[str, Any], entrada: LLMCacheEntry) -> Decision:
        """Traduce la respuesta del proveedor a la `Decision` del bucle.

        Un modelo que devuelve texto en vez de llamar a una herramienta no
        está fallando: está respondiendo. El harness lo trata como respuesta
        final sin documentos citados, que es lo que efectivamente es — y §7
        lo penaliza por no citar, no por no usar la herramienta.
        """
        comunes = {
            "tokens_in": entrada.tokens_input,
            "tokens_out": entrada.tokens_output,
            "costo_usd": entrada.historical_cost_usd,
        }
        llamadas = crudo.get("tool_calls") or []
        if not llamadas:
            return Decision(
                accion="responder", respuesta=crudo.get("content") or "", **comunes
            )

        primera = llamadas[0]
        try:
            argumentos = json.loads(primera["arguments"] or "{}")
        except json.JSONDecodeError:
            argumentos = {"__json_invalido__": primera["arguments"]}
        mensaje_asistente = {
            "role": "assistant",
            "content": crudo.get("content"),
            "tool_calls": [
                {
                    "id": primera["id"],
                    "type": "function",
                    "function": {
                        "name": primera["name"],
                        "arguments": primera["arguments"],
                    },
                }
            ],
        }

        if primera["name"] == "responder":
            return Decision(
                accion="responder",
                respuesta=argumentos.get("respuesta", ""),
                docs_citados=list(argumentos.get("docs_citados", []) or []),
                **comunes,
            )
        return Decision(
            accion="usar_herramienta",
            herramienta=primera["name"],
            argumentos=argumentos,
            mensaje_asistente=mensaje_asistente,
            tool_call_id=primera["id"],
            **comunes,
        )


# --------------------------------------------------------------------------- #
# Tareas y medición.
#
# La métrica es objetiva y no usa juez LLM: se compara el conjunto de
# documentos que el agente cita con el conjunto esperado por un golden ya
# auditado. Es una métrica dura —exige el conjunto exacto— y por eso el
# número absoluto importa menos que el delta entre harnesses.
# --------------------------------------------------------------------------- #
class Tarea(BaseModel):
    id: str
    familia: Literal["recuperacion", "estructural", "abstencion"]
    dificultad: str
    pregunta: str
    docs_esperados: list[str]
    origen: dict[str, str] = {}


class ResultadoTarea(BaseModel):
    tarea_id: str
    familia: str
    harness: str
    acierto_exacto: bool
    precision: float
    recall: float
    f1: float
    n_pasos: int
    n_errores: int
    tokens_in: int
    tokens_out: int
    costo_usd: float
    motivo_corte: MotivoCorte


def cargar_tareas(path: Path | None = None) -> list[Tarea]:
    ruta = path or (Path(__file__).resolve().parent.parent / "examples" / "tareas-agente.json")
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return [Tarea.model_validate(i) for i in datos["items"]]


def recuperacion_tras_error(trayectorias: list[Trayectoria]) -> tuple[int, float]:
    """De todos los pasos con error que tuvieron un paso siguiente, ¿qué
    fracción fue seguida de un paso exitoso?

    Es la métrica que operacionaliza la tesis de §3: un mensaje de error es
    un canal de enseñanza o no es nada. Un error del que el agente no se
    recupera costó un paso y no compró información.
    """
    oportunidades = exitos = 0
    for tray in trayectorias:
        for anterior, siguiente in zip(tray.pasos, tray.pasos[1:]):
            if anterior.estado is not EstadoPaso.OK:
                oportunidades += 1
                exitos += siguiente.estado is EstadoPaso.OK
    return oportunidades, (exitos / oportunidades if oportunidades else 0.0)


def llamadas_redundantes(tray: Trayectoria) -> int:
    """Llamadas idénticas (misma herramienta, mismos argumentos) repetidas.

    Una llamada redundante no es un error: la herramienta respondió bien.
    Es un paso pagado que no agregó información — el desperdicio que la
    métrica de resultado no ve y que §7 mide en serio.
    """
    vistas: set[str] = set()
    repetidas = 0
    for paso in tray.pasos:
        firma = f"{paso.herramienta}:{json.dumps(paso.argumentos, sort_keys=True, ensure_ascii=False)}"
        if firma in vistas:
            repetidas += 1
        vistas.add(firma)
    return repetidas


def evaluar_trayectoria(tray: Trayectoria, tarea: Tarea) -> ResultadoTarea:
    """Puntúa una trayectoria contra su tarea.

    La abstención se trata como el caso degenerado del conjunto vacío, no
    como una regla aparte: si no se espera ningún documento, citar cero es
    precisión y recall perfectos. Es la misma convención de `01 §5`, que
    evita tener dos métricas incomparables.
    """
    esperados = set(tarea.docs_esperados)
    citados = set(tray.docs_citados)
    if not esperados:
        precision = recall = 1.0 if not citados else 0.0
    else:
        interseccion = len(esperados & citados)
        precision = interseccion / len(citados) if citados else 0.0
        recall = interseccion / len(esperados)
    f1 = (
        2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    )
    return ResultadoTarea(
        tarea_id=tarea.id,
        familia=tarea.familia,
        harness=tray.harness,
        acierto_exacto=esperados == citados,
        precision=precision,
        recall=recall,
        f1=f1,
        n_pasos=tray.n_pasos,
        n_errores=tray.n_errores,
        tokens_in=tray.tokens_in,
        tokens_out=tray.tokens_out,
        costo_usd=tray.costo_usd,
        motivo_corte=tray.motivo_corte,
    )


# --------------------------------------------------------------------------- #
# Las herramientas del corpus chileno.
#
# Ninguna es de juguete: buscar es el BM25 de `02`, leer es el corpus real,
# recorrer es el grafo normativo auditado de `05`. La consecuencia es que
# los fallos del agente son fallos reales del dominio, no artefactos de un
# entorno inventado para la demo.
# --------------------------------------------------------------------------- #
CORPUS_DIR = get_project_root() / "shared" / "corpus_chileno"
RELACIONES_PATH = get_project_root() / "05-ontologias" / "examples" / "relaciones-manual.json"

CARACTERES_POR_PAGINA = 2_000


class ArgsBuscar(BaseModel):
    consulta: str
    k: int = 3


class ArgsLeer(BaseModel):
    doc_id: str
    pagina: int = 1


class ArgsVecinos(BaseModel):
    doc_id: str
    tipo_relacion: str
    direccion: Literal["in", "out"] = "out"


class ArgsResponder(BaseModel):
    respuesta: str
    docs_citados: list[str] = []


def cargar_grafo_normativo():
    """Grafo normativo de `05`, cargado desde el dataset auditado por B13."""
    datos = json.loads(RELACIONES_PATH.read_text(encoding="utf-8"))
    normas = [Norma.model_validate(n) for n in datos["normas"]]
    relaciones = [RelacionNormativa.model_validate(r) for r in datos["relaciones"]]
    return build_grafo_normativo(normas, relaciones)


def construir_herramientas(
    *, con_grafo: bool = True, chars_por_pagina: int = CARACTERES_POR_PAGINA
) -> ToolRegistry:
    """Registro de herramientas sobre el corpus chileno.

    `con_grafo=False` deja al agente solo con búsqueda y lectura: es el brazo
    de control de §7 para medir si recorrer el grafo de `05` bajo demanda
    aporta algo que el retrieval de `02` no daba.
    """
    chunks = load_corpus_chunks(CORPUS_DIR)
    bm25 = BM25Retriever().fit(chunks)
    docs_validos = sorted({c.doc_id for c in chunks})

    def buscar(args: ArgsBuscar) -> str:
        k = max(1, min(args.k, 10))
        resultados = bm25.search(args.consulta, k=k)
        if not resultados:
            return "Sin resultados para esa consulta."
        lineas = []
        for r in resultados:
            chunk = chunks[r.index]
            lineas.append(
                f"[{chunk.chunk_id}] (score {r.score:.2f})\n{chunk.text.strip()}"
            )
        return "\n\n".join(lineas)

    def leer(args: ArgsLeer) -> str:
        ruta = CORPUS_DIR / args.doc_id
        if not ruta.exists():
            # El id canónico es el nombre de archivo (doctrina #6). Cuando el
            # agente inventa uno, el error tiene que devolverlo al catálogo.
            cercanos = get_close_matches(args.doc_id, docs_validos, n=3, cutoff=0.4)
            raise ToolError(
                esperado="un doc_id existente del corpus (nombre de archivo .txt)",
                recibido=args.doc_id,
                siguiente_paso=(
                    f"probá con uno de estos: {', '.join(cercanos)}"
                    if cercanos
                    else "usá 'buscar_corpus' para localizar el documento y leé el "
                    "doc_id del identificador entre corchetes"
                ),
            )
        texto = ruta.read_text(encoding="utf-8")
        total_paginas = max(1, -(-len(texto) // chars_por_pagina))
        if args.pagina < 1 or args.pagina > total_paginas:
            raise ToolError(
                esperado=f"pagina entre 1 y {total_paginas}",
                recibido=str(args.pagina),
                siguiente_paso=f"volvé a llamar con pagina entre 1 y {total_paginas}",
            )
        inicio = (args.pagina - 1) * chars_por_pagina
        cuerpo = texto[inicio : inicio + chars_por_pagina]
        return (
            f"[{args.doc_id} — página {args.pagina} de {total_paginas}]\n{cuerpo}"
        )

    herramientas = [
        Herramienta(
            nombre="buscar_corpus",
            descripcion=(
                "Busca fragmentos relevantes en el corpus normativo chileno por "
                "palabras clave (BM25). Devuelve hasta k fragmentos con su "
                "identificador entre corchetes en formato 'archivo.txt#n'."
            ),
            args_model=ArgsBuscar,
            fn=buscar,
        ),
        Herramienta(
            nombre="leer_norma",
            descripcion=(
                "Lee una página del texto completo de un documento del corpus. "
                f"Cada página son {chars_por_pagina} caracteres. Usalo cuando el "
                "fragmento de búsqueda no alcance para responder."
            ),
            args_model=ArgsLeer,
            fn=leer,
        ),
        Herramienta(
            nombre="responder",
            descripcion=(
                "Entrega la respuesta final y cierra la tarea. 'docs_citados' son "
                "los nombres de archivo del corpus que sustentan la respuesta; "
                "dejalo vacío si la información no consta en el corpus."
            ),
            args_model=ArgsResponder,
            fn=lambda args: "",
        ),
    ]

    if con_grafo:
        grafo = cargar_grafo_normativo()
        tipos_validos = sorted(t.value for t in TipoRelacion)

        def vecinos(args: ArgsVecinos) -> str:
            if args.doc_id not in grafo:
                cercanos = get_close_matches(args.doc_id, sorted(grafo.nodes), n=3, cutoff=0.4)
                raise ToolError(
                    esperado="un doc_id presente en el grafo normativo",
                    recibido=args.doc_id,
                    siguiente_paso=(
                        f"probá con uno de estos: {', '.join(cercanos)}"
                        if cercanos
                        else "usá 'buscar_corpus' para ubicar el documento primero"
                    ),
                )
            if args.tipo_relacion not in tipos_validos:
                raise ToolError(
                    esperado=f"tipo_relacion en {tipos_validos}",
                    recibido=args.tipo_relacion,
                    siguiente_paso=f"volvé a llamar usando uno de: {', '.join(tipos_validos)}",
                )
            aristas = (
                grafo.out_edges(args.doc_id, data=True)
                if args.direccion == "out"
                else grafo.in_edges(args.doc_id, data=True)
            )
            salida = [
                (v if args.direccion == "out" else u, data.get("fundamento", ""))
                for u, v, data in aristas
                if data.get("tipo") == args.tipo_relacion
            ]
            if not salida:
                return (
                    f"Sin relaciones '{args.tipo_relacion}' con dirección "
                    f"'{args.direccion}' para {args.doc_id}."
                )
            lineas = []
            for otro, fund in salida:
                arista = (
                    f"{args.doc_id} --{args.tipo_relacion}--> {otro}"
                    if args.direccion == "out"
                    else f"{otro} --{args.tipo_relacion}--> {args.doc_id}"
                )
                # El fundamento es la cita literal que sustenta la arista
                # (`05 §2`). Sin él, el agente cita el grafo; con él, cita
                # la fuente — que es la doctrina de trazabilidad del repo.
                lineas.append(arista + (f"\n    fundamento: «{fund}»" if fund else ""))
            return "\n".join(lineas)

        herramientas.insert(
            2,
            Herramienta(
                nombre="vecinos_grafo",
                descripcion=(
                    "Recorre el grafo normativo auditado: devuelve las normas "
                    f"relacionadas con un documento. tipo_relacion ∈ {tipos_validos}. "
                    "direccion='out' responde '¿a qué normas afecta este documento?'; "
                    "'in' responde '¿qué normas afectan a este documento?'."
                ),
                args_model=ArgsVecinos,
                fn=vecinos,
            ),
        )

    return ToolRegistry(herramientas)
