"use client";

import { useLanguage as useArrayLanguage } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { AlertTriangleIcon, ArrowRightIcon } from "lucide-react";

import { AuthGuard } from "@/components/auth-guard";
import { DesktopLink } from "@/components/desktop-link";
import { ProjectShell } from "@/components/project-shell";

interface AgentClientProps {
  projectId: string;
}

const _WORKBENCH_LINKS = [
  {
    href: "data",
    label: "ui.projects.data",
    description: "ui.projects.uploadAndInspectADataset",
  },
  {
    href: "profiling",
    label: "ui.projects.profiling",
    description: "ui.projects.computeSchemaMissingnessAndCorrelations",
  },
  {
    href: "quality",
    label: "ui.projects.quality",
    description: "ui.projects.runDeterministicDataQualityRules",
  },
  {
    href: "statistics",
    label: "ui.projects.statistics",
    description: "ui.projects.configureAndRunStatisticalMethods",
  },
  {
    href: "machine-learning",
    label: "ui.projects.machineLearning",
    description:
      "ui.projects.configureAMachineLearningExperimentInTheWorkbench",
  },
  {
    href: "workflow",
    label: "ui.projects.workflow",
    description: "ui.projects.editAndValidateAnAnalysisWorkflow",
  },
] as const;

export default function AgentClient({ projectId }: AgentClientProps) {
  const { t: arrayT } = useArrayLanguage();
  const WORKBENCH_LINKS = _WORKBENCH_LINKS.map((item) => ({
    ...item,
    label: arrayT(item.label),
    description: arrayT(item.description),
  }));

  return (
    <UiAttributes messages={{ label: { id: "ui.projects.loadingAgent" } }}>
      <AuthGuard label={"Loading Agent…"}>
        <ProjectShell projectId={projectId}>
          <main className="mx-auto max-w-3xl space-y-6">
            <section className="rounded-lg border border-amber-500/40 bg-amber-50 p-5 text-amber-950 dark:bg-amber-950/30 dark:text-amber-100">
              <div className="flex gap-3">
                <AlertTriangleIcon
                  className="mt-0.5 size-5 shrink-0"
                  aria-hidden="true"
                />
                <div className="space-y-2">
                  <h1 className="text-lg font-semibold">
                    {" "}
                    <UiMessage id="ui.projects.restrictedAgentPreview" />{" "}
                  </h1>
                  <p className="text-sm">
                    {" "}
                    <UiMessage id="ui.projects.reviewTheCurrentProjectContextInTheRightWorkbenchConfigureModelsAndCostsThroughTheSecureSettingsEntryPointAndApproveEachEgressRequestStatisticsAndMlPagesCanUseCurrentDataFormsAndActualResultReferencesManualAnalysisRemainsAvailableWithoutConfiguration" />{" "}
                  </p>
                  <p className="text-sm">
                    {" "}
                    <UiMessage id="ui.projects.thisIsARestrictedLocalPreviewModelCallsAndProviderBillingAreUnverifiedUntilASeparatelyApprovedRealIntegrationTestItDoesNotConstituteGateCOrReleaseApproval" />{" "}
                  </p>
                </div>
              </div>
            </section>

            <section className="space-y-2">
              <h2 className="text-base font-semibold">
                {" "}
                <UiMessage id="ui.projects.useTheExistingWorkbenches" />{" "}
              </h2>
              <p className="text-sm text-muted-foreground">
                {" "}
                <UiMessage id="ui.projects.thesePagesProvideTheCurrentlyVerifiableManualWorkflows" />{" "}
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                {WORKBENCH_LINKS.map((item) => (
                  <DesktopLink
                    key={item.href}
                    href={`/projects/${projectId}/${item.href}`}
                    className="group rounded-lg border bg-card p-4 transition-colors hover:bg-muted"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <h3 className="font-medium">{item.label}</h3>
                      <ArrowRightIcon
                        className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5"
                        aria-hidden="true"
                      />
                    </div>
                    <p className="mt-2 text-sm text-muted-foreground">
                      {item.description}
                    </p>
                  </DesktopLink>
                ))}
              </div>
            </section>
          </main>
        </ProjectShell>
      </AuthGuard>
    </UiAttributes>
  );
}
