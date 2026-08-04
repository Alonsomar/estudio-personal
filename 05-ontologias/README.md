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

## Estado: En revisión posterior al cierre

Plan maestro y las 9 secciones están escritas (00-09). Prerrequisito cumplido:
`B6` expandió el corpus de 16 a 40 documentos en cuatro clusters con cadenas
de citas verificadas, diseñadas específicamente como insumo para el grafo de
este módulo.

Una auditoría posterior al commit de cierre confirmó problemas en el ground
truth, la reproducibilidad y el diseño de §8–§9. La conclusión negativa de §8
—el grafo no mejora el retrieval— sobrevive, pero `recall@3` era invariante por
construcción. El titular “100% contra 50%” de §9 no sobrevive al ground truth
corregido ni permite generalizar con `n=1`.

La evidencia completa y la separación entre baseline publicado y correcciones
locales está en
[`notes/01-auditoria-post-cierre.md`](notes/01-auditoria-post-cierre.md). La
remediación se registra como `B13`; hasta cerrarla, los números de este README y
de las secciones deben considerarse históricos, no resultados finales.

## Secciones

| #  | Título                                       | Doc | Código | Estado    |
|----|-----------------------------------------------|-----|--------|-----------|
| 00 | Plan maestro                                  | [theory/00-plan.md](theory/00-plan.md) | — | Terminado |
| 01 | Qué es una ontología y por qué ya construiste varias | [theory/01-que-es-una-ontologia.md](theory/01-que-es-una-ontologia.md) | [code/01-ontologia-vs-grafo.py](code/01-ontologia-vs-grafo.py) + [ontology_lib.py](code/ontology_lib.py) | Terminado |
| 02 | Modelado del dominio regulatorio chileno      | [theory/02-modelado-del-dominio.md](theory/02-modelado-del-dominio.md) | [code/02-grafo-normativo.py](code/02-grafo-normativo.py) | Terminado |
| 03 | Cuánto formalismo comprar                     | [theory/03-cuanto-formalismo.md](theory/03-cuanto-formalismo.md) | [code/03-cuanto-formalismo.py](code/03-cuanto-formalismo.py) | Terminado |
| 04 | Identidad y llaves canónicas                  | [theory/04-identidad-y-llaves.md](theory/04-identidad-y-llaves.md) | [code/04-identidad-y-llaves.py](code/04-identidad-y-llaves.py) | Terminado |
| 05 | Extraer la ontología del corpus               | [theory/05-extraccion-llm.md](theory/05-extraccion-llm.md) | [code/05-extraccion-llm.py](code/05-extraccion-llm.py) | Terminado |
| 06 | Vigencia temporal y versionado normativo      | [theory/06-vigencia-temporal.md](theory/06-vigencia-temporal.md) | [code/06-vigencia-temporal.py](code/06-vigencia-temporal.py) | Terminado |
| 07 | Del grafo al retrieval: GraphRAG y su economía | [theory/07-graphrag-economia.md](theory/07-graphrag-economia.md) | [code/07-graphrag-economia.py](code/07-graphrag-economia.py) | Terminado |
| 08 | Evaluar un sistema con ontología              | [theory/08-evaluar-con-ontologia.md](theory/08-evaluar-con-ontologia.md) | [code/08-evaluar-con-ontologia.py](code/08-evaluar-con-ontologia.py) | Terminado |
| 09 | La ontología como foso competitivo (+ governance EU AI Act) | [theory/09-la-ontologia-como-foso.md](theory/09-la-ontologia-como-foso.md) | [code/09-ontologia-como-foso.py](code/09-ontologia-como-foso.py) | Terminado |

## Nota de método

El grafo tuvo que **ganarse el lugar**. Todo lo que aportó se midió contra
el retrieval híbrido de `02`, reutilizando `golden-retrieval.json` sin
modificar y el aparato estadístico de `01 §8` (deltas + IC bootstrap). El
grafo no ganó en `§8` — y el resultado negativo se publicó igual, misma
disciplina que `04 §4` aplicó a self-hosting. `§9` cierra mostrando dónde
sí gana: preguntas de dependencia transitiva que el texto libre no puede
responder sin la curación de dominio de `§1-§6`.

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
brecha en vez de solo describirla); §4 sumó `Organismo`, `resolver_organismo`
(diccionario) y `resolver_organismo_difuso` (similitud de secuencia,
fallback) — pipeline de entity resolution de dos niveles con orden
justificado por un falso positivo real y medido; §5 sumó `LLMExtractor`
(structured output vía Pydantic, caché en disco), `resolver_por_numero`
(nivel intermedio de resolución específico de identificadores legales) y
`resolver_identificador_norma` (pipeline de tres niveles); §6 sumó
`ModificacionArticulo`, `texto_vigente` y `que_sabia_el_sistema` — vigencia
a nivel de artículo y bitemporalidad (vigencia legal vs. fecha de registro,
esta última tomada de commits reales de git); §7 sumó `comunidades_del_grafo`
(Louvain) y `GraphRAGIndexer` (structured output, caché en disco) — réplica
minimalista del paso de indexación de GraphRAG para medir su costo real
sobre este corpus. §8 y §9 no sumaron componentes nuevos a `ontology_lib.py`:
reutilizan todo lo anterior. §8 agregó `GraphExpandedRetriever` en el propio
script de demo, sometiendo el grafo a `golden-retrieval.json` con el aparato
de `01 §8` — **resultado negativo, publicado igual**: recall@3/@5 sin
diferencia contra BM25 solo, incluso en queries multi-doc. §9 cerró con un
experimento directo (LLM sin grafo vs. grafo, misma competency question de
§2): 50% de recall sin curación contra 100% con ella, y un documento de
governance sobre reclasificación *provider*/*deployer* bajo la EU AI Act,
con fechas verificadas contra el Diario Oficial de la UE (Reglamento (UE)
2026/1744).

## Datos

- Corpus regulatorio: `shared/corpus_chileno/` (40 documentos, `B6`).
- Golden de retrieval (§8, sin modificar): `02-retrieval/examples/golden-retrieval.json`.
- Verdad fundamental (§2, usada como referencia en §5, §8 y §9): [examples/relaciones-manual.json](examples/relaciones-manual.json).
- Caché de extracción LLM (§5): `examples/cache-extraccion-llm.json` — con caché poblada, el script corre sin API key.
- Caché de resúmenes de comunidad (§7): `examples/cache-graphrag-comunidades.json`.
- Caché del experimento de cierre (§9): `examples/cache-foso-llm-crudo.json`.

Ver [AGENTS.md](../AGENTS.md) para convenciones completas.
