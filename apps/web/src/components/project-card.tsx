"use client";

import { useLanguage as useDomainLanguage } from "@/providers/language-provider";
import { localizedMetaLabel } from "@/lib/format";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { ChevronRightIcon } from "lucide-react";

import { DesktopLink } from "@/components/desktop-link";
import { StatusBadge } from "@/components/status-badge";
import { PROJECT_STATUS_META, taskTypeLabel } from "@/lib/format";
import type { Project } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ProjectCardProps {
  project: Project;
  /** Stagger index for the reveal animation. */
  index?: number;
}

export function ProjectCard({ project, index = 0 }: ProjectCardProps) {
  const { t: uiLabelT } = useDomainLanguage();

  const { formatDate } = useLocaleDisplay();

  const status = PROJECT_STATUS_META[project.status];

  return (
    <DesktopLink
      href={`/projects/${project.id}/workspace`}
      style={{ animationDelay: `${Math.min(index, 8) * 60}ms` }}
      className={cn(
        "group animate-rise relative flex flex-col gap-3 rounded-lg border bg-card p-4 shadow-plate",
        "transition-all duration-200 outline-none",
        "hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-plate-lg",
        "focus-visible:ring-3 focus-visible:ring-ring/50",
      )}
    >
      {/* Corner tick — instrument plate detail */}
      <span
        aria-hidden
        className="absolute top-0 right-0 size-4 rounded-tr-lg border-t-2 border-r-2 border-transparent transition-colors duration-200 group-hover:border-accent"
      />

      <div className="flex items-center justify-between gap-2">
        <StatusBadge
          label={localizedMetaLabel(status, uiLabelT)}
          tone={status.tone}
          pulse={project.status === "active"}
        />
        <span className="rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[0.65rem] tracking-wide text-muted-foreground">
          {taskTypeLabel(project.task_type, uiLabelT)}
        </span>
      </div>

      <div className="min-w-0">
        <h3 className="truncate font-heading text-lg leading-snug font-semibold tracking-tight">
          {project.title}
        </h3>
        <p className="mt-1 line-clamp-2 min-h-10 text-sm text-muted-foreground">
          {project.description ?? project.research_question ?? (
            <UiMessage id="ui.common.noDescriptionYetOpenTheProjectToAddOne" />
          )}
        </p>
      </div>

      <div className="mt-auto flex items-center justify-between border-t border-dashed pt-3">
        <div className="flex items-center gap-3 font-mono text-[0.7rem] text-muted-foreground">
          <span className="rounded-sm bg-primary/8 px-1.5 py-0.5 font-semibold text-primary">
            {" "}
            <UiMessage id="ui.common.rev" /> {project.current_revision}
          </span>
          <UiAttributes
            messages={{
              "aria-label": {
                id: "ui.common.updatedValue",
                values: { value0: String(formatDate(project.updated_at)) },
              },
            }}
          >
            <span
              aria-label={"Updated {value0}".replaceAll(
                "{value0}",
                String(formatDate(project.updated_at)),
              )}
            >
              {formatDate(project.updated_at)}
            </span>
          </UiAttributes>
        </div>
        <ChevronRightIcon className="size-4 text-muted-foreground/50 transition-transform duration-200 group-hover:translate-x-0.5 group-hover:text-primary" />
      </div>
    </DesktopLink>
  );
}
