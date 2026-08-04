# 06 — Vigencia temporal y versionado normativo

## Retomando una frase que quedó pendiente

Al cerrar `02-retrieval`, la sección de casos límite dejó una advertencia
explícita sin resolver:

> "Anotar `vigencia_desde / vigencia_hasta` por doc es la parte fácil. Lo
> difícil es manejar **modificaciones parciales**: la Ley 21.210 modifica
> el artículo 8 del DL 825 pero deja los otros artículos intactos. El
> modelo 'doc reemplaza doc' se queda corto; el modelo correcto es a nivel
> artículo. Eso ya es retrieval de grano fino con versionado — el área
> donde se puede invertir mucho tiempo sin que el usuario lo note, y donde
> los productores serios diferencian."

Esta sección invierte ese tiempo. No porque sea elegante, sino porque es
el caso donde equivocarse cuesta más: una respuesta legal fundada en la
versión equivocada de una norma no es un error de recall, es un error con
consecuencias.

Código en [`ontology_lib.py`](../code/ontology_lib.py) (`ModificacionArticulo`,
`texto_vigente`, `que_sabia_el_sistema`); demo en
[`code/06-vigencia-temporal.py`](../code/06-vigencia-temporal.py).

## Dónde falla el modelo de documento, medido

`02 §9` implementó `DOC_TEMPORAL`: un diccionario que marca, por documento
completo, `vigencia_desde` y `vigencia_hasta`. Para el DL 825:

```
vigencia_desde=1974-12-31  vigencia_hasta=2020-02-23
```

Consultado en `2024-06-01`, ese modelo responde que el DL 825 **completo**
dejó de estar vigente. Es correcto para el **artículo 8º** —modificado por
la Ley 21.210 desde el 24 de febrero de 2020, incorporando el IVA a
servicios digitales—, y **falso** para cualquier otro artículo que esa ley
nunca tocó. El artículo 12º (exenciones, la fuente que cita `circular-04`)
sigue rigiendo el texto de 1974. El modelo de documento no puede
distinguir esto: solo tiene una fecha para todo el archivo.

## La misma consulta, a nivel de artículo

```
  Art.   8 del DL 825 en 2024-06-01: fuente=ley-02  (modificado, vigente desde 2020-02-24)
  Art.  12 del DL 825 en 2024-06-01: fuente=ley-01  (texto original, sin modificar)
```

Mismo documento base, misma fecha de consulta, dos respuestas distintas
según el artículo. `texto_vigente()` recorre solo las modificaciones
registradas para el artículo específico —no para el documento entero— y
devuelve la más reciente que ya regía en la fecha consultada, o el texto
original si ninguna aplica.

En la fecha con la que `02 §9` abrió el caso, `2018-06-30`, ambos artículos
coinciden en el texto original —correcto, ninguna modificación tenía
vigencia todavía—. La diferencia entre los dos modelos solo aparece cuando
la fecha consultada **cruza** la vigencia de una modificación puntual, que
es exactamente el caso que un modelo de documento entero no puede
representar.

## Un caso más rico: tres artículos, un documento, dos fechas de vigencia

El modelo de artículo no solo corrige el caso de `02 §9`; expone algo que
el corpus real ya contenía sin que nadie lo hubiera modelado. La Ley
Nº 21.634 (`ley-04`) modifica tres artículos de la Ley Nº 19.886, y **no
todos rigen desde la misma fecha**:

```
  artículo |  valido_desde |  fundamento
------------------------------------------------------------------------
         4 |    2023-12-11 |  nueva causal de inhabilidad
         5 |    2023-12-11 |  eleva el umbral de licitación pública
     7 bis |    2024-12-11 |  crea la compra ágil
```

El propio texto de la ley lo declara: *"Las modificaciones referidas al
procedimiento de compra ágil entrarán en vigencia transcurridos doce meses
desde la publicación de esta ley"* — una **vacancia legis** de un año, que
solo aplica al artículo 7º bis. Los artículos 4º y 5º rigen desde la
publicación misma.

```
Consultando ley-03 (Ley 19.886) en 2024-06-01:
  Art.     4: ley-04  (desde 2023-12-11)
  Art.     5: ley-04  (desde 2023-12-11)
  Art. 7 bis: NO EXISTE todavía — artículo nuevo, sin texto previo al que volver

Consultando ley-03 (Ley 19.886) en 2025-01-01:
  Art.     4: ley-04  (desde 2023-12-11)
  Art.     5: ley-04  (desde 2023-12-11)
  Art. 7 bis: ley-04  (desde 2024-12-11)
```

En junio de 2024, la ley que crea la compra ágil ya está publicada, ya
modificó dos artículos de la Ley 19.886 — y la compra ágil en sí **todavía
no existe legalmente**. Un producto que en esa fecha respondiera "sí, podés
usar compra ágil, la Ley 21.634 la creó" estaría citando una fuente
publicada pero no vigente. Es el modo de error más peligroso de un RAG
legal: la respuesta cita una fuente real, con número de ley correcto, y
aun así está mal — nada en el texto recuperado avisa que la fecha importa.

## Bitemporalidad: dos preguntas que se confunden

Hay una segunda dimensión temporal, distinta de la vigencia legal:
**cuándo el sistema mismo se enteró** de cada dato. `ModificacionArticulo`
separa `valido_desde` (vigencia) de `registrado_el` (registro), y esta
sección usa fechas **reales**, no inventadas para la demo: `registrado_el`
es la fecha de commit en que cada archivo entró al corpus.

```
                                 modificación | vigente desde | registrado el
----------------------------------------------------------------------------
                       ley-02 art.8 -> ley-01 |    2020-02-24 |    2026-05-27
                      ley-02 art.14 -> ley-05 |    2020-02-24 |    2026-08-03
                       ley-04 art.4 -> ley-03 |    2023-12-11 |    2026-08-03
                       ley-04 art.5 -> ley-03 |    2023-12-11 |    2026-08-03
                   ley-04 art.7 bis -> ley-03 |    2024-12-11 |    2026-08-03
```

![Vigencia legal vs. fecha de registro, con fechas reales de git](../diagrams/bitemporalidad.png)

El artículo 8º del DL 825 lleva vigente desde febrero de 2020. Esta
ontología no supo nada de él hasta mayo de 2026, cuando `02-retrieval`
expandió el corpus por primera vez — más de seis años de brecha entre
vigencia y registro. Las cinco modificaciones del artículo 14 en adelante
solo entraron el 3 de agosto de 2026, con `B6`.

Son preguntas genuinamente distintas:

```
¿Qué sabía el sistema el 2026-06-01 (antes de B6)?
  ley-02 modifica art. 8 de ley-01

¿Qué sabe el sistema hoy (después de B6, 2026-08-03)?
  ley-02 modifica art. 8 de ley-01
  ley-02 modifica art. 14 de ley-05
  ley-04 modifica art. 4 de ley-03
  ley-04 modifica art. 5 de ley-03
  ley-04 modifica art. 7 bis de ley-03
```

"¿Qué era legalmente vigente en 2020?" y "¿qué sabía este sistema en 2020?"
no son la misma pregunta. De hecho, en 2020 este sistema no existía. Un
sistema de auditoría que confunda las dos —que reporte "siempre lo
supimos" cuando el dato en realidad se cargó años después— comete el error
bitemporal clásico, y es exactamente el tipo de error que un registro de
auditoría (`03 §11`, `AuditLog`) existe para prevenir en otros contextos:
saber no solo qué pasó, sino cuándo el sistema se enteró de que había
pasado.

## Por qué esto no se resuelve duplicando documentos

La alternativa obvia al modelo de artículo —mantener una copia completa
del DL 825 por cada versión— no escala: cada modificación puntual
obligaría a copiar el documento entero, y el corpus se llenaría de texto
casi idéntico con una sola frase distinta. El modelo de esta sección evita
eso por diseño: **una arista con fecha** (`ModificacionArticulo`) reemplaza
a un documento entero duplicado. Es la misma economía que motivó el
property graph en `§3`: la información que hace falta es la relación y su
fecha, no una copia del texto completo por cada estado posible.

## Estado del arte (2026)

| Aspecto | Estado | Detalle |
|---|---|---|
| Modelos bitemporales en bases de datos | ✅ Estándar establecido | SQL:2011 (`PERIOD FOR SYSTEM_TIME`, `PERIOD FOR APPLICATION_TIME`); la distinción vigencia/registro no es nueva, viene de bases de datos temporales |
| Versionado normativo a nivel de artículo | 🟡 Área activa en legaltech | Akoma Ntoso (estándar XML para legislación) lo modela; poco extendido fuera de sistemas legislativos oficiales |
| Vacancia legis modelada explícitamente | 🔴 Rara en RAG legal | La mayoría de los sistemas tratan "publicado" y "vigente" como sinónimos; el caso del artículo 7º bis muestra por qué eso falla |
| Filtro temporal a nivel de documento (`02 §9`) | ✅ Práctica mínima viable | Correcto cuando el corpus no tiene modificaciones parciales; insuficiente en general |
| Auditoría bitemporal en sistemas de producción | 🟢 Madura en finanzas/seguros | Sectores regulados llevan décadas distinguiendo "as of" (vigencia) de "as reported" (registro) |

## Lo que viene en la próxima sección

El grafo de `§1-§6` ya tiene entidades, relaciones tipadas, identidad
resuelta y ahora vigencia temporal. La pregunta que falta es económica:
¿todo esto **paga** su costo de construcción frente al retrieval híbrido de
`02`? `§7` compara el grafo con GraphRAG (y su problema de costo conocido)
contra un filtro de metadatos estructurado — con números, no con
entusiasmo.

## Conexiones

- **`02 §9`**: el punto de partida literal de esta sección; `DOC_TEMPORAL`
  y `TemporalFilteredRetriever` se reutilizan sin modificar como el
  contraste "modelo de documento" contra el que se mide el modelo de
  artículo.
- **`§2`**: `RelacionNormativa` vive a nivel de documento; `Modificacion
  Articulo` es el nivel de grano fino que ese esquema no cubría.
- **`03 §11`**: `AuditLog` resuelve, en otro contexto, el mismo problema de
  fondo — distinguir cuándo pasó algo de cuándo el sistema se enteró.
- **`B6`** y **`02-retrieval`**: las fechas de `registrado_el` de esta
  sección son las fechas de commit reales de esos trabajos, no una demo
  inventada.
- **`§7`**: la pregunta de si este nivel de modelado se gana su costo
  frente al retrieval existente, con números.
