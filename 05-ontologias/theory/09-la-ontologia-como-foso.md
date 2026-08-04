# 09 — Ontología curada, competency questions y ventaja de dominio

## El experimento corregido

La versión anterior comparaba una sola pregunta contra la propia salida del
grafo y titulaba “100% vs. 50%”. Esa comparación no estimaba generalización.
B13 la reemplaza por [`golden-ontology.json`](../examples/golden-ontology.json):

- 8 preguntas de un salto;
- 7 preguntas multi-hop, cada una con al menos un camino de dos saltos;
- 3 negativas con respuesta vacía.

Cada ítem fija texto, categoría, nodo objetivo, dirección, tipos permitidos,
máximo de saltos, documentos esperados y caminos testigo. Los esperados no se
generan durante la evaluación.

## Dos objetos con roles diferentes

La “ontología curada + competency question compilada” reproduce los 18 sets
esperados. Ese 100% es una comprobación de consistencia interna, no una
estimación independiente.

El comparador sí es estocástico: `gpt-4o-mini`, temperatura 0, corpus completo,
tres réplicas por pregunta. Los nombres inexistentes se conservan como falsos
positivos. Se calculan precisión, recall, F1 y *exact-set match* por réplica;
luego se promedia cada pregunta y el bootstrap se hace sobre 18 preguntas, no
sobre 54 llamadas.

## Resultado reproducido

| Métrica | Media | IC95% bootstrap |
|---|---:|---:|
| Precisión | 0,436 | [0,288; 0,581] |
| Recall | 0,495 | [0,324; 0,674] |
| F1 | 0,439 | [0,289; 0,594] |
| Exact-set match | 0,148 | [0,000; 0,315] |

La desviación estándar media de F1 entre réplicas es 0,034. El delta de F1
respecto del conocimiento curado es −0,561, IC95% [−0,711; −0,406]. Como el
intervalo excluye cero, hay una brecha detectable de recuperación respecto del
conocimiento curado. No se afirma que el grafo tenga “100% de precisión” como
sistema independiente ni se revive el titular histórico.

La corrida controlada hizo 54 llamadas, consumió 1.386.351 tokens de entrada y
7.442 de salida, y costó históricamente USD 0,2124. Las corridas normales son
offline, hacen cero llamadas y leen el caché v2.

## Qué significa —y qué no—

El resultado muestra que, para estas preguntas estructurales y este corpus, un
modelo que lee texto crudo recupera menos del conocimiento explícitamente
curado. No demuestra un foso comercial por sí solo: no mide disposición a pagar,
costos de sustitución ni desempeño fuera de estas 18 preguntas. La ventaja
observada está en el activo curado y auditable, no en la tecnología de grafos.

## Governance: fine-tuning y EU AI Act

El Artículo 25 del Reglamento (UE) 2024/1689 puede reclasificar a un *deployer*
como *provider* si pone su marca sobre un sistema de alto riesgo, altera su
propósito previsto o realiza una modificación sustancial. No existe en el texto
un umbral numérico universal que permita afirmar que todo fine-tuning o toda
destilación activa automáticamente esa reclasificación; requiere análisis del
caso y del uso previsto.

El Reglamento (UE) 2026/1744 fue publicado el 24 de julio de 2026 y entró en
vigor el 27 de julio de 2026. Difirió obligaciones para sistemas del Anexo III
al 2 de diciembre de 2027 y del Anexo I al 2 de agosto de 2028
([EUR-Lex, texto oficial](https://eur-lex.europa.eu/eli/reg/2026/1744/oj/eng)).
Ese calendario no reemplaza el análisis de roles del Artículo 25.

> Esta sección es orientación de governance, no asesoría jurídica.

## Cierre

El módulo deja tres resultados distintos y compatibles: una ontología literal y
auditable; ninguna mejora del retrieval general con expansión fuerte y un leve
empeoramiento al expandir todas las citas; y una brecha detectable del LLM crudo
en preguntas estructurales. La tesis defendible es más estrecha que “el grafo es
un foso”: el conocimiento de dominio curado permite responder y auditar preguntas
que el texto crudo recupera de forma incompleta.
