import type { Metadata } from "next";
import "./globals.css";
import { Shell } from "./components/Shell";

export const metadata: Metadata = {
  title: "Stöll Assist",
  description: "Panel local de operadores para Stöll Assist"
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
