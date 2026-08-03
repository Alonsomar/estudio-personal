# 04 — Economía de Inferencia

Masterclass sobre **de dónde sale el precio** que pagás por token: la mecánica de
la inferencia (prefill, decode, KV cache), el batching como economía de escala,
las palancas que cambian el modelo (cuantización, destilación), la decisión de
self-hosting vs. API, la caducidad de los supuestos de costo, y el salto de costo
unitario a margen de producto.

Las masterclasses previas trataron el costo como **dato exógeno**: un precio
público que se mide (`01 §10`), se presupuesta y se enruta (`03 §10`). Esta lo
abre.

## Estado: En curso

Plan maestro terminado; 6 secciones en desarrollo. El temario fue
**re-especificado el 2026-08-03**: entre el 60% y el 70% del temario original ya
estaba escrito en `01 §10` y `03 §4/§8/§10`, así que este módulo cubre solo lo que
ningún otro toca. El mapa de lo absorbido está en
[theory/00-plan.md](theory/00-plan.md).

## Secciones

| #  | Título                                | Doc                                      | Código | Estado    |
|----|---------------------------------------|------------------------------------------|--------|-----------|
| 00 | Plan maestro                          | [theory/00-plan.md](theory/00-plan.md)   | —      | Terminado |
| 01 | Mecánica: prefill, decode y KV cache  | [theory/01-prefill-decode.md](theory/01-prefill-decode.md) | [code/01-prefill-decode.py](code/01-prefill-decode.py) + [econ_lib.py](code/econ_lib.py) | Terminado |
| 02 | Batching, throughput y latencia       | [theory/02-batching.md](theory/02-batching.md) | [code/02-batching.py](code/02-batching.py) | Terminado |
| 03 | Cuantización y destilación            | [theory/03-cuantizacion.md](theory/03-cuantizacion.md) | [code/03-cuantizacion.py](code/03-cuantizacion.py) | Terminado |
| 04 | Self-hosting vs. API                  | [theory/04-self-hosting.md](theory/04-self-hosting.md) | [code/04-self-hosting.py](code/04-self-hosting.py) | Terminado |
| 05 | Deriva de precios                     | [theory/05-deriva-precios.md](theory/05-deriva-precios.md) | [code/05-deriva-precios.py](code/05-deriva-precios.py) | Terminado |
| 06 | Unit economics de un SaaS regulatorio | —                                        | —      | Pendiente |

## Nota de método

A diferencia de 01–03, esta masterclass **no mide sobre corridas reales**: no hay
GPUs disponibles y alquilar un H100 para producir cuatro gráficos es exactamente
el gasto que la masterclass enseña a no hacer.

El método es un **modelo analítico** de la mecánica de inferencia, calibrado
contra especificaciones públicas de hardware y tarifas publicadas. Predice
**órdenes de magnitud y puntos de equilibrio**, no benchmarks. Toda constante de
hardware se cita; lo no verificable se marca. Las reglas completas están en el
plan maestro.

## Cómo ejecutar código

```bash
uv run python 04-economia/code/01-prefill-decode.py
```

Todo corre offline y determinista: sin GPU y sin llamadas a proveedores. Las
tarifas se leen de `prod_lib.PRICING_USD_PER_M_TOKENS` (no se duplican acá).

## Datos

- Carga de referencia: RAG chileno de `02-retrieval`, ~272 tokens de entrada por
  query (medidos en `03 §2`). La salida depende de cuánto se pide: 21 tokens en la
  demo de `03 §2`, 60 como valor representativo en `03 §10`. Acá se usa **272 in /
  60 out** y se marca cuando el resultado es sensible a ese supuesto.
- Tarifas: `03-produccion/code/prod_lib.py`.

Ver [AGENTS.md](../AGENTS.md) para convenciones completas.
