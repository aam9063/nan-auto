"""Registry failure types.

Kept together so a caller can fail open on `RegistryError` without importing
the catalog or transport modules (same house style as `clef_client.errors`).
"""


class RegistryError(RuntimeError):
    """Base class for every registry failure the gateway can fail open on."""


class CatalogUnavailable(RegistryError):
    """The `/v1/models` call did not answer usefully: transport, 5xx or a broken body."""


class CatalogAuthError(RegistryError):
    """The key was rejected by `/v1/models`; retrying cannot help."""


class CatalogFormatError(RegistryError):
    """The catalog body does not match the contract the registry depends on."""
