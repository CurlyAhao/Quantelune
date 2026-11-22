import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import type { StatisticalRun } from "@/lib/types";

import {
  inputParameters,
  isRecord,
  safeAggregate,
  storedText,
} from "./report-projection";

function Field({
  label,
  value,
  userLabel = false,
}: {
  label: string;
  value: unknown;
  userLabel?: boolean;
}) {
  return (
    <div className="grid gap-1 border-b py-2 sm:grid-cols-[12rem_1fr]">
      <dt
        className="text-sm text-muted-foreground"
        translate={userLabel ? "no" : undefined}
      >
        {label}
      </dt>
      <dd className="min-w-0 break-words font-mono text-sm" translate="no">
        {storedText(value)}
      </dd>
    </div>
  );
}

function SavedFields({ title, value }: { title: string; value: unknown }) {
  const safe = safeAggregate(value);
  const entries = isRecord(safe) ? Object.entries(safe) : [];
  return (
    <section aria-label={title} className="rounded-lg border p-4">
      <h4 className="mb-2 font-semibold">{title}</h4>
      {entries.length ? (
        <dl>
          {entries.map(([key, item]) => (
            <Field key={key} label={key} value={item} userLabel />
          ))}
        </dl>
      ) : (
        <p className="text-sm text-muted-foreground">
          <UiMessage id="ui.report.notRecorded" />
        </p>
      )}
    </section>
  );
}

export function SavedReport({ run }: { run: StatisticalRun }) {
  const result = run.result;
  if (!result) return null;
  return (
    <UiAttributes
      messages={{ "aria-label": { id: "ui.report.savedStatisticalReport" } }}
    >
      <article aria-label={"Saved statistical report"} className="space-y-5">
        <p className="text-xs text-muted-foreground">
          {" "}
          <UiMessage id="ui.report.storedApiValuesAreShownWithoutDisplayRoundingThisViewDoesNotRerunTheAnalysis" />{" "}
        </p>
        <section className="rounded-lg border p-4">
          <h4 className="mb-2 font-semibold">
            <UiMessage id="ui.report.provenanceAndParameters" />
          </h4>
          <dl>
            <UiAttributes messages={{ label: { id: "ui.report.runId" } }}>
              <Field label={"Run ID"} value={run.id} />
            </UiAttributes>
            <UiAttributes messages={{ label: { id: "ui.report.datasetId" } }}>
              <Field label={"Dataset ID"} value={run.dataset_id} />
            </UiAttributes>
            <UiAttributes
              messages={{ label: { id: "ui.report.datasetSha256" } }}
            >
              <Field label={"Dataset SHA-256"} value={run.dataset_sha256} />
            </UiAttributes>
            <UiAttributes
              messages={{ label: { id: "workflow.parameter.method" } }}
            >
              <Field label={"Method"} value={run.method} />
            </UiAttributes>
            <UiAttributes
              messages={{ label: { id: "ui.report.methodsetVersion" } }}
            >
              <Field
                label={"Methodset version"}
                value={run.methodset_version}
              />
            </UiAttributes>
            <UiAttributes
              messages={{ label: { id: "ui.report.methodVersion" } }}
            >
              <Field label={"Method version"} value={result.method_version} />
            </UiAttributes>
            <UiAttributes messages={{ label: { id: "ui.projects.created" } }}>
              <Field label={"Created"} value={run.created_at} />
            </UiAttributes>
            <UiAttributes messages={{ label: { id: "ui.report.started" } }}>
              <Field label={"Started"} value={run.started_at} />
            </UiAttributes>
            <UiAttributes messages={{ label: { id: "ui.report.completed" } }}>
              <Field label={"Completed"} value={run.completed_at} />
            </UiAttributes>
            <UiAttributes
              messages={{ label: { id: "ui.report.missingPolicy" } }}
            >
              <Field
                label={"Missing policy"}
                value={run.missing_value_policy}
              />
            </UiAttributes>
            <UiAttributes
              messages={{ label: { id: "ui.report.storedInputParameters" } }}
            >
              <Field
                label={"Stored input parameters"}

                value={inputParameters(run.input_config)}
              />
            </UiAttributes>
            <UiAttributes messages={{ label: { id: "ui.report.sampleSize" } }}>
              <Field label={"Sample size"} value={run.sample_size} />
            </UiAttributes>
            <UiAttributes messages={{ label: { id: "ui.report.originalN" } }}>
              <Field label={"Original n"} value={result.sample?.original_n} />
            </UiAttributes>
            <UiAttributes messages={{ label: { id: "ui.report.excludedN" } }}>
              <Field label={"Excluded n"} value={result.sample?.excluded_n} />
            </UiAttributes>
            <UiAttributes messages={{ label: { id: "ui.report.finalN" } }}>
              <Field label={"Final n"} value={result.sample?.final_n} />
            </UiAttributes>
            <UiAttributes
              messages={{ label: { id: "ui.report.exclusionReason" } }}
            >
              <Field label={"Exclusion reason"} value={result.sample?.reason} />
            </UiAttributes>
            <UiAttributes
              messages={{
                label: { id: "ui.report.storedPerVariableMissingCounts" },
              }}
            >
              <Field
                label={"Stored per-variable missing counts"}
                value={result.sample?.per_variable}
              />
            </UiAttributes>
          </dl>
        </section>
        <div className="grid gap-4 lg:grid-cols-2">
          <UiAttributes messages={{ title: { id: "ui.report.estimates" } }}>
            <SavedFields title={"Estimates"} value={result.estimates} />
          </UiAttributes>
          <UiAttributes messages={{ title: { id: "ui.report.statistic" } }}>
            <SavedFields title={"Statistic"} value={result.statistic} />
          </UiAttributes>
          <UiAttributes
            messages={{ title: { id: "ui.report.confidenceInterval" } }}
          >
            <SavedFields
              title={"Confidence interval"}

              value={result.confidence_interval}
            />
          </UiAttributes>
          <UiAttributes messages={{ title: { id: "ui.report.effectSize" } }}>
            <SavedFields title={"Effect size"} value={result.effect_size} />
          </UiAttributes>
          <UiAttributes
            messages={{ title: { id: "ui.report.descriptiveSummaries" } }}
          >
            <SavedFields
              title={"Descriptive summaries"}

              value={result.descriptive_statistics}
            />
          </UiAttributes>
          <UiAttributes
            messages={{ title: { id: "ui.report.softwareVersions" } }}
          >
            <SavedFields
              title={"Software versions"}

              value={result.software_versions}
            />
          </UiAttributes>
        </div>
        <section className="rounded-lg border p-4">
          <h4 className="font-semibold">
            <UiMessage id="ui.report.inferenceMetadata" />
          </h4>
          <dl>
            <UiAttributes messages={{ label: { id: "ui.report.pValue" } }}>
              <Field label={"p-value"} value={result.p_value} />
            </UiAttributes>
            <UiAttributes
              messages={{ label: { id: "workflow.parameter.alternative" } }}
            >
              <Field label={"Alternative"} value={result.alternative} />
            </UiAttributes>
            <UiAttributes
              messages={{ label: { id: "ui.report.confidenceLevel" } }}
            >
              <Field
                label={"Confidence level"}
                value={result.confidence_level}
              />
            </UiAttributes>
          </dl>
        </section>
        <section className="rounded-lg border p-4">
          <h4 className="mb-2 font-semibold">
            <UiMessage id="ui.report.assumptionChecks" />
          </h4>
          {result.assumption_checks?.length ? (
            <ul className="space-y-2 text-sm">
              {result.assumption_checks.map((check, index) => (
                <li
                  key={`${check.name}-${index}`}
                  className="border-b pb-2"
                  translate="no"
                >
                  {check.name}:{" "}
                  {check.passed ? (
                    <UiMessage id="ui.quality.passed.qualityOverview" />
                  ) : (
                    <UiMessage id="ui.displayBranch.report.notPassed" />
                  )}
                  . {check.detail}
                  {check.value !== null && (
                    <>
                      {" "}
                      <UiMessage id="ui.report.value" />{" "}
                      {storedText(check.value)}.
                    </>
                  )}
                  {check.threshold !== null && (
                    <>
                      {" "}
                      <UiMessage id="ui.report.threshold" />{" "}
                      {storedText(check.threshold)}.
                    </>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">
              <UiMessage id="ui.report.notRecorded" />
            </p>
          )}
        </section>
        <div className="grid gap-4 lg:grid-cols-2">
          <section className="rounded-lg border p-4">
            <h4 className="mb-2 font-semibold">
              <UiMessage id="ui.quality.warnings" />
            </h4>
            {result.warnings?.length ? (
              <ul className="list-disc pl-5 text-sm">
                {result.warnings.map((item, index) => (
                  <li key={index} translate="no">
                    {item}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">
                <UiMessage id="ui.report.noneRecorded" />
              </p>
            )}
          </section>
          <section className="rounded-lg border p-4">
            <h4 className="mb-2 font-semibold">
              <UiMessage id="ui.quality.limitations" />
            </h4>
            {result.limitations?.length ? (
              <ul className="list-disc pl-5 text-sm">
                {result.limitations.map((item, index) => (
                  <li key={index} translate="no">
                    {item}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">
                <UiMessage id="ui.report.noneRecorded" />
              </p>
            )}
          </section>
        </div>
      </article>
    </UiAttributes>
  );
}
