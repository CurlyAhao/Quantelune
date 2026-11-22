"""Direct Windows credential backend; no keyring discovery or environment fallback."""

import sys
import uuid
from typing import Protocol

from app.exceptions import StatForgeError


class Vault(Protocol):
    def get(self, namespace: str, user_id: uuid.UUID) -> str | None: ...
    def set(self, namespace: str, user_id: uuid.UUID, secret: str) -> None: ...
    def delete(self, namespace: str, user_id: uuid.UUID) -> None: ...


def unavailable() -> StatForgeError:
    return StatForgeError(
        503, "SECURE_VAULT_UNAVAILABLE", "Windows secure credentials unavailable."
    )


class WindowsVault:
    @staticmethod
    def _target(namespace: str) -> str:
        if len(namespace) != 32 or any(c not in "0123456789abcdef" for c in namespace):
            raise unavailable()
        return f"StatForge.AgentPreview.{namespace}"

    @staticmethod
    def _backend():
        if sys.platform != "win32":
            raise unavailable()
        # Import this exact backend, never keyring.get_keyring()/entry-point discovery.
        from keyring.backends.Windows import WinVaultKeyring

        return WinVaultKeyring()

    def get(self, namespace: str, user_id: uuid.UUID) -> str | None:
        try:
            return self._backend().get_password(self._target(namespace), str(user_id))
        except Exception:
            raise unavailable() from None

    def set(self, namespace: str, user_id: uuid.UUID, secret: str) -> None:
        try:
            self._backend().set_password(self._target(namespace), str(user_id), secret)
        except Exception:
            raise unavailable() from None

    def delete(self, namespace: str, user_id: uuid.UUID) -> None:
        try:
            self._backend().delete_password(self._target(namespace), str(user_id))
        except Exception:
            raise unavailable() from None


vault: Vault = WindowsVault()
