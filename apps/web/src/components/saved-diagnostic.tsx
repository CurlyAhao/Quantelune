"use client";
import type { ReactNode } from "react";
import { Message } from "./message";

/** Saved source prose remains verbatim and is labelled, never auto-translated. */
export function SavedDiagnostic({
  children,
  open = false,
}: {
  children: ReactNode;
  open?: boolean;
}) {
  return (
    <details open={open} className="mt-2 text-xs text-muted-foreground">
      <summary>
        <Message id="diagnostic.original" />
      </summary>
      <div className="mt-1 whitespace-pre-wrap break-words">{children}</div>
    </details>
  );
}
