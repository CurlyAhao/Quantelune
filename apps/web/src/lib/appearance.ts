export const PRESETS = ["forest", "ink-sage", "teal-ochre", "research-blue", "neutral", "plum-apricot"] as const;
export const ACCENTS = ["preset", "forest", "blue", "plum"] as const;
export const MODES = ["light", "dark", "system"] as const;
export const TEXT_SIZES = ["small", "standard", "large"] as const;
export type Appearance = {
  version: 1;
  preset: (typeof PRESETS)[number];
  accent: (typeof ACCENTS)[number];
  mode: (typeof MODES)[number];
  collapsed: boolean;
  textSize: (typeof TEXT_SIZES)[number];
};
export const APPEARANCE_KEY = "statforge.appearance.v1";
export const DEFAULT_APPEARANCE: Appearance = {
  version: 1,
  preset: "forest",
  accent: "preset",
  mode: "system",
  collapsed: true,
  textSize: "standard",
};

/** Device-only enumerated preferences: never research data or arbitrary CSS. */
export function parseAppearance(raw: string | null): Appearance | null {
  if (raw === null) return { ...DEFAULT_APPEARANCE };
  try {
    const p = JSON.parse(raw);
    if (
      !p ||
      p.version !== 1 ||
      !PRESETS.includes(p.preset) ||
      !ACCENTS.includes(p.accent) ||
      !MODES.includes(p.mode) ||
      (p.textSize !== undefined && !TEXT_SIZES.includes(p.textSize)) ||
      typeof p.collapsed !== "boolean"
    )
      return null;
    return {
      version: 1,
      preset: p.preset,
      accent: p.accent,
      mode: p.mode,
      collapsed: p.collapsed,
      textSize: p.textSize ?? "standard",
    };
  } catch {
    return null;
  }
}
