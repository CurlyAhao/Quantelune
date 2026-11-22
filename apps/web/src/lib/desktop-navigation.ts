import { toDesktopStaticHref } from "./tauri-adapter";

export interface AppRouter {
  push(href: string): void;
  replace(href: string): void;
}

/**
 * Convert dynamic project and dataset ids before Next Router resolves its
 * static-export payload. Converting history after router.push is too late:
 * Next has already requested a non-existent route by then.
 */
export function navigateToAppRoute(
  router: AppRouter,
  href: string,
  method: "push" | "replace" = "push",
): void {
  router[method](toDesktopStaticHref(href));
}
