# 03 — Patrones de Producción

Masterclass sobre las capas que hay que poner alrededor de un sistema RAG
para que sobreviva en producción: arquitectura de servicio, gestión de
prompts, caching multinivel, observabilidad, reliability, despliegue,
versionado de modelos, online evals, costo, seguridad e incidentes.

El RAG de **02-retrieval** y las métricas de **01-evals** son los insumos.
Aquí no cambiamos el RAG ni las métricas — los envolvemos en lo que
producción exige.

## Estado: En progreso

Plan maestro escrito; secciones 1 y 2 terminadas. Ver
[theory/00-plan.md](theory/00-plan.md) para el plan completo, dependencias
y decisiones técnicas tomadas (stack FastAPI + wrappers propios, despliegue
nivel B, cost monitoring real con caché).

## Secciones

| #  | Título                            | Doc                                                       | Código                                                | Estado     |
|----|-----------------------------------|-----------------------------------------------------------|-------------------------------------------------------|------------|
| 00 | Plan maestro                      | [theory/00-plan.md](theory/00-plan.md)                    | —                                                     | Terminado  |
| 01 | Salto a producción                | [theory/01-salto-a-produccion.md](theory/01-salto-a-produccion.md) | [code/01-demo-prod-vs-demo.py](code/01-demo-prod-vs-demo.py) | Terminado  |
| 02 | Arquitectura de servicio          | [theory/02-arquitectura-servicio.md](theory/02-arquitectura-servicio.md) | [code/02-fastapi-rag.py](code/02-fastapi-rag.py) + [code/prod_lib.py](code/prod_lib.py) | Terminado  |
| 03 | Gestión de prompts                | —                                                         | —                                                     | Pendiente  |
| 04 | Caching multinivel                | —                                                         | —                                                     | Pendiente  |
| 05 | Observabilidad y tracing          | —                                                         | —                                                     | Pendiente  |
| 06 | Reliability (rate, retry, breaker)| —                                                         | —                                                     | Pendiente  |
| 07 | Despliegue y configuración        | —                                                         | —                                                     | Pendiente  |
| 08 | Versionado de modelos             | —                                                         | —                                                     | Pendiente  |
| 09 | Online evals y loop de feedback   | —                                                         | —                                                     | Pendiente  |
| 10 | Costo en producción               | —                                                         | —                                                     | Pendiente  |
| 11 | Seguridad                         | —                                                         | —                                                     | Pendiente  |
| 12 | Incidentes y postmortems          | —                                                         | —                                                     | Pendiente  |

## Cómo ejecutar código

```bash
uv run python 03-produccion/code/01-demo-prod-vs-demo.py
```

El núcleo reutilizable vive en [code/prod_lib.py](code/prod_lib.py) y va
creciendo: §2 trajo `LLMClient` (puertos + adaptadores Anthropic/OpenAI/Static)
y `RAGOrchestrator`. Próximas secciones agregan caché (§4), reliability (§6),
model router (§8), cost meter (§10).

## Datos

- Corpus regulatorio: `shared/corpus_chileno/`
- RAG construido en: `02-retrieval/`
- Golden y métricas: `01-evals/examples/`
- Outputs y traces de esta masterclass: `03-produccion/examples/`

Ver [AGENTS.md](../AGENTS.md) para convenciones completas.
