"""Sección 7 — Configuración por entorno y manejo de secretos.

Demuestra los patrones de §7 que no necesitan Docker para verse:

  1. Config como entrada del entorno (ServiceSettings) vs os.environ[...] regado.
  2. Validación en el borde: un valor inválido falla al arrancar, no en runtime.
  3. Secretos como SecretStr: no se imprimen ni en repr ni en stack traces.
  4. Verificación automática: un scan que falla el build si un secreto se cuela
     en un log o en un dump de config (apto para CI).

Los artefactos de despliegue (Dockerfile, docker-compose, migración) están en
examples/deploy/.

Ejecutar:

    uv run python 03-produccion/code/07-config-secrets.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pydantic import ValidationError  # noqa: E402

from prod_lib import (  # noqa: E402
    ServiceSettings,
    StructuredLogger,
    redact_secrets,
    scan_for_secrets,
)

SEP = "=" * 72

# Valores FALSOS solo para la demo (no son secretos reales). _env_file=None
# hace la demo hermética: no lee el .env real del proyecto.
DEMO_KW = dict(
    _env_file=None,
    llm_model="claude-haiku-4-5",
    openai_api_key="sk-proj-FAKEdemo1234567890abcdef",
    database_url="postgresql://admin:s3cr3tpass@db.supabase.co:5432/postgres",
)


# --------------------------------------------------------------------------- #
# 1. Config: entorno tipado vs os.environ regado.
# --------------------------------------------------------------------------- #
def demo_config() -> ServiceSettings:
    print(SEP)
    print("1. CONFIG POR ENTORNO — tipada, con defaults, validada")

    print("\n  anti-patrón: os.environ['X'] sin default")
    try:
        _ = os.environ["RAG_TIMEOUT_QUE_NADIE_SETEO"]
    except KeyError as e:
        print(f"    ✗ KeyError {e} — el proceso explota en el primer request que")
        print("      toque esa variable, no al arrancar. Falla tarde y feo.")

    settings = ServiceSettings(**DEMO_KW)
    print("\n  ServiceSettings: un solo lugar, con defaults sensatos. Por ej.:")
    print(f"    llm_model={settings.llm_model!r} (de env)   "
          f"k_default={settings.k_default} (default)")
    print(f"    max_retries={settings.max_retries}   llm_timeout_s={settings.llm_timeout_s} "
          "(cierra el 'sin timeout' de §2)")
    return settings


def demo_validation() -> None:
    print("\n" + SEP)
    print("2. VALIDACIÓN EN EL BORDE — config inválida no arranca")
    print("\n  intento de arrancar con k_default=999 (fuera de rango 1..20):")
    try:
        ServiceSettings(_env_file=None, k_default=999)
        print("    (no debería pasar)")
    except ValidationError as e:
        err = e.errors()[0]
        print(f"    ✗ ValidationError: {err['loc'][0]} → {err['msg']}")
        print("      el deploy falla rápido y claro, no a las 3 AM con un valor raro.")


# --------------------------------------------------------------------------- #
# 3 + 4. Secretos: ocultos por tipo + verificación automática.
# --------------------------------------------------------------------------- #
def demo_secrets(settings: ServiceSettings) -> None:
    print("\n" + SEP)
    print("3. SECRETOS — SecretStr no se imprime")
    print(f"\n  repr(openai_api_key) = {settings.openai_api_key!r}")
    print(f"  str del settings dump NO trae el valor: "
          f"{'sk-proj' not in str(settings.model_dump())}")
    print("\n  public_dict() para loguear/exponer en /info (secretos redactados):")
    pub = settings.public_dict()
    print("    " + json.dumps({k: pub[k] for k in
          ["llm_model", "openai_api_key", "database_url", "k_default"]}, ensure_ascii=False))

    print("\n" + SEP)
    print("4. VERIFICACIÓN AUTOMÁTICA — un secreto en un log/dump falla el build")

    # (a) el dump público está limpio.
    clean = json.dumps(pub)
    print(f"\n  scan(public_dict): {scan_for_secrets(clean) or 'LIMPIO ✓'}")

    # (b) un log naive con credenciales crudas SÍ se detecta.
    naive = ("conectando con OPENAI_API_KEY=sk-proj-FAKEdemo1234567890abcdef "
             "a postgresql://admin:s3cr3tpass@db:5432/x")
    hits = scan_for_secrets(naive)
    print(f"  scan(log naive): {len(hits)} secretos detectados → CI debería FALLAR")
    print(f"    redactado: {redact_secrets(naive)}")

    # (c) el StructuredLogger de §5 redacta por defecto (defensa en profundidad).
    import io
    buf = io.StringIO()
    log = StructuredLogger(service="rag-fiscal", stream=buf)  # redact=True por default
    log.error("startup_misconfig", api_key="sk-proj-FAKEdemo1234567890abcdef")
    leaked = "sk-proj" in buf.getvalue()
    print(f"\n  StructuredLogger(redact=True): ¿se filtró el key al log? {leaked}")
    print(f"    {buf.getvalue().strip()}")


def main() -> None:
    settings = demo_config()
    demo_validation()
    demo_secrets(settings)
    print("\n" + SEP)
    print("La config es entrada del entorno; los secretos viven en el vault, nunca")
    print("en el repo ni en los logs. Artefactos de despliegue en examples/deploy/.")


if __name__ == "__main__":
    main()
