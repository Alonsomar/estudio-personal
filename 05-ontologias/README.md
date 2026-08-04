# 05 — Ontologías y Representación del Conocimiento

Masterclass sobre la representación explícita de relaciones en un corpus legal
chileno. El clasificador presupuestario sirve como punto de partida; el módulo
avanza hacia un grafo normativo auditable, temporalidad y evaluación.

## Estado: Terminado — B13

La remediación posterior a la auditoría quedó incorporada. El corpus permanece en
40 documentos. La ontología v2 contiene 38 normas y 69 relaciones cuyo fundamento
es una cita literal del documento origen. Un escáner determinista exige arista para
toda mención numerada resoluble al catálogo.

Resultados vigentes:

- §1: 68 nodos del clasificador; seis asignaciones de Educación y monto de
  Subtítulo de Obras Públicas incluidos, sin doble conteo.
- §5: P/R/F1 = 39%/43%/41% sobre 28 relaciones de la muestra; ablación numérica
  ejecutable desde el mismo caché.
- §7: cinco comunidades; costo histórico USD 0,0009.
- §8: la expansión fuerte empata con BM25; todas las relaciones empeoran
  recall@3 y recall@5 en 0,033, sin diferencia detectable.
- §9: 18 preguntas × 3 réplicas. El LLM crudo obtiene F1 0,439
  [0,289; 0,594]; delta respecto del conocimiento curado −0,561
  [−0,711; −0,406]. Es una brecha de recuperación, no una prueba general de
  “foso competitivo”.

## Secciones

| # | Tema | Teoría | Código |
|---|---|---|---|
| 00 | Plan maestro | [00-plan](theory/00-plan.md) | — |
| 01 | Clasificador presupuestario | [teoría](theory/01-que-es-una-ontologia.md) | [script](code/01-ontologia-vs-grafo.py) |
| 02 | Grafo normativo | [teoría](theory/02-modelado-del-dominio.md) | [script](code/02-grafo-normativo.py) |
| 03 | Formalismo | [teoría](theory/03-cuanto-formalismo.md) | [script](code/03-cuanto-formalismo.py) |
| 04 | Identidad | [teoría](theory/04-identidad-y-llaves.md) | [script](code/04-identidad-y-llaves.py) |
| 05 | Extracción | [teoría](theory/05-extraccion-llm.md) | [script](code/05-extraccion-llm.py) |
| 06 | Temporalidad | [teoría](theory/06-vigencia-temporal.md) | [script](code/06-vigencia-temporal.py) |
| 07 | GraphRAG | [teoría](theory/07-graphrag-economia.md) | [script](code/07-graphrag-economia.py) |
| 08 | Retrieval | [teoría](theory/08-evaluar-con-ontologia.md) | [script](code/08-evaluar-con-ontologia.py) |
| 09 | Competency questions | [teoría](theory/09-la-ontologia-como-foso.md) | [script](code/09-ontologia-como-foso.py) |

## Ejecución

Los scripts son offline por defecto:

```bash
for script in 05-ontologias/code/0[1-9]-*.py; do
  uv run python "$script"
done
```

Solo §5, §7 y §9 aceptan `--allow-api`, reservado para regenerar cachés. El
contrato `LLMCacheEntry` persiste prompt, esquema, modelo, uso y costo histórico.

## Datos

- [Ground truth normativo](examples/relaciones-manual.json)
- [Golden estructural de 18 preguntas](examples/golden-ontology.json)
- `examples/cache-extraccion-llm.json` — 10 respuestas
- `examples/cache-graphrag-comunidades.json` — 5 respuestas
- `examples/cache-foso-llm-crudo.json` — 54 respuestas
- [Auditoría y resolución](notes/01-auditoria-post-cierre.md)

Ver [AGENTS.md](../AGENTS.md) para las convenciones del repo.
