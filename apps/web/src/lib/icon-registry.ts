import {
  BarChart3Icon,
  BookOpenIcon,
  BracesIcon,
  DatabaseIcon,
  FileCheck2Icon,
  FolderOutputIcon,
  GaugeIcon,
  GitBranchIcon,
  LayersIcon,
  MicroscopeIcon,
  SettingsIcon,
  SparklesIcon,
  WorkflowIcon,
  type LucideIcon,
} from "lucide-react";

/**
 * Controlled icon registry for server→client safe icon references.
 * Pages pass a string name; the component resolves the icon internally.
 */
export const ICON_MAP = {
  "bar-chart": BarChart3Icon,
  "book-open": BookOpenIcon,
  braces: BracesIcon,
  database: DatabaseIcon,
  "file-check": FileCheck2Icon,
  "folder-output": FolderOutputIcon,
  gauge: GaugeIcon,
  "git-branch": GitBranchIcon,
  layers: LayersIcon,
  microscope: MicroscopeIcon,
  settings: SettingsIcon,
  sparkles: SparklesIcon,
  workflow: WorkflowIcon,
} as const;

export type IconName = keyof typeof ICON_MAP;

export function getIcon(name: IconName): LucideIcon {
  return ICON_MAP[name];
}
