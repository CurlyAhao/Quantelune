import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";

import { AuthGuard } from "@/components/auth-guard";
import { ComingSoon } from "@/components/coming-soon";

export default function TemplatesPage() {
  return (
    <UiAttributes messages={{ label: { id: "ui.common.loadingTemplates" } }}>
      <AuthGuard label={"Loading templates…"}>
        <div className="mx-auto max-w-4xl px-4 pt-10 pb-20 sm:px-6">
          <div className="animate-rise space-y-1.5">
            <p className="font-mono text-xs tracking-[0.22em] text-primary uppercase">
              {" "}
              <UiMessage id="ui.common.startingPoints" />{" "}
            </p>
            <h1 className="font-heading text-3xl font-bold tracking-tight sm:text-4xl">
              {" "}
              <UiMessage id="ui.common.templates" />{" "}
            </h1>
          </div>
          <div className="mt-8">
            <UiAttributes
              messages={{
                title: { id: "ui.common.startFromAProvenTemplate" },
                description: {
                  id: "ui.common.preWiredProjectsForCommonStudiesABTestsCohortAnalysesBenchmarkSuitesInsteadOfABlankBench",
                },
              }}
            >
              <ComingSoon
                title={"Start from a proven template"}
                description={
                  "Pre-wired projects for common studies — A/B tests, cohort analyses, benchmark suites — instead of a blank bench."
                }
                iconName="layers"

                eta="Week 4"
              />
            </UiAttributes>
          </div>
        </div>
      </AuthGuard>
    </UiAttributes>
  );
}
