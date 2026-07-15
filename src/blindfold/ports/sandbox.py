"""ComputeSandbox port — run untrusted code with resolved inputs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class SandboxError(Exception):
    """Raised by ComputeSandbox implementations on timeout, parse, or exec failure."""


class ComputeSandbox(ABC):
    @abstractmethod
    def run(self, code: str, inputs: dict[str, Any], timeout_s: float) -> Any: ...
