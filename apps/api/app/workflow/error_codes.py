"""Stable node diagnostics for locale-aware UI; original diagnostics are retained."""

import re


def node_error_code(error: Exception) -> str:
    declared = getattr(error, "error_code", None) or getattr(error, "code", None)
    if isinstance(declared, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", declared):
        return declared
    # Domain ValueError messages use these explicit namespaces. Human/library
    # text is not guessed or parsed into a fictitious specific diagnosis.
    if isinstance(error, ValueError) and re.fullmatch(
        r"(?:WORKFLOW|ML)_[A-Z0-9_]{1,70}", str(error)
    ):
        return str(error)
    return "WORKFLOW_NODE_FAILED"
