"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { navigateToAppRoute } from "@/lib/desktop-navigation";
import {
  FileTextIcon,
  PlusIcon,
  Undo2Icon,
  SearchIcon,
  XIcon,
  Settings2Icon,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api-client";
import {
  codingApi,
  type CodingState,
  type CodingChange,
  type CodingTheme,
  type ThemeColor,
  type TextCodedSegment as CodedSegment,
  isTextCodedSegment,
} from "@/lib/text-coding";
import { materialApi } from "@/lib/material-api";
import {
  codepointOffset,
  type Material,
  type TextPage,
  type Location,
} from "@/lib/material-types";
import { registerProjectEditor } from "@/lib/project-save";
import type { PreviewPublication } from "@/lib/preview-types";
import { useLanguage } from "@/providers/language-provider";
import { AuthGuard } from "./auth-guard";
import { ProjectShell } from "./project-shell";
import { useResultProjectId } from "./result-source";
import { PreviewPageContext } from "./preview-workspace";
import { Button } from "./ui/button";
import { DownloadButton } from "./download-button";
import { MaterialSourceUpdate } from "./material-source-update";
import { DesktopLink } from "./desktop-link";

type TextLocation = Extract<Location, { kind: "text" }>;
const emptyTheme = {
  name: "",
  color: "green" as ThemeColor,
  parent_id: null as string | null,
  memo: "",
};

export function TextCodingWorkbench({ projectId }: { projectId: string }) {
  const id = useResultProjectId(projectId);
  return id ? (
    <AuthGuard>
      <CodingBody key={id} projectId={id} />
    </AuthGuard>
  ) : (
    <p role="alert">项目不存在</p>
  );
}

function CodingBody({ projectId }: { projectId: string }) {
  const { locale } = useLanguage();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  const params = useSearchParams();
  const router = useRouter();
  const flushDraft = useRef<() => Promise<void>>(async () => {});
  const query = useQueryClient();
  const [state, setState] = useState<CodingState | null>(null);
  const saved = useRef<CodingState | null>(null);
  const pending = useRef<Promise<unknown> | null>(null);
  const alive = useRef(true);
  const [materials, setMaterials] = useState<Material[]>([]);
  const [materialFilter, setMaterialFilter] = useState("");
  const [materialId, setMaterialId] = useState(params.get("materialId") || "");
  const [pages, setPages] = useState<TextPage[]>([]);
  const [textMaterialId, setTextMaterialId] = useState("");
  const [textReload, setTextReload] = useState(0);
  const [pageNumber, setPageNumber] = useState(1);
  const [filter, setFilter] = useState("");
  const [textSearch, setTextSearch] = useState("");
  const [searchFrom, setSearchFrom] = useState(0);
  const [themeId, setThemeId] = useState("");
  const [selection, setSelection] = useState<TextLocation | null>(null);
  const [focused, setFocused] = useState<CodedSegment | null>(null);
  const [memo, setMemo] = useState("");
  const [manageId, setManageId] = useState<string | null>(null);
  const [themeForm, setThemeForm] = useState(emptyTheme);
  const [mergeTarget, setMergeTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loadingText, setLoadingText] = useState(false);
  const textRef = useRef<HTMLDivElement>(null);
  const upload = useRef<HTMLInputElement>(null);
  const doc = state?.payload;
  const material = materials.find((item) => item.id === materialId);
  const versionedRoots = new Set(materials.flatMap((item) =>
    item.metadata.file_version ? [item.metadata.file_version.root_id] : [],
  ));
  const visibleMaterials = materials.filter((item) =>
    `${item.filename} v${item.version}`.toLocaleLowerCase().includes(materialFilter.trim().toLocaleLowerCase()),
  ).sort((a, b) => a.filename.localeCompare(b.filename, locale, { numeric: true }) ||
    a.version - b.version || a.id.localeCompare(b.id));
  const currentPage = pages.find((item) => item.page === pageNumber);
  const themes = doc?.themes || [];
  const segments = doc?.segments.filter(isTextCodedSegment) || [];
  const activeTheme = themes.find((item) => item.id === themeId);
  const visibleSegments = segments.filter(
    (item) =>
      (!themeId || item.theme_ids.includes(themeId)) &&
      (!filter ||
        `${item.filename} ${item.locator.quote} ${item.memo}`
          .toLocaleLowerCase()
          .includes(filter.toLocaleLowerCase())),
  );
  const publication: PreviewPublication = {
    page: "文本编码",
    scope: { project_id: projectId },
    draft: material
      ? `${material.filename}${selection ? ` · ${selection.quote}` : ""}`
      : "",
    adviceKind: "coding",
    adviceQuestion: doc?.ai_question ?? "",
    saveAdviceQuestion: async (question) => {
      await pending.current;
      if (saved.current?.payload.ai_question === question) return;
      const actual = await change({ operation: "question", question });
      if (!actual || actual.payload.ai_question !== question)
        throw new Error(
          copy("问题未保存，请重试。", "Question was not saved. Try again."),
        );
    },
    prepareAdvice: async () => {
      await flushDraft.current();
      if (!material || !selection)
        throw new Error(
          copy(
            "请先在原文中选择一段文字。",
            "Select a passage in the original text first.",
          ),
        );
      const actual = await codingApi.read(projectId);
      return {
        kind: "coding",
        revision: actual.revision,
        selections: [
          {
            material_id: material.id,
            material_sha256: material.sha256,
            material_version: material.version,
            locator: selection,
          },
        ],
      };
    },
    adviceApplied: async () => {
      const actual = await codingApi.read(projectId);
      saved.current = actual;
      setState(actual);
      setFocused(null);
      setMemo("");
    },
  };
  useEffect(() => {
    alive.current = true;
    const abort = new AbortController();
    void Promise.all([
      codingApi.read(projectId, abort.signal),
      materialApi.list(projectId, abort.signal),
    ])
      .then(([value, files]) => {
        if (abort.signal.aborted) return;
        saved.current = value;
        setState(value);
        setMaterials(files);
        setMaterialId(
          (id) =>
            id ||
            files.find(
              (item) =>
                item.mime_type === "text/plain" ||
                item.mime_type === "application/pdf",
            )?.id ||
            "",
        );
      })
      .catch(() => {
        if (!abort.signal.aborted)
          setError(
            copy(
              "材料暂时无法读取，请刷新。",
              "Materials unavailable. Refresh to retry.",
            ),
          );
      });
    return () => {
      alive.current = false;
      abort.abort();
    };
    // Project identity owns this entire editor instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);
  useEffect(
    () =>
      registerProjectEditor(projectId, async () => {
        await flushDraft.current();
      }),
    [projectId],
  );
  useEffect(() => {
    const abort = new AbortController();
    setPages([]);
    setTextMaterialId("");
    setSelection(null);
    setFocused(null);
    setMemo("");
    if (!materialId) return () => abort.abort();
    setLoadingText(true);
    void materialApi
      .text(materialId, abort.signal)
      .then((value) => {
        if (abort.signal.aborted) return;
        setPages(value.pages);
        setTextMaterialId(materialId);
        setPageNumber(value.pages[0]?.page || 1);
        setLoadingText(false);
      })
      .catch(() => {
        if (!abort.signal.aborted) {
          setError(
            copy(
              "原文读取失败，请重新选择材料。",
              "Cannot read text. Select the material again.",
            ),
          );
          setLoadingText(false);
        }
      });
    return () => abort.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [materialId, textReload]);
  useEffect(() => {
    const id = params.get("materialId");
    if (id) setMaterialId(id);
  }, [params]);
  const handledLocation = useRef("");
  useEffect(() => {
    const citationId = params.get("citationId");
    const textSha = params.get("textSha");
    const urlMaterial = params.get("materialId");
    const key = JSON.stringify([
      urlMaterial,
      citationId,
      textSha,
      params.get("page"),
      params.get("start"),
      params.get("end"),
      params.get("materialSha"),
      params.get("materialVersion"),
    ]);
    if (
      !materialId ||
      !material ||
      textMaterialId !== materialId ||
      !pages.length ||
      handledLocation.current === key ||
      (urlMaterial && urlMaterial !== materialId) ||
      (!citationId && !textSha)
    )
      return;
    if (
      (params.get("materialSha") &&
        params.get("materialSha") !== material.sha256) ||
      (params.get("materialVersion") &&
        Number(params.get("materialVersion")) !== material.version)
    ) {
      handledLocation.current = key;
      setSelection(null);
      setError(
        copy(
          "引用与当前材料不一致，请从成果重新打开。",
          "Reference and current material differ. Open it again from the report.",
        ),
      );
      return;
    }
    const abort = new AbortController();
    const focus = (locator: TextLocation) => {
      handledLocation.current = key;
      setPageNumber(locator.page);
      setSelection(locator);
      const coded = saved.current?.payload.segments.filter(isTextCodedSegment).find(
        (item) =>
          item.material_id === materialId &&
          item.locator.text_sha256 === locator.text_sha256 &&
          item.locator.start === locator.start &&
          item.locator.end === locator.end &&
          item.locator.page === locator.page,
      );
      setFocused(coded || null);
      setMemo(coded?.memo || "");
      if (coded) setThemeId(coded.theme_ids[0]);
    };
    if (textSha) {
      const page = pages.find(
        (item) => item.page === Number(params.get("page")),
      );
      const start = Number(params.get("start")),
        end = Number(params.get("end"));
      if (
        !page ||
        page.sha256 !== textSha ||
        !Number.isInteger(start) ||
        !Number.isInteger(end) ||
        start < 0 ||
        end <= start ||
        end > Array.from(page.text).length ||
        end - start > 2000
      ) {
        handledLocation.current = key;
        setError(
          copy(
            "引用与当前文本不一致，请核对原材料。",
            "Reference and current text differ. Check the original material.",
          ),
        );
        return;
      }
      focus({
        kind: "text",
        page: page.page,
        start,
        end,
        text_sha256: page.sha256,
        quote: Array.from(page.text).slice(start, end).join(""),
      });
    } else if (citationId) {
      void materialApi
        .citation(materialId, citationId, abort.signal)
        .then((value) => {
          if (!abort.signal.aborted && value.locator.kind === "text")
            focus(value.locator);
        })
        .catch(() => {
          if (!abort.signal.aborted) {
            handledLocation.current = key;
            setError(
              copy("引用暂时无法定位。", "This reference cannot be located."),
            );
          }
        });
    }
    return () => abort.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [materialId, material, textMaterialId, pages, params]);
  async function change(command: CodingChange) {
    if (pending.current || !saved.current) return;
    setBusy(true);
    setError("");
    const work = codingApi.change(projectId, saved.current.revision, command);
    pending.current = work;
    try {
      const next = await work;
      saved.current = next;
      if (alive.current) {
        setState(next);
        await query.invalidateQueries({
          queryKey: ["workspace-records", projectId],
        });
      }
      return next;
    } catch (reason) {
      if (alive.current)
        setError(
          reason instanceof ApiError
            ? reason.detail
            : copy("未保存，请重试。", "Not saved. Try again."),
        );
      throw reason;
    } finally {
      pending.current = null;
      if (alive.current) setBusy(false);
    }
  }
  const perform = (command: CodingChange) => {
    void change(command).catch(() => {});
  };
  const originalTheme = themes.find((item) => item.id === manageId);
  const themeDirty =
    manageId !== null &&
    JSON.stringify(themeForm) !==
      JSON.stringify(
        originalTheme
          ? {
              name: originalTheme.name,
              color: originalTheme.color,
              parent_id: originalTheme.parent_id,
              memo: originalTheme.memo,
            }
          : emptyTheme,
      );
  const memoDirty = focused ? memo !== focused.memo : !!selection && !!memo;
  const dirty = themeDirty || memoDirty;
  flushDraft.current = async () => {
    await pending.current;
    if (themeDirty && manageId !== null) {
      if (!themeForm.name.trim())
        throw new Error(
          copy("请先填写主题名称。", "Enter a theme name first."),
        );
      await change({
        operation: manageId === "new" ? "add_theme" : "edit_theme",
        ...(manageId !== "new" ? { theme_id: manageId } : {}),
        ...themeForm,
      });
      if (alive.current) setManageId(null);
    }
    if (memoDirty) {
      if (focused) {
        await change({
          operation: "edit_segment",
          segment_id: focused.id,
          theme_ids: focused.theme_ids,
          memo,
        });
        if (alive.current) setFocused({ ...focused, memo });
      } else {
        throw new Error(
          copy(
            "请先将选段加入主题，或清除选段。",
            "Code or clear the selected excerpt first.",
          ),
        );
      }
    }
  };
  useEffect(() => {
    if (!dirty) return;
    const unload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    const leave = (event: MouseEvent) => {
      const link =
        event.target instanceof Element
          ? event.target.closest("a[href]")
          : null;
      if (
        !(link instanceof HTMLAnchorElement) ||
        link.target ||
        link.download ||
        event.button !== 0 ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        event.altKey
      )
        return;
      const destination = new URL(link.href);
      if (
        destination.origin !== window.location.origin ||
        destination.href === window.location.href
      )
        return;
      event.preventDefault();
      event.stopPropagation();
      void flushDraft
        .current()
        .then(() =>
          navigateToAppRoute(
            router,
            destination.pathname + destination.search + destination.hash,
          ),
        )
        .catch((reason) =>
          setError(
            reason instanceof Error
              ? reason.message
              : copy("未保存，请重试。", "Not saved. Try again."),
          ),
        );
    };
    window.addEventListener("beforeunload", unload);
    document.addEventListener("click", leave, true);
    return () => {
      window.removeEventListener("beforeunload", unload);
      document.removeEventListener("click", leave, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dirty, router, locale]);
  function selectText() {
    if (busy) return;
    if (dirty) {
      setError(
        copy(
          "请先保存当前编辑，再选择新段落。",
          "Save the current edit before selecting another passage.",
        ),
      );
      return;
    }
    const range = window.getSelection();
    const host = textRef.current;
    if (!host || !currentPage || !range?.rangeCount || range.isCollapsed)
      return;
    const selected = range.getRangeAt(0);
    if (
      !host.contains(selected.startContainer) ||
      !host.contains(selected.endContainer)
    )
      return;
    const prefix = selected.cloneRange();
    prefix.selectNodeContents(host);
    prefix.setEnd(selected.startContainer, selected.startOffset);
    const start = codepointOffset(currentPage.text, prefix.toString().length);
    const end = codepointOffset(
      currentPage.text,
      prefix.toString().length + selected.toString().length,
    );
    if (start === null || end === null || end <= start) return;
    if (end - start > 2000) {
      setError(
        copy(
          "每次请选择 2000 字以内的原文。",
          "Select up to 2,000 characters.",
        ),
      );
      return;
    }
    setSelection({
      kind: "text",
      page: currentPage.page,
      start,
      end,
      text_sha256: currentPage.sha256,
      quote: Array.from(currentPage.text).slice(start, end).join(""),
    });
    setFocused(null);
    setMemo("");
    setError("");
  }
  function locate(segment: CodedSegment) {
    setMaterialId(segment.material_id);
    setPageNumber(segment.locator.page);
    setSelection(segment.locator);
    setFocused(segment);
    setMemo(segment.memo);
  }
  // Selecting a different material completes asynchronously; keep a requested source.
  const requested = useRef<CodedSegment | null>(null);
  function jump(segment: CodedSegment) {
    void flushDraft
      .current()
      .then(() => {
        requested.current = segment;
        locate(segment);
      })
      .catch((reason) =>
        setError(reason instanceof Error ? reason.message : "未保存"),
      );
  }
  useEffect(() => {
    const value = requested.current;
    if (
      value &&
      value.material_id === materialId &&
      pages.some((item) => item.page === value.locator.page)
    ) {
      setPageNumber(value.locator.page);
      setSelection(value.locator);
      setFocused(value);
      setMemo(value.memo);
      requested.current = null;
    }
  }, [pages, materialId]);
  useEffect(() => {
    document
      .getElementById("coding-focus")
      ?.scrollIntoView({ block: "center" });
  }, [selection, currentPage]);
  function editTheme(theme?: CodingTheme) {
    setManageId(theme?.id || "new");
    setThemeForm(
      theme
        ? {
            name: theme.name,
            color: theme.color,
            parent_id: theme.parent_id,
            memo: theme.memo,
          }
        : { ...emptyTheme },
    );
    setMergeTarget("");
  }
  const orderedThemes: (CodingTheme & { depth: number })[] = [];
  function themeBranch(parent: string | null, depth: number) {
    for (const item of themes.filter((theme) => theme.parent_id === parent)) {
      orderedThemes.push({ ...item, depth });
      themeBranch(item.id, depth + 1);
    }
  }
  themeBranch(null, 0);
  function themePath(theme: CodingTheme) {
    const names = [theme.name];
    let parent = theme.parent_id;
    while (parent) {
      const item = themes.find((value) => value.id === parent);
      if (!item) break;
      names.unshift(item.name);
      parent = item.parent_id;
    }
    return names.join(" / ");
  }
  const themeList = (
    <>
      <div className="coding-side-heading">
        <strong>{copy("材料", "Materials")}</strong>
        <Button
          variant="ghost"
          size="sm"
          aria-label={copy("导入材料", "Import material")}
          disabled={busy}
          onClick={() => upload.current?.click()}
        >
          <PlusIcon className="size-4" />
        </Button>
      </div>
      <input
        disabled={busy}
        ref={upload}
        hidden
        type="file"
        accept=".txt,.md,.pdf,.png,.jpg,.jpeg,.gif,.webp,.wav,.mp4"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (!file || busy) return;
          setBusy(true);
          setError("");
          const task = materialApi.upload(projectId, file);
          pending.current = task;
          void task
            .then((value) => {
              if (alive.current) {
                setMaterials((items) => [...items, value]);
                setMaterialId(value.id);
              }
            })
            .catch(() => {
              if (alive.current)
                setError(
                  copy(
                    "材料未导入，请检查文件。",
                    "Material not imported. Check the file.",
                  ),
                );
            })
            .finally(() => {
              pending.current = null;
              if (alive.current) setBusy(false);
            });
        }}
      />
      <label className="coding-search coding-material-search">
        <SearchIcon className="size-4" />
        <input
          aria-label={copy("查找材料", "Find material")}
          placeholder={copy("查找材料", "Find material")}
          value={materialFilter}
          onChange={(event) => setMaterialFilter(event.target.value)}
        />
      </label>
      <div className="coding-file-list">
        {!visibleMaterials.length && materials.length > 0 && (
          <p className="text-sm text-muted-foreground">{copy("未找到材料", "No matching material")}</p>
        )}
        {visibleMaterials.map((file) => (
          <button
            key={file.id}
            className={file.id === materialId ? "is-active" : ""}
            disabled={busy}
            onClick={() => {
              void flushDraft
                .current()
                .then(() => {
                  requested.current = null;
                  setMaterialId(file.id);
                })
                .catch((reason) =>
                  setError(reason instanceof Error ? reason.message : "未保存"),
                );
            }}
          >
            <FileTextIcon className="size-4" />
            <span>{file.filename}{(file.version > 1 || versionedRoots.has(file.id)) && ` · v${file.version}`}</span>
          </button>
        ))}
      </div>
      <div className="coding-side-heading">
        <strong>{copy("主题", "Themes")}</strong>
        <Button
          variant="ghost"
          size="sm"
          aria-label={copy("新建主题", "New theme")}
          disabled={busy || !state}
          onClick={() => editTheme()}
        >
          <PlusIcon className="size-4" />
        </Button>
      </div>
      <button
        className={`coding-theme ${!themeId ? "is-active" : ""}`}
        onClick={() => setThemeId("")}
      >
        {copy("全部编码", "All coding")}
        <span>{segments.length}</span>
      </button>
      {orderedThemes.map((theme) => (
        <div key={theme.id} className="coding-theme-row">
          <button
            className={`coding-theme ${themeId === theme.id ? "is-active" : ""}`}
            onClick={() => setThemeId(theme.id)}
            title={themePath(theme)}
            style={{ paddingInlineStart: Math.min(10 + theme.depth * 14, 72) }}
          >
            <i data-tone={theme.color} style={theme.color_rgb ? { background: theme.color_rgb } : undefined} />
            <span>{theme.name}</span>
            <small>
              {
                segments.filter((segment) =>
                  segment.theme_ids.includes(theme.id),
                ).length
              }
            </small>
          </button>
          <Button
            variant="ghost"
            size="sm"
            aria-label={copy(
              `编辑主题 ${theme.name}`,
              `Edit theme ${theme.name}`,
            )}
            disabled={busy}
            onClick={() => editTheme(theme)}
          >
            <Settings2Icon className="size-3.5" />
          </Button>
        </div>
      ))}
      <DesktopLink
        className="coding-legacy"
        href={`/projects/${projectId}/materials?view=locations${materialId ? `&materialId=${materialId}` : ""}`}
      >
        {copy("原材料与位置标注", "Originals and locations")}
      </DesktopLink>
    </>
  );
  const validSelection =
    selection &&
    currentPage &&
    selection.page === currentPage.page &&
    selection.text_sha256 === currentPage.sha256;
  const letters = Array.from(currentPage?.text || "");
  const pageSegments = segments.filter(
    (item) =>
      item.material_id === materialId &&
      item.locator.page === pageNumber &&
      (!themeId || item.theme_ids.includes(themeId)),
  );
  const boundaries = new Set([0, letters.length]);
  pageSegments.forEach((item) => {
    boundaries.add(item.locator.start);
    boundaries.add(item.locator.end);
  });
  if (validSelection) {
    boundaries.add(selection.start);
    boundaries.add(selection.end);
  }
  const positions = [...boundaries]
    .filter((value) => value >= 0 && value <= letters.length)
    .sort((a, b) => a - b);
  const found = textSearch
    ? (currentPage?.text
        .toLocaleLowerCase()
        .indexOf(textSearch.toLocaleLowerCase(), searchFrom) ?? -1)
    : -1;
  const matchedSearch =
    found < 0 && searchFrom > 0 && textSearch
      ? (currentPage?.text
          .toLocaleLowerCase()
          .indexOf(textSearch.toLocaleLowerCase()) ?? -1)
      : found;
  const searchStart =
    currentPage && matchedSearch >= 0
      ? codepointOffset(currentPage.text, matchedSearch)
      : null;
  const searchEnd =
    currentPage && matchedSearch >= 0
      ? codepointOffset(currentPage.text, matchedSearch + textSearch.length)
      : null;
  useEffect(() => {
    document
      .getElementById("coding-search-match")
      ?.scrollIntoView({ block: "center" });
  }, [matchedSearch, textSearch]);
  if (matchedSearch >= 0 && currentPage) {
    const start = codepointOffset(currentPage.text, matchedSearch);
    const end = codepointOffset(
      currentPage.text,
      matchedSearch + textSearch.length,
    );
    if (start !== null && end !== null) {
      boundaries.add(start);
      boundaries.add(end);
    }
  }
  const finalPositions =
    matchedSearch >= 0 ? [...boundaries].sort((a, b) => a - b) : positions;
  return (
    <ProjectShell
      projectId={projectId}
      explorerContent={themeList}
      explorerSummary={copy(
        `${materials.length} 份材料`,
        `${materials.length} materials`,
      )}
    >
      <PreviewPageContext value={publication} />
      <section
        className="text-coding-workbench"
        aria-label={copy("文本编码", "Text coding")}
      >
        <header className="coding-toolbar">
          <h2>{copy("文本编码", "Text coding")}</h2>
          <div>
            <Button
              variant="ghost"
              disabled={busy || dirty || !doc?.undo_stack.length}
              onClick={() => {
                setFocused(null);
                perform({ operation: "undo" });
              }}
            >
              <Undo2Icon className="size-4" />
              {copy("撤销", "Undo")}
            </Button>
            <DownloadButton
              identity={`${projectId}:${state?.revision}:csv`}
              download={async () => {
                await pending.current;
                return codingApi.export(projectId, "csv");
              }}
            >
              {copy("导出编码", "Export coding")}
            </DownloadButton>
            <DownloadButton
              identity={`${projectId}:${state?.revision}:json`}
              download={async () => {
                await pending.current;
                return codingApi.export(projectId, "json");
              }}
            >
              JSON
            </DownloadButton>
          </div>
        </header>
        {error && (
          <p className="coding-error" role="alert">
            {error}
          </p>
        )}
        {manageId !== null && (
          <section
            className="coding-manage"
            aria-label={copy("编辑主题", "Edit theme")}
          >
            <div className="coding-side-heading">
              <strong>
                {manageId === "new"
                  ? copy("新建主题", "New theme")
                  : copy("编辑主题", "Edit theme")}
              </strong>
              <Button
                variant="ghost"
                size="sm"
                aria-label={copy("关闭主题编辑", "Close theme editor")}
                onClick={() => setManageId(null)}
              >
                <XIcon className="size-4" />
              </Button>
            </div>
            <div className="coding-theme-fields">
              <label>
                {copy("名称", "Name")}
                <input
                  disabled={busy}
                  aria-label={copy("主题名称", "Theme name")}
                  maxLength={80}
                  value={themeForm.name}
                  onChange={(event) =>
                    setThemeForm({ ...themeForm, name: event.target.value })
                  }
                />
              </label>
              <label>
                {copy("颜色", "Color")}
                <SearchSelect
                  disabled={busy}
                  value={themeForm.color}
                  onChange={(event) =>
                    setThemeForm({
                      ...themeForm,
                      color: event.target.value as ThemeColor,
                    })
                  }
                >
                  {(
                    [
                      ["green", "绿", "Green"],
                      ["blue", "蓝", "Blue"],
                      ["amber", "黄", "Amber"],
                      ["purple", "紫", "Purple"],
                      ["rose", "粉", "Rose"],
                      ["neutral", "灰", "Neutral"],
                    ] as const
                  ).map(([value, zh, en]) => (
                    <option key={value} value={value}>
                      {copy(zh, en)}
                    </option>
                  ))}
                </SearchSelect>
              </label>
              <label>
                {copy("上级主题", "Parent theme")}
                <SearchSelect
                  disabled={busy}
                  value={themeForm.parent_id || ""}
                  onChange={(event) =>
                    setThemeForm({
                      ...themeForm,
                      parent_id: event.target.value || null,
                    })
                  }
                >
                  <option value="">{copy("无", "None")}</option>
                  {themes
                    .filter((item) => item.id !== manageId)
                    .map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                </SearchSelect>
              </label>
              <label>
                {copy("说明", "Memo")}
                <input
                  disabled={busy}
                  maxLength={2000}
                  value={themeForm.memo}
                  onChange={(event) =>
                    setThemeForm({ ...themeForm, memo: event.target.value })
                  }
                />
              </label>
            </div>
            <div className="coding-inline-actions">
              <Button
                disabled={busy || !themeForm.name.trim()}
                onClick={() => {
                  void change({
                    operation: manageId === "new" ? "add_theme" : "edit_theme",
                    ...(manageId !== "new" ? { theme_id: manageId } : {}),
                    ...themeForm,
                  })
                    .then(() => setManageId(null))
                    .catch(() => {});
                }}
              >
                {copy("保存主题", "Save theme")}
              </Button>
              {manageId !== "new" && (
                <>
                  <SearchSelect
                    disabled={busy}
                    aria-label={copy("合并到主题", "Merge into theme")}
                    value={mergeTarget}
                    onChange={(event) => setMergeTarget(event.target.value)}
                  >
                    <option value="">{copy("合并到…", "Merge into…")}</option>
                    {themes
                      .filter((item) => item.id !== manageId)
                      .map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name}
                        </option>
                      ))}
                  </SearchSelect>
                  <Button
                    variant="outline"
                    disabled={busy || !mergeTarget}
                    onClick={() => {
                      void change({
                        operation: "merge_theme",
                        theme_id: manageId,
                        target_id: mergeTarget,
                      })
                        .then(() => {
                          setManageId(null);
                          setThemeId(mergeTarget);
                        })
                        .catch(() => {});
                    }}
                  >
                    {copy("合并", "Merge")}
                  </Button>
                  <Button
                    variant="ghost"
                    disabled={busy}
                    onClick={() => {
                      void change({
                        operation: "delete_theme",
                        theme_id: manageId,
                      })
                        .then(() => {
                          setManageId(null);
                          setThemeId("");
                        })
                        .catch(() => {});
                    }}
                  >
                    {copy("删除空主题", "Delete empty theme")}
                  </Button>
                </>
              )}
            </div>
          </section>
        )}
        <div className="coding-reading-layout">
          <article className="coding-document">
            <header>
              <h3>
                {material?.filename ||
                  copy("选择一份材料", "Choose a material")}
              </h3>
              <div>
                {pages.length > 1 && (
                  <SearchSelect
                    disabled={busy}
                    aria-label={copy("页码", "Page")}
                    value={pageNumber}
                    onChange={(event) => {
                      const target = Number(event.target.value);
                      void flushDraft
                        .current()
                        .then(() => {
                          setPageNumber(target);
                          setSelection(null);
                          setFocused(null);
                          setMemo("");
                        })
                        .catch((reason) =>
                          setError(
                            reason instanceof Error ? reason.message : "未保存",
                          ),
                        );
                    }}
                  >
                    {pages.map((page) => (
                      <option key={page.page} value={page.page}>
                        {copy(`第 ${page.page} 页`, `Page ${page.page}`)}
                      </option>
                    ))}
                  </SearchSelect>
                )}
                <label className="coding-search">
                  <SearchIcon className="size-4" />
                  <input
                    disabled={busy}
                    aria-label={copy("查找原文", "Find in text")}
                    placeholder={copy("查找原文", "Find in text")}
                    value={textSearch}
                    onChange={(event) => {
                      setTextSearch(event.target.value);
                      setSearchFrom(0);
                    }}
                  />
                </label>
                {textSearch && (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={matchedSearch < 0}
                    onClick={() =>
                      setSearchFrom(
                        matchedSearch + Math.max(1, textSearch.length),
                      )
                    }
                  >
                    {matchedSearch < 0
                      ? copy("未找到", "No match")
                      : copy("下一处", "Next match")}
                  </Button>
                )}
              </div>
              {material && ["text/plain", "application/pdf"].includes(material.mime_type) && (
                <MaterialSourceUpdate
                  key={material.id}
                  material={material}
                  disabled={busy}
                  beforeChange={() => flushDraft.current()}
                  onBusy={(value) => { if (alive.current) setBusy(value); }}
                  onSelect={async (identity) => {
                    const files = await materialApi.list(projectId);
                    if (!alive.current) return;
                    setMaterials(files);
                    requested.current = null;
                    setMaterialId(identity);
                    setTextReload((value) => value + 1);
                    navigateToAppRoute(router, `/projects/${projectId}/materials?materialId=${identity}`);
                  }}
                />
              )}
            </header>
            {loadingText ? (
              <p role="status">{copy("读取原文…", "Loading text…")}</p>
            ) : currentPage ? (
              <div
                className="coding-original"
                ref={textRef}
                tabIndex={0}
                aria-label={copy("材料原文", "Source text")}
                onMouseUp={selectText}
                onKeyUp={selectText}
              >
                {finalPositions.slice(0, -1).map((start, index) => {
                  const end = finalPositions[index + 1];
                  const covered = pageSegments.filter(
                    (item) =>
                      item.locator.start <= start && item.locator.end >= end,
                  );
                  const selected =
                    validSelection &&
                    selection.start <= start &&
                    selection.end >= end;
                  const value = letters.slice(start, end).join("");
                  const searching =
                    searchStart !== null &&
                    searchEnd !== null &&
                    start >= searchStart &&
                    end <= searchEnd;
                  return covered.length || selected || searching ? (
                    <mark
                      id={
                        selected && start === selection.start
                          ? "coding-focus"
                          : undefined
                      }
                      key={start}
                      data-search={searching || undefined}
                      data-selected={selected || undefined}
                      data-tone={
                        themes.find((theme) =>
                          covered[0]?.theme_ids.includes(theme.id),
                        )?.color || "green"
                      }
                      title={covered
                        .flatMap((item) =>
                          item.theme_ids.map(
                            (id) =>
                              themes.find((theme) => theme.id === id)?.name,
                          ),
                        )
                        .filter(Boolean)
                        .join(" · ")}
                    >
                      <span
                        id={
                          searching && start === searchStart
                            ? "coding-search-match"
                            : undefined
                        }
                      >
                        {value}
                      </span>
                    </mark>
                  ) : (
                    <span key={start}>{value}</span>
                  );
                })}
              </div>
            ) : (
              <p>
                {material
                  ? copy(
                      "这份材料没有可编码的文本。",
                      "No text is available for coding.",
                    )
                  : copy(
                      "从左侧选择或导入文本、PDF。",
                      "Select or import text or PDF from the file list.",
                    )}
                {material && (
                  <DesktopLink
                    href={`/projects/${projectId}/materials?view=locations&materialId=${materialId}`}
                  >
                    {copy(" 查看原材料", " View original")}
                  </DesktopLink>
                )}
              </p>
            )}
            {validSelection && material && (
              <section
                className="coding-selection"
                aria-label={copy("当前选段", "Selected excerpt")}
              >
                <div className="coding-side-heading">
                  <strong>
                    {focused
                      ? copy("已保存编码", "Saved coding")
                      : copy("选中文字", "Selected text")}
                  </strong>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={copy("取消选段", "Clear selection")}
                    onClick={() => {
                      setSelection(null);
                      setFocused(null);
                    }}
                  >
                    <XIcon className="size-4" />
                  </Button>
                </div>
                <blockquote>{selection.quote}</blockquote>
                <div className="coding-inline-actions">
                  <SearchSelect
                    disabled={busy}
                    aria-label={copy("选段主题", "Excerpt theme")}
                    value={themeId}
                    onChange={(event) => setThemeId(event.target.value)}
                  >
                    <option value="">{copy("选择主题", "Choose theme")}</option>
                    {themes.filter(theme => theme.is_codable !== false).map((theme) => (
                      <option key={theme.id} value={theme.id}>
                        {themePath(theme)}
                      </option>
                    ))}
                  </SearchSelect>
                  <input
                    disabled={busy}
                    aria-label={copy("编码备注", "Coding memo")}
                    placeholder={copy("备注（可选）", "Memo (optional)")}
                    maxLength={2000}
                    value={memo}
                    onChange={(event) => setMemo(event.target.value)}
                  />
                  <Button
                    disabled={!themeId || busy}
                    onClick={() => {
                      void change({
                        operation: "add_segment",
                        theme_ids: [themeId],
                        memo,
                        material_id: material.id,
                        material_sha256: material.sha256,
                        material_version: material.version,
                        locator: selection,
                      })
                        .then(() => {
                          setSelection(null);
                          setFocused(null);
                          setMemo("");
                        })
                        .catch(() => {});
                    }}
                  >
                    {copy("加入主题", "Code excerpt")}
                  </Button>
                  {focused && (
                    <>
                      <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() => {
                          void flushDraft
                            .current()
                            .then(async () => {
                              setBusy(true);
                              const task = codingApi.report(
                                projectId,
                                focused.id,
                                saved.current!.revision,
                              );
                              pending.current = task;
                              try {
                                const report = await task;
                                navigateToAppRoute(
                                  router,
                                  `/projects/${projectId}/results?reportId=${report.id}`,
                                );
                              } finally {
                                pending.current = null;
                                if (alive.current) setBusy(false);
                              }
                            })
                            .catch((reason) =>
                              setError(
                                reason instanceof ApiError
                                  ? reason.detail
                                  : reason instanceof Error
                                    ? reason.message
                                    : "未生成摘录",
                              ),
                            );
                        }}
                      >
                        {copy("生成成果摘录", "Create report excerpt")}
                      </Button>
                      <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() => {
                          void change({
                            operation: "edit_segment",
                            segment_id: focused.id,
                            theme_ids: focused.theme_ids,
                            memo,
                          })
                            .then(() => setFocused({ ...focused, memo }))
                            .catch(() => {});
                        }}
                      >
                        {copy("保存备注", "Save memo")}
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={busy}
                        onClick={() => {
                          void change({
                            operation: "remove_segment",
                            segment_id: focused.id,
                          })
                            .then(() => {
                              setSelection(null);
                              setFocused(null);
                            })
                            .catch(() => {});
                        }}
                      >
                        {copy("移除编码", "Remove coding")}
                      </Button>
                    </>
                  )}
                </div>
              </section>
            )}
          </article>
          <aside className="coding-excerpts">
            <header>
              <h3>
                {activeTheme?.name || copy("编码选段", "Coded excerpts")}
                <small>{visibleSegments.length}</small>
              </h3>
              <input
                disabled={busy}
                type="search"
                aria-label={copy("查找编码", "Find coding")}
                placeholder={copy("查找选段或备注", "Find excerpt or memo")}
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
              />
            </header>
            {activeTheme?.memo && <p>{activeTheme.memo}</p>}
            {visibleSegments.map((segment) => (
              <button
                className={`coding-excerpt ${focused?.id === segment.id ? "is-active" : ""}`}
                key={segment.id}
                onClick={() => jump(segment)}
              >
                <span>
                  {segment.theme_ids
                    .map((id) => themes.find((theme) => theme.id === id)?.name)
                    .filter(Boolean)
                    .join(" · ")}
                </span>
                <blockquote>{segment.locator.quote}</blockquote>
                {segment.memo && <p>{segment.memo}</p>}
                <small>
                  {segment.filename}{(segment.material_version > 1 || versionedRoots.has(segment.material_id)) && ` · v${segment.material_version}`} ·{" "}
                  {copy(
                    `第 ${segment.locator.page} 页`,
                    `Page ${segment.locator.page}`,
                  )}
                </small>
              </button>
            ))}
            {!visibleSegments.length && (
              <p>
                {filter
                  ? copy("没有匹配的编码。", "No matching coding.")
                  : copy(
                      "选中原文后加入主题。",
                      "Select text and assign a theme.",
                    )}
              </p>
            )}
            <DesktopLink
              className="coding-legacy"
              href={`/projects/${projectId}/results`}
            >
              {copy("研究成果", "Research results")}
            </DesktopLink>
          </aside>
        </div>
        <footer className="coding-status" role="status">
          {busy
            ? copy("保存中…", "Saving…")
            : dirty
              ? copy("有未保存的编辑", "Unsaved edits")
              : state
                ? copy(
                    `${segments.length} 个选段 · ${themes.length} 个主题 · 已保存`,
                    `${segments.length} excerpts · ${themes.length} themes · Saved`,
                  )
                : copy("读取编码…", "Loading coding…")}
        </footer>
      </section>
    </ProjectShell>
  );
}
