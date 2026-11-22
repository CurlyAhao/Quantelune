"use client";
import { useCallback, useEffect, useState, type ComponentProps } from "react";
import { useLanguage } from "@/providers/language-provider";
import { useMLDraft } from "@/lib/use-ml-draft";
import { ResearchFailure } from "@/components/formal-research-common";
import { Button } from "@/components/ui/button";
import { MLBuilder } from "./ml-builder";
import type { MLEditorState, MLExperimentCreateRequest } from "@/lib/types";

export function MLDraftEditor({
  projectId,
  datasetId,
  initialId,
  fresh = false,
  ...props
}: ComponentProps<typeof MLBuilder> & {
  projectId: string;
  datasetId: string;
  initialId?: string | null;
  fresh?: boolean;
}) {
  const { locale } = useLanguage();
  const draft = useMLDraft(
    projectId,
    datasetId,
    props.datasetSha256!,
    initialId,
    props.columns.map((column) => column.name),
    fresh,
  );
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  useEffect(() => {
    if (draft.status === "saved") setFailure(null);
  }, [draft.status]);
  const onChange = props.onConfigChange;
  const changeDraft = draft.change;
  const changed = useCallback(
    (
      config: MLExperimentCreateRequest,
      complete: boolean,
      editor?: MLEditorState,
    ) => {
      if (editor) changeDraft(config, complete, editor);
      onChange?.(config, complete);
    },
    [changeDraft, onChange],
  );
  if (!draft.ready)
    return draft.error ? (
      <ResearchFailure error={draft.error} />
    ) : (
      <p role="status">
        {locale === "zh-CN" ? "打开模型草稿…" : "Opening model draft…"}
      </p>
    );
  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <p role="status" className="text-sm text-muted-foreground">
          {locale === "zh-CN"
            ? draft.status === "saved"
              ? draft.hasSaved
                ? "草稿已保存"
                : "实验参数"
              : draft.status === "unsaved"
                ? "参数未保存"
                : "保存未完成"
            : draft.status === "saved"
              ? draft.hasSaved
                ? "Draft saved"
                : "Experiment parameters"
              : "Parameters not saved"}
        </p>
        <Button
          type="button"
          variant="ghost"
          disabled={props.isRunning || submitting}
          onClick={() => void draft.flush().catch(setFailure)}
        >
          {locale === "zh-CN" ? "保存参数" : "Save parameters"}
        </Button>
      </div>
      {(draft.error || failure) != null && (
        <ResearchFailure error={draft.error || failure} />
      )}
      <fieldset disabled={submitting} className="min-w-0">
        <MLBuilder
          {...props}
          proposal={
            props.proposal ?? (draft.initialEditor ? null : draft.initial)
          }
          editor={draft.initialEditor}
          onConfigChange={changed}
          onSubmit={(config) => {
            setSubmitting(true);
            void draft
              .flush()
              .then(() => {
                setFailure(null);
                props.onSubmit(config);
              })
              .catch(setFailure)
              .finally(() => setSubmitting(false));
          }}
        />
      </fieldset>
    </>
  );
}
