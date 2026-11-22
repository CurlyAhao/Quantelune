"use client";

import { SearchSelect } from "@/components/ui/search-select";

import { Message as UiMessage } from "@/components/message";
import { LocalizedAttributes as UiAttributes } from "@/components/localized-attributes";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AuthGuard } from "@/components/auth-guard";
import { ProjectShell } from "@/components/project-shell";
import { MaterialViewer } from "@/components/material-viewer";
import {
  usePreviewCitationSelection,
  usePreviewPublication,
} from "@/components/preview-workspace";
import { materialApi } from "@/lib/material-api";
import {
  codepointOffset,
  reference,
  type Citation,
  type Location,
  type Material,
  type TextPage,
} from "@/lib/material-types";
import { TextCodingWorkbench } from "@/components/text-coding-workbench";
import { Button } from "@/components/ui/button";

export default function MaterialsClient({ projectId }: { projectId: string }) {
  const params = useSearchParams();
  if (params.get("view") !== "locations") return <TextCodingWorkbench projectId={projectId} />;
  return <LegacyMaterialsClient projectId={projectId} />;
}

function LegacyMaterialsClient({ projectId }: { projectId: string }) {
  return (
    <UiAttributes messages={{ label: { id: "ui.materials.loadingMaterials" } }}>
      <AuthGuard label={"正在读取材料…"}>
        <ProjectShell projectId={projectId}>
          <MaterialsBody projectId={projectId} />
        </ProjectShell>
      </AuthGuard>
    </UiAttributes>
  );
}

export function MaterialsBody({ projectId }: { projectId: string }) {
  const search = useSearchParams();
  const urlMaterialId = search.get("materialId") ?? "";
  const urlCitationId = search.get("citationId") ?? "";
  const [materials, setMaterials] = useState<Material[]>([]);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [materialId, setMaterialId] = useState(urlMaterialId);
  const [editingId, setEditingId] = useState(urlCitationId);
  const [material, setMaterial] = useState<Material | null>(null);
  const [pages, setPages] = useState<TextPage[]>([]);
  const [page, setPage] = useState(1);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(0);
  const [x, setX] = useState(0);
  const [y, setY] = useState(0);
  const [width, setWidth] = useState(0.5);
  const [height, setHeight] = useState(0.5);
  const [startTime, setStartTime] = useState(0);
  const [endTime, setEndTime] = useState(1);
  const [note, setNote] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<React.ReactNode>("");
  const [saved, setSaved] = useState<Citation | null>(null);
  const active = useRef("");
  const selection = useRef({ key: "", revision: 0 });
  const selectionKey = JSON.stringify([
    projectId,
    materialId,
    editingId,
    urlMaterialId,
    urlCitationId,
  ]);
  if (selection.current.key !== selectionKey)
    selection.current = {
      key: selectionKey,
      revision: selection.current.revision + 1,
    };
  active.current = `${selectionKey}:${selection.current.revision}`;
  useEffect(() => {
    // React to navigation changes; a stable URL must not overwrite manual pickers.
    setMaterialId(urlMaterialId);
    setEditingId(urlCitationId);
  }, [projectId, urlMaterialId, urlCitationId]);
  useEffect(
    () => () => {
      active.current = "";
    },
    [],
  );
  const chooseCitation = usePreviewCitationSelection();
  usePreviewPublication({
    page: "材料与来源",
    pageMessage: { id: "ui.materials.materialsAndVerifiableSources" },
    scope: { project_id: projectId },
    draft:
      "抽取文本为候选。先人工确认引用，再在右侧逐次批准外发；此页不创建测量列。",
    draftMessage: { id: "preview.context.materials" },
  });
  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      materialApi.list(projectId, controller.signal),
      materialApi.citations(projectId, controller.signal),
    ])
      .then(([rows, refs]) => {
        if (!controller.signal.aborted) {
          setMaterials(rows);
          setCitations(refs);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setError(<UiMessage id="ui.residual.materials.listUnavailable" />);
      });
    return () => controller.abort();
  }, [projectId]);
  useEffect(() => {
    const controller = new AbortController();
    setMaterial(null);
    setPages([]);
    if (materialId)
      void Promise.all([
        materialApi.get(materialId, controller.signal),
        materialApi.text(materialId, controller.signal),
      ])
        .then(([value, text]) => {
          if (controller.signal.aborted) return;
          if (value.project_id !== projectId) throw new Error();
          setMaterial(value);
          setPages(text.pages);
          setError("");
        })
        .catch(() => {
          if (!controller.signal.aborted)
            setError(
              <UiMessage id="ui.residual.materials.fileVersionUnavailable" />,
            );
        });
    return () => controller.abort();
  }, [projectId, materialId]);
  useEffect(() => {
    // Draft identity includes the selected saved reference; approval is never carried.
    setSaved(null);
    setReviewed(false);
    setBusy(false);
    setError("");
    setPage(1);
    setStart(0);
    setEnd(0);
    setX(0);
    setY(0);
    setWidth(0.5);
    setHeight(0.5);
    setStartTime(0);
    setEndTime(1);
    setNote("");
  }, [projectId, materialId, editingId, urlMaterialId, urlCitationId]);
  const currentCitation = citations.find(
    (c) => c.id === editingId && c.material_id === materialId,
  );
  useEffect(() => {
    if (
      !material ||
      !currentCitation ||
      currentCitation.project_id !== projectId ||
      currentCitation.material_id !== material.id ||
      currentCitation.material_sha256 !== material.sha256 ||
      currentCitation.material_version !== material.version
    )
      return;
    const l = currentCitation.locator;
    if (l.kind === "text") {
      setPage(l.page);
      setStart(l.start);
      setEnd(l.end);
    } else if (l.kind === "region") {
      setX(l.x);
      setY(l.y);
      setWidth(l.width);
      setHeight(l.height);
      setNote(l.note);
    } else {
      setStartTime(l.start_seconds);
      setEndTime(l.end_seconds);
      setNote(l.note);
    }
    setReviewed(currentCitation.reviewed);
    setSaved(currentCitation);
  }, [projectId, material, currentCitation, urlMaterialId, urlCitationId]);
  const textPage = pages.find((p) => p.page === page);
  const quote = Array.from(textPage?.text ?? "")
    .slice(start, end)
    .join("");
  const image = material?.mime_type.startsWith("image/");
  const media =
    material?.mime_type === "audio/wav" || material?.mime_type === "video/mp4";
  const locator: Location | null = textPage
    ? { kind: "text", page, start, end, quote, text_sha256: textPage.sha256 }
    : image
      ? { kind: "region", x, y, width, height, note }
      : media
        ? { kind: "time", start_seconds: startTime, end_seconds: endTime, note }
        : null;
  async function upload(file: File) {
    const identity = active.current;
    setBusy(true);
    setError("");
    try {
      const value = await materialApi.upload(projectId, file);
      if (active.current !== identity || value.project_id !== projectId) return;
      setMaterials((rows) => [...rows, value]);
      setMaterialId(value.id);
      setEditingId("");
    } catch {
      if (active.current === identity)
        setError(
          <UiMessage id="ui.residual.materials.uploadIncompleteSupportLimits" />,
        );
    } finally {
      if (active.current === identity) setBusy(false);
    }
  }
  async function save() {
    if (!material || !locator) return;
    const identity = active.current;
    setBusy(true);
    setError("");
    try {
      const value = await materialApi.save(
        material.id,
        {
          material_sha256: material.sha256,
          material_version: material.version,
          locator,
          reviewed,
          ...(currentCitation
            ? { expected_revision: currentCitation.revision }
            : {}),
        },
        currentCitation?.id,
      );
      if (
        active.current !== identity ||
        value.material_id !== material.id ||
        value.project_id !== projectId
      )
        return;
      setCitations((rows) => [...rows.filter((c) => c.id !== value.id), value]);
      setEditingId(value.id);
      setSaved(value);
    } catch {
      if (active.current === identity)
        setError(
          <UiMessage id="ui.residual.materials.referenceNotSavedRereview" />,
        );
    } finally {
      if (active.current === identity) setBusy(false);
    }
  }
  const number = (
    label: string,
    value: number,
    change: (n: number) => void,
    max?: number,
    step = 1,
  ) => (
    <label className="block">
      {label}
      <input
        className="mx-2 w-28 rounded border bg-background p-1"
        type="number"
        min={0}
        max={max}
        step={step}
        value={value}
        onChange={(e) => change(Number(e.target.value))}
      />
    </label>
  );
  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">
        <UiMessage id="ui.materials.materialsAndVerifiableSources" />
      </h2>
      <p>
        {" "}
        <UiMessage id="ui.materials.theOriginalTableStaysOnTheDataPageMaterialsDoNotProduceMeasurementColumnsExtractedTextIsACandidateImagesAudioAndVideoSupportOnlyViewingPlaybackAndManualPositionNotes" />{" "}
      </p>
      <label className="block">
        {" "}
        <UiMessage id="ui.materials.uploadMaterial" />{" "}
        <UiAttributes
          messages={{ "aria-label": { id: "ui.materials.uploadMaterial" } }}
        >
          <input
            aria-label={"上传材料"}

            type="file"
            disabled={busy}
            accept=".txt,.md,.pdf,.png,.jpg,.jpeg,.gif,.webp,.wav,.mp4"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void upload(file);
              e.target.value = "";
            }}
          />
        </UiAttributes>
      </label>
      <label className="block">
        {" "}
        <UiMessage id="ui.materials.currentMaterial" />{" "}
        <SearchSelect
          disabled={busy}
          className="ml-2 max-w-full rounded border bg-background p-2"
          value={materialId}
          onChange={(e) => {
            setMaterialId(e.target.value);
            setEditingId("");
          }}
        >
          <option value="">
            <UiMessage id="ui.materials.selectMaterial" />
          </option>
          {materials.map((m) => (
            <option key={m.id} value={m.id}>
              {m.filename}
            </option>
          ))}
        </SearchSelect>
      </label>
      {material && (
        <>
          <p className="break-all text-xs">
            v{material.version} · {material.mime_type} · SHA {material.sha256}
          </p>
          <MaterialViewer material={material} page={page} location={locator} />
          <label className="block">
            {" "}
            <UiMessage id="ui.materials.savedCitation" />{" "}
            <SearchSelect
              disabled={busy}
              className="ml-2 rounded border bg-background p-2"
              value={editingId}
              onChange={(e) => {
                setEditingId(e.target.value);
                setReviewed(false);
                setSaved(null);
              }}
            >
              <option value="">
                <UiMessage id="ui.materials.createCitation" />
              </option>
              {citations
                .filter((c) => c.material_id === material.id)
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.locator.kind === "text" ? (
                      <UiMessage
                        id="ui.materials.pageValueValueValue"
                        values={{
                          value0: String(c.locator.page),
                          value1: String(c.locator.start),
                          value2: String(c.locator.end),
                        }}
                      />
                    ) : (
                      <UiMessage id="ui.displayBranch.materials.manualLocation" />
                    )}{" "}
                    · r{c.revision} ·{" "}
                    {c.reviewed ? (
                      <UiMessage id="ui.displayBranch.materials.reviewed" />
                    ) : (
                      <UiMessage id="ui.displayBranch.materials.candidate" />
                    )}
                  </option>
                ))}
            </SearchSelect>
          </label>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
            onChange={(e) => {
              if (!(
                e.target instanceof HTMLInputElement &&
                e.target.name === "reviewed"
              ))
                setReviewed(false);
            }}
          >
            {textPage ? (
              <>
                <label className="block">
                  {" "}
                  <UiMessage id="ui.materials.textPageNumber" />{" "}
                  <SearchSelect
                    name="page"
                    className="ml-2 rounded border bg-background p-1"
                    value={page}
                    onChange={(e) => {
                      setPage(Number(e.target.value));
                      setStart(0);
                      setEnd(0);
                    }}
                  >
                    {pages.map((p) => (
                      <option key={p.page} value={p.page}>
                        {p.page}
                      </option>
                    ))}
                  </SearchSelect>
                </label>
                <label className="block">
                  {" "}
                  <UiMessage id="ui.materials.candidateTextOnTheCurrentPage" />{" "}
                  <textarea
                    className="h-56 w-full whitespace-pre-wrap rounded border bg-background p-2"
                    readOnly
                    value={textPage.text}
                    onSelect={(e) => {
                      const target = e.currentTarget;
                      const a = codepointOffset(
                        target.value,
                        target.selectionStart,
                      );
                      const b = codepointOffset(
                        target.value,
                        target.selectionEnd,
                      );
                      if (a !== null && b !== null && b > a) {
                        setStart(a);
                        setEnd(b);
                        setReviewed(false);
                      }
                    }}
                  />
                </label>
                <p className="text-xs">
                  {" "}
                  <UiMessage id="ui.materials.offsetsAreZeroBasedUnicodeCodePointsInAHalfOpenStartEndRangeSelectTextOrEnterAnExactRangeEachExcerptIsLimitedTo2000CodePoints" />{" "}
                </p>
                {number(
                  "起始码点",
                  start,
                  setStart,
                  Array.from(textPage.text).length,
                )}
                {number(
                  "结束码点（不含）",
                  end,
                  setEnd,
                  Array.from(textPage.text).length,
                )}
                <label className="block">
                  {" "}
                  <UiMessage id="ui.materials.citationExcerpt" />{" "}
                  <textarea
                    className="w-full rounded border bg-background p-2"
                    readOnly
                    value={quote}
                  />
                </label>
              </>
            ) : image ? (
              <>
                <p>
                  <UiMessage id="ui.materials.regionsUseNormalized01CoordinatesTheBorderRepresentsAManualPositionNotVisualRecognition" />
                </p>
                {number("区域左边 x", x, setX, 1, 0.01)}
                {number("区域上边 y", y, setY, 1, 0.01)}
                {number("区域宽度", width, setWidth, 1, 0.01)}
                {number("区域高度", height, setHeight, 1, 0.01)}
              </>
            ) : media ? (
              <>
                {number("起始秒", startTime, setStartTime, 3600, 0.1)}
                {number("结束秒", endTime, setEndTime, 3600, 0.1)}
                <p>
                  {" "}
                  <UiMessage id="ui.materials.wavDurationIsCheckedAgainstTheServerSideFileHeaderMp4PositionsAreManualCandidatesPlaybackJumpsMustWaitForBrowserMetadataAndVerifyBounds" />{" "}
                </p>
              </>
            ) : (
              <p>
                {" "}
                <UiMessage id="ui.materials.textExtractionUnavailable" />{" "}
                {material.metadata.extraction_code ?? (
                  <UiMessage id="ui.displayBranch.materials.noTextLayer" />
                )}{" "}
                <UiMessage id="ui.materials.ocrTranscriptionAndNumericExtractionAreUnavailable" />{" "}
              </p>
            )}
            {(image || media) && (
              <label className="block">
                {" "}
                <UiMessage id="ui.materials.manualNote" />{" "}
                <textarea
                  className="w-full rounded border bg-background p-2"
                  maxLength={1000}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
              </label>
            )}
            <label className="flex gap-2">
              <input
                name="reviewed"
                type="checkbox"
                checked={reviewed}
                onChange={(e) => setReviewed(e.target.checked)}
              />{" "}
              <UiMessage id="ui.materials.iConfirmThatIReviewedThisCitationOrManualPositionSeparateFromEgressApproval" />{" "}
            </label>
            <Button
              type="submit"
              disabled={busy || !locator || (locator.kind === "text" && !quote)}
            >
              {" "}
              <UiMessage id="ui.materials.saveCitationAndReviewStatus" />{" "}
            </Button>
          </form>
          {saved && (
            <section>
              <p>
                {" "}
                <UiMessage id="ui.materials.savedCitationR" />
                {saved.revision} ·{" "}
                {saved.reviewed ? (
                  <UiMessage id="ui.displayBranch.materials.humanReviewed" />
                ) : (
                  <UiMessage id="ui.displayBranch.materials.candidateOutsideAiScope" />
                )}
              </p>
              <Button
                variant="outline"
                disabled={!saved.reviewed || !chooseCitation}
                onClick={() => {
                  if (saved.reviewed) chooseCitation?.(reference(saved));
                }}
              >
                {" "}
                <UiMessage id="ui.materials.forThisPageSAiContextAwaitingEgressApproval" />{" "}
              </Button>
              <p className="text-xs">
                {" "}
                <UiMessage id="ui.materials.onStatisticsMlFilePagesSelectTheSameConfirmedCitationFromTheRightPanelEarlierApprovalsDoNotCarryAcrossPages" />{" "}
              </p>
            </section>
          )}
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
