from __future__ import annotations

import fnmatch
import io
import json
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .models import Record, SourceSpec


USER_AGENT = "rubi-gto/0.1"
MINECRAFT_VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


def load_manifest(manifest_path: Path) -> tuple[dict[str, Any], list[SourceSpec]]:
    with manifest_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    defaults = raw.get("defaults", {})
    sources = [SourceSpec.from_dict(entry, defaults) for entry in raw.get("sources", [])]
    return raw, sources


def manifest_include_generated_default(manifest: dict[str, Any]) -> bool:
    build = manifest.get("build", {})
    return bool(build.get("include_generated_by_default", False))


def manifest_include_pending_default(manifest: dict[str, Any]) -> bool:
    build = manifest.get("build", {})
    return bool(build.get("include_pending_by_default", False))


def _http_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request) as response:
        return response.read()


def _resolve_github_ref(source: SourceSpec) -> str:
    if source.ref:
        return source.ref
    if not source.owner or not source.repo:
        raise ValueError(f"source {source.id} is missing owner/repo")
    metadata = _http_json(f"https://api.github.com/repos/{source.owner}/{source.repo}")
    return metadata["default_branch"]


def _archive_url(owner: str, repo: str, ref: str) -> str:
    return f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{ref}"


def _minecraft_asset_url(asset_hash: str) -> str:
    return f"https://resources.download.minecraft.net/{asset_hash[:2]}/{asset_hash}"


def _flatten_strings(payload: Any, prefix: str = "") -> dict[str, str]:
    flattened: dict[str, str] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            next_prefix = f"{prefix}.{key}" if prefix else key
            flattened.update(_flatten_strings(value, next_prefix))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            next_prefix = f"{prefix}.{index}" if prefix else str(index)
            flattened.update(_flatten_strings(value, next_prefix))
    elif isinstance(payload, str) and prefix:
        flattened[prefix] = payload
    return flattened


def _derive_lang_namespace(path: str, fallback: str | None) -> str:
    parts = PurePosixPath(path).parts
    if "assets" in parts:
        index = parts.index("assets")
        if index + 1 < len(parts):
            return parts[index + 1]
    if fallback:
        return fallback
    raise ValueError(f"could not derive namespace from path {path}")


def _path_matches(path: str, include_globs: list[str]) -> bool:
    if not include_globs:
        return path.endswith(".json")
    pure_path = PurePosixPath(path)
    normalized = path if path.startswith("/") else f"/{path}"
    return any(
        pure_path.match(pattern)
        or fnmatch.fnmatch(path, pattern)
        or fnmatch.fnmatch(normalized, pattern)
        for pattern in include_globs
    )


def _records_from_json(
    *,
    source: SourceSpec,
    origin: str,
    path: str,
    payload: Any,
) -> list[Record]:
    namespace = _derive_lang_namespace(path, source.target_namespace) if source.json_mode == "lang" else source.target_namespace
    if not namespace:
        raise ValueError(f"source {source.id} requires target_namespace for json_mode={source.json_mode}")
    if source.json_mode == "lang":
        if not isinstance(payload, dict):
            raise ValueError(f"source {source.id} expected a JSON object at {path}")
        items = {key: value for key, value in payload.items() if isinstance(value, str)}
    else:
        items = _flatten_strings(payload)
    return [
        Record(
            namespace=namespace,
            key=key,
            source_text=value,
            annotated_text=value,
            source_origin=f"{origin}:{path}",
            source_id=source.id,
        )
        for key, value in items.items()
    ]


def _ingest_local_dir(source: SourceSpec) -> list[Record]:
    if not source.path:
        raise ValueError(f"source {source.id} is missing path")
    root = Path(source.path)
    records: list[Record] = []
    for file_path in sorted(root.rglob("*.json")):
        rel = file_path.relative_to(root).as_posix()
        if not _path_matches(rel, source.include_globs):
            continue
        with file_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        records.extend(
            _records_from_json(
                source=source,
                origin=str(root.resolve()),
                path=rel,
                payload=payload,
            )
        )
    return records


def _ingest_github_archive(source: SourceSpec) -> list[Record]:
    if not source.owner or not source.repo:
        raise ValueError(f"source {source.id} is missing owner/repo")
    ref = _resolve_github_ref(source)
    archive_bytes = _http_bytes(_archive_url(source.owner, source.repo, ref))
    records: list[Record] = []
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for member in sorted(archive.namelist()):
            pure_member = PurePosixPath(member)
            if member.endswith("/") or pure_member.suffix.lower() != ".json":
                continue
            relative_parts = pure_member.parts[1:]
            rel = PurePosixPath(*relative_parts).as_posix()
            if not _path_matches(rel, source.include_globs):
                continue
            with archive.open(member) as handle:
                payload = json.loads(handle.read().decode("utf-8"))
            records.extend(
                _records_from_json(
                    source=source,
                    origin=f"github:{source.owner}/{source.repo}@{ref}",
                    path=rel,
                    payload=payload,
                )
            )
    return records


def _resolve_minecraft_version(source: SourceSpec) -> dict[str, Any]:
    if not source.minecraft_version:
        raise ValueError(f"source {source.id} is missing minecraft_version")
    manifest = _http_json(MINECRAFT_VERSION_MANIFEST_URL)
    for version in manifest["versions"]:
        if version["id"] == source.minecraft_version:
            return _http_json(version["url"])
    raise ValueError(f"minecraft version {source.minecraft_version} not found in official manifest")


def _ingest_minecraft_assets(source: SourceSpec) -> list[Record]:
    locale = source.locale or "ja_jp"
    version_payload = _resolve_minecraft_version(source)
    asset_index = _http_json(version_payload["assetIndex"]["url"])
    asset_key = f"minecraft/lang/{locale}.json"
    asset = asset_index["objects"].get(asset_key)
    if not asset:
        raise ValueError(f"asset index does not contain {asset_key}")
    payload = json.loads(_http_bytes(_minecraft_asset_url(asset["hash"])).decode("utf-8"))
    return _records_from_json(
        source=source,
        origin=f"minecraft:{source.minecraft_version}:{locale}",
        path=asset_key,
        payload=payload,
    )


def ingest_sources(sources: list[SourceSpec]) -> tuple[list[Record], list[dict[str, str]]]:
    records: list[Record] = []
    errors: list[dict[str, str]] = []
    for source in sources:
        if not source.enabled:
            continue
        try:
            if source.type == "github_repo_archive":
                records.extend(_ingest_github_archive(source))
            elif source.type == "minecraft_assets":
                records.extend(_ingest_minecraft_assets(source))
            elif source.type == "local_dir":
                records.extend(_ingest_local_dir(source))
            else:
                raise ValueError(f"unsupported source type: {source.type}")
        except Exception as exc:
            errors.append({"source_id": source.id, "error": str(exc)})
    return records, errors


def discover_local_sources(search_root: Path) -> list[dict[str, Any]]:
    discovered: list[dict[str, Any]] = []
    include_globs = ["**/assets/*/lang/ja_jp.json", "**/ja_jp.json"]
    for candidate in sorted(path for path in search_root.iterdir() if path.is_dir()):
        matches = sorted(
            match.relative_to(candidate).as_posix()
            for match in candidate.rglob("*.json")
            if _path_matches(match.relative_to(candidate).as_posix(), include_globs)
        )
        if not matches:
            continue
        discovered.append(
            {
                "id": candidate.name,
                "type": "local_dir",
                "path": str(candidate.resolve()),
                "include_globs": include_globs,
                "detected_files": matches,
            }
        )
    return discovered
