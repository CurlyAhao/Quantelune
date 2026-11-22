"use client";

import { Message as UiMessage } from "@/components/message";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";

export default function ProjectError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Project section error:", error);
  }, [error]);

  return (
    <div className="flex min-h-[40vh] flex-col items-center justify-center gap-4 px-4 text-center">
      <h2 className="text-xl font-bold">
        <UiMessage id="ui.projects.sectionError" />
      </h2>
      <p className="max-w-md text-sm text-muted-foreground">
        {" "}
        <UiMessage id="ui.projects.thisSectionEncounteredAnErrorOtherPartsOfStatforgeAreStillWorkingNormally" />{" "}
      </p>
      <div className="flex gap-3">
        <Button onClick={reset}>
          <UiMessage id="common.retry" />
        </Button>
        <Button
          variant="outline"
          onClick={() => (window.location.href = "/projects")}
        >
          {" "}
          <UiMessage id="ui.projects.backToProjects" />{" "}
        </Button>
      </div>
    </div>
  );
}
