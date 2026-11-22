"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { researchApi } from "@/lib/research-api";
import { useLanguage } from "@/providers/language-provider";
import { Button } from "./ui/button";
import { ResearchFailure, ResearchValue } from "./formal-research-common";

export function FormalResearchFacts({
  sessionId,
  acceptanceId,
  stepId,
  completionHash,
}: {
  sessionId: string;
  acceptanceId: string;
  stepId: string;
  completionHash: string;
}) {
  const { t, locale } = useLanguage();
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(0);
  const query = useQuery({
    queryKey: [
      "research",
      sessionId,
      acceptanceId,
      stepId,
      completionHash,
      "result-facts",
    ],
    enabled: open,
    retry: false,
    queryFn: async ({ signal }) => {
      const value = await researchApi.plans.resultFacts(
        sessionId,
        acceptanceId,
        stepId,
        signal,
      );
      if (
        value.version !== "p4-result-facts-v1" ||
        value.computed !== false ||
        value.binding.acceptance_id !== acceptanceId ||
        value.binding.step_id !== stepId ||
        value.binding.citation.completion_hash !== completionHash ||
        !Array.isArray(value.facts) ||
        value.facts.length > 4096 ||
        value.facts.some(
          (item) =>
            !Array.isArray(item.path) ||
            item.path.length > 20 ||
            (item.value !== null &&
              !["string", "number", "boolean"].includes(typeof item.value)) ||
            (typeof item.value === "number" && !Number.isFinite(item.value)),
        )
      )
        throw new Error("RESEARCH_RESULT_FACTS_UNPROVEN");
      return value;
    },
  });
  const value = query.data;
  const display = (item: string | number | boolean | null) =>
    item === null
      ? t("protocol.unavailable")
      : typeof item === "number"
        ? new Intl.NumberFormat(locale, {
            maximumSignificantDigits: 12,
          }).format(item)
        : String(item);
  return (
    <details
      className="formal-result-facts"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>{t("protocol.facts")}</summary>
      {query.isPending && open && <p role="status">{t("research.loading")}</p>}
      {query.isError && (
        <ResearchFailure
          error={query.error}
          retry={() => {
            void query.refetch();
          }}
        />
      )}
      {value && (
        <>
          <p data-no-translate>
            {value.method.id} · {value.method.version}
          </p>
          <p className="entry-help">{t("protocol.factsLimits")}</p>
          <p className="entry-help">{t("protocol.noEffectClaim")}</p>
          {value.wording.includes("no_pairwise_contrast_from_omnibus") && (
            <p className="entry-help">{t("protocol.noPairwise")}</p>
          )}
          {value.wording.includes("distribution_not_median") && (
            <p className="entry-help">{t("protocol.distribution")}</p>
          )}
          <dl className="formal-metadata">
            <dt>{t("protocol.ci")}</dt>
            <dd>
              {value.availability.ci === "unavailable"
                ? t("protocol.unavailable")
                : t("research.sourceDetails")}
            </dd>
            <dt>{t("protocol.effect")}</dt>
            <dd>
              {value.availability.effect === "unavailable"
                ? t("protocol.unavailable")
                : t("research.sourceDetails")}
            </dd>
          </dl>
          <div className="formal-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>{t("protocol.factPath")}</th>
                  <th>{t("protocol.factValue")}</th>
                </tr>
              </thead>
              <tbody>
                {value.facts
                  .slice(page * 40, (page + 1) * 40)
                  .map((item, index) => (
                    <tr key={`${JSON.stringify(item.path)}:${index}`}>
                      <th scope="row" data-no-translate>
                        {item.path.slice(3).join(" / ")}
                      </th>
                      <td className="tabular-nums" data-no-translate>
                        {display(item.value)}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          {value.facts.length > 40 && (
            <div className="formal-actions">
              <Button
                variant="ghost"
                disabled={page === 0}
                onClick={() => setPage((old) => old - 1)}
              >
                {t("research.previousPage")}
              </Button>
              <Button
                variant="ghost"
                disabled={(page + 1) * 40 >= value.facts.length}
                onClick={() => setPage((old) => old + 1)}
              >
                {t("research.nextPage")}
              </Button>
            </div>
          )}
          <details>
            <summary>{t("research.sourceDetails")}</summary>
            <ResearchValue
              value={{
                binding: value.binding,
                requested: value.requested,
                execution: value.execution,
                diagnostics: value.diagnostics,
                multiplicity: value.multiplicity,
                projection_hash: value.projection_hash,
              }}
            />
          </details>
        </>
      )}
    </details>
  );
}
