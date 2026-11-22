"use client";

import { useLanguage as useArrayLanguage } from "@/providers/language-provider";
import { FriendlyErrorMessage as UiError } from "@/components/friendly-error";

import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { BrainIcon, Loader2Icon, PlayIcon, SparklesIcon } from "lucide-react";

import { AuthGuard } from "@/components/auth-guard";
import { PageLoader } from "@/components/loading";
import { ProjectShell } from "@/components/project-shell";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { aiApi } from "@/lib/api-endpoints";

import type { AIReviewResult, AIStatus } from "@/lib/types";

const _REVIEW_TYPES = [
  {
    id: "quality",
    label: "ui.ai.qualityReview",
    desc: "ui.common.explainDataQualityFindingsInPlainLanguage",
  },
  {
    id: "method",
    label: "ui.ai.methodAdvisor",
    desc: "ui.common.suggestStatisticalMethodsForYourResearchQuestion",
  },
  {
    id: "result",
    label: "ui.ai.resultExplanation",
    desc: "ui.common.interpretAStatisticalResultPValueCiEffectSize",
  },
  {
    id: "regression",
    label: "ui.ai.regressionReview",
    desc: "ui.common.reviewDiagnosticPlotsAndCoefficientEstimates",
  },
  {
    id: "ml",
    label: "ui.ai.mlEvaluationReview",
    desc: "ui.common.assessOverfitClassBalanceAndMetricTradeoffs",
  },
  {
    id: "leakage",
    label: "ui.ai.leakageReview",
    desc: "ui.common.reviewDataLeakageChecksForAModelRun",
  },
] as const;

interface AIReviewClientProps {
  projectId: string;
}

export default function AIReviewClient({ projectId }: AIReviewClientProps) {
  return (
    <UiAttributes messages={{ label: { id: "ui.ai.loadingAiReview" } }}>
      <AuthGuard label={"Loading AI Review…"}>
        <ProjectShell projectId={projectId}>
          <AIBody projectId={projectId} />
        </ProjectShell>
      </AuthGuard>
    </UiAttributes>
  );
}

function AIBody({ projectId }: AIReviewClientProps) {
  const { t: arrayT } = useArrayLanguage();
  const REVIEW_TYPES = _REVIEW_TYPES.map((item) => ({
    ...item,
    label: arrayT(item.label),
    desc: arrayT(item.desc),
  }));

  const [selectedType, setSelectedType] = useState("quality");
  const [result, setResult] = useState<AIReviewResult | null>(null);
  const [errMsg, setErrMsg] = useState<React.ReactNode>(null);

  const statusQuery = useQuery({
    queryKey: ["ai", "status"],
    queryFn: ({ signal }) => aiApi.status(signal),
    retry: false,
  });

  const reviewMutation = useMutation({
    mutationFn: async () => {
      const payload = {
        project_id: projectId,
        [`${selectedType}_data`]: { projectId },
      };
      const m: Record<
        string,
        (d: Record<string, unknown>) => Promise<AIReviewResult>
      > = {
        quality: (d) =>
          aiApi.reviewQuality(
            d as {
              project_id?: string | null;
              quality_data: Record<string, unknown>;
            },
          ),
        method: (d) => aiApi.recommendMethod(d),
        result: (d) =>
          aiApi.explainResult(
            d as {
              project_id?: string | null;
              result: Record<string, unknown>;
            },
          ),
        regression: (d) =>
          aiApi.reviewRegression(
            d as {
              project_id?: string | null;
              regression_data: Record<string, unknown>;
            },
          ),
        ml: (d) =>
          aiApi.reviewMLEvaluation(
            d as {
              project_id?: string | null;
              ml_data: Record<string, unknown>;
            },
          ),
        leakage: (d) =>
          aiApi.reviewLeakage(
            d as {
              project_id?: string | null;
              leakage_data: Record<string, unknown>;
            },
          ),
      };
      return (
        m[selectedType]?.(payload) ?? Promise.reject(new Error("unknown type"))
      );
    },
    onSuccess: (data) => {
      setResult(data);
      setErrMsg(null);
    },
    onError: (err) => setErrMsg(<UiError error={err} part="message" />),
  });

  const status: AIStatus | undefined = statusQuery.data;

  return (
    <div className="space-y-6">
      {/* Status */}
      <Card>
        <CardContent className="flex flex-wrap items-center gap-4 py-3 text-xs">
          <span className="flex items-center gap-2">
            <SparklesIcon className="size-4 text-primary" />
            <span className="font-medium">
              <UiMessage id="ui.ai.aiAssistant" />
            </span>
          </span>
          {statusQuery.isLoading ? (
            <UiAttributes
              messages={{ label: { id: "ui.ai.checkingAiStatus" } }}
            >
              <PageLoader label={"Checking AI status…"} />
            </UiAttributes>
          ) : status ? (
            <>
              <UiAttributes
                messages={{
                  label: status.ready
                    ? { id: "ui.common.ready.format" }
                    : { id: "ui.common.notConfigured" },
                }}
              >
                <StatusBadge
                  label={status.ready ? "Ready" : "Not configured"}
                  tone={status.ready ? "teal" : "slate"}
                />
              </UiAttributes>
              <span>{status.provider}</span>
              <span className="font-mono">
                {status.model ?? <UiMessage id="ui.common.noModel" />}
              </span>
            </>
          ) : (
            <UiAttributes messages={{ label: { id: "common.unavailable" } }}>
              <StatusBadge label={"Unavailable"} tone="red" />
            </UiAttributes>
          )}
        </CardContent>
      </Card>

      {!status?.ready && !statusQuery.isLoading && (
        <div className="rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          {" "}
          <UiMessage id="ui.ai.aiIsNotConfiguredSet" />{" "}
          <code>AI_ENABLED=true</code>
          <UiMessage id="ui.ai.aProviderModelBaseUrlAndApiKeyInTheBackend" />{" "}
          <code>.env</code>{" "}
          <UiMessage id="ui.ai.fileAllOtherFeaturesWorkNormally" />{" "}
        </div>
      )}

      {/* Review type selector */}
      <div className="flex flex-wrap gap-2">
        {REVIEW_TYPES.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => {
              setSelectedType(t.id);
              setResult(null);
              setErrMsg(null);
            }}
            aria-pressed={selectedType === t.id}
            className={
              selectedType === t.id
                ? "rounded-md border border-primary bg-primary/8 px-3 py-1.5 text-left text-xs font-medium text-primary"
                : "rounded-md border bg-card px-3 py-1.5 text-left text-xs text-muted-foreground hover:bg-muted"
            }
          >
            <span className="font-medium">{t.label}</span>
            <br />
            <span className="text-[0.62rem]">{t.desc}</span>
          </button>
        ))}
      </div>

      <Button
        onClick={() => reviewMutation.mutate()}
        disabled={reviewMutation.isPending}
      >
        {reviewMutation.isPending ? (
          <Loader2Icon className="size-4 animate-spin" />
        ) : (
          <PlayIcon className="size-4" />
        )}{" "}
        <UiMessage id="ui.ai.generate" />{" "}
        {REVIEW_TYPES.find((t) => t.id === selectedType)?.label}
      </Button>

      {errMsg && (
        <div
          role="alert"
          className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive"
        >
          {errMsg}
        </div>
      )}

      {/* Result */}
      {result && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              <BrainIcon className="size-4" />
              {result.prompt_id} · {result.provider} / {result.model}
              {result.latency_ms != null && (
                <span className="font-mono text-xs text-muted-foreground">
                  {result.latency_ms}
                  <UiMessage id="ui.ai.ms" />{" "}
                </span>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {result.error && (
              <div className="mb-3 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700">
                {result.error}
              </div>
            )}
            {result.result && (
              <pre className="whitespace-pre-wrap text-xs">
                {JSON.stringify(result.result, null, 2)}
              </pre>
            )}
            {result.token_usage && (
              <p className="mt-2 font-mono text-[0.6rem] text-muted-foreground">
                {" "}
                <UiMessage id="ui.ai.tokens" />{" "}
                {JSON.stringify(result.token_usage)}
              </p>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
