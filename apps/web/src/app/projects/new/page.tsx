"use client";
import { FormErrorMessage } from "@/components/form-error-message";
import { FriendlyErrorMessage } from "@/components/friendly-error";

import { useLanguage as useArrayLanguage } from "@/providers/language-provider";

import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm, Controller } from "react-hook-form";
import { standardSchemaResolver } from "@hookform/resolvers/standard-schema";
import { toast } from "sonner";
import { z } from "zod";
import { ArrowLeftIcon, FlaskConicalIcon } from "lucide-react";
import Link from "next/link";

import { AuthGuard } from "@/components/auth-guard";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SearchSelect } from "@/components/ui/search-select";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { isApiError } from "@/lib/api-client";
import { projectsApi } from "@/lib/api-endpoints";
import { navigateToAppRoute } from "@/lib/desktop-navigation";

import type { TaskType } from "@/lib/types";

const NONE_VALUE = "__none__";

const _TASK_TYPE_OPTIONS = [
  {
    value: "statistical_analysis",
    label: "ui.projects.statisticalAnalysis",
    hint: "ui.residual.statistics.inferenceTestsModels",
  },
  {
    value: "classification_binary",
    label: "ui.projects.binaryClassification",
    hint: "ui.common.twoClassPrediction",
  },
  {
    value: "classification_multiclass",
    label: "ui.projects.multiclassClassification",
    hint: "ui.common.threeOrMoreClasses",
  },
  {
    value: "regression",
    label: "workflow.node.regression.label",
    hint: "ui.common.continuousTargets",
  },
  {
    value: "clustering",
    label: "ui.projects.clustering",
    hint: "ui.residual.ml.unsupervisedGrouping",
  },
  {
    value: "anomaly_detection",
    label: "ui.projects.anomalyDetection",
    hint: "ui.residual.ml.outlierDiscovery",
  },
] as const;

const newProjectSchema = z.object({
  title: z
    .string()
    .min(1, "validation.titleRequired")
    .max(255, "validation.titleLength"),
  task_type: z.string().optional(),
  description: z.string().max(2000, "validation.descriptionLength").optional(),
  research_question: z
    .string()
    .max(2000, "validation.researchQuestionLength")
    .optional(),
});

type NewProjectValues = z.infer<typeof newProjectSchema>;

export default function NewProjectPage() {
  return (
    <UiAttributes
      messages={{ label: { id: "ui.projects.preparingAFreshBench" } }}
    >
      <AuthGuard label={"Preparing a fresh bench…"}>
        <NewProjectClient />
      </AuthGuard>
    </UiAttributes>
  );
}

function NewProjectClient() {
  const { t: arrayT } = useArrayLanguage();
  const TASK_TYPE_OPTIONS = _TASK_TYPE_OPTIONS.map((item) => ({
    ...item,
    label: arrayT(item.label),
    hint: arrayT(item.hint),
  }));

  const router = useRouter();
  const queryClient = useQueryClient();

  const {
    register,
    handleSubmit,
    control,
    setError,
    formState: { errors },
  } = useForm<NewProjectValues>({
    resolver: standardSchemaResolver(newProjectSchema),
    defaultValues: {
      title: "",
      task_type: NONE_VALUE,
      description: "",
      research_question: "",
    },
  });

  const createMutation = useMutation({
    mutationFn: (values: NewProjectValues) =>
      projectsApi.create({
        title: values.title.trim(),
        description: values.description?.trim() || null,
        research_question: values.research_question?.trim() || null,
        task_type:
          values.task_type && values.task_type !== NONE_VALUE
            ? (values.task_type as TaskType)
            : null,
      }),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      toast.success(<UiMessage id="ui.projects.projectCreated" />, {
        description: project.title,
      });
      navigateToAppRoute(router, `/projects/${project.id}/overview`, "replace");
    },
    onError: (error) => {
      if (isApiError(error) && error.status === 422) {
        setError("title", { type: "server", message: error.errorCode });
      }
      toast.error(<FriendlyErrorMessage error={error} part="title" />, {
        description: <FriendlyErrorMessage error={error} />,
      });
    },
  });

  const isSubmitting = createMutation.isPending;

  return (
    <div className="mx-auto max-w-2xl px-4 pb-20 sm:px-6">
      <div className="pt-8">
        <Link
          href="/projects"
          className="inline-flex items-center gap-1.5 rounded-sm font-mono text-xs text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          <ArrowLeftIcon className="size-3.5" />{" "}
          <UiMessage id="ui.projects.allProjects" />{" "}
        </Link>
      </div>

      <div className="animate-rise mt-6 overflow-hidden rounded-lg border bg-card shadow-plate">
        <div
          aria-hidden
          className="h-1 bg-gradient-to-r from-primary to-accent"
        />
        <div className="p-6 sm:p-8">
          <div className="flex items-center gap-3">
            <span className="flex size-10 items-center justify-center rounded-md bg-primary/10">
              <FlaskConicalIcon className="size-5 text-primary" />
            </span>
            <div>
              <h1 className="font-heading text-2xl font-bold tracking-tight">
                {" "}
                <UiMessage id="ui.dashboard.newProject" />{" "}
              </h1>
              <p className="text-sm text-muted-foreground">
                {" "}
                <UiMessage id="ui.projects.nameTheStudyYouCanAddDataNext" />{" "}
              </p>
            </div>
          </div>

          <form
            onSubmit={handleSubmit((values) => createMutation.mutate(values))}
            className="mt-8 space-y-5"
            noValidate
          >
            <div className="space-y-2">
              <Label htmlFor="new-title">
                {" "}
                <UiMessage id="workflow.parameter.title" />{" "}
                <span className="text-destructive">*</span>
              </Label>
              <UiAttributes
                messages={{
                  placeholder: { id: "ui.projects.eGIrisMorphometricsStudy" },
                }}
              >
                <Input
                  placeholder={"e.g. Iris morphometrics study"}
                  id="new-title"

                  autoFocus
                  aria-invalid={Boolean(errors.title)}
                  aria-describedby={
                    errors.title ? "new-title-error" : undefined
                  }
                  disabled={isSubmitting}
                  {...register("title")}
                />
              </UiAttributes>
              {errors.title && (
                <p id="new-title-error" className="text-xs text-destructive">
                  <FormErrorMessage code={errors.title.message} />
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="new-task-type">
                <UiMessage id="ui.projects.taskType" />
              </Label>
              <Controller
                control={control}
                name="task_type"
                render={({ field }) => (
                  <UiAttributes messages={{ "aria-label": { id: "ui.projects.taskType" } }}><SearchSelect id="new-task-type" aria-label="Task type" className="w-full" value={field.value ?? NONE_VALUE} onChange={event => field.onChange(event.target.value)} disabled={isSubmitting}>
                      <option value={NONE_VALUE}>
                        {" "}
                        <UiMessage id="ui.projects.notSpecifiedYet" />{" "}
                      </option>
                      {TASK_TYPE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label} {option.hint}
                        </option>
                      ))}
                    </SearchSelect></UiAttributes>
                )}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="new-research-question">
                {" "}
                <UiMessage id="ui.projects.researchQuestion" />{" "}
                <span className="font-normal text-muted-foreground">
                  {" "}
                  <UiMessage id="ui.projects.optional" />{" "}
                </span>
              </Label>
              <UiAttributes
                messages={{
                  placeholder: {
                    id: "ui.projects.whatHypothesisWillThisProjectTest",
                  },
                }}
              >
                <Textarea
                  placeholder={"What hypothesis will this project test?"}
                  id="new-research-question"

                  rows={2}
                  disabled={isSubmitting}
                  {...register("research_question")}
                />
              </UiAttributes>
            </div>

            <div className="space-y-2">
              <Label htmlFor="new-description">
                {" "}
                <UiMessage id="ui.projects.description" />{" "}
                <span className="font-normal text-muted-foreground">
                  {" "}
                  <UiMessage id="ui.projects.optional" />{" "}
                </span>
              </Label>
              <UiAttributes
                messages={{
                  placeholder: {
                    id: "ui.projects.dataSourcesContextExpectedDeliverables",
                  },
                }}
              >
                <Textarea
                  placeholder={"Data sources, context, expected deliverables…"}
                  id="new-description"

                  rows={3}
                  aria-invalid={Boolean(errors.description)}
                  disabled={isSubmitting}
                  {...register("description")}
                />
              </UiAttributes>
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-dashed pt-5">
              <Button
                type="button"
                variant="outline"
                onClick={() => router.push("/projects")}
                disabled={isSubmitting}
              >
                {" "}
                <UiMessage id="ui.projects.cancel" />{" "}
              </Button>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting && <Spinner className="size-3.5" />}
                {isSubmitting ? (
                  <UiMessage id="ui.residual.common.creating" />
                ) : (
                  <UiMessage id="ui.common.createProject" />
                )}
              </Button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
