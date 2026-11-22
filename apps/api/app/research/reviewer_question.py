"""Pure Reviewer text boundary, within the existing model-history byte budget."""

MAX_REVIEW_QUESTION_BYTES = 8192


def reviewer_question_fits(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_REVIEW_QUESTION_BYTES:
        return False
    try:
        return len(value.encode("utf-8")) <= MAX_REVIEW_QUESTION_BYTES
    except UnicodeError:
        return False


def reviewer_question_text(value: str) -> str:
    if not reviewer_question_fits(value):
        raise ValueError("Review question exceeds its UTF-8 message budget.")
    return value
