"use client";

import {
  Suspense,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { navigateToAppRoute } from "@/lib/desktop-navigation";
import { flushProjectEditors } from "@/lib/project-save";
import { toast } from "sonner";
import {
  DEFAULT_WORKSPACE_PREFERENCES,
  WORKSPACE_PREFERENCES_KEY,
  parseWorkspacePreferences,
  type WorkspacePreferences,
} from "@/lib/workspace-preferences";

const Context = createContext<{
  value: WorkspacePreferences;
  ready: boolean;
  saved: boolean;
  update: (patch: Partial<Omit<WorkspacePreferences, "version">>) => boolean;
  lastProject: string;
} | null>(null);
const LAST_PROJECT_KEY = "statforge.last-project-page.v1";
function projectRoute(path: string, query: URLSearchParams) {
  const match = path.match(
    /^\/projects\/([a-f0-9-]{36}|__desktop_project__)\/([a-z][a-z0-9-]*(?:\/(?:[a-f0-9-]{36}|__desktop_dataset__))?)$/,
  );
  if (!match) return "";
  const id =
    match[1] === "__desktop_project__" ? query.get("projectId") : match[1];
  if (!id || !/^[a-f0-9-]{36}$/.test(id)) return "";
  const retained = new URLSearchParams();
  for (const name of [
    "datasetId",
    "plotId",
    "runId",
    "sourceId",
    "sourceJob",
    "kind",
    "view",
    "materialId",
    "page",
    "start",
    "end",
    "textSha",
  ]) {
    const value = query.get(name);
    if (value && /^[a-zA-Z0-9_-]{1,80}$/.test(value)) retained.set(name, value);
  }
  const tail = match[2].replace(
    "__desktop_dataset__",
    query.get("datasetId") || "",
  );
  if (tail.endsWith("/") || !/^[a-z0-9/-]+$/.test(tail)) return "";
  return `/projects/${id}/${tail}${retained.size ? "?" + retained : ""}`;
}
export function WorkspacePreferencesProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [value, setValue] = useState(DEFAULT_WORKSPACE_PREFERENCES);
  const [ready, setReady] = useState(false);
  const [saved, setSaved] = useState(true);
  const [lastProject, setLastProject] = useState("");
  const current = useRef(value);
  const router = useRouter();
  useEffect(() => {
    try {
      const next = parseWorkspacePreferences(
        localStorage.getItem(WORKSPACE_PREFERENCES_KEY),
      );
      if (next) {
        current.current = next;
        setValue(next);
      } else setSaved(false);
      const prior = localStorage.getItem(LAST_PROJECT_KEY);
      const route = projectRoute(
        location.pathname,
        new URLSearchParams(location.search),
      );
      if (route) setLastProject(route);
      else if (prior) {
        const url = new URL(prior, location.origin);
        if (url.origin === location.origin)
          setLastProject(projectRoute(url.pathname, url.searchParams));
      }
    } catch {
      setSaved(false);
    }
    setReady(true);
    const sync = (event: StorageEvent) => {
      if (event.key === WORKSPACE_PREFERENCES_KEY) {
        const next = parseWorkspacePreferences(event.newValue);
        if (next) {
          current.current = next;
          setValue(next);
          setSaved(true);
        }
      }
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);
  const remember = useCallback((route: string) => {
    setLastProject(route);
    try {
      localStorage.setItem(LAST_PROJECT_KEY, route);
    } catch {
      /* The in-memory return route still works. */
    }
  }, []);
  const update = useCallback(
    (patch: Partial<Omit<WorkspacePreferences, "version">>) => {
      const next = parseWorkspacePreferences(
        JSON.stringify({ ...current.current, ...patch }),
      );
      if (!next) return false;
      current.current = next;
      setValue(next);
      try {
        localStorage.setItem(WORKSPACE_PREFERENCES_KEY, JSON.stringify(next));
        setSaved(true);
      } catch {
        setSaved(false);
      }
      return true;
    },
    [],
  );
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (
        !ready ||
        event.defaultPrevented ||
        event.repeat ||
        !event.altKey ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey
      )
        return;
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))
      )
        return;
      if (
        [...document.querySelectorAll('[role="dialog"],dialog[open]')].some(
          (element) => element.getClientRects().length > 0,
        )
      )
        return;
      const name = event.key.toLowerCase();
      const destination =
        name === value.settingsKey
          ? `/settings${lastProject ? "?returnTo=" + encodeURIComponent(lastProject) : ""}#general`
          : name === value.projectKey
            ? lastProject
            : "";
      if (!destination) return;
      event.preventDefault();
      const route = projectRoute(
        location.pathname,
        new URLSearchParams(location.search),
      );
      void (
        route ? flushProjectEditors(route.split("/")[2]) : Promise.resolve()
      )
        .then(() => navigateToAppRoute(router, destination))
        .catch(() =>
          toast.error(
            document.documentElement.lang.startsWith("zh")
              ? "草稿未保存，请稍后重试。"
              : "Draft not saved. Try again shortly.",
          ),
        );
    };
    document.addEventListener("keydown", key, true);
    return () => document.removeEventListener("keydown", key, true);
  }, [ready, value.settingsKey, value.projectKey, lastProject, router]);
  return (
    <Context.Provider value={{ value, ready, saved, update, lastProject }}>
      <Suspense fallback={null}>
        <ProjectPageTracker remember={remember} />
      </Suspense>
      {children}
    </Context.Provider>
  );
}
function ProjectPageTracker({
  remember,
}: {
  remember: (route: string) => void;
}) {
  const path = usePathname();
  const params = useSearchParams();
  useEffect(() => {
    const route = projectRoute(path, new URLSearchParams(params.toString()));
    if (route) remember(route);
  }, [path, params, remember]);
  return null;
}
export function useWorkspacePreferences() {
  const context = useContext(Context);
  if (!context) throw new Error("Workspace preferences provider missing");
  return context;
}
