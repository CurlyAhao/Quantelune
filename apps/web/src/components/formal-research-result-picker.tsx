"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  mlApi,
  qualityApi,
  statisticsApi,
  workflowApi,
} from "@/lib/api-endpoints";
import type { ResearchContext } from "@/lib/research-types";
import { useLanguage } from "@/providers/language-provider";
import { Button } from "./ui/button";
import { ResearchFailure } from "./formal-research-common";

type Kind = "statistics" | "quality" | "ml" | "workflow";

/** Lists saved results only. Final source/bytes/SHA qualification belongs to adoption. */
export function ResearchResultPicker({
  kind,
  context,
  disabled,
  value,
  onSelect,
}: {
  kind: Kind;
  context: ResearchContext;
  disabled: boolean;
  value: string;
  onSelect: (id: string) => void;
}) {
  const { t } = useLanguage();
  const [datasetId, setDatasetId] = useState(
    context.datasets[0]?.dataset_id ?? "",
  );
  const dataset = context.datasets.find(
    (item) => item.dataset_id === datasetId,
  );
  return (
    <div className="formal-result-picker">
      <label>
        {t("research.dataset")}
        <SearchSelect
          value={dataset?.dataset_id ?? ""}
          disabled={disabled}
          onChange={(event) => {
            setDatasetId(event.target.value);
            onSelect("");
          }}
        >
          <option value="">{t("research.chooseDataset")}</option>
          {context.datasets.map((item) => (
            <option key={item.dataset_id} value={item.dataset_id}>
              {item.dataset_id}
            </option>
          ))}
        </SearchSelect>
      </label>
      {dataset && (
        <SavedRunList
          key={`${kind}:${dataset.dataset_id}`}
          kind={kind}
          datasetId={dataset.dataset_id}
          projectId={dataset.project_id}
          disabled={disabled}
          value={value}
          onSelect={onSelect}
        />
      )}
    </div>
  );
}

function SavedRunList({
  kind,
  datasetId,
  projectId,
  disabled,
  value,
  onSelect,
}: {
  kind: Kind;
  datasetId: string;
  projectId: string;
  disabled: boolean;
  value: string;
  onSelect: (id: string) => void;
}) {
  const { t } = useLanguage();
  const [page, setPage] = useState(1);
  const [containerId, setContainerId] = useState("");
  const experiments = useQuery({
    queryKey: ["research", "saved-experiments", datasetId],
    queryFn: ({ signal }) => mlApi.listExperiments(datasetId, signal),
    enabled: kind === "ml",
    retry: false,
  });
  const workflows = useQuery({
    queryKey: ["research", "saved-workflows", projectId],
    queryFn: ({ signal }) => workflowApi.list(projectId, signal),
    enabled: kind === "workflow",
    retry: false,
  });
  const container =
    kind === "ml"
      ? experiments.data?.find(
          (item) => item.id === containerId && item.dataset_id === datasetId,
        )?.id
      : workflows.data?.find((item) => item.id === containerId)?.id;
  const runs = useQuery({
    queryKey: [
      "research",
      "saved-domain-runs",
      kind,
      datasetId,
      container ?? "",
      page,
    ],
    queryFn: async ({ signal }) => {
      if (kind === "statistics") {
        const result = await statisticsApi.listRuns(
          datasetId,
          { page, pageSize: 50 },
          signal,
        );
        return {
          ...result,
          runs: result.runs
            .filter(
              (item) =>
                item.dataset_id === datasetId && item.status === "completed",
            )
            .map((item) => ({
              id: item.id,
              label: `${item.method} · ${item.id}`,
            })),
        };
      }
      if (kind === "quality") {
        const result = await qualityApi.listRuns(datasetId, signal);
        return {
          ...result,
          runs: result.runs
            .filter(
              (item) =>
                item.dataset_id === datasetId && item.status === "completed",
            )
            .map((item) => ({ id: item.id, label: item.id })),
        };
      }
      if (!container) throw new Error("Saved result identity unavailable.");
      const result =
        kind === "ml"
          ? await mlApi.listRuns(container, signal)
          : await workflowApi.listRuns(container, signal);
      return {
        ...result,
        runs: result.runs
          .filter((item) => item.status === "completed")
          .map((item) => ({ id: item.id, label: item.id })),
      };
    },
    enabled: kind === "statistics" || kind === "quality" || Boolean(container),
    retry: false,
  });
  return (
    <>
      {(kind === "ml" || kind === "workflow") && (
        <label>
          {t("research.experiment")}
          <SearchSelect
            value={container ?? ""}
            disabled={disabled}
            onChange={(event) => {
              setContainerId(event.target.value);
              onSelect("");
            }}
          >
            <option value="">{t("research.experiment")}</option>
            {kind === "ml"
              ? experiments.data
                  ?.filter((item) => item.dataset_id === datasetId)
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.id}
                    </option>
                  ))
              : workflows.data?.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {item.id}
                  </option>
                ))}
          </SearchSelect>
        </label>
      )}
      <label>
        {t("research.savedResult")}
        <SearchSelect
          value={runs.data?.runs.some((item) => item.id === value) ? value : ""}
          disabled={disabled || runs.isFetching}
          onChange={(event) => onSelect(event.target.value)}
        >
          <option value="">{t("research.chooseResult")}</option>
          {runs.data?.runs.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </SearchSelect>
      </label>
      {kind === "statistics" && (
        <div className="formal-actions">
          <span className="entry-help">
            {t("research.resultPage", { page })}
          </span>
          <Button
            variant="ghost"
            disabled={disabled || page <= 1 || runs.isFetching}
            onClick={() => {
              setPage((old) => old - 1);
              onSelect("");
            }}
          >
            {t("research.previousPage")}
          </Button>
          <Button
            variant="ghost"
            disabled={
              disabled ||
              !runs.data ||
              page * 50 >= runs.data.total ||
              runs.isFetching
            }
            onClick={() => {
              setPage((old) => old + 1);
              onSelect("");
            }}
          >
            {t("research.nextPage")}
          </Button>
        </div>
      )}
      {runs.isError && (
        <ResearchFailure
          error={runs.error}
          retry={() => {
            void runs.refetch();
          }}
        />
      )}
      {experiments.isError && (
        <ResearchFailure
          error={experiments.error}
          retry={() => {
            void experiments.refetch();
          }}
        />
      )}
      {workflows.isError && (
        <ResearchFailure
          error={workflows.error}
          retry={() => {
            void workflows.refetch();
          }}
        />
      )}
    </>
  );
}
