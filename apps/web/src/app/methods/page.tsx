import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";

import { AuthGuard } from "@/components/auth-guard";
import { ComingSoon } from "@/components/coming-soon";

export default function MethodsPage() {
  return (
    <UiAttributes messages={{ label: { id: "ui.common.loadingMethods" } }}>
      <AuthGuard label={"Loading methods…"}>
        <div className="mx-auto max-w-4xl px-4 pt-10 pb-20 sm:px-6">
          <div className="animate-rise space-y-1.5">
            <p className="font-mono text-xs tracking-[0.22em] text-primary uppercase">
              {" "}
              <UiMessage id="ui.common.referenceShelf" />{" "}
            </p>
            <h1 className="font-heading text-3xl font-bold tracking-tight sm:text-4xl">
              {" "}
              <UiMessage id="ui.common.methodLibrary" />{" "}
            </h1>
          </div>
          <div className="mt-8">
            <UiAttributes
              messages={{
                title: { id: "ui.common.everyMethodDocumented" },
                description: {
                  id: "ui.common.referenceDocumentationForEachStatisticalAndMlMethodInTheWorkbenchAssumptionsCaveatsAndCitations",
                },
              }}
            >
              <ComingSoon
                title={"Every method, documented"}
                description={
                  "Reference documentation for each statistical and ML method in the workbench — assumptions, caveats and citations."
                }
                iconName="book-open"

                eta="Week 5"
              />
            </UiAttributes>
          </div>
        </div>
      </AuthGuard>
    </UiAttributes>
  );
}
