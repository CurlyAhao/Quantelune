import { Message as UiMessage } from "@/components/message";
import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 px-4 text-center">
      <span className="font-mono text-6xl font-bold text-primary/20">404</span>
      <h1 className="text-2xl font-bold">
        <UiMessage id="ui.common.pageNotFound" />
      </h1>
      <p className="max-w-md text-sm text-muted-foreground">
        {" "}
        <UiMessage id="ui.common.thePageYouAreLookingForDoesNotExistOrHasBeenMoved" />{" "}
      </p>
      <Link
        href="/dashboard"
        className="inline-flex h-9 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
      >
        {" "}
        <UiMessage id="ui.common.goToDashboard" />{" "}
      </Link>
    </div>
  );
}
