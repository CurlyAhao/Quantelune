/** Local startup authority. A Desktop failure never selects a web API. */
export type DesktopStartupState = {
  startupState: "starting" | "ready" | "failed" | "stopped";
  healthy: boolean;
  phase: string;
  error: string | null;
  preferredLocale: string | null;
  diagnosticFile: string | null;
  diagnosticAvailable: boolean;
  elapsedMs: number;
};
type Runtime = { apiUrl: string; sessionToken: string; desktop: boolean };
type ReadyInfo = Runtime & { preferredLocale: string | null };
export class DesktopRuntimeError extends Error {
  constructor(public readonly code: string) {
    super("The local Quantelune service is unavailable.");
    this.name = "DesktopRuntimeError";
  }
}
let ready: ReadyInfo | null = null;
let bridgeInFlight: Promise<Record<string, unknown>> | null = null;
let readyInFlight: Promise<ReadyInfo> | null = null;
let fault: DesktopRuntimeError | null = null;
let cacheEpoch = 0;
const listeners = new Set<(error: DesktopRuntimeError) => void>();

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}
export function getDesktopFault() {
  return fault;
}
export function reportDesktopFault(code: string): DesktopRuntimeError {
  const safeCode = /^[A-Z_]{1,64}$/.test(code)
    ? code
    : "LOCAL_SERVICE_UNAVAILABLE";
  const error = fault ?? new DesktopRuntimeError(safeCode);
  if (isTauri() && !fault) {
    fault = error;
    for (const listener of listeners) listener(error);
  }
  return error;
}
export function subscribeDesktopFault(
  listener: (error: DesktopRuntimeError) => void,
) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

async function bridge(): Promise<Record<string, unknown>> {
  if (!isTauri()) throw new DesktopRuntimeError("DESKTOP_REQUIRED");
  if (!bridgeInFlight) {
    const epoch = cacheEpoch;
    const current = (async () => {
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        const internals = (window as unknown as Record<string, unknown>)
          .__TAURI_INTERNALS__ as
          { invoke?: (command: string) => Promise<unknown> } | undefined;
        if (typeof internals?.invoke !== "function") throw new Error();
        const raw = await Promise.race([
          internals.invoke("get_sidecar_info"),
          new Promise<never>((_, reject) => {
            timer = setTimeout(() => reject(new Error()), 2000);
          }),
        ]);
        if (epoch !== cacheEpoch)
          throw new DesktopRuntimeError("DESKTOP_RUNTIME_CHANGED");
        if (!raw || typeof raw !== "object" || Array.isArray(raw))
          throw new Error();
        return raw as Record<string, unknown>;
      } catch {
        if (epoch !== cacheEpoch)
          throw new DesktopRuntimeError("DESKTOP_RUNTIME_CHANGED");
        throw reportDesktopFault("DESKTOP_BRIDGE_UNAVAILABLE");
      } finally {
        if (timer !== undefined) clearTimeout(timer);
      }
    })();
    bridgeInFlight = current;
    void current
      .finally(() => {
        if (bridgeInFlight === current) bridgeInFlight = null;
      })
      .catch(() => undefined);
  }
  return bridgeInFlight;
}

function locale(raw: Record<string, unknown>): string | null {
  return raw.preferredLocale === "zh-CN" || raw.preferredLocale === "en"
    ? raw.preferredLocale
    : null;
}

export async function getDesktopStartupState(): Promise<DesktopStartupState | null> {
  if (!isTauri()) return null;
  if (fault) throw fault;
  const epoch = cacheEpoch;
  const raw = await bridge();
  if (epoch !== cacheEpoch)
    throw new DesktopRuntimeError("DESKTOP_RUNTIME_CHANGED");
  const state = raw.startupState;
  if (
    !(["starting", "ready", "failed", "stopped"] as unknown[]).includes(
      state,
    ) ||
    typeof raw.healthy !== "boolean" ||
    raw.healthy !== (state === "ready")
  ) {
    throw reportDesktopFault("DESKTOP_RUNTIME_INVALID");
  }
  const error =
    typeof raw.error === "string" && /^[A-Z_]{1,64}$/.test(raw.error)
      ? raw.error
      : null;
  if (state === "ready") {
    const apiUrl = typeof raw.apiUrl === "string" ? raw.apiUrl : "";
    const port = /^http:\/\/127\.0\.0\.1:([1-9][0-9]{0,4})\/api\/v1$/.exec(
      apiUrl,
    );
    if (
      !port ||
      Number(port[1]) > 65535 ||
      typeof raw.sessionToken !== "string" ||
      !/^[A-Za-z0-9_-]{16,256}$/.test(raw.sessionToken) ||
      raw.error != null
    ) {
      throw reportDesktopFault("DESKTOP_RUNTIME_INVALID");
    }
    ready = {
      apiUrl,
      sessionToken: raw.sessionToken,
      desktop: true,
      preferredLocale: locale(raw),
    };
  } else if (raw.apiUrl !== "" || raw.sessionToken !== "") {
    throw reportDesktopFault("DESKTOP_RUNTIME_INVALID");
  }
  return {
    startupState: state as DesktopStartupState["startupState"],
    healthy: raw.healthy,
    phase:
      typeof raw.phase === "string" && /^[a-z_]{1,40}$/.test(raw.phase)
        ? raw.phase
        : "starting",
    error,
    preferredLocale: locale(raw),
    diagnosticFile:
      typeof raw.diagnosticFile === "string" &&
      /^desktop-startup-[0-9]+-[0-9]+\.jsonl$/.test(raw.diagnosticFile)
        ? raw.diagnosticFile
        : null,
    diagnosticAvailable: raw.diagnosticAvailable === true,
    elapsedMs:
      typeof raw.elapsedMs === "number" &&
      Number.isSafeInteger(raw.elapsedMs) &&
      raw.elapsedMs >= 0
        ? raw.elapsedMs
        : 0,
  };
}

async function desktopReady(): Promise<ReadyInfo> {
  if (fault) throw fault;
  if (ready) return ready;
  if (!readyInFlight) {
    const current = (async () => {
      const started = performance.now();
      while (performance.now() - started < 122000) {
        const info = await getDesktopStartupState();
        if (info?.startupState === "ready" && ready) return ready;
        if (!info || info.startupState !== "starting")
          throw reportDesktopFault(info?.error ?? "LOCAL_SERVICE_UNAVAILABLE");
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      throw reportDesktopFault("STARTUP_TIMEOUT");
    })();
    readyInFlight = current;
    void current
      .finally(() => {
        if (readyInFlight === current) readyInFlight = null;
      })
      .catch(() => undefined);
  }
  return readyInFlight;
}

export async function getApiRuntime(): Promise<Runtime> {
  return isTauri()
    ? desktopReady()
    : {
        apiUrl: (
          process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1"
        ).replace(/\/$/, ""),
        sessionToken: "",
        desktop: false,
      };
}
export async function getApiBaseUrl() {
  return (await getApiRuntime()).apiUrl;
}
export async function getSessionToken() {
  return (await getApiRuntime()).sessionToken;
}
export async function getInstallerLocale(): Promise<string | null> {
  if (!isTauri()) return null;
  if (ready) return ready.preferredLocale;
  return locale(await bridge()); // Language never supplies API or authentication authority.
}
export function resetTauriAdapterCache(): void {
  cacheEpoch++;
  ready = null;
  bridgeInFlight = null;
  readyInFlight = null;
  fault = null;
}
