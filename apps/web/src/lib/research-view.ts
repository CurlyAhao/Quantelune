/** Presentation references come from authenticated events, never keyword planning. */
import type {
  ResearchDisclosure,
  ResearchEvent,
  ResearchRun,
} from "./research-types";

/** Display the request's captured disclosure, never the current editable protocol. */
export function researchModelRequest(
  events: ResearchEvent[],
  run: ResearchRun,
  attemptId: string | undefined,
  approvalId: string,
  requestHash: string | undefined,
): Record<string, unknown> | null {
  const requests = events
    .filter(
      (event) =>
        event.event_type === "harness.model_request" &&
        event.entity_kind === "model_request" &&
        event.entity_id === approvalId,
    )
    .map(researchDocument);
  if (requests.length !== 1) return null;
  const request = requests[0];
  if (
    !request ||
    request.approval_id !== approvalId ||
    request.request_hash !== requestHash ||
    request.run_id !== run.id ||
    request.attempt_id !== attemptId ||
    request.input_hash !== run.input_hash ||
    !Array.isArray(request.conversation)
  )
    return null;
  const admissions = events
    .filter(
      (event) =>
        event.event_type === "planning.admitted" &&
        event.entity_kind === "run" &&
        event.entity_id === run.id,
    )
    .map(researchDocument);
  const admission = admissions.length === 1 ? admissions[0] : null;
  if (request.version === 2) return admission?.version === 2 ? null : request;
  if (
    request.version !== 3 ||
    run.kind !== "planner" ||
    admission?.version !== 2 ||
    admission.run_id !== run.id ||
    admission.input_hash !== run.input_hash
  )
    return null;
  const snapshot = researchObject(admission.protocol);
  const version = researchObject(snapshot?.version),
    activation = researchObject(snapshot?.activation);
  const metadata = researchObject(request.research_protocol);
  const protocol = researchObject(metadata?.protocol);
  if (
    !version ||
    !activation ||
    !metadata ||
    !protocol ||
    metadata.version !== "research-planner-context-v2" ||
    protocol.version !== "research-protocol-v1" ||
    version.session_id !== run.session_id ||
    activation.session_id !== run.session_id ||
    activation.action !== "confirm" ||
    metadata.protocol_version_id !== version.version_id ||
    metadata.protocol_hash !== version.protocol_hash ||
    metadata.generation !== activation.generation ||
    activation.version_id !== version.version_id ||
    activation.protocol_hash !== version.protocol_hash ||
    protocol.source_version !== admission.source_version ||
    typeof request.profile_hash !== "string" ||
    !/^[a-f0-9]{64}$/.test(request.profile_hash) ||
    request.profile_hash !== admission.profile_hash ||
    JSON.stringify(protocol) !== JSON.stringify(version.protocol)
  )
    return null;
  return request;
}

export function researchObject(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function researchId(value: unknown): string | null {
  return typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)
    ? value
    : null;
}

export function researchDocument(
  event: ResearchEvent,
): Record<string, unknown> | null {
  if (
    typeof event.payload_json !== "string" ||
    event.payload_json.length > 1024 * 1024
  )
    return null;
  try {
    return researchObject(JSON.parse(event.payload_json));
  } catch {
    return null;
  }
}

export interface ResearchReferences {
  runIds: string[];
  draftIds: string[];
  acceptanceIds: string[];
}

export function researchReferences(
  events: ResearchEvent[],
  sessionId: string,
): ResearchReferences {
  const runs = new Set<string>(),
    drafts = new Set<string>(),
    acceptances = new Set<string>();
  for (const event of events) {
    const value = researchDocument(event);
    if (!value) continue;
    if (
      event.event_type === "run.queued" &&
      event.entity_kind === "run" &&
      value.session_id === sessionId
    ) {
      const id = researchId(event.entity_id);
      if (id) runs.add(id);
    }
    if (
      event.event_type === "plan.draft_saved" &&
      event.entity_kind === "plan_draft" &&
      value.session_id === sessionId
    ) {
      const id = researchId(value.draft_id);
      if (id === event.entity_id) drafts.add(id);
    }
    if (
      event.event_type === "plan.accepted" &&
      event.entity_kind === "plan_acceptance" &&
      researchObject(value.request)?.session_id === sessionId
    ) {
      const id = researchId(value.acceptance_id);
      if (id === event.entity_id) acceptances.add(id);
    }
  }
  return {
    runIds: [...runs],
    draftIds: [...drafts],
    acceptanceIds: [...acceptances],
  };
}

/** Recover only the exact server-frozen metadata selection, not edited UI inputs. */
export function researchAdmission(
  events: ResearchEvent[],
  runId: string,
  kind: "planner" | "reader",
): ResearchDisclosure | null {
  const name = kind === "reader" ? "reader.admitted" : "planning.admitted";
  const matches = events
    .filter((event) => event.event_type === name)
    .map(researchDocument)
    .filter((value) => value?.run_id === runId);
  if (matches.length !== 1) return null;
  const selection = researchObject(matches[0]?.selection);
  if (
    !selection ||
    typeof selection.question !== "string" ||
    !selection.question.trim() ||
    selection.question.length > 4000 ||
    selection.scope !== "metadata_only" ||
    selection.rows !== 0 ||
    selection.acknowledge_raw !== false ||
    selection.purpose !== (kind === "reader" ? "review" : "planning") ||
    !Array.isArray(selection.datasets) ||
    selection.datasets.length > 5 ||
    !Array.isArray(selection.artifact_ids) ||
    selection.artifact_ids.length > 5
  )
    return null;
  const datasets: NonNullable<ResearchDisclosure["datasets"]> = [];
  const datasetIds = new Set<string>();
  for (const item of selection.datasets) {
    const value = researchObject(item),
      id = researchId(value?.dataset_id),
      columns = value?.columns;
    if (
      !id ||
      datasetIds.has(id) ||
      !Array.isArray(columns) ||
      columns.length < 1 ||
      columns.length > 10 ||
      columns.some(
        (column) =>
          typeof column !== "string" ||
          column.length < 1 ||
          column.length > 255,
      ) ||
      new Set(columns).size !== columns.length
    )
      return null;
    datasetIds.add(id);
    datasets.push({ dataset_id: id, columns: columns as string[] });
  }
  const artifacts: string[] = [];
  for (const item of selection.artifact_ids) {
    const id = researchId(item);
    if (!id || artifacts.includes(id)) return null;
    artifacts.push(id);
  }
  if (kind === "reader" && (datasets.length > 0 || artifacts.length < 1))
    return null;
  return {
    question: selection.question,
    purpose: kind === "reader" ? "review" : "planning",
    scope: "metadata_only",
    datasets,
    artifact_ids: artifacts,
    rows: 0,
    acknowledge_raw: false,
  };
}

export function researchApprovals(
  events: ResearchEvent[],
  runId: string,
): string[] {
  const ids = new Set<string>();
  for (const event of events) {
    const value = researchDocument(event);
    if (
      event.event_type === "security.approval" &&
      event.entity_kind === "security_approval" &&
      value?.run_id === runId
    ) {
      const id = researchId(value.id);
      if (id === event.entity_id) ids.add(id);
    }
  }
  return [...ids];
}

export function sessionResearchEvents(
  events: ResearchEvent[],
  sessionId: string,
): ResearchEvent[] {
  const references = researchReferences(events, sessionId);
  const runs = new Set(references.runIds);
  const drafts = new Set(references.draftIds);
  const acceptances = new Set(references.acceptanceIds);
  const attachments = new Set<string>();
  const steps = new Map<string, Set<string>>();
  for (const event of events) {
    const value = researchDocument(event);
    if (
      event.event_type === "attachment.added" &&
      event.entity_kind === "attachment" &&
      value?.session_id === sessionId &&
      researchId(event.entity_id)
    )
      attachments.add(event.entity_id);
    if (
      event.event_type === "plan.accepted" &&
      acceptances.has(event.entity_id) &&
      Array.isArray(value?.steps)
    ) {
      steps.set(
        event.entity_id,
        new Set(
          value.steps.flatMap((item: unknown) => {
            const id = researchObject(item)?.step_id;
            return typeof id === "string" ? [id] : [];
          }),
        ),
      );
    }
  }
  return events.filter((event) => {
    const value = researchDocument(event);
    return (
      (event.entity_kind === "session" && event.entity_id === sessionId) ||
      (event.entity_kind === "run" && runs.has(event.entity_id)) ||
      (event.entity_kind === "plan_draft" && drafts.has(event.entity_id)) ||
      (event.entity_kind === "plan_acceptance" &&
        acceptances.has(event.entity_id)) ||
      (event.entity_kind === "attachment" &&
        attachments.has(event.entity_id)) ||
      (event.entity_kind === "message" && value?.session_id === sessionId) ||
      ((event.entity_kind === "protocol_version" ||
        event.entity_kind === "protocol_activation") &&
        value?.session_id === sessionId &&
        researchId(event.entity_id) !== null) ||
      (event.entity_kind === "plan_step" &&
        typeof value?.acceptance_id === "string" &&
        typeof value.step_id === "string" &&
        steps.get(value.acceptance_id)?.has(value.step_id) === true) ||
      (typeof value?.run_id === "string" && runs.has(value.run_id))
    );
  });
}

export interface ResearchEventFeed {
  rows: ResearchEvent[];
  cursor: number;
  more: boolean;
}

/** Fail visibly on a gap or changing duplicate; never silently skip audit records. */
export function appendResearchEvents(
  previous: ResearchEventFeed,
  page: ResearchEvent[],
  limit: number,
): ResearchEventFeed {
  if (page.length > limit)
    throw new Error("RESEARCH_EVENT_SEQUENCE_UNAVAILABLE");
  let expected = previous.cursor + 1;
  for (const item of page) {
    if (
      !Number.isSafeInteger(item.sequence) ||
      item.sequence !== expected ||
      typeof item.chain_hash !== "string"
    )
      throw new Error("RESEARCH_EVENT_SEQUENCE_UNAVAILABLE");
    expected++;
  }
  return {
    rows: [...previous.rows, ...page],
    cursor: page.at(-1)?.sequence ?? previous.cursor,
    more: page.length === limit,
  };
}
