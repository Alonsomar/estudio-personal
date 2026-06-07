# 03 — Patrones de Producción

Masterclass sobre las capas que hay que poner alrededor de un sistema RAG
para que sobreviva en producción: arquitectura de servicio, gestión de
prompts, caching multinivel, observabilidad, reliability, despliegue,
versionado de modelos, online evals, costo, seguridad e incidentes.

El RAG de **02-retrieval** y las métricas de **01-evals** son los insumos.
Aquí no cambiamos el RAG ni las métricas — los envolvemos en lo que
producción exige.

## Estado: En progreso

Plan maestro escrito; secciones 1 a 10 terminadas. Ver
[theory/00-plan.md](theory/00-plan.md) para el plan completo, dependencias
y decisiones técnicas tomadas (stack FastAPI + wrappers propios, despliegue
nivel B, cost monitoring real con caché).

## Secciones

| #  | Título                            | Doc                                                       | Código                                                | Estado     |
|----|-----------------------------------|-----------------------------------------------------------|-------------------------------------------------------|------------|
| 00 | Plan maestro                      | [theory/00-plan.md](theory/00-plan.md)                    | —                                                     | Terminado  |
| 01 | Salto a producción                | [theory/01-salto-a-produccion.md](theory/01-salto-a-produccion.md) | [code/01-demo-prod-vs-demo.py](code/01-demo-prod-vs-demo.py) | Terminado  |
| 02 | Arquitectura de servicio          | [theory/02-arquitectura-servicio.md](theory/02-arquitectura-servicio.md) | [code/02-fastapi-rag.py](code/02-fastapi-rag.py) + [code/prod_lib.py](code/prod_lib.py) | Terminado  |
| 03 | Gestión de prompts                | [theory/03-gestion-prompts.md](theory/03-gestion-prompts.md) | [code/03-prompt-registry.py](code/03-prompt-registry.py) + [prod_lib.py](code/prod_lib.py) | Terminado  |
| 04 | Caching multinivel                | [theory/04-caching-multinivel.md](theory/04-caching-multinivel.md) | [code/04-caching.py](code/04-caching.py) + [prod_lib.py](code/prod_lib.py) | Terminado  |
| 05 | Observabilidad y tracing          | [theory/05-observabilidad.md](theory/05-observabilidad.md) | [code/05-tracing.py](code/05-tracing.py) + [prod_lib.py](code/prod_lib.py) | Terminado  |
| 06 | Reliability (rate, retry, breaker)| [theory/06-reliability.md](theory/06-reliability.md)      | [code/06-reliability.py](code/06-reliability.py) + [prod_lib.py](code/prod_lib.py) | Terminado  |
| 07 | Despliegue y configuración        | [theory/07-despliegue-config.md](theory/07-despliegue-config.md) | [code/07-config-secrets.py](code/07-config-secrets.py) + [examples/deploy/](examples/deploy/) | Terminado  |
| 08 | Versionado de modelos             | [theory/08-versionado-modelos.md](theory/08-versionado-modelos.md) | [code/08-model-routing.py](code/08-model-routing.py) + [prod_lib.py](code/prod_lib.py) | Terminado  |
| 09 | Online evals y loop de feedback   | [theory/09-online-evals-loop.md](theory/09-online-evals-loop.md) | [code/09-online-eval-loop.py](code/09-online-eval-loop.py) + [prod_lib.py](code/prod_lib.py) | Terminado  |
| 10 | Costo en producción               | [theory/10-costo-produccion.md](theory/10-costo-produccion.md) | [code/10-cost-meter.py](code/10-cost-meter.py) + [prod_lib.py](code/prod_lib.py) | Terminado  |
| 11 | Seguridad                         | —                                                         | —                                                     | Pendiente  |
| 12 | Incidentes y postmortems          | —                                                         | —                                                     | Pendiente  |

## Cómo ejecutar código

```bash
uv run python 03-produccion/code/01-demo-prod-vs-demo.py
```

El núcleo reutilizable vive en [code/prod_lib.py](code/prod_lib.py) y va
creciendo: §2 trajo `LLMClient` (puertos + adaptadores Anthropic/OpenAI/Static)
y `RAGOrchestrator`; §3 sumó `PromptRegistry` + `PromptTemplate` + `render_safe`
(prompts versionados con hash y render seguro); §4 sumó `LRUCache`,
`ResponseCache` (componible como `LLMClient`) y `SemanticCache`; §5 sumó
`StructuredLogger`, `MetricsRegistry` y `Tracer`/`Span` (observabilidad desde
cero); §6 sumó `TokenBucket`, `retry_with_backoff`, `CircuitBreaker` y los
wrappers `RateLimited`/`Retrying`/`CircuitBreaking`/`Fallback`; §7 sumó
`ServiceSettings` (config tipada por entorno) y `scan_for_secrets`/`redact_secrets`;
§8 sumó `ShadowLLMClient` y `CanaryLLMClient` (shadow / A·B / canary con rollback);
§9 sumó `TraceSampler`, `OnlineEvalLoop` y `DriftDetector`/`psi`; §10 sumó
`CostMeter`, `BudgetGuard` y `CostAwareRouter`. Próximas secciones: seguridad
(§11), incidentes (§12).

## Datos

- Corpus regulatorio: `shared/corpus_chileno/`
- RAG construido en: `02-retrieval/`
- Golden y métricas: `01-evals/examples/`
- Outputs y traces de esta masterclass: `03-produccion/examples/`

Ver [AGENTS.md](../AGENTS.md) para convenciones completas.
