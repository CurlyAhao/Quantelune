"""Bounded editor inputs, including incomplete text; never an execution request."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.ml.types import MLModelId, MLTaskType, SplitStrategy

Column = Annotated[str, Field(max_length=255)]
NumberText = Annotated[str, Field(max_length=40)]


class MLEditorState(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(default="模型实验", max_length=255)
    task_type: MLTaskType
    target: Column = ""
    features: list[Column] = Field(default_factory=list, max_length=512)
    categorical_features: list[Column] = Field(default_factory=list, max_length=512)
    group_variable: Column | None = None
    time_variable: Column | None = None
    split_strategy: SplitStrategy = SplitStrategy.STRATIFIED_KFOLD
    n_splits: NumberText = "5"
    random_seed: NumberText = "42"
    test_size: NumberText = "0.2"
    feature_selection_k: NumberText = ""
    val_size: float = Field(default=0.15, ge=0, lt=0.8)
    model_ids: list[MLModelId] = Field(default_factory=list, max_length=13)
    standardize: bool = True
    handle_outliers: bool = False
    impute_numeric: str = Field(default="median", max_length=32)
    impute_categorical: str = Field(default="most_frequent", max_length=32)
    model_params: str = Field(default="{}", max_length=16000)
