/** Desktop startup authority and static route mapping. */
import { isTauri, getApiBaseUrl } from "./desktop-startup";
export {
  isTauri,
  getApiBaseUrl,
  getSessionToken,
  getApiRuntime,
  getInstallerLocale,
  resetTauriAdapterCache,
} from "./desktop-startup";
const DESKTOP_PROJECT_ID = "__desktop_project__";
const DESKTOP_DATASET_ID = "__desktop_dataset__";

export function toDesktopStaticHref(href: string): string {
  if (!isTauri() || typeof window === "undefined") return href;
  const url = new URL(href, window.location.href);
  const segments = url.pathname.split("/");
  if (segments[1] !== "projects" || !segments[2] || segments[2] === "new") {
    return `${url.pathname}${url.search}${url.hash}`;
  }

  const current = new URLSearchParams(window.location.search);
  const actualProjectId =
    segments[2] === DESKTOP_PROJECT_ID ? url.searchParams.get("projectId") || current.get("projectId") : segments[2];
  if (actualProjectId) url.searchParams.set("projectId", actualProjectId);
  segments[2] = DESKTOP_PROJECT_ID;

  if (segments[3] === "data" && segments[4]) {
    const actualDatasetId =
      segments[4] === DESKTOP_DATASET_ID
        ? current.get("datasetId")
        : segments[4];
    if (actualDatasetId) url.searchParams.set("datasetId", actualDatasetId);
    segments[4] = DESKTOP_DATASET_ID;
  }
  url.pathname = segments.join("/");
  return `${url.pathname}${url.search}${url.hash}`;
}

export function resolveDesktopApiPath(path: string): string {
  if (!isTauri() || typeof window === "undefined") return path;
  const search = new URLSearchParams(window.location.search);
  return path
    .replaceAll(
      DESKTOP_PROJECT_ID,
      search.get("projectId") ?? DESKTOP_PROJECT_ID,
    )
    .replaceAll(
      DESKTOP_DATASET_ID,
      search.get("datasetId") ?? DESKTOP_DATASET_ID,
    );
}

/** Reactive hook for use in React components. */
import { useEffect, useState } from "react";

export function useTauriApiUrl(): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    getApiBaseUrl().then(setUrl);
  }, []);
  return url;
}
