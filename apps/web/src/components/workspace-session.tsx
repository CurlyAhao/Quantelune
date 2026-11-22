"use client";

import { useLanguage as usePropertyLanguage } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useCallback, useEffect, useRef, useState } from "react";
import { PreviewTurnPanel } from "./preview-turn-panel";
import { PreviewEvidence } from "./preview-evidence";
import { WorkspacePlanPanel } from "./workspace-plan-panel";
import { Button } from "./ui/button";
import { getActorEpoch } from "@/lib/auth-session";
import {
  sessionSourceKey,
  type PreviewScope,
  type PreviewTurn,
} from "@/lib/preview-types";
import {
  workspaceApi,
  type SessionBody,
  type WorkspaceRecord,
} from "@/lib/workspace-api";
import type { StatisticalRunRequest } from "@/lib/types";

const labels = {
  confirmed_information: "已确认信息",
  personal_preferences: "个人偏好",
  project_rules: "项目规则",
  task_requirements: "当前任务要求",
} as const;
type Note = keyof typeof labels;
const baseScope = (scope: PreviewScope): PreviewScope => ({
  ...scope,
  session_id: null,
  session_revision: null,
});

export function WorkspaceSession({
  scope,
  identity,
  onApplyStatistics,
}: {
  scope: PreviewScope;
  identity: string;
  onApplyStatistics: (value: StatisticalRunRequest) => void;
}) {
  const { t: propertyT } = usePropertyLanguage();

  const [session, setSession] = useState<WorkspaceRecord<SessionBody> | null>(
    null,
  );
  const [notes, setNotes] = useState<Record<Note, string>>({
    confirmed_information: "",
    personal_preferences: "",
    project_rules: "",
    task_requirements: "",
  });
  const [dirty, setDirty] = useState(false),
    [pending, setPending] = useState(true),
    [error, setError] = useState<React.ReactNode>("");
  const [last, setLast] = useState<{
    turn: PreviewTurn;
    scope: PreviewScope;
    question: string;
  } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const generation = useRef(0),
    record = useRef(session);
  record.current = session;
  const scopeValue = useRef(scope);
  scopeValue.current = scope;
  const binding = sessionSourceKey(scope);
  const invalidate = useCallback(() => {
    generation.current++;
  }, []);
  useEffect(() => {
    const controller = new AbortController(),
      ticket = ++generation.current;
    setSession(null);
    setLast(null);
    setNotes({
      confirmed_information: "",
      personal_preferences: "",
      project_rules: "",
      task_requirements: "",
    });
    setDirty(false);
    setPending(true);
    setError("");
    void (async () => {
      const items = await workspaceApi.list(
        scopeValue.current.project_id,
        "session",
        controller.signal,
      );
      for (const item of items.filter((x) => x.available)) {
        const row = await workspaceApi.read(item.id, controller.signal);
        if (
          row.payload.kind === "session" &&
          sessionSourceKey(row.payload.scope) === binding
        ) {
          if (ticket !== generation.current || controller.signal.aborted)
            return;
          const value = row as WorkspaceRecord<SessionBody>;
          setSession(value);
          setNotes(
            Object.fromEntries(
              Object.keys(labels).map((k) => [k, value.payload[k as Note]]),
            ) as Record<Note, string>,
          );
          return;
        }
      }
      if (ticket === generation.current)
        setNotes({
          confirmed_information: "",
          personal_preferences: "",
          project_rules: "",
          task_requirements: "",
        });
    })()
      .catch(() => {
        if (!controller.signal.aborted)
          setError(
            <UiMessage id="ui.residual.workspace.sessionSourceUnavailable" />,
          );
      })
      .finally(() => {
        if (ticket === generation.current && !controller.signal.aborted)
          setPending(false);
      });
    return () => {
      controller.abort();
      invalidate();
    };
  }, [binding, identity, invalidate]);

  async function save() {
    const ticket = generation.current,
      actor = getActorEpoch();
    setPending(true);
    setError("");
    try {
      const prior = record.current;
      const payload: SessionBody = prior
        ? { ...prior.payload, ...notes }
        : {
            kind: "session",
            schema_version: 1,
            title: propertyT("ui.workspace.researchSession"),
            scope: baseScope(scopeValue.current),
            messages: [],
            ...notes,
          };
      const saved = await workspaceApi.save(
        scopeValue.current.project_id,
        payload,
        prior ?? undefined,
      );
      if (ticket === generation.current && actor === getActorEpoch()) {
        setSession(saved as WorkspaceRecord<SessionBody>);
        setDirty(false);
      }
    } catch {
      if (ticket === generation.current)
        setError(<UiMessage id="ui.residual.workspace.sessionNotSaved" />);
    } finally {
      if (ticket === generation.current) setPending(false);
    }
  }
  const currentScope: PreviewScope = session
    ? { ...scope, session_id: session.id, session_revision: session.revision }
    : scope;
  return (
    <div className="space-y-4">
      <details>
        <summary>
          <UiMessage id="ui.workspace.sessionInformationAndResearchRules" />
        </summary>
        <p className="text-xs text-muted-foreground">
          {" "}
          <UiMessage id="ui.workspace.afterSavingApproveEgressForEachRequestASourceChangeOpensASeparateSession" />{" "}
        </p>
        {Object.entries(labels).map(([key, label]) => (
          <label className="block" key={key}>
            {label}
            <textarea
              className="workspace-input mt-1 w-full"
              maxLength={key === "confirmed_information" ? 2000 : 1000}
              value={notes[key as Note]}
              onChange={(e) => {
                setNotes({ ...notes, [key]: e.target.value });
                setDirty(true);
              }}
            />
          </label>
        ))}
      </details>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          disabled={pending || (!!session && !dirty)}
          onClick={() => void save()}
        >
          {session ? (
            <UiMessage id="ui.displayBranch.workspace.saveSessionInfo" />
          ) : (
            <UiMessage id="ui.displayBranch.workspace.createResearchSession" />
          )}
        </Button>
        <span className="text-xs">
          {session ? (
            <UiMessage
              id="ui.workspace.sessionRevisionValueValueSavedQuestions"
              values={{
                value0: String(session.revision),
                value1: String(session.payload.messages.length),
              }}
            />
          ) : (
            <UiMessage id="ui.displayBranch.workspace.continuousSessionMissing" />
          )}
        </span>
      </div>
      {error && (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      )}
      {session && (
        <details>
          <summary>
            <UiMessage id="ui.workspace.savedQuestions" />
          </summary>
          <ol className="space-y-2">
            {session.payload.messages.map((message) => (
              <li key={message.request_id}>
                {message.question}
                <small className="block break-all">
                  {" "}
                  <UiMessage id="ui.workspace.request" /> {message.request_id}
                </small>
              </li>
            ))}
          </ol>
        </details>
      )}
      {!pending && !dirty && (
        <PreviewTurnPanel
          scope={currentScope}
          identity={identity}
          onApplyStatistics={onApplyStatistics}
          onCompleted={async (value) => {
            if (value.turn.status !== "finished") return;
            const prior = record.current,
              ticket = generation.current,
              actor = getActorEpoch();
            setLast(value);
            setRefresh((x) => x + 1);
            if (
              !prior ||
              prior.payload.messages.some(
                (x) => x.request_id === value.requestId,
              )
            )
              return;
            const saved = await workspaceApi.save(
              scope.project_id,
              {
                ...prior.payload,
                messages: [
                  ...prior.payload.messages,
                  {
                    request_id: value.requestId,
                    question: value.question,
                    scope: value.scope,
                    turn_id: value.turn.turn_id,
                  },
                ].slice(-12),
              },
              prior,
            );
            if (ticket === generation.current && actor === getActorEpoch())
              setSession(saved as WorkspaceRecord<SessionBody>);
          }}
        />
      )}
      {last && (
        <UiAttributes
          messages={{
            "aria-label": { id: "ui.workspace.savedResponseForThisTurn" },
          }}
        >
          <section aria-label={"本轮保存答复"}>
            <p className="font-medium">{last.question}</p>
            <p>
              <UiMessage id="ui.workspace.session" /> {last.turn.status}
              <UiMessage id="ui.workspace.seeTheSourceForBusinessStatus" />
            </p>
            <PreviewEvidence
              evidence={last.turn.evidence}
              scope={last.scope}
              onApplyStatistics={onApplyStatistics}
            />
          </section>
        </UiAttributes>
      )}
      <WorkspacePlanPanel
        scope={baseScope(scope)}
        identity={identity}
        refresh={refresh}
      />
    </div>
  );
}
