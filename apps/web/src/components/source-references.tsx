"use client";
import { Message as UiMessage } from "@/components/message";
import { useEffect, useState } from "react";
import { materialApi } from "@/lib/material-api";
import type { Citation } from "@/lib/material-types";
import type { PreviewScope } from "@/lib/preview-types";
import { DesktopLink } from "./desktop-link";

function Source({ saved, scope }: { saved: Citation; scope: PreviewScope }) {
  const [current, setCurrent] = useState<Citation | null>(null);
  const [error, setError] = useState(false);
  const allowed =
    saved.project_id === scope.project_id &&
    scope.material_references?.some(
      (r) =>
        r.citation_id === saved.id &&
        r.revision === saved.revision &&
        r.citation_sha256 === saved.citation_sha256,
    );
  useEffect(() => {
    const controller = new AbortController();
    setCurrent(null);
    setError(false);
    if (allowed)
      void materialApi
        .citation(saved.material_id, saved.id, controller.signal)
        .then((value) => {
          if (controller.signal.aborted) return;
          if (
            !value.reviewed ||
            value.project_id !== scope.project_id ||
            value.citation_sha256 !== saved.citation_sha256 ||
            value.revision !== saved.revision
          )
            throw new Error();
          setCurrent(value);
        })
        .catch(() => {
          if (!controller.signal.aborted) setError(true);
        });
    return () => controller.abort();
  }, [
    allowed,
    saved.id,
    saved.material_id,
    saved.citation_sha256,
    saved.revision,
    scope.project_id,
  ]);
  if (!allowed || error)
    return (
      <p role="alert">
        <UiMessage id="ui.common.theMaterialCitationIsUnavailableOrItsVersionHasChanged" />
      </p>
    );
  if (!current)
    return (
      <p>
        <UiMessage id="ui.common.checkingActualMaterialCitations" />
      </p>
    );
  const l = current.locator;
  return (
    <div className="my-2 rounded border p-2">
      <p>
        {" "}
        <UiMessage id="ui.common.source" /> {current.filename} · v
        {current.material_version}/r
        {current.revision}
      </p>
      {l.kind === "text" ? (
        <>
          <blockquote className="whitespace-pre-wrap">{l.quote}</blockquote>
          <p>
            {" "}
            <UiMessage id="ui.common.page" />
            {l.page}
            <UiMessage id="ui.common.codePoints" />
            {l.start}, {l.end})
          </p>
        </>
      ) : l.kind === "region" ? (
        <p>
          {" "}
          <UiMessage id="ui.common.manualRegion" />
          {l.x}, {l.y}, {l.width}, {l.height}；{l.note}
        </p>
      ) : (
        <p>
          {" "}
          <UiMessage id="ui.common.manualTimePosition" />
          {l.start_seconds}–{l.end_seconds} <UiMessage id="ui.common.seconds" />
          {l.note}
        </p>
      )}
      <p className="break-all text-xs">
        <UiMessage id="ui.common.materialSha" />
        {current.material_sha256}
      </p>
      <DesktopLink
        className="underline"
        href={`/projects/${scope.project_id}/materials?materialId=${current.material_id}&citationId=${current.id}`}
      >
        {" "}
        <UiMessage id="ui.common.locateOriginalMaterialTextOrAManualPosition" />{" "}
      </DesktopLink>
      <p className="text-xs">
        {" "}
        <UiMessage id="ui.common.manuallyConfirmedSourceNoOcrTranscriptionOrMediaUnderstanding" />{" "}
        {current.location_status === "unknown_candidate" ? (
          <UiMessage id="ui.displayBranch.materials.videoLocationCandidate" />
        ) : (
          ""
        )}
      </p>
    </div>
  );
}
export function SourceReferences({
  citations,
  scope,
}: {
  citations: Citation[];
  scope: PreviewScope;
}) {
  return (
    <section>
      <h3>
        <UiMessage id="ui.common.materialSourcesApprovedForThisRequest" />
      </h3>
      {citations.slice(0, 5).map((c) => (
        <Source key={c.id} saved={c} scope={scope} />
      ))}
    </section>
  );
}
