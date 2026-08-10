"""Plugin architecture exceptions."""

from __future__ import annotations


class PluginError(Exception):
    """Base class for plugin-related errors."""


class PluginLoadError(PluginError):
    """Raised when plugin loading fails in contexts that propagate errors."""


class PluginManifestError(PluginError):
    """Raised when a plugin manifest is invalid or cannot be parsed."""


class PluginRegistrationError(PluginError):
    """Raised when plugin registration violates contract or registrar is closed."""
