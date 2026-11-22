"use client";

import { Message as UiMessage } from "@/components/message";
import { MessageFromSpec } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { PanelResizeHandle } from "./panel-resize-handle";
import { useLanguage } from "@/providers/language-provider";
import type { PreviewPublication } from "@/lib/preview-types";
import { PreviewTurnPanel } from "./preview-turn-panel";
import { FormalResearch } from "./formal-research";
import { DraftAdvicePanel } from "./draft-advice-panel";
import { Button } from "./ui/button";
import { MaterialPicker } from "./material-picker";
import type { CitationReference } from "@/lib/material-types";

type Published = {
  value: PreviewPublication;
  revision: number;
  key: string;
  owner: symbol | null;
};
const Workspace = createContext<{
  publish: (owner: symbol, value: PreviewPublication) => void;
  clear: (owner: symbol) => void;
  chooseCitation: (ref: CitationReference) => void;
} | null>(null);

/** Remounted by project + route, so no object or permission follows another page. */
export function PreviewWorkspace({
  projectId,
  route,
  children,
}: {
  projectId: string;
  route: string;
  children: React.ReactNode;
}) {
  const initial: PreviewPublication = {
    scope: { project_id: projectId },
    page: "项目",
    draft: "页面切换后重新选择对象；旧列和结果引用已清除。",
    pageMessage: { id: "ui.dashboard.projects" },
    draftMessage: { id: "preview.context.routeReset" },
  };
  const [published, setPublished] = useState<Published>({
    value: initial,
    revision: 0,
    key: "",
    owner: null,
  });
  const [open, setOpen] = useState(false);
  const [panelWidth, setPanelWidth] = useState(340);
  const { locale } = useLanguage();
  const [materials, setMaterials] = useState<{
    refs: CitationReference[];
    revision: number;
  }>({ refs: [], revision: 0 });
  const changeMaterials = useCallback((refs: CitationReference[]) => {
    setMaterials((old) =>
      JSON.stringify(old.refs) === JSON.stringify(refs)
        ? old
        : { refs, revision: old.revision + 1 },
    );
  }, []);
  const chooseCitation = useCallback((ref: CitationReference) => {
    setMaterials((old) => {
      const refs = [
        ...old.refs.filter((r) => r.citation_id !== ref.citation_id),
        ref,
      ].slice(-5);
      return JSON.stringify(old.refs) === JSON.stringify(refs)
        ? old
        : { refs, revision: old.revision + 1 };
    });
  }, []);
  const publish = useCallback(
    (owner: symbol, value: PreviewPublication) => {
      if (value.scope.project_id !== projectId) return;
      const key = JSON.stringify({
        scope: value.scope,
        page: value.page,
        draft: value.draft,
        pageMessage: value.pageMessage,
        draftMessage: value.draftMessage,
        adviceKind: value.adviceKind,
        adviceDraftId: value.adviceDraftId,
        adviceQuestion: value.adviceQuestion,
      });
      setPublished((old) =>
        old.key === key && old.owner === owner
          ? old
          : {
              value,
              owner,
              key,
              revision: old.revision + 1,
            },
      );
    },
    [projectId],
  );
  const clear = useCallback(
    (owner: symbol) => {
      setPublished((old) =>
        old.owner === owner
          ? {
              value: {
                scope: { project_id: projectId },
                page: "项目",
                draft: "旧对象上下文已清除，请重新选择。",
                pageMessage: { id: "ui.dashboard.projects" },
                draftMessage: { id: "preview.context.objectReset" },
              },
              owner: null,
              key: "",
              revision: old.revision + 1,
            }
          : old,
      );
    },
    [projectId],
  );
  const identity = `${projectId}:${route}:${published.revision}:${materials.revision}`;
  return (
    <Workspace.Provider value={{ publish, clear, chooseCitation }}>
      <div
        className="project-ai-layout"
        style={{ "--inspector-width": `${panelWidth}px` } as CSSProperties}
      >
        <div className="project-ai-toolbar">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
          >
            {locale === "zh-CN"
              ? open
                ? "收起 AI 助手"
                : "AI 助手"
              : open
                ? "Close AI assistant"
                : "AI assistant"}
          </Button>
        </div>
        <div className={`project-ai-panels ${open ? "has-inspector" : ""}`}>
          <div className="min-w-0">{children}</div>
          {open && (
            <PanelResizeHandle
              width={panelWidth}
              onResize={setPanelWidth}
              onCommit={setPanelWidth}
            />
          )}
          <UiAttributes
            messages={{
              "aria-label": { id: "ui.workspace.projectAiWorkbench" },
            }}
          >
            <aside
              aria-label={"项目 AI 工作台"}

              className="project-ai-aside min-w-0"
              hidden={!open}
            >
              <div className="mb-3 flex items-center justify-between gap-2">
                <h2 className="font-semibold">
                  <UiMessage id="ui.workspace.aiResearchWorkbench" />
                </h2>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setOpen(!open)}
                  aria-expanded={open}
                >
                  {open ? (
                    <UiMessage id="ui.displayBranch.workspace.collapse" />
                  ) : (
                    <UiMessage id="ui.displayBranch.workspace.expand" />
                  )}
                </Button>
              </div>
              {
                <>
                  <p className="mb-2 text-sm text-muted-foreground">
                    {" "}
                    <UiMessage id="ui.workspace.current" />
                    {published.value.pageMessage ? (
                      <MessageFromSpec {...published.value.pageMessage} />
                    ) : (
                      published.value.page
                    )}{" "}
                  </p>
                  {published.value.adviceKind ? (
                    <DraftAdvicePanel
                      key={`${projectId}:${published.value.adviceKind}:${published.value.adviceDraftId ?? "new"}`}
                      projectId={projectId}
                      publication={published.value}
                    />
                  ) : (
                    <>
                      <FormalResearch
                        projectId={projectId}
                        initialDatasetId={
                          published.value.scope.dataset_id ?? undefined
                        }
                        manualSource={
                          published.value.scope.dataset_id &&
                          published.value.scope.dataset_sha256
                            ? {
                                datasetId: published.value.scope.dataset_id,
                                sha256: published.value.scope.dataset_sha256,
                              }
                            : undefined
                        }
                        onApplyStatistics={published.value.applyStatistics}
                      />
                      <details className="research-legacy-tools">
                        <summary>
                          {locale === "zh-CN"
                            ? "材料问答与机器学习建议"
                            : "Material questions and ML suggestions"}
                        </summary>
                        <MaterialPicker
                          projectId={projectId}
                          value={materials.refs}
                          onChange={changeMaterials}
                        />
                      </details>
                      <PreviewTurnPanel
                        key={identity}
                        scope={{
                          ...published.value.scope,
                          material_references: materials.refs,
                        }}
                        identity={identity}
                        onApplyStatistics={published.value.applyStatistics}
                        onApplyML={published.value.applyML}
                      />
                    </>
                  )}
                </>
              }
            </aside>
          </UiAttributes>
        </div>
      </div>
    </Workspace.Provider>
  );
}

export function usePreviewCitationSelection() {
  return useContext(Workspace)?.chooseCitation;
}

/** Safe no-op when a legacy page test renders children without ProjectShell. */
export function usePreviewPublication(value: PreviewPublication) {
  const workspace = useContext(Workspace);
  const owner = useRef(Symbol("preview-page"));
  const current = useRef(value);
  current.current = value;
  const key = JSON.stringify({
    scope: value.scope,
    page: value.page,
    draft: value.draft,
    pageMessage: value.pageMessage,
    draftMessage: value.draftMessage,
    adviceKind: value.adviceKind,
    adviceDraftId: value.adviceDraftId,
    adviceQuestion: value.adviceQuestion,
  });
  const publish = workspace?.publish;
  const clear = workspace?.clear;
  useEffect(() => {
    const token = owner.current;
    publish?.(token, {
      ...current.current,
      saveAdviceQuestion: (question) =>
        current.current.saveAdviceQuestion?.(question) ?? Promise.resolve(),
      prepareAdvice: current.current.prepareAdvice
        ? () => current.current.prepareAdvice!()
        : undefined,
      adviceApplied: () =>
        current.current.adviceApplied?.() ?? Promise.resolve(),
      applyStatistics: current.current.applyStatistics
        ? (spec) => current.current.applyStatistics?.(spec)
        : undefined,
      applyML: current.current.applyML
        ? (spec) => current.current.applyML?.(spec)
        : undefined,
    });
    return () => clear?.(token);
  }, [key, publish, clear]);
}

/** Use inside ProjectShell so the page publishes to its actual workspace. */
export function PreviewPageContext({ value }: { value: PreviewPublication }) {
  usePreviewPublication(value);
  return null;
}
