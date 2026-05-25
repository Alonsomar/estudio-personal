# AGENTS.md — Guía para LLMs y colaboradores

## Propósito del proyecto

Sistema de estudio personal estructurado en 4 masterclasses orientadas a construir
productos de IA sobre corpus regulatorio y fiscal chileno. El objetivo es dominar
evaluación de sistemas IA, information retrieval, patrones de producción y economía
de inferencia, con foco práctico y aplicado.

## Audiencia

El autor es un economista chileno radicado en España. No es ingeniero ML de formación.
Construye productos sobre corpus regulatorio y fiscal chileno (normativa, presupuesto
público, Diario Oficial) con stack Python + uv, APIs de LLMs y Supabase.

Implicaciones para LLMs:
- Explicar conceptos ML/NLP desde intuición económica cuando sea posible.
- No asumir familiaridad con álgebra lineal avanzada; sí asumir comodidad con
  estadística, series de tiempo y optimización.
- Preferir analogías del dominio regulatorio/fiscal chileno en los ejemplos.

## Estructura del repositorio

```
estudio-personal/
├── shared/              # Código y datos compartidos entre masterclasses
│   ├── corpus_chileno/  # Documentos sintéticos de normativa chilena
│   ├── llm_clients.py   # Wrappers para Anthropic y OpenAI
│   └── utils.py         # Logger, paths, carga de corpus
├── 01-evals/            # Masterclass: Evaluación de sistemas IA
├── 02-retrieval/        # Masterclass: Information Retrieval
├── 03-produccion/       # Masterclass: Patrones de producción
├── 04-economia/         # Masterclass: Economía de inferencia
└── blog-drafts/         # Borradores de posts derivados del estudio
```

## Estructura interna de cada masterclass

Cada carpeta `NN-nombre/` debe contener:

| Carpeta      | Propósito                                                  |
|--------------|------------------------------------------------------------|
| `theory/`    | Documentos conceptuales, explicaciones, marcos teóricos    |
| `code/`      | Scripts ejecutables, notebooks, implementaciones           |
| `examples/`  | Casos de uso concretos, datasets de prueba                 |
| `diagrams/`  | Imágenes generadas (matplotlib, exports), diagramas        |
| `notes/`     | Apuntes sueltos, ideas, preguntas pendientes               |
| `README.md`  | Índice de la masterclass: objetivos, sesiones, estado       |

No crear carpetas adicionales sin justificación explícita.

## Convenciones de naming

- **Archivos**: kebab-case → `01-intro-evals.md`, `benchmark-runner.py`
- **Código Python**: snake_case para variables, funciones y módulos
- **Documentos**: formato `NN-titulo-corto.md` (numerados para orden de lectura)
- **Carpetas de masterclass**: `NN-nombre` con número de dos dígitos

## Ejecución de código

Siempre ejecutar con uv, nunca con python directo:

```bash
# Correcto
uv run python script.py
uv run pytest

# Incorrecto
python script.py
pytest
```

## Uso de shared/

El paquete `shared/` es importable desde cualquier script del proyecto:

```python
from shared.utils import get_project_root, get_logger, load_corpus_doc
from shared.llm_clients import get_anthropic_client, get_openai_client
```

Si una masterclass necesita documentos de corpus adicionales, agregarlos en
`shared/corpus_chileno/`, no en la carpeta de la masterclass.

## Convenciones de Markdown

- Front matter mínimo solo cuando sea necesario (título, fecha).
- Headings jerárquicos claros: `#` para título, `##` para secciones, `###` para
  subsecciones.
- Diagramas de arquitectura y flujos en **Mermaid** embebido (no ASCII art).
- Imágenes matplotlib guardadas en `diagrams/` de la masterclass correspondiente
  y referenciadas con rutas relativas: `![desc](diagrams/nombre.png)`.

## Reglas de contenido

- Priorizar **profundidad sobre amplitud**: mejor una sección bien desarrollada
  que tres superficiales.
- Preferir **ejemplos numéricos concretos** sobre explicaciones abstractas.
- Todo código debe ser **ejecutable**, no pseudocódigo (salvo para ilustrar
  conceptos de alto nivel).
- Usar datos del corpus chileno siempre que sea posible para los ejemplos.

## Reglas para LLMs

- **No inventar APIs**: si no estás seguro de que un método/endpoint existe,
  indícalo explícitamente.
- **No fabricar números**: si un dato no es verificable, marcarlo como
  `[dato estimado]` o `[verificar]`.
- **Marcar incertidumbre**: usar `> ⚠️ No verificado: ...` para afirmaciones
  de las que no estés seguro.
- **Preferir Mermaid** a ASCII art para diagramas.
- **No generar contenido fuera de la carpeta** de la masterclass en la que se
  está trabajando, salvo archivos compartidos en `shared/`.
- **Leer este archivo al inicio** de cada sesión de trabajo.

## Convenciones de commits

Usar [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` nueva sección, script o funcionalidad
- `docs:` documentación, README, notas
- `fix:` corrección de errores en código o contenido
- `chore:` mantenimiento, dependencias, configuración

Granularidad: **un commit por sección o unidad de trabajo terminada**.
No acumular cambios de múltiples secciones en un solo commit.
