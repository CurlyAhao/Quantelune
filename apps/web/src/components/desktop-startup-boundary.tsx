"use client";

import { useEffect, useState } from "react";
import { createAppTranslator } from "@/lib/i18n-messages";
import {
  LANGUAGE_STORAGE_KEY,
  normalizeLocale,
  resolvePreferredLocale,
  type AppLocale,
} from "@/lib/i18n";
import {
  getDesktopStartupState,
  getDesktopFault,
  isTauri,
  reportDesktopFault,
  subscribeDesktopFault,
  type DesktopStartupState,
} from "@/lib/desktop-startup";

/** Startup failures remain local; auth and business children only mount after Ready. */
export function DesktopStartupBoundary({
  children,
}: {
  children: React.ReactNode;
}) {
  const [mode, setMode] = useState<"checking" | "ready" | "failed">("checking");
  const [locale, setLocale] = useState<AppLocale>("en");
  const [info, setInfo] = useState<DesktopStartupState | null>(null);
  const [code, setCode] = useState("LOCAL_SERVICE_UNAVAILABLE");
  useEffect(() => {
    if (!isTauri()) {
      setMode("ready");
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stored: AppLocale | null = null;
    try {
      stored = normalizeLocale(localStorage.getItem(LANGUAGE_STORAGE_KEY));
    } catch {
      /* Device preference is optional. */
    }
    setLocale(
      stored ?? resolvePreferredLocale({ browser: navigator.language }),
    );
    const started = performance.now();
    const unsubscribe = subscribeDesktopFault((error) => {
      if (!cancelled) {
        setCode(error.code);
        setMode("failed");
      }
    });
    async function poll() {
      try {
        const state = await getDesktopStartupState();
        if (cancelled) return;
        setInfo(state);
        if (!stored && state?.preferredLocale)
          setLocale(normalizeLocale(state.preferredLocale) ?? "en");
        const fault = getDesktopFault();
        if (fault) {
          setCode(fault.code);
          setMode("failed");
          return;
        }
        if (state?.startupState === "ready") {
          setMode("ready");
          return;
        }
        if (!state || state.startupState !== "starting") {
          const error = reportDesktopFault(
            state?.error ?? "LOCAL_SERVICE_UNAVAILABLE",
          );
          setCode(error.code);
          setMode("failed");
          return;
        }
        if (
          state.elapsedMs >= 120000 ||
          performance.now() - started >= 122000
        ) {
          reportDesktopFault("STARTUP_TIMEOUT");
          return;
        }
        timer = setTimeout(() => {
          void poll();
        }, 250);
      } catch {
        if (!cancelled) {
          setCode(getDesktopFault()?.code ?? "DESKTOP_BRIDGE_UNAVAILABLE");
          setMode("failed");
        }
      }
    }
    void poll();
    return () => {
      cancelled = true;
      unsubscribe();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, []);
  if (mode === "ready") return <>{children}</>;
  const t = createAppTranslator(locale);
  const disconnected = mode === "failed" && code === "LOCAL_SERVICE_UNAVAILABLE" && info?.startupState === "ready";
  return (
    <main
      lang={locale}
      className="flex min-h-screen items-center justify-center bg-background px-6 py-12 text-foreground"
    >
      <section className="w-full max-w-xl space-y-4" aria-live="polite">
        <h1 className="font-heading text-2xl font-semibold">
          {t(
            mode === "failed"
              ? disconnected ? "desktop.disconnected" : "desktop.startupFailed"
              : "desktop.startupTitle",
          )}
        </h1>
        <p>
          {t(
            mode === "failed"
              ? disconnected ? "desktop.reconnectHelp" : "desktop.startupRecovery"
              : "desktop.startupWaiting",
          )}
        </p>
        {disconnected && <button type="button" className="rounded-md border px-4 py-2" onClick={() => window.location.reload()}>{t("desktop.reconnect")}</button>}
        {mode === "failed" && (
          <details>
            <summary>{t("desktop.startupDetails")}</summary>
            <p className="mt-3 break-words">
              <code>{code}</code>
            </p>
            {info?.diagnosticAvailable && info.diagnosticFile ? (
              <p className="mt-2 break-words">
                {t("desktop.startupDiagnosticFile", {
                  name: info.diagnosticFile,
                })}
              </p>
            ) : (
              <p className="mt-2">{t("desktop.startupDiagnosticMissing")}</p>
            )}
          </details>
        )}
      </section>
    </main>
  );
}
