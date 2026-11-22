import { ApiError } from "./api-client";
import { preferredSavePath } from "./workspace-preferences";
import {
  assertActorEpoch,
  getActorEpoch,
  subscribeActorChange,
} from "./auth-session";
import { getApiRuntime, isTauri, resolveDesktopApiPath } from "./tauri-adapter";
import type { AppLocale } from "./i18n";
import { createAppTranslator } from "./i18n-messages";

export type SaveOutcome = "saved" | "handed_off";

const nativeWriteListeners = new WeakMap<AbortSignal, () => void>();

/** A native fs IPC cannot be cancelled once submitted to the OS. */
export function subscribeNativeWriteStart(
  signal: AbortSignal,
  listener: () => void,
): () => void {
  nativeWriteListeners.set(signal, listener);
  return () => {
    if (nativeWriteListeners.get(signal) === listener)
      nativeWriteListeners.delete(signal);
  };
}

export const MAX_DOWNLOAD_BYTES = Number.POSITIVE_INFINITY;
const generatedDownloads = new Map<
  string,
  { size: number; release: () => void }
>();

/** Browser fallback has no completion signal. Keep sources alive until page/actor exit. */
export function releaseGeneratedDownloads(): void {
  [...generatedDownloads.values()].forEach(({ release }) => release());
}
subscribeActorChange(releaseGeneratedDownloads);
if (typeof window !== "undefined")
  window.addEventListener("pagehide", releaseGeneratedDownloads);

function current(epoch: number, signal?: AbortSignal) {
  assertActorEpoch(epoch);
  if (signal?.aborted)
    throw new DOMException("Download cancelled.", "AbortError");
}

function assertDownloadPath(path: string): void {
  if (
    !/^\/api\/v1\/projects\/[0-9a-f-]{36}\/coding\/export\?format=(csv|json|qdc|summary_csv(?:&case_collection_id=[0-9a-f-]{36}&case_sample_id=[A-Za-z0-9_.!~*\x27()%+-]+)?)$/.test(path) &&
    !/^\/api\/v1\/projects\/[0-9a-f-]{36}\/package$/.test(path) &&
    !/^\/api\/v1\/projects\/[0-9a-f-]{36}\/export-qdpx$/.test(path) &&
    !/^\/api\/v1\/datasets\/[0-9a-f-]{36}\/pitch-export\?sample_id=[A-Za-z0-9_.!~*\x27()%+-]+&format=(long|short|binary)$/.test(path) &&
    !/^\/api\/v1\/projects\/[0-9a-f-]{36}\/plots\/runs\/[0-9a-f-]{36}\/package$/.test(
      path,
    ) &&
    !/^\/api\/v1\/projects\/[0-9a-f-]{36}\/plots\/runs\/[0-9a-f-]{36}\/files\/(svg|png|pdf|py)$/.test(
      path,
    ) &&
    !/^\/api\/v1\/(datasets\/[^/]+\/(download|multimodal\/source)|ml\/runs\/[^/]+\/artifacts|workflow\/runs\/[0-9a-f-]{36}\/artifacts\/[0-9a-f]{32}|materials\/[^/]+\/content)$/.test(
      path,
    ) &&
    !/^\/api\/v1\/research\/sessions\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/plans\/acceptances\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/report$/.test(
      path,
    )
  )
    throw new ApiError(
      400,
      "Download path unavailable.",
      "DOWNLOAD_PATH_INVALID",
    );
}

function downloadUrl(base: string, path: string): string {
  assertDownloadPath(path);
  const url = new URL(base);
  if (
    !/^https?:$/.test(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  )
    throw new ApiError(
      400,
      "Download runtime unavailable.",
      "DOWNLOAD_RUNTIME_INVALID",
    );
  const prefix = url.pathname.replace(/\/+$/, "");
  if (prefix && !prefix.endsWith("/api/v1"))
    throw new ApiError(
      400,
      "Download runtime unavailable.",
      "DOWNLOAD_RUNTIME_INVALID",
    );
  const [pathname, query = ""] = path.split("?");
  url.pathname = prefix ? prefix + pathname.replace(/^\/api\/v1/, "") : pathname;
  url.search = query;
  return url.href;
}

/** Native target selection precedes lazy API retrieval; only the dialog grants scope. */
async function saveNativeFile(
  loadBlob: () => Promise<Blob>,
  filename: string,
  signal: AbortSignal,
  epoch: number,
  verifySource?: () => Promise<void>,
  locale: AppLocale = "en",
): Promise<SaveOutcome> {
  current(epoch, signal);
  const name = downloadFilename(filename);
  const extension = name.split(".").at(-1) ?? "bin";
  const native = (
    window as unknown as {
      __TAURI_INTERNALS__: {
        invoke: (
          cmd: string,
          args?: unknown,
          options?: { headers: Record<string, string> },
        ) => Promise<unknown>;
      };
    }
  ).__TAURI_INTERNALS__;
  const path = await native.invoke("plugin:dialog|save", {
    options: {
      title: createAppTranslator(locale)("download.saveAnalysis"),
      defaultPath: preferredSavePath(name),
      filters: [{ name: extension.toUpperCase(), extensions: [extension] }],
    },
  });
  current(epoch, signal);
  if (path === null) throw new DOMException("Save cancelled.", "AbortError");
  if (typeof path !== "string" || !path)
    throw new Error("Native save unavailable.");
  const blob = await loadBlob();
  current(epoch, signal);
  await verifySource?.();
  current(epoch, signal);
  const bytes = new Uint8Array(await blob.arrayBuffer());
  current(epoch, signal);
  nativeWriteListeners.get(signal)?.();
  await native.invoke("plugin:fs|write_file", bytes, {
    headers: { path: encodeURIComponent(path), options: JSON.stringify({}) },
  });
  current(epoch, signal);
  return "saved";
}

export function downloadFilename(value: string): string {
  const leaf = value.split(/[\\/]/).at(-1) ?? "";
  let name = Array.from(leaf)
    .map((char) => {
      const code = char.codePointAt(0)!;
      return code < 32 || code === 127 || '<>:"|?*'.includes(char) ? "_" : char;
    })
    .join("")
    .trim()
    .replace(/[. ]+$/, "");
  if (!name || name === "." || name === "..") return "statforge-download.bin";
  if (/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name))
    name = "statforge-" + name;
  // Keep the real suffix: native picker filters reject a truncated title
  // interpreted as an extension. Count UTF-16 units without splitting Unicode.
  const extension = name.match(/\.[a-z0-9]{1,16}$/i)?.[0] ?? "";
  const stem = extension ? name.slice(0, -extension.length) : name;
  let bounded = "";
  for (const char of stem) {
    if (bounded.length + char.length > 160 - extension.length) break;
    bounded += char;
  }
  const safeStem = bounded.replace(/[. ]+$/, "");
  return safeStem
    ? safeStem + extension
    : "statforge-download" + (extension || ".bin");
}

/** Generated figures share actor/cancellation checks with authenticated downloads. */
export async function saveGeneratedFile(
  blob: Blob,
  filename: string,
  signal: AbortSignal,
  verifySource?: () => Promise<void>,
  locale: AppLocale = "en",
  options: { browserDownload?: boolean } = {},
): Promise<SaveOutcome> {
  const epoch = getActorEpoch();
  current(epoch, signal);
  if (generatedDownloads.size >= 16)
    throw new Error(
      "Too many pending downloads. Finish them or reopen the page.",
    );
  const name = downloadFilename(filename);
  const extension = name.split(".").at(-1) ?? "bin";
  if (isTauri()) {
    return saveNativeFile(
      async () => blob,
      name,
      signal,
      epoch,
      verifySource,
      locale,
    );
  }
  type Writable = {
    write: (value: Blob) => Promise<void>;
    close: () => Promise<void>;
    abort: () => Promise<void>;
  };
  const picker = (
    window as unknown as {
      showSaveFilePicker?: (
        options: unknown,
      ) => Promise<{ createWritable: () => Promise<Writable> }>;
    }
  ).showSaveFilePicker;
  if (picker && !options.browserDownload) {
    const file = await picker.call(window, {
      suggestedName: name,
      types: [
        {
          description: extension.toUpperCase(),
          accept: {
            [blob.type.split(";")[0] || "application/octet-stream"]: [
              `.${extension}`,
            ],
          },
        },
      ],
    });
    current(epoch, signal);
    await verifySource?.();
    current(epoch, signal);
    const writable = await file.createWritable();
    try {
      current(epoch, signal);
      await writable.write(blob);
      current(epoch, signal);
      await writable.close();
    } catch (error) {
      await writable.abort().catch(() => undefined);
      throw error;
    }
    current(epoch, signal);
    return "saved";
  }
  await verifySource?.();
  current(epoch, signal);
  // Use the same browser download boundary as owned API files.
  // File System Access pickers are unavailable in some Windows webviews.
  const url = URL.createObjectURL(blob),
    anchor = document.createElement("a");
  let handedOff = false;
  const release = () => {
    URL.revokeObjectURL(url);
    generatedDownloads.delete(url);
    signal.removeEventListener("abort", release);
  };
  try {
    anchor.href = url;
    anchor.download = name;
    document.body.append(anchor);
    current(epoch, signal);
    generatedDownloads.set(url, { size: blob.size, release });
    signal.addEventListener("abort", release, { once: true });
    anchor.click();
    handedOff = true;
  } finally {
    anchor.remove();
    if (!handedOff) release();
  }
  return "handed_off";
}

/** Owned binary download; no Provider context or token-bearing navigation URL. */
export async function downloadApiFile(
  path: string,
  filename: string,
  signal?: AbortSignal,
  _limitBytes = MAX_DOWNLOAD_BYTES,
): Promise<SaveOutcome> {
  const epoch = getActorEpoch();
  current(epoch, signal);
  const resolvedPath = resolveDesktopApiPath(path);
  assertDownloadPath(resolvedPath);
  if (isTauri()) {
    const nativeSignal = signal ?? new AbortController().signal;
    // The authenticated GET (including server source/ACL checks) happens only
    // after choosing a target. Cancellation does not fetch or write any bytes.
    return saveNativeFile(
      () =>
        fetchApiFileBlob(resolvedPath, nativeSignal, MAX_DOWNLOAD_BYTES, epoch),
      filename,
      nativeSignal,
      epoch,
    );
  }
  const blob = await fetchApiFileBlob(
    resolvedPath,
    signal,
    MAX_DOWNLOAD_BYTES,
    epoch,
  );
  current(epoch, signal);
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  let clicked = false;
  try {
    anchor.href = objectUrl;
    anchor.download = downloadFilename(filename);
    document.body.append(anchor);
    current(epoch, signal);
    anchor.click();
    clicked = true;
  } finally {
    anchor.remove();
    if (clicked) setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    else URL.revokeObjectURL(objectUrl);
  }
  return "handed_off";
}

async function fetchApiFileBlob(
  resolvedPath: string,
  signal: AbortSignal | undefined,
  limitBytes: number,
  epoch: number,
): Promise<Blob> {
  current(epoch, signal);
  const runtime = await getApiRuntime();
  current(epoch, signal);
  const response = await fetch(downloadUrl(runtime.apiUrl, resolvedPath), {
    credentials: "include",
    cache: "no-store", // current ACL/source must be checked after a native choice
    redirect: "error", // remote MinIO redirect downloads are deliberately unavailable here
    signal,
    headers: runtime.sessionToken
      ? { "X-Session-Token": runtime.sessionToken }
      : {},
  });
  try {
    current(epoch, signal);
    if (!response.ok) {
      if (response.status === 409 || response.status === 422) {
        const problem = await response.json().catch(() => null);
        const plotPackage =
          /^\/api\/v1\/projects\/[0-9a-f-]{36}\/plots\/runs\/[0-9a-f-]{36}\/package$/.test(
            resolvedPath,
          );
        if (
          typeof problem?.error_code === "string" &&
          (problem.error_code.startsWith("PLOT_CODE_") ||
            (plotPackage &&
              (problem.error_code.startsWith("PLOT_") ||
                [
                  "ML_BUSY",
                  "ML_TIMEOUT",
                  "ML_OUTPUT_LIMIT",
                  "ML_CANCELLED",
                ].includes(problem.error_code)))) &&
          typeof problem.detail === "string" &&
          problem.detail.length <= 1000
        )
          throw new ApiError(
            response.status,
            problem.detail,
            problem.error_code,
          );
      }
      throw new ApiError(
        response.status,
        "Download unavailable.",
        "DOWNLOAD_UNAVAILABLE",
      );
    }
    const declared = response.headers.get("content-length");
    if (
      declared !== null &&
      (!/^\d+$/.test(declared) || Number(declared) > limitBytes)
    )
      throw new ApiError(
        413,
        "Download exceeds its size limit.",
        "DOWNLOAD_SIZE_LIMIT",
      );
  } catch (error) {
    await response.body?.cancel().catch(() => undefined);
    throw error;
  }
  const reader = response.body?.getReader();
  if (!reader)
    throw new ApiError(0, "Download unavailable.", "DOWNLOAD_UNAVAILABLE");
  const buffers: ArrayBuffer[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      current(epoch, signal);
      if (done) break;
      size += value.byteLength;
      if (size > limitBytes)
        throw new ApiError(
          413,
          "Download exceeds its size limit.",
          "DOWNLOAD_SIZE_LIMIT",
        );
      buffers.push(new Uint8Array(value).buffer);
    }
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    throw error;
  } finally {
    reader.releaseLock();
  }
  current(epoch, signal);
  return new Blob(buffers, {
    type:
      response.headers.get("content-type")?.split(";")[0] ??
      "application/octet-stream",
  });
}

/** Authenticated preview reads use the same owned artifact route as downloads. */
export async function readApiFileBlob(
  path: string,
  signal?: AbortSignal,
): Promise<Blob> {
  const resolved = resolveDesktopApiPath(path);
  assertDownloadPath(resolved);
  return fetchApiFileBlob(
    resolved,
    signal,
    MAX_DOWNLOAD_BYTES,
    getActorEpoch(),
  );
}
