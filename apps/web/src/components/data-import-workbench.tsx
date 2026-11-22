"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { navigateToAppRoute } from "@/lib/desktop-navigation";
import { useQuery } from "@tanstack/react-query";
import { FileSpreadsheetIcon, SearchIcon } from "lucide-react";
import { AuthGuard } from "./auth-guard";
import { ProjectShell } from "./project-shell";
import { FileImport } from "./file-import";
import { DataPreparation } from "./data-preparation";
import { DesktopLink } from "./desktop-link";
import { DownloadButton } from "./download-button";
import { ErrorState } from "./error-state";
import { useResultProjectId } from "./result-source";
import { usePreviewPublication } from "./preview-workspace";
import { datasetsApi } from "@/lib/api-endpoints";
import { friendlyApiError } from "@/lib/format";
import { useLanguage } from "@/providers/language-provider";
import { useDataSelection } from "@/lib/use-data-selection";
import { SourceFileUpdate } from "./source-file-update";
import { flushProjectEditors } from "@/lib/project-save";

const types: Record<string, string> = {
  integer: "整数",
  float: "数值",
  string: "文本",
  categorical: "分类",
  date: "日期",
  datetime: "日期时间",
  boolean: "布尔",
};

export default function DataImportWorkbench({
  projectId,
  preparationMode = false,
}: {
  projectId: string;
  preparationMode?: boolean;
}) {
  const id = useResultProjectId(projectId);
  if (!id || id === "__desktop_project__")
    return <p role="alert">项目不存在</p>;
  return (
    <AuthGuard>
      <ProjectShell projectId={id}>
        <DataBody key={id} projectId={id} preparationMode={preparationMode} />
      </ProjectShell>
    </AuthGuard>
  );
}

function DataBody({
  projectId,
  preparationMode,
}: {
  projectId: string;
  preparationMode: boolean;
}) {
  const { locale, t } = useLanguage();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  const params = useSearchParams();
  const router = useRouter();
  const [selectedId, setSelectedId] = useState("");
  const [filter, setFilter] = useState("");
  const [fields, setFields] = useState(false);
  const [hidden, setHidden] = useState<string[]>([]);
  const [importsOpen, setImportsOpen] = useState(false);
  const [selectionError, setSelectionError] = useState("");
  const selection = useDataSelection(projectId);
  const { ready: selectionReady, persist: persistSelection } = selection;
  const list = useQuery({
    queryKey: ["datasets", projectId],
    queryFn: ({ signal }) => datasetsApi.list(projectId, signal),
  });
  const datasets = (list.data ?? []).filter((d) => d.project_id === projectId);
  const activeId = params.get("datasetId") || selectedId || selection.selected;
  const active = datasets.find((d) => d.id === activeId) ?? datasets[0];
  const currentId = active?.id;
  useEffect(() => {
    if (selectionReady && currentId) persistSelection(currentId);
  }, [selectionReady, persistSelection, currentId]);
  const filename = active?.original_filename.split(/[\\/]/).pop();
  const preview = useQuery({
    queryKey: ["dataset-preview", active?.id],
    queryFn: ({ signal }) => datasetsApi.preview(active!.id, 20, signal),
    enabled: !!active,
  });
  const data =
    preview.data?.dataset_id === active?.id ? preview.data : undefined;
  const columns = (data?.columns ?? []).filter((c) => !hidden.includes(c.name));
  const rows = (data?.rows ?? []).filter(
    (row) =>
      !filter.trim() ||
      Object.values(row).some((v) =>
        String(v ?? "")
          .toLocaleLowerCase()
          .includes(filter.trim().toLocaleLowerCase()),
      ),
  );
  usePreviewPublication({
    page: active ? active.original_filename : copy("项目文件", "Project files"),
    scope: {
      project_id: projectId,
      ...(active
        ? {
            dataset_id: active.id,
            dataset_sha256: active.sha256,
            selected_columns: columns.map((c) => c.name).slice(0, 50),
          }
        : {}),
    },
    draft: "",
  });
  function choose(id: string) {
    void flushProjectEditors(projectId)
      .then(() => {
        setSelectionError("");
        setSelectedId(id);
        setFilter("");
        setHidden([]);
        const url = new URL(window.location.href);
        url.searchParams.set("datasetId", id);
        window.history.replaceState(null, "", url.href);
      })
      .catch((reason) =>
        setSelectionError(
          reason instanceof Error
            ? reason.message
            : copy("草稿未保存。", "Draft not saved."),
        ),
      );
  }
  return (
    <div className="data-workbench">
      <header className="data-file-heading">
        <div>
          <h2>
            {preparationMode ? (
              copy("数据整理", "Data preparation")
            ) : active ? (
              <DesktopLink
                href={`/projects/${projectId}/data/${active.id}`}
                title={copy("文件详情", "File details")}
              >
                {filename}
              </DesktopLink>
            ) : (
              copy("项目数据", "Project data")
            )}
          </h2>
          {active && (
            <p>
              {active.file_format.toUpperCase()} · {active.row_count ?? "—"}{" "}
              {copy("行", "rows")} · {active.column_count ?? "—"}{" "}
              {copy("列", "columns")}
              {active.sheet_name && <> · {active.sheet_name}</>}
            </p>
          )}
          {active?.source_path && active.source_path !== filename && (
            <p>{active.source_path}</p>
          )}
        </div>
        <details
          open={importsOpen || (list.isSuccess && !datasets.length)}
          onToggle={(event) => {
            if (datasets.length) setImportsOpen(event.currentTarget.open);
          }}
          className="data-import-details"
        >
          <summary>{copy("导入数据", "Import data")}</summary>
          <FileImport
            projectId={projectId}
            onImported={(openedProject, id) => {
              if (openedProject !== projectId) {
                navigateToAppRoute(router, `/projects/${openedProject}/data`);
                return;
              }
              if (id) choose(id);
              setImportsOpen(false);
            }}
          />
        </details>
      </header>
      {selectionError && <p role="alert">{selectionError}</p>}
      {active && (
        <SourceFileUpdate key={active.id} dataset={active} onSelect={choose} />
      )}
      {selection.failed && (
        <p role="alert">
          {copy(
            "未能保存当前文件，请刷新后重试。",
            "Could not save the current file. Refresh and retry.",
          )}
        </p>
      )}
      <div className={preparationMode ? "data-preparation-layout" : undefined}>
        <section className="data-preparation-editor">
          {list.isError && (
            <ErrorState
              {...friendlyApiError(list.error, t)}
              onRetry={() => void list.refetch()}
            />
          )}
          {list.isPending && (
            <p role="status">{copy("读取文件…", "Loading files…")}</p>
          )}
          {active && (
            <>
              <div className="data-preview-heading">
                <span>
                  <FileSpreadsheetIcon className="size-4" />
                  {preparationMode
                    ? filename
                    : copy("数据预览", "Data preview")}
                </span>
                <div>
                  {!preparationMode && (
                    <DesktopLink
                      href={`/projects/${projectId}/prepare?datasetId=${active.id}`}
                    >
                      {copy("整理数据", "Prepare data")}
                    </DesktopLink>
                  )}
                  <DownloadButton
                    identity={active.id}
                    download={(signal) =>
                      datasetsApi.download(
                        active.id,
                        active.original_filename,
                        signal,
                      )
                    }
                  >
                    {active.preparation_steps
                      ? copy("导出数据", "Export data")
                      : copy("原文件", "Original file")}
                  </DownloadButton>
                  <DesktopLink
                    href={`/projects/${projectId}/workspace?datasetId=${active.id}`}
                  >
                    {copy("分析数据", "Analyze data")} →
                  </DesktopLink>
                </div>
              </div>
              <div className="data-preview-tools">
                <label className="data-preview-search">
                  <SearchIcon className="size-4" />
                  <input
                    aria-label={copy("搜索当前预览", "Search this preview")}
                    placeholder={copy("搜索当前预览", "Search this preview")}
                    value={filter}
                    onChange={(event) => setFilter(event.target.value)}
                  />
                </label>
                <SearchSelect
                  aria-label={copy("当前文件", "Current file")}
                  value={active.id}
                  onChange={(event) => {
                    choose(event.target.value);
                  }}
                >
                  {datasets.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.original_filename.split(/[\\/]/).pop()}
                      {(d.source_parent_id ||
                        datasets.some(
                          (other) =>
                            other.id !== d.id &&
                            other.source_root_id === d.source_root_id,
                        )) &&
                        ` · v${d.source_version || 1}`}
                    </option>
                  ))}
                </SearchSelect>
                <button
                  type="button"
                  aria-expanded={fields}
                  onClick={() => setFields(!fields)}
                >
                  {copy("显示列", "Columns")} {columns.length}/
                  {data?.columns.length ?? 0}
                </button>
              </div>
              {fields && (
                <fieldset className="data-column-picker">
                  <legend className="sr-only">
                    {copy("显示列", "Columns")}
                  </legend>
                  {data?.columns.map((c) => (
                    <label key={c.name}>
                      <input
                        type="checkbox"
                        checked={!hidden.includes(c.name)}
                        onChange={(event) =>
                          setHidden(
                            event.target.checked
                              ? hidden.filter((n) => n !== c.name)
                              : [...hidden, c.name],
                          )
                        }
                      />
                      {c.name}
                    </label>
                  ))}
                </fieldset>
              )}
              {preview.isPending && (
                <p role="status">{copy("读取预览…", "Loading preview…")}</p>
              )}
              {preview.isError && (
                <ErrorState
                  {...friendlyApiError(preview.error, t)}
                  onRetry={() => void preview.refetch()}
                />
              )}
              {data && (
                <>
                  <div className="data-table-scroll">
                    <table className="data-preview-table">
                      <caption className="sr-only">
                        {active.original_filename}
                      </caption>
                      <thead>
                        <tr>
                          {columns.map((c) => (
                            <th key={c.name} scope="col">
                              <strong>{c.name}</strong>
                              <span>
                                {locale === "zh-CN"
                                  ? (types[c.inferred_type] ?? c.inferred_type)
                                  : c.inferred_type}
                              </span>
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((row, i) => (
                          <tr key={i}>
                            {columns.map((c) => (
                              <td
                                key={c.name}
                                data-numeric={
                                  c.inferred_type === "integer" ||
                                  c.inferred_type === "float"
                                }
                              >
                                {row[c.name] == null ? (
                                  <span className="data-null">—</span>
                                ) : (
                                  String(row[c.name])
                                )}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="data-preview-count">
                    {copy(
                      `预览 ${rows.length} / ${data.total_rows} 行`,
                      `Preview ${rows.length} / ${data.total_rows} rows`,
                    )}
                  </p>
                  <details className="data-schema">
                    <summary>
                      {copy(
                        "字段类型与缺失值",
                        "Column types and missing values",
                      )}
                    </summary>
                    <ul>
                      {data.columns.map((c) => (
                        <li key={c.name}>
                          <strong>{c.name}</strong>
                          <span>{c.polars_type}</span>
                          <span>
                            {copy("缺失", "Missing")} {c.null_count}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </details>
                </>
              )}
            </>
          )}
          {list.isSuccess && !datasets.length && (
            <p className="data-no-files">
              {copy(
                "打开文件夹，或选择 CSV、Excel、Parquet 文件。",
                "Open a folder, or choose CSV, Excel or Parquet files.",
              )}
            </p>
          )}
        </section>
        {preparationMode && active && data && (
          <DataPreparation
            key={active.id}
            dataset={active}
            datasets={datasets}
            columns={data.columns}
            onChoose={choose}
          />
        )}
      </div>
    </div>
  );
}
