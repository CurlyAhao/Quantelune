"use client";

import { FriendlyErrorMessage as UiError } from "@/components/friendly-error";

import { useLanguage as useLocaleDisplay } from "@/providers/language-provider";
import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useEffect, useRef, useState } from "react";
import {
  CircleCheckIcon,
  FileUpIcon,
  RotateCwIcon,
  TriangleAlertIcon,
  UploadCloudIcon,
  XIcon,
} from "lucide-react";

import { DesktopLink } from "@/components/desktop-link";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { datasetsApi } from "@/lib/api-endpoints";
import { isApiError } from "@/lib/api-client";

import type { Dataset } from "@/lib/types";
import { cn } from "@/lib/utils";

const ACCEPTED_EXTENSIONS = [".csv", ".xlsx", ".parquet"] as const;

type UploadPhase = "idle" | "uploading" | "success" | "error";

interface UploadDropzoneProps {
  projectId: string;
  /** Called after each successful upload (e.g. to invalidate queries). */
  onUploaded?: (dataset: Dataset) => void;
}

export function UploadDropzone({ projectId, onUploaded }: UploadDropzoneProps) {
  const { formatBytes, formatNumber } = useLocaleDisplay();

  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const [phase, setPhase] = useState<UploadPhase>("idle");
  const [dragActive, setDragActive] = useState(false);
  const [progress, setProgress] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [sheetName, setSheetName] = useState("");
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [error, setError] = useState<{
    title: React.ReactNode;
    message: React.ReactNode;
  } | null>(null);
  const [isDuplicate, setIsDuplicate] = useState(false);

  // Abort any in-flight upload if the component unmounts (dialog closed).
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  function validate(fileCandidate: File): React.ReactNode {
    const lower = fileCandidate.name.toLowerCase();
    const hasValidExtension = ACCEPTED_EXTENSIONS.some((ext) =>
      lower.endsWith(ext),
    );
    if (!hasValidExtension) {
      return (
        <UiMessage id="ui.common.unsupportedFileTypeUploadACsvExcelXlsxOrParquetFile" />
      );
    }
    return null;
  }

  function startUpload(fileCandidate: File) {
    const validationError = validate(fileCandidate);
    if (validationError) {
      setFile(fileCandidate);
      setError({
        title: <UiMessage id="ui.common.fileRejected" />,
        message: validationError,
      });
      setIsDuplicate(false);
      setPhase("error");
      return;
    }

    setFile(fileCandidate);
    setPhase("uploading");
    setProgress(0);
    setError(null);
    setIsDuplicate(false);

    const controller = new AbortController();
    abortRef.current = controller;

    const isExcel = fileCandidate.name.toLowerCase().endsWith(".xlsx");

    datasetsApi
      .upload(projectId, fileCandidate, {
        sheetName: isExcel ? sheetName : undefined,
        signal: controller.signal,
        onProgress: setProgress,
      })
      .then((created) => {
        if (controller.signal.aborted) return;
        if (created.project_id !== projectId)
          throw new Error("Uploaded dataset project identity mismatch");
        setDataset(created);
        setPhase("success");
        onUploaded?.(created);
      })
      .catch((uploadError: unknown) => {
        if (
          isApiError(uploadError) &&
          uploadError.errorCode === "UPLOAD_CANCELLED"
        ) {
          setPhase("idle");
          setProgress(0);
          return;
        }
        setError({
          title: <UiError error={uploadError} part="title" />,
          message: <UiError error={uploadError} part="message" />,
        });
        setIsDuplicate(
          isApiError(uploadError) &&
            uploadError.errorCode === "DATASET_DUPLICATE",
        );
        setPhase("error");
      });
  }

  function handleFiles(files: FileList | null) {
    const first = files?.[0];
    if (first) startUpload(first);
  }

  function reset() {
    setPhase("idle");
    setProgress(0);
    setFile(null);
    setDataset(null);
    setError(null);
    setIsDuplicate(false);
    if (inputRef.current) inputRef.current.value = "";
  }

  const isBusy = phase === "uploading";

  return (
    <div className="space-y-3">
      <UiAttributes
        messages={{ "aria-label": { id: "ui.common.chooseADatasetFile" } }}
      >
        <input
          aria-label={"Choose a dataset file"}
          ref={inputRef}
          id="dataset-file-input"
          type="file"
          accept={ACCEPTED_EXTENSIONS.join(",")}
          className="sr-only"

          disabled={isBusy}
          onChange={(event) => handleFiles(event.target.files)}
        />
      </UiAttributes>

      {/* ------------------------------------------------ idle */}
      {phase === "idle" && (
        <>
          <UiAttributes
            messages={{
              "aria-label": {
                id: "ui.common.uploadADatasetDragAndDropAFileHereOrPressEnterToBrowse",
              },
            }}
          >
            <div
              aria-label={
                "Upload a dataset: drag and drop a file here, or press Enter to browse"
              }
              role="button"
              tabIndex={0}

              onClick={() => inputRef.current?.click()}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  inputRef.current?.click();
                }
              }}
              onDragOver={(event) => {
                event.preventDefault();
                setDragActive(true);
              }}
              onDragEnter={(event) => {
                event.preventDefault();
                setDragActive(true);
              }}
              onDragLeave={(event) => {
                event.preventDefault();
                setDragActive(false);
              }}
              onDrop={(event) => {
                event.preventDefault();
                setDragActive(false);
                handleFiles(event.dataTransfer.files);
              }}
              className={cn(
                "group flex cursor-pointer flex-col items-center gap-3 rounded-lg border-2 border-dashed px-6 py-10 text-center transition-all duration-200 outline-none",
                "focus-visible:ring-3 focus-visible:ring-ring/50",
                dragActive
                  ? "scale-[1.01] border-primary bg-primary/8"
                  : "border-border bg-muted/40 hover:border-primary/50 hover:bg-primary/4",
              )}
            >
              <span
                className={cn(
                  "flex size-12 items-center justify-center rounded-full border bg-card transition-all duration-200",
                  dragActive
                    ? "scale-110 border-primary/50 text-primary"
                    : "text-muted-foreground group-hover:scale-105 group-hover:text-primary",
                )}
              >
                <UploadCloudIcon className="size-5.5" />
              </span>
              <div>
                <p className="text-sm font-medium">
                  {dragActive ? (
                    <UiMessage id="ui.common.dropItTheForgeIsHot" />
                  ) : (
                    <UiMessage id="ui.common.dragDropYourDataset" />
                  )}
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {" "}
                  <UiMessage id="ui.common.or" />{" "}
                  <span className="font-medium text-primary">
                    <UiMessage id="ui.common.browseFiles" />
                  </span>{" "}
                  <UiMessage id="ui.common.fromYourComputer" />{" "}
                </p>
              </div>
              <div className="flex flex-wrap items-center justify-center gap-1.5">
                {ACCEPTED_EXTENSIONS.map((ext) => (
                  <span
                    key={ext}
                    className="rounded-sm border bg-card px-1.5 py-0.5 font-mono text-[0.65rem] font-medium tracking-wide text-muted-foreground"
                  >
                    {ext.toUpperCase().slice(1)}
                  </span>
                ))}
              </div>
            </div>
          </UiAttributes>

          <div className="space-y-1.5">
            <Label
              htmlFor="dataset-sheet-name"
              className="font-mono text-[0.7rem] tracking-wide text-muted-foreground uppercase"
            >
              {" "}
              <UiMessage id="ui.common.excelSheetNameOptional" />{" "}
            </Label>
            <UiAttributes
              messages={{
                placeholder: { id: "ui.common.defaultsToTheFirstSheet" },
              }}
            >
              <Input
                placeholder={"Defaults to the first sheet"}
                id="dataset-sheet-name"
                value={sheetName}
                onChange={(event) => setSheetName(event.target.value)}

                className="font-mono text-xs"
              />
            </UiAttributes>
          </div>
        </>
      )}

      {/* ------------------------------------------------ uploading */}
      {phase === "uploading" && file && (
        <div
          className="animate-rise rounded-lg border bg-card p-4"
          role="status"
          aria-live="polite"
        >
          <div className="flex items-center gap-3">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-md bg-primary/10">
              <FileUpIcon className="size-4.5 animate-pulse text-primary" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{file.name}</p>
              <p className="font-mono text-xs text-muted-foreground">
                {formatBytes(file.size)} · {progress}%
              </p>
            </div>
            <UiAttributes
              messages={{ "aria-label": { id: "ui.common.cancelUpload" } }}
            >
              <Button
                aria-label={"Cancel upload"}
                variant="ghost"
                size="icon-sm"

                onClick={() => abortRef.current?.abort()}
              >
                <XIcon className="size-4" />
              </Button>
            </UiAttributes>
          </div>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-200 ease-out"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className="mt-2 font-mono text-[0.65rem] tracking-wide text-muted-foreground uppercase">
            {progress < 100 ? (
              <UiMessage id="ui.common.transferringToStorage" />
            ) : (
              <UiMessage id="ui.common.processing" />
            )}
          </p>
        </div>
      )}

      {/* ------------------------------------------------ success */}
      {phase === "success" && dataset && (
        <div className="animate-rise rounded-lg border border-primary/30 bg-primary/5 p-4">
          <div className="flex items-start gap-3">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-primary/15">
              <CircleCheckIcon className="size-5 text-primary" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">
                <UiMessage id="ui.common.uploadComplete" />
              </p>
              <p className="mt-0.5 truncate text-sm text-muted-foreground">
                {dataset.original_filename}
              </p>
              <p className="mt-1 font-mono text-xs text-muted-foreground">
                {formatNumber(dataset.row_count)}{" "}
                <UiMessage id="ui.common.rows" />{" "}
                {formatNumber(dataset.column_count)}{" "}
                <UiMessage id="ui.common.cols.uploadDropzone" />{" "}
                {formatBytes(dataset.file_size)} ·{" "}
                {dataset.file_format.toUpperCase()}
              </p>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <DesktopLink
              href={`/projects/${projectId}/data/${dataset.id}`}
              className={cn(buttonVariants({ size: "sm" }))}
            >
              {" "}
              <UiMessage id="ui.common.previewDataset" />{" "}
            </DesktopLink>
            <Button variant="outline" size="sm" onClick={reset}>
              {" "}
              <UiMessage id="ui.common.uploadAnother" />{" "}
            </Button>
          </div>
        </div>
      )}

      {/* ------------------------------------------------ error */}
      {phase === "error" && error && (
        <div
          role="alert"
          className={cn(
            "animate-rise rounded-lg border p-4",
            isDuplicate
              ? "border-accent/60 bg-accent/10"
              : "border-destructive/40 bg-destructive/5",
          )}
        >
          <div className="flex items-start gap-3">
            <span
              className={cn(
                "flex size-10 shrink-0 items-center justify-center rounded-full",
                isDuplicate ? "bg-accent/25" : "bg-destructive/10",
              )}
            >
              <TriangleAlertIcon
                className={cn(
                  "size-5",
                  isDuplicate ? "text-accent-foreground" : "text-destructive",
                )}
              />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">
                {isDuplicate ? (
                  <UiMessage id="ui.common.duplicateFileDetected" />
                ) : (
                  error.title
                )}
              </p>
              <p className="mt-0.5 text-sm text-muted-foreground">
                {error.message}
              </p>
              {file && (
                <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                  {file.name} · {formatBytes(file.size)}
                </p>
              )}
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {file && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => startUpload(file)}
              >
                <RotateCwIcon className="size-3.5" />{" "}
                <UiMessage id="ui.common.retryUpload" />{" "}
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={reset}>
              {" "}
              <UiMessage id="ui.common.chooseADifferentFile" />{" "}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
