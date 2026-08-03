# 05 — Ontologías y Representación del Conocimiento

Masterclass sobre la segunda de las cuatro capas invariantes de un producto
sobre corpus legal (ver [README del repo](../README.md)): cómo formalizar
las relaciones que hoy viven implícitas en el texto —qué norma modifica a
cuál, qué reglamenta a qué, qué cita a qué— en una estructura explícita que
un sistema puede recorrer.

No es una disciplina ajena al autor: el clasificador presupuestario chileno
(partida → capítulo → programa → subtítulo → ítem → asignación) ya es una
ontología. Esta masterclass le pone nombre técnico a una práctica que el
autor ya ejerce, y la conecta con el retrieval construido en `02` y las
herramientas de IA del resto del repo.

## Estado: En curso

Plan maestro terminado; 9 secciones en desarrollo. Prerrequisito cumplido:
`B6` expandió el corpus de 16 a 40 documentos en cuatro clusters con cadenas
de citas verificadas, diseñadas específicamente como insumo para el grafo de
este módulo.

## Secciones

| #  | Título                                       | Doc | Código | Estado    |
|----|-----------------------------------------------|-----|--------|-----------|
| 00 | Plan maestro                                  | [theory/00-plan.md](theory/00-plan.md) | — | Terminado |
| 01 | Qué es una ontología y por qué ya construiste varias | [theory/01-que-es-una-ontologia.md](theory/01-que-es-una-ontologia.md) | [code/01-ontologia-vs-grafo.py](code/01-ontologia-vs-grafo.py) + [ontology_lib.py](code/ontology_lib.py) | Terminado |
| 02 | Modelado del dominio regulatorio chileno      | [theory/02-modelado-del-dominio.md](theory/02-modelado-del-dominio.md) | [code/02-grafo-normativo.py](code/02-grafo-normativo.py) | Terminado |
| 03 | Cuánto formalismo comprar                     | [theory/03-cuanto-formalismo.md](theory/03-cuanto-formalismo.md) | [code/03-cuanto-formalismo.py](code/03-cuanto-formalismo.py) | Terminado |
| 04 | Identidad y llaves canónicas                  | — | — | Pendiente |
| 05 | Extraer la ontología del corpus               | — | — | Pendiente |
| 06 | Vigencia temporal y versionado normativo      | — | — | Pendiente |
| 07 | Del grafo al retrieval: GraphRAG y su economía | — | — | Pendiente |
| 08 | Evaluar un sistema con ontología              | — | — | Pendiente |
| 09 | La ontología como foso competitivo            | — | — | Pendiente |

## Nota de método

El grafo tiene que **ganarse el lugar**. Todo lo que aporte se mide contra
el retrieval híbrido de `02`, reutilizando `golden-retrieval.json` sin
modificar y el aparato estadístico de `01 §8` (deltas + IC bootstrap). Si el
grafo no gana, el resultado negativo se publica igual — la misma disciplina
que `04 §4` aplicó a self-hosting.

## Cómo ejecutar código

```bash
uv run python 05-ontologias/code/01-ontologia-vs-grafo.py
```

Property graph con `networkx` + esquema Pydantic, sin base de grafos
dedicada ni razonador OWL — decisión justificada en §3. La extracción usa
LLM real con caché; el costo se mide con la aritmética de `04 §1`.

El núcleo reutilizable vive en [code/ontology_lib.py](code/ontology_lib.py):
§1 trajo `NodoClasificador` y `parse_clasificador_presupuestario` (el
clasificador presupuestario chileno como property graph, parseado desde
`shared/corpus_chileno/glosa-*.txt`); §2 sumó `Norma`, `RelacionNormativa`,
`TipoRelacion`, `build_grafo_normativo`, `vecinos_por_relacion` y
`alcance_transitivo` — el vocabulario de relaciones tipadas para el corpus
normativo completo, con datos curados a mano en
[examples/relaciones-manual.json](examples/relaciones-manual.json) (37
normas, 47 relaciones con fundamento textual verificable — la verdad
fundamental que §5 usará para medir la extracción automática); §3 sumó
`ConceptoSKOS`, `esquema_skos_tipos_norma` y `es_subconcepto_de` (el nivel
de formalismo inmediatamente anterior al property graph, para medir la
brecha en vez de solo describirla).

## Datos

- Corpus regulatorio: `shared/corpus_chileno/` (40 documentos, `B6`).
- Golden de retrieval (§8, sin modificar): `02-retrieval/examples/golden-retrieval.json`.

Ver [AGENTS.md](../AGENTS.md) para convenciones completas.
