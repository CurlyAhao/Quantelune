"use client";

import { Message as UiMessage } from "@/components/message";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { FileUpIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { UploadDropzone } from "@/components/upload-dropzone";
import type { Dataset } from "@/lib/types";

interface UploadDialogProps {
  projectId: string;
}

/** "Upload data" dialog wrapping the drag & drop zone. */
export function UploadDialog({ projectId }: UploadDialogProps) {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button />}>
        <FileUpIcon className="size-4" />{" "}
        <UiMessage id="ui.common.uploadData" />{" "}
      </DialogTrigger>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>
            <UiMessage id="ui.projects.uploadADataset" />
          </DialogTitle>
          <DialogDescription>
            {" "}
            <UiMessage id="ui.common.csvExcelXlsxOrParquetUpTo100MbTheSchemaIsProfiledAutomaticallyAfterUpload" />{" "}
          </DialogDescription>
        </DialogHeader>
        <UploadDropzone
          key={projectId}
          projectId={projectId}
          onUploaded={(dataset) => {
            if (dataset.project_id !== projectId) return;
            queryClient.setQueryData<Dataset[]>(
              ["datasets", projectId],
              (datasets = []) => [
                dataset,
                ...datasets.filter((item) => item.id !== dataset.id),
              ],
            );
            void queryClient.invalidateQueries({
              queryKey: ["datasets", projectId],
            });
          }}
        />
      </DialogContent>
    </Dialog>
  );
}
