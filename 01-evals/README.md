# 01 — Evaluación de Sistemas IA

Masterclass sobre diseño, implementación e interpretación de evaluaciones (evals)
para sistemas basados en LLMs, con foco en aplicaciones RAG sobre corpus regulatorio
y fiscal chileno.

## Estado: En progreso

## Secciones

| #  | Título                                | Doc                                         | Código                                  | Estado     |
|----|---------------------------------------|---------------------------------------------|-----------------------------------------|------------|
| 00 | Plan maestro                          | [theory/00-plan.md](theory/00-plan.md)      | —                                       | Terminado  |
| 01 | Por qué evals                         | [theory/01-por-que-evals.md](theory/01-por-que-evals.md) | [code/demo-fallos-silenciosos.py](code/demo-fallos-silenciosos.py) | Terminado  |
| 02 | Taxonomía                             | theory/02-taxonomia.md                      | —                                       | Pendiente  |
| 03 | Análisis de errores                   | theory/03-analisis-errores.md               | —                                       | Pendiente  |
| 04 | Golden datasets                       | theory/04-golden-datasets.md                | code/eval-golden-dataset.py             | Pendiente  |
| 05 | Métricas de retrieval                 | theory/05-metricas-retrieval.md             | code/eval-metricas-retrieval.py         | Pendiente  |
| 06 | Métricas de generación                | theory/06-metricas-generacion.md            | code/eval-metricas-generacion.py        | Pendiente  |
| 07 | LLM-as-judge                          | theory/07-llm-as-judge.md                   | code/eval-judge-sesgos.py               | Pendiente  |
| 08 | Estadística para sistemas estocásticos | theory/08-estadistica-estocastica.md       | code/eval-bootstrap.py                  | Pendiente  |
| 09 | Regresiones y CI                      | theory/09-regresiones-ci.md                 | code/eval-harness.py                    | Pendiente  |
| 10 | Costo, latencia y frontera de Pareto  | theory/10-costo-pareto.md                   | —                                       | Pendiente  |
| 11 | Online evals                          | theory/11-online-evals.md                   | —                                       | Pendiente  |
| 12 | Bonus: dominios alto-stake            | theory/12-dominios-alto-stake.md            | —                                       | Pendiente  |

## Cómo ejecutar código

```bash
uv run python 01-evals/code/demo-fallos-silenciosos.py
```

## Datos

- Corpus regulatorio: `shared/corpus_chileno/`
- Golden datasets y resultados: `01-evals/examples/`

Ver [AGENTS.md](../AGENTS.md) para convenciones completas.
