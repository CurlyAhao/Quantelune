import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";

import { SectionPlaceholder } from "@/components/section-placeholder";

interface PageProps {
  params: Promise<{ projectId: string }>;
}

export default async function ProjectEvaluationPage({ params }: PageProps) {
  const { projectId } = await params;
  return (
    <UiAttributes
      messages={{
        title: { id: "ui.projects.modelEvaluation" },
        description: {
          id: "ui.projects.metricsCalibrationCurvesAndErrorAnalysisSideBySideForEveryCandidateModelInTheProject",
        },
      }}
    >
      <SectionPlaceholder
        title={"Model Evaluation"}
        description={
          "Metrics, calibration curves and error analysis side by side for every candidate model in the project."
        }
        projectId={projectId}
        iconName="file-check"

        eta="Week 5"
      />
    </UiAttributes>
  );
}
