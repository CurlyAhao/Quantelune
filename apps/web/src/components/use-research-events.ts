"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth-store";
import { ApiError } from "@/lib/api-client";
import { researchApi } from "@/lib/research-api";
import {
  appendResearchEvents,
  type ResearchEventFeed,
} from "@/lib/research-view";

export function useResearchEvents(workspaceId: string) {
  const epoch = useAuthStore((state) => state.actorEpoch);
  const client = useQueryClient();
  const queryKey = ["research", epoch, workspaceId, "events"];
  return useQuery({
    queryKey,
    queryFn: async ({ signal }) => {
      const previous = client.getQueryData<ResearchEventFeed>(queryKey) ?? {
        rows: [],
        cursor: 0,
        more: false,
      };
      const page = await researchApi.events.list(
        workspaceId,
        previous.cursor,
        100,
        signal,
      );
      try {
        return appendResearchEvents(previous, page, 100);
      } catch {
        throw new ApiError(
          409,
          "Event sequence unavailable.",
          "RESEARCH_EVENT_SEQUENCE_UNAVAILABLE",
        );
      }
    },
    refetchInterval: 3000,
    retry: false,
    staleTime: 0,
  });
}
