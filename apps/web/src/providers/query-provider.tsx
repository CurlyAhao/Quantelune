"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useAuthStore } from "@/stores/auth-store";

export function QueryProvider({ children }: { children: React.ReactNode }) {
  const actorId = useAuthStore((state) => state.user?.id ?? "anonymous");
  const epoch = useAuthStore((state) => state.actorEpoch);
  return <ActorQueries key={`${actorId}:${epoch}`}>{children}</ActorQueries>;
}

/** An account transition remounts both its query cache and all page-local state. */
function ActorQueries({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60 * 1000,
            retry: 1,
          },
        },
      }),
  );

  useEffect(
    () => () => {
      void queryClient.cancelQueries();
      queryClient.clear();
    },
    [queryClient],
  );

  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}
