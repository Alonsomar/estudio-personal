# 07 — Del grafo al retrieval: GraphRAG y su economía

## Qué se mide

GraphRAG agrega un paso de indexación: detectar comunidades del grafo y
resumir cada una con un LLM. El script
[`07-graphrag-economia.py`](../code/07-graphrag-economia.py) reproduce ese
paso sobre la ontología auditada, no sobre cifras copiadas de otra fuente.

Las normas, relaciones y comunidades se ordenan antes de construir cada
prompt. La clave del caché v2 incluye modelo, prompt completo, esquema,
temperatura y réplica. Un *cache miss* es error en modo offline; la API solo
se habilita con `--allow-api`.

## Resultado reproducido

```
Louvain: 5 comunidades sobre 38 nodos / 69 aristas
tamaños: 12, 8, 8, 6 y 4
llamadas de generación: 5
tokens históricos in/out: 3.752 / 504
costo histórico: USD 0,0009
```

Los cinco grupos cubren tributación, educación, compras públicas, lobby y
probidad, y presupuesto de Salud. El número se deriva del grafo: no es un
parámetro narrativo.

![Comunidades detectadas](../diagrams/comunidades-graphrag.png)

La corrida actual y el uso histórico están separados. Al ejecutar offline,
las llamadas y el costo de la corrida son cero, aunque se sigue mostrando el
uso que generó el caché.

## Qué no se publica

La versión anterior atribuía USD 33.000 a la indexación de un “dataset legal
de 5 GB” y una reducción posterior al 0,1%. La auditoría no encontró una
fuente primaria que confirmara conjuntamente dominio, tamaño y magnitud, por
lo que esas cifras se retiraron. La única conclusión cuantitativa vigente es
la reproducida sobre este corpus.

## Grafo simple frente a resumen de comunidades

Las consultas estructurales puntuales no necesitan GraphRAG:

```
Ley 21.210 --MODIFICA--> 2 normas          $0
dependencias entrantes de Ley 20.248: 7    $0
```

El resumen de comunidades sirve para navegación temática global. Para una
pregunta con origen, dirección, tipos y número de saltos definidos, recorrer
el grafo simple conserva más precisión y no llama a un modelo.

## Regla de decisión

- Consulta de uno a pocos saltos: tabla de relaciones o grafo simple.
- Dependencia transitiva: recorrido de grafo bajo demanda.
- *Sensemaking* global: comunidades y resúmenes pueden justificar su costo.
- En todos los casos: caché versionado, API opt-in y costo histórico
  persistido.

## Conexiones

- `§2` aporta las aristas auditadas.
- `§5` comparte el contrato `LLMCacheEntry`.
- `§8` mide si expandir el ranking con el grafo mejora retrieval.
- `§9` usa preguntas estructurales congeladas, no resúmenes de comunidad.
