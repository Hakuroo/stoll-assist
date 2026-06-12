import type { Metadata } from "next";
import "./globals.css";
import { Shell } from "./components/Shell";

export const metadata: Metadata = {
  title: "Stoll Assist",
  description: "Panel local de operadores para Stoll Assist"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es-AR">
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
