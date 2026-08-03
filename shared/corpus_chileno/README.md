# Corpus Chileno

Directorio de documentos sintéticos pero realistas del dominio regulatorio y fiscal
chileno. Estos documentos sirven como datos de prueba compartidos entre todas las
masterclasses.

## Tipos de documentos esperados

- **Decretos**: textos con estructura de decreto supremo o exento.
- **Glosas presupuestarias**: partidas, capítulos y programas del presupuesto público.
- **Fragmentos normativos**: artículos de leyes, circulares del SII, oficios de la
  Contraloría, en español jurídico-técnico chileno.
- **Publicaciones del Diario Oficial**: extractos con formato típico del DO.

## Convenciones

- Formato: archivos `.txt` o `.md`, UTF-8.
- Naming: `tipo-NN-descripcion.txt` (ej: `decreto-01-subvencion-escolar.txt`).
- Cada masterclass puede agregar documentos aquí si los necesita; nunca guardar
  documentos de corpus dentro de la carpeta de una masterclass.
- Mantener los documentos concisos (1-3 páginas) para facilitar pruebas rápidas.

## Inventario actual (40 documentos)

El corpus se expandió desde 4 documentos núcleo a 16 en la masterclass
`02-retrieval`, para poder **medir** diferencias entre arquitecturas de retrieval
(no solo ilustrar mecánica). Cada documento añadido ejercita deliberadamente un
fenómeno de retrieval del temario:

| Documento | Tipo | Fenómeno de retrieval que ejercita |
|---|---|---|
| `circular-01-sii-iva-digital.txt` | circular | núcleo — IVA servicios digitales |
| `decreto-01-subvencion-escolar.txt` | decreto | núcleo — subvención escolar preferencial (SEP) |
| `glosa-01-presupuesto-salud.txt` | glosa | núcleo — presupuesto Salud 2024 |
| `norma-01-ley-lobby.txt` | ley | núcleo — Ley Nº 20.730 de Lobby |
| `ley-01-dl-825-iva-base.txt` | ley | **versión temporal**: DL 825 *antes* de la reforma |
| `ley-02-ley-21210-modernizacion.txt` | ley | **versión temporal + referencia cruzada**: la Ley 21.210 modifica el DL 825 (fuente que cita la circular-01) |
| `circular-02-sii-renta-propyme.txt` | circular | **distractor**: comparte "SII", "Ley 21.210", "régimen" pero trata de Renta, no de IVA |
| `circular-03-sii-ppm-honorarios.txt` | circular | **distractor + tabla**: tasas de retención por año |
| `tabla-01-valores-tributarios-2024.txt` | tabla | **tabla pura**: UTM/UF/UTA mensuales en grilla |
| `glosa-02-presupuesto-educacion.txt` | glosa | **tabla + distractor**: presupuesto Educación con grilla de montos (distractor de Salud) |
| `decreto-02-reglamento-ley-lobby.txt` | decreto | **referencia cruzada**: reglamenta y cita artículos de la Ley 20.730 |
| `norma-02-ley-20880-probidad.txt` | ley | **distractor temático cercano**: probidad/declaración de patrimonio, vecino del lobby |
| `circular-04-sii-iva-exenciones.txt` | circular | **enlace cross-dominio + sinonimia**: exenciones de IVA en salud y educación |
| `oficio-01-contraloria-subvenciones.txt` | oficio | **referencia cruzada + género nuevo**: dictamen que cita la Ley 20.248 y el decreto |
| `do-01-extracto-decreto-aranceles.txt` | diario oficial | **género nuevo + sinonimia**: define el valor de la USE; enlaza varios organismos |
| `glosa-03-presupuesto-trabajo.txt` | glosa | **sinonimia engañosa**: "subsidio" al empleo vs "subvención" escolar |

### Ampliación a 40 documentos (B6, 2026-08-03)

Motivada por dos límites ya documentados en el repo: `02 §8` marcó que 16
documentos no bastan para que las diferencias entre arquitecturas de retrieval
sean estadísticamente detectables, y `04 §3` mostró cuántas queries de golden
hacen falta para detectar una degradación — el mismo problema de poder
estadístico, aplicado a comparar sistemas en vez de comparar un modelo cuantizado.
Los 24 documentos nuevos están organizados en **cuatro clusters temáticos** con
densidad de relaciones (modifica / deroga / reglamenta / cita), pensados como
insumo directo para el grafo de conocimiento de `05-ontologias`:

**Cluster: compras públicas** (ley → reforma → reglamento → resolución → dictamen → DO)

| Documento | Tipo | Fenómeno |
|---|---|---|
| `ley-03-ley-19886-compras-publicas.txt` | ley | núcleo — Ley de Bases de Contratos Administrativos |
| `ley-04-ley-21634-moderniza-compras.txt` | ley | **reforma**: introduce la compra ágil (100 UTM), modifica la Ley 19.886 |
| `decreto-03-reglamento-compras-publicas.txt` | decreto | **reglamenta** la Ley 19.886; define trato directo y emergencia |
| `resolucion-01-chilecompra-compra-agil.txt` | resolución | **instruye** sobre la compra ágil introducida por la Ley 21.634 |
| `oficio-02-contraloria-trato-directo.txt` | oficio | **aplica el reglamento** a un caso de fraccionamiento; cita ley, reforma y decreto |
| `do-02-extracto-licitacion-publica.txt` | diario oficial | género nuevo — llamado a licitación + toma de razón de la resolución |

**Cluster: tributario ampliado** (Renta, factura electrónica, servicios digitales)

| Documento | Tipo | Fenómeno |
|---|---|---|
| `ley-05-dl-824-renta-base.txt` | ley | núcleo — Ley de Impuesto a la Renta (paralelo al DL 825 de IVA) |
| `circular-05-sii-factura-electronica.txt` | circular | **cita cruzada**: crédito fiscal del DL 825 + excepción para prestadores del art. 8º n) |
| `circular-06-sii-credito-especial-construccion.txt` | circular | **distractor de precisión**: crédito ≠ exención; cita la circular-04 por su número real |
| `resolucion-02-sii-registro-plataformas.txt` | resolución | **reglamenta la operatoria** del art. 8º n) creado por la Ley 21.210 |
| `oficio-03-sii-consulta-plataforma-intermediacion.txt` | oficio | **aplica tres normas a la vez**: art. 8º n), exención de transporte, resolución de registro |
| `tabla-02-tasas-impuesto-renta-2024.txt` | tabla | tabla pura, paralela a `tabla-01`; referencia cruzada a Pro Pyme (Ley 21.210) |

**Cluster: presupuesto y ejecución** (ley de presupuestos → glosas → modificación → oficio DIPRES)

| Documento | Tipo | Fenómeno |
|---|---|---|
| `ley-06-ley-presupuestos-2024-articulado.txt` | ley | núcleo — articulado de la Ley de Presupuestos 2024 (las glosas ya existían sin su ley) |
| `glosa-04-presupuesto-obras-publicas.txt` | glosa | **referencia cruzada**: cita la Ley 19.886 y el límite de compra ágil |
| `glosa-05-presupuesto-interior.txt` | glosa | **referencia cruzada + emergencia**: enlaza FNDR, municipalidades y trato directo por emergencia |
| `decreto-04-modificacion-presupuestaria.txt` | decreto | **modifica una asignación** de `glosa-01-presupuesto-salud`; prueba de vigencia de glosa tras modificación |
| `oficio-04-dipres-ejecucion-presupuestaria.txt` | oficio | **cita el decreto de modificación** como ejemplo de aplicación de la regla que instruye |

**Cluster: probidad y educación pública** (dos cadenas que convergen en un dictamen)

| Documento | Tipo | Fenómeno |
|---|---|---|
| `ley-07-ley-18575-bases-administracion.txt` | ley | núcleo — probidad administrativa; marco común para lobby y declaración de intereses |
| `decreto-05-reglamento-declaracion-intereses.txt` | decreto | **reglamenta** la Ley 20.880 (ya presente); cita la Ley 18.575 |
| `resolucion-03-registro-lobbistas.txt` | resolución | **distingue** el registro de lobby (Ley 20.730) de la declaración de intereses (Ley 20.880) |
| `ley-08-ley-20248-subvencion-preferencial.txt` | ley | núcleo — texto completo de la SEP (antes solo citada por `decreto-01` y `oficio-01`) |
| `ley-09-ley-21040-educacion-publica.txt` | ley | **modifica competencias**: traspaso del servicio educacional; cita la Ley 20.248 |
| `decreto-06-reglamento-servicios-locales.txt` | decreto | **reglamenta** el traspaso; cita ambas leyes anteriores |
| `oficio-05-contraloria-traspaso-slep.txt` | oficio | **converge**: cita la Ley 21.040, el decreto de traspaso Y el dictamen original (`oficio-01`) sobre uso de la SEP |

### Efecto medido en el benchmark de retrieval (`02 §8`)

Correr `02-retrieval/code/08-benchmark-retrievers.py` sobre el corpus de 40
documentos (mismas 27 queries de golden, mismos documentos relevantes) baja el
recall@3 en todos los sistemas — más distractores compiten por el mismo top-k:

| Sistema | recall@3 con 16 docs | recall@3 con 40 docs | Δ |
|---|---|---|---|
| BM25 | 0.907 | 0.870 | −0.037 |
| TF-IDF | 0.907 | 0.870 | −0.037 |
| Denso | 0.981 | 0.889 | **−0.093** |
| Hybrid-RRF | 0.926 | 0.926 | +0.000 |
| Hybrid-weighted | 0.963 | 0.926 | −0.037 |
| Hybrid + HyDE | 0.981 | 0.944 | −0.037 |
| Hybrid + LLM-rerank | 0.981 | 0.926 | −0.056 |

El denso es el más afectado: los clusters nuevos comparten vocabulario temático
con las queries originales (SII, presupuesto, subvención, ley) sin ser la fuente
correcta, y esa cercanía semántica es exactamente lo que el retrieval denso
confunde. Hybrid-RRF es el único sistema que no se movió — consistente con `02
§3`, donde la fusión por rango amortigua los falsos positivos de un solo método.
Ningún delta es señal de que un sistema "empeoró": es la muestra volviéndose más
realista. Con más candidatos compitiendo, el golden de 27 queries sigue siendo el
límite de poder estadístico documentado en `02 §8` — ampliarlo es tarea aparte.

### Restricción a respetar (no romper 01-evals)

El golden dataset de `01-evals/examples/golden-dataset-rag-fiscal.json` contiene
queries de **abstención** que dependen de que ciertas fuentes *no* existan en el
corpus. No deben agregarse documentos que respondan:

- **DFL Nº 3 sobre educación rural** (query `gd-026`).
- **Ley de Transparencia de 2022** (query `gd-025`).
- **Presupuesto de cualquier año distinto de 2024**, p. ej. 2025 (query `gd-027`).
