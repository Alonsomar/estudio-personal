# Auditoría posterior al cierre de B7

Fecha: **2026-08-04**  
Baseline auditado: **`91eaebf`** (`feat(ontologias): B7 — sección 9, cierre del módulo`)  
Objeto: validar de forma independiente los 20 hallazgos del diagnóstico externo
entregado después del cierre de la masterclass.

## Alcance y método

La auditoría distingue dos estados que no deben mezclarse:

1. **Cierre publicado:** el commit `91eaebf`, que contiene las afirmaciones y
   experimentos cuestionados.
2. **Working tree del 2026-08-04:** cambios locales previos a esta auditoría que
   ya corrigen parte de los problemas, pero todavía no forman una unidad cerrada
   ni mantienen sincronizadas teoría, scripts, datos y README.

La validación usó, sin llamadas a proveedores de LLM:

- comparación directa contra `shared/corpus_chileno/`;
- lectura del baseline mediante `git show HEAD:<archivo>`;
- reconstrucción de métricas de §5, §8 y §9 desde los cachés comiteados;
- ejecuciones con distintos `PYTHONHASHSEED` para §7;
- casos borde ejecutados contra `ontology_lib.py`;
- la fuente oficial del SII para la vigencia del IVA a servicios digitales;
- `uv run pytest` sobre el working tree.

No se ejecutó §7 de una forma que pudiera provocar llamadas reales a la API.

## Resultado agregado

- **19 hallazgos tienen su núcleo confirmado.** El nº 3 incluye una salvedad
  cuantitativa: la degeneración de `recall@3` está probada, pero no se reprodujo
  el valor alternativo exacto publicado por el diagnóstico.
- **1 hallazgo es mixto** (nº 10): confirma errores metodológicos de §4, pero el
  propio diagnóstico contiene dos comprobaciones por `grep` incorrectas.
- La conclusión negativa de §8 —el grafo no mejora el retrieval— **sobrevive**.
  Lo que no sobrevive es presentar el delta exactamente cero de `recall@3` como
  evidencia empírica: con `n_semillas=3` es cero por construcción.
- La conclusión cuantitativa central de §9 —**100% contra 50%**— **no
  sobrevive**. Con el ground truth mínimo corregido es 86% contra 57% de recall;
  además, `n=1` no permite una conclusión general sobre LLM contra grafo.
- La tesis cualitativa “la curación de dominio tiene valor” sigue siendo
  defendible, pero este módulo no la demuestra todavía con el diseño publicado.

## Matriz de validación

### Hallazgos graves

| # | Veredicto | Evidencia independiente | Estado del working tree |
|---|---|---|---|
| 1 | **Confirmado** | `norma-02` declara en las líneas 50–54 su relación con la Ley 20.730. La relación presentada en §5 como implícita es explícita. | La arista `norma-02 → norma-01` fue agregada, pero la narrativa de §5 sigue afirmando lo contrario. |
| 2 | **Confirmado** | El baseline asigna `2020-02-24` al art. 8 del DL 825, confundiendo publicación con vigencia. El [SII confirma vigencia desde 2020-06-01](https://www.sii.cl/noticias/2020/010620noti01aav.htm) para el IVA a servicios digitales. La vacancia de Ley 21.634 también está escrita literalmente en el corpus. | Código y tests usan 2020-06-01; la teoría de §6 y el pilar nº 4 de §9 siguen desactualizados. |
| 3 | **Confirmado con salvedad** | En `GraphExpandedRetriever`, `semillas = orden[:3]` y el retorno top-3 comienza por esas mismas semillas: `recall@3` no puede cambiar. Se reprodujo baseline 0,783 y MRR 0,765 frente a 0,763 con `n_semillas=3`. | Sin corregir. Con relaciones fuertes, esta auditoría no reprodujo el 0,750 informado para `n_semillas=1`: obtuvo 0,783. Con todas las relaciones obtuvo 0,683. El valor alternativo depende de una configuración no explicitada en el diagnóstico. |
| 4 | **Confirmado** | El caché tiene una entrada y cuatro documentos. Contra seis documentos: P=75%, R=50%; al incluir `glosa-02`: P=100%, R=57%. El grafo baseline cubre 6/7=86%. | El dataset ya incluye `glosa-02`, por lo que el grafo devuelve 7, pero §9 conserva un set hardcodeado de 6 y sigue imprimiendo “100%”. La inconsistencia ahora es visible en una sola corrida. |
| 5 | **Confirmado** | En el baseline, **0/47** fundamentos son subcadenas literales del documento origen tras normalizar acentos y espacios. También se confirmaron las omisiones centrales; el working tree identifica 21 aristas nuevas, no solo las ~15 de la heurística inicial. | El dataset tiene 38 normas/68 relaciones y el test de literalidad pasa. Las métricas, conteos y narrativas que dependían de 37/47 quedaron obsoletos. |
| 6 | **Confirmado** | En el baseline, cinco documentos están a un salto de Ley 20.248 y solo `oficio-05` está a dos, vía `oficio-01`; la cadena de tres saltos narrada en §2 no existe. `oficio-05` cita directamente la Ley 20.248 en el corpus. | Agregada la arista directa: los siete documentos de P4 están ahora a un salto. Las justificaciones multi-salto de §2, §7 y §9 no fueron reescritas. |
| 7 | **Confirmado** | El baseline devuelve `list[set[str]]`. En ocho procesos con hash seeds distintos cambiaron los prompts y hubo entre 4 y 6 misses para siete comunidades contra el caché comiteado. El caché contiene 13 entradas y no conserva tokens. | Las comunidades ahora se ordenan y los conteos del script son dinámicos. El caché histórico y los números publicados de coste/tokens todavía no son un artefacto reproducible. |

### Hallazgos medios

| # | Veredicto | Evidencia independiente | Estado del working tree |
|---|---|---|---|
| 8 | **Confirmado** | La regex del baseline toma la primera cifra: `art. 12 del DL 825` resuelve a `glosa-04`; `artículo 71` resuelve a `decreto-02`; `LEY 21210` no resuelve. | La regex fue anclada al designador de norma y los casos borde pasan tests. |
| 9 | **Confirmado** | El baseline devuelve texto original para un artículo todavía inexistente y acepta fechas arbitrarias comparadas lexicográficamente. El modelo no representa fin de vigencia/derogación a nivel de artículo. | Se distingue artículo inexistente y se validan fechas ISO. Sigue sin modelarse `valido_hasta` o derogación; la corrección es parcial respecto de la pretensión general de §6. |
| 10 | **Mixto** | Confirmado: la tabla “verificada por grep” es incompleta y el demo de riesgo usa `difflib` sobre una lista hardcodeada, no `resolver_organismo_difuso`; con el catálogo real, la forma oficial resuelve a `dccp` con score 1,0. Corregido al diagnóstico: **Dirección de Obras Hidráulicas sí aparece** en `glosa-04` (mayúsculas), y la forma completa de Dirección de Compras también aparece en más documentos de los que el diagnóstico enumera, incluida `resolucion-01`. | Sin corregir. `ChileCompra` solo aparece como `CHILECOMPRA`; afirmar literalidad sensible a mayúsculas también es impreciso. |
| 11 | **Confirmado** | Los cachés de §5 y §7 no guardan tokens. Por ello no reconstruyen los costes históricos. §5 afirma $0,0147 en teoría y “menos de un centavo” en el script; son incompatibles. | Sin corregir. El caché de §9 sí guarda 25.694/298 tokens y permite reconstruir $0,0040. |
| 12 | **Confirmado** | Los valores “antes” están hardcodeados. Al desactivar `resolver_por_numero` se reprodujeron: 20 relaciones resueltas, VP=5, FP=15, FN=18, P/R/F1=25%/21,7%/23,3%. | La veracidad queda corroborada, pero no existe aún un camino ejecutable mantenido para regenerarlos. |
| 13 | **Confirmado** | El parser devuelve para Partida 09 una asignación y 1.012.567.400, omitiendo la tabla de 10.928.003.100. El total documental es 11.940.570.500. También descarta 34.219.008 de Partida 12 por estar a nivel Subtítulo. | Sin corregir y sin tests que fijen ambos casos. |
| 14 | **Confirmado** | En el baseline, el docstring afirma que §5 evalúa `fundamento`/artículo, pero el scoring usa solo `(origen, tipo, destino)`. | El docstring local ya describe correctamente el scoring. |

### Hallazgos menores

| # | Veredicto | Comprobación |
|---|---|---|
| 15 | **Confirmado** | Sin nivel numérico hay 18 relaciones no resueltas y 12 identificadores únicos, no 20 identificadores. |
| 16 | **Confirmado** | 52/22 = 2,36: el recall se más que duplicó. |
| 17 | **Confirmado** | “este Servicio” aparece en dos circulares y una resolución, no en tres circulares. |
| 18 | **Confirmado** | La pregunta sobre distinguir tipos viene después de P4; llamarla “la cuarta” es incorrecto. |
| 19 | **Confirmado** | Desde art. 14 inclusive hay cuatro filas/modificaciones, no cinco. |
| 20 | **Confirmado** | El baseline imprime 37/47 hardcodeado. El working tree ya usa `g.number_of_nodes()` y `g.number_of_edges()`. |

## Efecto de las correcciones locales sobre resultados publicados

Las correcciones de datos no son neutrales: obligan a recalcular resultados y
reescribir teoría.

| Resultado | Cierre publicado | Working tree actual |
|---|---:|---:|
| Grafo | 37 normas / 47 relaciones | 38 normas / 68 relaciones |
| Cobertura del grafo | 92,5% | 95,0% |
| §5: ground truth en muestra | 23 relaciones | 28 relaciones |
| §5: precisión / recall / F1 | 38% / 52% / 44% | 47% / 54% / 50% |
| P4: dependientes de Ley 20.248 | 6 | 7 |
| P4: máximo de saltos dentro del conjunto | 2 | 1 |
| §8, todas las relaciones: recall@5 | 0,833 | 0,800 |

La corrida actual de §8 también imprime texto obsoleto: enumera “tres documentos
fuera” cuando muestra dos, y conserva 38% como precisión de entity linking aunque
la reconstrucción actual da 47%.

## Verificaciones ejecutadas

```text
uv run pytest tests/test_ontology_lib.py -q  -> 13 passed
uv run pytest -q                             -> 68 passed
```

Los tests nuevos validan literalidad de fundamentos, integridad del dataset,
determinismo de comunidades, resolución de números legales y fechas ISO. No
cubren todavía el parser presupuestario de §1, el diseño experimental de §8 ni la
validez estadística de §9.

## Conclusión de auditoría

B7 tiene las nueve secciones escritas, pero **no está cerrado en sentido
epistémico**: código, evidencia y relato no sostienen todavía todas las
afirmaciones de cierre. La remediación debe preservar los resultados negativos
honestos, retirar los titulares no demostrados y separar con claridad tres cosas:

- lo que el corpus declara explícitamente;
- lo que aporta la curación humana;
- lo que un experimento realmente permite inferir con su muestra y diseño.

La definición del plan completo de remediación queda fuera de esta auditoría y se
registra como tarea separada en el backlog.
