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

## Pruebas locales

Desde un checkout limpio, las pruebas se ejecutan con un unico comando:

```bash
docker compose run --rm --build test
```

El servicio `test` construye una imagen separada con las dependencias de desarrollo,
levanta un PostgreSQL efimero exclusivo para tests con `tmpfs`, aplica `init.sql` y las
migraciones, ejecuta `compileall` y corre `pytest`. No toca la base local de desarrollo
ni publica el puerto de PostgreSQL de tests al host.
No requiere `.env`: usa credenciales locales dummy y no conecta OpenAI ni WhatsApp real.
La imagen normal de `api` y `worker` se construye desde el target `production`, sin
instalar dependencias de testing.

## Panel local de operadores

El panel web vive en `apps/dashboard` y usa Next.js + TypeScript. Consume la API
FastAPI por HTTP; no accede a PostgreSQL directamente.

Levantar API, worker y panel:

```bash
docker compose up --build api worker dashboard
```

Abrir:

```text
http://localhost:3000
```

Crear el primer OWNER local una sola vez, con entrada segura:

```powershell
docker compose run --rm api python -m app.bootstrap_owner
```

Tambien se puede pasar `OWNER_EMAIL`, `OWNER_PASSWORD`, `OWNER_DISPLAY_NAME` y
`OWNER_TENANT_SLUG` como variables locales del shell. No guardes esas credenciales en
archivos versionados ni las subas a Git.

El panel usa sesiones opacas emitidas por FastAPI. La cookie solo contiene un token
aleatorio; PostgreSQL guarda su hash. Los endpoints `/operator/*` requieren una sesion
valida y aplican permisos por tenant.

## Numero de prueba de WhatsApp Cloud

La API expone el webhook en:

```text
GET/POST /webhooks/whatsapp
```

Para configurar credenciales locales mas adelante, copia `.env.example` a `.env` y completa
solo en tu entorno local:

```text
META_VERIFY_TOKEN=...
META_APP_SECRET=...
META_ACCESS_TOKEN=...
META_PHONE_NUMBER_ID=...
META_API_VERSION=vXX.X
WHATSAPP_SEND_ENABLED=false
```

No subas `.env` a Git ni pegues esos valores en logs, issues o PRs. El endpoint `GET`
responde el challenge de Meta cuando el verify token coincide. El `POST` valida
`X-Hub-Signature-256` contra el cuerpo crudo antes de persistir o encolar.

La aprobacion de un outbound no envia WhatsApp. El envio es una accion separada del panel
y de `POST /operator/outbox/{outbound_id}/send`; ademas queda bloqueado mientras
`WHATSAPP_SEND_ENABLED=false`, que es el valor por defecto.

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
- guarda el payload validado para procesamiento asincronico;
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

## Version 0.11 - redaccion asistida por IA

La IA se usa solo como capa de redaccion. El planner sigue decidiendo si corresponde
`ANSWER`, `ASK`, `HANDOFF` o `IGNORE`; las fuentes siguen siendo las knowledge keys ya
seleccionadas; y cada borrador vuelve a pasar por el verifier antes de llegar a
`outbound_messages`.

La bandera `LLM_DRAFTING_ENABLED` queda en `false` por defecto. Con la bandera apagada,
se conserva el borrador determinista del planner y no se llama a ningun proveedor. Con
la bandera encendida, el proveedor genera una salida estructurada con `draft_reply`,
`used_knowledge_keys`, `claims`, `should_handoff`, `reason_code` y `confidence`. Si hay
timeout, rate limit, refusal, error o salida invalida, se registra el fallo en
`response_generations` y se usa el fallback determinista cuando exista.

Controles incorporados:

- OpenAI se invoca mediante Responses API y Structured Outputs; no se habilitan tools ni
  function calling;
- el mensaje del cliente y el historial reciente viajan como datos no confiables, nunca
  dentro de instrucciones de sistema;
- el historial se limita antes de enviarlo al proveedor;
- `used_knowledge_keys` debe pertenecer al conocimiento publicado del tenant actual;
- cada `response_plan` reclama una unica `response_generation` con lease temporal antes
  de llamar al proveedor, para evitar llamadas externas duplicadas entre workers;
- `should_handoff=true` deriva la conversacion y no crea outbound;
- ningun borrador se envia a WhatsApp en esta version;
- aun aprobado, el outbound queda en `PENDING_REVIEW` por `REVIEW_REQUIRED`.
