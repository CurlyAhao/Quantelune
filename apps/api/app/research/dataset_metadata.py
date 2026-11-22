"""Project selected dataset metadata from the legacy text JSON column."""

from sqlalchemy import JSON, cast, func

from app.models.dataset import Dataset


def preview_field(db, field: str):
    """Extract a supported field without loading the full dataset preview."""
    if field not in {"csv_encoding", "sheet_name"}:
        raise ValueError("Unsupported dataset preview field.")
    # Dataset properties treat both NULL and the empty string as an empty object.
    # Other malformed JSON must still fail rather than losing metadata silently.
    preview = func.nullif(Dataset.preview_json, "")
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        return func.json_extract(preview, f"$.{field}").label(field)
    if dialect == "postgresql":
        return cast(preview, JSON)[field].as_string().label(field)
    raise ValueError("Unsupported dataset preview database dialect.")
