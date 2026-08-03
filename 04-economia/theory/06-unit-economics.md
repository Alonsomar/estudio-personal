# 06 — Unit economics de un SaaS regulatorio

## De costo a margen

Las cinco secciones anteriores calcularon **costo**: por token, por query, por hora
de GPU. Ninguna respondió la pregunta que decide si un producto existe: *¿cuánto
queda después de restar el costo de servir a un cliente de lo que ese cliente
paga?*

El salto importa porque el costo por query es un dato técnico y el margen es un
dato de negocio, y no se comportan igual. En particular, el margen tiene una
propiedad que el costo no tiene: **depende de la distribución del uso, no de su
media**.

### La analogía: el seguro con prima plana

Un SaaS de tarifa plana con costo variable por uso es, estructuralmente, un
seguro. Cobrás una prima fija y asumís un costo que depende del comportamiento del
asegurado. Y como cualquier seguro, el negocio no lo define el asegurado promedio:
lo define la cola de la distribución, y la selección adversa que la alimenta —los
usuarios más intensivos son los que más valoran el plan ilimitado, y por eso lo
eligen.

Corrida en [`code/06-unit-economics.py`](../code/06-unit-economics.py).

> **Supuestos comerciales ilustrativos, fechados 2026-08.** Los tres planes y sus
> precios son un ejemplo razonable para instituciones públicas y estudios
> jurídicos medianos en Chile, no una propuesta comercial validada. Lo que se
> defiende acá es la *estructura* del análisis, no los niveles.

## El margen en el uso medio: engañosamente cómodo

```
           plan |   precio |   q/mes |  costo LLM |    margen |  margen %
------------------------------------------------------------------------
         Básico | $     49 |     200 | $    0.006 | $   48.99 |   99.99%
    Profesional | $    199 |   1,500 | $    0.046 | $  198.95 |   99.98%
  Institucional | $    799 |   8,000 | $    0.246 | $  798.75 |   99.97%
```

Márgenes brutos sobre el 99.9% en los tres planes. El costo del LLM literalmente
no se ve en el P&L: seis milésimas de dólar contra cuarenta y nueve dólares de
ingreso.

Si el análisis terminara acá, la conclusión sería que el costo de inferencia no
importa y que las cinco secciones anteriores fueron un ejercicio académico. **Esa
es la conclusión que produce la media, y es la equivocada** — por la misma razón
que `03 §10` insistía en reportar media *y* p99.

## ¿Cuánto tendría que usar un cliente para destruir el margen?

```
           plan |   precio |  q/mes media |  break-even q/mes |  múltiplo
--------------------------------------------------------------------------
         Básico | $     49 |          200 |         1,595,052 |    7,975×
    Profesional | $    199 |        1,500 |         6,477,865 |    4,319×
  Institucional | $    799 |        8,000 |        26,009,115 |    3,251×
```

Un cliente del plan Básico tendría que hacer casi **8.000 veces** su uso medio para
que el plan pierda plata. Con el RAG simple de `02-retrieval` y un modelo barato,
la tarifa plana no es solo segura: es imposible de romper por uso legítimo.

Este es el resultado que hay que tener presente antes de diseñar cualquier sistema
de créditos, cuotas o medición fina. **A esta escala, esos mecanismos son
complejidad sin beneficio.** El instinto de "cobrar por uso porque el LLM cuesta"
resuelve un problema que no existe todavía, a cambio de fricción comercial real:
un cliente institucional prefiere un precio predecible, y la tarifa plana es un
argumento de venta.

## Cuándo deja de ser seguro

Ahora bien, "todavía" es la palabra importante. §5 mostró que el consumo por query
crece más rápido de lo que cae la tarifa. Aplicando esa trayectoria al plan Básico:

```
                     escenario |   $/query |  break-even q/mes |   holgura
--------------------------------------------------------------------------
               hoy: RAG simple | $  0.0000 |         1,595,052 |    7,975×
    contexto largo (50 chunks) | $  0.0002 |           275,901 |    1,380×
             respuestas largas | $  0.0002 |           305,639 |    1,528×
                modelo premium | $  0.0007 |            71,387 |      357×
  agéntico: 15 pasos por query | $  0.0009 |            53,168 |      266×
            agéntico + premium | $  0.0206 |             2,380 |       12×
```

De 7.975× de holgura a **12×**. Cuatro órdenes de magnitud a uno.

Y 12× no es holgura. Es un cliente entusiasta. Es una integración que alguien
conectó a un cron. Es un usuario que descubrió que la herramienta le sirve y la usa
todo el día — o sea, exactamente el cliente que uno querría tener.

> El punto de inflexión no es el precio del token: es la **arquitectura del
> producto**. Un RAG de un paso con modelo barato tolera cualquier cosa; un agente
> de 15 pasos con modelo premium convierte a tu mejor cliente en tu peor cliente.

Esta es la conexión más importante entre esta masterclass y la 06 (harness) que
viene: la decisión de hacer el producto agéntico es, además de una decisión de
capacidad, una decisión de modelo de negocio.

## La distribución, que es donde vive el riesgo

Simulando una cartera de 200 clientes con uso lognormal —la forma típica del uso
de un SaaS— en el escenario agéntico premium:

```
  uso mediano:               1,249 queries/mes
  uso medio:                 2,557 queries/mes
  uso p95:                   9,733 queries/mes
  uso máximo:               34,748 queries/mes

  ingreso total:        $    39,800
  costo total:          $    10,531
  margen total:         $    29,269 (73.5%)

  clientes con margen negativo: 11 de 200 (5.5%)
  el top 5% (10 clientes) consume 30% del costo total
  margen sin ese 5%:    $    30,428 (80.5%)
```

Cuatro lecturas:

- **La media dobla a la mediana** (2.557 vs. 1.249). Presupuestar con el uso medio
  ya es un error; presupuestar con el mediano es un error mayor.
- **El 5.5% de los clientes tiene margen negativo.** No son abusadores: son
  usuarios intensivos con un plan que no los contempló.
- **El top 5% consume el 30% del costo.** Diez clientes de doscientos definen un
  tercio de la factura.
- **Excluir a ese 5% sube el margen de la cartera de 73.5% a 80.5%.** Siete puntos
  de margen concentrados en diez cuentas.

Es la lección de `03 §10` —el costo de una feature es una distribución, no un
número— trasladada del dashboard al P&L. Y tiene la misma implicación operativa:
la métrica que hay que mirar no es el costo medio por cliente, es el **p95 del
costo por cliente**, y la cantidad de cuentas bajo cero.

## Las palancas, ordenadas por retorno

Cierre del módulo. Midiendo cada palanca por cuánto sube el break-even del plan
Básico en el escenario agéntico premium (más alto es mejor):

```
                               palanca |  break-even q/mes |   mejora
----------------------------------------------------------------------
  (base) agéntico + premium, caché 20% |             2,380 |        —
                       Límite por plan |   (corta la cola) |        ∞
                  Caché al 60% (03 §4) |             4,759 |     2.0×
    Rutear lo simple a barato (03 §10) |            53,168 |    22.3×
               Acortar respuestas (§1) |             3,659 |     1.5×
                 Menos pasos agénticos |             7,139 |     3.0×
                            Todo junto |           319,010 |   134.1×
```

El orden importa y no es el intuitivo:

1. **El límite por plan es cualitativamente distinto.** No mejora el margen medio:
   *elimina la cola que lo destruye*. Es la única palanca que acota el peor caso en
   vez de mejorar el promedio, y por eso va primero. En términos de seguros, es el
   deducible — el instrumento que hace asegurable un riesgo de cola.
2. **Rutear lo simple a un modelo barato (22×)** es, de lejos, la palanca continua
   más grande. El `CostAwareRouter` de `03 §10` ya está construido.
3. **El caché (2×)** duplica la holgura sin degradar nada. Barato de operar y ya
   implementado en `03 §4`.
4. **Las optimizaciones de prompt (1.5×)** son las que más se discuten y las que
   menos rinden. Acortar respuestas ayuda, pero es la última palanca a tocar, no la
   primera.

Y la conclusión que atraviesa todo el módulo: **nada de esto importa hasta que la
arquitectura cambie**. Con el producto de hoy —RAG de un paso, modelo barato— el
costo de inferencia es ruido y optimizarlo es procrastinación disfrazada de rigor.
El momento de aplicar estas palancas es cuando la holgura baje de dos órdenes de
magnitud, y el trabajo de hoy es **saber medirlo para enterarse a tiempo**.

## Lo que este análisis no cubre

- **Solo margen bruto de LLM.** No incluye infraestructura, soporte, adquisición de
  clientes ni desarrollo. En un SaaS B2B real, el costo de venta y el soporte
  suelen dominar al costo de inferencia por varios órdenes de magnitud — que es
  otra forma de decir lo mismo que §4.
- **Uso independiente entre clientes.** La simulación ignora que el caché comparte
  beneficios entre clientes con preguntas parecidas: un segundo cliente preguntando
  por el IVA de servicios digitales es más barato que el primero. En un corpus
  común y acotado como el normativo chileno, ese efecto es real y favorable, y está
  omitido conservadoramente.
- **Sin selección adversa modelada.** Los planes ilimitados atraen usuarios
  intensivos; la distribución de uso no es exógena al diseño de planes. Modelarlo
  requeriría datos reales de comportamiento.
- **Precios ilustrativos.** Ver la advertencia del principio.

## Estado del arte (2026)

| Aspecto | Estado | Detalle |
|---|---|---|
| Tarifa plana con costo variable | 🟢 Dominante en SaaS IA | Funciona mientras el costo unitario sea ruido; se rompe con agentes |
| Créditos / tokens al usuario final | 🟡 En discusión | Alinea costos pero traslada complejidad al cliente; mala experiencia B2B |
| Límites por plan como instrumento de margen | 🟢 Best practice | La palanca que acota el peor caso, no el promedio |
| Margen bruto de SaaS IA vs. SaaS clásico | 🟡 Menor y más variable | El SaaS clásico opera a 80-90%; con agentes intensivos baja |
| Monitoreo de costo por cliente | 🟢 Necesario con agentes | `CostMeter` por `label` de cliente (`03 §10`) ya lo permite |
| Precios agénticos (por tarea, no por asiento) | 🟡 Emergente | Cobrar por resultado alinea precio y costo; poco asentado |
| Datos públicos de unit economics en IA vertical | 🔴 Escasos | Casi nadie publica; los benchmarks son de SaaS clásico |

La fila a seguir es **precio por tarea en vez de por asiento**: si el producto se
vuelve agéntico, cobrar por asiento deja de tener relación con el costo de servir,
y el modelo de precios tiene que moverse con la arquitectura.

## Cierre de la masterclass

El módulo empezó preguntando de dónde sale el precio por token y terminó en el
margen de un producto. El recorrido, en una línea cada uno:

- **§1**: el output cuesta más que el input porque generar relee todos los pesos.
- **§2**: el batching reparte ese costo fijo; por eso la inferencia es vendible.
- **§3**: cuantizar y destilar cambian el modelo, y la calidad se mide o no se sabe.
- **§4**: la API gana al self-hosting por órdenes de magnitud en este escenario.
- **§5**: los niveles caducan, los ratios y las estructuras no.
- **§6**: el margen lo define la cola de la distribución, no la media.

Y el resultado más útil de todos es negativo: **hoy, para este producto, el costo
de inferencia no es un problema**. $10.80 al mes (§4), márgenes sobre 99% (§6). El
tiempo rinde muchísimo más en la calidad del corpus y en la ontología del dominio
—módulo 05— que en optimizar tokens.

Lo que esta masterclass deja no es un ahorro: es el **instrumental para saber
cuándo eso deje de ser cierto**, y un criterio para no gastar atención en el
problema equivocado mientras tanto.

## Conexiones

- **§1 (mecánica)**: acortar respuestas como palanca de margen sale de ahí; es la
  fase cara.
- **§4 (self-hosting)**: el mismo veredicto desde el otro lado — a este volumen, el
  costo de inferencia no es donde está el problema.
- **§5 (deriva)**: la trayectoria de consumo creciente es lo que convierte 7.975×
  de holgura en 12×. Sin §5, este análisis parecería tranquilizador.
- **`03 §4` (caching)**: el hit rate entra directo al margen; duplica la holgura.
- **`03 §10` (costo en producción)**: el `CostMeter` etiquetado por cliente es el
  instrumento que mide todo esto en producción, y "la media miente" es la misma
  lección en otro plano.
- **`01 §10` (Pareto)**: la frontera costo/calidad decide qué modelo va a cada
  query; acá se ve el efecto de esa decisión en el margen.
- **06-harness (planificado)**: hacer el producto agéntico es también una decisión
  de modelo de negocio. §8 de ese módulo mide la varianza de costo por tarea.
