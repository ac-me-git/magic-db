class QueryError(ValueError):
    """The search text cannot be understood. The message is shown to the user."""


class CatalogUnavailable(RuntimeError):
    """No card catalog has been built yet, so there is nothing to search."""
