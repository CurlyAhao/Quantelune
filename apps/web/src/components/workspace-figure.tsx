"use client";

import { useLanguage as usePropertyLanguage } from "@/providers/language-provider";

import { Message as UiMessage } from "@/components/message";
import { useState } from "react";
import { useLanguage } from "@/providers/language-provider";

import type {
  FigureData,
  FigureSpec,
  WorkspaceRecord,
} from "@/lib/workspace-api";
import type { StatisticalRun } from "@/lib/types";
import { figureSvg } from "@/lib/workspace-figure";
import {
  DescriptiveTable,
  ResearchResultTable,
} from "@/app/projects/[projectId]/statistics/statistics-result";
import {
  descriptiveTable,
  researchPresentation,
  type ResultTable,
} from "@/lib/research-results";

export function WorkspaceFigure({
  spec,
  run,
  snapshot,
}: {
  spec: FigureSpec;
  run: StatisticalRun;
  snapshot?: WorkspaceRecord<FigureData> | null;
}) {
  const { t: propertyT } = usePropertyLanguage();

  const { locale } = useLanguage();
  const [showPoints, setShowPoints] = useState(false);
  let svg: string;
  try {
    svg = figureSvg(spec, run, snapshot, locale);
  } catch (error) {
    return (
      <div role="alert">
        <p>{error instanceof Error ? error.message : <UiMessage id="ui.displayBranch.figure.verifyRunGroupsIntervals" />}</p>
      </div>
    );
  }
  return (
    <figure>
      <div
        dangerouslySetInnerHTML={{ __html: svg }}
        className="workspace-figure"
      />
      <figcaption className="workspace-note">
        {spec.caption} ·{" "}
        {spec.schema_version === 1 ? (
          <UiMessage id="ui.displayBranch.figure.groupPointsDifferenceIntervals" />
        ) : snapshot?.payload.chart_type === "correlation_scatter" ? (
          <UiMessage
            id="ui.workspace.showingValueValueCompletePairsSeedValueNoAdditionalFittingOrReEstimation"
            values={{
              value0: String(snapshot.payload.displayed_n),
              value1: String(snapshot.payload.n),
              value2: String(snapshot.payload.seed),
            }}
          />
        ) : snapshot?.payload.chart_type === "distribution" ? (
          <UiMessage id="ui.displayBranch.figure.fullDistributionCappedOutliers" />
        ) : (
          <UiMessage id="ui.displayBranch.figure.savedOlsIntervals" />
        )}{" "}
      </figcaption>
      {spec.schema_version === 1 ? (
        <DescriptiveTable
          descriptive={run.result?.descriptive_statistics ?? {}}
        />
      ) : spec.chart_type === "distribution" ? (
        <ResearchResultTable
          table={descriptiveTable(
            run.result?.descriptive_statistics ?? {},
            run.result?.sample?.per_variable,
            locale,
          )}
        />
      ) : spec.chart_type === "ols_coefficients" && run.result ? (
        researchPresentation(run.result, run.input_config, locale).tables.map(
          (table, i) => <ResearchResultTable key={i} table={table} />,
        )
      ) : (
        snapshot && (
          <details
            onToggle={(event) => setShowPoints(event.currentTarget.open)}
          >
            <summary>
              {" "}
              <UiMessage id="ui.workspace.equivalentTableSavedDisplayPointCoordinates" />
              {snapshot.payload.displayed_n}/{snapshot.payload.n}{" "}
              <UiMessage id="ui.workspace.pairs" />{" "}
            </summary>
            {showPoints && (
              <ResearchResultTable
                table={{
                  caption: propertyT(
                    "ui.residual.research.savedPointsRowOrder",
                  ),
                  columns: [
                    {
                      key: "row_index",
                      label: propertyT("ui.workspace.originalRowIndex"),
                      kind: "count",
                    },
                    { key: "x", label: spec.fields[0] },
                    { key: "y", label: spec.fields[1] },
                  ],
                  rows: snapshot.payload.data.points as ResultTable["rows"],
                }}
              />
            )}
          </details>
        )
      )}
    </figure>
  );
}
