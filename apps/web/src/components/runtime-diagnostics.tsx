"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2Icon, RefreshCwIcon, XCircleIcon } from "lucide-react";
import { desktopApi } from "@/lib/api-endpoints";
import { useLanguage } from "@/providers/language-provider";
import { Button } from "./ui/button";
import { PreviewSettings } from "./preview-settings";

export function RuntimeDiagnostics() {
  const { locale } = useLanguage();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  const query = useQuery({
    queryKey: ["desktop-diagnostics"],
    queryFn: ({ signal }) => desktopApi.diagnostics(signal),
    retry: false,
  });
  const data = query.data;
  const rows = data
    ? [
        { name: copy("版本", "Version"), value: data.api_version, ok: true },
        {
          name: copy("运行方式", "Runtime"),
          value: data.desktop_mode
            ? copy("桌面应用", "Desktop app")
            : copy("本机预览", "Local preview"),
          ok: true,
        },
        {
          name: copy("数据库", "Database"),
          value: `${data.db_backend} · ${data.sqlite_path}`,
          ok: data.database_directory.writable,
        },
        {
          name: copy("数据文件", "Data files"),
          value: `${data.storage_backend} · ${data.storage_path}`,
          ok: data.storage_directory.writable,
        },
        {
          name: copy("应用数据", "App data"),
          value: data.application_data.path ?? "—",
          ok: data.application_data.writable,
        },
      ]
    : [];
  return (
    <section className="settings-runtime">
      <header className="settings-section-heading">
        <h2>{copy("本机环境", "Local environment")}</h2>
        <Button
          variant="outline"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          <RefreshCwIcon className="size-4" />
          {copy("刷新", "Refresh")}
        </Button>
      </header>
      {query.isPending && (
        <p role="status">{copy("读取环境…", "Reading environment…")}</p>
      )}
      {query.isError && (
        <p role="alert">
          {copy(
            "未能读取本机环境，请重试。",
            "Could not read the local environment. Retry.",
          )}
        </p>
      )}
      <dl>
        {rows.map((row) => (
          <div key={row.name}>
            <dt>{row.name}</dt>
            <dd>{row.value}</dd>
            {row.ok ? (
              <CheckCircle2Icon
                className="size-4"
                aria-label={copy("正常", "Ready")}
              />
            ) : (
              <XCircleIcon
                className="size-4 text-destructive"
                aria-label={copy("需要检查", "Needs attention")}
              />
            )}
          </div>
        ))}
      </dl>
      <details className="model-maintenance">
        <summary>
          {copy("旧版会话配置", "Legacy session configuration")}
        </summary>
        <PreviewSettings />
      </details>
    </section>
  );
}
