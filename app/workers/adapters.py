"""Trusted deployment extension point, never selected from a request or queue message."""
import importlib
import inspect
from typing import Protocol

from app.models.screening_run import ResumeParseRun, ScreeningRun
from app.schemas.worker import ParseOutput, ScreeningOutput
from app.models.resume_extraction import ResumeExtractionRun
from app.schemas.extraction import ExtractionServiceOutput


class WorkerAdapter(Protocol):
    async def parse(self, run: ResumeParseRun) -> ParseOutput: ...
    async def screen(self, run: ScreeningRun) -> ScreeningOutput: ...
    async def aclose(self) -> None: ...


class ExtractionWorkerAdapter(Protocol):
    async def extract(self, run: ResumeExtractionRun) -> ExtractionServiceOutput: ...
    async def aclose(self) -> None: ...


def load_adapter(factory_path: str, queue: str) -> WorkerAdapter | ExtractionWorkerAdapter:
    """Factory is a synchronous no-argument function returning an async adapter."""
    if not factory_path or ":" not in factory_path:
        raise ValueError("Set WORKER_ADAPTER_FACTORY=module:factory before starting a worker")
    module, name = factory_path.split(":", 1)
    factory = getattr(importlib.import_module(module), name)
    if not callable(factory) or inspect.iscoroutinefunction(factory):
        raise ValueError("Worker adapter factory must be synchronous")
    adapter = factory()
    operation = {"resume-parse": "parse", "screening": "screen", "resume-extract": "extract"}[queue]
    for method in (operation, "aclose"):
        if not inspect.iscoroutinefunction(getattr(adapter, method, None)):
            raise ValueError(f"Worker adapter requires async {method}")
    return adapter
