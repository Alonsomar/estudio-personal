# 02 — Information Retrieval

Masterclass sobre information retrieval entendido como disciplina —anterior y
posterior a los LLMs— aplicada a corpus regulatorio y fiscal chileno. Cubre desde
IR clásico (BM25, TF-IDF) hasta embeddings densos, hybrid search, chunking, query
rewriting, reranking, retrieval estructurado y evaluación aislada de retrieval.

## Estado: Terminada

Masterclass completa: secciones 1 a 9 terminadas. Ver
[theory/00-plan.md](theory/00-plan.md) para el plan maestro, el árbol propuesto y
las decisiones técnicas pendientes (stack de embeddings, expansión del corpus).

## Secciones

| #  | Título                                  | Doc                                                       | Código                                        | Estado     |
|----|-----------------------------------------|-----------------------------------------------------------|-----------------------------------------------|------------|
| 00 | Plan maestro                            | [theory/00-plan.md](theory/00-plan.md)                    | —                                             | Terminado  |
| 01 | IR pre-LLM: BM25 y TF-IDF               | [theory/01-ir-pre-llm.md](theory/01-ir-pre-llm.md)        | [code/01-ir-clasico.py](code/01-ir-clasico.py) | Terminado  |
| 02 | Embeddings densos: geometría y fallos   | [theory/02-embeddings-densos.md](theory/02-embeddings-densos.md) | [code/02-embeddings-geometria.py](code/02-embeddings-geometria.py) | Terminado  |
| 03 | Hybrid search: sparse + dense (RRF)     | [theory/03-hybrid-search.md](theory/03-hybrid-search.md)  | [code/03-hybrid-rrf.py](code/03-hybrid-rrf.py) | Terminado  |
| 04 | Chunking serio para documentos legales  | [theory/04-chunking.md](theory/04-chunking.md)            | [code/04-chunking-estrategias.py](code/04-chunking-estrategias.py) | Terminado  |
| 05 | Query rewriting (HyDE, multi-query…)    | [theory/05-query-rewriting.md](theory/05-query-rewriting.md) | [code/05-query-rewriting.py](code/05-query-rewriting.py) | Terminado  |
| 06 | Reranking (cross-encoders, ColBERT)     | [theory/06-reranking.md](theory/06-reranking.md)          | [code/06-reranking.py](code/06-reranking.py)  | Terminado  |
| 07 | Metadata filtering y retrieval estructurado | [theory/07-metadata-estructurado.md](theory/07-metadata-estructurado.md) | [code/07-sql-vs-vectores.py](code/07-sql-vs-vectores.py) | Terminado  |
| 08 | Evaluación de retrieval aislada         | [theory/08-evaluacion-retrieval.md](theory/08-evaluacion-retrieval.md) | [code/08-benchmark-retrievers.py](code/08-benchmark-retrievers.py) | Terminado  |
| 09 | Casos límite del dominio regulatorio    | [theory/09-casos-limite-dominio.md](theory/09-casos-limite-dominio.md) | [code/09-casos-limite.py](code/09-casos-limite.py) | Terminado  |

## Cómo ejecutar código

```bash
uv run python 02-retrieval/code/01-ir-clasico.py
```

El núcleo reutilizable (BM25, TF-IDF, tokenizer y, en secciones futuras, fusión y
rerankers) vive en [code/retrieval_lib.py](code/retrieval_lib.py); los scripts
demo numerados lo importan.

## Datos

- Corpus regulatorio: `shared/corpus_chileno/`
- Golden doc-level reutilizado: `01-evals/examples/golden-dataset-rag-fiscal.json`
- Golden chunk-level (§8): `02-retrieval/examples/golden-retrieval.json`
- Resultados del benchmark (§8): `02-retrieval/examples/benchmark-retrievers.json`
- Diagramas generados: `02-retrieval/diagrams/`

Ver [AGENTS.md](../AGENTS.md) para convenciones completas.
