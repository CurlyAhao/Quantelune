"""Deterministic, reproducible machine-learning experiment engine.

Design principles (consistent with the statistics engine):

* Structured data only; every run records split, seed, metrics, leakage checks
  and software versions for full reproducibility.
* Preprocessing is fit inside scikit-learn pipelines / CV folds — never on the
  full dataset — so evaluation is leak-free.
* Model comparison is multi-metric and never declares a single objective best.
* Per-model failures are isolated; one bad model does not fail the run.
"""

from app.ml.config import MLConfig
from app.ml.types import (
    MAX_FOLDS,
    MAX_MODELS_PER_RUN,
    MIN_FOLDS,
    MODEL_METADATA,
    MLModelId,
    MLTaskType,
    SplitStrategy,
)

__all__ = [
    "MLConfig",
    "run_ml_experiment",
    "MLModelId",
    "MLTaskType",
    "SplitStrategy",
    "MODEL_METADATA",
    "MIN_FOLDS",
    "MAX_FOLDS",
    "MAX_MODELS_PER_RUN",
]


def __getattr__(name: str):
    # The worker bootstrap must attach OS limits before scientific imports.
    if name == "run_ml_experiment":
        from app.ml.executor import run_ml_experiment

        return run_ml_experiment
    raise AttributeError(name)
