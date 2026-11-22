"""Core enumerations for the data-quality rule engine."""

from __future__ import annotations

import enum


class Severity(str, enum.Enum):
    """How serious a finding is. Ordered least → most severe."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Numeric weight per severity, used by the scoring model.
SEVERITY_WEIGHT: dict[Severity, float] = {
    Severity.INFO: 2.0,
    Severity.LOW: 4.0,
    Severity.MEDIUM: 8.0,
    Severity.HIGH: 15.0,
    Severity.CRITICAL: 25.0,
}

# Total ordering for comparing severities.
SEVERITY_ORDER: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class Category(str, enum.Enum):
    """Logical grouping of rules. Maps onto scoring dimensions."""

    STRUCTURE = "structure"
    COMPLETENESS = "completeness"
    UNIQUENESS = "uniqueness"
    VALIDITY = "validity"
    CONSISTENCY = "consistency"
    TEMPORAL = "temporal"
    DISTRIBUTION = "distribution"
    DOMAIN = "domain"


class RuleStatus(str, enum.Enum):
    """Outcome of evaluating a single rule."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class RuleLevel(str, enum.Enum):
    """Whether a rule inspects the whole dataset or individual columns."""

    DATASET = "dataset"
    COLUMN = "column"


class Dimension(str, enum.Enum):
    """Quality scoring dimensions."""

    COMPLETENESS = "completeness"
    VALIDITY = "validity"
    UNIQUENESS = "uniqueness"
    CONSISTENCY = "consistency"
    TEMPORAL = "temporal"
    USABILITY = "usability"


# Map each rule category to the scoring dimension it affects.
CATEGORY_TO_DIMENSION: dict[Category, Dimension] = {
    Category.STRUCTURE: Dimension.USABILITY,
    Category.COMPLETENESS: Dimension.COMPLETENESS,
    Category.UNIQUENESS: Dimension.UNIQUENESS,
    Category.VALIDITY: Dimension.VALIDITY,
    Category.CONSISTENCY: Dimension.CONSISTENCY,
    Category.TEMPORAL: Dimension.TEMPORAL,
    Category.DISTRIBUTION: Dimension.VALIDITY,
    Category.DOMAIN: Dimension.VALIDITY,
}

# Dimension weights for the overall score (must sum to 1.0).
DIMENSION_WEIGHTS: dict[Dimension, float] = {
    Dimension.COMPLETENESS: 0.25,
    Dimension.VALIDITY: 0.20,
    Dimension.UNIQUENESS: 0.15,
    Dimension.CONSISTENCY: 0.15,
    Dimension.TEMPORAL: 0.10,
    Dimension.USABILITY: 0.15,
}
