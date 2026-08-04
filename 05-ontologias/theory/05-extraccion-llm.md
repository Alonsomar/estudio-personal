# 05 — Extraer la ontología del corpus

## Diseño medible

El LLM extrae `identificador_destino`, tipo y fundamento. Una etapa separada
resuelve el identificador a `doc_id`. Esa separación permite una ablación real:
el mismo caché se evalúa con `usar_numero=False` y `usar_numero=True`.

La regex numérica está anclada al designador de norma. Por eso `art. 12 del DL
825` resuelve 825, `artículo 71` no inventa una norma y `LEY 21210` tolera la
ausencia de puntos.

## Resultado contra ground truth v2

La muestra contiene 10 documentos y 28 relaciones curadas. Con resolución
numérica:

```
extraídas y resueltas: 31
verdaderos positivos: 12
falsos positivos: 19
falsos negativos: 16
precisión: 39% · recall: 43% · F1: 41%
```

La variante sin resolución numérica se calcula desde las mismas diez respuestas
cacheadas; no hay barras hardcodeadas. El gráfico se regenera con ambas salidas.

![Ablación de resolución numérica](../diagrams/efecto-resolucion-numero.png)

## Qué falló

El extractor comete errores de agencia: atribuye al documento que describe un
cambio la relación `MODIFICA` ejecutada por otra norma. También pierde relaciones
explícitas. Lobby–Probidad es el caso instructivo: la referencia aparece en el
texto, pero la omitieron tanto el extractor como la primera curación manual. El
ground truth v2 la corrige.

Las relaciones conceptuales verdaderamente implícitas —dos normas afines que no
se citan— siguen siendo una limitación del método, pero no se mezclan con las
aristas literales ni se presentan como una magnitud medida.

## Caché y costo

`LLMExtractor` usa el contrato `LLMCacheEntry`. La clave incluye modelo, prompt
completo, esquema, temperatura y réplica; el prompt SHA-256, modelo devuelto,
tokens, tarifa y costo histórico quedan persistidos. `allow_api=False` es el
default y un miss offline falla explícitamente.

La corrida controlada hizo 10 llamadas: 15.735 tokens de entrada, 2.193 de
salida y USD 0,0037. La proyección lineal a 40 documentos es USD 0,0147. Una
corrida posterior sin credenciales hace cero llamadas y distingue ese costo
actual del costo histórico del caché.

## Conexiones

- `§2`: ground truth literal de 69 relaciones.
- `§4`: resolución determinista antes de similitud difusa.
- `§7` y `§9`: mismo contrato de caché versionado.
