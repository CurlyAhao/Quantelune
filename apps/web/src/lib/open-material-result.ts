import { datasetsApi } from "./api-endpoints";

/** Resolve a saved transcript to its exact revision in the original media editor. */
export async function openMaterialResult(id: string, open: (id: string) => void) {
  const dataset = await datasetsApi.get(id);
  if (!dataset.transcription) { open(id); return; }
  const source = dataset.transcription.source_id;
  sessionStorage.setItem("statforge:open-transcript:" + source, JSON.stringify({
    runId: dataset.transcription.run_id, textId: dataset.id, format: dataset.file_format,
  }));
  open(source);
  window.dispatchEvent(new Event("statforge:open-transcript-version"));
}
