"use client";

import { useLanguage as useCopyLanguage } from "@/providers/language-provider";
import { useLanguage as useDomainLanguage } from "@/providers/language-provider";
import { localizedMetaLabel } from "@/lib/format";
import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { qualityRuleName } from "@/lib/quality-rule-labels";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  FilterXIcon,
  InboxIcon,
  SearchIcon,
} from "lucide-react";

import { ErrorState } from "@/components/error-state";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { SearchSelect } from "@/components/ui/search-select";
import { Spinner } from "@/components/ui/spinner";
import { QualityFindingDetail } from "./quality-finding-detail";
import {
  formatObserved,
  formatThreshold,
  qualityCategoryLabel,
} from "./quality-shared";
import { qualityApi } from "@/lib/api-endpoints";
import {
  FINDING_SEVERITY_META,
  FINDING_STATUS_META,
  friendlyApiError,
  severityColor,
} from "@/lib/format";
import type {
  FindingCategory,
  FindingSeverity,
  FindingStatus,
  QualityFinding,
  QualityRun,
} from "@/lib/types";

const FINDINGS_PAGE_SIZE = 50;
const ALL = "all";

const SEVERITY_OPTIONS: FindingSeverity[] = [
  "info",
  "low",
  "medium",
  "high",
  "critical",
];
const STATUS_OPTIONS: FindingStatus[] = [
  "passed",
  "warning",
  "failed",
  "skipped",
  "error",
];
const CATEGORY_OPTIONS: FindingCategory[] = [
  "structure",
  "completeness",
  "uniqueness",
  "validity",
  "consistency",
  "temporal",
  "distribution",
  "domain",
];

interface QualityFindingsTabProps {
  run: QualityRun;
}

export function QualityFindingsTab({ run }: QualityFindingsTabProps) {
  const { t: uiCopyT } = useCopyLanguage();

  const { t: uiLabelT } = useDomainLanguage();

  const { formatNumber } = useLocaleDisplay();

  const [severity, setSeverity] = useState<string>(ALL);
  const [category, setCategory] = useState<string>(ALL);
  const [status, setStatus] = useState<string>(ALL);
  const [columnInput, setColumnInput] = useState("");
  const column = useDebouncedValue(columnInput.trim(), 350);
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<QualityFinding | null>(null);

  const findingsQuery = useQuery({
    queryKey: [
      "quality",
      "findings",
      run.id,
      severity,
      category,
      status,
      column,
      page,
    ],
    queryFn: ({ signal }) =>
      qualityApi.listFindings(
        run.id,
        {
          severity: severity !== ALL ? severity : undefined,
          category: category !== ALL ? category : undefined,
          status: status !== ALL ? status : undefined,
          column: column || undefined,
          page,
          pageSize: FINDINGS_PAGE_SIZE,
        },
        signal,
      ),
    placeholderData: keepPreviousData,
  });

  // Any filter change restarts pagination.
  useEffect(() => {
    setPage(1);
  }, [severity, category, status, column]);

  // Keyword search is client-side, over the fetched page.
  const rows = useMemo(() => {
    const items = findingsQuery.data?.findings ?? [];
    const kw = keyword.trim().toLowerCase();
    if (!kw) return items;
    return items.filter((f) =>
      `${qualityRuleName(f.rule_id, f.rule_version, uiLabelT) ?? ""} ${f.rule_id} ${f.title} ${f.summary ?? ""} ${f.rule_name}`
        .toLowerCase()
        .includes(kw),
    );
  }, [findingsQuery.data, keyword, uiLabelT]);

  const total = findingsQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / FINDINGS_PAGE_SIZE));
  const hasAnyFilter =
    severity !== ALL ||
    category !== ALL ||
    status !== ALL ||
    column !== "" ||
    keyword.trim() !== "";

  function clearFilters() {
    setSeverity(ALL);
    setCategory(ALL);
    setStatus(ALL);
    setColumnInput("");
    setKeyword("");
  }

  return (
    <div className="space-y-4">
      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2">
        <UiAttributes messages={{ "aria-label": { id: "ui.quality.filterBySeverity" } }}><SearchSelect aria-label="Filter by severity" className="w-32" value={severity} onChange={event => setSeverity(event.target.value)}>
            <option value={ALL}>
              <UiMessage id="ui.quality.allSeverities" />
            </option>
            {SEVERITY_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {localizedMetaLabel(FINDING_SEVERITY_META[s], uiLabelT)}
              </option>
            ))}
          </SearchSelect></UiAttributes>

        <UiAttributes messages={{ "aria-label": { id: "ui.quality.filterByCategory" } }}><SearchSelect aria-label="Filter by category" className="w-36" value={category} onChange={event => setCategory(event.target.value)}>
            <option value={ALL}>
              <UiMessage id="ui.quality.allCategories" />
            </option>
            {CATEGORY_OPTIONS.map((c) => (
              <option key={c} value={c}>
                {qualityCategoryLabel(c, uiLabelT)}
              </option>
            ))}
          </SearchSelect></UiAttributes>

        <UiAttributes messages={{ "aria-label": { id: "ui.quality.filterByStatus" } }}><SearchSelect aria-label="Filter by status" className="w-32" value={status} onChange={event => setStatus(event.target.value)}>
            <option value={ALL}>
              <UiMessage id="ui.quality.allStatuses" />
            </option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {localizedMetaLabel(FINDING_STATUS_META[s], uiLabelT)}
              </option>
            ))}
          </SearchSelect></UiAttributes>

        <UiAttributes
          messages={{
            placeholder: { id: "ui.quality.columnName" },
            "aria-label": { id: "ui.quality.filterByAffectedColumn" },
          }}
        >
          <Input
            placeholder={"Column name…"}
            aria-label={"Filter by affected column"}
            value={columnInput}
            onChange={(e) => setColumnInput(e.target.value)}

            className="w-36 font-mono text-xs"
          />
        </UiAttributes>

        <div className="relative min-w-44 flex-1">
          <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <UiAttributes
            messages={{
              placeholder: { id: "ui.quality.searchTitleOrSummary" },
              "aria-label": { id: "ui.quality.searchFindingsByKeyword" },
            }}
          >
            <Input
              placeholder={"Search title or summary…"}
              aria-label={"Search findings by keyword"}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}

              className="pl-8 text-xs"
            />
          </UiAttributes>
        </div>

        {hasAnyFilter && (
          <UiAttributes
            messages={{ "aria-label": { id: "ui.quality.clearAllFilters" } }}
          >
            <Button
              aria-label={"Clear all filters"}
              variant="ghost"
              size="sm"
              onClick={clearFilters}
            >
              <FilterXIcon className="size-3.5" />{" "}
              <UiMessage id="ui.quality.clear" />{" "}
            </Button>
          </UiAttributes>
        )}
      </div>

      {/* Results */}
      {findingsQuery.isLoading ? (
        <div className="flex items-center justify-center gap-2 py-12">
          <Spinner className="size-5 text-primary" />
          <span className="text-sm text-muted-foreground">
            <UiMessage id="ui.quality.loadingFindings" />
          </span>
        </div>
      ) : findingsQuery.isError ? (
        <ErrorState
          title={friendlyApiError(findingsQuery.error, uiCopyT).title}
          message={friendlyApiError(findingsQuery.error, uiCopyT).message}
          onRetry={() => void findingsQuery.refetch()}
        />
      ) : rows.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center gap-2 py-12 text-center">
            <InboxIcon className="size-7 text-primary/40" />
            <p className="text-sm font-medium">
              {hasAnyFilter ? (
                <UiMessage id="ui.common.noFindingsMatchTheCurrentFilters" />
              ) : (
                <UiMessage id="ui.common.noFindingsRecordedForThisRun" />
              )}
            </p>
            {hasAnyFilter && (
              <Button variant="outline" size="sm" onClick={clearFilters}>
                <FilterXIcon className="size-3.5" />{" "}
                <UiMessage id="ui.quality.clearFilters" />{" "}
              </Button>
            )}
          </CardContent>
        </Card>
      ) : (
        <>
          <Card>
            <CardContent className="p-0">
              <ul className="divide-y">
                {rows.map((f, i) => (
                  <li key={`${f.rule_id}-${i}`}>
                    <FindingRow finding={f} onOpen={() => setSelected(f)} />
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>

          {/* Pagination */}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-muted-foreground">
              {uiCopyT("quality.pagination", {
                page,
                pages: totalPages,
                count: formatNumber(total),
              })}
              {keyword.trim() && (
                <span className="ml-1 opacity-70">
                  {" "}
                  <UiMessage id="ui.quality.keywordNarrowsTheCurrentPageOnly" />{" "}
                </span>
              )}
            </p>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={page <= 1 || findingsQuery.isPlaceholderData}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                <ChevronLeftIcon className="size-3.5" />{" "}
                <UiMessage id="ui.quality.previous" />{" "}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={page >= totalPages || findingsQuery.isPlaceholderData}
                onClick={() => setPage((p) => p + 1)}
              >
                {" "}
                <UiMessage id="ui.quality.next" />{" "}
                <ChevronRightIcon className="size-3.5" />
              </Button>
            </div>
          </div>
        </>
      )}

      {/* Rule detail dialog */}
      <QualityFindingDetail
        finding={selected}
        onClose={() => setSelected(null)}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */

function FindingRow({
  finding,
  onOpen,
}: {
  finding: QualityFinding;
  onOpen: () => void;
}) {
  const { t: uiLabelT, locale } = useDomainLanguage();

  const { formatNumber, formatPercent } = useLocaleDisplay();

  const severityMeta = FINDING_SEVERITY_META[finding.severity];
  const statusMeta = FINDING_STATUS_META[finding.status];
  const columnsShown = finding.affected_columns.slice(0, 3);
  const extraColumns = finding.affected_columns.length - columnsShown.length;

  return (
    <UiAttributes
      messages={{
        "aria-label": {
          id: "ui.quality.viewDetailsValue",
          values: {
            value0:
              qualityRuleName(
                finding.rule_id,
                finding.rule_version,
                uiLabelT,
              ) ?? finding.title,
          },
        },
      }}
    >
      <button
        aria-label={"View details: {value0}".replaceAll(
          "{value0}",
          String(finding.title),
        )}
        type="button"
        onClick={onOpen}

        className="group flex w-full items-stretch gap-3 rounded-lg px-4 py-3 text-left transition-colors outline-none hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <span
          aria-hidden
          className="w-1 shrink-0 rounded-full"
          style={{ backgroundColor: severityColor(finding.severity) }}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 truncate text-sm font-medium">
              {qualityRuleName(
                finding.rule_id,
                finding.rule_version,
                uiLabelT,
              ) ?? finding.title}
            </span>
            <StatusBadge
              label={
                localizedMetaLabel(severityMeta, uiLabelT) ?? finding.severity
              }
              tone={severityMeta?.tone ?? "slate"}
              dot={false}
            />
            <StatusBadge
              label={localizedMetaLabel(statusMeta, uiLabelT) ?? finding.status}
              tone={statusMeta?.tone ?? "slate"}
              dot={false}
            />
            <span className="rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[0.62rem] tracking-wide text-muted-foreground uppercase">
              {qualityCategoryLabel(finding.category, uiLabelT)}
            </span>
          </div>
          <p className="mt-1 truncate text-xs text-muted-foreground">
            <UiMessage id="diagnostic.original" />: {finding.title}
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span className="font-mono text-[0.65rem]">{finding.rule_id}</span>
            {finding.affected_columns.length > 0 && (
              <span className="min-w-0 truncate font-mono text-[0.65rem]">
                {columnsShown.join(", ")}
                {extraColumns > 0 && ` +${extraColumns}`}
              </span>
            )}
            <span className="font-mono text-[0.65rem]">
              {" "}
              <UiMessage id="ui.quality.obs" />{" "}
              {formatObserved(finding.observed_value, locale)}
            </span>
            <span className="font-mono text-[0.65rem]">
              {" "}
              <UiMessage id="ui.quality.thr" />{" "}
              {formatThreshold(finding.threshold)}
            </span>
            <span className="ml-auto shrink-0 font-mono text-[0.65rem]">
              {formatNumber(finding.affected_count)}{" "}
              <UiMessage id="ui.quality.rows" />{" "}
              {formatPercent(finding.affected_ratio)}
            </span>
          </div>
        </div>
        <ChevronRightIcon className="mt-1 size-4 shrink-0 text-muted-foreground/40 transition-colors group-hover:text-foreground" />
      </button>
    </UiAttributes>
  );
}

/* ------------------------------------------------------------------ */

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handler = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(handler);
  }, [value, delayMs]);
  return debounced;
}
