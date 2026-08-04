"""Blindfold — privacy proxy for LLM tool calls."""

from blindfold.core.rehydrator import rehydrate
from blindfold.core.tokenizer import describe_schema

__all__ = ["describe_schema", "rehydrate"]
