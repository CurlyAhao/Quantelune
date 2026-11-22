"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { useTheme } from "next-themes";
import {
  APPEARANCE_KEY,
  DEFAULT_APPEARANCE,
  parseAppearance,
  type Appearance,
} from "@/lib/appearance";

const AppearanceContext = createContext<{
  value: Appearance;
  ready: boolean;
  saved: boolean;
  update: (patch: Partial<Omit<Appearance, "version">>) => void;
} | null>(null);

export function AppearanceProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [value, setValue] = useState(DEFAULT_APPEARANCE);
  const [ready, setReady] = useState(false);
  const [saved, setSaved] = useState(true);
  const { setTheme } = useTheme();
  useEffect(() => {
    let parsed: Appearance | null = null;
    try {
      parsed = parseAppearance(localStorage.getItem(APPEARANCE_KEY));
    } catch {
      /* Storage can be unavailable; keep the usable device default. */
    }
    const next = parsed ?? { ...DEFAULT_APPEARANCE };
    setValue(next);
    setSaved(parsed !== null);
    setTheme(next.mode);
    setReady(true);
    const sync = (event: StorageEvent) => {
      if (event.key !== APPEARANCE_KEY) return;
      const next = parseAppearance(event.newValue);
      if (!next) return;
      setValue(next);
      setTheme(next.mode);
      setSaved(true);
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, [setTheme]);
  useEffect(() => {
    document.documentElement.dataset.preset = value.preset;
    document.documentElement.dataset.accent = value.accent;
    document.documentElement.dataset.uiSize = value.textSize;
  }, [value.preset, value.accent, value.textSize]);

  function update(patch: Partial<Omit<Appearance, "version">>) {
    const next = parseAppearance(JSON.stringify({ ...value, ...patch }));
    if (!next) return;
    setValue(next);
    setTheme(next.mode);
    try {
      localStorage.setItem(APPEARANCE_KEY, JSON.stringify(next));
      setSaved(true);
    } catch {
      setSaved(false);
    }
  }
  return (
    <AppearanceContext.Provider value={{ value, ready, saved, update }}>
      {children}
    </AppearanceContext.Provider>
  );
}

export function useAppearance() {
  const context = useContext(AppearanceContext);
  if (!context) throw new Error("AppearanceProvider missing");
  return context;
}
