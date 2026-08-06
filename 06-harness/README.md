# 06 — Harness Agéntico

Masterclass sobre el entorno en que opera un modelo: el bucle, el contexto, las
herramientas, los permisos. La tesis es que a igualdad de modelo el harness explica
más varianza que un salto de generación — y el módulo la somete a medición sobre el
corpus regulatorio chileno.

## Estado: Terminado — B8

Nueve secciones, un servidor MCP funcional y 12 tareas congeladas copiadas de
goldens ya auditados de `01` y `05`. Todos los scripts corren **offline por
defecto** desde caché versionado; `--allow-api` sólo regenera.

### El resultado que atraviesa el módulo

Cuatro intervenciones de harness —contrato de error (§1), compactación (§2),
granularidad de la herramienta (§3), orquestación y sus dos arreglos (§5)— y
**ninguna movió el resultado de forma detectable con n=12**. Las cuatro movieron el
proceso, y mucho. El harness ataca el desperdicio, y el desperdicio no aparece en la
respuesta:

| Sección | Qué se cambió | Resultado | Proceso |
|---|---|---|---|
| §1 | Contrato de error | 0,000 | recuperación 0,222 → 1,000; errores 10 → 3 |
| §2 | Compactación de contexto | −0,083 | +1 paso/tarea; +12% tokens |
| §3 | Granularidad de la tool | 0,000 | −0,83 pasos; −25% tokens |
| §5 | Orquestación + arreglos | +0,083 (1 tarea) | −16 pasos; −8.409 tokens |

§7 lo confirma con bootstrap: ningún IC de resultado excluye cero, todos los de
proceso sí.

### Otros resultados vigentes

- **§1** · `t-08` con error opaco: ocho pasos enumerando tipos de relación porque
  nadie le dijo que el argumento malo era `doc_id`. Acotar la observación cuesta
  −0,083 con 8 pasos y +0,000 con 16, al precio de 47% más tokens.
- **§2** · El 51,2% del gasto de entrada es prefijo idéntico reenviado; multiplicador
  de reenvío 3,32×. Compactar tiene punto de equilibrio: N\* ≈ 6,4 pasos con
  P=757, h=311, s=225 medidos, y estas trayectorias promedian 4,8.
- **§3** · La herramienta de grano grueso salió 25% más barata sin mover el acierto,
  y `t-08` siguió fallando: tenía la respuesta en el paso 2 y no paró.
- **§4** · Servidor MCP sobre el corpus, protocolo `2026-07-28`, verificado por stdio
  y en memoria. Cuatro esquemas = 618 tokens en cada iteración.
- **§5** · El orquestador procesa contextos de la mitad de tamaño y gasta 3,5× más.
  La frontera departamental cortó la dependencia de entity resolution y el
  subagente degeneró: `ds-250` → `ds-250-ds` → `ds-250-ds-250`.
- **§6** · 1 de 4 cargas de inyección logró la acción irreversible, y fue la que se
  hace pasar por el usuario. El agente respondió **correctamente** en los cuatro
  escenarios: la salida no contiene evidencia del incidente.
- **§8** · Costo por tarea máx/mediana de 5,5× en el sistema orquestado. Del 51,2%
  de prefijo quedan 14,6% de ahorro real con caché.
- **§9** · De 20 reglas del harness personal de este repo, 6 tienen mecanismo que
  las verifica. 11 son aspiracionales, incluidas dos doctrinas centrales.

## Secciones

| # | Tema | Teoría | Código |
|---|---|---|---|
| 00 | Plan maestro | [00-plan](theory/00-plan.md) | — |
| 01 | El bucle y el factorial 2×2 | [teoría](theory/01-que-es-un-harness.md) | [script](code/01-el-bucle.py) |
| 02 | Contexto como asignación | [teoría](theory/02-contexto-escaso.md) | [script](code/02-contexto-escaso.py) |
| 03 | La tool como contrato | [teoría](theory/03-tool-como-contrato.md) | [script](code/03-tool-como-contrato.py) |
| 04 | MCP y el servidor del corpus | [teoría](theory/04-mcp.md) | [cliente](code/04-mcp.py) · [servidor](code/mcp_corpus_server.py) |
| 05 | Orquestador y trabajadores | [teoría](theory/05-multiagente.md) | [script](code/05-multiagente.py) |
| 06 | Permisos, idempotencia, inyección | [teoría](theory/06-permisos-y-control.md) | [script](code/06-permisos-y-control.py) |
| 07 | Evaluar trayectorias | [teoría](theory/07-evaluar-trayectorias.md) | [script](code/07-evaluar-trayectorias.py) |
| 08 | Costo y corte del bucle | [teoría](theory/08-costo-del-bucle.md) | [script](code/08-costo-del-bucle.py) |
| 09 | El harness como práctica | [teoría](theory/09-harness-personal.md) | [script](code/09-harness-personal.py) |

## Ejecución

Todos los scripts son offline por defecto:

```bash
for script in 06-harness/code/0[1-9]-*.py; do
  uv run python "$script"
done
```

Sólo §1, §2, §3, §5 y §6 aceptan `--allow-api`, reservado para regenerar cachés. El
contrato `LLMCacheEntry` se importa de `05-ontologias/code/ontology_lib.py`, no se
duplica. Un *cache miss* en modo offline es un error explícito, nunca una llamada
silenciosa a la red.

### El servidor MCP

```bash
uv run python 06-harness/code/mcp_corpus_server.py
```

Configuración para un cliente MCP:

```json
{
  "mcpServers": {
    "corpus-chileno": {
      "command": "uv",
      "args": ["run", "python", "06-harness/code/mcp_corpus_server.py"],
      "cwd": "/ruta/a/estudio-personal"
    }
  }
}
```

Expone `buscar_corpus` (BM25 de `02`), `leer_norma` (corpus real paginado),
`vecinos_grafo` y `alcance_normativo` (grafo auditado de `05`), más el catálogo y
los documentos como resources y una plantilla de auditoría como prompt. No expone
`responder`: el control de flujo del agente es del harness, no del corpus.

## Datos

- [12 tareas congeladas](examples/tareas-agente.json) — copiadas de los goldens de
  `01-evals` y `05-ontologias`, con su procedencia
- [Auditoría del harness personal](examples/reglas-harness.json) — 20 reglas
  clasificadas a mano con su mecanismo
- `examples/trayectorias-01.json`, `trayectorias-05.json` — trayectorias completas
- `examples/metricas-trayectoria.json` — métricas de proceso por tarea y sistema
- `examples/cache-*.json` — 5 cachés versionados con 537 respuestas; costo
  histórico total del módulo: **USD 0,1034**

## Tests

`tests/test_harness_lib.py` y `tests/test_mcp_corpus_server.py`. Los del servidor
MCP hablan el protocolo de verdad sobre transporte en memoria: no son mocks, pero no
usan red ni proceso hijo.

Ver [AGENTS.md](../AGENTS.md) para las convenciones del repo.
