"""TokenStore port — abstract vault interface."""

from __future__ import annotations

import secrets
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from blindfold.core.lineage import VaultRecord


class TokenStore(ABC):
    @staticmethod
    def mint_token() -> str:
        """Mint a fresh token.

        On the port, not on an implementation: the delimiters and the hex width
        are a contract between whoever mints and the rehydrator's regex, not a
        detail of where records happen to be kept.
        """
        return f"⟦tok_{secrets.token_hex(4)}⟧"

    @abstractmethod
    def put(self, record: VaultRecord) -> None: ...

    @abstractmethod
    def get(self, token: str) -> VaultRecord | None: ...

    @abstractmethod
    def resolve(self, token: str) -> Any | None: ...

    @abstractmethod
    def find_by_session(self, session_id: str) -> list[VaultRecord]: ...

    @abstractmethod
    def invalidate_cascade(self, token: str) -> int: ...

    @abstractmethod
    def purge_expired(self, now: datetime | None = None) -> int: ...
