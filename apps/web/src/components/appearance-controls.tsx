"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { PaletteIcon } from "lucide-react";
import { useLanguage } from "@/providers/language-provider";
import { useAppearance } from "@/providers/appearance-provider";
import { DEFAULT_APPEARANCE, PRESETS, MODES, ACCENTS } from "@/lib/appearance";
import { Button } from "./ui/button";

export function AppearanceControls() {
  const { t } = useLanguage();
  const { value, ready, saved, update } = useAppearance();
  return (
    <details className="appearance-menu">
      <summary>
        <PaletteIcon className="size-4" aria-hidden />
        <span>{t("entry.appearance")}</span>
      </summary>
      <div className="appearance-panel">
        <label>
          {t("entry.mode")}
          <SearchSelect
            aria-label={t("entry.mode")}
            disabled={!ready}
            value={value.mode}
            onChange={(e) =>
              update({ mode: e.target.value as typeof value.mode })
            }
          >
            {MODES.map((x) => (
              <option key={x} value={x}>
                {t(`entry.mode.${x}`)}
              </option>
            ))}
          </SearchSelect>
        </label>
        <label>
          {t("entry.preset")}
          <SearchSelect
            aria-label={t("entry.preset")}
            disabled={!ready}
            value={value.preset}
            onChange={(e) =>
              update({ preset: e.target.value as typeof value.preset })
            }
          >
            {PRESETS.map((x) => (
              <option key={x} value={x}>
                {t(`entry.preset.${x}`)}
              </option>
            ))}
          </SearchSelect>
        </label>
        <label>
          {t("entry.accent")}
          <SearchSelect
            aria-label={t("entry.accent")}
            disabled={!ready}
            value={value.accent}
            onChange={(e) =>
              update({ accent: e.target.value as typeof value.accent })
            }
          >
            {ACCENTS.map((x) => (
              <option key={x} value={x}>
                {t(`entry.accent.${x}`)}
              </option>
            ))}
          </SearchSelect>
        </label>
        <Button
          variant="outline"
          size="sm"
          onClick={() => update(DEFAULT_APPEARANCE)}
        >
          {t("entry.restoreAppearance")}
        </Button>
        {!saved && <p role="status">{t("entry.preferenceUnsaved")}</p>}
      </div>
    </details>
  );
}
