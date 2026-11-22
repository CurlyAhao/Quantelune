import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";

import { AuthGuard } from "@/components/auth-guard";
import { ComingSoon } from "@/components/coming-soon";

export default function DatasetsPage() {
  return (
    <UiAttributes messages={{ label: { id: "ui.data.loadingDatasets" } }}>
      <AuthGuard label={"Loading datasets…"}>
        <div className="mx-auto max-w-4xl px-4 pt-10 pb-20 sm:px-6">
          <div className="animate-rise space-y-1.5">
            <p className="font-mono text-xs tracking-[0.22em] text-primary uppercase">
              {" "}
              <UiMessage id="ui.data.crossProjectIndex" />{" "}
            </p>
            <h1 className="font-heading text-3xl font-bold tracking-tight sm:text-4xl">
              {" "}
              <UiMessage id="ui.data.datasets" />{" "}
            </h1>
          </div>
          <div className="mt-8">
            <UiAttributes
              messages={{
                title: { id: "ui.data.allYourDatasetsOneIndex" },
                description: {
                  id: "ui.data.aSearchableCrossProjectViewOfEveryDatasetYouHaveUploadedSizesSchemasAndReuseCountsIncluded",
                },
              }}
            >
              <ComingSoon
                title={"All your datasets, one index"}
                description={
                  "A searchable, cross-project view of every dataset you have uploaded — sizes, schemas and reuse counts included."
                }
                iconName="database"

                eta="Week 3"
              />
            </UiAttributes>
          </div>
        </div>
      </AuthGuard>
    </UiAttributes>
  );
}
