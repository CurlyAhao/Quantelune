"use client";

import type { ResearchProviderState } from "@/lib/research-types";
import { ModelConnectionsPanel } from "./model-connections";

/** Reuse the same optional-pricing connection editor at every formal entry. */
export function ResearchProviderPanel(props: {
  state?: ResearchProviderState;
  loading: boolean;
  error: unknown;
  changed: () => Promise<unknown>;
}) {
  return <ModelConnectionsPanel onChanged={props.changed} />;
}
