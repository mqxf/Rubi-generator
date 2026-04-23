from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SourceSpec:
    id: str
    type: str
    owner: str | None = None
    repo: str | None = None
    ref: str | None = None
    path: str | None = None
    include_globs: list[str] = field(default_factory=list)
    target_namespace: str | None = None
    json_mode: str = "lang"
    enabled: bool = True
    minecraft_version: str | None = None
    locale: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], defaults: dict[str, Any]) -> "SourceSpec":
        include_globs = list(data.get("include_globs") or defaults.get("include_globs") or [])
        return cls(
            id=data["id"],
            type=data["type"],
            owner=data.get("owner") or defaults.get("github_owner"),
            repo=data.get("repo"),
            ref=data.get("ref"),
            path=data.get("path"),
            include_globs=include_globs,
            target_namespace=data.get("target_namespace"),
            json_mode=data.get("json_mode", "lang"),
            enabled=data.get("enabled", True),
            minecraft_version=data.get("minecraft_version"),
            locale=data.get("locale"),
        )


@dataclass(slots=True)
class Record:
    namespace: str
    key: str
    source_text: str
    annotated_text: str
    source_origin: str
    source_id: str
    review_status: str = "pending"
    issues: list[str] = field(default_factory=list)
    notes: str | None = None

    @property
    def record_id(self) -> str:
        return f"{self.namespace}:{self.key}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.record_id,
            "namespace": self.namespace,
            "key": self.key,
            "source_text": self.source_text,
            "annotated_text": self.annotated_text,
            "source_origin": self.source_origin,
            "source_id": self.source_id,
            "review_status": self.review_status,
            "issues": list(self.issues),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Record":
        return cls(
            namespace=data["namespace"],
            key=data["key"],
            source_text=data["source_text"],
            annotated_text=data.get("annotated_text", data["source_text"]),
            source_origin=data["source_origin"],
            source_id=data["source_id"],
            review_status=data.get("review_status", "pending"),
            issues=list(data.get("issues", [])),
            notes=data.get("notes"),
        )
