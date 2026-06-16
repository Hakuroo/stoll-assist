import Link from "next/link";
import { Check, Send, X } from "lucide-react";
import { approveOutbound, rejectOutbound, sendOutbound } from "../actions";
import {
  apiGet,
  canOperate,
  dashboardOutboxPath,
  getCurrentUser,
  outboxSendConfigPath,
  type OutboxReviewItem,
  type OutboxSendConfig
} from "../lib/api";
import { formatDate, stateLabel } from "../lib/format";
import { EmptyState, ErrorState } from "../components/State";

export default async function ResponsesPage() {
  const user = await getCurrentUser();
  const result = await apiGet<OutboxReviewItem[]>(dashboardOutboxPath());
  const sendConfig = await apiGet<OutboxSendConfig>(outboxSendConfigPath());
  const showActions = user ? canOperate(user.role) : false;
  const sendEnabled = sendConfig.ok ? sendConfig.data.whatsapp_send_enabled : false;

  return (
    <>
      <header className="page-header">
        <div>
          <h1 className="page-title">Bandeja de respuestas</h1>
          <p className="page-subtitle">Borradores verificados en modo REVIEW_REQUIRED.</p>
        </div>
      </header>

      {!result.ok ? (
        <ErrorState message={result.error} />
      ) : result.data.length === 0 ? (
        <EmptyState title="No hay respuestas para revisar" />
      ) : (
        <div className="grid">
          {result.data.map((item) => (
            <article className="panel panel-pad" key={item.outbound_id}>
              <div className="page-header" style={{ marginBottom: 12 }}>
                <div>
                  <div className="item-meta">
                    <span className="badge warn">{stateLabel(item.status)}</span>
                    <span>{formatDate(item.created_at)}</span>
                    <span>{item.display_name ?? item.recipient}</span>
                  </div>
                  <Link className="primary-text" href={`/conversaciones/${item.conversation_id}`}>
                    Ver conversación
                  </Link>
                </div>
                <div className="item-meta">
                  <Send size={15} />
                  <span>provider_message_id: {item.provider_message_id ?? "NULL"}</span>
                  <span>intentos: {item.send_attempt_count}</span>
                  {item.sent_at ? <span>enviado: {formatDate(item.sent_at)}</span> : null}
                </div>
              </div>

              <div className="grid two">
                <section>
                  <h2 className="section-title">Cliente</h2>
                  <p>{item.customer_message_text ?? "Mensaje sin texto"}</p>
                </section>
                <section>
                  <h2 className="section-title">Borrador</h2>
                  <div className="draft-box">{item.body_text}</div>
                </section>
              </div>

              <section className="stack-item">
                <h2 className="section-title">Fuentes y verificación</h2>
                <div className="button-row">
                  {item.knowledge_sources.map((source) => (
                    <span className="badge" key={`${source.external_key}-${source.version}`}>
                      {source.external_key} v{source.version}
                    </span>
                  ))}
                  <span className={`badge ${item.verification.status === "APPROVED" ? "ok" : "danger"}`}>
                    {stateLabel(item.verification.status)}
                  </span>
                  <span className="badge">{item.verification.reason_code}</span>
                </div>
                <p className="muted small">
                  Aprobar cambia el estado del borrador. Enviar WhatsApp requiere una acción separada.
                </p>
                {!sendEnabled ? <p className="muted small">Envío deshabilitado.</p> : null}
                {item.error_message ? (
                  <p className="error-text">Error de envío: {item.error_message}</p>
                ) : null}
              </section>

              {showActions ? (
                <div className="button-row">
                  {item.status === "PENDING_REVIEW" ? (
                    <>
                      <form action={approveOutbound.bind(null, item.outbound_id)}>
                        <button className="button primary" type="submit">
                          <Check size={16} />
                          Aprobar
                        </button>
                      </form>
                      <form action={rejectOutbound.bind(null, item.outbound_id)} className="button-row">
                        <input
                          className="reject-input"
                          name="reason"
                          placeholder="Motivo de rechazo"
                          minLength={3}
                        />
                        <button className="button danger" type="submit">
                          <X size={16} />
                          Rechazar
                        </button>
                      </form>
                    </>
                  ) : null}
                  {item.status === "APPROVED" && sendEnabled ? (
                    <form action={sendOutbound.bind(null, item.outbound_id)}>
                      <button className="button primary" type="submit">
                        <Send size={16} />
                        Enviar
                      </button>
                    </form>
                  ) : null}
                </div>
              ) : null}
            </article>
          ))}
        </div>
      )}
    </>
  );
}
