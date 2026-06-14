import Link from "next/link";
import { AlertTriangle } from "lucide-react";
import {
  apiGet,
  dashboardConversationsPath,
  type ConversationState,
  type ConversationSummary
} from "../lib/api";
import { formatDate, stateLabel } from "../lib/format";
import { EmptyState, ErrorState } from "../components/State";

const filters: Array<{ value?: ConversationState; label: string }> = [
  { label: "Todas" },
  { value: "AUTOMATED", label: "Automatizadas" },
  { value: "HUMAN_REQUIRED", label: "Requieren atención" },
  { value: "HUMAN_ACTIVE", label: "Con operador" },
  { value: "CLOSED", label: "Cerradas" }
];

export default async function ConversationsPage({
  searchParams
}: {
  searchParams?: Promise<{ state?: string }>;
}) {
  const resolved = await searchParams;
  const selected = parseState(resolved?.state);
  const result = await apiGet<ConversationSummary[]>(dashboardConversationsPath(selected));

  return (
    <>
      <header className="page-header">
        <div>
          <h1 className="page-title">Bandeja de conversaciones</h1>
          <p className="page-subtitle">Atención local de Grupo Stöll.</p>
        </div>
      </header>

      <div className="toolbar">
        {filters.map((filter) => {
          const href = filter.value ? `/conversaciones?state=${filter.value}` : "/conversaciones";
          const active = filter.value === selected || (!filter.value && !selected);
          return (
            <Link key={filter.label} href={href} className={`segmented-link ${active ? "active" : ""}`}>
              {filter.label}
            </Link>
          );
        })}
      </div>

      {!result.ok ? (
        <ErrorState message={result.error} />
      ) : result.data.length === 0 ? (
        <EmptyState title="No hay conversaciones para este filtro" />
      ) : (
        <section className="panel overflow">
          <table className="list-table">
            <thead>
              <tr>
                <th>Contacto</th>
                <th>Teléfono</th>
                <th>Último mensaje</th>
                <th>Estado</th>
                <th>Fecha</th>
              </tr>
            </thead>
            <tbody>
              {result.data.map((conversation) => (
                <tr
                  key={conversation.conversation_id}
                  className={conversation.requires_human ? "row-highlight" : undefined}
                >
                  <td>
                    <Link className="primary-text" href={`/conversaciones/${conversation.conversation_id}`}>
                      {conversation.display_name ?? "Sin nombre"}
                    </Link>
                    {conversation.requires_human ? (
                      <div className="item-meta">
                        <AlertTriangle size={14} />
                        <span>Requiere seguimiento humano</span>
                      </div>
                    ) : null}
                  </td>
                  <td>{conversation.phone_e164 ?? conversation.whatsapp_user_id}</td>
                  <td>
                    <span>{conversation.last_message_body ?? "Sin mensajes"}</span>
                    <div className="muted small">
                      {conversation.last_message_type ?? "sin tipo"}
                    </div>
                  </td>
                  <td>
                    <span className={`badge ${conversation.requires_human ? "warn" : ""}`}>
                      {stateLabel(conversation.state)}
                    </span>
                  </td>
                  <td>{formatDate(conversation.last_message_at ?? conversation.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}

function parseState(value: string | undefined): ConversationState | undefined {
  if (
    value === "AUTOMATED" ||
    value === "HUMAN_REQUIRED" ||
    value === "HUMAN_ACTIVE" ||
    value === "CLOSED"
  ) {
    return value;
  }
  return undefined;
}
