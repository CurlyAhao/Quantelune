"""Formal credential adapter, independent of settings, models and database imports."""

from app.preview.vault import WindowsVault


class ResearchWindowsVault(WindowsVault):
    @staticmethod
    def _target(namespace: str) -> str:
        # Use the inherited explicit WinVault implementation, a distinct target.
        WindowsVault._target(namespace)
        return f"StatForge.ResearchProvider.{namespace}"
