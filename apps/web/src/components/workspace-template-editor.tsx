"use client";

import { Message as UiMessage } from "@/components/message";
import { useLanguage } from "@/providers/language-provider";
import { derivedDisplayTitle } from "@/lib/derived-display-title";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "./ui/button";
import { getActorEpoch } from "@/lib/auth-session";
import {
  workspaceApi,
  type TemplateBody,
  type WorkspaceRecord,
} from "@/lib/workspace-api";

export function WorkspaceTemplateEditor({
  projectId,
  templateId,
  onSaved,
}: {
  projectId: string;
  templateId: string;
  onSaved: () => void;
}) {
  const { t } = useLanguage();
  const [record, setRecord] = useState<WorkspaceRecord<TemplateBody> | null>(
    null,
  );
  const [title, setTitle] = useState("");
  const [question, setQuestion] = useState("");
  const [text, setText] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<React.ReactNode>("");
  const generation = useRef(0);
  const invalidate = useCallback(() => {
    generation.current++;
  }, []);
  useEffect(() => {
    const controller = new AbortController(),
      value = ++generation.current;
    setRecord(null);
    setDirty(false);
    setBusy(true);
    setError("");
    void workspaceApi
      .read(templateId, controller.signal)
      .then((row) => {
        if (controller.signal.aborted || value !== generation.current) return;
        if (row.project_id !== projectId || row.payload.kind !== "template")
          throw new Error();
        const saved = row as WorkspaceRecord<TemplateBody>;
        setRecord(saved);
        setTitle(saved.payload.title);
        setQuestion(saved.payload.research_question);
        setText(
          JSON.stringify(
            {
              required_columns: saved.payload.required_columns,
              steps: saved.payload.steps,
            },
            null,
            2,
          ),
        );
      })
      .catch(() => {
        if (!controller.signal.aborted && value === generation.current)
          setError(
            <UiMessage id="ui.residual.workspace.templateUnavailable" />,
          );
      })
      .finally(() => {
        if (!controller.signal.aborted && value === generation.current)
          setBusy(false);
      });
    return () => {
      invalidate();
      controller.abort();
    };
  }, [projectId, templateId, invalidate]);

  async function save(copy: boolean) {
    if (!record) return;
    const value = generation.current,
      actor = getActorEpoch();
    const current = () =>
      value === generation.current && actor === getActorEpoch();
    setBusy(true);
    setError("");
    try {
      const config = JSON.parse(text) as Pick<
        TemplateBody,
        "steps" | "required_columns"
      >;
      if (Object.keys(config).sort().join(",") !== "required_columns,steps")
        throw new Error();
      const payload: TemplateBody = {
        kind: "template",
        schema_version: 1,
        title: copy ? derivedDisplayTitle(title, "copy", t) : title,
        research_question: question,
        required_columns: config.required_columns,
        steps: config.steps,
      };
      const saved = await workspaceApi.save(
        projectId,
        payload,
        copy ? undefined : record,
      );
      if (current()) {
        if (!copy) {
          setRecord(saved as WorkspaceRecord<TemplateBody>);
          setDirty(false);
        }
        onSaved();
      }
    } catch {
      if (current())
        setError(
          <UiMessage id="ui.residual.workspace.templateNotSavedResultsRetained" />,
        );
    } finally {
      if (current()) setBusy(false);
    }
  }
  return (
    <details className="space-y-3">
      <summary>
        <UiMessage id="ui.workspace.editCopySelectedTemplate" />
      </summary>
      <p className="text-xs text-muted-foreground">
        {" "}
        <UiMessage id="ui.workspace.templatesSaveConfigurationAndColumnTypesEachUseRequiresRebindingDataAndApprovingANewPlan" />{" "}
      </p>
      {record && (
        <>
          <p>
            <UiMessage id="ui.workspace.templateR" />
            {record.revision}
          </p>
          <label className="block">
            {" "}
            <UiMessage id="ui.workspace.templateName" />{" "}
            <input
              className="workspace-input w-full"
              value={title}
              maxLength={160}
              disabled={busy}
              onChange={(e) => {
                setTitle(e.target.value);
                setDirty(true);
              }}
            />
          </label>
          <label className="block">
            {" "}
            <UiMessage id="ui.preview.researchQuestion" />{" "}
            <textarea
              className="workspace-input w-full"
              value={question}
              maxLength={1000}
              disabled={busy}
              onChange={(e) => {
                setQuestion(e.target.value);
                setDirty(true);
              }}
            />
          </label>
          <label className="block">
            {" "}
            <UiMessage id="ui.workspace.requiredColumnTypesAndSteps" />{" "}
            <textarea
              className="workspace-input w-full font-mono text-xs"
              rows={12}
              value={text}
              maxLength={24000}
              disabled={busy}
              onChange={(e) => {
                setText(e.target.value);
                setDirty(true);
              }}
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              disabled={busy || !dirty}
              onClick={() => void save(false)}
            >
              {" "}
              <UiMessage id="ui.workspace.validateAndSaveTemplateRevision" />{" "}
            </Button>
            <Button size="sm" disabled={busy} onClick={() => void save(true)}>
              {" "}
              <UiMessage id="ui.workspace.saveATemplateCopy" />{" "}
            </Button>
          </div>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </details>
  );
}
