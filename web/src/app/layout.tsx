import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = { title: "LAIP · Business rules to AI context", description: "Extract evidence-linked business rules and legacy system context for AI-assisted modernization." };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
