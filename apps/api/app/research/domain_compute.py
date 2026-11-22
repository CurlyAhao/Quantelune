"""Registered scientific cores only, after native limits and snapshot SHA proof."""

from pathlib import Path

from app.ml.resources import MAX_ROWS, MLResourceError


def normalize(operation: str, config: dict) -> dict:
    from pydantic import BaseModel

    request: BaseModel
    if operation == "research_quality":
        from app.quality.thresholds import ThresholdConfig
        from app.schemas.quality import QualityRunRequest

        if set(config) - set(QualityRunRequest.model_fields):
            raise MLResourceError(
                "ML_CONFIGURATION_INVALID", "Unknown registered quality parameter."
            )
        request = QualityRunRequest.model_validate(config)
        ThresholdConfig().with_overrides(request.thresholds)
    elif operation in {"research_statistics", "manual_statistics"}:
        from app.schemas.statistics import StatisticalRunRequest

        if set(config) - set(StatisticalRunRequest.model_fields):
            raise MLResourceError(
                "ML_CONFIGURATION_INVALID", "Unknown registered statistics parameter."
            )
        request = StatisticalRunRequest.model_validate(config)
    else:
        raise MLResourceError("ML_OPERATION_INVALID", "Unknown registered scientific operation.")
    return request.model_dump(mode="json")


def compute(
    path: Path,
    fmt: str,
    sha: str,
    operation: str,
    config: dict,
    sheet_name: str | None = None,
    csv_encoding: str | None = None,
) -> dict:
    config = normalize(operation, config)
    from app.services.profiling_service import load_dataframe

    frame = load_dataframe(str(path), fmt, sheet_name, csv_encoding)
    if operation != "manual_statistics" and (frame.height > MAX_ROWS or frame.width > 500):
        raise MLResourceError("ML_INPUT_LIMIT", "Registered domain input exceeds fixed bounds.")
    if operation == "research_quality":
        from app.quality.service import run_quality_check
        from app.quality.thresholds import ThresholdConfig
        from app.services.profiling_service import compute_profile

        profile = compute_profile(frame, sha)
        report = run_quality_check(
            frame,
            profile,
            ThresholdConfig().with_overrides(config["thresholds"]),
            dataset_sha256=sha,
            enable_ohlcv=config["enable_ohlcv"],
            ohlcv_mapping=config["ohlcv_mapping"],
        )
        return {"profile": profile, "report": report}
    from app.schemas.statistics import StatisticalRunRequest
    from app.services.statistics_service import _config_from_request
    from app.statistics.service import run_statistical_analysis

    request = StatisticalRunRequest.model_validate(config)
    return run_statistical_analysis(
        frame, _config_from_request(request), dataset_sha256=sha
    ).to_dict()
