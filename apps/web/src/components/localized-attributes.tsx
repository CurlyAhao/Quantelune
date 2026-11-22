"use client";

import { cloneElement, type ReactElement } from "react";
import type {
  MessageKey,
  MessageSpec,
  MessageValue,
} from "@/lib/i18n-messages";
import { useLanguage } from "@/providers/language-provider";

type OwnedAttribute =
  | "aria-label"
  | "ariaLabel"
  | "retryLabel"
  | "placeholder"
  | "title"
  | "alt"
  | "label"
  | "description"
  | "message";

/** Explicit product-copy boundary for attributes authored in a server view. */
export function LocalizedAttributes({
  children,
  messages,
}: {
  children: ReactElement;
  messages: Partial<Record<OwnedAttribute, MessageSpec>>;
}) {
  const { t } = useLanguage();
  const render = t as (
    key: MessageKey,
    values?: Record<string, MessageValue>,
  ) => string;
  const attributes: Record<string, string> = {};
  for (const [name, spec] of Object.entries(messages)) {
    if (spec)
      attributes[name] =
        spec.values === undefined
          ? render(spec.id)
          : render(spec.id, spec.values);
  }
  return cloneElement(children, attributes);
}
