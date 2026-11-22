"""Password hashing and verification using Argon2id."""

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=65536,  # 64 MiB
    parallelism=1,
    hash_len=32,
    type=Type.ID,
)

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


def validate_password_strength(password: str) -> str | None:
    """Return a reason string if the password is weak, else None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"must be at least {MIN_PASSWORD_LENGTH} characters"
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"must be at most {MAX_PASSWORD_LENGTH} characters"
    if password.isdigit():
        return "must not be only digits"
    if password.isalpha():
        return "must contain at least one digit or symbol"
    return None


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against its hash."""
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
