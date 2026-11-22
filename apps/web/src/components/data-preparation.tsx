"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRightIcon, Undo2Icon } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "./ui/dialog";
import { Button } from "./ui/button";
import { datasetsApi } from "@/lib/api-endpoints";
import {
  preparationApi,
  type PreparationOperation,
  type PreparationPreview,
  type PreparationRequest,
} from "@/lib/preparation-api";
import type { Dataset, DatasetPreview } from "@/lib/types";
import { useLanguage } from "@/providers/language-provider";
import { usePreparationDraft } from "@/lib/use-preparation-draft";

export function DataPreparation({
  dataset,
  datasets,
  columns,
  onChoose,
}: {
  dataset: Dataset;
  datasets: Dataset[];
  columns: DatasetPreview["columns"];
  onChoose: (id: string) => void | Promise<void>;
}) {
  const { locale } = useLanguage();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  const queryClient = useQueryClient();
  const history = useQuery({
    queryKey: ["preparation", dataset.id],
    queryFn: ({ signal }) => preparationApi.metadata(dataset.id, signal),
  });
  const [operation, setOperation] = useState<PreparationOperation | null>(null);
  const [body, setBody] = useState<PreparationRequest>({
    source_sha256: dataset.sha256,
    operation: "missing",
    column: "",
  });
  const [preview, setPreview] = useState<PreparationPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [createdVersion, setCreatedVersion] = useState<Dataset | null>(null);
  const pendingDraft = usePreparationDraft(
    dataset.project_id,
    dataset.id,
    setBody,
  );
  const right = useQuery({
    queryKey: ["dataset-preview", body.right_dataset_id],
    queryFn: ({ signal }) =>
      datasetsApi.preview(body.right_dataset_id!, 20, signal),
    enabled: operation === "join" && !!body.right_dataset_id,
  });
  const actions: { id: PreparationOperation; title: string; hint: string }[] = [
    {
      id: "missing",
      title: copy("处理缺失", "Handle missing values"),
      hint: copy(
        `${columns.reduce((sum, c) => sum + c.null_count, 0)} 个空值`,
        `${columns.reduce((sum, c) => sum + c.null_count, 0)} null values`,
      ),
    },
    {
      id: "units",
      title: copy("统一单位", "Convert units"),
      hint: copy("时间、长度、质量", "Time, length, mass"),
    },
    {
      id: "dates",
      title: copy("统一日期", "Normalize dates"),
      hint: "YYYY-MM-DD",
    },
    {
      id: "join",
      title: copy("关联数据表", "Join tables"),
      hint: copy("按键左关联", "Left join by key"),
    },
  ];
  const other: [PreparationOperation, string][] = [
    ["rename", copy("字段重命名", "Rename column")],
    ["cast", copy("转换类型", "Change type")],
    ["trim", copy("清除空格", "Trim whitespace")],
    ["deduplicate", copy("去除重复行", "Remove duplicate rows")],
  ];
  function start(id: PreparationOperation) {
    setCreatedVersion(null);
    setOperation(id);
    setPreview(null);
    setError("");
    const col =
      id === "missing"
        ? columns.find((c) => c.null_count > 0)
        : id === "units" || id === "dates" || id === "trim"
          ? columns.find((c) => c.inferred_type === "string")
          : columns[0];
    const initial: PreparationRequest = {
      source_sha256: dataset.sha256,
      operation: id,
      column: id === "deduplicate" ? "" : (col?.name ?? columns[0]?.name ?? ""),
      rule: "keep",
      target: id === "units" ? "ms" : id === "cast" ? "text" : "",
      duplicate: "block",
    };
    setBody(initial);
    pendingDraft.change(initial);
  }
  function update(patch: Partial<PreparationRequest>) {
    setCreatedVersion(null);
    const next = { ...body, ...patch };
    setBody(next);
    pendingDraft.change(next);
    setPreview(null);
    setError("");
  }
  async function inspect() {
    setBusy(true);
    setError("");
    setPreview(null);
    try {
      setPreview(await preparationApi.preview(dataset.id, body));
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : copy("预览失败，请重试。", "Preview failed. Please retry."),
      );
    } finally {
      setBusy(false);
    }
  }
  async function apply() {
    if (!preview?.result_sha256) return;
    setBusy(true);
    setError("");
    try {
      const saved = createdVersion ?? await preparationApi.apply(dataset.id, {
        ...body,
        result_sha256: preview.result_sha256,
      });
      setCreatedVersion(saved);
      // Publish the returned version before handing its identity to the task.
      // A background list request must not overwrite it with an older snapshot.
      await queryClient.cancelQueries({ queryKey: ["datasets", dataset.project_id] });
      queryClient.setQueryData<Dataset[]>(["datasets", dataset.project_id], (prior) => [
        saved,
        ...(prior ?? datasets).filter((item) => item.id !== saved.id),
      ]);
      const prepared = await datasetsApi.preview(saved.id, 100);
      queryClient.setQueryData(["dataset-preview", saved.id, 100], prepared);
      await queryClient.invalidateQueries({
        queryKey: ["datasets", dataset.project_id],
        refetchType: "none",
      });
      pendingDraft.change(null);
      await pendingDraft.flush();
      setOperation(null);
      await onChoose(saved.id);
      setCreatedVersion(null);
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : copy("应用失败，请重试。", "Could not apply. Please retry."),
      );
    } finally {
      setBusy(false);
    }
  }
  const title =
    actions.find((a) => a.id === operation)?.title ??
    other.find((a) => a[0] === operation)?.[1];
  return (
    <aside
      className="preparation-panel"
      aria-label={copy("数据整理", "Data preparation")}
    >
      <h3>{copy("整理数据", "Prepare data")}</h3>
      {createdVersion && !busy && <p role="status">
        {createdVersion.id === dataset.id ? copy("表格内容相同，已沿用当前版本。", "The table is unchanged. Using the current version.") : copy("处理结果已保存。", "The result is saved.")}
        <button type="button" className="underline" onClick={() => void apply()}>{copy("打开结果", "Open result")}</button>
      </p>}
      {pendingDraft.error && <p role="alert">{pendingDraft.error}</p>}
      {pendingDraft.hasDraft && !operation && (
        <Button
          variant="outline"
          disabled={!pendingDraft.ready || busy}
          onClick={() => {
            setOperation(body.operation);
            setPreview(null);
          }}
        >
          {copy("继续整理草稿", "Continue preparation draft")}
        </Button>
      )}
      {pendingDraft.hasDraft && (
        <p className="preparation-draft-status" role="status">
          {pendingDraft.status === "saved"
            ? copy("草稿已保存", "Draft saved")
            : pendingDraft.status === "failed"
              ? copy("草稿未保存", "Draft not saved")
              : copy("保存草稿中…", "Saving draft…")}
        </p>
      )}
      <div className="preparation-actions">
        {actions.map((action, index) => (
          <button
            type="button"
            key={action.id}
            disabled={!pendingDraft.ready || busy}
            onClick={() => start(action.id)}
          >
            <span className="preparation-number">
              {String(index + 1).padStart(2, "0")}
            </span>
            <span>
              <strong>{action.title}</strong>
              <small>{action.hint}</small>
            </span>
            <ArrowRightIcon className="size-4" />
          </button>
        ))}
      </div>
      <details className="preparation-more">
        <summary>{copy("更多操作", "More actions")}</summary>
        {other.map(([id, label]) => (
          <button
            key={id}
            type="button"
            disabled={!pendingDraft.ready || busy}
            onClick={() => start(id)}
          >
            {label}
            <ArrowRightIcon className="size-3.5" />
          </button>
        ))}
      </details>
      {history.isError && (
        <p role="alert">
          {copy("处理记录读取失败。", "Could not read preparation history.")}
        </p>
      )}
      <details
        className="preparation-source"
        open={!!history.data?.steps.length}
      >
        <summary>
          {copy("来源与处理步骤", "Source and steps")}
          {history.data?.steps.length ? ` · ${history.data.steps.length}` : ""}
        </summary>
        {history.data?.root_id && (
          <button
            type="button"
            onClick={() => onChoose(history.data!.root_id!)}
          >
            {copy("查看原始数据", "View original data")}
          </button>
        )}
        <ol>
          {history.data?.steps.map((step, index) => (
            <li key={index}>
              <strong>{step.label}</strong>
              <span>
                {step.recipe.column}
                {step.recipe.target ? ` → ${step.recipe.target}` : ""}
              </span>
              <small>
                {copy(
                  `影响 ${step.affected_rows} 行`,
                  `${step.affected_rows} rows affected`,
                )}
                {step.removed_rows > 0
                  ? copy(
                      ` · 删除 ${step.removed_rows} 行`,
                      ` · ${step.removed_rows} rows removed`,
                    )
                  : ""}
              </small>
            </li>
          ))}
        </ol>
      </details>
      {history.data?.parent_id && (
        <button
          className="preparation-undo"
          type="button"
          onClick={() => onChoose(history.data!.parent_id!)}
        >
          <Undo2Icon className="size-4" />
          {copy("撤销最近一步", "Undo last step")}
        </button>
      )}
      <Dialog
        open={!!operation}
        onOpenChange={(open) => {
          if (!open && !busy) setOperation(null);
        }}
      >
        <DialogContent
          className="preparation-dialog sm:max-w-2xl"
          aria-describedby="preparation-description"
        >
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription id="preparation-description" className="sr-only">
            {copy(
              "选择规则后预览，再应用到数据副本。",
              "Choose a rule, preview it, then apply to a data copy.",
            )}
          </DialogDescription>
          <fieldset disabled={busy} className="preparation-form">
            <label>
              {copy("字段", "Column")}
              <SearchSelect
                value={body.column}
                onChange={(e) => update({ column: e.target.value })}
              >
                {operation === "deduplicate" && (
                  <option value="">{copy("所有字段", "All columns")}</option>
                )}
                {columns.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name}
                  </option>
                ))}
              </SearchSelect>
            </label>
            {operation === "missing" && (
              <>
                <label>
                  {copy("处理规则", "Rule")}
                  <SearchSelect
                    value={body.rule}
                    onChange={(e) =>
                      update({
                        rule: e.target.value as PreparationRequest["rule"],
                      })
                    }
                  >
                    <option value="keep">
                      {copy("保留缺失", "Keep missing")}
                    </option>
                    <option value="median">
                      {copy("中位数填补", "Fill with median")}
                    </option>
                    <option value="mean">
                      {copy("均值填补", "Fill with mean")}
                    </option>
                    <option value="value">
                      {copy("指定值填补", "Fill with a value")}
                    </option>
                    <option value="drop">
                      {copy(
                        "移除该字段缺失的行",
                        "Remove rows missing this column",
                      )}
                    </option>
                  </SearchSelect>
                </label>
                {body.rule === "value" && (
                  <label>
                    {copy("填充值", "Fill value")}
                    <input
                      value={body.value ?? ""}
                      onChange={(e) => update({ value: e.target.value })}
                    />
                  </label>
                )}
              </>
            )}
            {operation === "units" && (
              <label>
                {copy("目标单位", "Target unit")}
                <SearchSelect
                  value={body.target}
                  onChange={(e) => update({ target: e.target.value })}
                >
                  {[
                    "ms",
                    "s",
                    "min",
                    "h",
                    "mm",
                    "cm",
                    "m",
                    "mg",
                    "g",
                    "kg",
                  ].map((unit) => (
                    <option key={unit}>{unit}</option>
                  ))}
                </SearchSelect>
              </label>
            )}
            {operation === "dates" && (
              <label>
                {copy("原日期格式", "Source date format")}
                <SearchSelect
                  value={body.value ?? ""}
                  onChange={(e) => update({ value: e.target.value })}
                >
                  <option value="">
                    {copy("自动识别年月日", "Auto Y-M-D")}
                  </option>
                  {[
                    "YYYY-MM-DD",
                    "YYYY/MM/DD",
                    "DD/MM/YYYY",
                    "MM/DD/YYYY",
                    "YYYYMMDD",
                  ].map((format) => (
                    <option key={format}>{format}</option>
                  ))}
                </SearchSelect>
              </label>
            )}
            {operation === "rename" && (
              <label>
                {copy("新字段名", "New name")}
                <input
                  value={body.target ?? ""}
                  onChange={(e) => update({ target: e.target.value })}
                />
              </label>
            )}
            {operation === "cast" && (
              <label>
                {copy("类型", "Type")}
                <SearchSelect
                  value={body.target}
                  onChange={(e) => update({ target: e.target.value })}
                >
                  <option value="text">{copy("文本", "Text")}</option>
                  <option value="integer">{copy("整数", "Integer")}</option>
                  <option value="number">{copy("数值", "Number")}</option>
                  <option value="boolean">{copy("布尔", "Boolean")}</option>
                </SearchSelect>
              </label>
            )}
            {operation === "join" && (
              <>
                <label>
                  {copy("关联表", "Right table")}
                  <SearchSelect
                    value={body.right_dataset_id ?? ""}
                    onChange={(e) => {
                      const selected = datasets.find(
                        (d) => d.id === e.target.value,
                      );
                      update({
                        right_dataset_id: selected?.id,
                        right_sha256: selected?.sha256,
                        right_key: "",
                      });
                    }}
                  >
                    <option value="">
                      {copy("选择数据表", "Choose a table")}
                    </option>
                    {datasets
                      .filter((d) => d.id !== dataset.id && d.upload_status === "ready")
                      .map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.original_filename}
                        </option>
                      ))}
                  </SearchSelect>
                </label>
                <label>
                  {copy("关联表键字段", "Right key")}
                  <SearchSelect
                    disabled={!right.data}
                    value={body.right_key ?? ""}
                    onChange={(e) => update({ right_key: e.target.value })}
                  >
                    <option value="">{copy("选择键字段", "Choose key")}</option>
                    {right.data?.columns.map((c) => (
                      <option key={c.name}>{c.name}</option>
                    ))}
                  </SearchSelect>
                </label>
                <label>
                  {copy("右表重复键", "Duplicate right keys")}
                  <SearchSelect
                    value={body.duplicate}
                    onChange={(e) =>
                      update({
                        duplicate: e.target
                          .value as PreparationRequest["duplicate"],
                      })
                    }
                  >
                    <option value="block">
                      {copy("发现重复后暂停", "Stop if duplicates exist")}
                    </option>
                    <option value="first">
                      {copy("保留首次出现的行", "Keep first row")}
                    </option>
                    <option value="last">
                      {copy("保留最后出现的行", "Keep last row")}
                    </option>
                  </SearchSelect>
                </label>
                {right.isError && (
                  <p role="alert">
                    {copy("关联表读取失败。", "Could not read right table.")}
                  </p>
                )}
              </>
            )}
          </fieldset>
          {preview && (
            <section
              className="preparation-preview"
              aria-label={copy("变化预览", "Change preview")}
            >
              {preview.join && (
                <p>
                  {copy(
                    `匹配 ${preview.join.matched} 行 · 未匹配 ${preview.join.unmatched} 行 · 右表重复键 ${preview.join.duplicate_keys} 个`,
                    `${preview.join.matched} matched · ${preview.join.unmatched} unmatched · ${preview.join.duplicate_keys} duplicate right keys`,
                  )}
                </p>
              )}
              {!!preview.join?.unmatched_examples?.length && <details><summary>查看未匹配的键</summary><p>{preview.join.unmatched_examples.map(value=>value==null?"（空值）":String(value)).join(" · ")}</p></details>}
              {preview.blocked ? (
                <div role="alert">
                  <p>
                    {copy(
                      "请选择重复键的保留规则。",
                      "Choose which duplicate row to keep.",
                    )}
                  </p>
                  <ul>
                    {preview.join?.duplicate_examples.map((row, i) => (
                      <li key={i}>
                        {Object.entries(row)
                          .map(
                            ([key, value]) =>
                              `${key === "__right_row__" ? copy("右表行", "Right row") : key}: ${value}`,
                          )
                          .join(" · ")}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <>
                  <p>
                    {copy(
                      `影响 ${preview.affected_rows} 行 · ${preview.before_rows} → ${preview.after_rows} 行 · 删除 ${preview.removed_rows} 行`,
                      `${preview.affected_rows} rows affected · ${preview.before_rows} → ${preview.after_rows} rows · ${preview.removed_rows} removed`,
                    )}
                  </p>
                  {preview.notes.map((note) => (
                    <p key={note}>{note}</p>
                  ))}
                  {preview.schema_changes?.map((change) => (
                    <p key={change.column}>
                      {change.column} · {change.before} → {change.after}
                    </p>
                  ))}
                  {!!preview.changes.length && (
                    <div className="data-table-scroll">
                      <table className="data-preview-table">
                        <caption>
                          {copy(
                            "变化示例（最多 20 处）",
                            "Changes (up to 20 examples)",
                          )}
                        </caption>
                        <thead>
                          <tr>
                            <th>{copy("行 / 字段", "Row / column")}</th>
                            <th>{copy("修改前", "Before")}</th>
                            <th>{copy("修改后", "After")}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {preview.changes.map((change, i) => (
                            <tr key={i}>
                              <td>
                                {change.row} · {change.column}
                              </td>
                              <td>{String(change.before ?? "—")}</td>
                              <td className="preparation-changed">
                                {String(change.after ?? "—")}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}
            </section>
          )}
          {error && (
            <p role="alert" className="text-destructive">
              {error}
            </p>
          )}
          <div className="preparation-dialog-actions">
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setOperation(null)}
            >
              {copy("取消", "Cancel")}
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void inspect()}
            >
              {busy
                ? copy("处理中…", "Working…")
                : copy("预览变化", "Preview changes")}
            </Button>
            <Button
              disabled={busy || !preview?.result_sha256 || preview.blocked}
              onClick={() => void apply()}
            >
              {copy("应用", "Apply")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </aside>
  );
}
