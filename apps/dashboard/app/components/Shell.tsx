import Link from "next/link";
import { BookOpen, Inbox, LogOut, MessageSquareText } from "lucide-react";
import { logoutAction } from "../actions";
import { getCurrentUser } from "../lib/api";

const navItems = [
  { href: "/conversaciones", label: "Conversaciones", icon: MessageSquareText },
  { href: "/respuestas", label: "Respuestas", icon: Inbox },
  { href: "/conocimiento", label: "Conocimiento", icon: BookOpen }
];

export async function Shell({ children }: { children: React.ReactNode }) {
  const user = await getCurrentUser();
  if (!user) {
    return <main className="login-content">{children}</main>;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">S</span>
          <div>
            <strong>Stöll Assist</strong>
            <span>Grupo Stöll</span>
          </div>
        </div>
        <nav className="nav-list" aria-label="Principal">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <Link key={item.href} href={item.href} className="nav-link">
                <Icon size={18} />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="session-box">
          <strong>{user.display_name}</strong>
          <span>{user.tenant_name}</span>
          <span className="badge">{user.role}</span>
          <form action={logoutAction}>
            <button className="nav-link logout-button" type="submit">
              <LogOut size={18} />
              <span>Salir</span>
            </button>
          </form>
        </div>
      </aside>
      <main className="content">{children}</main>
    </div>
  );
}
