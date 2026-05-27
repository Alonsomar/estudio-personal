# 02 — Information Retrieval

Masterclass sobre information retrieval entendido como disciplina —anterior y
posterior a los LLMs— aplicada a corpus regulatorio y fiscal chileno. Cubre desde
IR clásico (BM25, TF-IDF) hasta embeddings densos, hybrid search, chunking, query
rewriting, reranking, retrieval estructurado y evaluación aislada de retrieval.

## Estado: En progreso

Estructura completa definida; secciones 1 a 3 terminadas. Ver
[theory/00-plan.md](theory/00-plan.md) para el plan maestro, el árbol propuesto y
las decisiones técnicas pendientes (stack de embeddings, expansión del corpus).

## Secciones

| #  | Título                                  | Doc                                                       | Código                                        | Estado     |
|----|-----------------------------------------|-----------------------------------------------------------|-----------------------------------------------|------------|
| 00 | Plan maestro                            | [theory/00-plan.md](theory/00-plan.md)                    | —                                             | Terminado  |
| 01 | IR pre-LLM: BM25 y TF-IDF               | [theory/01-ir-pre-llm.md](theory/01-ir-pre-llm.md)        | [code/01-ir-clasico.py](code/01-ir-clasico.py) | Terminado  |
| 02 | Embeddings densos: geometría y fallos   | [theory/02-embeddings-densos.md](theory/02-embeddings-densos.md) | [code/02-embeddings-geometria.py](code/02-embeddings-geometria.py) | Terminado  |
| 03 | Hybrid search: sparse + dense (RRF)     | [theory/03-hybrid-search.md](theory/03-hybrid-search.md)  | [code/03-hybrid-rrf.py](code/03-hybrid-rrf.py) | Terminado  |
| 04 | Chunking serio para documentos legales  | —                                                         | —                                             | Pendiente  |
| 05 | Query rewriting (HyDE, multi-query…)    | —                                                         | —                                             | Pendiente  |
| 06 | Reranking (cross-encoders, ColBERT)     | —                                                         | —                                             | Pendiente  |
| 07 | Metadata filtering y retrieval estructurado | —                                                     | —                                             | Pendiente  |
| 08 | Evaluación de retrieval aislada         | —                                                         | —                                             | Pendiente  |
| 09 | Casos límite del dominio regulatorio    | —                                                         | —                                             | Pendiente  |

## Cómo ejecutar código

```bash
uv run python 02-retrieval/code/01-ir-clasico.py
```

El núcleo reutilizable (BM25, TF-IDF, tokenizer y, en secciones futuras, fusión y
rerankers) vive en [code/retrieval_lib.py](code/retrieval_lib.py); los scripts
demo numerados lo importan.

## Datos

- Corpus regulatorio: `shared/corpus_chileno/`
- Golden dataset reutilizado: `01-evals/examples/golden-dataset-rag-fiscal.json`
- Diagramas generados: `02-retrieval/diagrams/`

Ver [AGENTS.md](../AGENTS.md) para convenciones completas.
