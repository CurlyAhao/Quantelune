import type { AppLocale } from "./i18n";
import {
  createTranslator,
  type MessagePlaceholders,
  type MessageValues,
} from "./i18n-core";
import { messagesEn } from "./messages/en";
import { messagesZh } from "./messages/zh-CN";

export type MessageKey = keyof typeof messagesEn;
export type MessageParameters<K extends MessageKey> = MessagePlaceholders<
  (typeof messagesEn)[K]
>;
export type MessageValue = MessageValues;
export type NoParameterMessageKey = {
  [K in MessageKey]: [MessageParameters<K>] extends [never] ? K : never;
}[MessageKey];
export type MessageSpec = {
  [K in MessageKey]: { id: K } & ([MessageParameters<K>] extends [never]
    ? { values?: never }
    : { values: Record<MessageParameters<K>, MessageValue> });
}[MessageKey];

const catalogs = { en: messagesEn, "zh-CN": messagesZh };
const translators = {
  en: createTranslator("en", catalogs),
  "zh-CN": createTranslator("zh-CN", catalogs),
};
export function createAppTranslator(locale: AppLocale) {
  return translators[locale];
}
export type AppTranslator = ReturnType<typeof createAppTranslator>;
