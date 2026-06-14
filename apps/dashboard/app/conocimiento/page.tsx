import { apiGet, knowledgePath, type KnowledgeItem } from "../lib/api";
import { formatDate, stateLabel } from "../lib/format";
import { EmptyState, ErrorState } from "../components/State";

export default async function KnowledgePage() {
  const result = await apiGet<KnowledgeItem[]>(knowledgePath());

  return (
    <>
      <header className="page-header">
        <div>
          <h1 className="page-title">Conocimiento</h1>
          <p className="page-subtitle">Versiones publicadas y borradores locales.</p>
        </div>
      </header>

      {!result.ok ? (
        <ErrorState message={result.error} />
      ) : result.data.length === 0 ? (
        <EmptyState title="No hay conocimiento cargado" detail="Cargalo desde la API local." />
      ) : (
        <div className="grid">
          {result.data.map((item) => (
            <article className="panel panel-pad" key={item.item_id}>
              <div className="page-header" style={{ marginBottom: 12 }}>
                <div>
                  <div className="item-meta">
                    <span className="badge">{item.external_key}</span>
                    <span className={item.status === "published" ? "badge ok" : "badge"}>
                      {stateLabel(item.status)}
                    </span>
                    <span>v{item.version}</span>
                    <span>{formatDate(item.updated_at)}</span>
                  </div>
                  <h2 className="section-title">{item.title}</h2>
                </div>
              </div>

              <p className="knowledge-content">{item.content}</p>

              <div className="grid two">
                <section>
                  <h3 className="section-title">Afirmaciones permitidas</h3>
                  {item.allowed_claims.length === 0 ? (
                    <p className="muted">Sin afirmaciones declaradas.</p>
                  ) : (
                    <ul className="claim-list">
                      {item.allowed_claims.map((claim) => (
                        <li key={claim}>{claim}</li>
                      ))}
                    </ul>
                  )}
                </section>
                <section>
                  <h3 className="section-title">Afirmaciones prohibidas</h3>
                  {item.forbidden_claims.length === 0 ? (
                    <p className="muted">Sin restricciones declaradas.</p>
                  ) : (
                    <ul className="claim-list">
                      {item.forbidden_claims.map((claim) => (
                        <li key={claim}>{claim}</li>
                      ))}
                    </ul>
                  )}
                </section>
              </div>
            </article>
          ))}
        </div>
      )}
    </>
  );
}
