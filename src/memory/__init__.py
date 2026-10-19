"""Memory/knowledge substrate (cycle-006): provenance-tagged, tamper-evident, budgeted,
redacting store behind the control plane. Contents are untrusted data, never instructions."""

from .handlers import (NOTICE, SEARCH_CAP, SEARCH_SCHEMA, WRITE_CAP, WRITE_SCHEMA, MemorySearchHandler,
                       MemoryWriteHandler, register_memory)
from .ingest import fetch_to_memory_args, ingest_fetch
from .store import MemoryBudget, MemoryError_, MemoryStore, fts5_available, wrap

__all__ = ["NOTICE", "SEARCH_CAP", "SEARCH_SCHEMA", "WRITE_CAP", "WRITE_SCHEMA", "MemorySearchHandler",
           "MemoryWriteHandler", "register_memory", "fetch_to_memory_args", "ingest_fetch", "MemoryBudget",
           "MemoryError_", "MemoryStore", "fts5_available", "wrap"]
