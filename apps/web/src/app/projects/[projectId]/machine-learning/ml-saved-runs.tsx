"use client";

import { SearchSelect } from "@/components/ui/search-select";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { mlApi } from "@/lib/api-endpoints";

export function MLSavedRuns({
  projectId,
  datasetId,
  sha256,
  onSelect,
}: {
  projectId: string;
  datasetId: string;
  sha256: string | null;
  onSelect: (runId: string) => void;
}) {
  const [experimentId, setExperimentId] = useState("");
  const experiments = useQuery({
    queryKey: ["ml", "saved-experiments", projectId, datasetId],
    queryFn: ({ signal }) => mlApi.listExperiments(datasetId, signal),
  });
  const valid = (experiments.data ?? []).filter(
    (e) =>
      e.project_id === projectId &&
      e.dataset_id === datasetId &&
      e.dataset_sha256 === sha256,
  );
  const selected = valid.find((e) => e.id === experimentId);
  const runs = useQuery({
    queryKey: ["ml", "saved-runs", projectId, datasetId, selected?.id],
    enabled: !!selected,
    queryFn: ({ signal }) => mlApi.listRuns(selected!.id, signal),
  });
  return (
    <details>
      <summary>
        <UiMessage id="ui.ml.loadSavedMlResults" />
      </summary>
      <label>
        {" "}
        <UiMessage id="ui.ml.savedExperiment" />{" "}
        <UiAttributes
          messages={{ "aria-label": { id: "ui.ml.savedMlExperiments" } }}
        >
          <SearchSelect
            aria-label={"保存的 ML 实验"}
            className="max-w-full rounded-md border bg-background px-2 py-1 text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring [color-scheme:light] dark:[color-scheme:dark] [&>option]:bg-background [&>option]:text-foreground"
            value={selected?.id ?? ""}
            onChange={(e) => setExperimentId(e.target.value)}
          >
            <option value="">
              <UiMessage id="ui.ml.selectAnExperimentForTheCurrentSourceVersion" />
            </option>
            {valid.map((e) => (
              <option key={e.id} value={e.id}>
                {e.name} · {e.id}
              </option>
            ))}
          </SearchSelect>
        </UiAttributes>
      </label>
      {(experiments.isError || runs.isError) && (
        <p role="alert">
          <UiMessage id="ui.ml.savedResultsCouldNotBeLoadedCheckTheSourceAndPermissions" />
        </p>
      )}
      {!valid.length && (
        <p>
          <UiMessage id="ui.ml.thereAreNoSavedMlExperimentsForTheCurrentSourceVersion" />
        </p>
      )}
      {selected &&
        runs.data?.runs.map((r) => (
          <button
            className="block underline"
            key={r.id}
            onClick={() => onSelect(r.id)}
          >
            {" "}
            <UiMessage id="ui.ml.loadRun" /> {r.id} · {r.status}
          </button>
        ))}
    </details>
  );
}
