"""Compatibility for former mandatory output defaults; no wire limit in auto mode."""


def normalize_output_settings(value):
    if not isinstance(value, dict):
        return value
    result = dict(value)
    mode = result.get("output_length_mode")
    if mode is None:
        previous = result.get("max_output_tokens")
        mode = "auto" if previous is None or previous in (1024, 4096) else "manual"
    result["output_length_mode"] = mode
    if mode == "auto":
        result["max_output_tokens"] = None
    return result
