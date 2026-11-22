"use client";

import { Message as UiMessage } from "@/components/message";
import { useLanguage } from "@/providers/language-provider";
import { getIcon, type IconName } from "@/lib/icon-registry";

interface ComingSoonProps {
  iconName: IconName;
  title: string;
  description: string;
  eta: string;
}

/** Placeholder panel for modules arriving in later weeks. */
export function ComingSoon({
  iconName,
  title,
  description,
  eta,
}: ComingSoonProps) {
  const { t, formatNumber } = useLanguage();
  const week = /^Week (\d+)$/.exec(eta);
  const Icon = getIcon(iconName);
  return (
    <div className="animate-rise relative overflow-hidden rounded-lg border border-dashed bg-card/60">
      <div aria-hidden className="bg-grid-paper absolute inset-0 opacity-70" />
      <div className="relative flex flex-col items-center gap-4 px-6 py-16 text-center sm:py-20">
        <span className="flex size-14 items-center justify-center rounded-lg border bg-card shadow-plate">
          <Icon className="size-6 text-primary" />
        </span>
        <p className="font-mono text-[0.65rem] tracking-[0.25em] text-accent-foreground uppercase">
          {" "}
          <UiMessage id="ui.common.modulePendingShips" />{" "}
          {week
            ? t("common.week", { count: formatNumber(Number(week[1])) })
            : eta}
        </p>
        <h2 className="font-heading text-2xl font-bold tracking-tight">
          {title}
        </h2>
        <p className="max-w-md text-sm leading-relaxed text-muted-foreground">
          {description}
        </p>
      </div>
    </div>
  );
}
