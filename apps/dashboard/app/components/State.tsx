export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      {detail ? <span>{detail}</span> : null}
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="error-state">
      <strong>No se pudo cargar</strong>
      <span>{message}</span>
    </div>
  );
}
