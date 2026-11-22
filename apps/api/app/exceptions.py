"""Custom application exceptions with stable error codes."""

from fastapi import HTTPException, status


class StatForgeError(HTTPException):
    """Base exception with a stable error_code for client handling."""

    def __init__(
        self,
        status_code: int,
        error_code: str,
        detail: str,
    ) -> None:
        self.error_code = error_code
        super().__init__(status_code=status_code, detail=detail)


# --- Auth ---
class InvalidCredentialsError(StatForgeError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_401_UNAUTHORIZED,
            "AUTH_INVALID_CREDENTIALS",
            "Incorrect email or password.",
        )


class TokenExpiredError(StatForgeError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_401_UNAUTHORIZED,
            "AUTH_TOKEN_EXPIRED",
            "Session expired. Please log in again.",
        )


class TokenInvalidError(StatForgeError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_401_UNAUTHORIZED,
            "AUTH_TOKEN_INVALID",
            "Invalid or missing authentication token.",
        )


class EmailAlreadyRegisteredError(StatForgeError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_409_CONFLICT,
            "AUTH_EMAIL_EXISTS",
            "An account with this email already exists.",
        )


class WeakPasswordError(StatForgeError):
    def __init__(self, reason: str) -> None:
        super().__init__(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "AUTH_WEAK_PASSWORD",
            f"Password too weak: {reason}",
        )


# --- Projects ---
class ProjectNotFoundError(StatForgeError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_404_NOT_FOUND,
            "PROJECT_NOT_FOUND",
            "Project not found.",
        )


class ProjectAccessDeniedError(StatForgeError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_403_FORBIDDEN,
            "PROJECT_ACCESS_DENIED",
            "You do not have access to this project.",
        )


# --- Datasets ---
class DatasetNotFoundError(StatForgeError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_404_NOT_FOUND,
            "DATASET_NOT_FOUND",
            "Dataset not found.",
        )


class DatasetAccessDeniedError(StatForgeError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_403_FORBIDDEN,
            "DATASET_ACCESS_DENIED",
            "You do not have access to this dataset.",
        )


class FileTooLargeError(StatForgeError):
    def __init__(self, max_mb: int) -> None:
        super().__init__(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "FILE_TOO_LARGE",
            f"File exceeds the {max_mb} MB size limit.",
        )


class UnsupportedFileTypeError(StatForgeError):
    def __init__(self, detail: str = "Unsupported file type.") -> None:
        super().__init__(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "FILE_UNSUPPORTED_TYPE",
            detail,
        )


class FileValidationError(StatForgeError):
    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            error_code,
            detail,
        )


class DuplicateFileError(StatForgeError):
    def __init__(self, existing_dataset_id: str) -> None:
        super().__init__(
            status.HTTP_409_CONFLICT,
            "DATASET_DUPLICATE",
            f"This file has already been uploaded (dataset {existing_dataset_id}).",
        )


class StorageError(StatForgeError):
    def __init__(self, detail: str = "File storage operation failed.") -> None:
        super().__init__(
            status.HTTP_502_BAD_GATEWAY,
            "STORAGE_ERROR",
            detail,
        )


# --- Profiling ---
class ProfileNotFoundError(StatForgeError):
    def __init__(self, detail: str = "No profile found for this dataset.") -> None:
        super().__init__(
            status.HTTP_404_NOT_FOUND,
            "PROFILE_NOT_FOUND",
            detail,
        )


class ProfileColumnNotFoundError(StatForgeError):
    def __init__(self, column_name: str) -> None:
        super().__init__(
            status.HTTP_404_NOT_FOUND,
            "PROFILE_COLUMN_NOT_FOUND",
            f"Column '{column_name}' not found in the dataset profile.",
        )


class DatasetNotReadyError(StatForgeError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_409_CONFLICT,
            "DATASET_NOT_READY",
            "Dataset is not ready for profiling (upload incomplete or failed).",
        )


class ProfileComputationError(StatForgeError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "PROFILE_COMPUTE_FAILED",
            "Failed to compute the dataset profile.",
        )
