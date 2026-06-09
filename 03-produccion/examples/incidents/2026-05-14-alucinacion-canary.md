# Postmortem — Alucinación masiva tras canary de modelo

> **Ficticio**, con fines didácticos (§12). Formato blameless: el foco no es
> quién, sino qué nos faltó para detectarlo antes.

| Campo | Valor |
|---|---|
| Fecha | 2026-05-14 |
| Duración | 14:02 – 15:48 (1h 46m) |
| Severidad | Alta (calidad), sin caída de servicio |
| Modo de falla (§12) | `hallucination` (regresión por cambio de modelo) |
| MTTD | **94 min** (lo reportó un usuario, no una alerta) |
| MTTR | 12 min (rollback del canary) |
| Impacto | ~6.300 respuestas servidas por el candidato; ~18% con citas inventadas |

## Qué pasó

A las 14:02 se promovió el canary del modelo candidato de **5% a 25%** del
tráfico (migración para bajar costo, §8/§10). El candidato, sobre las queries de
interpretación normativa, **inventaba números de artículo** que no estaban en los
fragmentos: citaba "[Fragmento 2]" con un dato que el fragmento no contenía.

El servicio nunca se cayó: latencia y tasa de error normales. La regresión era de
**calidad**, invisible para las métricas de sistema (§5). A las 15:36 un usuario
reportó "me citó un artículo que no existe". A las 15:42 se correlacionó por
`prompt_ref`/`model` (§5) con el canary; a las 15:48 se hizo rollback a la versión
estable (§8). El impacto se detuvo de inmediato.

## Línea de tiempo

| Hora | Evento |
|---|---|
| 14:02 | Canary 5% → 25% (cambio planificado) |
| 14:05 | El `online_pass_rate` del candidato empieza a caer (lo vimos *después*, en los datos) |
| 15:36 | Primer reporte de usuario |
| 15:42 | Correlación con el canary por `model` en los traces |
| 15:48 | Rollback; impacto detenido |

## Por qué tardamos en detectarlo (la pregunta que importa)

La señal **existía**: el `online_pass_rate` por variante (§9) venía cayendo desde
las 14:05. No teníamos una **alerta** sobre esa métrica por variante de canary —
solo un panel que nadie miraba en tiempo real. El `IncidentDetector` (§12) la
habría marcado como `hallucination` a los pocos minutos, pero no estaba cableado a
las métricas online del canary.

> El MTTD de 94 min no fue por falta de datos, sino por falta de **alerta sobre los
> datos que ya teníamos**. Es el anti-patrón de §5: dashboard sin alerta.

## Qué salió bien

- El rollback del canary (§8) fue un comando, no una restauración de backup: MTTR
  de 12 min.
- El ruteo sticky (§8) acotó el impacto al 25% del tráfico, no al 100%.
- Los `trace_id` (§5) permitieron juntar las respuestas malas para el golden (§9).

## Acciones

| # | Acción | Tipo |
|---|---|---|
| 1 | Cablear `online_pass_rate` por variante al `IncidentDetector` con alerta (umbral 0.7) | Detección (baja MTTD) |
| 2 | Bloquear la promoción de canary si el IC del delta de calidad (01-evals §8) no excluye 0 | Prevención |
| 3 | Agregar las ~1.100 respuestas malas muestreadas al golden (§9) | Prevención de regresión |
| 4 | Runbook `hallucination` (§12) enlazado desde la alerta | MTTR |

## Lección

El daño no lo causó "elegir un mal modelo" —para eso estaba el canary, que hizo su
trabajo acotando el blast radius—. Lo causó **no tener una alerta sobre la métrica
de calidad que ya estábamos calculando**. La inversión que paga no es otro modelo
ni otro dashboard: es bajar el MTTD conectando las señales que ya tenemos a una
alerta accionable.
