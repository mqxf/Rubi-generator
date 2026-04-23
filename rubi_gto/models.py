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
    output_kind: str = "resourcepack"
    output_root: str | None = None
    content_kinds: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

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
            output_kind=data.get("output_kind", "resourcepack"),
            output_root=data.get("output_root"),
            content_kinds=list(data.get("content_kinds") or []),
            extra={
                key: value
                for key, value in data.items()
                if key
                not in {
                    "id",
                    "type",
                    "owner",
                    "repo",
                    "ref",
                    "path",
                    "include_globs",
                    "target_namespace",
                    "json_mode",
                    "enabled",
                    "minecraft_version",
                    "locale",
                    "output_kind",
                    "output_root",
                    "content_kinds",
                }
            },
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
    content_type: str = "lang_json"
    output_kind: str = "resourcepack"
    output_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

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
            "content_type": self.content_type,
            "output_kind": self.output_kind,
            "output_path": self.output_path,
            "metadata": dict(self.metadata),
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
            content_type=data.get("content_type", "lang_json"),
            output_kind=data.get("output_kind", "resourcepack"),
            output_path=data.get("output_path"),
            metadata=dict(data.get("metadata", {})),
        )
