"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import {
  adviceApi,
  type AdviceRequest,
  type AdviceProposal,
  type AdviceTarget,
  type AdviceContext,
} from "@/lib/draft-advice";
import type { PreviewPublication } from "@/lib/preview-types";
import { researchApi } from "@/lib/research-api";
import { flushProjectEditors, registerProjectEditor } from "@/lib/project-save";
import { navigateToAppRoute } from "@/lib/desktop-navigation";
import { useLanguage } from "@/providers/language-provider";
import { ModelSettingsLink } from "./model-settings-link";
import { ResearchFailure, ResearchMoney } from "./formal-research-common";
import { Button } from "./ui/button";

type Prepared = {
  target: AdviceTarget;
  source_version: string;
  context: AdviceContext;
  question: string;
  requestId: string;
};
export function DraftAdvicePanel({
  projectId,
  publication,
}: {
  projectId: string;
  publication: PreviewPublication;
}) {
  const { locale } = useLanguage();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  const router = useRouter();
  const client = useQueryClient();
  const current = useRef(publication);
  current.current = publication;
  const [question, setQuestion] = useState(publication.adviceQuestion ?? "");
  const questionRef = useRef(question);
  const dirty = useRef(false);
  const writes = useRef(Promise.resolve());
  const [prepared, setPrepared] = useState<Prepared | null>(null);
  const [identity, setIdentity] = useState("");
  const [proposal, setProposal] = useState<AdviceProposal | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const flush = useRef(async () => {});
  flush.current = () => {
    const pending = writes.current
      .catch(() => {})
      .then(async () => {
        if (!dirty.current) return;
        const snapshot = questionRef.current;
        await current.current.saveAdviceQuestion?.(snapshot);
        if (snapshot === questionRef.current) dirty.current = false;
      });
    writes.current = pending;
    return pending;
  };
  useEffect(
    () => registerProjectEditor(projectId, () => flush.current()),
    [projectId],
  );
  useEffect(() => {
    if (!dirty.current) {
      questionRef.current = publication.adviceQuestion ?? "";
      setQuestion(questionRef.current);
    }
  }, [publication.adviceQuestion]);
  useEffect(() => {
    if (!dirty.current) return;
    const timer = setTimeout(() => {
      void flush.current().catch(setError);
    }, 700);
    const unload = (event: BeforeUnloadEvent) => {
      if (dirty.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    const leave = (event: MouseEvent) => {
      const link =
        event.target instanceof Element
          ? event.target.closest("a[href]")
          : null;
      if (
        !dirty.current ||
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
      void flush
        .current()
        .then(() =>
          navigateToAppRoute(
            router,
            destination.pathname + destination.search + destination.hash,
          ),
        )
        .catch(setError);
    };
    window.addEventListener("beforeunload", unload);
    document.addEventListener("click", leave, true);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("beforeunload", unload);
      document.removeEventListener("click", leave, true);
    };
  }, [question, router]);
  const kind = publication.adviceKind!;
  const provider = useQuery({
    queryKey: ["research", "provider"],
    queryFn: ({ signal }) => researchApi.security.provider(signal),
    retry: false,
  });
  const configured = provider.data?.enabled && provider.data?.key_configured;
  const historyKey = [
    "draft-advice",
    projectId,
    kind,
    publication.adviceDraftId,
  ];
  const history = useQuery({
    queryKey: historyKey,
    queryFn: ({ signal }) =>
      adviceApi.recent(projectId, kind, publication.adviceDraftId, signal),
    enabled: kind !== "plot" || !!publication.adviceDraftId,
    retry: false,
  });
  const selected = useQuery({
    queryKey: ["draft-advice-request", projectId, identity],
    queryFn: ({ signal }) => adviceApi.read(projectId, identity, signal),
    enabled: !!identity,
    retry: false,
    refetchInterval: (query) =>
      ["running", "cancel_requested"].includes(query.state.data?.state ?? "")
        ? 1000
        : false,
  });
  const request = selected.data;
  const received = request?.proposal;
  useEffect(() => {
    setProposal(received ? structuredClone(received) : null);
  }, [received]);
  // Selection is explicit: loading old requests never replaces an unsent question.
  async function perform(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (reason) {
      setError(reason);
    } finally {
      setBusy(false);
    }
  }
  function store(value: AdviceRequest) {
    client.setQueryData(["draft-advice-request", projectId, value.id], value);
    setIdentity(value.id);
    void client.invalidateQueries({ queryKey: historyKey });
  }
  async function preview() {
    await flushProjectEditors(projectId);
    const target = await current.current.prepareAdvice!();
    const context = await adviceApi.context(projectId, target);
    const snapshot = {
      target,
      ...context,
      question: questionRef.current.trim(),
      requestId: crypto.randomUUID(),
    };
    setIdentity("");
    setPrepared(snapshot);
    if (configured)
      store(
        await adviceApi.create(
          projectId,
          snapshot.requestId,
          snapshot.question,
          target,
          context.source_version,
        ),
      );
  }
  const context = request?.context ?? prepared?.context;
  const active =
    request && ["running", "cancel_requested"].includes(request.state);
  const labels: Record<string, string> = {
    waiting_approval: copy("待发送", "Ready to send"),
    running: copy("正在生成建议…", "Generating a suggestion…"),
    cancel_requested: copy("正在停止…", "Stopping…"),
    cancelled: copy("已停止", "Stopped"),
    failed: copy("未完成", "Failed"),
    interrupted: copy("已中断", "Interrupted"),
    proposed: copy("建议", "Suggestion"),
    applied: copy("已采用", "Applied"),
  };
  return (
    <section
      className="draft-advice-panel"
      aria-label={copy("当前页面的 AI 建议", "AI suggestions for this page")}
    >
      <div className="advice-model-row">
        <span>
          {configured
            ? provider.data?.settings?.model
            : copy("尚未连接模型", "Connect a model")}
        </span>
        <ModelSettingsLink projectId={projectId} />
      </div>
      {provider.error && (
        <ResearchFailure
          error={provider.error}
          retry={() => {
            void provider.refetch();
          }}
        />
      )}
      {!!history.data?.length && (
        <label>
          {copy("以往建议", "Previous suggestions")}
          <SearchSelect
            value={identity}
            disabled={busy || !!active}
            onChange={(event) => {
              setIdentity(event.target.value);
              setPrepared(null);
              setError(null);
            }}
          >
            <option value="">{copy("新建议", "New suggestion")}</option>
            {history.data.map((item) => (
              <option key={item.id} value={item.id}>
                {item.question.slice(0, 60)}
              </option>
            ))}
          </SearchSelect>
        </label>
      )}
      {!identity && !prepared && (
        <>
          <label htmlFor="draft-advice-question">
            {kind === "plot"
              ? copy("想怎样调整图形？", "How would you change the plot?")
              : copy(
                  "想从这段文字了解什么？",
                  "What would you explore in this passage?",
                )}
          </label>
          <textarea
            id="draft-advice-question"
            rows={4}
            maxLength={4000}
            value={question}
            onChange={(event) => {
              questionRef.current = event.target.value;
              dirty.current = true;
              setQuestion(event.target.value);
            }}
          />
          <Button
            disabled={
              busy ||
              !question.trim() ||
              !publication.prepareAdvice ||
              (kind === "plot" && !publication.adviceDraftId)
            }
            onClick={() => {
              void perform(preview);
            }}
          >
            {copy("预览发送内容", "Preview request")}
          </Button>
        </>
      )}
      {selected.isPending && identity && (
        <p role="status">{copy("正在读取…", "Loading…")}</p>
      )}
      {selected.error && (
        <ResearchFailure
          error={selected.error}
          retry={() => {
            void selected.refetch();
          }}
        />
      )}
      {context && (
        <>
          <p className="advice-question">
            {request?.question ?? prepared?.question}
          </p>
          <details
            className="advice-context"
            open={!request || request.state === "waiting_approval"}
          >
            <summary>{copy("发送内容", "Request contents")}</summary>
            {context.kind === "plot" ? (
              <>
                <p>{context.draft.title}</p>
                <p className="entry-help">
                  {context.draft.scope.columns?.join(" · ")}
                </p>
                <pre>{context.draft.code}</pre>
              </>
            ) : (
              <>
                {!!context.themes.length && (
                  <p>{context.themes.map((theme) => theme.name).join(" · ")}</p>
                )}
                {context.sources.map((source) => (
                  <blockquote key={source.key}>{source.text}</blockquote>
                ))}
              </>
            )}
            <details>
              <summary>{copy("完整内容", "Full contents")}</summary>
              <pre>{JSON.stringify(context, null, 2)}</pre>
            </details>
          </details>
          {request && <p role="status">{labels[request.state]}</p>}
          {request?.state === "waiting_approval" && (
            <>
              <p className="entry-help">
                {request.approval.model} ·{" "}
                {copy("本次费用上限", "Cost ceiling")}{" "}
                <ResearchMoney units={request.approval.reserved_cost_units} />
              </p>
              <div className="advice-actions">
                <Button
                  disabled={busy || !configured}
                  onClick={() => {
                    void perform(async () => {
                      store(await adviceApi.send(projectId, request));
                    });
                  }}
                >
                  {copy("发送这些内容", "Send these contents")}
                </Button>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => {
                    void perform(async () => {
                      store(await adviceApi.cancel(projectId, request.id));
                    });
                  }}
                >
                  {copy("取消", "Cancel")}
                </Button>
              </div>
            </>
          )}
          {!request &&
            prepared &&
            (configured ? (
              <Button
                disabled={busy}
                onClick={() => {
                  void perform(async () => {
                    store(
                      await adviceApi.create(
                        projectId,
                        prepared.requestId,
                        prepared.question,
                        prepared.target,
                        prepared.source_version,
                      ),
                    );
                  });
                }}
              >
                {copy("准备发送", "Prepare to send")}
              </Button>
            ) : (
              <ModelSettingsLink projectId={projectId}>
                {copy("连接模型后发送", "Connect a model to send")}
              </ModelSettingsLink>
            ))}
          {active && (
            <Button
              variant="outline"
              disabled={busy || request.state === "cancel_requested"}
              onClick={() => {
                void perform(async () => {
                  store(await adviceApi.cancel(projectId, request.id));
                });
              }}
            >
              {copy("停止", "Stop")}
            </Button>
          )}
          {request?.error && <p role="alert">{request.error}</p>}
        </>
      )}
      {proposal && (
        <ProposalEditor
          proposal={proposal}
          context={context!}
          disabled={busy || request?.state === "applied"}
          onChange={setProposal}
        />
      )}
      {request?.state === "proposed" && proposal && (
        <Button
          disabled={busy}
          onClick={() => {
            void perform(async () => {
              await flushProjectEditors(projectId);
              store(await adviceApi.accept(projectId, request, proposal));
              await current.current.adviceApplied?.();
              void client.invalidateQueries({
                queryKey: ["project-history", projectId],
              });
            });
          }}
        >
          {copy("采用到草稿", "Apply to draft")}
        </Button>
      )}
      {(prepared || request) && !active && (
        <Button
          variant="ghost"
          disabled={busy}
          onClick={() => {
            setIdentity("");
            setPrepared(null);
            setProposal(null);
            setError(null);
          }}
        >
          {copy("写新建议", "New suggestion")}
        </Button>
      )}
      {error != null && (
        <ResearchFailure
          error={error}
          retry={
            identity
              ? () => {
                  void selected.refetch();
                }
              : undefined
          }
        />
      )}
    </section>
  );
}

function ProposalEditor({
  proposal,
  context,
  disabled,
  onChange,
}: {
  proposal: AdviceProposal;
  context: AdviceContext;
  disabled: boolean;
  onChange: (value: AdviceProposal) => void;
}) {
  const { locale } = useLanguage();
  const copy = (zh: string, en: string) => (locale === "zh-CN" ? zh : en);
  if (proposal.kind === "plot")
    return (
      <div className="advice-proposal">
        <label>
          {copy("图形名称", "Plot name")}
          <input
            disabled={disabled}
            value={proposal.title}
            maxLength={160}
            onChange={(event) =>
              onChange({ ...proposal, title: event.target.value })
            }
          />
        </label>
        <label>
          {copy("Python 代码", "Python code")}
          <textarea
            className="advice-code"
            rows={12}
            disabled={disabled}
            value={proposal.code}
            onChange={(event) =>
              onChange({ ...proposal, code: event.target.value })
            }
          />
        </label>
      </div>
    );
  const themes = [
    ...(context.kind === "coding" ? context.themes : []),
    ...proposal.themes,
  ];
  return (
    <fieldset className="advice-proposal" disabled={disabled}>
      <legend>{copy("主题与选段", "Themes and passages")}</legend>
      {proposal.themes.map((theme, index) => (
        <div className="advice-theme" key={theme.key}>
          <label>
            {copy("主题名称", "Theme name")}
            <input
              value={theme.name}
              maxLength={80}
              onChange={(event) =>
                onChange({
                  ...proposal,
                  themes: proposal.themes.map((item, i) =>
                    i === index ? { ...item, name: event.target.value } : item,
                  ),
                })
              }
            />
          </label>
          <label>
            {copy("主题备注", "Theme memo")}
            <textarea
              rows={2}
              value={theme.memo}
              maxLength={1000}
              onChange={(event) =>
                onChange({
                  ...proposal,
                  themes: proposal.themes.map((item, i) =>
                    i === index ? { ...item, memo: event.target.value } : item,
                  ),
                })
              }
            />
          </label>
        </div>
      ))}
      {proposal.segments.map((segment, index) => (
        <div
          className="advice-segment"
          key={`${segment.source_key}:${segment.start}:${segment.end}`}
        >
          <blockquote>{segment.quote}</blockquote>
          <div className="advice-theme-options">
            {themes.map((theme) => (
              <label key={theme.key}>
                <input
                  type="checkbox"
                  checked={segment.theme_keys.includes(theme.key)}
                  onChange={(event) =>
                    onChange({
                      ...proposal,
                      segments: proposal.segments.map((item, i) =>
                        i === index
                          ? {
                              ...item,
                              theme_keys: event.target.checked
                                ? [...item.theme_keys, theme.key]
                                : item.theme_keys.filter(
                                    (key) => key !== theme.key,
                                  ),
                            }
                          : item,
                      ),
                    })
                  }
                />
                {theme.name}
              </label>
            ))}
          </div>
          <label>
            {copy("选段备注", "Passage memo")}
            <textarea
              rows={2}
              maxLength={1000}
              value={segment.memo}
              onChange={(event) =>
                onChange({
                  ...proposal,
                  segments: proposal.segments.map((item, i) =>
                    i === index ? { ...item, memo: event.target.value } : item,
                  ),
                })
              }
            />
          </label>
          <Button
            size="sm"
            variant="ghost"
            onClick={() =>
              onChange({
                ...proposal,
                segments: proposal.segments.filter((_, i) => i !== index),
              })
            }
          >
            {copy("移出建议", "Remove from suggestion")}
          </Button>
        </div>
      ))}
    </fieldset>
  );
}
