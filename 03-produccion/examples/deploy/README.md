# Despliegue — §7 (nivel B: local end-to-end)

Artefactos para levantar el RAG completo **en tu máquina**, sin cuentas cloud:
app (FastAPI) + Postgres/pgvector + Redis, orquestados con docker-compose.

```
deploy/
├── Dockerfile            # imagen del servicio (uv, no-root, healthcheck)
├── docker-compose.yml    # app + db (pgvector) + cache (redis)
├── .env.example          # plantilla de config; copiar a .env y completar
├── initdb/
│   └── 0001_init.sql     # schema, corre en el primer arranque de Postgres
└── alembic/
    └── versions/
        └── 0001_init.py  # la MISMA migración como revisión alembic (con rollback)
```

## Levantarlo

```bash
cd 03-produccion/examples/deploy
cp .env.example .env          # completá OPENAI_API_KEY y POSTGRES_PASSWORD
docker compose up --build
```

Luego:

```bash
curl localhost:8000/healthz
curl localhost:8000/readyz
curl -X POST localhost:8000/query \
     -H 'content-type: application/json' \
     -d '{"query": "¿Tasa de IVA digital?", "k": 3}'
```

## Decisiones

- **uv en el contenedor**: el mismo gestor que en local; deps reproducibles
  desde `uv.lock`. Las dependencias se instalan en una capa aparte del código,
  así un cambio de código no reinstala todo.
- **No-root**: el proceso corre como `app`, no como root.
- **Secretos solo en `.env`**: el `docker-compose.yml` commiteado no contiene
  ninguna credencial; las toma de `.env` (gitignored). Es el patrón de §7.
  > El `scan_for_secrets` de §7 marca el `DATABASE_URL` de este `.env.example`
  > por su forma `user:pass@`, aunque el password sea un placeholder
  > (`cambiar-en-local`). Es el clásico falso positivo de plantillas: en CI los
  > archivos `*.example` se **allowlistean**, exactamente como hacen gitleaks /
  > trufflehog. El scanner correctamente es naive; el allowlist es config de CI.
- **`initdb` vs `alembic`**: el `.sql` de `initdb/` es el atajo para el nivel B
  (Postgres lo corre solo al crear el volumen). Para producción se usa
  **alembic**: migraciones versionadas con `upgrade()`/`downgrade()`, donde el
  `downgrade` es el plan de rollback. Ambos crean el mismo schema.

## Migraciones en producción (alembic)

```bash
alembic upgrade head     # aplica las migraciones pendientes
alembic downgrade -1     # rollback de la última (el plan de reversa)
```

El `downgrade()` de cada revisión es lo que convierte "rompimos producción con
una migración" en "un comando para volver atrás", en vez de una restauración de
backup a mano.

## Por qué NO Kubernetes

Para un SaaS chileno pequeño-mediano (1-3 personas, miles de usuarios), este
compose —o su equivalente en Fly.io / Railway / un VPS— alcanza y sobra. K8s
agrega un plano de control, YAML por capas, y un costo operativo que no paga
valor a esta escala. Se justifica cuando hay decenas de servicios y un equipo
de plataforma dedicado; antes de eso, es complejidad por miedo. Ver
`theory/07-despliegue-config.md`.
