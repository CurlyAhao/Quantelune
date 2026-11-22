"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { FriendlyErrorMessage as UiError } from "@/components/friendly-error";

import { useLanguage } from "@/providers/language-provider";
import {
  workflowNodeLabel,
  workflowNodeDescription,
  workflowCategoryLabel,
  workflowStatusLabel,
} from "@/lib/workflow-labels";
import { SavedDiagnostic } from "@/components/saved-diagnostic";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2Icon,
  CircleAlertIcon,
  Clock3Icon,
  LinkIcon,
  PlayIcon,
  PlusIcon,
  SaveIcon,
  ShieldCheckIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";
import { AuthGuard } from "@/components/auth-guard";
import { DownloadButton } from "@/components/download-button";
import { PageLoader } from "@/components/loading";
import { ProjectShell } from "@/components/project-shell";
import { useResultProjectId } from "@/components/result-source";
import { Button } from "@/components/ui/button";
import { ApiError, isApiError } from "@/lib/api-client";
import { datasetsApi, mlApi, workflowApi } from "@/lib/api-endpoints";

import { cn } from "@/lib/utils";
import type {
  GraphValidation,
  Workflow,
  WorkflowArtifact,
  WorkflowCatalogNode,
  WorkflowGraph,
  WorkflowParameterValue,
  WorkflowRun,
} from "@/lib/types";
import { ParameterInspector } from "./parameter-inspector";
import { WorkflowNode, type WorkflowNodeData } from "./workflow-node";

const nodeTypes = { workflow: WorkflowNode };
function SavedRunNotice({ status }: { status: string }) {
  const { t } = useLanguage();
  return t("ui.workflow.savedRunValue", {
    value0: workflowStatusLabel(status, t),
  });
}
const control =
  "min-w-0 rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring";
type FlowNode = Node<WorkflowNodeData>;
type PendingAction =
  { kind: "open"; id: string } | { kind: "new" } | { kind: "delete" };

export default function WorkflowClient({ projectId }: { projectId: string }) {
  const resolvedProjectId = useResultProjectId(projectId);
  if (!resolvedProjectId || resolvedProjectId === "__desktop_project__")
    return (
      <p role="alert">
        <UiMessage id="ui.research.projectIdentityIsMissing" />
      </p>
    );
  return (
    <UiAttributes messages={{ label: { id: "ui.workflow.loadingWorkflow" } }}>
      <AuthGuard label={"Loading workflow…"}>
        <ProjectShell projectId={resolvedProjectId}>
          <WorkflowBody key={resolvedProjectId} projectId={resolvedProjectId} />
        </ProjectShell>
      </AuthGuard>
    </UiAttributes>
  );
}

export function restoreGraph(
  graph: WorkflowGraph | null,
  catalog: WorkflowCatalogNode[],
  run?: WorkflowRun,
): { nodes: FlowNode[]; edges: Edge[] } {
  return {
    nodes: (graph?.nodes ?? []).map((node, index) => {
      const spec = catalog.find((item) => item.node_type === node.type);
      const result = run?.node_results[node.id];
      return {
        id: node.id,
        type: "workflow",
        position: node.position ?? { x: 60 + index * 260, y: 80 },
        data: {
          node_type: node.type,
          label: spec?.label ?? node.type,
          category: spec?.category ?? "unknown",
          params: node.data ?? {},
          status: result?.status,
          summary: result?.summary,
          error: result?.error ?? undefined,
          error_code: result?.error_code ?? undefined,
          reason_code: result?.reason_code ?? undefined,
        },
      };
    }),
    edges: (graph?.edges ?? []).map((edge) => ({
      ...edge,
      sourceHandle: edge.sourceHandle ?? undefined,
      targetHandle: edge.targetHandle ?? undefined,
    })),
  };
}

export function serializeGraph(
  nodes: FlowNode[],
  edges: Edge[],
  version: 1 | 2,
): WorkflowGraph {
  return {
    schema_version: version,
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.data.node_type,
      data: node.data.params,
      position: node.position,
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle,
      targetHandle: edge.targetHandle,
    })),
  };
}

function rememberWorkflow(projectId: string, workflowId: string | null) {
  const url = new URL(window.location.href);
  if (workflowId) url.searchParams.set("workflow", workflowId);
  else url.searchParams.delete("workflow");
  window.history.replaceState(window.history.state, "", url);
  try {
    const key = `statforge.workflow:${projectId}`;
    if (workflowId) window.localStorage.setItem(key, workflowId);
    else window.localStorage.removeItem(key);
  } catch {
    /* Server-backed workflows remain available in the selector. */
  }
}

function outputArtifacts(
  output?: Record<string, unknown> | null,
): WorkflowArtifact[] {
  return Array.isArray(output?.artifacts)
    ? output.artifacts.filter(
        (value): value is WorkflowArtifact =>
          typeof value === "object" &&
          value !== null &&
          typeof value.id === "string" &&
          typeof value.filename === "string" &&
          typeof value.sha256 === "string",
      )
    : [];
}

function outputModel(output?: Record<string, unknown> | null) {
  const source = output?.source;
  if (
    !source ||
    typeof source !== "object" ||
    !("ml_run_id" in source) ||
    !("artifact" in source)
  )
    return null;
  const artifact = source.artifact;
  if (
    typeof source.ml_run_id !== "string" ||
    !artifact ||
    typeof artifact !== "object" ||
    !("sha256" in artifact) ||
    typeof artifact.sha256 !== "string"
  )
    return null;
  return { runId: source.ml_run_id, sha256: artifact.sha256 };
}

function WorkflowBody({ projectId }: { projectId: string }) {
  const { t: workflowT, formatDate, formatNumber } = useLanguage();
  const [datasetId, setDatasetId] = useState<string | null>(null);
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [name, setName] = useState(() => workflowT("workflow.defaultName"));
  const [version, setVersion] = useState(1);
  const [schemaVersion, setSchemaVersion] = useState<1 | 2>(2);
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [validation, setValidation] = useState<GraphValidation | null>(null);
  const [run, setRun] = useState<WorkflowRun | null>(null);
  const [snapshot, setSnapshot] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [unknownPublication, setUnknownPublication] = useState(false);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [message, setMessage] = useState<{
    error: boolean;
    text: React.ReactNode;
  } | null>(null);
  const [connectFrom, setConnectFrom] = useState("");
  const [connectTo, setConnectTo] = useState("");
  const [deletionOffset, setDeletionOffset] = useState(0);
  const context = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const initialRestored = useRef(false);
  useEffect(
    () => () => {
      context.current++;
      controller.current?.abort();
    },
    [],
  );

  const datasetsQuery = useQuery({
    queryKey: ["datasets", projectId],
    queryFn: ({ signal }) => datasetsApi.list(projectId, signal),
  });
  const catalogQuery = useQuery({
    queryKey: ["workflow", "catalog"],
    queryFn: ({ signal }) => workflowApi.catalog(signal),
  });
  const workflowsQuery = useQuery({
    queryKey: ["workflows", projectId],
    queryFn: ({ signal }) => workflowApi.list(projectId, signal),
  });
  const deletionsQuery = useQuery({
    queryKey: ["workflow-output-deletions", projectId, deletionOffset],
    queryFn: ({ signal }) =>
      workflowApi.pendingDeletions(projectId, signal, deletionOffset),
  });
  const historyQuery = useQuery({
    queryKey: ["workflow-runs", workflowId],
    enabled: !!workflowId,
    queryFn: ({ signal }) => workflowApi.listRuns(workflowId!, signal),
  });
  const selected = nodes.find((node) => node.id === selectedId);
  const spec = catalogQuery.data?.nodes.find(
    (item) => item.node_type === selected?.data.node_type,
  );
  const sourceId = useMemo(() => {
    const visited = new Set<string>();
    const source = (id: string): string | null => {
      if (visited.has(id)) return null;
      visited.add(id);
      const node = nodes.find((item) => item.id === id);
      if (node?.data.node_type === "dataset")
        return typeof node.data.params.dataset_id === "string"
          ? node.data.params.dataset_id
          : datasetId;
      for (const edge of edges.filter((item) => item.target === id)) {
        const found = source(edge.source);
        if (found) return found;
      }
      return null;
    };
    return selectedId ? (source(selectedId) ?? datasetId) : datasetId;
  }, [nodes, edges, selectedId, datasetId]);
  const columnsQuery = useQuery({
    queryKey: ["dataset", sourceId, "preview"],
    enabled: !!sourceId,
    queryFn: ({ signal }) => datasetsApi.preview(sourceId!, 0, signal),
  });
  const columns = useMemo(() => {
    let result = (columnsQuery.data?.columns ?? []).map((column) => ({
      name: column.name,
      is_numeric: /int|float|decimal|numeric|number/i.test(
        column.polars_type || column.inferred_type,
      ),
    }));
    const visited = new Set<string>();
    const upstream = (id: string) => {
      if (visited.has(id)) return;
      visited.add(id);
      for (const edge of edges.filter((item) => item.target === id))
        upstream(edge.source);
      const node = nodes.find((item) => item.id === id);
      if (
        node?.data.node_type === "column_select" &&
        Array.isArray(node.data.params.columns)
      ) {
        const selectedColumns = node.data.params.columns;
        result = result.filter((column) =>
          selectedColumns.includes(column.name),
        );
      }
    };
    if (selectedId)
      for (const edge of edges.filter((item) => item.target === selectedId))
        upstream(edge.source);
    return result;
  }, [columnsQuery.data, nodes, edges, selectedId]);
  const ready = (datasetsQuery.data ?? []).filter(
    (dataset) => dataset.upload_status === "ready",
  );
  const locked = !!busy || snapshot;
  const catalog = useMemo(
    () => catalogQuery.data?.nodes ?? [],
    [catalogQuery.data],
  );

  function edited() {
    setDirty(true);
    setValidation(null);
    setRun(null);
    setNodes((current) =>
      current.map((node) => ({
        ...node,
        data: {
          ...node.data,
          status: undefined,
          summary: undefined,
          error: undefined,
          error_code: undefined,
          reason_code: undefined,
        },
      })),
    );
  }
  const applyWorkflow = useCallback(
    (workflow: Workflow) => {
      const graph = restoreGraph(workflow.graph, catalog);
      setNodes(graph.nodes);
      setEdges(graph.edges);
      setWorkflowId(workflow.id);
      setName(workflow.name);
      setDatasetId(workflow.dataset_id);
      setVersion(workflow.version);
      setSchemaVersion(workflow.graph?.schema_version ?? 1);
      setSelectedId(null);
      setDirty(false);
      setSnapshot(false);
      setRun(null);
      setValidation(null);
      setPending(null);
      rememberWorkflow(projectId, workflow.id);
    },
    [catalog, projectId, setNodes, setEdges],
  );
  const operate = useCallback(
    async (
      label: string,
      work: (signal: AbortSignal, current: () => boolean) => Promise<void>,
    ) => {
      controller.current?.abort();
      const operation = new AbortController(),
        ticket = ++context.current;
      controller.current = operation;
      setBusy(label);
      setMessage(null);
      const current = () =>
        context.current === ticket && !operation.signal.aborted;
      try {
        await work(operation.signal, current);
      } catch (error) {
        if (current()) {
          const unknown =
            isApiError(error) &&
            error.errorCode === "WORKFLOW_PUBLICATION_UNKNOWN";
          if (unknown) setUnknownPublication(true);
          setMessage({
            error: true,
            text: unknown ? (
              <UiMessage id="error.publicationUnknown.message" />
            ) : isApiError(error) &&
              error.errorCode === "WORKFLOW_VERSION_CONFLICT" ? (
              <UiMessage id="error.versionConflict.message" />
            ) : (
              <UiError error={error} part="message" />
            ),
          });
        }
      } finally {
        if (context.current === ticket) {
          controller.current = null;
          setBusy(null);
        }
      }
    },
    [],
  );
  const open = useCallback(
    (id: string) =>
      operate("open", async (signal, current) => {
        const workflow = await workflowApi.get(id, signal);
        if (workflow.project_id !== projectId)
          throw new ApiError(
            403,
            "Workflow belongs to a different project.",
            "WORKFLOW_PROJECT_MISMATCH",
          );
        if (current()) {
          applyWorkflow(workflow);
          setMessage({
            error: false,
            text: (
              <UiMessage
                id="ui.workflow.openedSavedVersionValue"
                values={{ value0: String(workflow.version) }}
              />
            ),
          });
        }
      }),
    [projectId, operate, applyWorkflow],
  );
  useEffect(() => {
    if (
      initialRestored.current ||
      !catalogQuery.isSuccess ||
      !workflowsQuery.data
    )
      return;
    let id = new URLSearchParams(window.location.search).get("workflow");
    try {
      id ??= window.localStorage.getItem(`statforge.workflow:${projectId}`);
    } catch {
      /* URL and selector remain available. */
    }
    if (!id || !workflowsQuery.data.some((workflow) => workflow.id === id)) {
      initialRestored.current = true;
      return;
    }
    let active = true;
    void open(id).finally(() => {
      if (active) initialRestored.current = true;
    });
    const owned = controller.current;
    return () => {
      active = false;
      if (!initialRestored.current && controller.current === owned)
        owned?.abort();
    };
  }, [catalogQuery.isSuccess, workflowsQuery.data, open, projectId]);
  function fresh() {
    context.current++;
    controller.current?.abort();
    setBusy(null);
    rememberWorkflow(projectId, null);
    setWorkflowId(null);
    setName(workflowT("workflow.defaultName"));
    setVersion(1);
    setSchemaVersion(2);
    setNodes([]);
    setEdges([]);
    setDirty(false);
    setSnapshot(false);
    setRun(null);
    setValidation(null);
    setPending(null);
    setSelectedId(null);
    setUnknownPublication(false);
    setMessage(null);
  }
  function request(action: PendingAction) {
    if (dirty || action.kind === "delete") setPending(action);
    else if (action.kind === "open") open(action.id);
    else fresh();
  }
  function confirmPending() {
    if (!pending) return;
    if (pending.kind === "open") open(pending.id);
    else if (pending.kind === "new") fresh();
    else if (workflowId)
      void operate("delete", async (signal, current) => {
        const result = await workflowApi.remove(workflowId, signal);
        if (current()) {
          fresh();
          setMessage({
            error: false,
            text:
              result.status === "cleanup_pending" ? (
                <UiMessage id="workflow.deletedPending" />
              ) : (
                <UiMessage id="workflow.deletedCompleted" />
              ),
          });
          if (deletionOffset > 0) setDeletionOffset(0);
          else void deletionsQuery.refetch();
          await workflowsQuery.refetch();
        }
      });
  }
  async function ensureSaved(signal: AbortSignal, current: () => boolean) {
    const graph = serializeGraph(nodes, edges, schemaVersion);
    const saved = workflowId
      ? dirty
        ? await workflowApi.update(
            workflowId,
            {
              name: name.trim(),
              dataset_id: datasetId,
              graph,
              expected_version: version,
            },
            signal,
          )
        : { id: workflowId, version }
      : await workflowApi.create(
          projectId,
          { name: name.trim(), dataset_id: datasetId, graph },
          signal,
        );
    if (!current()) return null;
    rememberWorkflow(projectId, saved.id);
    setWorkflowId(saved.id);
    setVersion(saved.version);
    setDirty(false);
    void workflowsQuery.refetch();
    return saved;
  }
  function save() {
    void operate("save", async (signal, current) => {
      const saved = await ensureSaved(signal, current);
      if (saved && current())
        setMessage({
          error: false,
          text: (
            <UiMessage
              id="ui.workflow.savedVersionValue.workflowClient"
              values={{ value0: String(saved.version) }}
            />
          ),
        });
    });
  }
  function validate() {
    void operate("validate", async (signal, current) => {
      const saved = await ensureSaved(signal, current);
      if (!saved) return;
      const result = await workflowApi.validate(saved.id, signal);
      if (current()) {
        setValidation(result);
        setMessage({
          error: !result.valid,
          text: result.valid ? (
            <UiMessage id="workflow.validationReady" />
          ) : (
            <UiMessage id="workflow.validationIssues" />
          ),
        });
      }
    });
  }
  function startRun() {
    void operate("run", async (signal, current) => {
      const saved = await ensureSaved(signal, current);
      if (!saved) return;
      const checked = await workflowApi.validate(saved.id, signal);
      if (!current()) return;
      setValidation(checked);
      if (!checked.valid) {
        setMessage({
          error: true,
          text: (
            <UiMessage id="ui.workflow.resolveTheValidationIssuesBeforeRunning" />
          ),
        });
        return;
      }
      const result = await workflowApi.run(saved.id, signal);
      if (!current()) return;
      setRun(result);
      const applied = restoreGraph(
        serializeGraph(nodes, edges, schemaVersion),
        catalog,
        result,
      );
      setNodes(applied.nodes);
      setMessage({
        error: result.status !== "completed",
        text: <SavedRunNotice status={result.status} />,
      });
      void historyQuery.refetch();
      void datasetsQuery.refetch();
    });
  }
  function viewRun(id: string) {
    void operate("history", async (signal, current) => {
      const result = await workflowApi.getRun(id, signal);
      if (!current()) return;
      if (!result.graph_snapshot) {
        setMessage({
          error: true,
          text: (
            <UiMessage id="ui.workflow.thisLegacyRunHasNoReadableGraphSnapshotItsSavedResultsRemainAvailable" />
          ),
        });
        setRun(result);
        return;
      }
      const applied = restoreGraph(result.graph_snapshot, catalog, result);
      setNodes(applied.nodes);
      setEdges(applied.edges);
      setSchemaVersion(result.graph_snapshot.schema_version ?? 1);
      setSelectedId(null);
      setDatasetId(result.dataset_id);
      setSnapshot(true);
      setRun(result);
      setMessage(null);
    });
  }
  const onConnect = useCallback(
    (connection: Connection) => {
      if (
        locked ||
        !connection.source ||
        !connection.target ||
        connection.source === connection.target
      )
        return;
      setEdges((current) => addEdge(connection, current));
      setDirty(true);
      setValidation(null);
      setRun(null);
    },
    [locked, setEdges],
  );
  function addNode(item: WorkflowCatalogNode) {
    if (locked) return;
    const id = crypto.randomUUID();
    edited();
    setNodes((current) => [
      ...current,
      {
        id,
        type: "workflow",
        position: {
          x: 60 + (current.length % 3) * 270,
          y: 60 + Math.floor(current.length / 3) * 180,
        },
        data: {
          node_type: item.node_type,
          label: item.label,
          category: item.category,
          params: {},
        },
      },
    ]);
    setSelectedId(id);
  }
  function changeParam(key: string, value: WorkflowParameterValue | undefined) {
    if (locked || !selectedId) return;
    edited();
    setNodes((current) =>
      current.map((node) => {
        if (node.id !== selectedId) return node;
        const params = { ...node.data.params };
        if (value === undefined) delete params[key];
        else params[key] = value;
        return { ...node, data: { ...node.data, params } };
      }),
    );
  }
  function deleteNode() {
    if (!selectedId || locked) return;
    edited();
    setNodes((current) => current.filter((node) => node.id !== selectedId));
    setEdges((current) =>
      current.filter(
        (edge) => edge.source !== selectedId && edge.target !== selectedId,
      ),
    );
    setSelectedId(null);
  }

  if (
    datasetsQuery.isLoading ||
    catalogQuery.isLoading ||
    workflowsQuery.isLoading
  )
    return (
      <UiAttributes
        messages={{ label: { id: "ui.workflow.loadingWorkflows" } }}
      >
        <PageLoader label={"Loading workflows…"} />
      </UiAttributes>
    );
  if (datasetsQuery.error || catalogQuery.error || workflowsQuery.error)
    return (
      <div role="alert" className="space-y-3">
        <p>
          <UiMessage id="ui.workflow.workflowDataCouldNotBeLoadedCheckYourAccessAndRetry" />
        </p>
        <Button
          variant="outline"
          onClick={() => {
            void datasetsQuery.refetch();
            void catalogQuery.refetch();
            void workflowsQuery.refetch();
          }}
        >
          {" "}
          <UiMessage id="common.retry" />{" "}
        </Button>
      </div>
    );
  const grouped = catalog.reduce<Record<string, WorkflowCatalogNode[]>>(
    (groups, item) => {
      (groups[item.category] ??= []).push(item);
      return groups;
    },
    {},
  );
  return (
    <div className="min-w-0 space-y-5">
      {(!!deletionsQuery.data?.receipts.length || deletionOffset > 0) && (
        <UiAttributes
          messages={{
            "aria-label": { id: "ui.workflow.pendingOutputCleanup" },
          }}
        >
          <section
            aria-label={"Pending output cleanup"}

            className="space-y-3 rounded-md border p-3"
          >
            <h2 className="text-sm font-semibold">
              <UiMessage id="ui.workflow.outputCleanupPending" />
            </h2>
            <p className="text-sm text-muted-foreground">
              {" "}
              <UiMessage id="ui.workflow.theWorkflowIsDeletedItsRemainingOutputFilesHaveASavedCleanupReceiptAndCanBeRetried" />{" "}
            </p>
            {deletionsQuery.data?.receipts.map((receipt) => (
              <div
                key={receipt.receipt_id}
                className="flex flex-wrap items-center gap-3 text-sm"
              >
                <span>
                  {receipt.pending_artifacts}{" "}
                  <UiMessage id="ui.workflow.filesRemaining" />
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!!busy}
                  onClick={() =>
                    void operate("cleanup", async (signal, current) => {
                      const result = await workflowApi.retryDeletion(
                        receipt.receipt_id,
                        signal,
                      );
                      if (current()) {
                        setMessage({
                          error: result.status !== "completed",
                          text:
                            result.status === "completed" ? (
                              <UiMessage id="workflow.cleanupCompleted" />
                            ) : (
                              <UiMessage id="workflow.cleanupPending" />
                            ),
                        });
                        if (deletionOffset > 0) setDeletionOffset(0);
                        else void deletionsQuery.refetch();
                      }
                    })
                  }
                >
                  {" "}
                  <UiMessage id="ui.workflow.retryOutputCleanup" />{" "}
                </Button>
              </div>
            ))}
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={
                  !!busy || deletionOffset === 0 || deletionsQuery.isFetching
                }
                onClick={() =>
                  setDeletionOffset(Math.max(0, deletionOffset - 100))
                }
              >
                {" "}
                <UiMessage id="ui.workflow.previousCleanupBatch" />{" "}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={
                  !!busy ||
                  deletionsQuery.isFetching ||
                  deletionsQuery.data?.next_offset == null
                }
                onClick={() => {
                  if (deletionsQuery.data?.next_offset != null)
                    setDeletionOffset(deletionsQuery.data.next_offset);
                }}
              >
                {" "}
                <UiMessage id="ui.workflow.nextCleanupBatch" />{" "}
              </Button>
            </div>
          </section>
        </UiAttributes>
      )}
      <div className="flex flex-wrap items-end gap-3 border-b pb-4">
        <label className="min-w-40 flex-1 space-y-1.5 text-sm">
          <span className="font-medium">
            <UiMessage id="ui.workflow.savedWorkflow" />
          </span>
          <UiAttributes
            messages={{ "aria-label": { id: "ui.workflow.savedWorkflow" } }}
          >
            <SearchSelect
              aria-label={"Saved workflow"}

              className={control + " w-full"}
              value={workflowId ?? ""}
              disabled={!!busy}
              onChange={(event) =>
                event.target.value
                  ? request({ kind: "open", id: event.target.value })
                  : request({ kind: "new" })
              }
            >
              <option value="">
                <UiMessage id="ui.workflow.newWorkflow" />
              </option>
              {(workflowsQuery.data ?? []).map((workflow) => (
                <option key={workflow.id} value={workflow.id}>
                  {workflow.name} · v{workflow.version}
                </option>
              ))}
            </SearchSelect>
          </UiAttributes>
        </label>
        <Button
          variant="outline"
          disabled={!!busy}
          onClick={() => request({ kind: "new" })}
        >
          <PlusIcon className="size-4" />{" "}
          <UiMessage id="ui.workflow.new" />{" "}
        </Button>
        {workflowId && (
          <>
            <Button
              variant="outline"
              disabled={!!busy}
              onClick={() => request({ kind: "open", id: workflowId })}
            >
              {" "}
              <UiMessage id="ui.workflow.reload" />{" "}
            </Button>
            <Button
              variant="ghost"
              disabled={!!busy || snapshot}
              onClick={() => request({ kind: "delete" })}
            >
              <Trash2Icon className="size-4" />{" "}
              <UiMessage id="ui.workflow.deleteWorkflow" />{" "}
            </Button>
          </>
        )}
      </div>
      {pending && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-3 text-sm"
        >
          <p>
            {pending.kind === "delete" ? (
              <UiMessage id="ui.displayBranch.workflow.confirmDeleteHistory" />
            ) : (
              <UiMessage id="ui.displayBranch.workflow.unsavedDraftChoice" />
            )}
          </p>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setPending(null)}
            >
              {" "}
              <UiMessage id="ui.workflow.keepEditing" />{" "}
            </Button>
            <Button
              size="sm"
              variant={pending.kind === "delete" ? "destructive" : "outline"}
              onClick={confirmPending}
            >
              {pending.kind === "delete" ? (
                <UiMessage id="ui.displayBranch.workflow.deleteSavedWorkflow" />
              ) : (
                <UiMessage id="ui.displayBranch.workflow.discardEditsContinue" />
              )}
            </Button>
          </div>
        </div>
      )}
      <div className="flex flex-wrap items-end gap-3">
        <label className="min-w-44 flex-1 space-y-1.5 text-sm">
          <span className="font-medium">
            <UiMessage id="ui.workflow.workflowName" />
          </span>
          <UiAttributes
            messages={{ "aria-label": { id: "ui.workflow.workflowName" } }}
          >
            <input
              aria-label={"Workflow name"}

              className={control + " w-full"}
              value={name}
              maxLength={255}
              disabled={locked}
              onChange={(event) => {
                setName(event.target.value);
                edited();
              }}
            />
          </UiAttributes>
        </label>
        <label className="min-w-44 flex-1 space-y-1.5 text-sm">
          <span className="font-medium">
            <UiMessage id="workflow.node.dataset.label" />
          </span>
          <UiAttributes
            messages={{ "aria-label": { id: "ui.workflow.workflowDataset" } }}
          >
            <SearchSelect
              aria-label={"Workflow dataset"}

              className={control + " w-full"}
              value={datasetId ?? ""}
              disabled={locked}
              onChange={(event) => {
                setDatasetId(event.target.value || null);
                edited();
              }}
            >
              <option value="">
                <UiMessage id="ui.workflow.chooseADataset" />
              </option>
              {ready.map((dataset) => (
                <option key={dataset.id} value={dataset.id}>
                  {dataset.original_filename}
                </option>
              ))}
            </SearchSelect>
          </UiAttributes>
        </label>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={locked || !name.trim()}
            onClick={save}
          >
            <SaveIcon className="size-4" />{" "}
            <UiMessage id="ui.workflow.save" />{" "}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={locked || !nodes.length || !name.trim()}
            onClick={validate}
          >
            <ShieldCheckIcon className="size-4" />{" "}
            <UiMessage id="ui.workflow.validate" />{" "}
          </Button>
          <Button
            size="sm"
            disabled={
              locked ||
              !datasetId ||
              !nodes.length ||
              !name.trim() ||
              unknownPublication
            }
            onClick={startRun}
          >
            <PlayIcon className="size-4" />
            {busy === "run" ? (
              <UiMessage id="ui.common.running.alternate" />
            ) : (
              <UiMessage id="ui.common.run" />
            )}
          </Button>
          {busy === "run" && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                controller.current?.abort();
                setUnknownPublication(true);
                setMessage({
                  error: false,
                  text: (
                    <UiMessage id="ui.workflow.cancellationRequestedReviewHistoryToCheckWhetherPublicationCompleted" />
                  ),
                });
              }}
            >
              <XIcon className="size-4" />{" "}
              <UiMessage id="ui.workflow.cancelRun" />{" "}
            </Button>
          )}
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>
          {workflowId ? (
            <UiMessage
              id="ui.workflow.savedVersionValue"
              values={{ value0: String(version) }}
            />
          ) : (
            <UiMessage id="ui.displayBranch.workflow.newDraft" />
          )}
          {dirty && <UiMessage id="workflow.unsavedEdits" />}
          {schemaVersion === 1 && <UiMessage id="workflow.legacyParameters" />}
        </span>
        <span>
          {" "}
          <UiMessage id="ui.workflow.sourceFilesAreRetainedLearningHappensInsideTrainingFolds" />{" "}
        </span>
      </div>
      {message && (
        <p
          role={message.error ? "alert" : "status"}
          className={cn(
            "rounded-md border px-3 py-2 text-sm leading-relaxed",
            message.error && "text-destructive",
          )}
        >
          {message.text}
        </p>
      )}
      {unknownPublication && (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <p>
            <UiMessage id="ui.workflow.checkSavedHistoryBeforeRetrying" />
          </p>
          <Button
            size="sm"
            variant="outline"
            disabled={!!busy || !workflowId}
            onClick={() => {
              void historyQuery.refetch();
            }}
          >
            {" "}
            <UiMessage id="ui.workflow.refreshHistory" />{" "}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={
              !!busy ||
              historyQuery.isFetching ||
              !!historyQuery.error ||
              !historyQuery.data
            }
            onClick={() => setUnknownPublication(false)}
          >
            {" "}
            <UiMessage id="ui.workflow.historyCheckedAllowANewRun" />{" "}
          </Button>
        </div>
      )}
      {snapshot && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-muted/40 p-3 text-sm">
          <p>
            {" "}
            <UiMessage id="ui.workflow.readOnlyRunSnapshotVersion" />{" "}
            {run?.workflow_version ?? <UiMessage id="ui.profiling.unknown" />} ·{" "}
            {workflowStatusLabel(run?.status, workflowT)}
          </p>
          <Button
            size="sm"
            variant="outline"
            onClick={() => workflowId && open(workflowId)}
          >
            {" "}
            <UiMessage id="ui.workflow.returnToCurrentWorkflow" />{" "}
          </Button>
        </div>
      )}
      <div className="grid min-w-0 gap-4 xl:grid-cols-[176px_minmax(280px,1fr)_280px]">
        <UiAttributes
          messages={{ "aria-label": { id: "ui.workflow.nodePalette" } }}
        >
          <aside
            aria-label={"Node palette"}
            className="min-w-0 space-y-4 rounded-xl border p-3 xl:max-h-[680px] xl:overflow-y-auto"
          >
            <h2 className="text-sm font-semibold">
              <UiMessage id="ui.workflow.addAStep" />
            </h2>
            {Object.entries(grouped).map(([category, items]) => (
              <details
                key={category}
                open={category === "data" || category === "statistics"}
                className="text-sm"
              >
                <summary className="cursor-pointer py-2 font-medium">
                  {workflowCategoryLabel(category, workflowT)}
                </summary>
                <div className="mt-1 flex flex-wrap gap-1 xl:flex-col">
                  {items.map((item) => (
                    <button
                      key={item.node_type}
                      type="button"
                      disabled={locked || item.available === false}
                      title={workflowNodeDescription(item.node_type, workflowT)}
                      onClick={() => addNode(item)}
                      className="flex min-h-9 items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-primary disabled:opacity-50"
                    >
                      <PlusIcon className="size-3.5 shrink-0" />
                      {workflowNodeLabel(item.node_type, workflowT)}
                    </button>
                  ))}
                </div>
              </details>
            ))}
          </aside>
        </UiAttributes>
        <div className="min-w-0 space-y-3">
          <UiAttributes
            messages={{ "aria-label": { id: "ui.workflow.workflowGraph" } }}
          >
            <div
              aria-label={"Workflow graph"}
              className="relative h-[480px] min-w-0 overflow-hidden rounded-xl border bg-muted/20 xl:h-[680px]"
            >
              <ReactFlow
                nodes={nodes}
                edges={edges}
                nodeTypes={nodeTypes}
                onNodesChange={(changes) => {
                  if (locked) {
                    onNodesChange(
                      changes.filter(
                        (change) =>
                          change.type === "select" ||
                          change.type === "dimensions",
                      ),
                    );
                    return;
                  }
                  onNodesChange(changes);
                  if (
                    changes.some(
                      (change) =>
                        change.type === "remove" ||
                        (change.type === "position" && change.dragging),
                    )
                  )
                    edited();
                }}
                onEdgesChange={(changes) => {
                  if (!locked) {
                    onEdgesChange(changes);
                    if (changes.some((change) => change.type === "remove"))
                      edited();
                  }
                }}
                onConnect={onConnect}
                onNodeClick={(_, node) => setSelectedId(node.id)}
                nodesDraggable={!locked}
                nodesConnectable={!locked}
                edgesReconnectable={!locked}
                deleteKeyCode={locked ? null : "Backspace"}
                fitView
                ariaLabelConfig={{
                  "node.a11yDescription.default": workflowT(
                    locked ? "workflow.aria.nodeLocked" : "workflow.aria.node",
                  ),
                  "node.a11yDescription.keyboardDisabled": workflowT(
                    locked ? "workflow.aria.nodeLocked" : "workflow.aria.node",
                  ),
                  "node.a11yDescription.ariaLiveMessage": ({
                    direction,
                    x,
                    y,
                  }) =>
                    workflowT("workflow.aria.moved", {
                      direction: workflowT(
                        direction === "left"
                          ? "workflow.aria.left"
                          : direction === "right"
                            ? "workflow.aria.right"
                            : direction === "up"
                              ? "workflow.aria.up"
                              : "workflow.aria.down",
                      ),
                      x: formatNumber(x),
                      y: formatNumber(y),
                    }),
                  "edge.a11yDescription.default": workflowT(
                    locked ? "workflow.aria.edgeLocked" : "workflow.aria.edge",
                  ),
                  "controls.ariaLabel": workflowT("workflow.aria.controls"),
                  "controls.zoomIn.ariaLabel": workflowT(
                    "workflow.aria.zoomIn",
                  ),
                  "controls.zoomOut.ariaLabel": workflowT(
                    "workflow.aria.zoomOut",
                  ),
                  "controls.fitView.ariaLabel": workflowT(
                    "workflow.aria.fitView",
                  ),
                  "controls.interactive.ariaLabel": workflowT(
                    "workflow.aria.interactive",
                  ),
                  "minimap.ariaLabel": workflowT("workflow.aria.minimap"),
                  "handle.ariaLabel": workflowT("workflow.aria.handle"),
                }}
                proOptions={{ hideAttribution: true }}
              >
                <Background gap={24} />
                <Controls showInteractive={false} />
              </ReactFlow>
              {nodes.length === 0 && (
                <div className="pointer-events-none absolute inset-0 grid place-items-center px-8">
                  <div className="max-w-sm space-y-2 text-center">
                    <h2 className="text-base font-semibold">
                      {" "}
                      <UiMessage id="ui.workflow.buildAReproducibleAnalysis" />{" "}
                    </h2>
                    <p className="text-sm leading-relaxed text-muted-foreground">
                      {" "}
                      <UiMessage id="ui.workflow.addADatasetSourceConnectAStatisticsOrModelStepThenChooseAnOutputSaveTheGraphToResumeItLater" />{" "}
                    </p>
                  </div>
                </div>
              )}
            </div>
          </UiAttributes>
          <details className="rounded-md border p-3 text-sm">
            <summary className="cursor-pointer font-medium">
              {" "}
              <UiMessage id="ui.workflow.connectNodesWithTheKeyboard" />{" "}
            </summary>
            <div className="mt-3 flex flex-wrap gap-2">
              <UiAttributes
                messages={{
                  "aria-label": { id: "ui.workflow.connectionSource" },
                }}
              >
                <SearchSelect
                  aria-label={"Connection source"}

                  className={control + " flex-1"}
                  value={connectFrom}
                  disabled={locked}
                  onChange={(event) => setConnectFrom(event.target.value)}
                >
                  <option value="">
                    <UiMessage id="ui.workflow.fromNode" />
                  </option>
                  {nodes.map((node) => (
                    <option key={node.id} value={node.id}>
                      {workflowNodeLabel(node.data.node_type, workflowT)} ·{" "}
                      {node.id.slice(0, 5)}
                    </option>
                  ))}
                </SearchSelect>
              </UiAttributes>
              <UiAttributes
                messages={{
                  "aria-label": { id: "ui.workflow.connectionTarget" },
                }}
              >
                <SearchSelect
                  aria-label={"Connection target"}

                  className={control + " flex-1"}
                  value={connectTo}
                  disabled={locked}
                  onChange={(event) => setConnectTo(event.target.value)}
                >
                  <option value="">
                    <UiMessage id="ui.workflow.toNode" />
                  </option>
                  {nodes
                    .filter(
                      (node) =>
                        node.id !== connectFrom &&
                        node.data.node_type !== "dataset",
                    )
                    .map((node) => (
                      <option key={node.id} value={node.id}>
                        {workflowNodeLabel(node.data.node_type, workflowT)} ·{" "}
                        {node.id.slice(0, 5)}
                      </option>
                    ))}
                </SearchSelect>
              </UiAttributes>
              <Button
                size="sm"
                variant="outline"
                disabled={locked || !connectFrom || !connectTo}
                onClick={() =>
                  onConnect({
                    source: connectFrom,
                    target: connectTo,
                    sourceHandle: null,
                    targetHandle: null,
                  })
                }
              >
                <LinkIcon className="size-4" />{" "}
                <UiMessage id="ui.workflow.connect" />{" "}
              </Button>
            </div>
          </details>
        </div>
        <UiAttributes
          messages={{ "aria-label": { id: "ui.workflow.nodeParameters" } }}
        >
          <aside
            aria-label={"Node parameters"}
            className="min-w-0 space-y-4 rounded-xl border p-4 xl:max-h-[680px] xl:overflow-y-auto"
          >
            <h2 className="text-sm font-semibold">
              <UiMessage id="ui.workflow.stepParameters" />
            </h2>
            <UiAttributes
              messages={{ "aria-label": { id: "ui.workflow.selectedNode" } }}
            >
              <SearchSelect
                aria-label={"Selected node"}

                className={control + " w-full"}
                value={selectedId ?? ""}
                onChange={(event) => setSelectedId(event.target.value || null)}
              >
                <option value="">
                  <UiMessage id="ui.workflow.chooseANode" />
                </option>
                {nodes.map((node) => (
                  <option key={node.id} value={node.id}>
                    {workflowNodeLabel(node.data.node_type, workflowT)} ·{" "}
                    {node.id.slice(0, 5)}
                  </option>
                ))}
              </SearchSelect>
            </UiAttributes>
            {selected && spec ? (
              <>
                <div className="space-y-1">
                  <h3 className="font-semibold">
                    {workflowNodeLabel(spec.node_type, workflowT)}
                  </h3>
                  <p className="text-sm leading-relaxed text-muted-foreground">
                    {workflowNodeDescription(spec.node_type, workflowT)}
                  </p>
                </div>
                {columnsQuery.error && (
                  <p role="alert" className="text-sm text-destructive">
                    {" "}
                    <UiMessage id="ui.workflow.columnSchemaIsUnavailableCheckDatasetAccess" />{" "}
                  </p>
                )}
                <ParameterInspector
                  spec={spec}
                  parameters={selected.data.params}
                  columns={columns}
                  datasets={ready}
                  disabled={locked}
                  onChange={changeParam}
                />
                <Button
                  size="sm"
                  variant="outline"
                  className="w-full"
                  disabled={locked}
                  onClick={deleteNode}
                >
                  <Trash2Icon className="size-4" />{" "}
                  <UiMessage id="ui.workflow.deleteNode" />{" "}
                </Button>
              </>
            ) : (
              <p className="text-sm leading-relaxed text-muted-foreground">
                {" "}
                <UiMessage id="ui.workflow.selectANodeInTheGraphOrListToChooseActualColumnsAndConfigureItsParameters" />{" "}
              </p>
            )}
            {validation && (
              <div className="space-y-2 border-t pt-4">
                <h3 className="flex items-center gap-2 text-sm font-semibold">
                  {validation.valid ? (
                    <CheckCircle2Icon className="size-4" />
                  ) : (
                    <CircleAlertIcon className="size-4" />
                  )}{" "}
                  <UiMessage id="ui.ml.validation" />{" "}
                </h3>
                {validation.issues.length ? (
                  validation.issues.map((issue, index) => (
                    <div key={index}>
                      <button
                        key={index}
                        className={cn(
                          "block text-left text-sm leading-relaxed",
                          issue.level === "error" && "text-destructive",
                        )}
                        onClick={() =>
                          issue.node_id && setSelectedId(issue.node_id)
                        }
                      >
                        {workflowT("error.requestFailed.message", {
                          code: issue.code,
                        })}
                      </button>
                      <SavedDiagnostic>{issue.message}</SavedDiagnostic>
                    </div>
                  ))
                ) : (
                  <p className="text-sm text-muted-foreground">
                    <UiMessage id="ui.workflow.noIssues" />
                  </p>
                )}
              </div>
            )}
          </aside>
        </UiAttributes>
      </div>
      {workflowId && (
        <UiAttributes
          messages={{ "aria-label": { id: "ui.workflow.runHistory" } }}
        >
          <section
            aria-label={"Run history"}
            className="space-y-3 border-t pt-5"
          >
            <h2 className="flex items-center gap-2 text-base font-semibold">
              <Clock3Icon className="size-4" />{" "}
              <UiMessage id="ui.workflow.savedRunHistory" />{" "}
            </h2>
            {historyQuery.error ? (
              <p role="alert" className="text-sm text-destructive">
                {" "}
                <UiMessage id="ui.workflow.historyCouldNotBeLoadedCheckActualDatasetAccess" />{" "}
              </p>
            ) : historyQuery.isFetching ? (
              <p className="text-sm text-muted-foreground">
                <UiMessage id="ui.statistics.loadingHistory" />
              </p>
            ) : historyQuery.data?.runs.length ? (
              <div className="flex flex-wrap gap-2">
                {historyQuery.data.runs.map((item) => (
                  <button
                    key={item.id}
                    disabled={!!busy || dirty}
                    onClick={() => viewRun(item.id)}
                    className={cn(
                      "rounded-md border px-3 py-2 text-left text-sm focus-visible:outline-2 focus-visible:outline-primary disabled:opacity-50",
                      run?.id === item.id && "border-primary",
                    )}
                  >
                    <span className="block font-medium">
                      {formatDate(item.created_at)}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {workflowStatusLabel(item.status, workflowT)}
                      {item.total_seconds != null &&
                        ` · ${formatNumber(item.total_seconds)}s`}
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                {" "}
                <UiMessage id="ui.workflow.runsAppearHereAfterTheyAreSaved" />{" "}
              </p>
            )}
            {dirty && (
              <p className="text-xs text-muted-foreground">
                {" "}
                <UiMessage id="ui.workflow.saveThisDraftBeforeOpeningAnImmutableRunSnapshot" />{" "}
              </p>
            )}
          </section>
        </UiAttributes>
      )}
      {run && (
        <UiAttributes
          messages={{ "aria-label": { id: "ui.workflow.savedRunResults" } }}
        >
          <section
            aria-label={"Saved run results"}
            className="min-w-0 space-y-4 border-t pt-5"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-base font-semibold">
                {" "}
                <UiMessage id="ui.workflow.savedResults" />{" "}
                {workflowStatusLabel(run.status, workflowT)}
              </h2>
              <span className="text-xs text-muted-foreground">
                {" "}
                <UiMessage id="ui.workflow.workflowVersion" />{" "}
                {run.workflow_version ?? (
                  <UiMessage id="workflow.legacyVersion" />
                )}
              </span>
            </div>
            {run.error_message && (
              <div role="alert" className="text-sm text-destructive">
                <UiMessage id="error.unexpected.message" />
                <SavedDiagnostic>{run.error_message}</SavedDiagnostic>
              </div>
            )}
            <div className="overflow-x-auto rounded-xl border">
              <table className="w-full text-left text-sm">
                <thead className="bg-muted/40">
                  <tr>
                    <th className="px-4 py-3 font-medium">
                      <UiMessage id="ui.workflow.step" />
                    </th>
                    <th className="px-4 py-3 font-medium">
                      <UiMessage id="ui.projects.status" />
                    </th>
                    <th className="px-4 py-3 font-medium">
                      <UiMessage id="ui.statistics.result" />
                    </th>
                    <th className="px-4 py-3 font-medium">
                      <UiMessage id="ui.workflow.files" />
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {run.order.map((id) => {
                    const result = run.node_results[id];
                    const artifacts = outputArtifacts(result?.output);
                    const model = outputModel(result?.output);
                    return (
                      <tr key={id} className="border-t align-top">
                        <td className="px-4 py-3">
                          <button
                            onClick={() => setSelectedId(id)}
                            className="text-left font-medium hover:underline"
                          >
                            {result?.node_type
                              ? workflowNodeLabel(result.node_type, workflowT)
                              : id}
                          </button>
                        </td>
                        <td
                          className={cn(
                            "px-4 py-3",
                            result?.status === "failed" && "text-destructive",
                          )}
                        >
                          {workflowStatusLabel(result?.status, workflowT)}
                        </td>
                        <td className="max-w-md px-4 py-3 leading-relaxed">
                          {result?.error_code &&
                            workflowT("error.requestFailed.message", {
                              code: result.error_code,
                            })}
                          {(result?.error || result?.summary) && (
                            <SavedDiagnostic>
                              {result?.error || result?.summary}
                            </SavedDiagnostic>
                          )}
                          {result?.output && (
                            <details className="mt-2">
                              <summary className="cursor-pointer text-xs text-muted-foreground">
                                {" "}
                                <UiMessage id="ui.workflow.savedResultDetails" />{" "}
                              </summary>
                              <pre className="mt-2 max-h-72 max-w-lg overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/30 p-3 text-xs">
                                {JSON.stringify(result.output, null, 2)}
                              </pre>
                            </details>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          {model && (
                            <DownloadButton
                              identity={`${run.id}:${model.runId}:${model.sha256}`}
                              ariaLabel={workflowT("workflow.downloadModel")}
                              className="mb-2 rounded-md border px-3 py-2 text-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-primary"
                              download={(signal) =>
                                mlApi.downloadArtifact(model.runId, signal)
                              }
                            >
                              {" "}
                              <UiMessage id="ui.workflow.fittedModel" />{" "}
                            </DownloadButton>
                          )}
                          {artifacts.map((artifact) => (
                            <div key={artifact.id} className="mb-2 space-y-1">
                              <DownloadButton
                                identity={`${run.id}:${artifact.id}:${artifact.sha256}`}
                                ariaLabel={workflowT(
                                  "workflow.downloadArtifact",
                                  { filename: artifact.filename },
                                )}
                                className="rounded-md border px-3 py-2 text-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-primary"
                                download={(signal) =>
                                  workflowApi.downloadArtifact(
                                    run.id,
                                    artifact.id,
                                    artifact.filename,
                                    signal,
                                  )
                                }
                              >
                                {artifact.filename}
                              </DownloadButton>
                              <p className="text-xs tabular-nums text-muted-foreground">
                                {formatNumber(artifact.bytes)}{" "}
                                <UiMessage id="ui.workflow.bytes" />{" "}
                                {artifact.includes_raw_rows && (
                                  <UiMessage id="workflow.includesRawRows" />
                                )}
                              </p>
                            </div>
                          ))}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {run.sources?.map((source) => (
              <p
                key={source.dataset_id}
                className="break-all text-xs text-muted-foreground"
              >
                {" "}
                <UiMessage id="workflow.node.dataset.label" />{" "}
                {source.dataset_id} · SHA {source.dataset_sha256}
              </p>
            ))}
          </section>
        </UiAttributes>
      )}
    </div>
  );
}
