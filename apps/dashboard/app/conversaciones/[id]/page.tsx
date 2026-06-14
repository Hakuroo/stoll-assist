import { CheckCircle, Lock, RotateCcw, UserCheck } from "lucide-react";
import {
  closeConversation,
  returnConversation,
  takeConversation
} from "../../actions";
import { apiGet, conversationDetailPath, type ConversationDetail } from "../../lib/api";
import { formatDate, shortId, stateLabel } from "../../lib/format";
import { EmptyState, ErrorState } from "../../components/State";

export default async function ConversationDetailPage({
  params
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const result = await apiGet<ConversationDetail>(conversationDetailPath(id));

  if (!result.ok) {
    return <ErrorState message={result.error} />;
  }

  const detail = result.data;
  const conversation = detail.conversation;

  return (
    <>
      <header className="page-header">
        <div>
          <h1 className="page-title">{conversation.display_name ?? "Conversación"}</h1>
          <p className="page-subtitle">
            {conversation.phone_e164 ?? conversation.whatsapp_user_id} - {shortId(conversation.conversation_id)}
          </p>
        </div>
        <div className="button-row">
          {conversation.state !== "HUMAN_ACTIVE" && conversation.state !== "CLOSED" ? (
            <form action={takeConversation.bind(null, conversation.conversation_id)}>
              <button className="button primary" type="submit">
                <UserCheck size={16} />
                Tomar
              </button>
            </form>
          ) : null}
          {conversation.state !== "AUTOMATED" && conversation.state !== "CLOSED" ? (
            <form action={returnConversation.bind(null, conversation.conversation_id)}>
              <button className="button" type="submit" aria-label="Devolver a automatización">
                <RotateCcw size={16} />
                Devolver
              </button>
            </form>
          ) : null}
          {conversation.state !== "CLOSED" ? (
            <form action={closeConversation.bind(null, conversation.conversation_id)}>
              <button className="button danger" type="submit">
                <Lock size={16} />
                Cerrar
              </button>
            </form>
          ) : null}
        </div>
      </header>

      <section className="grid two" style={{ marginBottom: 16 }}>
        <div className="panel panel-pad">
          <h2 className="section-title">Estado</h2>
          <div className="button-row">
            <span className={`badge ${conversation.requires_human ? "warn" : "ok"}`}>
              {stateLabel(conversation.state)}
            </span>
            {conversation.assigned_operator ? (
              <span className="badge">{conversation.assigned_operator}</span>
            ) : null}
          </div>
          <p className="muted">
            Último cambio: {conversation.last_state_reason ?? "sin motivo registrado"}
          </p>
        </div>
        <div className="panel panel-pad">
          <h2 className="section-title">Handoffs</h2>
          {detail.handoffs.length === 0 ? (
            <p className="muted">Sin derivaciones registradas.</p>
          ) : (
            detail.handoffs.map((handoff) => (
              <div className="stack-item" key={handoff.handoff_id}>
                <div className="item-meta">
                  <span className="badge">{stateLabel(handoff.status)}</span>
                  <span>{formatDate(handoff.created_at)}</span>
                </div>
                <strong>{handoff.reason_code}</strong>
                <p className="muted">{handoff.summary ?? "Sin resumen"}</p>
              </div>
            ))
          )}
        </div>
      </section>

      <div className="detail-layout">
        <section className="panel panel-pad">
          <h2 className="section-title">Historial</h2>
          {detail.messages.length === 0 ? (
            <EmptyState title="Sin mensajes" />
          ) : (
            <div className="message-list">
              {detail.messages.map((message) => (
                <article
                  key={message.message_id}
                  className={`message ${message.direction === "OUTBOUND" ? "outbound" : ""}`}
                >
                  <div className="message-meta">
                    <span>{message.direction === "INBOUND" ? "Cliente" : "Asistente"}</span>
                    <span>{message.message_type}</span>
                    <span>{formatDate(message.created_at)}</span>
                  </div>
                  <p>{message.body_text ?? "Mensaje sin texto"}</p>
                </article>
              ))}
            </div>
          )}
        </section>

        <aside className="grid">
          <section className="panel panel-pad">
            <h2 className="section-title">Últimos planes</h2>
            {detail.response_plans.length === 0 ? (
              <p className="muted">Sin planes.</p>
            ) : (
              detail.response_plans.map((plan) => (
                <div className="stack-item" key={plan.plan_id}>
                  <div className="item-meta">
                    <span className="badge">{stateLabel(plan.decision)}</span>
                    <span>{formatDate(plan.created_at)}</span>
                  </div>
                  <strong>{plan.reason_code}</strong>
                  <p className="muted">{plan.reply_goal}</p>
                  {plan.draft_reply ? <div className="draft-box">{plan.draft_reply}</div> : null}
                </div>
              ))
            )}
          </section>

          <section className="panel panel-pad">
            <h2 className="section-title">Verificaciones</h2>
            {detail.verifications.length === 0 ? (
              <p className="muted">Sin verificaciones.</p>
            ) : (
              detail.verifications.map((verification) => (
                <div className="stack-item" key={verification.verification_id}>
                  <div className="item-meta">
                    <span className={`badge ${verification.status === "APPROVED" ? "ok" : "danger"}`}>
                      {stateLabel(verification.status)}
                    </span>
                    <span>{formatDate(verification.created_at)}</span>
                  </div>
                  <p>{verification.reason_code}</p>
                  {verification.status === "APPROVED" ? (
                    <div className="item-meta">
                      <CheckCircle size={14} />
                      <span>Validado contra conocimiento aprobado</span>
                    </div>
                  ) : null}
                </div>
              ))
            )}
          </section>
        </aside>
      </div>
    </>
  );
}
