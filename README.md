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

## Incremento v0.3 — normalización de mensajes

La API ahora convierte los payloads de Meta en entidades internas:

- crea o actualiza el contacto de WhatsApp;
- reutiliza una conversación activa o crea una nueva;
- guarda el mensaje con tipo, texto, fecha del proveedor y metadatos;
- conserva el mensaje original para auditoría;
- marca el webhook como `PROCESSED`, `IGNORED` o `FAILED`;
- mantiene idempotencia tanto a nivel webhook como a nivel mensaje.

Aplicar la migración y probar:

```powershell
.\scripts\apply-migrations.ps1
.\scripts\test-normalization.ps1
```

## v0.4 — procesamiento asíncrono

El endpoint de WhatsApp ahora solo valida, persiste y encola el evento. Un contenedor `worker`
consume la cola de Redis, reclama el evento de forma atómica y ejecuta la normalización fuera
del ciclo HTTP. Los eventos registran `queued_at`, `attempt_count` y `last_attempt_at`.

Prueba local:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-async-processing.ps1
```


## Conversation state machine

Version 0.5 adds explicit, audited conversation states:

- `AUTOMATED`: the assistant may produce a response.
- `HUMAN_REQUIRED`: automation is suspended and a handoff is open.
- `HUMAN_ACTIVE`: an operator owns the conversation; automation must remain silent.
- `CLOSED`: the conversation is immutable; a new inbound message creates a new conversation.

Local operator endpoints are available under `/operator/conversations`. They are intentionally unauthenticated only for development; authentication and roles are added before any public deployment.


## Policy engine v0.6

The deterministic policy layer evaluates newly persisted inbound messages before any AI response is considered. Rules are tenant-scoped and stored in PostgreSQL. High-risk topics such as prices, structural calculations, guarantees, complaints and internal information automatically move an `AUTOMATED` conversation to `HUMAN_REQUIRED` and create an auditable handoff.

Local operator endpoints:

```text
GET  /operator/policies
POST /operator/policies/preview
```

## v0.7 — Base de conocimiento aprobada

- Importa archivos YAML como borradores.
- Nunca publica conocimiento automáticamente.
- Versiona cambios después de una publicación.
- Busca únicamente registros con estado `published`.
- Conserva afirmaciones permitidas y prohibidas por fuente.
- Audita cada búsqueda para facilitar evaluación y soporte.

Prueba local:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-knowledge-base.ps1
```

## Response planner (v0.8)

The response planner combines four independent controls before any future message can be sent:

1. conversation state;
2. deterministic policy evaluation;
3. published knowledge retrieval;
4. explicit allowed and forbidden claims.

It stores a response plan for each inbound message with one of four decisions:
`ANSWER`, `ASK`, `HANDOFF`, or `IGNORE`. Version 0.8 does not send WhatsApp
messages. Its draft is an auditable intermediate artifact for later generation and
verification.

## Version 0.9 — verificación de borradores

Antes de habilitar una respuesta automática, el sistema verifica que:

- exista un borrador;
- no exceda el límite de longitud;
- no contenga precios, garantías o afirmaciones técnicas restringidas;
- no reproduzca afirmaciones explícitamente prohibidas;
- una respuesta informativa esté respaldada por conocimiento publicado;
- no introduzca números ausentes de las fuentes;
- una solicitud de datos sea breve y contenga preguntas.

Los resultados quedan registrados en `response_verifications`. Un borrador rechazado deriva la conversación a una persona.

## Version 0.10 — bandeja de salida y aprobación humana

Las respuestas verificadas ya no quedan como texto suelto: se transforman en registros de
`outbound_messages`. Para Grupo Stöll el modo predeterminado es `REVIEW_REQUIRED`, por lo
que cada respuesta segura queda en `PENDING_REVIEW` hasta que un operador la aprueba o
rechaza.

Controles incorporados:

- solo se crea una salida para planes `ANSWER` o `ASK` con verificación `APPROVED`;
- la conversación debe continuar en estado `AUTOMATED`;
- la aprobación comprueba que el borrador no haya cambiado desde la verificación;
- aprobar o rechazar deja un evento de auditoría;
- esta versión no envía mensajes reales a WhatsApp.

Prueba local:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-outbox-review.ps1
```
