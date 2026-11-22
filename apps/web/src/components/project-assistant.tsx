"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { AssistantMarkdown } from "./assistant-markdown";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpIcon, PlusIcon, SquareIcon, Table2Icon } from "lucide-react";
import { datasetsApi } from "@/lib/api-endpoints";
import { getActorEpoch } from "@/lib/auth-session";
import { ApiError } from "@/lib/api-client";
import { researchApi } from "@/lib/research-api";
import {
  projectAssistantApi,
  type AssistantConversation,
  type AssistantMessage,
  type AssistantResult,
  type AssistantTurnInput,
  type AssistantFigureTarget,
} from "@/lib/project-assistant-api";
import type { StatisticalRunRequest } from "@/lib/types";
import { useAuthStore } from "@/stores/auth-store";
import { useLanguage } from "@/providers/language-provider";
import { DesktopLink } from "./desktop-link";
import { ModelSettingsLink } from "./model-settings-link";
import { Button } from "./ui/button";
import { openMaterialResult } from "@/lib/open-material-result";
import { AssistantPlotPreview } from "./assistant-plot-preview";
import { useRouter } from "next/navigation";
import { navigateToAppRoute } from "@/lib/desktop-navigation";

type Props = {
  projectId: string;
  contextKey?: string;
  initialDatasetId?: string;
  initialQuestion?: string;
  datasetId?: string | null;
  contextRevision?: string;
  beforeSend?: () => Promise<{ datasetId: string | null }>;
  figureTarget?: AssistantFigureTarget | null;
  onDetachFigure?: () => void;
  onCompleted?: () => Promise<void>;
  onOpenResult?: (result: AssistantResult) => boolean;
  onApplyStatistics?: (request: StatisticalRunRequest) => void;
  manualSource?: { datasetId: string; sha256: string };
};

function displayedQuestion(content: string) {
  if (!/^请先读取当前图形 [0-9a-f-]+ 的实际结果/.test(content)) return content;
  const instruction = content.indexOf("要求：");
  return instruction >= 0 ? content.slice(instruction + 3) : "调整图形";
}
function displayedAnswer(message: AssistantMessage) {
  let content = message.content;
  for (const result of message.results ?? []) {
    content = content.split(result.href).join(result.title);
  }
  return content;
}

export function ProjectAssistant(props: Props) {
  const epoch = useAuthStore((state) => state.actorEpoch);
  return (
    <AssistantConversationView
      key={`${epoch}:${props.projectId}:${props.contextKey ?? "project"}`}
      {...props}
      epoch={epoch}
    />
  );
}

function AssistantConversationView({
  projectId,
  contextKey = "project",
  initialDatasetId,
  initialQuestion = "",
  datasetId: taskDatasetId,
  contextRevision,
  beforeSend,
  figureTarget,
  onDetachFigure,
  onCompleted,
  onOpenResult,
  epoch,
}: Props & { epoch: number }) {
  const { locale } = useLanguage();
  const copy = useCallback((zh: string, en: string) => (locale === "zh-CN" ? zh : en), [locale]);
  const client = useQueryClient();
  const router = useRouter();
  const questionId = useId();
  const composer = useRef<HTMLTextAreaElement>(null);
  // undefined restores the latest discussion; null starts a new one.
  const [conversationId, setConversationId] = useState<string | null | undefined>(() => typeof window === "undefined" ? undefined : sessionStorage.getItem(`statforge:material-conversation:${projectId}:${contextKey}`) || undefined);
  const [question, setQuestion] = useState(initialQuestion);
  const [datasetOverride, setDatasetOverride] = useState<string>();
  const [pending, setPending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true);
  const submitting = useRef(false);
  const request = useRef<AbortController | null>(null);
  const retryInput = useRef<AssistantTurnInput | null>(null);
  const follow = useRef(true);
  const messagesElement = useRef<HTMLDivElement | null>(null);
  const completed = useRef(new Set<string>());
  useEffect(() => {
    retryInput.current = null;
    if (figureTarget) {
      setQuestion(text => text === initialQuestion ? "" : text);
      composer.current?.focus();
    }
  }, [figureTarget, initialQuestion]);
  useEffect(() => {
    const key = `statforge:material-conversation:${projectId}:${contextKey}`;
    const restore = () => { const id = sessionStorage.getItem(key); if (id) { setConversationId(id); sessionStorage.removeItem(key); void client.invalidateQueries({queryKey:["project-assistant",epoch,projectId,contextKey]}); } };
    const open = (event: Event) => {
      const detail = (event as CustomEvent<{project:string;context:string}>).detail;
      if(detail?.project === projectId && detail.context === contextKey) restore();
    };
    restore(); window.addEventListener("statforge:material-conversation", open);
    return () => window.removeEventListener("statforge:material-conversation", open);
  }, [projectId, contextKey, client, epoch]);
  const observedRunning = useRef(new Set<string>());
  const publishedResults = useRef(new Set<string>());
  const listKey = useMemo(
    () => ["project-assistant", epoch, projectId, contextKey] as const,
    [epoch, projectId, contextKey],
  );
  const conversationKey = [
    "project-assistant-conversation",
    epoch,
    projectId,
    conversationId,
  ] as const;
  const history = useQuery({
    queryKey: listKey,
    queryFn: ({ signal }) =>
      projectAssistantApi.list(projectId, contextKey, signal),
    retry: false,
  });
  const selectedId = conversationId ?? "";
  const current = useQuery({
    queryKey: conversationKey,
    queryFn: ({ signal }) =>
      projectAssistantApi.read(projectId, selectedId, signal),
    enabled: Boolean(selectedId),
    initialData: () => history.data?.items.find((item) => item.id === selectedId),
    refetchInterval: (query) =>
      query.state.data?.active_turn ? 1000 : false,
    retry: false,
  });
  const datasets = useQuery({
    queryKey: ["datasets", projectId],
    queryFn: ({ signal }) => datasetsApi.list(projectId, signal),
  });
  const provider = useQuery({
    queryKey: ["research", "provider"],
    queryFn: ({ signal }) => researchApi.security.provider(signal),
    retry: false,
  });
  const conversation = current.data;
  const readyDatasets = datasets.data?.filter(
    (item) => item.upload_status === "ready",
  ) ?? [];
  const preferredDataset =
    initialDatasetId ?? conversation?.dataset_id ??
    (readyDatasets.length === 1 ? readyDatasets[0].id : "");
  const datasetId = taskDatasetId !== undefined ? taskDatasetId ?? "" : datasetOverride ?? preferredDataset;
  const selectedDataset = readyDatasets.find((item) => item.id === datasetId);
  const activeTurn = conversation?.active_turn;
  const busy = pending || Boolean(activeTurn);
  const missingModel =
    Boolean(provider.data && (!provider.data.enabled || !provider.data.key_configured));
  const lastMessage = conversation?.messages.at(-1);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      request.current?.abort();
    };
  }, []);
  useEffect(() => {
    setDatasetOverride(undefined);
    retryInput.current = null;
  }, [initialDatasetId, taskDatasetId, contextRevision]);
  useEffect(() => {
    if (conversationId !== undefined || !history.data) return;
    setConversationId(history.data.items[0]?.id ?? null);
    if (history.data.items.length) setQuestion("");
  }, [conversationId, history.data]);
  useEffect(() => {
    const element = messagesElement.current;
    if (element && follow.current) element.scrollTop = element.scrollHeight;
  }, [lastMessage?.content, conversation?.messages.length, activeTurn?.progress]);
  useEffect(() => {
    if (conversation?.active_turn) observedRunning.current.add(conversation.id);
    if (!conversation || !lastMessage || lastMessage.role !== "assistant") return;
    const fresh = (lastMessage.results ?? []).filter((result) => !publishedResults.current.has(`${lastMessage.id}:${result.kind}:${result.id}`));
    for (const result of fresh) publishedResults.current.add(`${lastMessage.id}:${result.kind}:${result.id}`);
    if (fresh.length && observedRunning.current.has(conversation.id)) {
      for (const root of ["statistics-run", "ml-run", "task-existing-runs", "task-existing-ml", "datasets", "research-tasks", "task-analyses", "task-figures", "task-plots"]) {
        void client.invalidateQueries({ queryKey: [root] });
      }
      void onCompleted?.().catch(() => setError(copy("任务结果暂未刷新，请重新打开任务。", "Reopen this task to refresh its results.")));
    }
    if (conversation.active_turn || lastMessage.status === "running" || completed.current.has(lastMessage.id)) return;
    completed.current.add(lastMessage.id);
    observedRunning.current.delete(conversation.id);
    void client.invalidateQueries({ queryKey: listKey });
  }, [conversation, lastMessage, client, listKey, onCompleted, copy]);

  function accept(value: AssistantConversation) {
    client.setQueryData(
      ["project-assistant-conversation", epoch, projectId, value.id],
      value,
    );
    client.setQueryData<{ items: AssistantConversation[] }>(listKey, (previous) => ({
      items: [value, ...(previous?.items ?? []).filter((item) => item.id !== value.id)],
    }));
    setConversationId(value.id);
  }
  async function send() {
    if (!question.trim() || busy || submitting.current || missingModel) return;
    if (conversationId === undefined) setConversationId(null);
    submitting.current = true;
    setPending(true);
    setError("");
    request.current = new AbortController();
    try {
      const source = await beforeSend?.();
      if (!alive.current || getActorEpoch() !== epoch) return;
      const value = retryInput.current ?? {
        request_id: crypto.randomUUID(),
        conversation_id: conversationId ?? null,
        context_key: contextKey,
        question: question.trim(),
        ...(figureTarget && {figure_target: {id: figureTarget.id, revision: figureTarget.revision}}),
        dataset_id: source ? source.datasetId : datasetId || null,
      };
      retryInput.current = value;
      const saved = await projectAssistantApi.send(projectId, value, request.current.signal);
      if (!alive.current || getActorEpoch() !== epoch) return;
      if (saved.active_turn) observedRunning.current.add(saved.id);
      accept(saved);
      setQuestion("");
      retryInput.current = null;
      follow.current = true;
    } catch (reason) {
      if (!alive.current || request.current.signal.aborted || getActorEpoch() !== epoch) return;
      setError(reason instanceof ApiError
        ? reason.detail
        : reason instanceof Error ? reason.message : copy("连接中断，请重试。", "Connection interrupted. Try again."));
    } finally {
      submitting.current = false;
      if (alive.current && getActorEpoch() === epoch) setPending(false);
    }
  }
  async function stop() {
    if (!conversation || stopping) return;
    setStopping(true);
    setError("");
    request.current = new AbortController();
    try {
      const saved = await projectAssistantApi.stop(projectId, conversation.id, request.current.signal);
      if (alive.current && getActorEpoch() === epoch) accept(saved);
    } catch (reason) {
      if (alive.current && !request.current.signal.aborted && getActorEpoch() === epoch)
        setError(reason instanceof Error ? reason.message : copy("停止失败，请重试。", "Could not stop. Try again."));
    } finally {
      if (alive.current && getActorEpoch() === epoch) setStopping(false);
    }
  }

  return (
    <section className="project-assistant" aria-label={copy("分析助手", "Analysis assistant")}>
      <div className="pa-toolbar">
        <label className="pa-history">
          <span className="sr-only">{copy("对话记录", "Conversations")}</span>
          <SearchSelect
            value={conversationId ?? ""}
            disabled={busy || stopping || history.isPending}
            onChange={(event) => {
              setConversationId(event.target.value || null);
              setQuestion("");
              setError("");
              retryInput.current = null;
              follow.current = true;
            }}
          >
            <option value="">{copy("新对话", "New conversation")}</option>
            {history.data?.items.map((item) => <option key={item.id} value={item.id}>{displayedQuestion(item.messages?.[0]?.content ?? item.title)}</option>)}
          </SearchSelect>
        </label>
        <button
          type="button"
          className="pa-new"
          title={copy("新对话", "New conversation")}
          aria-label={copy("新对话", "New conversation")}
          disabled={busy || stopping}
          onClick={() => {
            setConversationId(null);
            setQuestion("");
            setError("");
            retryInput.current = null;
            follow.current = true;
          }}
        ><PlusIcon size={17} /></button>
      </div>
      <div
        className="pa-messages"
        ref={messagesElement}
        role="log"
        aria-label={copy("对话内容", "Messages")}
        aria-live="off"
        onScroll={(event) => {
          const element = event.currentTarget;
          follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
        }}
      >
        {current.isPending && selectedId && <p className="pa-empty">{copy("正在打开对话…", "Opening conversation…")}</p>}
        {!conversation?.messages.length && !current.isFetching && !figureTarget && <p className="pa-empty">{copy("想了解这份数据的什么？", "What would you like to know?")}</p>}
        {conversation?.messages.map((message) => (
          <article key={message.id} className={`pa-message is-${message.role}${message.status === "failed" ? " is-error" : ""}`}>
            <span className="sr-only">{message.role === "user" ? copy("你", "You") : "Agent"}</span>
            {message.role === "user"
              ? <p className="pa-user-text">{displayedQuestion(message.content)}</p>
              : <AssistantMarkdown content={displayedAnswer(message)} />}
            {message.results && message.results.length > 0 && <div className="pa-results">
              {message.results.filter((result) => result.href.startsWith(`/projects/${projectId}/`)).map((result) => (
                <DesktopLink key={`${result.kind}:${result.id}`} href={result.href} onClick={(event) => {
                  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
                  if (result.kind === "dataset" && onOpenResult) {
                    event.preventDefault();
                    void openMaterialResult(result.id, id => { if(!onOpenResult({...result,id})) navigateToAppRoute(router,result.href); }).catch(reason => setError(reason instanceof Error ? reason.message : "无法打开结果，请重试。"));
                  } else if(onOpenResult?.(result)) event.preventDefault();
                }}>
                  {result.kind === "plot" ? <AssistantPlotPreview project={projectId} id={result.id} title={result.title}/> : <><Table2Icon size={15} />{result.title}</>}
                </DesktopLink>
              ))}
            </div>}
            {message.status === "cancelled" && <span className="pa-message-note">{copy("已停止", "Stopped")}</span>}
          </article>
        ))}
      </div>
      {(history.isError || current.isError) && <div className="pa-error" role="alert">
        <span>{copy("无法读取对话。", "Could not load the conversation.")}</span>
        <button type="button" onClick={() => { void history.refetch(); if (selectedId) void current.refetch(); }}>{copy("重试", "Retry")}</button>
      </div>}
      {missingModel && <div className="pa-model-notice">
        <span>{copy("连接模型后即可提问。", "Connect a model to begin.")}</span>
        <ModelSettingsLink projectId={projectId}>{copy("连接模型", "Connect model")}</ModelSettingsLink>
      </div>}
      {error && <p className="pa-error" role="alert">{error}</p>}
      <form className="pa-composer" onSubmit={(event) => { event.preventDefault(); void send(); }}>
        {figureTarget ? <div className="pa-dataset qp-figure-context">
          <span>{copy("当前图形", "Current figure")} · {figureTarget.name}</span>
          <button type="button" onClick={onDetachFigure} aria-label={copy("移除当前图形引用", "Detach figure")}>×</button>
        </div> : taskDatasetId === undefined && readyDatasets.length > 1 ? <label className="pa-dataset">
          <Table2Icon size={14} />
          <span className="sr-only">{copy("当前数据", "Current data")}</span>
          <SearchSelect value={datasetId} disabled={busy} onChange={(event) => { setDatasetOverride(event.target.value); retryInput.current = null; }}>
            <option value="">{copy("项目数据", "Project data")}</option>
            {readyDatasets.map((item) => <option key={item.id} value={item.id}>{item.original_filename}</option>)}
          </SearchSelect>
        </label> : selectedDataset && <div className="pa-dataset" title={selectedDataset.original_filename}>
          <Table2Icon size={14} /><span>{selectedDataset.original_filename}</span>
        </div>}
        <label className="sr-only" htmlFor={questionId}>{copy("你的问题", "Your question")}</label>
        <textarea
          ref={composer}
          id={questionId}
          value={question}
          rows={3}
          placeholder={figureTarget ? copy("希望怎样调整这张图？", "How would you like to change this figure?") : copy("直接提问…", "Ask a question…")}
          disabled={pending}
          onChange={(event) => { setQuestion(event.target.value); retryInput.current = null; }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void send();
            }
          }}
        />
        <div className="pa-send-row">
          <span className="pa-progress" role="status" aria-live="polite">
            {activeTurn?.status === "stopping"
              ? copy("正在停止…", "Stopping…")
              : activeTurn?.progress || (pending ? copy("正在发送…", "Sending…") : "")}
          </span>
          {activeTurn ? <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={stopping || activeTurn.status === "stopping"}
            onClick={() => void stop()}
          ><SquareIcon size={13} />{copy("停止", "Stop")}</Button>
            : <Button type="submit" size="sm" disabled={pending || !question.trim() || missingModel}>
              <ArrowUpIcon size={16} />{copy("发送", "Send")}
            </Button>}
        </div>
      </form>
    </section>
  );
}
