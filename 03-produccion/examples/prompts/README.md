# Prompts versionados — §3 Gestión de prompts

Registry de prompts del RAG fiscal. Cada prompt es **código**: vive en control
de versiones, se carga por nombre + versión, y se identifica por un hash de
contenido.

## Convención de archivos

```
<name>.<version>.txt
```

- `name`: kebab-case, identifica el prompt lógico (`rag-fiscal`).
- `version`: `vN` (`v1`, `v2`, …). Subir versión es un acto deliberado.
- El **cuerpo del archivo ES el prompt**. Sin frontmatter, para que el hash
  (`sha256` de los bytes) sea estable y comparable.

## Variables

Los placeholders usan `{{ var }}`. Las requeridas para el RAG son:

- `{{ context }}` — los fragmentos recuperados, ya numerados.
- `{{ query }}` — la pregunta del usuario.

`PromptRegistry` (en `code/prod_lib.py`) **valida al cargar** que cada prompt
declare las variables requeridas; un prompt inválido nunca entra al registry.

## Render seguro

El renderizador (`render_safe`) sustituye en **una sola pasada**: los valores
se insertan literalmente y no se re-evalúan. Un fragmento del corpus que
contenga `{{ query }}` o `{` queda como texto, no como instrucción. Esa es la
defensa contra inyección corpus→prompt (más en §11).

## Versiones actuales

| Prompt | Versión | Cambio respecto a la anterior |
|---|---|---|
| `rag-fiscal` | `v1` | Baseline: cita fragmentos, no inventa. |
| `rag-fiscal` | `v2` | Formato de cita explícito `[Fragmento N]`, concisión (2-4 frases), frase fija para "no encontrado", y separación contexto/instrucción (fragmentos como datos, no órdenes). |

## Uso

```python
from prod_lib import PromptRegistry, RAGOrchestrator

reg = PromptRegistry("03-produccion/examples/prompts")
prompt = reg.get("rag-fiscal")          # última versión (v2)
prompt = reg.get("rag-fiscal", "v1")    # versión específica
rag = RAGOrchestrator(retriever=hybrid, llm_client=llm, prompt_template=prompt)
```

Demo completa (registry, hash, versionado, render seguro, A/B):

```bash
uv run python 03-produccion/code/03-prompt-registry.py          # offline, gratis
uv run python 03-produccion/code/03-prompt-registry.py --live   # A/B con LLM real
```
