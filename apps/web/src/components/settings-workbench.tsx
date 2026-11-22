"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeftIcon, FolderOpenIcon } from "lucide-react";
import { AuthGuard } from "./auth-guard";
import { DesktopLink } from "./desktop-link";
import { ModelConnectionsPanel } from "./model-connections";
import { RuntimeDiagnostics } from "./runtime-diagnostics";
import { Button } from "./ui/button";
import { useLanguage } from "@/providers/language-provider";
import { useAppearance } from "@/providers/appearance-provider";
import { useWorkspacePreferences } from "@/providers/workspace-preferences-provider";
import { MODES, PRESETS, ACCENTS, TEXT_SIZES } from "@/lib/appearance";
import { SHORTCUT_KEYS } from "@/lib/workspace-preferences";
import { localEnvironment } from "@/lib/local-environment";
import { desktopApi } from "@/lib/api-endpoints";
import { isTauri } from "@/lib/tauri-adapter";

type Section = "general" | "models" | "runtime" | "files" | "export";

export default function SettingsWorkbench() {
  return (
    <AuthGuard>
      <SettingsBody />
    </AuthGuard>
  );
}

function SettingsBody() {
  const { locale, setLocale, t, preferenceSaved } = useLanguage();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  const appearance = useAppearance();
  const preferences = useWorkspacePreferences();
  const { value, update } = preferences;
  const [section, setSection] = useState<Section>("general");
  const [explicitReturn, setExplicitReturn] = useState("");
  const [readDiagnostics, setReadDiagnostics] = useState(false);
  const [failure, setFailure] = useState("");
  const [choosingFolder, setChoosingFolder] = useState(false);
  const returnTo = explicitReturn || preferences.lastProject || "/dashboard";
  useEffect(() => {
    function sync() {
      const hash = location.hash.slice(1);
      setSection(
        hash === "diagnostics"
          ? "runtime"
          : ["models", "runtime", "files", "export"].includes(hash)
            ? (hash as Section)
            : "general",
      );
      const target = new URL(location.href).searchParams.get("returnTo");
      setExplicitReturn("");
      if (target) {
        try {
          const url = new URL(target, location.origin);
          if (
            url.origin === location.origin &&
            /^\/projects\/(?:[0-9a-f-]{36}|__desktop_project__)\/[a-z][a-z0-9-]*(?:\/|$)/.test(
              url.pathname,
            )
          )
            setExplicitReturn(url.pathname + url.search + url.hash);
        } catch {
          /* Keep the local return route. */
        }
      }
    }
    sync();
    window.addEventListener("hashchange", sync);
    window.addEventListener("popstate", sync);
    return () => {
      window.removeEventListener("hashchange", sync);
      window.removeEventListener("popstate", sync);
    };
  }, []);
  const environment = useQuery({
    queryKey: ["local-environment"],
    queryFn: ({ signal }) => localEnvironment(signal),
    enabled: section === "runtime",
    retry: false,
    staleTime: 30000,
  });
  const storage = useQuery({
    queryKey: ["desktop-diagnostics"],
    queryFn: ({ signal }) => desktopApi.diagnostics(signal),
    enabled: section === "files",
    retry: false,
    staleTime: 30000,
  });
  function choose(next: Section) {
    setSection(next);
    setFailure("");
    const url = new URL(location.href);
    url.hash = next;
    history.replaceState(history.state, "", url);
  }
  function shortcut(key: "settingsKey" | "projectKey", next: string) {
    if (!update({ [key]: next as typeof value.settingsKey }))
      setFailure(
        copy("两个快捷键不能相同。", "Choose different shortcut keys."),
      );
    else setFailure("");
  }
  async function chooseFolder() {
    if (!isTauri() || choosingFolder) return;
    setChoosingFolder(true);
    setFailure("");
    try {
      const native = (
        window as unknown as {
          __TAURI_INTERNALS__: {
            invoke: (command: string, args: unknown) => Promise<unknown>;
          };
        }
      ).__TAURI_INTERNALS__;
      const path = await native.invoke("plugin:dialog|open", {
        options: {
          title: copy("默认保存位置", "Default save folder"),
          directory: true,
          multiple: false,
          ...(value.saveDirectory ? { defaultPath: value.saveDirectory } : {}),
        },
      });
      if (typeof path === "string" && !update({ saveDirectory: path }))
        throw new Error("Invalid folder");
    } catch {
      setFailure(
        copy(
          "未能选择保存位置，请重试。",
          "Could not select a folder. Try again.",
        ),
      );
    } finally {
      setChoosingFolder(false);
    }
  }
  const labels: [Section, string][] = [
    ["general", copy("通用", "General")],
    ["models", copy("AI 模型", "AI models")],
    ["runtime", copy("代码环境", "Code environment")],
    ["files", copy("文件", "Files")],
    ["export", copy("导出", "Export")],
  ];
  return (
    <div className="settings-workbench">
      <aside className="settings-navigation">
        <DesktopLink className="settings-back" href={returnTo}>
          <ArrowLeftIcon className="size-4" />
          {returnTo === "/dashboard"
            ? copy("返回首页", "Home")
            : copy("返回项目", "Back to project")}
        </DesktopLink>
        <h1>{copy("设置", "Settings")}</h1>
        <nav aria-label={copy("设置分类", "Settings sections")}>
          {labels.map(([key, label]) => (
            <button
              type="button"
              key={key}
              aria-controls={`settings-${key}`}
              aria-current={section === key ? "page" : undefined}
              onClick={() => choose(key)}
            >
              {label}
            </button>
          ))}
        </nav>
      </aside>
      <div className="settings-content">
        <div id="settings-models" hidden={section !== "models"}>
          <ModelConnectionsPanel />
        </div>
        <section id="settings-general" hidden={section !== "general"}>
          <header className="settings-section-heading">
            <h2>{copy("通用", "General")}</h2>
          </header>
          <Preference title={copy("语言", "Language")}>
            <SearchSelect
              aria-label={copy("界面语言", "Interface language")}
              value={locale}
              onChange={(e) => setLocale(e.target.value as "zh-CN" | "en")}
            >
              <option value="zh-CN">简体中文</option>
              <option value="en">English</option>
            </SearchSelect>
          </Preference>
          <Preference title={copy("主题", "Theme")}>
            <SearchSelect
              aria-label={copy("界面主题", "Interface theme")}
              disabled={!appearance.ready}
              value={appearance.value.mode}
              onChange={(e) =>
                appearance.update({
                  mode: e.target.value as typeof appearance.value.mode,
                })
              }
            >
              {MODES.map((mode) => (
                <option key={mode} value={mode}>
                  {t(`entry.mode.${mode}`)}
                </option>
              ))}
            </SearchSelect>
          </Preference>
          <Preference title={copy("配色", "Palette")}>
            <SearchSelect
              aria-label={copy("界面配色", "Interface palette")}
              disabled={!appearance.ready}
              value={appearance.value.preset}
              onChange={(e) =>
                appearance.update({
                  preset: e.target.value as typeof appearance.value.preset,
                })
              }
            >
              {PRESETS.map((preset) => (
                <option key={preset} value={preset}>
                  {t(`entry.preset.${preset}`)}
                </option>
              ))}
            </SearchSelect>
          </Preference>
          <Preference title={copy("强调色", "Accent")}>
            <SearchSelect
              aria-label={copy("界面强调色", "Interface accent")}
              disabled={!appearance.ready}
              value={appearance.value.accent}
              onChange={(e) =>
                appearance.update({
                  accent: e.target.value as typeof appearance.value.accent,
                })
              }
            >
              {ACCENTS.map((accent) => (
                <option key={accent} value={accent}>
                  {t(`entry.accent.${accent}`)}
                </option>
              ))}
            </SearchSelect>
          </Preference>
          <Preference title={copy("字号", "Text size")}>
            <div
              className="settings-choice-group"
              role="group"
              aria-label={copy("界面字号", "Interface text size")}
            >
              {TEXT_SIZES.map((size, i) => (
                <button
                  key={size}
                  disabled={!appearance.ready}
                  aria-pressed={appearance.value.textSize === size}
                  onClick={() => appearance.update({ textSize: size })}
                >
                  {
                    [
                      copy("较小", "Small"),
                      copy("标准", "Standard"),
                      copy("较大", "Large"),
                    ][i]
                  }
                </button>
              ))}
            </div>
          </Preference>

        </section>
        <section id="settings-runtime" hidden={section !== "runtime"}>
          <header className="settings-section-heading">
            <h2>{copy("代码环境", "Code environment")}</h2>
          </header>
          {environment.isPending && (
            <p role="status">{copy("读取环境…", "Reading environment…")}</p>
          )}
          {environment.isError && (
            <p role="alert">
              {copy("未能读取环境。", "Could not read environment.")}{" "}
              <button
                className="settings-text-action"
                onClick={() => void environment.refetch()}
              >
                {copy("重试", "Retry")}
              </button>
            </p>
          )}
          {environment.data && (
            <>
              <Preference title={copy("默认编辑器", "Default editor")}>
                <SearchSelect
                  aria-label={copy("默认代码编辑器", "Default code editor")}
                  disabled={!preferences.ready}
                  value={value.editor}
                  onChange={(e) =>
                    update({ editor: e.target.value as typeof value.editor })
                  }
                >
                  <option value="auto">{copy("自动选择", "Automatic")}</option>
                  {environment.data.editors.map((editor) => (
                    <option key={editor.id} value={editor.id}>
                      {editor.name}
                    </option>
                  ))}
                  {value.editor !== "auto" &&
                    !environment.data.editors.some(
                      (editor) => editor.id === value.editor,
                    ) && (
                      <option value={value.editor} disabled>
                        {value.editor} · {copy("未找到", "Not found")}
                      </option>
                    )}
                </SearchSelect>
              </Preference>
              <Preference title="Python">
                <span className="settings-value">
                  {environment.data.python_version}
                </span>
              </Preference>
              <details className="settings-details">
                <summary>{copy("运行库与路径", "Libraries and path")}</summary>
                <dl className="settings-facts">
                  <div>
                    <dt>Python</dt>
                    <dd>{environment.data.python_executable}</dd>
                  </div>
                  {environment.data.packages.map((pkg) => (
                    <div key={pkg.name}>
                      <dt>{pkg.name}</dt>
                      <dd>{pkg.version || copy("未安装", "Not installed")}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            </>
          )}
          <details
            className="settings-details"
            onToggle={(e) => {
              if (e.currentTarget.open) setReadDiagnostics(true);
            }}
          >
            <summary>
              {copy("诊断与旧版配置", "Diagnostics and legacy configuration")}
            </summary>
            {readDiagnostics && <RuntimeDiagnostics />}
          </details>
        </section>
        <section id="settings-files" hidden={section !== "files"}>
          <header className="settings-section-heading">
            <h2>{copy("文件", "Files")}</h2>
          </header>
          <Preference title={copy("默认保存位置", "Default save folder")}>
            <div className="settings-folder">
              {preferences.ready && isTauri() ? (
                <>
                  <span>
                    {value.saveDirectory ||
                      copy("每次选择", "Choose each time")}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={choosingFolder}
                    onClick={() => void chooseFolder()}
                  >
                    <FolderOpenIcon className="size-4" />
                    {copy("选择文件夹", "Choose folder")}
                  </Button>
                  {value.saveDirectory && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => update({ saveDirectory: "" })}
                    >
                      {copy("重置", "Reset")}
                    </Button>
                  )}
                </>
              ) : (
                <span>
                  {copy(
                    "使用浏览器的下载设置。",
                    "Uses your browser download settings.",
                  )}
                </span>
              )}
            </div>
          </Preference>
          {storage.isPending && (
            <p role="status">
              {copy("读取存储位置…", "Reading storage locations…")}
            </p>
          )}
          {storage.isError && (
            <p role="alert">
              {copy("未能读取存储位置。", "Could not read storage locations.")}{" "}
              <button
                className="settings-text-action"
                onClick={() => void storage.refetch()}
              >
                {copy("重试", "Retry")}
              </button>
            </p>
          )}
          {storage.data && (
            <dl className="settings-facts">
              {[
                [
                  copy("项目数据", "Project data"),
                  storage.data.storage_directory,
                ],
                [
                  copy("本机数据库", "Local database"),
                  storage.data.database_directory,
                ],
                [copy("日志", "Logs"), storage.data.log_directory],
              ].map(([label, directory]) => {
                const place = directory as {
                  path: string | null;
                  writable: boolean;
                };
                return (
                  <div key={String(label)}>
                    <dt>{String(label)}</dt>
                    <dd>
                      {place.path || "—"}
                      {!place.writable && (
                        <span className="settings-storage-warning">
                          {copy("无法写入", "Cannot write")}
                        </span>
                      )}
                    </dd>
                  </div>
                );
              })}
            </dl>
          )}
        </section>
        <section id="settings-export" hidden={section !== "export"}>
          <header className="settings-section-heading">
            <h2>{copy("导出", "Export")}</h2>
          </header>
          <Preference title={copy("默认图形格式", "Default figure format")}>
            <SearchSelect
              aria-label={copy("默认图形格式", "Default figure format")}
              value={value.imageFormat}
              disabled={!preferences.ready}
              onChange={(e) =>
                update({
                  imageFormat: e.target.value as typeof value.imageFormat,
                })
              }
            >
              {["png", "svg", "pdf"].map((format) => (
                <option key={format} value={format}>
                  {format.toUpperCase()}
                </option>
              ))}
            </SearchSelect>
          </Preference>
          <Preference title={copy("新图形分辨率", "New figure resolution")}>
            <SearchSelect
              aria-label={copy("新图形 DPI", "New figure DPI")}
              value={value.plotDpi}
              disabled={!preferences.ready}
              onChange={(e) =>
                update({
                  plotDpi: Number(e.target.value) as typeof value.plotDpi,
                })
              }
            >
              {[150, 300, 600].map((dpi) => (
                <option key={dpi} value={dpi}>
                  {dpi} DPI
                </option>
              ))}
            </SearchSelect>
          </Preference>
          <Preference title={copy("默认成果格式", "Default report format")}>
            <SearchSelect
              aria-label={copy("默认成果格式", "Default report format")}
              value={value.reportFormat}
              disabled={!preferences.ready}
              onChange={(e) =>
                update({
                  reportFormat: e.target.value as typeof value.reportFormat,
                })
              }
            >
              {["html", "md", "json"].map((format) => (
                <option key={format} value={format}>
                  {format === "md" ? "Markdown" : format.toUpperCase()}
                </option>
              ))}
            </SearchSelect>
          </Preference>
        </section>
        {failure && (
          <p role="alert" className="settings-feedback">
            {failure}
          </p>
        )}
        {(!preferences.saved || !appearance.saved || !preferenceSaved) && (
          <p role="alert" className="settings-feedback">
            {copy(
              "设置未能保存，请重新选择。",
              "Settings were not saved. Select again.",
            )}
          </p>
        )}
      </div>
    </div>
  );
}

function Preference({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="settings-preference">
      <span>{title}</span>
      {children}
    </div>
  );
}
