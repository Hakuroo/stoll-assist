# ADR-001 — Stack técnico

**Estado:** aceptado para el piloto  
**Fecha:** 2026-06-12

## Decisión

### Canal

Usar directamente WhatsApp Business Platform Cloud API.

**Razones:**
- menor dependencia y menor margen agregado por intermediarios;
- acceso directo a webhooks, plantillas y estados;
- camino natural hacia Embedded Signup al convertirse en Tech Provider;
- arquitectura portable.

Twilio o un BSP se conservan como alternativa para acelerar onboarding o atender clientes que ya trabajan con uno.

### Backend

Python + FastAPI + Pydantic.

**Razones:**
- excelente encaje con extracción estructurada, evaluaciones y procesamiento de documentos;
- tipado de esquemas de entrada y salida;
- buen valor de portfolio para roles de IA/backend;
- suficiente rendimiento porque el trabajo pesado se procesa en workers.

### Base de datos

PostgreSQL administrado con pgvector.

No se ata la arquitectura a Supabase, Neon, RDS ni otro proveedor. En desarrollo se utiliza una imagen estándar. En producción puede desplegarse sobre cualquier PostgreSQL compatible.

### Procesamiento asíncrono

Primera etapa: Redis + workers y estado durable en PostgreSQL.

Temporal se incorpora cuando existan varios clientes, procesos largos, reintentos complejos, temporizadores y flujos de aprobación que justifiquen su costo operativo.

### IA

OpenAI Responses API detrás de una interfaz interna:

- clasificador/extractor económico;
- redactor de bajo riesgo;
- verificador;
- posibilidad de reemplazar modelos sin modificar el dominio.

No se permite que el modelo envíe directamente una respuesta a WhatsApp.

### Retrieval

Búsqueda híbrida:
- filtros obligatorios por `tenant_id`, estado `published` y vigencia;
- búsqueda lexical;
- búsqueda vectorial;
- reranking opcional;
- respaldo por fragmentos identificables.

### Archivos

S3 compatible:
- MinIO local;
- S3 o proveedor compatible en producción;
- cifrado, URLs firmadas y separación lógica por tenant.

### Panel

Next.js + TypeScript:
- bandeja humana;
- edición y aprobación del conocimiento;
- reglas;
- auditoría;
- métricas.

## Herramientas que no serán el núcleo

- n8n: solo integraciones periféricas y notificaciones.
- Dify: útil para prototipos, no como motor principal.
- LangChain/LangGraph: no se agrega hasta que aporte una ventaja concreta.
- Pinecone/Qdrant: no se necesitan en la etapa inicial.
- Kubernetes: no se necesita en la etapa inicial.
