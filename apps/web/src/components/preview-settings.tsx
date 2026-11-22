"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { Button } from "@/components/ui/button";

type Configuration = {
  configured: boolean;
  key_configured: boolean;
  revision: number | null;
  reserved: string | null;
  settings: Record<string, string | number | boolean | null> | null;
};

export function PreviewSettings() {
  const [status, setStatus] = useState<Configuration | null>(null);
  const [error, setError] = useState<React.ReactNode>("");
  const [busy, setBusy] = useState(false);
  const [key, setKey] = useState("");
  const [provider, setProvider] = useState("deepseek");
  const [model, setModel] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [inputPrice, setInputPrice] = useState("");
  const [outputPrice, setOutputPrice] = useState("");
  const [budget, setBudget] = useState("");
  const [estimateEnabled, setEstimateEnabled] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void apiFetch<Configuration>("/api/v1/preview/configuration", {
      signal: controller.signal,
    })
      .then((value) => {
        setStatus(value);
        if (value.settings) {
          setProvider(String(value.settings.provider));
          setModel(String(value.settings.model));
          setCurrency(String(value.settings.currency));
          setEstimateEnabled(value.settings.pricing_mode === "estimate");
          setInputPrice(String(value.settings.input_price_per_million ?? ""));
          setOutputPrice(String(value.settings.output_price_per_million ?? ""));
          setBudget(String(value.settings.budget ?? ""));
        }
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setError(
            <UiMessage id="ui.residual.preview.securityConfigurationUnavailable" />,
          );
      });
    return () => controller.abort();
  }, []);

  async function save() {
    setBusy(true);
    setError("");
    try {
      const value = await apiFetch<Configuration>(
        "/api/v1/preview/configuration",
        {
          method: "PATCH",
          body: JSON.stringify({
            provider,
            model,
            currency,
            pricing_mode: estimateEnabled ? "estimate" : "off",
            input_price_per_million: estimateEnabled ? inputPrice : null,
            output_price_per_million: estimateEnabled ? outputPrice : null,
            budget: estimateEnabled && budget ? budget : null,
            output_length_mode: "auto",
            max_output_tokens: null,
            max_input_bytes: 32000,
            ...(key ? { key } : {}),
          }),
        },
      );
      setStatus(value);
    } catch {
      // Never display a backend/network message that might echo secret input.
      setError(
        <UiMessage id="ui.residual.preview.configurationNotSavedCredentialService" />,
      );
    } finally {
      setKey("");
      setBusy(false);
    }
  }

  return (
    <section className="mt-6 rounded-xl border bg-card p-5">
      <h2 className="font-heading font-semibold">
        {" "}
        <UiMessage id="ui.preview.restrictedAiDevelopmentPreviewSecureConfiguration" />{" "}
      </h2>
      <p className="mt-2 text-sm text-muted-foreground">
        {status?.configured ? (
          <UiMessage id="ui.displayBranch.preview.providerUnverifiedSavedProtocol" />
        ) : (
          <UiMessage id="ui.displayBranch.preview.agentNotReadyManualAvailable" />
        )}{" "}
        <UiMessage id="ui.preview.theKeyIsStoredInThisInstanceSWindowsCredentialsForTheCurrentUserThisEntryPointMakesNoModelRequestReEnterTheMatchingKeyWhenChangingProvidersEachQuestionStillRequiresApprovalOfTheQuestionCurrentDataVersionSelectedColumnsAndSummaryEgressScope" />{" "}
      </p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <label>
          {" "}
          <UiMessage id="ui.preview.provider" />{" "}
          <SearchSelect
            className="mt-1 block w-full rounded border bg-background p-2"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="deepseek">DeepSeek</option>
            <option value="mimo">MiMo</option>
            <option value="glm">
              <UiMessage id="ui.preview.glm" />
            </option>
            <option value="opencode_zen">
              {" "}
              <UiMessage id="ui.preview.opencodeZenModelsExplicitlyCompatibleWithChatCompletions" />{" "}
            </option>
          </SearchSelect>
        </label>
        <label>
          {" "}
          <UiMessage id="ui.preview.exactModel" />{" "}
          <input
            className="mt-1 block w-full rounded border bg-background p-2"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            autoComplete="off"
          />
        </label>
        <label>
          {" "}
          <UiMessage id="ui.preview.secureKey" />
          {status?.key_configured && (
            <UiMessage id="ui.residual.preview.savedNotRedisplayed" />
          )}
          <UiAttributes
            messages={{ "aria-label": { id: "ui.preview.secureKey" } }}
          >
            <input
              aria-label={"安全密钥"}

              type="password"
              autoComplete="new-password"
              className="mt-1 block w-full rounded border bg-background p-2"
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
          </UiAttributes>
        </label>
        <details className="col-span-full">
          <summary>高级设置：费用估算（可选）</summary>
          <label>
            <input
              type="checkbox"
              checked={estimateEnabled}
              onChange={(event) => setEstimateEnabled(event.target.checked)}
            />
            启用费用估算
          </label>
          {estimateEnabled && (
            <div>
              <label>
                {" "}
                <UiMessage id="ui.preview.currency" />{" "}
                <SearchSelect
                  className="mt-1 block w-full rounded border bg-background p-2"
                  value={currency}
                  onChange={(e) => setCurrency(e.target.value)}
                >
                  <option>USD</option>
                  <option>CNY</option>
                </SearchSelect>
              </label>
              <label>
                {" "}
                <UiMessage id="ui.preview.inputPriceCeilingMillionTokens" />{" "}
                <input
                  className="mt-1 block w-full rounded border bg-background p-2"
                  inputMode="decimal"
                  value={inputPrice}
                  onChange={(e) => setInputPrice(e.target.value)}
                />
              </label>
              <label>
                {" "}
                <UiMessage id="ui.preview.outputPriceCeilingMillionTokens" />{" "}
                <input
                  className="mt-1 block w-full rounded border bg-background p-2"
                  inputMode="decimal"
                  value={outputPrice}
                  onChange={(e) => setOutputPrice(e.target.value)}
                />
              </label>
              <label>
                {" "}
                <UiMessage id="ui.preview.localCallBudget" />{" "}
                <input
                  className="mt-1 block w-full rounded border bg-background p-2"
                  inputMode="decimal"
                  value={budget}
                  onChange={(e) => setBudget(e.target.value)}
                />
              </label>
            </div>
          )}
          <p>不启用也能保存使用，费用显示为未估算。</p>
        </details>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        {" "}
        <UiMessage id="ui.preview.reservedCost" />
        {status?.reserved ?? "0"}{" "}
        <UiMessage id="ui.preview.reservationsAreNotRefundedOnFailureTimeoutOrMissingVerifiedUsageHiddenReasoningTokensAndProviderBillingHaveNotBeenTestedTheLocalBudgetDoesNotGuaranteeAHardLimitOnTheProviderBill" />{" "}
      </p>
      {error && (
        <p role="alert" className="mt-3 text-sm text-destructive">
          {error}
        </p>
      )}
      <Button
        className="mt-4"
        disabled={busy || !model.trim()}
        onClick={() => void save()}
      >
        {" "}
        <UiMessage id="ui.preview.saveSecureConfiguration" />{" "}
      </Button>
    </section>
  );
}
