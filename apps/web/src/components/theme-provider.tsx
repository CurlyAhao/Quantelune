"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";
import { AppearanceProvider } from "@/providers/appearance-provider";

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      storageKey="statforge.theme.v1"
      disableTransitionOnChange
    >
      <AppearanceProvider>{children}</AppearanceProvider>
    </NextThemesProvider>
  );
}
