FROM python:3.12-slim AS base

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml .
COPY app ./app
RUN pip install --no-cache-dir .

FROM base AS test

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir ".[dev]"

COPY tests ./tests

CMD ["sh", "-c", "for migration in /app/migrations/*.sql; do [ -e \"$migration\" ] || { echo 'No migrations found'; exit 1; }; echo \"Applying $(basename \"$migration\")\"; psql -v ON_ERROR_STOP=1 -f \"$migration\"; done && python -m compileall app tests && python -m pytest -q"]

FROM base AS production

EXPOSE 8000
