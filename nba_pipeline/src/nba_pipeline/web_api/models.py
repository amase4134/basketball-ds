"""Shared API envelope models for consumers and future generated clients."""
from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")

class PageMeta(BaseModel):
    page: int = Field(ge=1)
    page_size: Literal[25, 50, 100]
    total: int = Field(ge=0)
    filters: dict[str, Any]

class Page(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta

class DataUnavailableError(BaseModel):
    code: Literal["data_unavailable"] = "data_unavailable"
    dataset: str
    message: str
    retryable: bool = True

class ErrorEnvelope(BaseModel):
    error: DataUnavailableError
