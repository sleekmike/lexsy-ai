import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], display: "swap", variable: "--font-inter" });
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000';


export const metadata: Metadata = {
  title: "Lexsy SAFE — AI-assisted Legal Draft Filler",
  description: "Minimal, fast frontend for uploading SAFE docs, filling placeholders, and downloading a finished .docx.",
  metadataBase: new URL("https://lexsy-safe-web.local"),
  icons: { icon: "/favicon.ico" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${inter.variable} font-sans bg-background text-foreground antialiased`}>
        <div className="min-h-screen flex flex-col">
          <header className="sticky top-0 z-30 border-b border-border backdrop-blur bg-white/70 dark:bg-black/30">
            <div className="mx-auto max-w-6xl px-4 py-3 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="h-8 w-8 rounded-xl bg-primary/90"></div>
                <span className="text-base font-semibold tracking-tight">Lexsy SAFE</span>
              </div>
              <nav className="flex items-center gap-3 text-sm text-muted-foreground">
                <a href="https://lexsy-safe-api.fly.dev/health" target="_blank" rel="noreferrer" className="hover:text-foreground">API Health</a>
                <a href="https://github.com/" target="_blank" rel="noreferrer" className="hover:text-foreground">Repo</a>
              </nav><div className="hidden sm:block text-xs text-muted-foreground"><span className="px-2 py-1 rounded-lg border border-border bg-muted/60">API: {apiBase}</span></div>
            </div>
          </header>

          <main className="flex-1">
            <div className="mx-auto max-w-6xl px-4 py-8">
              {children}
            </div>
          </main>

          <footer className="border-t border-border">
            <div className="mx-auto max-w-6xl px-4 py-6 text-sm text-muted-foreground">
              <span>© {new Date().getFullYear()} Lexsy. Built with Next.js 14.</span>
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}
