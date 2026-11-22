import type { AppLocale } from "./i18n";

export type MessageValues = string | number | boolean | null | undefined;
export type MessageParamValues = Record<string, MessageValues>;
type Letter =
  | "a"
  | "b"
  | "c"
  | "d"
  | "e"
  | "f"
  | "g"
  | "h"
  | "i"
  | "j"
  | "k"
  | "l"
  | "m"
  | "n"
  | "o"
  | "p"
  | "q"
  | "r"
  | "s"
  | "t"
  | "u"
  | "v"
  | "w"
  | "x"
  | "y"
  | "z";
type IdentifierStart = Letter | Uppercase<Letter> | "_";
type IdentifierPart =
  IdentifierStart | "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9";
type IdentifierTail<S extends string> = S extends ""
  ? true
  : S extends `${IdentifierPart}${infer Rest}`
    ? IdentifierTail<Rest>
    : false;
type Identifier<S extends string> = S extends `${IdentifierStart}${infer Rest}`
  ? IdentifierTail<Rest> extends true
    ? S
    : never
  : never;

export type MessagePlaceholders<S extends string> =
  S extends `${string}{${infer Name}}${infer Rest}`
    ? Identifier<Name> | MessagePlaceholders<Rest>
    : never;

export type TypedTranslator<M extends Record<string, string>> = <
  K extends keyof M & string,
  V extends Record<MessagePlaceholders<M[K]>, MessageValues>,
>(
  key: K,
  ...args: [MessagePlaceholders<M[K]>] extends [never]
    ? []
    : [values: V & Record<Exclude<keyof V, MessagePlaceholders<M[K]>>, never>]
) => string;

type Catalogs<M extends Record<string, string>> = {
  en: M;
  "zh-CN": { [K in keyof M]: string };
};

/** Recognize only complete identifier braces, leaving JSON examples intact. */
function placeholders(template: string): string[] {
  const names = new Set<string>();
  let offset = 0;
  while (offset < template.length) {
    const start = template.indexOf("{", offset);
    if (start < 0) break;
    const end = template.indexOf("}", start + 1);
    if (end < 0) break;
    const name = template.slice(start + 1, end);
    if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) names.add(name);
    offset = end + 1;
  }
  return [...names].sort();
}

export function assertMessageCatalogs<const M extends Record<string, string>>(
  catalogs: Catalogs<M>,
): void {
  const enKeys = Object.keys(catalogs.en).sort();
  const zhKeys = Object.keys(catalogs["zh-CN"]).sort();
  if (
    enKeys.length !== zhKeys.length ||
    enKeys.some((key, i) => key !== zhKeys[i])
  ) {
    throw new Error("Message catalog keys differ between locales");
  }
  for (const key of enKeys) {
    const en = catalogs.en[key];
    const zh = catalogs["zh-CN"][key];
    if (typeof en !== "string" || typeof zh !== "string") {
      throw new Error(`Invalid message template: ${key}`);
    }
    if (JSON.stringify(placeholders(en)) !== JSON.stringify(placeholders(zh))) {
      throw new Error(`Message placeholders differ between locales: ${key}`);
    }
  }
}

export function createTranslator<const M extends Record<string, string>>(
  locale: AppLocale,
  catalogs: Catalogs<M>,
): TypedTranslator<M> {
  assertMessageCatalogs(catalogs);
  if (locale !== "en" && locale !== "zh-CN")
    throw new Error("Unsupported locale");
  const messages = catalogs[locale];
  return ((key: string, ...args: MessageParamValues[]) => {
    if (!Object.hasOwn(messages, key))
      throw new Error(`Unknown message key: ${key}`);
    const template = messages[key];
    const names = placeholders(template);
    const values = args[0];
    if (names.length === 0) {
      if (args.length !== 0)
        throw new Error(`Unexpected message arguments: ${key}`);
      return template;
    }
    if (
      args.length !== 1 ||
      values === null ||
      typeof values !== "object" ||
      Array.isArray(values)
    ) {
      throw new Error(`Missing message arguments: ${key}`);
    }
    const keys = Reflect.ownKeys(values);
    if (
      keys.length !== names.length ||
      names.some((name) => !Object.hasOwn(values, name)) ||
      keys.some((name) => typeof name !== "string" || !names.includes(name))
    ) {
      throw new Error(`Message arguments do not match placeholders: ${key}`);
    }
    for (const name of names) {
      const value = values[name];
      if (
        value != null &&
        !["string", "number", "boolean"].includes(typeof value)
      ) {
        throw new Error(`Invalid message argument: ${name}`);
      }
    }
    // Insert each original value once; never process braces inside user values.
    let result = "";
    let offset = 0;
    while (offset < template.length) {
      const start = template.indexOf("{", offset);
      if (start < 0) return result + template.slice(offset);
      const end = template.indexOf("}", start + 1);
      if (end < 0) return result + template.slice(offset);
      const name = template.slice(start + 1, end);
      result += template.slice(offset, start);
      result += names.includes(name)
        ? values[name] == null
          ? "—"
          : String(values[name])
        : template.slice(start, end + 1);
      offset = end + 1;
    }
    return result;
  }) as TypedTranslator<M>;
}

const UNAVAILABLE = "—";
function finite(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
function zero(value: number): number {
  return Object.is(value, -0) ? 0 : value;
}

export function formatLocaleNumber(
  value: number | null | undefined,
  locale: AppLocale,
  options?: Intl.NumberFormatOptions,
): string {
  return finite(value)
    ? new Intl.NumberFormat(locale, options).format(zero(value))
    : UNAVAILABLE;
}

export function formatLocaleDate(
  iso: string | null | undefined,
  locale: AppLocale,
  timeZone?: string,
): string {
  if (!iso) return UNAVAILABLE;
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return UNAVAILABLE;
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    ...(timeZone ? { timeZone } : {}),
  }).format(date);
}

export function formatLocalePercent(
  value: number | null | undefined,
  locale: AppLocale,
): string {
  if (!finite(value) || !Number.isFinite(value * 100)) return UNAVAILABLE;
  return `${formatLocaleStat(value * 100, locale, 1)}%`;
}

export function formatLocaleBytes(
  bytes: number | null | undefined,
  locale: AppLocale,
): string {
  if (!finite(bytes) || bytes < 0) return UNAVAILABLE;
  if (bytes < 1024) return `${zero(bytes)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = value >= 100 ? 0 : 1;
  return `${formatLocaleNumber(digits ? value : Math.round(value), locale, { minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: false })} ${units[unit]}`;
}

export function formatLocaleStat(
  value: number | null | undefined,
  locale: AppLocale,
  digits = 4,
): string {
  if (!finite(value) || !Number.isInteger(digits) || digits < 0 || digits > 100)
    return UNAVAILABLE;
  const decimal =
    new Intl.NumberFormat(locale)
      .formatToParts(1.1)
      .find((part) => part.type === "decimal")?.value ?? ".";
  return zero(value).toFixed(digits).replace(".", decimal);
}

export function formatLocalePValue(
  p: number | null | undefined,
  locale: AppLocale,
): string {
  if (!finite(p) || p < 0 || p > 1) return UNAVAILABLE;
  if (p < 0.0001) return `< ${formatLocaleStat(0.0001, locale, 4)}`;
  return formatLocaleStat(p, locale, 4);
}
