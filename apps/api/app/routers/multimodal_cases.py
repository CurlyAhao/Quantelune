import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.audio_spectrum import SpectralQuery, SpectrumRequest
from app.schemas.formant_revisions import FormantBrowse, FormantRevision
from app.schemas.media_runs import MediaAssociation, MediaRequest
from app.schemas.pitch_revisions import PitchBrowse, PitchRevision
from app.schemas.voice_quality import VoiceQualityRequest
from app.services import dataset_service, transcription
from app.services import multimodal_cases as service

router = APIRouter(prefix="/api/v1/datasets", tags=["local media samples"])


@router.post("/{dataset_id}/formant-candidates")
async def formant_candidates(
    dataset_id: uuid.UUID,
    body: FormantBrowse,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import formant_revisions

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await formant_revisions.browse(db, user, source, body)


@router.post("/{dataset_id}/formant-revisions")
async def revise_formants(
    dataset_id: uuid.UUID,
    body: FormantRevision,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import formant_revisions

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await formant_revisions.save(db, user, source, body)


@router.get("/{dataset_id}/voice-quality")
async def voice_quality_runs(
    dataset_id: uuid.UUID,
    package_id: uuid.UUID | None = None,
    sample_id: str | None = None,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import voice_quality

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await voice_quality.listing(db, user, source, package_id, sample_id)


@router.post("/{dataset_id}/voice-quality")
async def create_voice_quality(
    dataset_id: uuid.UUID,
    body: VoiceQualityRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import voice_quality

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await voice_quality.calculate(db, user, source, body)


@router.get("/{dataset_id}/voice-quality/{run_id}")
async def voice_quality_result(
    dataset_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import voice_quality

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await voice_quality.detail(db, user, source, run_id)


@router.get("/{dataset_id}/voice-quality/{run_id}/export")
async def export_voice_quality(
    dataset_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from fastapi.responses import FileResponse

    from app.services import voice_quality

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return FileResponse(
        await voice_quality.export(db, user, source, run_id),
        media_type="application/zip",
        filename="voice-quality.zip",
    )


@router.get("/{dataset_id}/audio-spectra")
async def spectra(
    dataset_id: uuid.UUID,
    package_id: uuid.UUID | None = None,
    sample_id: str | None = None,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import audio_spectrum

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await audio_spectrum.listing(db, user, source, package_id, sample_id)


@router.post("/{dataset_id}/audio-spectra")
async def create_spectrum(
    dataset_id: uuid.UUID,
    body: SpectrumRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import audio_spectrum

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await audio_spectrum.calculate(db, user, source, body)


@router.get("/{dataset_id}/audio-spectra/{run_id}")
async def spectrum(
    dataset_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import audio_spectrum

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await audio_spectrum.detail(db, user, source, run_id)


@router.get("/{dataset_id}/audio-spectra/{run_id}/export")
async def export_spectrum(
    dataset_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from fastapi.responses import FileResponse

    from app.services import audio_spectrum

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return FileResponse(
        await audio_spectrum.export(db, user, source, run_id),
        media_type="application/zip",
        filename="spectrum.zip",
    )


@router.post("/{dataset_id}/audio-spectra/{run_id}/query")
async def query_spectrum(
    dataset_id: uuid.UUID,
    run_id: uuid.UUID,
    body: SpectralQuery,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import audio_spectrum

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await audio_spectrum.query(db, user, source, run_id, body)


@router.get("/{dataset_id}/transcriptions")
async def transcriptions(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await transcription.listing(db, user, source)


@router.post("/{dataset_id}/transcriptions")
async def start_transcription(
    dataset_id: uuid.UUID,
    body: transcription.TranscriptionRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await transcription.start(db, user, source, body)


@router.get("/{dataset_id}/transcriptions/{run_id}")
async def get_transcription(
    dataset_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await transcription.get(db, user, source, run_id)


@router.get("/{dataset_id}/transcriptions/{run_id}/versions/{text_id}")
async def transcription_version(
    dataset_id: uuid.UUID,
    run_id: uuid.UUID,
    text_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await transcription.version(db, user, source, run_id, text_id)


@router.put("/{dataset_id}/transcriptions/{run_id}")
async def edit_transcription(
    dataset_id: uuid.UUID,
    run_id: uuid.UUID,
    body: transcription.TranscriptionEdit,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await transcription.edit(db, user, source, run_id, body)


@router.get("/{dataset_id}/media-filename-metadata")
async def media_filename_metadata(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import local_workspaces
    from app.services import media_filename_metadata as metadata

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    root = local_workspaces.project_root(source.project_id)
    if root is None:
        return {}
    return await metadata.filename_metadata(db, source.project_id, root)


@router.post("/{dataset_id}/media-association")
async def associate(
    dataset_id: uuid.UUID,
    body: MediaAssociation,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await service.associate(db, user, dataset_id, body)


class CaseName(BaseModel):
    name: str = Field(min_length=1, max_length=255)


@router.patch("/{dataset_id}/cases/{sample_id}")
async def rename_case(
    dataset_id: uuid.UUID,
    sample_id: str,
    body: CaseName,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await service.change_case(db, user, dataset_id, sample_id, title=body.name)


@router.delete("/{dataset_id}/cases/{sample_id}")
async def remove_case(
    dataset_id: uuid.UUID,
    sample_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await service.change_case(db, user, dataset_id, sample_id, remove=True)


@router.get("/{dataset_id}/samples")
async def samples(
    dataset_id: uuid.UUID,
    task_id: str = "project",
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await service.view(db, user, dataset_id, task_id)


@router.post("/{dataset_id}/media-process")
async def process(
    dataset_id: uuid.UUID,
    body: MediaRequest,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    return await service.start(db, user, dataset_id, body)


@router.get("/{dataset_id}/media-runs/{run_id}")
async def run(
    dataset_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    if source.file_format != "pitch":
        source, _, _ = await service.collection(db, user, dataset_id)
    return await service.get_run(db, user, source, run_id)


@router.get("/{dataset_id}/media-result")
async def result(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await service.result_index(db, user, source)


class AudioInspection(BaseModel):
    sample_id: str
    point: float | None = Field(default=None, allow_inf_nan=False)
    start: float | None = Field(default=None, allow_inf_nan=False)
    end: float | None = Field(default=None, allow_inf_nan=False)
    spectrogram: bool = False

    @model_validator(mode="after")
    def valid_range(self):
        if (self.start is None) != (self.end is None) or (
            self.start is not None and self.end <= self.start
        ):
            raise ValueError("请填写晚于开始时间的结束时间。")
        return self


@router.post("/{dataset_id}/audio-inspection")
async def inspect_audio(
    dataset_id: uuid.UUID,
    body: AudioInspection,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import audio_inspection

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    if body.spectrogram:
        return await audio_inspection.spectrogram(
            db, user, source, body.sample_id, body.start, body.end
        )
    return await audio_inspection.measurements(
        db, user, source, body.sample_id, body.point, body.start, body.end
    )


@router.post("/{dataset_id}/pitch-candidates")
async def pitch_candidates(
    dataset_id: uuid.UUID,
    body: PitchBrowse,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import pitch_revisions

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await pitch_revisions.browse(db, user, source, body)


@router.post("/{dataset_id}/pitch-revisions")
async def revise_pitch(
    dataset_id: uuid.UUID,
    body: PitchRevision,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import pitch_revisions

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await pitch_revisions.save(db, user, source, body)


@router.post("/{dataset_id}/pitch-object")
async def open_pitch(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import pitch_native

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await pitch_native.open_object(db, user, source)


class PitchAudioAssociation(BaseModel):
    audio_id: uuid.UUID
    offset_seconds: float = Field(allow_inf_nan=False)


@router.post("/{dataset_id}/pitch-association")
async def associate_pitch(
    dataset_id: uuid.UUID,
    body: PitchAudioAssociation,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services import pitch_native

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return await pitch_native.link_audio(db, user, source, body.audio_id, body.offset_seconds)


@router.get("/{dataset_id}/pitch-export")
async def export_pitch(
    dataset_id: uuid.UUID,
    sample_id: str,
    format: Literal["long", "short", "binary"] = "long",
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    import asyncio
    import tempfile

    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask

    from app.services import pitch_exchange, pitch_revisions, storage_service

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    _, run, entry, package = await pitch_revisions._context(db, user, source, sample_id)
    path = storage_service.get_local_object_path(package.object_key)
    data = await asyncio.to_thread(pitch_revisions._read, path, entry["array_prefix"])
    if data is None:
        raise service.invalid("这份旧结果没有完整候选，无法导出 Pitch。")
    clock = pitch_exchange.clock_for(data, entry, run["parameters"])
    directory = tempfile.TemporaryDirectory(prefix="sf-pitch-export-")
    try:
        destination = await asyncio.to_thread(
            pitch_exchange.export_file,
            path,
            package.sha256,
            entry["array_prefix"],
            clock,
            format,
            directory.name,
        )
    except Exception:
        directory.cleanup()
        raise
    return FileResponse(
        destination,
        media_type="application/octet-stream",
        filename=f"pitch-{format}.Pitch",
        background=BackgroundTask(directory.cleanup),
    )


class Selection(BaseModel):
    processing: MediaRequest | None = None
    task_id: uuid.UUID | None = None
    sample_id: str
    audio_source: Literal["video", "independent"] | None = None
    start: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    end: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self):
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("结束秒数应晚于开始秒数。")
        return self


@router.post("/{dataset_id}/sample-selection")
async def selection(
    dataset_id: uuid.UUID,
    body: Selection,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.preview.write_fence import acquire_write_fence
    from app.services.project_assistant_tasks import context

    await acquire_write_fence(db, user.id)
    source, root, descriptor = await service.collection(db, user, dataset_id)
    if body.task_id:
        await context(db, source.project_id, user, str(body.task_id))
    if body.sample_id not in {s["sample_id"] for s in service.sample_rows(root, descriptor)}:
        raise service.invalid("样本不存在，请刷新后重选。")
    meta = service.read_metadata(source)
    selections = meta.setdefault("sample_selection", {})
    scope = str(body.task_id or "project")
    entry = body.model_dump(mode="json", exclude={"processing"})
    previous = service.saved_selection(source, scope)
    if body.processing is not None:
        entry["processing"] = body.processing.model_dump(
            mode="json", exclude={"start", "end", "task_id", "force", "sample_ids"}
        )
    elif previous.get("processing"):
        entry["processing"] = dict(previous["processing"])
        if body.audio_source is not None:
            entry["processing"]["audio_source"] = body.audio_source
    processing = entry.get("processing") or {}
    if processing.get("speech_count") is not None:
        count_for = service.speech_count_scope(
            source.id, body.sample_id, body.start, body.end, processing
        )
        if body.processing is not None or previous.get("speech_count_for") == count_for:
            entry["speech_count_for"] = count_for
        else:
            processing["speech_count"] = None
    selections[scope] = entry
    source.preview_json = json.dumps(meta, ensure_ascii=False)
    await db.commit()
    return entry


class OriginalTimeRange(BaseModel):
    task_id: uuid.UUID | None = None
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("结束时间应晚于开始时间。")
        return self


@router.get("/{dataset_id}/original-time-range")
async def original_range(
    dataset_id: uuid.UUID,
    task_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.services.project_assistant import material_selection

    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    return material_selection(source, str(task_id or "project"))


@router.put("/{dataset_id}/original-time-range")
async def set_original_range(
    dataset_id: uuid.UUID,
    body: OriginalTimeRange,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    from app.preview.write_fence import acquire_write_fence
    from app.services import material_service
    from app.services.project_assistant_tasks import context

    await acquire_write_fence(db, user.id)
    source = await dataset_service.get_dataset_with_access(db, dataset_id, user.id)
    meta = service.read_metadata(source)
    if (
        source.file_format not in {"wav", "mp3", "mp4"}
        or source.sha256 != body.sha256
        or not meta.get("material_id")
    ):
        raise service.invalid("请重新打开当前音视频。")
    if body.task_id:
        await context(db, source.project_id, user, str(body.task_id))
    material = await material_service.load(
        db, uuid.UUID(meta["material_id"]), user.id, source.project_id
    )
    duration = json.loads(material.metadata_json or "{}").get("duration_seconds")
    if source.file_format == "mp3":
        from app.services import audio_playback

        duration = (await audio_playback.preview(material))["duration"]
    if material.sha256 != source.sha256 or (duration and body.end > duration + 0.002):
        raise service.invalid("选段超出当前材料，请重新选择。")
    ranges = meta.setdefault("sample_selection", {})
    key = str(body.task_id or "project")
    if duration and body.start == 0 and abs(duration - body.end) <= 0.002:
        ranges.pop(key, None)
    else:
        ranges[key] = {"start": body.start, "end": body.end, "sha256": body.sha256}
    source.preview_json = json.dumps(meta, ensure_ascii=False)
    await db.commit()
    return ranges.get(key, {})


class Search(BaseModel):
    run_id: uuid.UUID
    sample_id: str
    caption_id: int | None = None


@router.post("/{dataset_id}/media-search")
async def search(
    dataset_id: uuid.UUID,
    body: Search,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
):
    source, _, _ = await service.collection(db, user, dataset_id)
    return await service.search(db, user, source, body.run_id, body.sample_id, body.caption_id)
