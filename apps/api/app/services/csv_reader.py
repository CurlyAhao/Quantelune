"""Shared, lossless CSV decoding and dialect selection for dataset readers."""

from __future__ import annotations

import codecs
import csv
import io
from dataclasses import dataclass
from fractions import Fraction

import polars as pl

_SAMPLE_BYTES = 64 * 1024
_DELIMITERS = ",;\t|"
CSV_ENCODINGS = frozenset({"utf-8", "gb18030", "big5", "cp1252", "latin-1"})


class CsvEncodingError(ValueError):
    """A new import needs an explicit decoding choice, or that choice is invalid."""


class CsvDelimiterError(ValueError):
    """The sampled records do not distinguish competing CSV delimiters."""


@dataclass(frozen=True)
class CsvReadResult:
    dataframe: pl.DataFrame
    encoding: str
    separator: str


def _encoding(file_path: str) -> str:
    """Check the whole file so a late non-UTF-8 byte cannot be replaced silently."""
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        with open(file_path, "rb") as source:
            for chunk in iter(lambda: source.read(_SAMPLE_BYTES), b""):
                decoder.decode(chunk)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return "latin-1"
    return "utf-8"


def _separator(sample: str, sample_truncated: bool = False) -> str:
    """Infer a common delimiter without resolving structural ties by order."""
    # Compare every candidate: even consistent records can support multiple
    # delimiters, and Sniffer resolves those ties by preference. The body supports
    # its header. Malformed extra fields are rejected by the parser;
    # missing fields give weaker evidence. Punctuation only in a header does
    # not provide body evidence. Fractions avoid favoring a candidate merely
    # because its interpretation of quoted newlines produces more records.
    scores: dict[str, Fraction] = {}
    has_complete_header = False
    for delimiter in _DELIMITERS:
        try:
            records = [row for row in csv.reader(io.StringIO(sample), delimiter=delimiter) if row]
        except csv.Error:
            continue
        if sample_truncated:
            # The last record may stop between delimiters or inside a quote.
            # It cannot distinguish otherwise identical candidate structures.
            complete_tail = sample.endswith(("\n", "\r"))
            if complete_tail:
                try:
                    list(csv.reader(io.StringIO(sample), delimiter=delimiter, strict=True))
                except csv.Error:
                    complete_tail = False
            if not complete_tail:
                records = records[:-1]
        has_complete_header = has_complete_header or bool(records)
        if not records or len(records[0]) <= 1:
            continue
        header_width = len(records[0])
        body_widths = [len(row) for row in records[1:]]
        total = len(body_widths) or 1
        scores[delimiter] = Fraction(sum(width >= header_width for width in body_widths), total)
    if not scores:
        if sample_truncated and not has_complete_header:
            raise CsvDelimiterError("CSV delimiter cannot be inferred from an incomplete header.")
        return ","  # Preserve existing single-column CSV behavior.
    best = max(scores.values())
    candidates = [delimiter for delimiter, score in scores.items() if score == best]
    if len(candidates) == 1:
        return candidates[0]
    raise CsvDelimiterError(
        "CSV delimiter cannot be inferred reliably from the header and data rows. "
        "Save a CSV with a consistent comma, semicolon, tab, or pipe delimiter."
    )


def read_csv_dataset(file_path: str, csv_encoding: str | None = None) -> CsvReadResult:
    """Read a CSV with identical decoding and columns in every product path."""
    # None preserves existing datasets' historical UTF-8/Latin-1 interpretation.
    # New imports pass auto: never silently reinterpret Chinese bytes as Latin-1.
    encoding = _encoding(file_path) if csv_encoding is None else csv_encoding
    if encoding == "auto":
        encoding = _encoding(file_path)
        if encoding != "utf-8":
            raise CsvEncodingError("请选择 CSV 编码后重新导入。")
    if csv_encoding == "utf-8" and _encoding(file_path) != "utf-8":
        raise CsvEncodingError("文件与所选编码不符，请更换编码。")
    if encoding not in CSV_ENCODINGS:
        raise CsvEncodingError("请选择有效的 CSV 编码。")
    with open(file_path, "rb") as source:
        sample_bytes = source.read(_SAMPLE_BYTES)
        sample_truncated = bool(source.read(1))
    try:
        # The sample may end between bytes of a valid multibyte character.
        sample = codecs.getincrementaldecoder("utf-8-sig" if encoding == "utf-8" else encoding)(
            "strict"
        ).decode(sample_bytes, final=not sample_truncated)
    except UnicodeDecodeError as exc:
        raise CsvEncodingError("文件与所选编码不符，请更换编码。") from exc
    separator = _separator(sample, sample_truncated)
    try:
        dataframe = pl.read_csv(
            file_path,
            encoding="utf8" if encoding == "utf-8" else encoding,
            separator=separator,
            infer_schema_length=None,
            truncate_ragged_lines=False,
            ignore_errors=False,
        )
    except UnicodeDecodeError as exc:
        raise CsvEncodingError("文件与所选编码不符，请更换编码。") from exc
    return CsvReadResult(dataframe, encoding, separator)
