"use client";

import { useEffect, useRef, useState } from "react";
import { getActorEpoch } from "@/lib/auth-session";

/** A retry reuses the exact closure/key; changing scope abandons only the browser wait. */
export function useResearchAction(identity: string) {
  const scope = useRef(identity);
  const alive = useRef(true);
  const generation = useRef(0);
  // Retire closures at the identity boundary, before a new handler is published.
  // Effect cleanup also runs for StrictMode rehearsal without an identity change.
  if (scope.current !== identity) {
    scope.current = identity;
    generation.current++;
  }
  const operation = useRef<AbortController | null>(null);
  const retryWork = useRef<(() => void) | null>(null);
  const blocked = useRef(false);
  const [state, setState] = useState<{
    identity: string;
    generation: number;
    pending: boolean;
    error: unknown;
    canRetry: boolean;
  }>({ identity, generation: 0, pending: false, error: null, canRetry: false });
  const renderedGeneration = generation.current;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      operation.current?.abort();
      operation.current = null;
      retryWork.current = null;
      blocked.current = false;
    };
  }, [identity]);
  function start<T>(
    request: (signal: AbortSignal, key: string) => Promise<T>,
    accepted: (value: T) => void,
    options: { retryable?: boolean } = {},
  ) {
    if (
      !alive.current ||
      scope.current !== identity ||
      generation.current !== renderedGeneration ||
      operation.current ||
      blocked.current
    )
      return;
    const epoch = getActorEpoch(),
      expected = identity,
      key = crypto.randomUUID(),
      version = generation.current;
    const current = () =>
      alive.current &&
      scope.current === expected &&
      getActorEpoch() === epoch &&
      generation.current === version;
    const run = async () => {
      if (!current() || operation.current) return;
      const controller = new AbortController();
      operation.current = controller;
      setState({
        identity: expected,
        generation: version,
        pending: true,
        error: null,
        canRetry: false,
      });
      try {
        const value = await request(controller.signal, key);
        if (current() && !controller.signal.aborted) {
          retryWork.current = null;
          blocked.current = false;
          accepted(value);
        }
      } catch (failure) {
        if (current() && !controller.signal.aborted) {
          blocked.current = true;
          retryWork.current = options.retryable
            ? () => {
                void run();
              }
            : null;
          setState({
            identity: expected,
            generation: version,
            pending: false,
            error: failure,
            canRetry: Boolean(options.retryable),
          });
        }
      } finally {
        if (operation.current === controller) {
          operation.current = null;
          if (current()) setState((old) => ({ ...old, pending: false }));
        }
      }
    };
    void run();
  }
  return {
    pending:
      state.identity === identity &&
      state.generation === generation.current &&
      state.pending,
    error:
      state.identity === identity && state.generation === generation.current
        ? state.error
        : null,
    canRetry:
      state.identity === identity &&
      state.generation === generation.current &&
      state.canRetry,
    start,
    retry: () => {
      if (
        alive.current &&
        scope.current === identity &&
        generation.current === renderedGeneration
      )
        retryWork.current?.();
    },
    reset: () => {
      if (
        !alive.current ||
        scope.current !== identity ||
        generation.current !== renderedGeneration
      )
        return;
      retryWork.current = null;
      blocked.current = false;
      setState({
        identity,
        generation: generation.current,
        pending: false,
        error: null,
        canRetry: false,
      });
    },
  };
}
