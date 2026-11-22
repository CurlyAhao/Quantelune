"use client";

import { Message as UiMessage } from "@/components/message";
import { useEffect, useState } from "react";
import { materialApi } from "@/lib/material-api";
import {
  reference,
  type Citation,
  type CitationReference,
} from "@/lib/material-types";
import { DesktopLink } from "./desktop-link";

export function MaterialPicker({
  projectId,
  value,
  onChange,
}: {
  projectId: string;
  value: CitationReference[];
  onChange: (refs: CitationReference[]) => void;
}) {
  const [citations, setCitations] = useState<Citation[]>([]);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState<React.ReactNode>("");
  const identity = JSON.stringify(value);
  useEffect(() => {
    const controller = new AbortController();
    setCitations([]);
    void materialApi
      .citations(projectId, controller.signal)
      .then((rows) => {
        if (controller.signal.aborted) return;
        setCitations(
          Array.isArray(rows)
            ? rows.filter((c) => c.reviewed && c.project_id === projectId)
            : [],
        );
        setError("");
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setError(
            <UiMessage id="ui.residual.materials.currentReferenceUnavailable" />,
          );
      });
    return () => controller.abort();
  }, [projectId, refresh, identity]);
  return (
    <details className="mb-3 text-xs">
      <summary>
        <UiMessage id="ui.materials.selectMaterialCitationsForThisPageUpTo5ShortExcerpts" />
      </summary>
      <p>
        <UiMessage id="ui.materials.selectOnlyManuallyConfirmedSourcesApproveEgressAnewEachTurnAndReviewAgainWhenTheSourceOrVersionChanges" />
      </p>
      {citations.map((citation) => (
        <label className="my-2 block" key={citation.id}>
          <input
            type="checkbox"
            checked={value.some((r) => r.citation_id === citation.id)}
            disabled={
              value.length >= 5 &&
              !value.some((r) => r.citation_id === citation.id)
            }
            onChange={(e) =>
              onChange(
                e.target.checked
                  ? [
                      ...value.filter((r) => r.citation_id !== citation.id),
                      reference(citation),
                    ]
                  : value.filter((r) => r.citation_id !== citation.id),
              )
            }
          />{" "}
          <UiMessage id="ui.materials.citation" /> {citation.filename} · v
          {citation.material_version}/r
          {citation.revision} ·{" "}
          {citation.locator.kind === "text" ? (
            Array.from(citation.locator.quote).slice(0, 80).join("")
          ) : (
            <UiMessage id="ui.displayBranch.materials.manualLocationNote" />
          )}
        </label>
      ))}
      {value.some(
        (r) =>
          !citations.some(
            (c) =>
              c.id === r.citation_id &&
              c.revision === r.revision &&
              c.citation_sha256 === r.citation_sha256,
          ),
      ) && (
        <p role="alert">
          <UiMessage id="ui.materials.aSelectedCitationVersionIsUnavailableClearAndReviewTheSelectionsAgain" />
        </p>
      )}
      <button
        type="button"
        className="mr-3 underline"
        onClick={() => {
          onChange([]);
          setRefresh((r) => r + 1);
        }}
      >
        {" "}
        <UiMessage id="ui.materials.clearAndRefreshMaterialCitations" />{" "}
      </button>
      <DesktopLink
        className="underline"
        href={`/projects/${projectId}/materials`}
      >
        {" "}
        <UiMessage id="ui.materials.inspectConfirmAndLocateMaterials" />{" "}
      </DesktopLink>
      {error && <p role="alert">{error}</p>}
    </details>
  );
}
