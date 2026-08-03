# 03 — Cuánto formalismo comprar

## La tentación y su costo

`§1` y `§2` construyeron una ontología con el formalismo mínimo que hacía
falta: una relación (`CONTIENE`) para el clasificador presupuestario, seis
relaciones (`MODIFICA`, `DEROGA`, `REGLAMENTA`, `INTERPRETA`, `APLICA`,
`CITA`) para el grafo normativo. Ninguna de las dos decisiones se justificó
todavía — se tomaron y se siguió. Esta sección vuelve atrás y hace
explícita la regla que las justifica, porque la tentación en cualquier
proyecto de ontologías es la contraria: comprar el formalismo más completo
disponible "por si acaso".

Esa tentación tiene nombre en la disciplina: **sobre-construcción**. Y tiene
un costo medible, no solo estético — cada nivel de formalismo adicional trae
infraestructura, curva de aprendizaje y superficie de mantenimiento que hay
que justificar con una pregunta real del dominio, no con la elegancia del
modelo.

### La analogía: sobre-ajuste, pero en el modelo de datos

Un economista reconoce el patrón: es la misma tentación que sobre-ajustar
un modelo con más regresores de los que los datos sostienen. El modelo se ve
más sofisticado y explica menos generalizablemente. Un esquema de ontología
con razonamiento OWL completo cuando el dominio solo necesita "¿qué modifica
a qué?" es sobre-ajuste de formalismo: impresiona en el diseño y no cambia
ni una respuesta.

Código en [`ontology_lib.py`](../code/ontology_lib.py); demo en
[`code/03-cuanto-formalismo.py`](../code/03-cuanto-formalismo.py).

## El espectro, con un ejemplo corriendo en cada nivel

### Nivel 1 — Lista de sinónimos

Ya construido en `02 §9`: `expand_synonyms` anexa la forma extendida de una
sigla detectada en una query.

```
Original:   El oficio del SII sobre la 21.210
Expandido:  El oficio del SII sobre la 21.210 Ley 21.210 de Modernización
            Tributaria Servicio de Impuestos Internos
```

Resuelve un problema —que "SII" y "Servicio de Impuestos Internos" matcheen
en retrieval léxico— y ninguno más. No sabe que un "oficio" es un *tipo* de
norma, ni que el SII es el organismo que *emite* oficios y circulares. Es
potente para lo que hace y ciego a todo lo demás, por diseño.

### Nivel 2 — SKOS: jerarquía is-a + sinónimos

Un esquema con diez conceptos, construido sobre los mismos géneros
documentales de `§2`:

```
Norma
  Norma con rango legal
  Norma administrativa
  Instrumento presupuestario
  Ley (alt: DL, DFL)
  Decreto (alt: DS, decreto supremo, decreto exento)
  Circular
  Resolución (alt: res. exenta)
  Oficio (alt: dictamen)
  Glosa presupuestaria
```

Responde preguntas de clasificación por herencia:

```
  ✓ ¿'circular' es un tipo de 'norma_administrativa'? -> True
  ✓ ¿'circular' es un tipo de 'norma_legal'? -> False
  ✓ ¿'glosa' es un tipo de 'instrumento_presupuestario'? -> True
```

Pero no puede responder algo que suena parecido y no lo es:

> **¿La circular-01 INTERPRETA la ley-02?**

No es una pregunta de "¿X es un tipo de Y?" (jerarquía de **clases**). Es
"esta **instancia** concreta se relaciona con esa otra instancia concreta,
de esta forma específica" — y SKOS, por diseño, no tiene vocabulario para
relaciones tipadas entre instancias. Solo conoce `broader`/`narrower` y
sinónimos. Esta es, medida y no solo descrita, la brecha exacta que
justificó pasar al property graph de `§2`.

### Nivel 3 — Property graph: cuántos saltos hacen falta

Sobre el grafo normativo de `§2` (37 nodos, 47 aristas), se midió la
distancia real que las competency questions necesitan, en vez de asumirla:

```
                          competency question | saltos reales
---------------------------------------------------------------
          ¿Qué normas modifica la Ley 21.210? |             1
     ¿Qué documento reglamenta la Ley 19.886? |             1
   ¿oficio-05 depende de la SEP (transitivo)? |             2
```

El tercer caso vale la pena mirarlo de cerca: la ruta más corta entre
`oficio-05` y la Ley 20.248 (SEP) **no** pasa por la cadena obvia de tres
saltos (`oficio-05 → decreto-06 → ley-09 → ley-08`). Pasa por una arista de
convergencia diseñada a propósito en `B6`: `oficio-05` cita directamente a
`oficio-01`, que ya cita a la SEP. Dos saltos, no tres — el corpus premia
que las citas cruzadas entre clusters existan.

Sobre el corpus completo, el máximo observado es **3 saltos**. Ese es el
número que define la regla de decisión de esta sección.

### Nivel 4 — Lo que RDF/OWL prometería, medido

La promesa de venta de OWL es la inferencia automática: declarar que una
relación es `owl:TransitiveProperty` y dejar que un razonador derive todas
las consecuencias. Se puede medir qué aporta eso *sobre este corpus*, sin
instalar un razonador:

```
Aristas CITA directas:                33
Aristas CITA tras clausura transitiva: 36
```

`nx.transitive_closure()` —una función de la librería estándar de grafos,
sin RDF, sin SPARQL, sin HermiT ni Pellet, sin triple store— produce
exactamente lo que un axioma `owl:TransitiveProperty` sobre `CITA` le
pediría inferir a un razonador. La diferencia entre 33 y 36 aristas es toda
la ganancia que ese formalismo traería para este corpus.

> Esto no dice que OWL sea inútil en general — hay dominios (taxonomías
> biomédicas, clasificaciones regulatorias internacionales con miles de
> categorías y restricciones de disjunción) donde la inferencia automática
> de axiomas paga su costo. Dice que, **para este corpus y estas preguntas**,
> la parte de OWL que se vendería ya está resuelta por la estructura de
> grafo dirigido, sin comprar el resto del paquete.

## El costo que no es conceptual: infraestructura

```
                       nivel |       representación |                  infraestructura
----------------------------------------------------------------------------------------
          Lista de sinónimos |          dict Python |                          ninguna
                        SKOS | clase Pydantic + dict |                          ninguna
   Property graph (networkx) |     grafo en memoria |      ninguna — un proceso Python
      Property graph (Neo4j) |    grafo en servidor | servidor de base de datos + Cypher
         RDF/OWL + razonador | triples + ontología formal | triple store + razonador + SPARQL
```

Es la misma tesis de `03 §7` ("Kubernetes es over-engineering para el 95%
de estos productos") aplicada a la capa de conocimiento. 37 nodos y 47
aristas viven cómodos en memoria, en un proceso Python que arranca en
milisegundos. Levantar Neo4j o un triple store para este corpus sería pagar
el costo operativo de una escala que no existe — otro servicio que
mantener, otro lenguaje de consulta que aprender, otro punto de falla en
producción, para un problema que `networkx` resuelve sin salir del proceso.

## La regla de decisión

1. Escribir las *competency questions* (`§2`) **antes** de elegir el nivel.
2. Contar cuántos saltos necesita cada una sobre el grafo más simple que
   las responda — medido, como se hizo arriba, no estimado a ojo.
3. Si el máximo son 2-3 saltos: un property graph con recorrido (BFS/DFS,
   `nx.descendants`) alcanza. No comprar más.
4. Subir de nivel **solo** si aparece una pregunta que exige:
   - **(a) clasificación automática** de instancias no vistas en los
     datos ("¿esta norma nueva es de rango legal, sin que nadie la haya
     etiquetado?"), o
   - **(b) verificación de consistencia lógica** entre axiomas ("¿es
     válido que un Decreto modifique una Ley?" — una restricción que el
     dominio jurídico chileno sí tiene, y que un razonador podría
     verificar automáticamente contra todas las aristas).

   Ninguna de las competency questions de `§2` pide (a) o (b).

**Decisión tomada para el módulo:** property graph con `networkx` + esquema
Pydantic. Tres justificaciones, cada una con su número:

1. Las competency questions de `§2` se responden en 1-3 saltos — medido en
   esta sección, no supuesto.
2. El corpus tiene 37 nodos y 47 aristas — cabe en memoria sin
   infraestructura adicional.
3. Lo que OWL vendería como razonamiento (transitividad) ya sale de
   `nx.transitive_closure` sin razonador.

Ningún criterio de la regla empuja hacia más formalismo. Si el corpus
creciera a un tamaño donde `networkx` en memoria dejara de alcanzar, o si
apareciera una pregunta de tipo (a) o (b), esta misma sección da el
criterio explícito para reabrir la decisión — no haría falta adivinar.

## Estado del arte (2026)

| Aspecto | Estado | Detalle |
|---|---|---|
| Property graphs para dominios de escala pequeña-mediana | ✅ Consenso | `networkx`, `igraph`; sin servidor dedicado hasta cientos de miles de nodos |
| SKOS para taxonomías de dominio | ✅ Estándar W3C maduro | Bibliotecas, tesauros institucionales; encaja bien cuando solo hace falta jerarquía |
| RDF/OWL con razonador en producción | 🟡 Nicho especializado | Bio-ontologías (GO, SNOMED), algunos reguladores; raro fuera de esos nichos |
| Neo4j / bases de grafos dedicadas | 🟢 Sólido a escala | Paga su costo operativo cuando el grafo no cabe en memoria de un proceso o necesita consultas concurrentes de múltiples servicios |
| Sobre-construcción con OWL en proyectos chicos | 🔴 Antipatrón común | La causa más citada de proyectos de "ontología corporativa" abandonados |
| Regla "competency questions → nivel de formalismo" | 🟢 Método establecido en ingeniería ontológica | METHONTOLOGY y metodologías similares lo prescriben desde los 2000; sigue vigente porque funciona |

## Lo que viene en la próxima sección

El property graph de `§1-§2` asume, tácitamente, que "DIPRES" en un
documento y "Dirección de Presupuestos" en otro son el mismo nodo si
alguien los escribe igual. No lo son automáticamente: `§4` aborda el
problema de decidir cuándo dos menciones textuales distintas son la misma
entidad — el problema de identidad que toda la construcción de `§1-§3` dio
por resuelto.

## Conexiones

- **`02 §9`**: `expand_synonyms` es el Nivel 1 completo del espectro,
  reutilizado sin modificar.
- **`§2`**: las competency questions que se miden acá son las que esa
  sección escribió; la regla de decisión confirma, con números, la
  elección de diseño que ya se había tomado.
- **`03 §7`**: la tesis de no sobre-construir infraestructura se aplica acá
  a la capa de conocimiento con el mismo argumento de escala.
- **`§7`** (planificada): retoma esta misma pregunta —cuánto conviene
  invertir— para el par GraphRAG vs. metadata filtering, con costo en
  dólares en vez de infraestructura.
- **`§4`**: la identidad de entidades es la pregunta que este nivel de
  formalismo no resuelve por sí solo — "DIPRES" y "Dirección de
  Presupuestos" son el mismo nodo solo si algo, en algún punto, los
  resuelve a la misma llave canónica.
