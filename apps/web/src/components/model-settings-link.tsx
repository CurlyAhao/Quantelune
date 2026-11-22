"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { DesktopLink } from "./desktop-link";
import { flushProjectEditors } from "@/lib/project-save";
import { navigateToAppRoute } from "@/lib/desktop-navigation";
import { useLanguage } from "@/providers/language-provider";

export function ModelSettingsLink({
  projectId,
  className,
  children,
}: {
  projectId?: string;
  className?: string;
  children?: React.ReactNode;
}) {
  const router = useRouter();
  const { locale } = useLanguage();
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  return (
    <DesktopLink
      href="/settings#models"
      className={className}
      aria-disabled={busy}
      onClick={(event) => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)
          return;
        event.preventDefault();
        if (pending.current) return;
        pending.current = true;
        setBusy(true);
        void (async () => {
          try {
            if (projectId) await flushProjectEditors(projectId);
            const path =
              window.location.pathname.replace(
                "__desktop_project__",
                projectId ?? "__desktop_project__",
              ) + window.location.search;
            navigateToAppRoute(
              router,
              `/settings?returnTo=${encodeURIComponent(path)}#models`,
            );
          } catch {
            toast.error(
              locale === "zh-CN"
                ? "当前编辑尚未保存，请保存后打开模型设置。"
                : "Save your current edits before opening model settings.",
            );
          } finally {
            pending.current = false;
            setBusy(false);
          }
        })();
      }}
    >
      {children ?? (locale === "zh-CN" ? "模型设置" : "Model settings")}
    </DesktopLink>
  );
}
