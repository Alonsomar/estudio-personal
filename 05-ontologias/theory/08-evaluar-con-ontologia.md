# 08 — Evaluar un sistema con ontología

## Dos evaluaciones distintas

La ontología se audita por cobertura y consistencia; el retriever se evalúa
con las 30 queries originales de `golden-retrieval.json`. Ese golden no se
modificó para favorecer al grafo y no se presenta como evidencia multi-hop.

```
Cobertura: 38/40 documentos = 95,0%
Fuera: glosa-03 y tabla-01
Ciclos MODIFICA: 0
```

El escáner determinista no encuentra en los dos documentos excluidos una
mención numerada resoluble al catálogo. No se redefine el denominador como
“documentos relevantes”.

## Retriever corregido

`GraphExpandedRetriever` usa una semilla. Produce el orden:

1. semilla BM25;
2. vecinos, por prioridad de relación, ranking BM25 y `doc_id`;
3. resto del ranking BM25.

No itera conjuntos para ordenar, no duplica documentos y, si la semilla no
tiene vecinos permitidos, conserva exactamente el baseline. Hay dos variantes:
relaciones fuertes (`MODIFICA`, `REGLAMENTA`, `INTERPRETA`) y todas las
relaciones. Se reportan `recall@3`, `recall@5` y MRR.

## Resultado sobre 30 queries

| Sistema | recall@3 | recall@5 | MRR |
|---|---:|---:|---:|
| BM25 | 0,783 | 0,833 | 0,765 |
| BM25 + fuertes | 0,783 | 0,833 | 0,765 |
| BM25 + todas | 0,750 | 0,800 | 0,754 |

La variante fuerte empata exactamente. La expansión con todas las relaciones
empeora las tres medias; sus intervalos del delta incluyen cero:

| Variante | Δrecall@3 | Δrecall@5 | ΔMRR |
|---|---:|---:|---:|
| Fuertes | +0,000 | +0,000 | +0,000 |
| Todas | −0,033 | −0,033 | −0,011 |

![Resultado por tipo de query](../diagrams/evaluacion-estratificada.png)

Las cuatro queries `multi-doc` combinan fuentes temáticamente distintas; no
requieren seguir cadenas de citas. Por eso el resultado no valida ni refuta la
recuperación multi-hop. Esa capacidad se prueba por separado con las 18
competency questions de `§9`.

## Conclusión

En este golden, la expansión fuerte no aporta valor y expandir por `CITA`
puede desplazar resultados útiles. El resultado negativo se publica sin
convertirlo en una afirmación general contra grafos.

## Conexiones

- `01 §8`: bootstrap e intervalos de confianza.
- `02-retrieval`: corpus, BM25 y golden sin cambios.
- `§9`: benchmark estructural específico, separado del de retrieval.
