# BACKLOG — Estudio Personal

Cola de trabajo del repo. IDs estables (`B1`, `B2`…), prioridad `P0`–`P2`, criterios
de aceptación verificables. Los commits referencian el ID (`feat(ontologias): B7 —
sección 3`). Si una tarea cambia de alcance o estado, se actualiza aquí en el mismo
cambio.

Última revisión: **2026-08-03**.

---

## Estado de las masterclasses

| #  | Módulo                    | Secciones | Estado      |
|----|---------------------------|-----------|-------------|
| 01 | Evaluación de sistemas IA | 12/12     | Terminado   |
| 02 | Information Retrieval     | 9/9       | Terminado   |
| 03 | Patrones de producción    | 12/12     | Terminado   |
| 04 | Economía de inferencia    | 0/6       | Re-especificado, pendiente |
| 05 | Ontologías y representación del conocimiento | 0/9 | Planificado |
| 06 | Harness agéntico          | 0/9       | Planificado |

---

## Hoja de ruta

El orden es **secuencial**, no paralelo (doctrina: completar un módulo antes de
abrir el siguiente).

```mermaid
graph LR
    F0["Fase 0<br/>Higiene"] --> F1["Fase 1<br/>Cerrar 04-economia"]
    F1 --> F2["Fase 2<br/>Expandir corpus"]
    F2 --> F3["Fase 3<br/>05-ontologias"]
    F3 --> F4["Fase 4<br/>06-harness"]

    style F0 fill:#cfc,stroke:#333,color:#1a1a1a
    style F4 fill:#fd9,stroke:#333,color:#1a1a1a
```

| Fase | Contenido | Tareas | Sesiones est. |
|---|---|---|---|
| 0 | Higiene del repo | B1–B4 | 1 |
| 1 | Cerrar 04-economia (re-especificado a 6 secciones) | B5 | 2–3 |
| 2 | Expandir corpus 16 → ~40 documentos | B6 | 1 |
| 3 | 05-ontologias | B7 | 4–6 |
| 4 | 06-harness | B8 | 4–6 |

---

## Tareas abiertas

### B1 · P0 · Estado real en README y marco de capas invariantes
El `README.md` es la portada del sitio público (`docs/index.md` es symlink) y
declaraba 02 y 03 como "Pendiente" cuando están terminadas.

- [x] Tabla de estado refleja el estado real de los seis módulos.
- [x] El README presenta el repo como las **cuatro capas invariantes** de un
      producto sobre corpus regulatorio, con cada módulo colgando de una capa.
- [x] Enlace a este backlog desde el README.

### B2 · P0 · BACKLOG.md como cola de trabajo
- [x] Este archivo existe con IDs estables, prioridades y criterios verificables.
- [x] Referenciado desde `AGENTS.md` como lectura obligatoria al inicio de sesión.

### B3 · P1 · Higiene de repositorio
- [x] `.vscode/` ignorado (`git status` limpio).
- [x] `AGENTS.md` documenta `tests/`, `BACKLOG.md` y los módulos 05–06.
- [x] `mkdocs.yml` incluye la hoja de ruta en la navegación.

### B4 · P1 · Suite mínima de tests
`pyproject.toml` declara `pytest` y hay dos librerías reutilizables reales
(`retrieval_lib.py`, `prod_lib.py`) sin un solo test.

- [x] `tests/` con smoke tests que corren **sin API keys ni red**.
- [x] `uv run pytest` pasa en verde (55 tests).
- [x] Los tests corren en CI en cada push y PR
      (`.github/workflows/tests.yml`).

### B11 · P2 · Discrepancia en STOPWORDS_ES
Detectada al escribir los tests de B4. El comentario sobre `STOPWORDS_ES` en
`02-retrieval/code/retrieval_lib.py` afirma que en dominio legal palabras como
"no", "sin" o "menor" cambian el sentido y por eso **no** se filtran — pero
`sin` **sí está** en la lista. `no` y `menor` están correctamente fuera.

Impacto acotado: afecta queries donde "sin" es discriminante (p. ej. "operaciones
*sin* derecho a crédito fiscal"). **Arreglarlo mueve las métricas publicadas en
toda la masterclass 02**, así que no se toca de forma aislada.

- [ ] Decidir entre las dos salidas: (a) sacar `sin` de la lista y **re-correr los
      benchmarks de 02**, documentando el delta con IC; o (b) corregir el
      comentario para que describa la lista real y justificar por qué `sin` se
      filtra.
- [ ] Si se elige (a), actualizar los números citados en las secciones afectadas
      de `02-retrieval/theory/`.

### B5 · P0 · Cerrar 04-economia (re-especificado)
**Diagnóstico (2026-08-03):** entre el 60% y el 70% del temario original de 04 ya
está escrito en otros módulos — caching en `03 §4`, selección/versionado de modelos
en `03 §8`, presupuestos y cost-aware routing en `03 §10`, frontera de Pareto en
`01 §10`. Ejecutarlo tal como estaba especificado sería duplicación.

Se re-especifica a **6 secciones**, cubriendo solo lo que ningún módulo toca:

1. Mecánica de la inferencia: prefill vs. decode, KV cache, por qué el primer
   token y el token N-ésimo no cuestan lo mismo.
2. Batching y throughput vs. latencia: continuous batching, la curva de
   utilización, por qué el proveedor te cobra lo que te cobra.
3. Cuantización y destilación: qué se pierde, cómo se mide lo que se pierde
   (con el aparato de `01 §8`).
4. Self-hosting vs. API: punto de equilibrio explícito, costo total incluyendo
   operación, y a qué volumen deja de ser una decisión obvia.
5. Deriva de las curvas de precio: qué supuestos de costo envejecen y en qué
   plazo; cómo escribir un modelo de costos que no caduque en seis meses.
6. Unit economics de un SaaS regulatorio: margen por consulta (no costo por
   token), mezcla de planes, y el costo marginal de un cliente más.

**Criterios de aceptación:**
- [ ] `04-economia/theory/00-plan.md` con el temario re-especificado y una nota
      explícita de qué quedó absorbido por 01/03 y dónde.
- [ ] Seis secciones con ejemplo numérico sobre el corpus chileno, tabla de estado
      del arte y sección "Conexiones" (mismo template que 01–03).
- [ ] Código ejecutable por sección; sin duplicar componentes de `prod_lib.py`.
- [ ] `README.md` del módulo y `mkdocs.yml` actualizados.

### B6 · P1 · Expandir corpus de 16 a ~40 documentos
Prerrequisito de B7: `02 §8` ya advirtió que 16 documentos no alcanzan para que las
diferencias entre arquitecturas sean estadísticamente detectables, y un grafo sobre
16 nodos no muestra nada interesante.

- [ ] ~24 documentos nuevos en `shared/corpus_chileno/`, con **densidad de
      relaciones** (modifica / deroga / reglamenta / cita) suficiente para un grafo.
- [ ] Se respetan las restricciones de abstención documentadas en el README del
      corpus: nada que responda `gd-025`, `gd-026` ni `gd-027`.
- [ ] Inventario del README del corpus actualizado con el fenómeno que ejercita
      cada documento nuevo.
- [ ] `uv run python 02-retrieval/code/08-benchmark-retrievers.py` sigue corriendo
      y se registra cómo se movieron las métricas.

### B7 · P0 · Masterclass 05 — Ontologías y representación del conocimiento
**Encuadre:** una ontología es un sistema de clasificación formalizado. El
clasificador presupuestario chileno (partida → capítulo → programa → subtítulo →
ítem → asignación) *es* una ontología; COFOG, CIIU, CIUO y UNSPSC también. El
módulo no enseña una disciplina nueva: le pone nombre y formalismo a una que el
autor ya ejerce, y la conecta con retrieval.

Cubre el material transversal #5 (capas invariantes) y #6 (knowledge graphs,
GraphRAG) del inventario temático.

**Temario:**
1. Qué es una ontología y por qué ya construiste varias. Taxonomía vs. tesauro vs.
   ontología vs. grafo de conocimiento.
2. Modelado del dominio regulatorio chileno: entidades, relaciones, *competency
   questions* como método de diseño.
3. Cuánto formalismo comprar: SKOS < RDF/OWL < property graph pragmático. Regla de
   decisión explícita para no sobre-construir.
4. Identidad y llaves canónicas: entity resolution (DIPRES = Dirección de
   Presupuestos), RUT de organismos, `cut_comunal`. Es *record linkage*.
5. Extraer la ontología del corpus: extracción con LLM + esquema Pydantic, tasa de
   error medida, costo por documento.
6. Vigencia temporal y versionado normativo: bitemporalidad; caso Ley 21.210 ↔
   DL 825, ya sembrado en el corpus y marcado como pendiente en `02 §9`.
7. Del grafo al retrieval: GraphRAG y su economía. Costo de indexación de Microsoft
   GraphRAG; LightRAG, HippoRAG, RAPTOR, agentic graph retrieval. Cuándo el grafo
   paga y cuándo un filtro de metadatos (`02 §7`) hace lo mismo por 1/100.
8. Evaluar un sistema con ontología: el grafo debe ganarse el lugar con deltas e
   intervalos de confianza (aparato de `01 §8`), más métricas de la ontología misma.
9. La ontología como foso competitivo: por qué un modelo mejor no comoditiza esto.

**Criterios de aceptación:**
- [ ] `00-plan.md` + 9 secciones con el template de 01–03.
- [ ] Grafo real sobre el corpus (networkx + esquema Pydantic) y extractor con LLM.
- [ ] Benchmark honesto grafo vs. híbrido reutilizando `golden-retrieval.json`, con
      IC. Si el grafo no gana, **el resultado negativo se publica**.
- [ ] Documento sobre EU AI Act / governance dentro del módulo (ver B9).

### B8 · P0 · Masterclass 06 — Harness agéntico
**Encuadre:** el harness es diseño institucional. El mismo modelo rinde
radicalmente distinto según las reglas, la información y los incentivos del entorno
en que opera — por eso rediseñar el bucle rinde más que cambiar de modelo.

"Harness" aquí significa el **harness agéntico** (bucle, contexto, herramientas,
permisos), no el eval harness — ese está en `01 §9` y se trata como su antepasado.
Cubre el tema #7 del inventario (orquestación Claude Code + Codex).

**Temario:**
1. Qué es un harness y por qué decide la calidad más que el modelo. El bucle
   percibir → decidir → actuar → observar.
2. Context engineering: el contexto como recurso escaso con problema de asignación.
   Compaction, memoria externa. Conecta con 02 (retrieval como mecanismo de contexto).
3. Diseño de herramientas: la tool como contrato. Granularidad, esquemas, mensajes
   de error como canal de enseñanza. Por qué una tool que devuelve 40k tokens rompe
   el bucle.
4. MCP como estándar: protocolo y primitivas; consolidación bajo Linux Foundation.
5. Arquitecturas multiagente: cuándo gana un solo agente con buen contexto.
   Orquestador/trabajador; subagentes como aislamiento de contexto, no como "más
   inteligencia". El patrón Claude Code planificador + Codex implementador.
6. Control, permisos y sandboxing: checkpoints humanos, idempotencia de tool-calls
   (sembrado en `03 §6`), sandbox de ejecución. Conecta con `03 §11`.
7. Evaluar agentes: se evalúan trayectorias, no respuestas. Métricas de trayectoria
   vs. de resultado. Límites de los benchmarks agénticos.
8. Costo y latencia del bucle: un agente hace N llamadas, no una; la varianza del
   costo por tarea es el problema real. Caching de prefijo. Cuándo cortar un bucle
   que no converge.
9. El harness como práctica personal: `AGENTS.md`, hooks, skills, subagentes,
   perfiles de Codex. Qué delegar y qué no.

**Criterios de aceptación:**
- [ ] `00-plan.md` + 9 secciones con el template de 01–03.
- [ ] **Servidor MCP funcional sobre el corpus chileno** como entregable de §4
      (artefacto de portfolio, no ejercicio).
- [ ] Un agente evaluado con métricas de trayectoria sobre una tarea del dominio.

### B9 · P2 · Documento de governance / EU AI Act
Material de alto valor para entrevista y **perecedero**. Vive como documento dentro
de 05-ontologias, no como masterclass propia. El gancho con 05 es real: un
fine-tuning sustancial puede reclasificar a un *deployer* como *provider*.

- [ ] Fechas del Omnibus verificadas **contra fuente primaria (DOUE)** antes de
      escribirlas como hechos. El inventario cita dic-2027 (Anexo III) y ago-2028
      (Anexo I): tratar como `[verificar]` hasta confirmar.
- [ ] Cada afirmación normativa con enlace a su fuente.

### B10 · P2 · Enlazar artículos publicados desde blog-drafts
`blog-drafts/` está vacío salvo el README. El artículo sobre riesgos fiscales de
desastres naturales (Chile/España) ya está publicado en el blog.

- [ ] `blog-drafts/README.md` enlaza los artículos publicados derivados del estudio.
- [ ] No duplicar el contenido en este repo — solo enlazar.

---

## Decisiones tomadas

**2026-08-03 · Revisión del inventario temático y hoja de ruta**

| Decisión | Resolución |
|---|---|
| Significado de "harness" | **Agéntico** (bucle, contexto, herramientas), no eval harness. |
| Qué hacer con 04-economia | **Re-especificar** a 6 secciones sobre lo no cubierto, no cerrarlo por absorción ni ejecutarlo como estaba. |
| Orden de ejecución | **04 → 05 → 06.** No abrir módulos nuevos con uno pendiente visible en el sitio público. |
| Governance / EU AI Act | **Documento dentro de 05**, con verificación contra fuente primaria. |

**Desfase detectado en el inventario temático.** El inventario (fechado al 2 de
agosto) describe un estado de ~junio: daba por pendiente el bloque de evaluación de
IR (hecho en `02 §8`), pedía completar 02–04 (02 y 03 cerrados), y asumía
scaffolding de módulos 05–09 que nunca se creó. La hoja de ruta vigente es **este
archivo**, no el inventario.

---

## Fuera de alcance

Explícito para que ninguna sesión futura los reabra por inercia:

- **Módulos 08 (model adaptation) y 09 (MLOps/cloud)** del inventario. Baja
  prioridad para el perfil; el propio inventario los marcaba como "conceptuales,
  sin pipelines".
- **Bloque D del inventario (robótica, SO-101, electrónica).** Fuera del alcance
  declarado del repo (IA aplicada a corpus regulatorio y fiscal chileno). Si se
  retoma, va en repo propio.
- **Bloque C (Mercado Público, desastres naturales, CV PNUD).** Cada uno tiene su
  hogar: `buscador-oportunidades`, el blog, y material personal respectivamente.
  Aquí solo se enlaza (B10).
