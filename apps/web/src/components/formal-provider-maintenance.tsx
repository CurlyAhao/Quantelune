"use client";

import { useState, useSyncExternalStore } from "react";
import { apiFetch, isApiError } from "@/lib/api-client";
import { getActorEpoch, subscribeActorChange } from "@/lib/auth-session";
import { useLanguage } from "@/providers/language-provider";
import { ResearchFailure } from "./formal-research-common";
import { useResearchAction } from "./use-research-action";
import { Button } from "./ui/button";

const PATH = "/api/v1/research/security/provider/maintenance";
type Configuration = {
  configuration_id: string;
  is_current: boolean;
  key_state: string;
};
type Maintenance = {
  revision: number;
  configurations: Configuration[];
  operations: { operation_id: string; kind: string; state: string }[];
  configurations_truncated: boolean;
  operations_truncated: boolean;
};

export function ProviderMaintenance({
  revision,
  changed,
}: {
  revision: number;
  changed: () => Promise<unknown>;
}) {
  const epoch = useSyncExternalStore(
    subscribeActorChange,
    getActorEpoch,
    () => 0,
  );
  return <MaintenanceControls key={`${epoch}:${revision}`} changed={changed} />;
}

function MaintenanceControls({ changed }: { changed: () => Promise<unknown> }) {
  const { t } = useLanguage();
  const [listing, setListing] = useState<Maintenance | null>(null);
  const [confirmed, setConfirmed] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<
    "deleted" | "recovered" | "attention" | null
  >(null);
  const action = useResearchAction("provider-maintenance");
  const blocked = action.pending || action.error != null;

  function read() {
    if (action.pending) return;
    action.reset();
    setConfirmed(null);
    setListing(null);
    setOutcome(null);
    action.start(
      (signal) => apiFetch<Maintenance>(PATH, { signal }),
      setListing,
    );
  }
  function accepted(result: "deleted" | "recovered" | "attention") {
    setListing(null);
    setConfirmed(null);
    setOutcome(result);
    void changed().catch(() => undefined);
  }
  function remove(row: Configuration) {
    if (
      blocked ||
      !listing ||
      row.is_current ||
      confirmed !== row.configuration_id
    )
      return;
    action.start(
      (signal) =>
        apiFetch<{ status: string }>(
          `${PATH}/retired/${row.configuration_id}`,
          {
            method: "POST",
            signal,
            body: JSON.stringify({ expected_revision: listing.revision }),
          },
        ),
      (result) =>
        accepted(result.status === "deleted" ? "deleted" : "attention"),
    );
  }
  return (
    <section aria-label={t("research.maintenanceTitle")}>
      <h3>{t("research.maintenanceTitle")}</h3>
      <p className="entry-help">{t("research.maintenanceHelp")}</p>
      <Button variant="outline" disabled={action.pending} onClick={read}>
        {t("research.maintenanceRead")}
      </Button>
      {action.pending && <p role="status">{t("research.pending")}</p>}
      {action.error != null && (
        <>
          <ResearchFailure error={action.error} />
          <p>
            {t(
              isApiError(action.error) && action.error.status > 0
                ? "research.maintenanceServerError"
                : "research.uncertain",
            )}
          </p>
        </>
      )}
      {outcome && (
        <p role="status">
          {t(
            outcome === "deleted"
              ? "research.maintenanceDeleted"
              : outcome === "recovered"
                ? "research.maintenanceRecovered"
                : "research.maintenanceAttention",
          )}
        </p>
      )}
      {listing && (
        <>
          {(listing.configurations_truncated ||
            listing.operations_truncated) && (
            <p role="status">{t("research.maintenanceLimited")}</p>
          )}
          {listing.configurations.length === 0 &&
            listing.operations.length === 0 && (
              <p>{t("research.maintenanceEmpty")}</p>
            )}
          <ul>
            {listing.configurations.map((row) => (
              <li key={row.configuration_id}>
                <p>
                  {t(
                    row.is_current
                      ? "research.maintenanceCurrent"
                      : row.key_state === "configured"
                        ? "research.maintenanceRetired"
                        : "research.maintenancePending",
                  )}{" "}
                  <code>{row.configuration_id}</code>
                </p>
                {!row.is_current && row.key_state === "configured" && (
                  <div className="formal-actions">
                    <label>
                      <input
                        type="checkbox"
                        checked={confirmed === row.configuration_id}
                        disabled={blocked}
                        onChange={(event) =>
                          setConfirmed(
                            event.target.checked ? row.configuration_id : null,
                          )
                        }
                      />{" "}
                      {t("research.maintenanceConfirm")}
                    </label>
                    <Button
                      variant="outline"
                      disabled={blocked || confirmed !== row.configuration_id}
                      onClick={() => remove(row)}
                    >
                      {t("research.maintenanceRemove")}
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
          {listing.operations.length > 0 && (
            <>
              <ul>
                {listing.operations.map((row) => (
                  <li key={row.operation_id}>
                    {t("research.maintenancePending")}{" "}
                    <code>{row.operation_id}</code>
                  </li>
                ))}
              </ul>
              <Button
                variant="outline"
                disabled={blocked}
                onClick={() =>
                  action.start(
                    (signal) =>
                      apiFetch<{ items: { status: string }[] }>(
                        `${PATH}/reconcile`,
                        {
                          method: "POST",
                          signal,
                          body: JSON.stringify({ limit: 20 }),
                        },
                      ),
                    (result) =>
                      accepted(
                        result.items.length > 0 &&
                          result.items.every((row) => row.status === "deleted")
                          ? "recovered"
                          : "attention",
                      ),
                  )
                }
              >
                {t("research.maintenanceRecover")}
              </Button>
            </>
          )}
        </>
      )}
    </section>
  );
}
