export const WORKSPACE_PREFERENCES_KEY = "statforge.workspace-preferences.v1";
export const EDITOR_IDS = [
  "auto",
  "system",
  "vscode",
  "cursor",
  "vscode-insiders",
  "notepadpp",
] as const;
export const SHORTCUT_KEYS = ["1", "2", "s", "p"] as const;
export type WorkspacePreferences = {
  version: 1;
  editor: (typeof EDITOR_IDS)[number];
  imageFormat: "png" | "svg" | "pdf";
  reportFormat: "html" | "md" | "json";
  plotDpi: 150 | 300 | 600;
  saveDirectory: string;
  settingsKey: (typeof SHORTCUT_KEYS)[number];
  projectKey: (typeof SHORTCUT_KEYS)[number];
};
export const DEFAULT_WORKSPACE_PREFERENCES: WorkspacePreferences = {
  version: 1,
  editor: "auto",
  imageFormat: "png",
  reportFormat: "html",
  plotDpi: 300,
  saveDirectory: "",
  settingsKey: "1",
  projectKey: "2",
};
export function parseWorkspacePreferences(
  raw: string | null,
): WorkspacePreferences | null {
  if (!raw) return { ...DEFAULT_WORKSPACE_PREFERENCES };
  try {
    const value = JSON.parse(raw);
    if (
      value?.version !== 1 ||
      !EDITOR_IDS.includes(value.editor) ||
      !["png", "svg", "pdf"].includes(value.imageFormat) ||
      !["html", "md", "json"].includes(value.reportFormat) ||
      ![150, 300, 600].includes(value.plotDpi) ||
      typeof value.saveDirectory !== "string" ||
      value.saveDirectory.length > 1024 ||
      /[\u0000-\u001f]/.test(value.saveDirectory) ||
      !SHORTCUT_KEYS.includes(value.settingsKey) ||
      !SHORTCUT_KEYS.includes(value.projectKey) ||
      value.settingsKey === value.projectKey
    )
      return null;
    return {
      version: 1,
      editor: value.editor,
      imageFormat: value.imageFormat,
      reportFormat: value.reportFormat,
      plotDpi: value.plotDpi,
      saveDirectory: value.saveDirectory,
      settingsKey: value.settingsKey,
      projectKey: value.projectKey,
    };
  } catch {
    return null;
  }
}
export function readWorkspacePreferences(): WorkspacePreferences {
  try {
    return (
      parseWorkspacePreferences(
        localStorage.getItem(WORKSPACE_PREFERENCES_KEY),
      ) || { ...DEFAULT_WORKSPACE_PREFERENCES }
    );
  } catch {
    return { ...DEFAULT_WORKSPACE_PREFERENCES };
  }
}

/** A preferred folder only seeds the native save dialog; it grants no write path. */
export function preferredSavePath(filename: string) {
  const directory = readWorkspacePreferences().saveDirectory;
  return directory
    ? directory.replace(/[\\/]+$/g, "") + "/" + filename
    : filename;
}
