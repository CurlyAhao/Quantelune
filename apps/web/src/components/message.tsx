"use client";
import type {
  MessageKey,
  MessageParameters,
  MessageValue,
  MessageSpec,
} from "@/lib/i18n-messages";
import { useLanguage } from "@/providers/language-provider";

type MessageProps<K extends MessageKey> = { id: K } & ([
  MessageParameters<K>,
] extends [never]
  ? { values?: never }
  : { values: Record<MessageParameters<K>, MessageValue> });

/** Owned product copy only. User text stays in ordinary React values. */
export function Message<K extends MessageKey>(props: MessageProps<K>) {
  const { t } = useLanguage();
  // Public props enforce the key's exact shape; the translator checks runtime.
  const render = t as (
    key: MessageKey,
    values?: Record<string, MessageValue>,
  ) => string;
  return props.values === undefined
    ? render(props.id)
    : render(props.id, props.values);
}

/** Stored UI specs preserve the exact source key/values across locale changes. */
export function MessageFromSpec(props: MessageSpec) {
  const { t } = useLanguage();
  const render = t as (
    key: MessageKey,
    values?: Record<string, MessageValue>,
  ) => string;
  return props.values === undefined
    ? render(props.id)
    : render(props.id, props.values);
}
