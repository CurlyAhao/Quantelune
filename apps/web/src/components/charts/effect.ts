"use client";

import { useEffect, useState } from "react";

/**
 * Client-only render guard for ECharts. ECharts touches `window` at import
 * time, so chart components must never render during SSR.
 */
export function useIsClient(): boolean {
  const [isClient, setIsClient] = useState(false);
  useEffect(() => {
    setIsClient(true);
  }, []);
  return isClient;
}

/** Shared ECharts color tokens drawn from the StatForge palette. */
export const CHART_COLORS = {
  teal: "#2f7a74",
  tealSoft: "#4d9a93",
  amber: "#c99a2e",
  ink: "#2c3a52",
  muted: "#7d8aa0",
  grid: "rgba(44, 58, 82, 0.08)",
  negative: "#3b62c4",
  positive: "#c2452e",
} as const;

export const BASE_TEXT_STYLE = {
  fontFamily:
    "'IBM Plex Sans', ui-sans-serif, system-ui, -apple-system, sans-serif",
  color: CHART_COLORS.ink,
} as const;
