# syntax=docker/dockerfile:1
#
# Imagen minimalista del servicio RAG. Build context = raíz del repo
# (ver docker-compose.yml). Patrón uv: deps en una capa cacheable, código en
# otra, usuario no-root, sin nada superfluo.

FROM python:3.12-slim

# uv: gestor de deps reproducible (mismo que usamos en local).
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# 1) Solo el manifiesto + lock primero: esta capa se reusa mientras no cambien
#    las dependencias, aunque cambie el código. Acelera los rebuilds.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 2) El código que el servicio necesita (no el repo entero).
COPY shared/ ./shared/
COPY 02-retrieval/code/ ./02-retrieval/code/
COPY 03-produccion/ ./03-produccion/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Usuario no-root: si el proceso se compromete, no es root del container.
RUN useradd --create-home app && chown -R app:app /app
USER app

# HOST=0.0.0.0 para escuchar fuera del localhost del container.
ENV HOST=0.0.0.0 PORT=8000
EXPOSE 8000

# Healthcheck contra el /healthz de §2.
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"

CMD ["uv", "run", "python", "03-produccion/code/02-fastapi-rag.py", "--server"]
