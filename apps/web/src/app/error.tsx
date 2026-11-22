"use client";

import { Message as UiMessage } from "@/components/message";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";

export default function RootError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Unhandled error:", error);
  }, [error]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 px-4 text-center">
      <span className="font-mono text-xs tracking-widest text-muted-foreground uppercase">
        {" "}
        <UiMessage id="ui.common.error" />{" "}
        {error.digest ?? <UiMessage id="ui.profiling.unknown" />}
      </span>
      <h1 className="text-2xl font-bold">
        <UiMessage id="error.unexpected.title" />
      </h1>
      <p className="max-w-md text-sm text-muted-foreground">
        {" "}
        <UiMessage id="ui.common.anUnexpectedErrorOccurredYourDataIsSafeTryAgainOrGoBackToTheDashboard" />{" "}
      </p>
      <div className="flex gap-3">
        <Button onClick={reset}>
          <UiMessage id="ui.common.tryAgain" />
        </Button>
        <Button
          variant="outline"
          onClick={() => (window.location.href = "/dashboard")}
        >
          {" "}
          <UiMessage id="ui.common.goToDashboard" />{" "}
        </Button>
      </div>
    </div>
  );
}
