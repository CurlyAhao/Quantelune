"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  LANGUAGE_STORAGE_KEY,
  normalizeLocale,
  resolvePreferredLocale,
  type AppLocale,
} from "@/lib/i18n";
import { createAppTranslator, type AppTranslator } from "@/lib/i18n-messages";
import {
  formatLocaleBytes,
  formatLocaleDate,
  formatLocaleNumber,
  formatLocalePercent,
  formatLocalePValue,
  formatLocaleStat,
} from "@/lib/i18n-core";
import { getInstallerLocale } from "@/lib/tauri-adapter";

interface LanguageContextValue {
  locale: AppLocale;
  setLocale: (locale: AppLocale) => void;
  t: AppTranslator;
  preferenceSaved: boolean;
  formatNumber: (
    value: number | null | undefined,
    options?: Intl.NumberFormatOptions,
  ) => string;
  formatDate: (iso: string | null | undefined, timeZone?: string) => string;
  formatPercent: (value: number | null | undefined) => string;
  formatBytes: (value: number) => string;
  formatStat: (value: number | null | undefined, digits?: number) => string;
  formatPValue: (value: number | null | undefined) => string;
}

function valueFor(
  locale: AppLocale,
  setLocale: LanguageContextValue["setLocale"],
  preferenceSaved: boolean,
): LanguageContextValue {
  return {
    locale,
    setLocale,
    preferenceSaved,
    t: createAppTranslator(locale),
    formatNumber: (value, options) =>
      formatLocaleNumber(value, locale, options),
    formatDate: (iso, timeZone) => formatLocaleDate(iso, locale, timeZone),
    formatPercent: (value) => formatLocalePercent(value, locale),
    formatBytes: (value) => formatLocaleBytes(value, locale),
    formatStat: (value, digits) => formatLocaleStat(value, locale, digits),
    formatPValue: (value) => formatLocalePValue(value, locale),
  };
}

// Standalone components use English. The app layout supplies resolved state.
const standalone = valueFor(
  "en",
  () => {
    throw new Error("Language changes require LanguageProvider");
  },
  false,
);
const LanguageContext = createContext<LanguageContextValue>(standalone);
function savedPreference(): string | null {
  try {
    return localStorage.getItem(LANGUAGE_STORAGE_KEY);
  } catch {
    return null;
  }
}
function applyDocumentLocale(locale: AppLocale) {
  const t = createAppTranslator(locale);
  document.documentElement.lang = locale;
  document.title = t("app.title");
  document.head
    .querySelector('meta[name="description"]')
    ?.setAttribute("content", t("app.description"));
}

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<AppLocale>("en");
  const [ready, setReady] = useState(false);
  const [choose, setChoose] = useState(false);
  const [preferenceSaved, setPreferenceSaved] = useState(true);
  const revision = useRef(0);
  const setLocale = useCallback((next: AppLocale) => {
    revision.current++;
    let persisted = true;
    try {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, next);
    } catch {
      persisted = false;
    }
    applyDocumentLocale(next);
    setPreferenceSaved(persisted);
    setLocaleState(next);
    setReady(true);
    setChoose(false);
  }, []);
  useEffect(() => {
    let cancelled = false;
    const initialRevision = revision.current;
    const saved = normalizeLocale(savedPreference());
    function apply(next: AppLocale) {
      if (cancelled || revision.current !== initialRevision) return;
      applyDocumentLocale(next);
      setLocaleState(next);
      setReady(true);
      setChoose(false);
    }
    if (saved) {
      apply(saved);
      return;
    }
    // No business content until the installer preference resolves. A stalled
    // bridge offers a real language choice, rather than flashing a provisional locale.
    const timeout = window.setTimeout(() => {
      if (!cancelled && revision.current === initialRevision) setChoose(true);
    }, 2000);
    void getInstallerLocale()
      .then(
        (installer) =>
          apply(
            resolvePreferredLocale({ installer, browser: navigator.language }),
          ),
        () => {
          if (!cancelled) setChoose(true);
        },
      )
      .finally(() => window.clearTimeout(timeout));
    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
    };
  }, []);
  const value = useMemo(
    () => valueFor(locale, setLocale, preferenceSaved),
    [locale, setLocale, preferenceSaved],
  );
  return (
    <LanguageContext.Provider value={value}>
      {ready ? (
        children
      ) : choose ? (
        <div className="flex min-h-48 items-center justify-center gap-3">
          <button
            type="button"
            className="rounded-md border px-4 py-2"
            onClick={() => setLocale("en")}
          >
            English
          </button>
          <button
            type="button"
            className="rounded-md border px-4 py-2"
            onClick={() => setLocale("zh-CN")}
          >
            简体中文
          </button>
        </div>
      ) : (
        <div
          role="status"
          aria-busy="true"
          aria-label="Loading / 正在加载"
          className="min-h-48"
        />
      )}
    </LanguageContext.Provider>
  );
}
export function useLanguage(): LanguageContextValue {
  return useContext(LanguageContext);
}
