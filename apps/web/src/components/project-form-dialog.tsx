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

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SearchSelect } from "@/components/ui/search-select";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { isApiError } from "@/lib/api-client";
import { projectsApi } from "@/lib/api-endpoints";
import { navigateToAppRoute } from "@/lib/desktop-navigation";

import type { Project, TaskType } from "@/lib/types";

const NONE_VALUE = "__none__";

const _TASK_TYPE_OPTIONS = [
  { value: "statistical_analysis", label: "ui.projects.statisticalAnalysis" },
  { value: "classification_binary", label: "ui.projects.binaryClassification" },
  {
    value: "classification_multiclass",
    label: "ui.projects.multiclassClassification",
  },
  { value: "regression", label: "workflow.node.regression.label" },
  { value: "clustering", label: "ui.projects.clustering" },
  { value: "anomaly_detection", label: "ui.projects.anomalyDetection" },
] as const;

const projectSchema = z.object({
  title: z
    .string()
    .min(1, "validation.titleRequired")
    .max(255, "validation.titleLength"),
  description: z.string().max(2000, "validation.descriptionLength").optional(),
  task_type: z.string().optional(),
});

type ProjectFormValues = z.infer<typeof projectSchema>;

interface ProjectFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: (project: Project) => void;
}

/** "New project" dialog used from the dashboard. */
export function ProjectFormDialog({
  open,
  onOpenChange,
  onCreated,
}: ProjectFormDialogProps) {
  const { t: arrayT } = useArrayLanguage();
  const TASK_TYPE_OPTIONS = _TASK_TYPE_OPTIONS.map((item) => ({
    ...item,
    label: arrayT(item.label),
  }));

  const router = useRouter();
  const queryClient = useQueryClient();

  const {
    register,
    handleSubmit,
    control,
    reset,
    setError,
    formState: { errors },
  } = useForm<ProjectFormValues>({
    resolver: standardSchemaResolver(projectSchema),
    defaultValues: { title: "", description: "", task_type: NONE_VALUE },
  });

  const createMutation = useMutation({
    mutationFn: (values: ProjectFormValues) =>
      projectsApi.create({
        title: values.title.trim(),
        description: values.description?.trim() || null,
        task_type:
          values.task_type && values.task_type !== NONE_VALUE
            ? (values.task_type as TaskType)
            : null,
      }),
    onSuccess: (project) => {
      queryClient.setQueryData<Project[]>(["projects"], (projects = []) => [
        project,
        ...projects.filter((item) => item.id !== project.id),
      ]);
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      onOpenChange(false);
      reset();
      toast.success(<UiMessage id="ui.projects.projectCreated" />, {
        description: project.title,
      });
      if (onCreated) onCreated(project);
      else navigateToAppRoute(router, `/projects/${project.id}/workspace`);
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

  function onSubmit(values: ProjectFormValues) {
    createMutation.mutate(values);
  }

  const isSubmitting = createMutation.isPending;

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) reset();
        onOpenChange(nextOpen);
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            <UiMessage id="ui.dashboard.newProject" />
          </DialogTitle>
          <DialogDescription>
            {" "}
            <UiMessage id="ui.common.startAFreshAnalysisWorkspaceYouCanUploadDataRightAfter" />{" "}
          </DialogDescription>
        </DialogHeader>

        <form
          id="new-project-form"
          onSubmit={handleSubmit(onSubmit)}
          className="space-y-4"
          noValidate
        >
          <div className="space-y-2">
            <Label htmlFor="project-title">
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
                id="project-title"

                autoFocus
                aria-invalid={Boolean(errors.title)}
                aria-describedby={
                  errors.title ? "project-title-error" : undefined
                }
                disabled={isSubmitting}
                {...register("title")}
              />
            </UiAttributes>
            {errors.title && (
              <p id="project-title-error" className="text-xs text-destructive">
                <FormErrorMessage code={errors.title.message} />
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="project-task-type">
              <UiMessage id="ui.projects.taskType" />
            </Label>
            <Controller
              control={control}
              name="task_type"
              render={({ field }) => (
                <UiAttributes messages={{ "aria-label": { id: "ui.projects.taskType" } }}><SearchSelect id="project-task-type" aria-label="Task type" className="w-full" value={field.value ?? NONE_VALUE} onChange={event => field.onChange(event.target.value)} disabled={isSubmitting}>
                    <option value={NONE_VALUE}>
                      <UiMessage id="ui.projects.notSpecifiedYet" />
                    </option>
                    {TASK_TYPE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </SearchSelect></UiAttributes>
              )}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="project-description">
              {" "}
              <UiMessage id="ui.projects.description" />{" "}
              <span className="font-normal text-muted-foreground">
                {" "}
                <UiMessage id="ui.projects.optional" />{" "}
              </span>
            </Label>
            <UiAttributes
              messages={{
                placeholder: { id: "ui.common.whatAreYouAnalyzingAndWhy" },
              }}
            >
              <Textarea
                placeholder={"What are you analyzing, and why?"}
                id="project-description"

                rows={3}
                aria-invalid={Boolean(errors.description)}
                disabled={isSubmitting}
                {...register("description")}
              />
            </UiAttributes>
          </div>
        </form>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              reset();
              onOpenChange(false);
            }}
            disabled={isSubmitting}
          >
            {" "}
            <UiMessage id="ui.projects.cancel" />{" "}
          </Button>
          <Button type="submit" form="new-project-form" disabled={isSubmitting}>
            {isSubmitting && <Spinner className="size-3.5" />}{" "}
            <UiMessage id="ui.common.createProject" />{" "}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
