"use client";

import { PlugIcon } from "lucide-react";
import type { ResearchProviderState } from "@/lib/research-types";
import { useLanguage } from "@/providers/language-provider";
import { ResearchFailure } from "./formal-research-common";
import { ModelSettingsLink } from "./model-settings-link";

export function ResearchProviderPanel({
  state,
  loading,
  error,
  changed,
  projectId,
}: {
  state?: ResearchProviderState;
  loading: boolean;
  error: unknown;
  changed: () => Promise<unknown>;
  projectId?: string;
}) {
  const { locale } = useLanguage();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  return (
    <div className="formal-model-status">
      {loading ? (
        <span role="status">{copy("读取模型…", "Loading model…")}</span>
      ) : (
        <span>
          <PlugIcon className="size-3.5" />
          {state?.key_configured
            ? `${state.settings?.model ?? ""}${state.enabled ? "" : copy(" · 已暂停", " · Paused")}`
            : copy("尚未选择模型", "Choose a model")}
        </span>
      )}
      <ModelSettingsLink projectId={projectId} />
      {error != null && (
        <ResearchFailure
          error={error}
          retry={() => {
            void changed().catch(() => undefined);
          }}
        />
      )}
    </div>
  );
}
