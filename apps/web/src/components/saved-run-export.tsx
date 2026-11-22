"use client";

import { useLayoutEffect, useRef } from "react";
import { DownloadButton } from "@/components/download-button";
import { datasetsApi, statisticsApi } from "@/lib/api-endpoints";
import { saveGeneratedFile } from "@/lib/api-download";
import { assertActorEpoch, getActorEpoch } from "@/lib/auth-session";
import {
  assertSavedRunSource,
  savedRunCsv,
  savedRunHtml,
} from "@/lib/saved-run-output";
import type { Dataset, StatisticalRun } from "@/lib/types";
import { useLanguage } from "@/providers/language-provider";

export function SavedRunExport({
  projectId,
  dataset,
  run,
  selectionIdentity,
  ticket,
}: {
  projectId: string;
  dataset: Dataset;
  run: StatisticalRun;
  selectionIdentity: string;
  ticket: () => () => boolean;
}) {
  const { locale, t } = useLanguage();
  const datasetId = dataset.id,
    runId = run.id;
  const sourceSnapshot = JSON.stringify([dataset, run]);
  const identity = JSON.stringify([
    projectId,
    sourceSnapshot,
    selectionIdentity,
    locale,
  ]);
  const generation = useRef(0);
  useLayoutEffect(() => {
    generation.current += 1;
    return () => {
      generation.current += 1;
    };
  }, [identity]);

  async function download(format: "html" | "csv", signal: AbortSignal) {
    const started = generation.current,
      epoch = getActorEpoch(),
      selected = ticket();
    const current = () => {
      assertActorEpoch(epoch);
      if (signal.aborted || generation.current !== started || !selected())
        throw new DOMException("Saved Run selection changed.", "AbortError");
    };
    const readSource = async () => {
      current();
      const [freshDataset, freshRun] = await Promise.all([
        datasetsApi.get(datasetId, signal),
        statisticsApi.getRun(runId, signal),
      ]);
      current();
      assertSavedRunSource(projectId, freshDataset, freshRun);
      if (JSON.stringify([freshDataset, freshRun]) !== sourceSnapshot)
        throw new Error("Saved Run source changed.");
      return { freshDataset, freshRun };
    };
    const { freshDataset, freshRun } = await readSource();
    const contents =
      format === "html"
        ? savedRunHtml(projectId, freshDataset, freshRun, locale)
        : savedRunCsv(projectId, freshDataset, freshRun);
    const blob = new Blob([contents], {
      type:
        format === "html"
          ? "text/html;charset=utf-8"
          : "text/csv;charset=utf-8",
    });
    current();
    const outcome = await saveGeneratedFile(
      blob,
      `statforge-run-${runId}.${format}`,
      signal,
      async () => {
        await readSource();
      },
      locale,
    );
    current();
    return outcome;
  }

  try {
    assertSavedRunSource(projectId, dataset, run);
  } catch {
    return null;
  }
  return (
    <div
      role="group"
      aria-label={t("research.output.single")}
      className="flex flex-wrap items-center gap-3"
    >
      {(["html", "csv"] as const).map((format) => (
        <DownloadButton
          key={format}
          identity={identity}
          download={(signal) => download(format, signal)}
          limitMiB={8}
          className="rounded-md border bg-background px-3 py-2 text-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
          handoffMessage={t("research.reportHandoff")}
        >
          {t("ui.research.export")} {format.toUpperCase()}
        </DownloadButton>
      ))}
    </div>
  );
}
