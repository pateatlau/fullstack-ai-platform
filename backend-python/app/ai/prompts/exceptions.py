"""Prompt infrastructure errors."""


class PromptNotFoundError(LookupError):
    """Raised when a prompt template cannot be resolved."""

    def __init__(self, category: str, name: str, version: str) -> None:
        self.category = category
        self.name = name
        self.version = version
        super().__init__(
            f"Prompt template not found: category={category!r}, "
            f"name={name!r}, version={version!r}"
        )


class PromptRenderError(ValueError):
    """Raised when template rendering fails (e.g. missing variables)."""

    def __init__(
        self,
        category: str,
        name: str,
        version: str,
        message: str,
    ) -> None:
        self.category = category
        self.name = name
        self.version = version
        super().__init__(
            f"Failed to render prompt {category}/{name}.v{version}: {message}"
        )
