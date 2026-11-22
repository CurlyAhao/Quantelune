"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { researchApi } from "@/lib/research-api";
import { ApiError } from "@/lib/api-client";
import { saveGeneratedFile } from "@/lib/api-download";
import {
  researchAdmission,
  researchApprovals,
  researchDocument,
  researchId,
  researchObject,
  researchReferences,
  researchModelRequest,
  sessionResearchEvents,
} from "@/lib/research-view";
import type {
  ResearchArtifact,
  ResearchContext,
  ResearchDisclosure,
  ResearchEvent,
  ResearchRun,
} from "@/lib/research-types";
import type { StatisticalRunRequest } from "@/lib/types";
import type { NoParameterMessageKey } from "@/lib/i18n-messages";
import { useLanguage } from "@/providers/language-provider";
import { Button } from "./ui/button";
import { DesktopLink } from "./desktop-link";
import { DownloadButton } from "./download-button";
import {
  FormalResearchDraft,
  FormalResearchExecution,
} from "./formal-research-plan";
import {
  ResearchActionNotice,
  ResearchFailure,
  ResearchMoney,
  ResearchStatus,
  ResearchValue,
} from "./formal-research-common";
import { useResearchAction } from "./use-research-action";

export function FormalResearchActivity({
  context,
  runs,
  events,
  verified,
  changed,
  initialRunId,
  onApplyStatistics,
  manualSource,
  readOnly = false,
}: {
  context: ResearchContext;
  runs: ResearchRun[];
  events: ResearchEvent[];
  verified: boolean;
  changed: () => Promise<unknown>;
  initialRunId?: string | null;
  onApplyStatistics?: (request: StatisticalRunRequest) => void;
  manualSource?: { datasetId: string; sha256: string };
  readOnly?: boolean;
}) {
  const { t, formatDate } = useLanguage();
  const references = researchReferences(events, context.session_id);
  const acceptedVersions = events
    .filter(
      (event) =>
        event.event_type === "plan.accepted" &&
        event.entity_kind === "plan_acceptance" &&
        references.acceptanceIds.includes(event.entity_id),
    )
    .flatMap((event) => {
      const request = researchObject(researchDocument(event)?.request);
      const versionId = researchId(request?.version_id),
        planHash = request?.plan_hash,
        sourceVersion = request?.source_version;
      return versionId &&
        typeof planHash === "string" &&
        typeof sourceVersion === "string"
        ? [{ versionId, planHash, sourceVersion }]
        : [];
    });
  const [visibleEvents, setVisibleEvents] = useState(30);
  const timeline = sessionResearchEvents(events, context.session_id);
  return (
    <div className="formal-activity">
      {references.draftIds.map((id) => (
        <FormalResearchDraft
          key={id}
          draftId={id}
          context={context}
          sessionId={context.session_id}
          changed={changed}
          onApplyStatistics={onApplyStatistics}
          manualSource={manualSource}
          readOnly={readOnly}
          acceptedVersions={acceptedVersions}
        />
      ))}
      {references.acceptanceIds.map((id) => (
        <FormalResearchExecution
          key={id}
          acceptanceId={id}
          protocolBound={events.some(
            (event) =>
              event.event_type === "plan.accepted" &&
              event.entity_kind === "plan_acceptance" &&
              event.entity_id === id &&
              researchDocument(event)?.version === 2 &&
              researchObject(researchDocument(event)?.protocol) !== null,
          )}
          workspaceId={context.workspace_id}
          sessionId={context.session_id}
          readOnly={readOnly}
        />
      ))}
      <section aria-label={t("research.runs")}>
        <h3>{t("research.runs")}</h3>
        {runs.length === 0 && (
          <p className="entry-help">{t("research.noRuns")}</p>
        )}
        {runs.map((run) => (
          <ResearchRunRow
            key={run.id}
            run={run}
            events={events}
            verified={verified}
            changed={changed}
            initiallyOpen={run.id === initialRunId || runs.length === 1}
            readOnly={readOnly}
          />
        ))}
      </section>
      <details className="formal-timeline">
        <summary>
          {t("research.timeline")} ({timeline.length})
        </summary>
        <ol>
          {timeline.slice(-visibleEvents).map((event) => (
            <li key={event.sequence}>
              <time dateTime={event.occurred_at}>
                {formatDate(event.occurred_at)}
              </time>
              <span data-no-translate>{event.event_type}</span>
              <details>
                <summary>{t("research.eventDetails")}</summary>
                <code>
                  #{event.sequence} · {event.entity_id}
                </code>
                <ResearchValue value={researchDocument(event)} />
              </details>
            </li>
          ))}
        </ol>
        {visibleEvents < timeline.length && (
          <Button
            variant="ghost"
            onClick={() => setVisibleEvents((value) => value + 50)}
          >
            {t("research.more")}
          </Button>
        )}
      </details>
    </div>
  );
}

function ResearchRunRow({
  run,
  events,
  verified,
  changed,
  initiallyOpen,
  readOnly,
}: {
  run: ResearchRun;
  events: ResearchEvent[];
  verified: boolean;
  changed: () => Promise<unknown>;
  initiallyOpen: boolean;
  readOnly: boolean;
}) {
  const { t } = useLanguage();
  const [open, setOpen] = useState(initiallyOpen);
  const [requested, setRequested] = useState<{
    action: "pause" | "resume" | "cancel";
    attempt: number;
  } | null>(null);
  const action = useResearchAction(`${run.id}:${run.attempt_count}`);
  const attempts = useQuery({
    queryKey: [
      "research",
      run.session_id,
      run.id,
      "attempts",
      run.attempt_count,
    ],
    queryFn: ({ signal }) => researchApi.runs.attempts(run.id, signal),
    enabled: open,
    refetchInterval: open ? 3000 : false,
    retry: false,
  });
  const artifacts = useQuery({
    queryKey: ["research", run.session_id, run.id, "artifacts", run.revision],
    queryFn: ({ signal }) => researchApi.runs.artifacts(run.id, signal),
    enabled: open,
    retry: false,
  });
  const kind =
    run.kind === "reader" || run.kind === "planner" ? run.kind : null;
  const selection = kind ? researchAdmission(events, run.id, kind) : null;
  const approvals = researchApprovals(events, run.id);
  const active = [
    "queued",
    "running",
    "waiting_approval",
    "paused",
    "cancel_requested",
  ].includes(run.state);
  useEffect(() => {
    if (
      requested &&
      (requested.attempt !== run.attempt_count ||
        !active ||
        (requested.action === "pause" && run.state === "paused") ||
        (requested.action === "resume" && run.state === "running"))
    )
      setRequested(null);
  }, [requested, run.state, run.attempt_count, active]);
  function control(next: "pause" | "resume" | "cancel") {
    if (readOnly) return;
    if (kind) {
      action.start(
        (signal, key) =>
          (kind === "reader"
            ? researchApi.runs.readerControl
            : researchApi.runs.planningControl)(
            run.id,
            {
              expected_revision: run.revision,
              action: next,
              idempotency_key: key,
            },
            signal,
          ),
        (value) => {
          setRequested(
            value.pending === false
              ? null
              : { action: next, attempt: run.attempt_count },
          );
          void changed().catch(() => undefined);
        },
        { retryable: true },
      );
    } else {
      action.start(
        (signal) =>
          researchApi.runs[next](
            run.id,
            { expected_revision: run.revision },
            signal,
          ),
        () => {
          setRequested({ action: next, attempt: run.attempt_count });
          void changed().catch(() => undefined);
        },
      );
    }
  }
  const currentAttempt = attempts.data?.find(
    (attempt) => attempt.number === run.attempt_count,
  );
  const unresolvedEvent = events.some(
    (event) =>
      event.event_type === "planning.unresolved_recorded" &&
      event.entity_kind === "run" &&
      event.entity_id === run.id &&
      researchDocument(event)?.attempt_id === currentAttempt?.id,
  );
  const outcome = useQuery({
    queryKey: [
      "research",
      run.session_id,
      run.id,
      "planning-outcome",
      run.revision,
      currentAttempt?.id,
    ],
    queryFn: ({ signal }) => researchApi.runs.planningOutcome(run.id, signal),
    enabled: open && run.kind === "planner" && unresolvedEvent && verified,
    retry: false,
  });
  const awaitingInput =
    outcome.data?.outcome === "awaiting_input" &&
    outcome.data.run_id === run.id &&
    outcome.data.attempt_id === currentAttempt?.id;
  const receipt = [...events]
    .reverse()
    .find(
      (event) =>
        event.event_type === "reader.answer_receipted" &&
        researchDocument(event)?.run_id === run.id &&
        researchDocument(event)?.attempt_id === currentAttempt?.id,
    );
  const sdkFinished = events.some(
    (event) =>
      ["reader.sdk_finished", "planning.sdk_finished"].includes(
        event.event_type,
      ) &&
      researchDocument(event)?.run_id === run.id &&
      researchDocument(event)?.attempt_id === currentAttempt?.id,
  );
  return (
    <details
      className="formal-run"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <span data-no-translate>
          {run.kind} · {run.id.slice(0, 8)}
        </span>{" "}
        <ResearchStatus state={run.state} />
      </summary>
      {open && (
        <>
          <p className="entry-help">
            {t("research.attempt", { number: run.attempt_count })}
          </p>
          {sdkFinished && (
            <p className="entry-help">{t("research.sdkFinished")}</p>
          )}
          {requested && active && (
            <p role="status">{t("research.requested")}</p>
          )}
          <fieldset className="formal-history" disabled={readOnly}>
            <div className="formal-actions">
              {active && (
                <>
                  <Button
                    variant="outline"
                    disabled={
                      action.pending ||
                      action.error != null ||
                      requested !== null ||
                      !["running", "waiting_approval"].includes(run.state)
                    }
                    onClick={() => control("pause")}
                  >
                    {t("research.pause")}
                  </Button>
                  <Button
                    variant="outline"
                    disabled={
                      action.pending ||
                      action.error != null ||
                      requested !== null ||
                      (run.kind === "planner" && attempts.isPending) ||
                      unresolvedEvent ||
                      run.state !== "paused"
                    }
                    onClick={() => control("resume")}
                  >
                    {t("research.resume")}
                  </Button>
                  <Button
                    variant="outline"
                    disabled={
                      action.pending ||
                      action.error != null ||
                      requested !== null ||
                      run.state === "cancel_requested"
                    }
                    onClick={() => control("cancel")}
                  >
                    {t("research.cancel")}
                  </Button>
                </>
              )}
              {!kind &&
                ["quality", "statistics", "ml", "workflow"].includes(
                  run.kind,
                ) &&
                ["failed", "interrupted", "cancelled"].includes(run.state) && (
                  <Button
                    disabled={action.pending || action.error != null}
                    variant="outline"
                    onClick={() =>
                      action.start(
                        (signal, key) =>
                          researchApi.runs.retry(
                            run.id,
                            { expected_revision: run.revision, retry_key: key },
                            signal,
                          ),
                        () => {
                          void changed().catch(() => undefined);
                        },
                        { retryable: true },
                      )
                    }
                  >
                    {t("research.retryRun")}
                  </Button>
                )}
            </div>
          </fieldset>
          {selection && <p data-no-translate>{selection.question}</p>}
          {awaitingInput && (
            <section aria-label={t("research.awaitingInput")}>
              <h4>{t("research.awaitingInput")}</h4>
              <p className="entry-help">{t("research.awaitingInputHelp")}</p>
              <p className="whitespace-pre-wrap break-words" data-no-translate>
                {outcome.data?.advisory}
              </p>
            </section>
          )}
          {outcome.isError && (
            <ResearchFailure
              error={outcome.error}
              retry={() => {
                void outcome.refetch();
              }}
            />
          )}
          {unresolvedEvent && !awaitingInput && !outcome.isError && (
            <p>{t("research.notVerified")}</p>
          )}
          {approvals.map((id) => (
            <fieldset className="formal-history" key={id} disabled={readOnly}>
              <ResearchApprovalRow
                id={id}
                run={run}
                attemptId={currentAttempt?.id}
                selection={selection}
                events={events}
                changed={changed}
              />
            </fieldset>
          ))}
          {attempts.isError && (
            <ResearchFailure
              error={attempts.error}
              retry={() => {
                void attempts.refetch();
              }}
            />
          )}
          {receipt &&
            (verified &&
            run.state === "completed" &&
            currentAttempt?.completion_hash ? (
              <ResearchAnswer event={receipt} />
            ) : (
              <p>{t("research.notVerified")}</p>
            ))}
          {artifacts.isError && (
            <ResearchFailure
              error={artifacts.error}
              retry={() => {
                void artifacts.refetch();
              }}
            />
          )}
          {artifacts.data?.map((artifact) => (
            <ResearchArtifactRow
              key={`${artifact.id}:${artifact.sha256}`}
              artifact={artifact}
            />
          ))}
          <details>
            <summary>{t("research.sourceDetails")}</summary>
            <code>{run.id}</code>
            <p>
              <code>{run.input_hash}</code>
            </p>
            <p>
              <code>{run.completion_hash}</code>
            </p>
            <ResearchValue value={attempts.data ?? []} />
          </details>
          <ResearchActionNotice action={action} reload={changed} />
        </>
      )}
    </details>
  );
}

function ResearchApprovalRow({
  id,
  run,
  attemptId,
  selection,
  events,
  changed,
}: {
  id: string;
  run: ResearchRun;
  attemptId?: string;
  selection: ResearchDisclosure | null;
  events: ResearchEvent[];
  changed: () => Promise<unknown>;
}) {
  const { t, formatBytes, formatDate, locale } = useLanguage();
  const approval = useQuery({
    queryKey: ["research", run.session_id, run.id, "approval", id],
    queryFn: ({ signal }) => researchApi.security.approval(id, signal),
    refetchInterval: 3000,
    retry: false,
  });
  const action = useResearchAction(`${run.id}:${id}`);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const value = approval.data;
  const request = researchModelRequest(
    events,
    run,
    attemptId,
    id,
    value?.request_hash,
  );
  function decide(decision: "approve" | "deny" | "revoke") {
    if (!value) return;
    action.start(
      (signal) =>
        researchApi.security.decide(
          id,
          {
            request_hash: value.request_hash,
            decision,
            ...(selection ? { selection } : {}),
          },
          signal,
        ),
      () => {
        void approval.refetch();
        void changed().catch(() => undefined);
      },
    );
  }
  return (
    <section className="formal-approval">
      {approval.isError && (
        <ResearchFailure
          error={approval.error}
          retry={() => {
            void approval.refetch();
          }}
        />
      )}
      {value && (
        <>
          <div className="formal-section-heading">
            <h4>{t("research.approval")}</h4>
            <ResearchStatus state={value.state} />
          </div>
          <dl className="formal-metadata">
            <dt>{t("research.model")}</dt>
            <dd data-no-translate>
              {value.provider} / {value.model}
            </dd>
            <dt>{t("research.bytes")}</dt>
            <dd>{formatBytes(value.input_bytes)}</dd>
            <dt>{t("research.maxReserve")}</dt>
            <dd>
              <ResearchMoney units={value.reserved_cost_units} />
            </dd>
            <dt>{t("research.expiry")}</dt>
            <dd>{formatDate(new Date(value.expires_at_ms).toISOString())}</dd>
          </dl>
          <p className="entry-help">
            {value.pricing_mode === "unpriced"
              ? locale === "zh-CN"
                ? "费用未估算，token 用量仍会记录。"
                : "Costs are not estimated; token usage is still recorded."
              : value.pricing_mode === "estimated" && !value.price_verified
                ? locale === "zh-CN"
                  ? "费用按填写单价估算，实际账单以服务商为准。"
                  : "Costs use your estimated rates; provider billing applies."
                : t(
                    value.price_verified
                      ? "research.priceVerified"
                      : "research.priceUnverified",
                  )}
          </p>
          <details>
            <summary>{t("research.disclosure")}</summary>
            <p>{t("research.metadataOnly")}</p>
            <ResearchValue value={selection} />
            <ResearchValue value={request?.conversation ?? null} />
            {request?.version === 3 && (
              <>
                <h5>{t("protocol.snapshot")}</h5>
                <ResearchValue value={request.research_protocol} />
                <ResearchValue value={request.tools} />
                <code>{String(request.profile_hash)}</code>
              </>
            )}
            <code>{value.request_hash}</code>
            <br />
            <code>{value.payload_sha256}</code>
          </details>
          {!selection && (
            <p role="alert">{t("research.approvalUnavailable")}</p>
          )}
          <div className="formal-actions">
            <Button
              disabled={
                action.pending ||
                action.error != null ||
                value.state !== "pending" ||
                value.run_id !== run.id ||
                !selection ||
                !request ||
                (!value.price_verified &&
                  (!value.pricing_mode || value.pricing_mode === "reviewed")) ||
                value.expires_at_ms <= now ||
                run.state !== "waiting_approval"
              }
              onClick={() => decide("approve")}
            >
              {t("research.approve")}
            </Button>
            <Button
              variant="outline"
              disabled={
                action.pending ||
                action.error != null ||
                value.state !== "pending"
              }
              onClick={() => decide("deny")}
            >
              {t("research.deny")}
            </Button>
            <Button
              variant="outline"
              disabled={
                action.pending ||
                action.error != null ||
                !["pending", "approved"].includes(value.state)
              }
              onClick={() => decide("revoke")}
            >
              {t("research.revoke")}
            </Button>
          </div>
        </>
      )}
      <ResearchActionNotice
        action={action}
        reload={async () => {
          await approval.refetch({ throwOnError: true });
          return changed();
        }}
      />
    </section>
  );
}

function ResearchAnswer({ event }: { event: ResearchEvent }) {
  const { t } = useLanguage();
  const document = researchDocument(event);
  const answer =
    document?.version === 1 ? researchObject(document.answer) : null;
  if (
    !answer ||
    answer.kind !== "validated_explanation" ||
    answer.computed !== false ||
    !Array.isArray(answer.fields) ||
    !Array.isArray(answer.sources)
  )
    return <p>{t("research.notVerified")}</p>;
  const sources: unknown[] = answer.sources;
  const known = new Set([
    "status",
    "n",
    "statistic",
    "p_value",
    "confidence_interval",
    "group_means",
    "scores",
  ]);
  return (
    <section className="formal-answer">
      <h4>{t("research.answer")}</h4>
      <p className="entry-help">{t("research.verified")}</p>
      {answer.fields.map((item, index) => {
        const field = researchObject(item);
        if (!field) return null;
        const name = typeof field.field === "string" ? field.field : "";
        const citations = Array.isArray(field.citations) ? field.citations : [];
        return (
          <div className="formal-answer-field" key={index}>
            <strong>
              {known.has(name) ? (
                t(`research.field.${name}` as NoParameterMessageKey)
              ) : (
                <span data-no-translate>{name}</span>
              )}
            </strong>
            {field.availability === "unavailable" ? (
              <p>{t("research.unavailableValue")}</p>
            ) : name === "status" && typeof field.value === "string" ? (
              <ResearchStatus state={field.value} />
            ) : (
              <ResearchValue value={field.value} />
            )}
            {known.has(name) && (
              <p>{t(`research.explain.${name}` as NoParameterMessageKey)}</p>
            )}
            <details>
              <summary>{t("research.sourceDetails")}</summary>
              <ResearchValue value={citations} />
              {citations.map((item, number) => {
                const citation = researchObject(item);
                const source = sources.find(
                  (item: unknown) =>
                    researchObject(item)?.dataset_id === citation?.dataset_id,
                );
                const project = researchId(researchObject(source)?.project_id),
                  dataset = researchId(citation?.dataset_id),
                  run = researchId(field.run_id);
                const target =
                  field.kind === "statistics"
                    ? "statistics"
                    : field.kind === "ml"
                      ? "machine-learning"
                      : null;
                return project && dataset && run && target ? (
                  <DesktopLink
                    key={number}
                    className="formal-source-link"
                    href={`/projects/${project}/${target}?datasetId=${dataset}&runId=${run}`}
                  >
                    {t("research.openSource")}
                  </DesktopLink>
                ) : null;
              })}
            </details>
          </div>
        );
      })}
      <details>
        <summary>{t("research.eventDetails")}</summary>
        <ResearchValue value={answer} />
      </details>
    </section>
  );
}

async function artifactBlob(artifact: ResearchArtifact, signal: AbortSignal) {
  const blob = await researchApi.artifacts.content(artifact.id, signal);
  const bytes = await blob.arrayBuffer();
  const digest = [
    ...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
  ]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  if (signal.aborted) throw new DOMException("Read cancelled.", "AbortError");
  if (blob.size !== artifact.byte_size || digest !== artifact.sha256)
    throw new ApiError(409, "Artifact changed.", "RESEARCH_ARTIFACT_CHANGED");
  return blob;
}

function ResearchArtifactRow({ artifact }: { artifact: ResearchArtifact }) {
  const { t, formatBytes, locale } = useLanguage();
  const [show, setShow] = useState(false);
  const content = useQuery({
    queryKey: ["research", "artifact-view", artifact.id, artifact.sha256],
    queryFn: async ({ signal }) => {
      const blob = await artifactBlob(artifact, signal);
      if (artifact.media_type !== "application/json" || blob.size > 1024 * 1024)
        return null;
      return JSON.parse(await blob.text()) as unknown;
    },
    enabled: show,
    retry: false,
  });
  const graph = useQuery({
    queryKey: ["research", "artifact-graph", artifact.id, artifact.sha256],
    queryFn: ({ signal }) => researchApi.artifacts.graph(artifact.id, signal),
    enabled: show,
    retry: false,
  });
  return (
    <div className="formal-artifact">
      <div className="formal-section-heading">
        <span data-no-translate>
          {artifact.kind} · {formatBytes(artifact.byte_size)}
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setShow((value) => !value)}
          aria-expanded={show}
        >
          {t("research.sourceDetails")}
        </Button>
      </div>
      <DownloadButton
        identity={`${artifact.id}:${artifact.sha256}`}
        limitMiB={10}
        className="formal-text-action"
        download={async (signal) => {
          const blob = await artifactBlob(artifact, signal);
          const extension =
            artifact.media_type === "application/json" ? "json" : "bin";
          return saveGeneratedFile(
            blob,
            `${artifact.kind}-${artifact.id}.${extension}`,
            signal,
            async () => {
              await artifactBlob(artifact, signal);
            },
            locale,
          );
        }}
      >
        {t("research.download")}
      </DownloadButton>
      {show && (
        <>
          <code>{artifact.id}</code>
          <br />
          <code>{artifact.sha256}</code>
          {content.isError && (
            <ResearchFailure
              error={content.error}
              retry={() => {
                void content.refetch();
              }}
            />
          )}
          {graph.isError && (
            <ResearchFailure
              error={graph.error}
              retry={() => {
                void graph.refetch();
              }}
            />
          )}
          <ResearchValue value={content.data ?? null} />
          <details>
            <summary>{t("research.sourceDetails")}</summary>
            <ResearchValue value={graph.data ?? null} />
          </details>
        </>
      )}
    </div>
  );
}
