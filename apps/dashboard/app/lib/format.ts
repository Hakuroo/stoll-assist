export function formatDate(value: string | null): string {
  if (!value) {
    return "Sin fecha";
  }
  return new Intl.DateTimeFormat("es-AR", {
    dateStyle: "short",
    timeStyle: "short"
  }).format(new Date(value));
}

export function stateLabel(value: string): string {
  const labels: Record<string, string> = {
    AUTOMATED: "Automatizada",
    HUMAN_REQUIRED: "Requiere persona",
    HUMAN_ACTIVE: "Con operador",
    CLOSED: "Cerrada",
    PENDING_REVIEW: "Pendiente",
    APPROVED: "Aprobada",
    REJECTED: "Rechazada",
    QUEUED: "En cola",
    SENT: "Enviada",
    FAILED: "Fallida",
    UNKNOWN: "Incierta",
    DELIVERED: "Entregada",
    READ: "Leída",
    CANCELLED: "Cancelada",
    ANSWER: "Responder",
    ASK: "Preguntar",
    HANDOFF: "Derivar",
    IGNORE: "Ignorar",
    OPEN: "Abierto",
    TAKEN: "Tomado",
    RESOLVED: "Resuelto",
    draft: "Borrador",
    published: "Publicado",
    archived: "Archivado",
    SKIPPED: "Omitida"
  };
  return labels[value] ?? value;
}

export function shortId(value: string): string {
  return value.slice(0, 8);
}
