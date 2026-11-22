"use client";
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { ActivityIcon } from "lucide-react";
import { projectRunsApi } from "@/lib/analysis-jobs";
import { useLanguage } from "@/providers/language-provider";
import { DesktopLink } from "./desktop-link";

export function ProjectRunLink({
  projectId,
  active,
}: {
  projectId: string;
  active: boolean;
}) {
  const { locale } = useLanguage();
  const query = useQuery({
    queryKey: ["project-run-count", projectId],
    queryFn: ({ signal }) =>
      projectRunsApi.list(projectId, { state: "active", limit: 1 }, signal),
    refetchInterval: 4000,
    retry: false,
  });
  const refetch = query.refetch;
  useEffect(() => {
    const started = (event: Event) => {
      if ((event as CustomEvent).detail?.projectId === projectId)
        void refetch();
    };
    window.addEventListener("statforge:run-started", started);
    return () => window.removeEventListener("statforge:run-started", started);
  }, [projectId, refetch]);
  return (
    <DesktopLink
      className="project-run-link"
      href={`/projects/${projectId}/runs`}
      aria-current={active ? "page" : undefined}
    >
      <ActivityIcon className="size-3.5" />
      {locale === "zh-CN" ? "运行" : "Runs"}
      {!!query.data?.active_total && (
        <span className="run-live-count">{query.data.active_total}</span>
      )}
    </DesktopLink>
  );
}
