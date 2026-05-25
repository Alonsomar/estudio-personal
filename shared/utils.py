"""Utilidades compartidas entre masterclasses.

Provee:
- get_project_root(): ruta absoluta a la raíz del proyecto.
- get_logger(name): logger configurado con rich para output legible.
- load_corpus_doc(filename): carga un documento del corpus chileno como string.
"""

import logging
from pathlib import Path

from rich.logging import RichHandler


def get_project_root() -> Path:
    """Retorna la ruta absoluta a la raíz del proyecto (donde está pyproject.toml)."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    raise FileNotFoundError("No se encontró pyproject.toml en ningún directorio padre")


def get_logger(name: str, level: str | None = None) -> logging.Logger:
    """Retorna un logger configurado con RichHandler.

    Args:
        name: Nombre del logger (típicamente __name__).
        level: Nivel de logging. Si no se especifica, usa LOG_LEVEL del .env o INFO.
    """
    import os

    from dotenv import load_dotenv

    load_dotenv()

    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")

    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = RichHandler(rich_tracebacks=True, markup=True)
        handler.setLevel(level)
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger


def load_corpus_doc(filename: str) -> str:
    """Carga un documento del corpus chileno como string UTF-8.

    Args:
        filename: Nombre del archivo dentro de shared/corpus_chileno/.

    Returns:
        Contenido del documento como string.

    Raises:
        FileNotFoundError: Si el archivo no existe en el corpus.
    """
    corpus_dir = get_project_root() / "shared" / "corpus_chileno"
    filepath = corpus_dir / filename
    if not filepath.exists():
        raise FileNotFoundError(f"Documento no encontrado en corpus: {filepath}")
    return filepath.read_text(encoding="utf-8")
