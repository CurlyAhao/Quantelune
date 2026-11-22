"use client";

import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import type { IconName } from "@/lib/icon-registry";

import { AuthGuard } from "@/components/auth-guard";
import { ComingSoon } from "@/components/coming-soon";
import { ProjectShell } from "@/components/project-shell";

interface SectionPlaceholderProps {
  projectId: string;
  iconName: IconName;
  title: string;
  description: string;
  eta: string;
}

/** Auth-guarded project section that hasn't shipped yet. */
export function SectionPlaceholder({
  projectId,
  iconName,
  title,
  description,
  eta,
}: SectionPlaceholderProps) {
  return (
    <UiAttributes
      messages={{
        label: {
          id: "ui.common.loadingValue",
          values: { value0: String(title.toLowerCase()) },
        },
      }}
    >
      <AuthGuard
        label={"Loading {value0}…".replaceAll(
          "{value0}",
          String(title.toLowerCase()),
        )}
      >
        <ProjectShell projectId={projectId}>
          <ComingSoon
            iconName={iconName}
            title={title}
            description={description}
            eta={eta}
          />
        </ProjectShell>
      </AuthGuard>
    </UiAttributes>
  );
}
