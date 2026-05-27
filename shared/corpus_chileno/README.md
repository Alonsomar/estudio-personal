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

## Inventario actual (16 documentos)

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

### Restricción a respetar (no romper 01-evals)

El golden dataset de `01-evals/examples/golden-dataset-rag-fiscal.json` contiene
queries de **abstención** que dependen de que ciertas fuentes *no* existan en el
corpus. No deben agregarse documentos que respondan:

- **DFL Nº 3 sobre educación rural** (query `gd-026`).
- **Ley de Transparencia de 2022** (query `gd-025`).
- **Presupuesto de cualquier año distinto de 2024**, p. ej. 2025 (query `gd-027`).
