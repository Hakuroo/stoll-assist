import Link from "next/link";
import { BookOpen, Inbox, MessageSquareText } from "lucide-react";

const navItems = [
  { href: "/conversaciones", label: "Conversaciones", icon: MessageSquareText },
  { href: "/respuestas", label: "Respuestas", icon: Inbox },
  { href: "/conocimiento", label: "Conocimiento", icon: BookOpen }
];

export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">S</span>
          <div>
            <strong>Stoll Assist</strong>
            <span>Grupo Stoll</span>
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
        <p className="dev-note">Panel local sin autenticacion. No exponer publicamente.</p>
      </aside>
      <main className="content">{children}</main>
    </div>
  );
}
