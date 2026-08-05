"""Configuración de pytest: hace importables las librerías de las masterclasses.

`retrieval_lib.py` y `prod_lib.py` viven dentro de la carpeta `code/` de su
masterclass y se importan por path (los scripts demo funcionan porque el
directorio del script queda en `sys.path`). Aquí replicamos eso para los tests.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for lib_dir in (
    ROOT,
    ROOT / "02-retrieval" / "code",
    ROOT / "03-produccion" / "code",
    ROOT / "05-ontologias" / "code",
    ROOT / "06-harness" / "code",
):
    if str(lib_dir) not in sys.path:
        sys.path.insert(0, str(lib_dir))
