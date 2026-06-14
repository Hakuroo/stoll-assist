export default function Loading() {
  return (
    <div className="empty-state" aria-live="polite">
      <strong>Cargando panel...</strong>
      <span>Consultando la API local.</span>
    </div>
  );
}
