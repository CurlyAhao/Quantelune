import type { Metadata } from "next";
import { WorkspacePreferencesProvider } from "@/providers/workspace-preferences-provider";
import { QueryProvider } from "@/providers/query-provider";
import { LanguageProvider } from "@/providers/language-provider";
import { ThemeProvider } from "@/components/theme-provider";
import { AppFrame } from "@/components/app-frame";
import { DesktopRouteBridge } from "@/components/desktop-route-bridge";
import { DesktopStartupBoundary } from "@/components/desktop-startup-boundary";
import { Toaster } from "@/components/ui/sonner";
import "./globals.css";
import "@/components/real-workspace.css";

// Fonts are resolved from local system stacks defined in globals.css
// (--font-space-grotesk / --font-plex-sans / --font-plex-mono). This keeps the
// dev server fully offline — no Google Fonts network dependency — while
// gracefully using the named typefaces when they are installed locally.

export const metadata: Metadata = {
  title: "Quantelune",
  description: "Quantelune",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="relative min-h-screen">
        <DesktopStartupBoundary>
          <LanguageProvider>
            <DesktopRouteBridge />
            <ThemeProvider>
              <QueryProvider>
                <WorkspacePreferencesProvider>
                  <AppFrame>{children}</AppFrame>
                </WorkspacePreferencesProvider>
              </QueryProvider>
              <Toaster
                position="top-center"
                richColors
                closeButton
                toastOptions={{
                  classNames: {
                    title: "font-heading",
                    description: "font-mono text-xs",
                  },
                }}
              />
            </ThemeProvider>
          </LanguageProvider>
        </DesktopStartupBoundary>
      </body>
    </html>
  );
}
