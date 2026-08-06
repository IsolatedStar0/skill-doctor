from __future__ import annotations

from .aime import AimeAdapter
from .base import IngestInput, LoadedTrace, SourceAdapter
from .generic import GenericAdapter


ADAPTERS: dict[str, SourceAdapter] = {
    "aime": AimeAdapter(),
    "generic": GenericAdapter(),
}

SUPPORTED_SOURCES = tuple(sorted(ADAPTERS))


def get_adapter(source: str) -> SourceAdapter:
    try:
        return ADAPTERS[source]
    except KeyError as error:
        raise ValueError(f"unsupported trace source: {source}") from error


__all__ = [
    "ADAPTERS",
    "SUPPORTED_SOURCES",
    "IngestInput",
    "LoadedTrace",
    "SourceAdapter",
    "get_adapter",
]
