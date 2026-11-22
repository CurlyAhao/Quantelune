/** IDs outside a controlled catalog must not resolve inherited object members. */
export function ownedRecordValue<T>(
  record: Record<string, T>,
  id: string,
): T | undefined {
  return Object.hasOwn(record, id) ? record[id] : undefined;
}
