const editors = new Map<string, Set<() => Promise<void>>>();

export function registerProjectEditor(
  projectId: string,
  flush: () => Promise<void>,
) {
  const active = editors.get(projectId) ?? new Set();
  active.add(flush);
  editors.set(projectId, active);
  return () => {
    active.delete(flush);
    if (!active.size) editors.delete(projectId);
  };
}

export async function flushProjectEditors(projectId: string) {
  for (const flush of editors.get(projectId) ?? []) await flush();
}
