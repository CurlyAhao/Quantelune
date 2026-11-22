"""Bounded reports of proved saved P4 outputs; no model or statistical execution."""

import hashlib
import html
import io
import json
import re
import uuid
import zipfile

from sqlalchemy.ext.asyncio import AsyncSession

from app.research import p4_result_reader, plan_acceptance, plan_execution, plan_versioning
from app.research.context_contracts import ContextSnapshot
from app.research.contracts import canonical_bytes, logical_hash
from app.research.plan_validation import failure

MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
NOTICE = (
    "Saved statistical output; researcher declarations and planned diagnostics are recorded, "
    "not independently verified. Missing confidence intervals/effects remain unavailable. "
    "No multiplicity adjustment or new computation is performed by this report. "
    "Association does not establish causation; non-significance does not establish no effect."
)


async def document(
    db: AsyncSession, owner: uuid.UUID, session_id: uuid.UUID, acceptance_id: uuid.UUID
) -> dict:
    """Authorize history and prove every saved step/publication before rendering."""
    acceptance, _, plan, steps = await plan_execution.load(db, owner, acceptance_id)
    if acceptance.session_id != session_id or not plan_versioning.is_v2(plan):
        raise failure("REPORT_UNAVAILABLE")
    if not steps or any(step.result_hash is None for step in steps.values()):
        raise failure("REPORT_INCOMPLETE")
    statistical = [step for step in plan.steps if step.capability == "statistics"]
    if not statistical:
        raise failure("REPORT_UNAVAILABLE")
    _, _, saved = await plan_acceptance.version(db, owner, session_id, acceptance.plan_version_id)
    protocol = plan_versioning.captured_protocol(
        plan, ContextSnapshot.model_validate(saved["context"]), saved["protocol"]
    )
    proofs = [
        await plan_execution.prove_step(db, owner, acceptance, steps[step.id])
        for step in plan.steps
    ]
    value: dict = {
        "version": "p4-saved-report-v1",
        "computed": False,
        "notice": NOTICE,
        "session_id": str(session_id),
        "acceptance_id": str(acceptance.id),
        "acceptance_hash": acceptance.document_hash,
        "plan_version_id": str(acceptance.plan_version_id),
        "plan_hash": saved["plan_hash"],
        "source_version": plan.source_version,
        "protocol_binding": plan_versioning.binding(plan),
        "protocol": protocol.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
        "step_proofs": proofs,
        "results": [],
    }
    for step in statistical:
        # Ordinary reader repeats full replay; the report never skips provenance checks.
        value["results"].append(
            await p4_result_reader.read(db, owner, session_id, acceptance_id, step.id)
        )
        if len(canonical_bytes(value)) > MAX_REPORT_BYTES:
            raise failure("REPORT_LIMIT")
    value["report_hash"] = logical_hash(value)
    if len(canonical_bytes(value)) > MAX_REPORT_BYTES:
        raise failure("REPORT_LIMIT")
    return value


def _text(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _markdown(value) -> str:
    # Inline code suppresses Markdown/GFM autolinks and HTML interpretation.
    # A longer fence keeps arbitrary source backticks inside the code span.
    text = _text(value)
    fence = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    # GFM parses table pipes before inline code; escape them at the table layer.
    return fence + " " + text.replace("|", "\\|") + " " + fence


def bundle(value: dict) -> bytes:
    """Pure deterministic encoding of the already proved document, without I/O."""
    raw = canonical_bytes(value)
    if len(raw) > MAX_REPORT_BYTES:
        raise failure("REPORT_LIMIT")
    markdown = ["# Saved research report", "", NOTICE, ""]
    markup = [
        '<!doctype html><html lang="en"><meta charset="utf-8">',
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
        "form-action 'none'\">",
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>Saved research report</title><style>body{font:1rem/1.6 sans-serif;"
        "max-width:72rem;margin:2rem auto;padding:0 1rem;color:#222;background:#fff}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #aaa;"
        "padding:.4rem;text-align:left;overflow-wrap:anywhere}pre{white-space:pre-wrap;"
        "overflow-wrap:anywhere}code{overflow-wrap:anywhere}</style><body>",
        "<h1>Saved research report</h1><p>" + html.escape(NOTICE) + "</p>",
    ]
    for result in value["results"]:
        step = result["binding"]["step_id"]
        markdown += [
            "## Step " + _markdown(step),
            "",
            "| Saved field path | Value |",
            "| --- | --- |",
        ]
        markup += [
            "<h2>Step "
            + html.escape(step)
            + "</h2><table><tr><th>Saved field path</th><th>Value</th></tr>"
        ]
        for fact in result["facts"]:
            markdown.append(
                "| " + _markdown(fact["path"]) + " | " + _markdown(fact["value"]) + " |"
            )
            markup.append(
                "<tr><td><code>"
                + html.escape(_text(fact["path"]))
                + "</code></td><td>"
                + html.escape(_text(fact["value"]))
                + "</td></tr>"
            )
        markup.append("</table>")
        details = {key: result[key] for key in result if key != "facts"}
        encoded = json.dumps(details, ensure_ascii=False, indent=2, allow_nan=False)
        # JSON escapes newlines in user strings; HTML metacharacters also stay inert in Markdown.
        safe_json = encoded.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
        markdown += [
            "",
            "Saved bindings, availability and limitations:",
            "",
            "```json",
            safe_json,
            "```",
            "",
        ]
        markup += [
            "<h3>Saved bindings, availability and limitations</h3><pre>"
            + html.escape(encoded)
            + "</pre>"
        ]
    metadata = {key: item for key, item in value.items() if key != "results"}
    encoded = json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False)
    safe_json = encoded.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    markdown += ["## Accepted declaration and source manifest", "", "```json", safe_json, "```", ""]
    markup += [
        "<h2>Accepted declaration and source manifest</h2><pre>"
        + html.escape(encoded)
        + "</pre></body></html>"
    ]
    members = {
        "report.json": raw,
        "report.md": "\n".join(markdown).encode("utf-8"),
        "report.html": "\n".join(markup).encode("utf-8"),
    }
    manifest = {
        "version": "p4-saved-report-bundle-v1",
        "report_hash": value["report_hash"],
        "members": {
            name: {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
            for name, content in sorted(members.items())
        },
    }
    members["manifest.json"] = canonical_bytes(manifest)
    if sum(map(len, members.values())) > MAX_BUNDLE_BYTES:
        raise failure("REPORT_LIMIT")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(members.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, content)
    zip_bytes = output.getvalue()
    if len(zip_bytes) > MAX_BUNDLE_BYTES:
        raise failure("REPORT_LIMIT")
    return zip_bytes
