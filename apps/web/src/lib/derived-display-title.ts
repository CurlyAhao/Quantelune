import type { AppTranslator } from "./i18n-messages";
/** Only newly created copies get a locale default; source titles remain untouched. */
export function derivedDisplayTitle(
  title: string,
  kind: "copy" | "template" | "figure",
  t: AppTranslator,
): string {
  const key =
    kind === "copy"
      ? "workspace.copyTitle"
      : kind === "template"
        ? "workspace.templateTitle"
        : "workspace.figureTitle";
  const suffix = t(key, { title: "" });
  const room = Math.max(0, 160 - Array.from(suffix).length);
  return t(key, { title: Array.from(title).slice(0, room).join("") });
}
