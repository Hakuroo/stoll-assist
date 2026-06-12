# Stöll Assist — starter v0.1

Base inicial para un sistema multiempresa de atención y calificación de consultas por WhatsApp.

## Objetivo

- Responder únicamente con conocimiento aprobado.
- Extraer datos comerciales de la conversación.
- Derivar a una persona cuando existe riesgo, falta información o el cliente lo pide.
- Suspender automáticamente la respuesta automática cuando un operador toma la conversación.
- Mantener auditoría de cada decisión.
- Poder reutilizar la plataforma para distintas empresas mediante `tenant_id`.

## Principios

1. **Lenguaje natural, identidad transparente:** el agente puede llamarse “Agustina”, pero se presenta una vez como asistente digital del equipo.
2. **Abstención antes que invención:** si una afirmación no está respaldada, no se envía.
3. **IA como componente, no como autoridad:** reglas deterministas controlan precios, promesas, temas técnicos y derivaciones.
4. **Multiempresa desde el modelo de datos:** toda entidad comercial pertenece a un tenant.
5. **Portabilidad:** PostgreSQL, Redis, S3 y contenedores; sin quedar atados a un proveedor concreto.

## Stack elegido

- Python + FastAPI + Pydantic
- PostgreSQL + pgvector
- SQLAlchemy + Alembic
- Redis para colas, locks y rate limits
- S3 compatible para adjuntos
- OpenAI Responses API detrás de un adaptador de proveedor
- Next.js para el panel (se agrega en la siguiente etapa)
- Langfuse/OpenTelemetry para trazas de IA
- Sentry para errores de aplicación
- Docker para desarrollo y despliegue

## Inicio local

1. Copiar `.env.example` a `.env`
2. Ejecutar:

```bash
docker compose up --build
```

3. Abrir:

```text
http://localhost:8000/health
```

## Estado

Este repositorio es un **scaffold técnico**, no una aplicación terminada. La siguiente etapa implementa:

- persistencia real de webhooks;
- normalización de mensajes;
- máquina de estados de conversación;
- políticas por tenant;
- extracción estructurada;
- retrieval híbrido;
- verificador de afirmaciones;
- panel humano.

## Incremento v0.2 — recepción durable de webhooks

La API ahora:

- valida la firma HMAC enviada por Meta;
- valida que el cuerpo sea JSON;
- crea una identidad estable para el evento;
- guarda el payload completo en PostgreSQL;
- detecta reintentos idénticos sin crear filas duplicadas.

Aplicar migraciones:

```powershell
.\scripts\apply-migrations.ps1
```

Probar localmente con una firma válida:

```powershell
.\scripts\test-webhook.ps1
```
