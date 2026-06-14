import { redirect } from "next/navigation";
import { loginAction } from "../actions";
import { getCurrentUser } from "../lib/api";

export default async function LoginPage({
  searchParams
}: {
  searchParams?: Promise<{ error?: string }>;
}) {
  const user = await getCurrentUser();
  if (user) {
    redirect("/conversaciones");
  }
  const resolved = await searchParams;
  const hasError = resolved?.error === "1";

  return (
    <section className="login-panel">
      <div>
        <span className="brand-mark">S</span>
        <h1 className="page-title">Stöll Assist</h1>
        <p className="page-subtitle">Ingresá para operar el panel local.</p>
      </div>
      <form className="login-form" action={loginAction}>
        <label>
          Email
          <input name="email" type="email" autoComplete="email" required />
        </label>
        <label>
          Contraseña
          <input name="password" type="password" autoComplete="current-password" required />
        </label>
        {hasError ? (
          <p className="error-text">Email o contraseña inválidos.</p>
        ) : null}
        <button className="button primary" type="submit">
          Ingresar
        </button>
      </form>
    </section>
  );
}
