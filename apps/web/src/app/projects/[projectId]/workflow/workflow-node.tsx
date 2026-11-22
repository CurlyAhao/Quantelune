"use client";

import { useLanguage } from "@/providers/language-provider";
import {
  workflowNodeLabel,
  workflowCategoryLabel,
  workflowParameterLabel,
  workflowStatusLabel,
} from "@/lib/workflow-labels";
import { Message as UiMessage } from "@/components/message";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import {
  CheckCircle2Icon,
  CircleAlertIcon,
  CircleMinusIcon,
} from "lucide-react";
import type { WorkflowParameterValue } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface WorkflowNodeData {
  node_type: string;
  label: string;
  category: string;
  params: Record<string, WorkflowParameterValue>;
  status?: string;
  summary?: string;
  error?: string;
  error_code?: string;
  reason_code?: string;
  [key: string]: unknown;
}

export function WorkflowNode({ data, selected }: NodeProps) {
  const { t } = useLanguage();
  const node = data as unknown as WorkflowNodeData;
  const entries = Object.entries(node.params ?? {}).filter(
    ([, value]) =>
      value !== "" && value != null && !(Array.isArray(value) && !value.length),
  );
  const StatusIcon =
    node.status === "completed"
      ? CheckCircle2Icon
      : node.status === "failed"
        ? CircleAlertIcon
        : CircleMinusIcon;
  return (
    <div
      className={cn(
        "w-56 rounded-xl border bg-background px-4 py-3 text-sm text-foreground",
        selected && "border-primary outline-2 outline-offset-2 outline-primary",
        node.status === "failed" && "border-destructive",
      )}
    >
      {node.node_type !== "dataset" && (
        <Handle
          type="target"
          position={Position.Left}
          className="!size-3 !border-background !bg-muted-foreground"
        />
      )}
      <div className="flex items-start justify-between gap-3">
        <span className="font-semibold">
          {workflowNodeLabel(node.node_type, t)}
        </span>
        <span className="text-xs text-muted-foreground">
          {workflowCategoryLabel(node.category, t)}
        </span>
      </div>
      {entries.length > 0 && (
        <div className="mt-3 space-y-1 border-t pt-2 text-xs text-muted-foreground">
          {entries.slice(0, 2).map(([key, value]) => (
            <p key={key} className="break-words">
              {workflowParameterLabel(node.node_type, key, t)}:{" "}
              {Array.isArray(value) ? (
                value.join(", ")
              ) : typeof value === "object" ? (
                <UiMessage id="ui.common.configured" />
              ) : (
                String(value)
              )}
            </p>
          ))}
          {entries.length > 2 && (
            <p>
              {entries.length - 2} <UiMessage id="ui.workflow.moreParameters" />
            </p>
          )}
        </div>
      )}
      {node.status && (
        <p
          className={cn(
            "mt-3 flex items-start gap-2 text-xs",
            node.status === "failed"
              ? "text-destructive"
              : "text-muted-foreground",
          )}
        >
          <StatusIcon className="mt-0.5 size-3.5 shrink-0" />
          <span className="break-words">
            {node.status === "failed"
              ? node.error_code
                ? t("error.requestFailed.message", { code: node.error_code })
                : t("error.unexpected.message")
              : workflowStatusLabel(node.status, t)}
          </span>
        </p>
      )}
      <Handle
        type="source"
        position={Position.Right}
        className="!size-3 !border-background !bg-muted-foreground"
      />
    </div>
  );
}
