# 09 — La ontología como foso competitivo

## Después de un resultado negativo, la pregunta que sigue

`§8` publicó, con el mismo rigor que cualquier resultado positivo, que el
grafo no mejora el recall sobre `golden-retrieval.json`. Sería fácil leer
eso como el cierre pesimista del módulo. No lo es, y esta sección explica
por qué con un experimento, no solo con un argumento.

La pregunta que `§8` no hizo —porque el golden de `02-retrieval` no la
contiene— es la que sí importa para el foso: **¿qué tan bien responde un
LLM, sin la curación de este módulo, las preguntas que la curación sí
responde?**

Código en [`code/09-ontologia-como-foso.py`](../code/09-ontologia-como-foso.py).

## El experimento: la misma pregunta, con y sin curación

La competency question P4 de `§2` —"¿qué documentos dependen, directa o
indirectamente, de la Ley Nº 20.248 (SEP)?"— tiene una verdad fundamental
de seis documentos, verificada por lectura directa del corpus. Se le hizo
la **misma pregunta**, en español natural, a `gpt-4o-mini` con el corpus
completo (40 documentos, ~25.700 tokens) puesto directamente en el
contexto — sin grafo, sin relaciones tipadas, solo texto.

```
Verdad fundamental (§2, P4):
  decreto-01, decreto-06, do-01, ley-09, oficio-01, oficio-05

LLM sin grafo (corpus crudo en contexto) respondió:
  decreto-01, glosa-02, oficio-01, oficio-05

Precisión: 75%  (3/4)
Recall:    50%  (3/6)
```

El LLM encontró la mitad de los documentos que realmente dependen de la
Ley 20.248. Le faltaron `decreto-06` y `ley-09` —la cadena de dos saltos
del cluster de educación pública— y `do-01`, cuya mención de la SEP está
en un párrafo sobre aranceles aduaneros, lejos temáticamente de donde uno
buscaría. Con el grafo, la misma pregunta:

```
Con el grafo (§2, alcance_transitivo):
  decreto-01, decreto-06, do-01, ley-09, oficio-01, oficio-05
Precisión: 100%  Recall: 100%  (por construcción)
```

100% exacto, en menos de 5 milisegundos, sin costo. La comparación es, por
diseño, favorable al grafo —la verdad fundamental de `§2` es literalmente
la salida de esta misma función—, y aun así el punto queda claro: **la
estructura que el grafo ya tiene explícita es exactamente la que el LLM
tiene que reconstruir desde cero, en lenguaje natural, cada vez que se le
pregunta.**

## Un hallazgo que se repite hasta el final del módulo

El "falso positivo" del LLM —`glosa-02`— vale la pena mirarlo antes de
descartarlo como error. Verificado contra el texto real:

```
Glosa 04: La Subvención Escolar Preferencial se rige por la Ley
Nº 20.248 y su reglamento.
```

`glosa-02` **sí** cita la Ley 20.248, literalmente. No es un error del
LLM: es otro hueco en la curación manual de `§2`, que excluyó `glosa-02`
por ser uno de los distractores de `B6` sin revisar si, aun siendo
distractor por diseño, igual mencionaba algo real. Es el mismo patrón que
`§5` encontró con `resolucion-01` y la Ley 19.886. Que aparezca **otra
vez**, en la última sección del módulo, es la mejor demostración posible
de la lección que atraviesa todo el trabajo: **verificar contra la fuente
primaria gana, siempre, contra confiar en el artefacto curado —sea
manual o automático.**

## Por qué un modelo mejor no comoditiza esto

El experimento usó `gpt-4o-mini`, no el modelo más grande disponible. El
argumento no es que un modelo de frontera lo haría mejor —probablemente
sí—. El argumento es que hay cuatro cosas que tuvieron que existir
**antes** de que el grafo pudiera responder con 100% de precisión, y
ninguna de las cuatro es una capacidad de modelo:

1. **Alguien leyó los 40 documentos** y decidió, con criterio de dominio,
   que "oficio-05 cita a oficio-01" es una relación real (`§2`).
2. **Alguien distinguió** que `MODIFICA` y `CITA` no son la misma
   relación, con consecuencias jurídicas distintas (`§2`).
3. **Alguien resolvió** que "Dirección de Compras" y "CHILECOMPRA" son la
   misma entidad (`§4`) sin caer en el falso positivo de nombres
   institucionales parecidos ("Dirección de Educación Pública" ganaba por
   similitud de texto).
4. **Alguien supo** que el artículo 7º bis de la Ley 21.634 tiene
   vacancia legis de 12 meses (`§6`) — un hecho que ningún modelo puede
   inferir del texto sin conocimiento específico del derecho
   administrativo chileno.

Un modelo mejor hace mejor el paso de extracción (`§5`) sobre esta
estructura. No reemplaza los cuatro pasos anteriores, que son juicio de
dominio aplicado documento por documento, con la misma clase de esfuerzo
que un economista de finanzas públicas aplica al leer una Ley de
Presupuestos. **Ese es el foso: no la tecnología, la curación que la
tecnología todavía no sabe hacer sola** — y que, según `§5` mostró,
tampoco resuelve sin supervisión cuando se automatiza (38% de precisión
en match exacto, con resolución de identidad como cuello de botella real,
no el costo).

Es el argumento transversal del [README del repo](../../README.md), capa 2
de 4, hecho concreto: la ventaja competitiva vive en la intersección
dominio × capacidad, y la ontología es donde esa intersección se vuelve
un activo versionable —en un archivo JSON con 37 normas y 47 relaciones
verificadas— en vez de quedar como una explicación en un README.

---

## Documento de governance: fine-tuning y reclasificación bajo la EU AI Act

`04 §3` dejó un puente sin desarrollar: *"un fine-tuning sustancial —y una
destilación lo es— puede reclasificar a un deployer como provider bajo la
EU AI Act."* Este documento lo desarrolla, con las fechas verificadas
contra fuente primaria antes de escribirlas como hechos (regla del plan
maestro de este módulo).

### El mecanismo: Artículo 25

El Artículo 25 del Reglamento (UE) 2024/1689 (la EU AI Act) establece que
un *deployer* (quien usa un sistema de IA de alto riesgo) se reclasifica
automáticamente como *provider* (quien lo pone en el mercado, con todas
las obligaciones que eso implica) cuando:

- pone su marca o nombre comercial sobre un sistema de alto riesgo ya en
  el mercado,
- hace una **modificación sustancial** a un sistema de alto riesgo que
  sigue clasificado como tal, o
- modifica el propósito previsto de un sistema que no era de alto riesgo
  de forma que pasa a serlo.

El estándar de "modificación sustancial" no está definido con precisión
numérica en el texto: la guía disponible sugiere que la prueba clave es si
la modificación afecta el propósito previsto del sistema o su desempeño de
una forma no anticipada por el proveedor original.

### Dónde cae el fine-tuning

Un *fine-tuning* liviano, específico de tarea, que no cambia el propósito
previsto ni afecta el cumplimiento de los requisitos de alto riesgo,
típicamente **no** dispara la reclasificación. Cambios sustanciales al
comportamiento de clasificación del sistema, al alcance de los datos, o al
espacio de acciones que el sistema puede tomar, **sí** pueden dispararla.

Prompt engineering que se mantiene dentro del uso previsto documentado por
el proveedor original generalmente no activa el Artículo 25; prompt
engineering sistemático que redirige el modelo hacia un dominio nuevo crea
un argumento razonable de modificación sustancial.

Para el argumento de `04 §3`: **la destilación sobre el corpus regulatorio
chileno** —entrenar un modelo chico para imitar a uno grande, específico
del dominio— es, por definición, un cambio dirigido al comportamiento del
sistema en un dominio nuevo. Cae del lado de "probable modificación
sustancial", no del lado de "fine-tuning liviano que no cambia nada". La
decisión de destilar, entonces, no es solo técnica y económica: puede
mover a quien la toma de *deployer* de un modelo de un tercero a
*provider* de un sistema propio, con las obligaciones de conformidad,
documentación técnica y gestión de riesgo que eso arrastra si el sistema
califica como de alto riesgo.

### Las fechas, verificadas contra el Diario Oficial de la UE

El "Digital Omnibus on AI" —el paquete de simplificación que difiere las
obligaciones de alto riesgo— **ya no es una propuesta**: es el
**Reglamento (UE) 2026/1744**, publicado en el Diario Oficial de la Unión
Europea el **24 de julio de 2026**, en vigor desde el **27 de julio de
2026**
([EUR-Lex, texto oficial](https://eur-lex.europa.eu/eli/reg/2026/1744/oj/eng)).

| Obligación | Fecha original (Reglamento 2024/1689) | Fecha diferida (2026/1744) |
|---|---|---|
| Sistemas de alto riesgo independientes (Anexo III — incluye empleo y educación) | 2 de agosto de 2026 | **2 de diciembre de 2027** |
| Sistemas de alto riesgo embebidos en productos regulados (Anexo I — dispositivos médicos, ascensores, equipos de radio) | 2 de agosto de 2027 | **2 de agosto de 2028** |
| Marcado de contenido generado por IA para sistemas ya en el mercado antes de agosto de 2026 | 2 de agosto de 2026 | 2 de diciembre de 2026 |

Estas cifras confirman, con fuente primaria, las que el inventario
temático del proyecto había anticipado (diciembre 2027 / agosto 2028) —y
resuelven la incertidumbre que en su momento solo podía marcarse
`[verificar]`: al momento de escribir esta sección, el Omnibus ya está
publicado y vigente, no en negociación.

> **Lo que la fecha diferida NO cambia**: el Artículo 25 sobre
> reclasificación *provider*/*deployer* no es una obligación de alto
> riesgo con fecha diferida — es una regla estructural sobre quién es
> quién en la cadena de responsabilidad, vigente desde la entrada en vigor
> original de la AI Act. Diferir el calendario de obligaciones de alto
> riesgo no difiere la pregunta de si destilar te convierte en *provider*.

### Por qué esto conecta con el foso, no es un tema aparte

Este documento no es un capítulo de cumplimiento legal desconectado del
resto del módulo. Es la misma tesis vista desde el riesgo: la curación de
dominio que hace valioso un producto sobre corpus regulatorio chileno
—ontología, extracción, resolución de identidad— es, bajo cierto punto,
la misma actividad que un regulador europeo consideraría "modificación
sustancial" si se aplica sobre un modelo de terceros. El foso y la
obligación regulatoria nacen del mismo lugar: hacer algo específico de
dominio con un modelo genérico.

## Estado del arte (2026)

| Aspecto | Estado | Fuente |
|---|---|---|
| Digital Omnibus on AI | ✅ Publicado y vigente | Reglamento (UE) 2026/1744, DOUE 24-07-2026 — [EUR-Lex](https://eur-lex.europa.eu/eli/reg/2026/1744/oj/eng) |
| Diferimiento Anexo III (alto riesgo independiente) | ✅ Confirmado | 2 dic 2027 |
| Diferimiento Anexo I (alto riesgo embebido en producto) | ✅ Confirmado | 2 ago 2028 |
| Umbral de "modificación sustancial" (Art. 25) | 🟡 Sin definición numérica | Guía interpretativa, no letra cerrada del reglamento |
| Fine-tuning liviano vs. destilación de dominio | 🟢 Distinción reconocida en la práctica legal | Destilación de dominio pesa más hacia reclasificación |
| Curación de dominio como foso competitivo | ✅ Demostrado en este módulo | Experimento de esta sección: 100% vs. 50% de recall, con y sin curación |

## Cierre del módulo

Nueve secciones, un argumento que se sostiene con números en cada paso: el
clasificador presupuestario es una ontología con todas sus letras (`§1`),
el vocabulario de relaciones se extrajo del propio corpus (`§2`), el
formalismo se compró exactamente hasta donde las preguntas lo pedían
(`§3`), la identidad se resolvió con el mismo cuidado que exige cualquier
llave canónica (`§4`), la extracción automática funciona pero necesita
resolución de identidad para que su recall se duplique (`§5`), la vigencia
a nivel de artículo corrige un error real que el modelo de documento no
podía ver (`§6`), el costo de indexación completa nunca fue el problema a
esta escala (`§7`), el grafo no ganó donde se midió con el rigor de todo
el repo (`§8`) — y, en la última sección, la misma pregunta que el grafo
responde perfecto, un LLM sin la curación la responde a medias.

Ese es el cierre honesto: no "el grafo es superior", sino "el grafo
responde preguntas que el texto solo no responde, y esas preguntas son
las que definen si un producto sobre corpus regulatorio tiene un foso o
solo tiene un README bien escrito".

## Conexiones

- **`§2`**: la competency question P4 es, literalmente, el experimento de
  esta sección.
- **`§4`, `§5`**: los mismos riesgos de resolución de identidad —falsos
  positivos por similitud, huecos en la curación manual— reaparecen en el
  cierre, confirmando que son estructurales al problema, no accidentes de
  una sección.
- **`§8`**: el resultado negativo de esa sección y el resultado de esta se
  complementan: el grafo no gana en QA general de texto libre, y sí gana
  en las preguntas que requieren la estructura que el texto no declara.
- **`04 §3`**: el puente hacia governance que esa sección dejó pendiente,
  cerrado acá con fuente primaria.
- **README del repo**: el argumento de las cuatro capas invariantes y la
  intersección dominio × capacidad, demostrado con un experimento en vez
  de solo enunciado.
