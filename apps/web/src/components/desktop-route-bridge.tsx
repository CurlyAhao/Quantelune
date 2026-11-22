"use client";

import { useEffect } from "react";

import { isTauri, toDesktopStaticHref } from "@/lib/tauri-adapter";

/**
 * Keeps arbitrary local project ids on the finite routes emitted by Next's
 * static export. The real ids travel in the query string and are restored by
 * the API client before every sidecar request.
 */
export function DesktopRouteBridge() {
  useEffect(() => {
    if (!isTauri()) return;

    const originalPushState = window.history.pushState.bind(window.history);
    const originalReplaceState = window.history.replaceState.bind(window.history);

    window.history.pushState = (data, unused, url) => {
      const resolved = typeof url === "string" ? toDesktopStaticHref(url) : url;
      originalPushState(data, unused, resolved);
    };
    window.history.replaceState = (data, unused, url) => {
      const resolved = typeof url === "string" ? toDesktopStaticHref(url) : url;
      originalReplaceState(data, unused, resolved);
    };

    const handleClick = (event: MouseEvent) => {
      const anchor = (event.target as Element | null)?.closest("a");
      if (!anchor || anchor.target === "_blank" || event.defaultPrevented) return;
      const resolved = toDesktopStaticHref(anchor.href);
      if (`${window.location.pathname}${window.location.search}` === resolved) return;
      if (resolved !== `${anchor.pathname}${anchor.search}${anchor.hash}`) {
        anchor.href = resolved;
      }
    };
    document.addEventListener("click", handleClick, true);

    return () => {
      window.history.pushState = originalPushState;
      window.history.replaceState = originalReplaceState;
      document.removeEventListener("click", handleClick, true);
    };
  }, []);

  return null;
}
