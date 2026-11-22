/** In-memory identity revision; never stores credentials or server session tokens. */
let actorEpoch = 0;
let sessionQueue: Promise<void> = Promise.resolve();
const actorListeners = new Set<() => void>();

export function subscribeActorChange(listener: () => void): () => void {
  actorListeners.add(listener);
  return () => actorListeners.delete(listener);
}

export function getActorEpoch(): number {
  return actorEpoch;
}

export function advanceActorEpoch(): number {
  actorEpoch++;
  actorListeners.forEach((listener) => listener());
  return actorEpoch;
}

export function assertActorEpoch(expected: number): void {
  if (expected !== actorEpoch)
    throw new DOMException("Authentication context changed.", "AbortError");
}

/** Cookie-changing HTTP responses finish before the next session writer begins. */
export function serializeSessionRequest<T>(
  request: () => Promise<T>,
): Promise<T> {
  const result = sessionQueue.then(request, request);
  sessionQueue = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}
