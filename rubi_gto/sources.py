from __future__ import annotations

import ast
import copy
import fnmatch
import io
import json
import re
import tomllib
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .models import Record, SourceSpec
from .progress import NullProgress
from .snbt import Literal as SnbtLiteral
from .snbt import dump as dump_snbt
from .snbt import parse as parse_snbt


USER_AGENT = "rubi-gto/0.1"
MINECRAFT_VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
GENERIC_MATCH_TOKENS = {
    "addon",
    "api",
    "fabric",
    "forge",
    "gto",
    "jar",
    "lib",
    "library",
    "mc",
    "mod",
    "mods",
    "neoforge",
}
DEFAULT_LOCAL_INCLUDE_GLOBS = ["**/assets/*/lang/ja_jp.json", "**/ja_jp.json"]
DEFAULT_SOURCE_INCLUDE_GLOBS = ["src/main/resources/assets/*/lang/ja_jp.json", "**/assets/*/lang/ja_jp.json"]
DEFAULT_ARCHIVE_INCLUDE_GLOBS = ["assets/*/lang/ja_jp.json"]
DEFAULT_ANY_LANG_INCLUDE_GLOBS = ["**/assets/*/lang/*.json"]
DEFAULT_GUIDEME_INCLUDE_GLOBS = ["assets/*/ae2guide/**"]
DEFAULT_PATCHOULI_ASSET_INCLUDE_GLOBS = ["assets/*/patchouli_books/**"]
DEFAULT_PATCHOULI_DATA_INCLUDE_GLOBS = ["data/*/patchouli_books/**"]
FTBQUESTS_DIR_NAMES = {"ftbquests", "ftb_quests"}
FTBQUESTS_ROOT_NAMES = {"quests", "normal"}
GTO_TRANSLATIONS_PRIORITY_NAMESPACES = {"gto", "gtocore", "gto_core"}
GTM_PRIORITY_NAMESPACES = {"gtceu", "gregtech", "gtmodern"}
GUIDE_TEXT_SUFFIXES = {".md", ".txt"}
PATCHOULI_JSON_SUFFIXES = {".json", ".json5"}
PATCHOULI_TEXT_SUFFIXES = {".md", ".txt"}
JAPANESE_TEXT_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff々ヶ]")
LOCALE_SEGMENT_RE = re.compile(r"^_?[a-z]{2}_[a-z]{2}$")
FTBQUESTS_TRANSLATABLE_FIELDS = {"title", "subtitle", "description"}


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


def _json_string_entries(payload: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], str]]:
    entries: list[tuple[tuple[str, ...], str]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            entries.extend(_json_string_entries(value, path + (str(key),)))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            entries.extend(_json_string_entries(value, path + (str(index),)))
    elif isinstance(payload, str) and path:
        entries.append((path, payload))
    return entries


def _derive_lang_namespace(path: str, fallback: str | None) -> str:
    parts = PurePosixPath(path).parts
    if "assets" in parts:
        index = parts.index("assets")
        if index + 1 < len(parts):
            return parts[index + 1]
    if "data" in parts:
        index = parts.index("data")
        if index + 1 < len(parts):
            return parts[index + 1]
    if fallback:
        return fallback
    raise ValueError(f"could not derive namespace from path {path}")


def _contains_japanese(text: str) -> bool:
    return bool(JAPANESE_TEXT_RE.search(text))


def _looks_like_japanese_locale_path(path: str) -> bool:
    parts = {part.lower() for part in PurePosixPath(path).parts}
    return "ja_jp" in parts or "_ja_jp" in parts


def _explicit_locale_segments(path: str) -> set[str]:
    return {
        part.lower()
        for part in PurePosixPath(path).parts
        if LOCALE_SEGMENT_RE.match(part.lower())
    }


def _has_explicit_non_japanese_locale_path(path: str) -> bool:
    locales = _explicit_locale_segments(path)
    return bool(locales) and not {"ja_jp", "_ja_jp"} & locales


def _json_path_to_key(path: tuple[str, ...]) -> str:
    return ".".join(path)


def _set_nested_value(payload: Any, path: tuple[str, ...], value: str) -> None:
    current = payload
    for part in path[:-1]:
        if isinstance(current, list):
            current = current[int(part)]
        else:
            current = current[part]
    last = path[-1]
    if isinstance(current, list):
        current[int(last)] = value
    else:
        current[last] = value


def _record_metadata(
    *,
    source: SourceSpec,
    output_path: str,
    template_payload: Any | None = None,
    json_path: tuple[str, ...] | None = None,
    source_path: str,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "output_root": source.output_root or "resourcepack",
        "source_path": source_path,
    }
    if source.output_kind != "resourcepack":
        metadata["pack_meta"] = {
            "pack_id": source.id,
        }
    if template_payload is not None:
        metadata["template_payload"] = template_payload
    if json_path is not None:
        metadata["json_path"] = list(json_path)
    return metadata


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
    source_id: str | None = None,
) -> list[Record]:
    namespace = _derive_lang_namespace(path, source.target_namespace) if source.json_mode == "lang" else source.target_namespace
    if not namespace:
        raise ValueError(f"source {source.id} requires target_namespace for json_mode={source.json_mode}")
    if not _namespace_allowed_for_source(source, namespace):
        return []
    if source.json_mode == "lang":
        if not isinstance(payload, dict):
            raise ValueError(f"source {source.id} expected a JSON object at {path}")
        items = {key: value for key, value in payload.items() if isinstance(value, str)}
    else:
        items = { _json_path_to_key(json_path): value for json_path, value in _json_string_entries(payload)}
    return [
        Record(
            namespace=namespace,
            key=key,
            source_text=value,
            annotated_text=value,
            source_origin=f"{origin}:{path}",
            source_id=source_id or source.id,
            output_kind=source.output_kind,
            output_path=f"assets/{namespace}/lang/{source.locale or 'ja_jp'}.json" if source.json_mode == "lang" else None,
            metadata=_record_metadata(
                source=source,
                output_path=f"assets/{namespace}/lang/{source.locale or 'ja_jp'}.json" if source.json_mode == "lang" else path,
                source_path=path,
            )
            | _record_extra_metadata(source),
        )
        for key, value in items.items()
    ]


def _text_record(
    *,
    source: SourceSpec,
    origin: str,
    path: str,
    text: str,
    output_path: str,
    source_id: str | None = None,
    content_type: str = "text_file",
) -> Record | None:
    if not text:
        return None
    if _has_explicit_non_japanese_locale_path(path):
        return None
    if not _looks_like_japanese_locale_path(path) and not _contains_japanese(text):
        return None
    namespace = _derive_lang_namespace(path, source.target_namespace or source.id)
    if not _namespace_allowed_for_source(source, namespace):
        return None
    return Record(
        namespace=namespace,
        key=path,
        source_text=text,
        annotated_text=text,
        source_origin=f"{origin}:{path}",
        source_id=source_id or source.id,
        content_type=content_type,
        output_kind=source.output_kind,
        output_path=output_path,
        metadata=_metadata_with_source_extra(
            _record_metadata(source=source, output_path=output_path, source_path=path),
            source,
        ),
    )


def _records_from_generic_json(
    *,
    source: SourceSpec,
    origin: str,
    path: str,
    payload: Any,
    output_path: str,
    source_id: str | None = None,
    content_type: str = "json_strings",
) -> list[Record]:
    if _has_explicit_non_japanese_locale_path(path):
        return []
    namespace = _derive_lang_namespace(path, source.target_namespace or source.id)
    if not _namespace_allowed_for_source(source, namespace):
        return []
    entries = _json_string_entries(payload)
    if not entries:
        return []
    include_all = _looks_like_japanese_locale_path(path)
    if not include_all and not any(_contains_japanese(value) for _, value in entries):
        return []
    records: list[Record] = []
    for json_path, value in entries:
        if not include_all and not _contains_japanese(value):
            continue
        records.append(
            Record(
                namespace=namespace,
                key=f"{path}::{_json_path_to_key(json_path)}",
                source_text=value,
                annotated_text=value,
                source_origin=f"{origin}:{path}",
                source_id=source_id or source.id,
                content_type=content_type,
                output_kind=source.output_kind,
                output_path=output_path,
                metadata=_record_metadata(
                    source=source,
                    output_path=output_path,
                    template_payload=payload,
                    json_path=json_path,
                    source_path=path,
                )
                | _record_extra_metadata(source),
            )
        )
    return records


def _iter_archive_paths(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in {".jar", ".zip"}
    )


def _fallback_archive_source_id(archive_path: Path) -> str:
    stem = archive_path.stem
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-")
    return normalized or stem or archive_path.name


def _archive_metadata(archive: zipfile.ZipFile, archive_path: Path) -> dict[str, Any]:
    mod_ids: list[str] = []
    display_names: list[str] = []

    for metadata_name in ("META-INF/mods.toml", "META-INF/neoforge.mods.toml"):
        try:
            raw = archive.read(metadata_name).decode("utf-8")
        except KeyError:
            continue
        try:
            payload = tomllib.loads(raw)
        except Exception:
            continue
        for mod in payload.get("mods", []):
            mod_id = str(mod.get("modId", "")).strip()
            display_name = str(mod.get("displayName", "")).strip()
            if mod_id and mod_id not in mod_ids:
                mod_ids.append(mod_id)
            if display_name and display_name not in display_names:
                display_names.append(display_name)

    for metadata_name in ("fabric.mod.json", "quilt.mod.json"):
        try:
            raw_json = json.loads(archive.read(metadata_name).decode("utf-8"))
        except (KeyError, json.JSONDecodeError):
            continue
        if metadata_name == "fabric.mod.json":
            mod_id = str(raw_json.get("id", "")).strip()
            display_name = str(raw_json.get("name", "")).strip()
        else:
            loader = raw_json.get("quilt_loader", {})
            metadata = raw_json.get("metadata", {})
            mod_id = str(loader.get("id", "")).strip()
            display_name = str(metadata.get("name", "")).strip()
        if mod_id and mod_id not in mod_ids:
            mod_ids.append(mod_id)
        if display_name and display_name not in display_names:
            display_names.append(display_name)

    return {
        "source_id": mod_ids[0] if mod_ids else _fallback_archive_source_id(archive_path),
        "mod_ids": mod_ids,
        "display_names": display_names,
    }


def _archive_lang_members(archive: zipfile.ZipFile, include_globs: list[str]) -> list[str]:
    return _archive_matching_members(archive, include_globs, suffixes={".json"})


def _archive_matching_members(
    archive: zipfile.ZipFile,
    include_globs: list[str],
    *,
    suffixes: set[str] | None = None,
) -> list[str]:
    members: list[str] = []
    for member in sorted(archive.namelist()):
        pure_member = PurePosixPath(member)
        if member.endswith("/"):
            continue
        if suffixes is not None and pure_member.suffix.lower() not in suffixes:
            continue
        if _path_matches(member, include_globs):
            members.append(member)
    return members


def _dir_matching_members(
    root: Path,
    include_globs: list[str],
    *,
    suffixes: set[str] | None = None,
) -> list[str]:
    if not root.exists():
        return []
    members: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        suffix = PurePosixPath(rel).suffix.lower()
        if suffixes is not None and suffix not in suffixes:
            continue
        if _path_matches(rel, include_globs):
            members.append(rel)
    return members


def _derive_locale_from_lang_path(path: str) -> str:
    pure_path = PurePosixPath(path)
    parts = pure_path.parts
    if "lang" in parts:
        index = parts.index("lang")
        if index + 1 < len(parts):
            return PurePosixPath(parts[index + 1]).stem
    return pure_path.stem


def _namespaces_from_asset_paths(paths: list[str]) -> list[str]:
    namespaces: set[str] = set()
    for path in paths:
        try:
            namespaces.add(_derive_lang_namespace(path, None))
        except ValueError:
            continue
    return sorted(namespaces)


def _namespace_filter(values: Any) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(value).strip().lower() for value in values if str(value).strip()}


def _namespace_allowed_for_source(source: SourceSpec, namespace: str) -> bool:
    normalized = namespace.strip().lower()
    include_namespaces = _namespace_filter(source.extra.get("include_namespaces"))
    exclude_namespaces = _namespace_filter(source.extra.get("exclude_namespaces"))
    if include_namespaces and normalized not in include_namespaces:
        return False
    return normalized not in exclude_namespaces


def _patchouli_book_names(paths: list[str]) -> list[str]:
    books: set[str] = set()
    for path in paths:
        parts = PurePosixPath(path).parts
        if "patchouli_books" not in parts:
            continue
        index = parts.index("patchouli_books")
        if index + 1 < len(parts):
            books.add(parts[index + 1])
    return sorted(books)


def _pack_metadata_from_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "has_pack_mcmeta": True,
            "pack_id": None,
            "pack_description": None,
            "pack_format": None,
        }
    pack = payload.get("pack", {})
    description = pack.get("description") if isinstance(pack, dict) else None
    if isinstance(description, dict):
        description = description.get("text")
    return {
        "has_pack_mcmeta": True,
        "pack_id": payload.get("id"),
        "pack_description": description,
        "pack_format": pack.get("pack_format") if isinstance(pack, dict) else None,
    }


def _pack_metadata_from_dir(root: Path) -> dict[str, Any]:
    pack_path = root / "pack.mcmeta"
    if not pack_path.exists():
        return {
            "has_pack_mcmeta": False,
            "pack_id": None,
            "pack_description": None,
            "pack_format": None,
        }
    try:
        payload = json.loads(pack_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "has_pack_mcmeta": True,
            "pack_id": None,
            "pack_description": None,
            "pack_format": None,
            "pack_mcmeta_error": str(exc),
        }
    return _pack_metadata_from_payload(payload)


def _pack_metadata_from_archive(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        raw = archive.read("pack.mcmeta").decode("utf-8")
    except KeyError:
        return {
            "has_pack_mcmeta": False,
            "pack_id": None,
            "pack_description": None,
            "pack_format": None,
        }
    except Exception as exc:
        return {
            "has_pack_mcmeta": True,
            "pack_id": None,
            "pack_description": None,
            "pack_format": None,
            "pack_mcmeta_error": str(exc),
        }
    try:
        payload = json.loads(raw)
    except Exception as exc:
        return {
            "has_pack_mcmeta": True,
            "pack_id": None,
            "pack_description": None,
            "pack_format": None,
            "pack_mcmeta_error": str(exc),
        }
    return _pack_metadata_from_payload(payload)


def _normalize_source_id(label: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._:-]+", "-", label).strip("-")
    return normalized or label


def _archive_source_descriptor(
    archive_path: Path,
    *,
    include_globs: list[str] | None = None,
    source_id: str | None = None,
) -> dict[str, Any]:
    normalized_globs = list(include_globs or DEFAULT_ARCHIVE_INCLUDE_GLOBS)
    with zipfile.ZipFile(archive_path) as archive:
        metadata = _archive_metadata(archive, archive_path)
        members = _archive_lang_members(archive, normalized_globs)
        all_lang_members = _archive_matching_members(archive, list(DEFAULT_ANY_LANG_INCLUDE_GLOBS), suffixes={".json"})
        guide_members = _archive_matching_members(archive, list(DEFAULT_GUIDEME_INCLUDE_GLOBS))
        patchouli_asset_members = _archive_matching_members(archive, list(DEFAULT_PATCHOULI_ASSET_INCLUDE_GLOBS))
        patchouli_data_members = _archive_matching_members(archive, list(DEFAULT_PATCHOULI_DATA_INCLUDE_GLOBS))
        pack_metadata = _pack_metadata_from_archive(archive)
    detected_namespaces = sorted({_derive_lang_namespace(member, None) for member in members})
    return {
        "id": source_id or str(metadata["source_id"]),
        "type": "local_archive",
        "path": str(archive_path.resolve()),
        "include_globs": normalized_globs,
        "archive_name": archive_path.name,
        "detected_files": members,
        "detected_namespaces": detected_namespaces,
        "mod_ids": list(metadata["mod_ids"]),
        "display_names": list(metadata["display_names"]),
        "has_ja_jp": bool(members),
        "all_lang_files": all_lang_members,
        "all_lang_namespaces": _namespaces_from_asset_paths(all_lang_members),
        "available_locales": sorted({_derive_locale_from_lang_path(member) for member in all_lang_members}),
        "guide_files": guide_members,
        "guide_namespaces": _namespaces_from_asset_paths(guide_members),
        "patchouli_asset_files": patchouli_asset_members,
        "patchouli_asset_namespaces": _namespaces_from_asset_paths(patchouli_asset_members),
        "patchouli_data_files": patchouli_data_members,
        "patchouli_book_names": _patchouli_book_names(patchouli_asset_members + patchouli_data_members),
        **pack_metadata,
    }


def _dir_source_descriptor(
    root: Path,
    *,
    include_globs: list[str] | None = None,
    source_id: str | None = None,
) -> dict[str, Any]:
    normalized_globs = list(include_globs or DEFAULT_ARCHIVE_INCLUDE_GLOBS)
    members = _dir_matching_members(root, normalized_globs, suffixes={".json"})
    all_lang_members = _dir_matching_members(root, list(DEFAULT_ANY_LANG_INCLUDE_GLOBS), suffixes={".json"})
    guide_members = _dir_matching_members(root, list(DEFAULT_GUIDEME_INCLUDE_GLOBS))
    patchouli_asset_members = _dir_matching_members(root, list(DEFAULT_PATCHOULI_ASSET_INCLUDE_GLOBS))
    patchouli_data_members = _dir_matching_members(root, list(DEFAULT_PATCHOULI_DATA_INCLUDE_GLOBS))
    pack_metadata = _pack_metadata_from_dir(root)
    detected_namespaces = sorted({_derive_lang_namespace(member, None) for member in members})
    return {
        "id": source_id or root.name,
        "type": "local_dir",
        "path": str(root.resolve()),
        "include_globs": normalized_globs,
        "entry_name": root.name,
        "detected_files": members,
        "detected_namespaces": detected_namespaces,
        "has_ja_jp": bool(members),
        "all_lang_files": all_lang_members,
        "all_lang_namespaces": _namespaces_from_asset_paths(all_lang_members),
        "available_locales": sorted({_derive_locale_from_lang_path(member) for member in all_lang_members}),
        "guide_files": guide_members,
        "guide_namespaces": _namespaces_from_asset_paths(guide_members),
        "patchouli_asset_files": patchouli_asset_members,
        "patchouli_asset_namespaces": _namespaces_from_asset_paths(patchouli_asset_members),
        "patchouli_data_files": patchouli_data_members,
        "patchouli_book_names": _patchouli_book_names(patchouli_asset_members + patchouli_data_members),
        **pack_metadata,
    }


def _ingest_local_dir(source: SourceSpec, *, progress: NullProgress | None = None) -> list[Record]:
    if not source.path:
        raise ValueError(f"source {source.id} is missing path")
    root = Path(source.path)
    records: list[Record] = []
    matched_files = [
        file_path
        for file_path in sorted(root.rglob("*.json"))
        if _path_matches(file_path.relative_to(root).as_posix(), source.include_globs)
    ]
    for index, file_path in enumerate(matched_files, start=1):
        rel = file_path.relative_to(root).as_posix()
        if progress:
            progress.item("FILE", index, len(matched_files), rel, source.id)
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


def _instance_output_path_for_member(path: str) -> str:
    return path


def _replace_locale_in_path(path: str, locale: str) -> str:
    pure_path = PurePosixPath(path)
    parts = list(pure_path.parts)
    if "lang" in parts:
        index = parts.index("lang")
        if index + 1 < len(parts):
            suffix = PurePosixPath(parts[index + 1]).suffix
            parts[index + 1] = f"{locale}{suffix}"
            return PurePosixPath(*parts).as_posix()
    return path


def _ftbquests_text_filter(text: str) -> bool:
    if not text:
        return False
    if text.startswith("{") and text.endswith("}"):
        return False
    return True


def _ftbquests_list_item_path(base_path: tuple[str, ...], item: Any, index: int) -> tuple[str, ...]:
    if isinstance(item, dict) and "id" in item and isinstance(item["id"], str):
        return base_path + (f"{base_path[-1]}.{item['id']}",)
    return base_path + (f"{base_path[-1]}{index}",)


def _portability_from_source(source: SourceSpec) -> str:
    portability = str(source.extra.get("portability", "")).strip()
    if portability:
        return portability
    if source.type == "ftbquests_locale_snbt":
        return "overwrite_only"
    if source.type == "ftbquests_legacy_inline":
        return "portable" if source.extra.get("full_pack_rewrite_root") else "overwrite_only"
    if source.output_kind == "instance":
        return "overwrite_only"
    return "portable"


def _record_extra_metadata(source: SourceSpec) -> dict[str, Any]:
    portability = _portability_from_source(source)
    metadata: dict[str, Any] = {
        "portability": portability,
        "full_pack_supported": portability == "portable",
        "merge_priority": int(source.extra.get("merge_priority", 0)),
    }
    if source.extra.get("full_pack_output_root"):
        metadata["full_pack_output_root"] = str(source.extra["full_pack_output_root"])
    if source.extra.get("full_pack_rewrite_root"):
        metadata["full_pack_rewrite_root"] = str(source.extra["full_pack_rewrite_root"])
    return metadata


def _metadata_with_source_extra(metadata: dict[str, Any], source: SourceSpec) -> dict[str, Any]:
    merged = dict(metadata)
    merged.update(_record_extra_metadata(source))
    return merged


def _ftbquests_record(
    *,
    source: SourceSpec,
    source_id: str,
    source_origin: str,
    key: str,
    source_text: str,
    metadata: dict[str, Any],
) -> Record:
    namespace = source.target_namespace or str(source.extra.get("lang_namespace") or "ftbquests")
    return Record(
        namespace=namespace,
        key=key,
        source_text=source_text,
        annotated_text=source_text,
        source_origin=source_origin,
        source_id=source_id,
        content_type=str(metadata["content_type"]),
        output_kind=source.output_kind,
        output_path=metadata.get("output_path"),
        metadata=_metadata_with_source_extra(metadata, source),
    )


def _extract_legacy_ftbquests_records(
    source: SourceSpec,
    *,
    progress: NullProgress | None = None,
) -> list[Record]:
    if not source.path:
        raise ValueError(f"source {source.id} is missing path")
    quest_root = Path(source.path)
    namespace = source.target_namespace or str(source.extra.get("lang_namespace") or "ftbquests")
    lang_output_root = str(source.extra.get("lang_output_root") or source.output_root or "resourcepack")
    lang_output_path = f"assets/{namespace}/lang/{source.locale or 'ja_jp'}.json"
    rewritten_output_root = str(source.extra.get("rewritten_output_root") or source.output_root or "config/ftbquests/quests")
    full_pack_rewrite_root = source.extra.get("full_pack_rewrite_root")
    records: list[Record] = []
    snbt_files = sorted(path for path in quest_root.rglob("*.snbt") if "lang" not in path.relative_to(quest_root).parts)
    for index, file_path in enumerate(snbt_files, start=1):
        relative_path = file_path.relative_to(quest_root).as_posix()
        if progress:
            progress.item("FILE", index, len(snbt_files), relative_path, source.id)
        payload = parse_snbt(file_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            continue
        chapter = file_path.stem
        file_records = _legacy_ftbquests_records_from_payload(
            source=source,
            payload=payload,
            chapter=chapter,
            relative_path=relative_path,
            source_origin=str(file_path.resolve()),
            lang_output_root=lang_output_root,
            lang_output_path=lang_output_path,
            rewritten_output_root=rewritten_output_root,
            full_pack_rewrite_root=str(full_pack_rewrite_root) if full_pack_rewrite_root else None,
        )
        records.extend(file_records)
    return records


def _legacy_ftbquests_records_from_payload(
    *,
    source: SourceSpec,
    payload: dict[str, Any],
    chapter: str,
    relative_path: str,
    source_origin: str,
    lang_output_root: str,
    lang_output_path: str,
    rewritten_output_root: str,
    full_pack_rewrite_root: str | None,
) -> list[Record]:
    records: list[Record] = []

    def walk(node: Any, lang_key: str, rewrite_path: list[str]) -> None:
        if not isinstance(node, dict):
            return
        for key in [candidate for candidate in node.keys() if node[candidate]]:
            value = node[key]
            if isinstance(value, dict):
                walk(value, f"{lang_key}.{key}", rewrite_path + [key])
                continue
            if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
                for index, item in enumerate(value):
                    nested_key = f"{key}.{item['id']}" if isinstance(item.get("id"), str) else f"{key}{index}"
                    walk(item, f"{lang_key}.{nested_key}", rewrite_path + [nested_key])
                continue
            if key not in FTBQUESTS_TRANSLATABLE_FIELDS:
                continue
            records.extend(
                _legacy_ftbquests_field_records(
                    source=source,
                    lang_key=lang_key,
                    rewrite_path=rewrite_path,
                    field_name=key,
                    value=value,
                    relative_path=relative_path,
                    source_origin=source_origin,
                    lang_output_root=lang_output_root,
                    lang_output_path=lang_output_path,
                    rewritten_output_root=rewritten_output_root,
                    full_pack_rewrite_root=full_pack_rewrite_root,
                )
            )

    walk(payload, f"{source.target_namespace or source.extra.get('lang_namespace') or 'ftbquests'}.{chapter}", [])
    return records


def _legacy_ftbquests_field_records(
    *,
    source: SourceSpec,
    lang_key: str,
    rewrite_path: list[str],
    field_name: str,
    value: Any,
    relative_path: str,
    source_origin: str,
    lang_output_root: str,
    lang_output_path: str,
    rewritten_output_root: str,
    full_pack_rewrite_root: str | None,
) -> list[Record]:
    records: list[Record] = []
    base_metadata = {
        "quest_root_type": "ftbquests_legacy_inline",
        "content_type": "ftbquests_legacy_inline",
        "quest_file_path": relative_path,
        "rewritten_output_root": rewritten_output_root,
        "rewritten_output_path": relative_path,
        "generated_lang_output_root": lang_output_root,
        "generated_lang_output_path": lang_output_path,
        "generated_lang_namespace": source.target_namespace or str(source.extra.get("lang_namespace") or "ftbquests"),
    }
    if full_pack_rewrite_root:
        base_metadata["full_pack_rewrite_root"] = full_pack_rewrite_root
    if isinstance(value, str) and _ftbquests_text_filter(value):
        translation_key = f"{lang_key}.{field_name}"
        metadata = dict(base_metadata)
        metadata["translation_key"] = translation_key
        metadata["rewrite_path"] = list(rewrite_path)
        metadata["rewrite_field"] = field_name
        records.append(
            _ftbquests_record(
                source=source,
                source_id=source.id,
                source_origin=source_origin,
                key=f"{relative_path}::{translation_key}",
                source_text=value,
                metadata=metadata,
            )
        )
        return records
    if not isinstance(value, list):
        return records
    translated_index = 0
    for item in value:
        if not isinstance(item, str) or not _ftbquests_text_filter(item):
            continue
        if item.startswith("[") and item.endswith("]"):
            parsed_list = ast.literal_eval(item.replace("true", "True").replace("false", "False"))
            rich_index = 0
            for rich_item in parsed_list:
                if rich_item == "":
                    continue
                translation_key = f"{lang_key}.{field_name}{translated_index}.rich_text{rich_index}"
                metadata = dict(base_metadata)
                metadata["translation_key"] = translation_key
                metadata["rewrite_path"] = list(rewrite_path)
                metadata["rewrite_field"] = field_name
                metadata["rewrite_list_index"] = translated_index
                metadata["rewrite_rich_index"] = rich_index
                source_text = rich_item if isinstance(rich_item, str) else str(rich_item.get("text", ""))
                records.append(
                    _ftbquests_record(
                        source=source,
                        source_id=source.id,
                        source_origin=source_origin,
                        key=f"{relative_path}::{translation_key}",
                        source_text=source_text,
                        metadata=metadata,
                    )
                )
                rich_index += 1
        else:
            translation_key = f"{lang_key}.{field_name}{translated_index}"
            metadata = dict(base_metadata)
            metadata["translation_key"] = translation_key
            metadata["rewrite_path"] = list(rewrite_path)
            metadata["rewrite_field"] = field_name
            metadata["rewrite_list_index"] = translated_index
            records.append(
                _ftbquests_record(
                    source=source,
                    source_id=source.id,
                    source_origin=source_origin,
                    key=f"{relative_path}::{translation_key}",
                    source_text=item,
                    metadata=metadata,
                )
            )
        translated_index += 1
    return records


def _extract_locale_ftbquests_records(
    source: SourceSpec,
    *,
    progress: NullProgress | None = None,
) -> list[Record]:
    if not source.path:
        raise ValueError(f"source {source.id} is missing path")
    quest_root = Path(source.path)
    target_locale = str(source.extra.get("target_locale") or source.locale or "ja_jp")
    lang_root = quest_root / "lang"
    locale_files = sorted(lang_root.glob("*.snbt"))
    records: list[Record] = []
    for index, file_path in enumerate(locale_files, start=1):
        relative_path = file_path.relative_to(quest_root).as_posix()
        if progress:
            progress.item("FILE", index, len(locale_files), relative_path, source.id)
        payload = parse_snbt(file_path.read_text(encoding="utf-8"))
        records.extend(
            _locale_ftbquests_records_from_payload(
                source=source,
                payload=payload,
                relative_path=relative_path,
                source_origin=str(file_path.resolve()),
                target_locale=target_locale,
            )
        )
    return records


def _locale_ftbquests_records_from_payload(
    *,
    source: SourceSpec,
    payload: Any,
    relative_path: str,
    source_origin: str,
    target_locale: str,
) -> list[Record]:
    records: list[Record] = []
    if not isinstance(payload, dict):
        return records
    target_path = _replace_locale_in_path(relative_path, target_locale)
    for json_path, value in _json_string_entries(payload):
        records.append(
            _ftbquests_record(
                source=source,
                source_id=source.id,
                source_origin=source_origin,
                key=f"{relative_path}::{_json_path_to_key(json_path)}",
                source_text=value,
                metadata={
                    "content_type": "ftbquests_locale_snbt",
                    "quest_root_type": "ftbquests_locale_snbt",
                    "quest_file_path": relative_path,
                    "output_root": source.output_root or "config/ftbquests/quests",
                    "output_path": target_path,
                    "template_payload": payload,
                    "json_path": list(json_path),
                },
            )
        )
    return records


def _ingest_instance_payload(
    *,
    source: SourceSpec,
    origin: str,
    path: str,
    payload: bytes | str,
    source_id: str | None = None,
) -> list[Record]:
    suffix = PurePosixPath(path).suffix.lower()
    output_path = _instance_output_path_for_member(path)
    if "assets/" in path and "/lang/" in path and suffix == ".json":
        json_payload = json.loads(payload if isinstance(payload, str) else payload.decode("utf-8"))
        return _records_from_json(
            source=source,
            origin=origin,
            path=path,
            payload=json_payload,
            source_id=source_id,
        )
    if "/ae2guide/" in path and suffix in GUIDE_TEXT_SUFFIXES:
        text = payload if isinstance(payload, str) else payload.decode("utf-8")
        record = _text_record(
            source=source,
            origin=origin,
            path=path,
            text=text,
            output_path=output_path,
            source_id=source_id,
            content_type="guide_text",
        )
        return [record] if record else []
    if ("patchouli_books" in path or source.output_kind == "instance") and suffix in PATCHOULI_JSON_SUFFIXES:
        json_payload = json.loads(payload if isinstance(payload, str) else payload.decode("utf-8"))
        return _records_from_generic_json(
            source=source,
            origin=origin,
            path=path,
            payload=json_payload,
            output_path=output_path,
            source_id=source_id,
            content_type="patchouli_json",
        )
    if ("patchouli_books" in path or source.output_kind == "instance") and suffix in PATCHOULI_TEXT_SUFFIXES:
        text = payload if isinstance(payload, str) else payload.decode("utf-8")
        record = _text_record(
            source=source,
            origin=origin,
            path=path,
            text=text,
            output_path=output_path,
            source_id=source_id,
            content_type="patchouli_text",
        )
        return [record] if record else []
    return []


def _iter_instance_dir_members(root: Path) -> list[str]:
    members = set(_dir_matching_members(root, list(DEFAULT_ARCHIVE_INCLUDE_GLOBS), suffixes={".json"}))
    members.update(_dir_matching_members(root, list(DEFAULT_GUIDEME_INCLUDE_GLOBS)))
    members.update(_dir_matching_members(root, list(DEFAULT_PATCHOULI_ASSET_INCLUDE_GLOBS)))
    members.update(_dir_matching_members(root, list(DEFAULT_PATCHOULI_DATA_INCLUDE_GLOBS)))
    return sorted(members)


def _iter_instance_archive_members(archive: zipfile.ZipFile) -> list[str]:
    members = set(_archive_matching_members(archive, list(DEFAULT_ARCHIVE_INCLUDE_GLOBS), suffixes={".json"}))
    members.update(_archive_matching_members(archive, list(DEFAULT_GUIDEME_INCLUDE_GLOBS)))
    members.update(_archive_matching_members(archive, list(DEFAULT_PATCHOULI_ASSET_INCLUDE_GLOBS)))
    members.update(_archive_matching_members(archive, list(DEFAULT_PATCHOULI_DATA_INCLUDE_GLOBS)))
    return sorted(members)


def _ingest_instance_dir(source: SourceSpec, *, progress: NullProgress | None = None) -> list[Record]:
    if not source.path:
        raise ValueError(f"source {source.id} is missing path")
    if source.type == "ftbquests_legacy_inline":
        return _extract_legacy_ftbquests_records(source, progress=progress)
    if source.type == "ftbquests_locale_snbt":
        return _extract_locale_ftbquests_records(source, progress=progress)
    root = Path(source.path)
    members = set(_iter_instance_dir_members(root))
    if source.output_kind == "instance":
        for suffix in ("*.json", "*.json5", "*.md", "*.txt"):
            for path in root.rglob(suffix):
                if path.is_file():
                    members.add(path.relative_to(root).as_posix())
    ordered_members = sorted(members)
    records: list[Record] = []
    for index, rel in enumerate(ordered_members, start=1):
        if progress:
            progress.item("FILE", index, len(ordered_members), rel, source.id)
        path = root / rel
        raw = path.read_text(encoding="utf-8") if path.suffix.lower() in {".json", ".md", ".txt"} else path.read_bytes()
        records.extend(
            _ingest_instance_payload(
                source=source,
                origin=str(root.resolve()),
                path=rel,
                payload=raw,
            )
        )
    return records


def _ingest_instance_archive(source: SourceSpec, *, progress: NullProgress | None = None) -> list[Record]:
    if not source.path:
        raise ValueError(f"source {source.id} is missing path")
    archive_path = Path(source.path)
    records: list[Record] = []
    with zipfile.ZipFile(archive_path) as archive:
        metadata = _archive_metadata(archive, archive_path)
        members = _iter_instance_archive_members(archive)
        for index, member in enumerate(members, start=1):
            if progress:
                progress.item("FILE", index, len(members), member, archive_path.name)
            payload = archive.read(member)
            records.extend(
                _ingest_instance_payload(
                    source=source,
                    origin=str(archive_path.resolve()),
                    path=member,
                    payload=payload,
                    source_id=str(metadata["source_id"]),
                )
            )
    return records


def _ingest_github_archive(source: SourceSpec, *, progress: NullProgress | None = None) -> list[Record]:
    if not source.owner or not source.repo:
        raise ValueError(f"source {source.id} is missing owner/repo")
    ref = _resolve_github_ref(source)
    archive_bytes = _http_bytes(_archive_url(source.owner, source.repo, ref))
    records: list[Record] = []
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        matched_members = []
        for member in sorted(archive.namelist()):
            pure_member = PurePosixPath(member)
            if member.endswith("/") or pure_member.suffix.lower() != ".json":
                continue
            relative_parts = pure_member.parts[1:]
            rel = PurePosixPath(*relative_parts).as_posix()
            if not _path_matches(rel, source.include_globs):
                continue
            matched_members.append(member)
        for index, member in enumerate(matched_members, start=1):
            pure_member = PurePosixPath(member)
            relative_parts = pure_member.parts[1:]
            rel = PurePosixPath(*relative_parts).as_posix()
            if progress:
                progress.item("FILE", index, len(matched_members), rel, source.id)
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


def _ingest_minecraft_assets(source: SourceSpec, *, progress: NullProgress | None = None) -> list[Record]:
    locale = source.locale or "ja_jp"
    version_payload = _resolve_minecraft_version(source)
    asset_index = _http_json(version_payload["assetIndex"]["url"])
    asset_key = f"minecraft/lang/{locale}.json"
    asset = asset_index["objects"].get(asset_key)
    if not asset:
        raise ValueError(f"asset index does not contain {asset_key}")
    payload = json.loads(_http_bytes(_minecraft_asset_url(asset["hash"])).decode("utf-8"))
    if progress:
        progress.note("ASSET", f"{asset_key}  minecraft {source.minecraft_version}")
    return _records_from_json(
        source=source,
        origin=f"minecraft:{source.minecraft_version}:{locale}",
        path=asset_key,
        payload=payload,
    )


def _ingest_local_archive(source: SourceSpec, *, progress: NullProgress | None = None) -> list[Record]:
    if not source.path:
        raise ValueError(f"source {source.id} is missing path")
    archive_path = Path(source.path)
    records: list[Record] = []
    with zipfile.ZipFile(archive_path) as archive:
        members = _archive_lang_members(archive, source.include_globs or list(DEFAULT_ARCHIVE_INCLUDE_GLOBS))
        for member_index, member in enumerate(members, start=1):
            if progress:
                progress.item("LANG", member_index, len(members), member, archive_path.name)
            payload = json.loads(archive.read(member).decode("utf-8"))
            records.extend(
                _records_from_json(
                    source=source,
                    origin=str(archive_path.resolve()),
                    path=member,
                    payload=payload,
                )
            )
    return records


def _ingest_local_mod_archives(source: SourceSpec, *, progress: NullProgress | None = None) -> list[Record]:
    if not source.path:
        raise ValueError(f"source {source.id} is missing path")
    root = Path(source.path)
    archives = _iter_archive_paths(root)
    records: list[Record] = []
    for archive_index, archive_path in enumerate(archives, start=1):
        try:
            with zipfile.ZipFile(archive_path) as archive:
                metadata = _archive_metadata(archive, archive_path)
                members = _archive_lang_members(archive, source.include_globs or list(DEFAULT_ARCHIVE_INCLUDE_GLOBS))
        except zipfile.BadZipFile:
            if progress:
                progress.note("SKIP", f"{archive_path.name}  invalid archive")
            continue
        detail = f"{metadata['source_id']}  matches={len(members)}"
        if progress:
            progress.item("ARCHIVE", archive_index, len(archives), archive_path.name, detail)
        with zipfile.ZipFile(archive_path) as archive:
            for member_index, member in enumerate(members, start=1):
                if progress:
                    progress.item("LANG", member_index, len(members), member, archive_path.name)
                payload = json.loads(archive.read(member).decode("utf-8"))
                records.extend(
                    _records_from_json(
                        source=source,
                        origin=str(archive_path.resolve()),
                        path=member,
                        payload=payload,
                        source_id=str(metadata["source_id"]),
                    )
                )
    return records


def ingest_sources_with_report(
    sources: list[SourceSpec],
    *,
    progress: NullProgress | None = None,
) -> tuple[list[Record], list[dict[str, Any]]]:
    records: list[Record] = []
    source_results: list[dict[str, Any]] = []
    reporter = progress or NullProgress()
    enabled_sources = [source for source in sources if source.enabled]
    for index, source in enumerate(enabled_sources, start=1):
        reporter.item("SOURCE", index, len(enabled_sources), source.id, source.type)
        result: dict[str, Any] = {
            "source_id": source.id,
            "type": source.type,
            "path": source.path,
            "record_count": 0,
            "status": "ok",
            "error": None,
        }
        if not source.enabled:
            continue
        try:
            if source.type == "github_repo_archive":
                source_records = _ingest_github_archive(source, progress=reporter)
            elif source.type == "minecraft_assets":
                source_records = _ingest_minecraft_assets(source, progress=reporter)
            elif source.type == "local_dir":
                source_records = _ingest_local_dir(source, progress=reporter)
            elif source.type == "local_archive":
                source_records = _ingest_local_archive(source, progress=reporter)
            elif source.type == "local_mod_archives":
                source_records = _ingest_local_mod_archives(source, progress=reporter)
            elif source.type == "instance_dir":
                source_records = _ingest_instance_dir(source, progress=reporter)
            elif source.type == "instance_archive":
                source_records = _ingest_instance_archive(source, progress=reporter)
            elif source.type == "ftbquests_legacy_inline":
                source_records = _extract_legacy_ftbquests_records(source, progress=reporter)
            elif source.type == "ftbquests_locale_snbt":
                source_records = _extract_locale_ftbquests_records(source, progress=reporter)
            else:
                raise ValueError(f"unsupported source type: {source.type}")
            records.extend(source_records)
            result["record_count"] = len(source_records)
            reporter.done("SOURCE", f"{source.id}  records={len(source_records)}")
        except Exception as exc:
            result["status"] = "error"
            result["error"] = str(exc)
            reporter.note("FAIL", f"{source.id}  {exc}")
            reporter.done("SOURCE", f"{source.id}  error={exc}")
        source_results.append(result)
    return records, source_results


def ingest_sources(
    sources: list[SourceSpec],
    *,
    progress: NullProgress | None = None,
) -> tuple[list[Record], list[dict[str, str]]]:
    records, source_results = ingest_sources_with_report(sources, progress=progress)
    errors = [
        {"source_id": str(item["source_id"]), "error": str(item["error"])}
        for item in source_results
        if item.get("status") == "error"
    ]
    return records, errors


def _source_root_for_match(match: Path, search_root: Path) -> Path:
    current = match.parent
    while True:
        if (current / ".git").exists():
            return current
        if current == search_root:
            break
        if search_root not in current.parents:
            break
        current = current.parent

    parts = match.relative_to(search_root).parts
    if not parts:
        return search_root
    return search_root / parts[0]


def _search_terms_for_mod(mod: dict[str, Any]) -> list[str]:
    filename_stem = Path(str(mod.get("filename", ""))).stem
    values = [str(mod.get("name", "")), str(mod.get("id", "")), filename_stem]
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        terms.append(cleaned)
    return terms


def _source_stub_examples(mod: dict[str, Any]) -> dict[str, dict[str, Any]]:
    source_id = str(mod.get("id", "mod"))
    search_terms = _search_terms_for_mod(mod)
    repo_guess = re.sub(r"[^A-Za-z0-9._-]+", "-", search_terms[0]).strip("-") if search_terms else source_id
    return {
        "local_dir": {
            "id": source_id,
            "type": "local_dir",
            "path": f"../gto_repos/{repo_guess}",
            "include_globs": list(DEFAULT_LOCAL_INCLUDE_GLOBS),
        },
        "github_repo_archive": {
            "id": source_id,
            "type": "github_repo_archive",
            "owner": "REPLACE_ME",
            "repo": repo_guess,
            "ref": "main",
            "include_globs": list(DEFAULT_SOURCE_INCLUDE_GLOBS),
        },
    }


def discover_local_sources(search_root: Path) -> list[dict[str, Any]]:
    discovered: list[dict[str, Any]] = []
    grouped_matches: dict[Path, list[str]] = {}
    for match in sorted(search_root.rglob("*.json")):
        rel_to_root = match.relative_to(search_root).as_posix()
        if not _path_matches(rel_to_root, DEFAULT_LOCAL_INCLUDE_GLOBS):
            continue
        candidate = _source_root_for_match(match, search_root)
        grouped_matches.setdefault(candidate, []).append(match.relative_to(candidate).as_posix())

    for candidate in sorted(grouped_matches):
        matches = sorted(set(grouped_matches[candidate]))
        if not matches:
            continue
        detected_namespaces = sorted(
            {
                _derive_lang_namespace(match, None)
                for match in matches
                if "assets/" in match and "/lang/ja_jp.json" in match
            }
        )
        discovered.append(
            {
                "id": candidate.name,
                "type": "local_dir",
                "path": str(candidate.resolve()),
                "include_globs": list(DEFAULT_LOCAL_INCLUDE_GLOBS),
                "detected_files": matches,
                "detected_namespaces": detected_namespaces,
            }
        )
    return discovered


def discover_mod_archives(mods_dir: Path, *, source_id: str = "mods-folder") -> dict[str, Any]:
    archives = _iter_archive_paths(mods_dir)
    entries: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    skipped_archives: list[dict[str, Any]] = []
    failed_archives: list[dict[str, Any]] = []
    detected_namespaces: set[str] = set()
    all_lang_namespaces: set[str] = set()
    guide_namespaces: set[str] = set()
    patchouli_asset_namespaces: set[str] = set()
    patchouli_book_names: set[str] = set()
    used_ids: dict[str, int] = {}

    for archive_path in archives:
        try:
            descriptor = _archive_source_descriptor(archive_path, include_globs=list(DEFAULT_ARCHIVE_INCLUDE_GLOBS))
        except zipfile.BadZipFile:
            failed_archives.append(
                {
                    "archive_name": archive_path.name,
                    "path": str(archive_path.resolve()),
                    "error": "invalid_archive",
                }
            )
            continue
        except Exception as exc:
            failed_archives.append(
                {
                    "archive_name": archive_path.name,
                    "path": str(archive_path.resolve()),
                    "error": str(exc),
                }
            )
            continue
        has_relevant_content = bool(
            descriptor["all_lang_files"]
            or descriptor["guide_files"]
            or descriptor["patchouli_asset_files"]
            or descriptor["patchouli_data_files"]
        )
        if not has_relevant_content:
            skipped_archives.append(
                {
                    "archive_name": archive_path.name,
                    "path": str(archive_path.resolve()),
                    "reason": "no_relevant_lang_or_book_files",
                }
            )
            continue
        base_id = str(descriptor["id"])
        sequence = used_ids.get(base_id, 0) + 1
        used_ids[base_id] = sequence
        if sequence > 1:
            descriptor["id"] = f"{base_id}#{sequence}"
            descriptor["base_id"] = base_id
        entries.append(descriptor)
        detected_namespaces.update(descriptor["detected_namespaces"])
        all_lang_namespaces.update(descriptor.get("all_lang_namespaces", []))
        guide_namespaces.update(descriptor.get("guide_namespaces", []))
        patchouli_asset_namespaces.update(descriptor.get("patchouli_asset_namespaces", []))
        patchouli_book_names.update(descriptor.get("patchouli_book_names", []))
        if descriptor["has_ja_jp"]:
            sources.append(descriptor)

    return {
        "id": source_id,
        "archive_count": len(archives),
        "detected_archive_count": len(entries),
        "ja_source_count": len(sources),
        "detected_namespaces": sorted(detected_namespaces),
        "all_lang_namespaces": sorted(all_lang_namespaces),
        "guide_namespaces": sorted(guide_namespaces),
        "patchouli_asset_namespaces": sorted(patchouli_asset_namespaces),
        "patchouli_book_names": sorted(patchouli_book_names),
        "entries": entries,
        "sources": sources,
        "skipped_archives": skipped_archives,
        "failed_archives": failed_archives,
    }


def build_local_manifest(
    search_root: Path,
    *,
    pack_description: str,
    pack_format: int,
    include_vanilla: bool = False,
    minecraft_version: str = "1.20.1",
    locale: str = "ja_jp",
    extra_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    sources = discover_local_sources(search_root)
    if extra_sources:
        sources.extend(extra_sources)
    if include_vanilla:
        sources.insert(
            0,
            {
                "id": f"minecraft-vanilla-{minecraft_version}",
                "type": "minecraft_assets",
                "minecraft_version": minecraft_version,
                "locale": locale,
                "target_namespace": "minecraft",
            },
        )
    return {
        "pack": {
            "description": pack_description,
            "pack_format": pack_format,
        },
        "build": {
            "include_generated_by_default": True,
            "include_pending_by_default": True,
        },
        "sources": sources,
    }


def build_mod_archive_manifest(
    mods_dir: Path,
    *,
    pack_description: str,
    pack_format: int,
    include_vanilla: bool = False,
    minecraft_version: str = "1.20.1",
    locale: str = "ja_jp",
    source_id: str = "mods-folder",
) -> dict[str, Any]:
    discovery = discover_mod_archives(mods_dir, source_id=source_id)
    sources = list(discovery["sources"])
    if include_vanilla:
        sources.insert(
            0,
            {
                "id": f"minecraft-vanilla-{minecraft_version}",
                "type": "minecraft_assets",
                "minecraft_version": minecraft_version,
                "locale": locale,
                "target_namespace": "minecraft",
            },
        )
    return {
        "pack": {
            "description": pack_description,
            "pack_format": pack_format,
        },
        "build": {
            "include_generated_by_default": True,
            "include_pending_by_default": True,
        },
        "discovery": discovery,
        "sources": sources,
    }


def _pack_candidate_entries(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.iterdir()
        if path.is_dir() or (path.is_file() and path.suffix.lower() in {".zip", ".jar"})
    )


def discover_resource_packs(root: Path, *, source_prefix: str) -> dict[str, Any]:
    candidates = _pack_candidate_entries(root)
    entries: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    skipped_packs: list[dict[str, Any]] = []
    failed_packs: list[dict[str, Any]] = []
    detected_namespaces: set[str] = set()
    all_lang_namespaces: set[str] = set()
    available_locales: set[str] = set()
    guide_namespaces: set[str] = set()
    patchouli_asset_namespaces: set[str] = set()
    patchouli_book_names: set[str] = set()
    used_ids: dict[str, int] = {}

    for entry_path in candidates:
        try:
            if entry_path.is_dir():
                base_label = entry_path.name
                descriptor = _dir_source_descriptor(entry_path, include_globs=list(DEFAULT_ARCHIVE_INCLUDE_GLOBS))
            else:
                base_label = entry_path.stem
                descriptor = _archive_source_descriptor(entry_path, include_globs=list(DEFAULT_ARCHIVE_INCLUDE_GLOBS))
        except zipfile.BadZipFile:
            failed_packs.append(
                {
                    "entry_name": entry_path.name,
                    "path": str(entry_path.resolve()),
                    "error": "invalid_archive",
                }
            )
            continue
        except Exception as exc:
            failed_packs.append(
                {
                    "entry_name": entry_path.name,
                    "path": str(entry_path.resolve()),
                    "error": str(exc),
                }
            )
            continue

        has_relevant_content = bool(
            descriptor["all_lang_files"]
            or descriptor["guide_files"]
            or descriptor["patchouli_asset_files"]
            or descriptor["patchouli_data_files"]
        )
        if not has_relevant_content:
            skipped_packs.append(
                {
                    "entry_name": entry_path.name,
                    "path": str(entry_path.resolve()),
                    "reason": "no_relevant_lang_or_book_files",
                }
            )
            continue

        base_id = _normalize_source_id(f"{source_prefix}:{descriptor.get('pack_id') or base_label}")
        sequence = used_ids.get(base_id, 0) + 1
        used_ids[base_id] = sequence
        descriptor["id"] = base_id if sequence == 1 else f"{base_id}#{sequence}"
        if sequence > 1:
            descriptor["base_id"] = base_id

        entries.append(descriptor)
        detected_namespaces.update(descriptor.get("detected_namespaces", []))
        all_lang_namespaces.update(descriptor.get("all_lang_namespaces", []))
        available_locales.update(descriptor.get("available_locales", []))
        guide_namespaces.update(descriptor.get("guide_namespaces", []))
        patchouli_asset_namespaces.update(descriptor.get("patchouli_asset_namespaces", []))
        patchouli_book_names.update(descriptor.get("patchouli_book_names", []))
        if descriptor["has_ja_jp"]:
            sources.append(descriptor)

    return {
        "root": str(root.resolve()),
        "pack_count": len(candidates),
        "detected_pack_count": len(entries),
        "ja_source_count": len(sources),
        "detected_namespaces": sorted(detected_namespaces),
        "all_lang_namespaces": sorted(all_lang_namespaces),
        "available_locales": sorted(available_locales),
        "guide_namespaces": sorted(guide_namespaces),
        "patchouli_asset_namespaces": sorted(patchouli_asset_namespaces),
        "patchouli_book_names": sorted(patchouli_book_names),
        "entries": entries,
        "sources": sources,
        "skipped_packs": skipped_packs,
        "failed_packs": failed_packs,
    }


def discover_ftbquests(instance_root: Path) -> dict[str, Any]:
    config_root = instance_root / "config"
    if not config_root.exists():
        return {
            "config_root": str(config_root.resolve()),
            "quest_root_count": 0,
            "entries": [],
        }

    roots: list[Path] = []
    seen: set[str] = set()
    for directory in sorted(config_root.rglob("*")):
        if not directory.is_dir() or directory.name not in FTBQUESTS_DIR_NAMES:
            continue
        for root_name in FTBQUESTS_ROOT_NAMES:
            quest_root = directory / root_name
            if not quest_root.exists() or not quest_root.is_dir():
                continue
            resolved = str(quest_root.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            roots.append(quest_root)

    entries: list[dict[str, Any]] = []
    for quest_root in sorted(roots):
        snbt_files = sorted(path.relative_to(quest_root).as_posix() for path in quest_root.rglob("*.snbt"))
        lang_files = [path for path in snbt_files if path.startswith("lang/")]
        content_files = [path for path in snbt_files if not path.startswith("lang/")]
        quest_root_type = "ftbquests_locale_snbt" if lang_files else "ftbquests_legacy_inline"
        portability = "overwrite_only" if quest_root_type == "ftbquests_locale_snbt" else "portable"
        full_pack_rewrite_root = "assets/ftbquests" if quest_root_type == "ftbquests_legacy_inline" else None
        entries.append(
            {
                "path": str(quest_root.resolve()),
                "file_count": len(snbt_files),
                "content_file_count": len(content_files),
                "lang_file_count": len(lang_files),
                "available_locales": sorted({_derive_locale_from_lang_path(path) for path in lang_files}),
                "has_data_snbt": "data.snbt" in snbt_files,
                "has_chapter_groups_snbt": "chapter_groups.snbt" in snbt_files,
                "localization_style": "locale_snbt" if lang_files else "inline_or_key_rewritten",
                "quest_root_type": quest_root_type,
                "portability": portability,
                "portable": portability == "portable",
                "overwrite_output_root": str(quest_root.relative_to(instance_root).as_posix()),
                "full_pack_output_root": full_pack_rewrite_root,
                "full_pack_rewrite_root": full_pack_rewrite_root,
                "sample_files": snbt_files[:20],
            }
        )

    return {
        "config_root": str(config_root.resolve()),
        "quest_root_count": len(entries),
        "entries": entries,
    }


def discover_patchouli_external_books(instance_root: Path) -> dict[str, Any]:
    patchouli_root = instance_root / "patchouli_books"
    if not patchouli_root.exists():
        return {
            "root": str(patchouli_root.resolve()),
            "book_count": 0,
            "entries": [],
        }

    entries: list[dict[str, Any]] = []
    for book_json in sorted(patchouli_root.glob("*/book.json")):
        book_root = book_json.parent
        entries.append(
            {
                "book_name": book_root.name,
                "path": str(book_root.resolve()),
                "has_book_json": True,
                "asset_files": _dir_matching_members(book_root, list(DEFAULT_PATCHOULI_ASSET_INCLUDE_GLOBS)),
                "data_files": _dir_matching_members(book_root, list(DEFAULT_PATCHOULI_DATA_INCLUDE_GLOBS)),
            }
        )

    return {
        "root": str(patchouli_root.resolve()),
        "book_count": len(entries),
        "entries": entries,
    }


def _guide_entries(label: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    guide_entries: list[dict[str, Any]] = []
    for entry in entries:
        guide_files = list(entry.get("guide_files", []))
        if not guide_files:
            continue
        guide_entries.append(
            {
                "location_type": label,
                "source_id": entry.get("id"),
                "path": entry.get("path"),
                "guide_namespaces": list(entry.get("guide_namespaces", [])),
                "file_count": len(guide_files),
            }
        )
    return guide_entries


def _patchouli_entries(label: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    patchouli_entries: list[dict[str, Any]] = []
    for entry in entries:
        asset_files = list(entry.get("patchouli_asset_files", []))
        data_files = list(entry.get("patchouli_data_files", []))
        if not asset_files and not data_files:
            continue
        patchouli_entries.append(
            {
                "location_type": label,
                "source_id": entry.get("id"),
                "path": entry.get("path"),
                "patchouli_asset_namespaces": list(entry.get("patchouli_asset_namespaces", [])),
                "patchouli_book_names": list(entry.get("patchouli_book_names", [])),
                "asset_file_count": len(asset_files),
                "data_file_count": len(data_files),
            }
        )
    return patchouli_entries


def _instance_override_rules() -> list[dict[str, str]]:
    return [
        {
            "content_type": "openloader_resource_pack",
            "override_path": "config/openloader/resources/<pack>/...",
            "rule": "Open Loader loads folder and zip resource packs from config/openloader/resources with normal resource-pack semantics.",
        },
        {
            "content_type": "ftbquests_1_20_keyed_text",
            "override_path": "assets/<namespace>/lang/ja_jp.json",
            "rule": "On 1.20.1 GTO-style packs, quest SNBT is rewritten to translation keys and the visible text comes from a resource-pack lang file with the same namespace and keys.",
        },
        {
            "content_type": "ftbquests_1_21_locale_snbt",
            "override_path": "config/ftb_quests/quests/lang/<locale>.snbt",
            "rule": "On newer FTB Quests releases, localized quest text lives in lang/<locale>.snbt alongside the quest data instead of JSON lang files.",
        },
        {
            "content_type": "guideme_ae2guide",
            "override_path": "assets/<namespace>/ae2guide/**",
            "rule": "GuideME loads pages from the ae2guide subtree of all resource packs across all namespaces.",
        },
        {
            "content_type": "patchouli_resource_pack_books",
            "override_path": "assets/<namespace>/patchouli_books/** and data/<namespace>/patchouli_books/<book>/book.json",
            "rule": "Patchouli book contents override through the resource-pack asset paths when use_resource_pack is enabled; the book declaration stays under data or the external patchouli_books folder.",
        },
    ]


def _instance_source_from_entry(entry: dict[str, Any], *, output_kind: str, output_root: str) -> dict[str, Any]:
    entry_type = str(entry.get("type", "local_dir"))
    source_type = "instance_archive" if entry_type == "local_archive" else "instance_dir"
    payload = {
        "id": entry["id"],
        "type": source_type,
        "path": entry["path"],
        "output_kind": output_kind,
        "output_root": output_root,
        "locale": "ja_jp",
        "content_kinds": ["lang_json", "guide_text", "patchouli_json", "patchouli_text"],
        "portability": "portable" if output_kind != "instance" else "overwrite_only",
        "detected_namespaces": list(entry.get("detected_namespaces", [])),
        "detected_files": list(entry.get("detected_files", [])),
    }
    if output_kind != "instance":
        payload["full_pack_output_root"] = "resourcepack"
    return payload


def _instance_sources_from_discovery(
    discovery: dict[str, Any],
    *,
    output_kind: str,
    output_root_factory: Any,
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for entry in discovery.get("entries", []):
        has_relevant_content = bool(
            entry.get("has_ja_jp")
            or entry.get("guide_files")
            or entry.get("patchouli_asset_files")
            or entry.get("patchouli_data_files")
        )
        if not has_relevant_content:
            continue
        sources.append(
            _instance_source_from_entry(
                entry,
                output_kind=output_kind,
                output_root=output_root_factory(entry),
            )
        )
    return sources


def build_instance_manifest(
    instance_root: Path,
    *,
    pack_description: str,
    pack_format: int,
    include_vanilla: bool = False,
    minecraft_version: str = "1.20.1",
    locale: str = "ja_jp",
) -> dict[str, Any]:
    mods = discover_mod_archives(instance_root / "mods", source_id="mods-folder")
    openloader_resources = discover_resource_packs(
        instance_root / "config" / "openloader" / "resources",
        source_prefix="openloader",
    )
    resourcepacks = discover_resource_packs(
        instance_root / "resourcepacks",
        source_prefix="resourcepack",
    )
    ftbquests = discover_ftbquests(instance_root)
    sources: list[dict[str, Any]] = []
    if include_vanilla:
        sources.append(
            {
                "id": f"minecraft-vanilla-{minecraft_version}",
                "type": "minecraft_assets",
                "minecraft_version": minecraft_version,
                "locale": locale,
                "target_namespace": "minecraft",
            }
        )
    sources.extend(
        _instance_sources_from_discovery(
            mods,
            output_kind="resourcepack",
            output_root_factory=lambda entry: "resourcepack",
        )
    )
    sources.extend(
        _instance_sources_from_discovery(
            openloader_resources,
            output_kind="openloader",
            output_root_factory=lambda entry: f"config/openloader/resources/{entry.get('pack_id') or entry.get('entry_name') or entry.get('id')}",
        )
    )
    sources.extend(
        _instance_sources_from_discovery(
            resourcepacks,
            output_kind="resourcepack",
            output_root_factory=lambda entry: "resourcepack",
        )
    )
    quest_pack_entry = next(
        (
            entry
            for entry in openloader_resources.get("entries", [])
            if "quest" in str(entry.get("entry_name", "")).lower() or "quest" in str(entry.get("pack_id", "")).lower()
        ),
        None,
    )
    quest_pack_root = (
        f"config/openloader/resources/{quest_pack_entry.get('pack_id') or quest_pack_entry.get('entry_name')}"
        if quest_pack_entry
        else "config/openloader/resources/quests"
    )
    quest_namespace = None
    if quest_pack_entry and quest_pack_entry.get("all_lang_namespaces"):
        quest_namespace = list(quest_pack_entry["all_lang_namespaces"])[0]
    for entry in ftbquests.get("entries", []):
        if entry.get("quest_root_type") == "ftbquests_legacy_inline":
            sources.append(
                {
                    "id": f"ftbquests:{Path(entry['path']).name}",
                    "type": "ftbquests_legacy_inline",
                    "path": entry["path"],
                    "target_namespace": quest_namespace or "ftbquests",
                    "locale": locale,
                    "output_kind": "instance",
                    "output_root": entry["overwrite_output_root"],
                    "rewritten_output_root": entry["overwrite_output_root"],
                    "lang_output_root": quest_pack_root,
                    "full_pack_rewrite_root": entry.get("full_pack_rewrite_root"),
                    "full_pack_output_root": entry.get("full_pack_output_root"),
                    "content_kinds": ["ftbquests_legacy_inline", "ftbquests_generated_lang_json"],
                    "portability": entry.get("portability", "overwrite_only"),
                    "quest_root_type": entry.get("quest_root_type"),
                }
            )
        else:
            sources.append(
                {
                    "id": f"ftbquests:{Path(entry['path']).name}:lang",
                    "type": "ftbquests_locale_snbt",
                    "path": entry["path"],
                    "locale": locale,
                    "target_locale": locale,
                    "output_kind": "instance",
                    "output_root": entry["overwrite_output_root"],
                    "content_kinds": ["ftbquests_locale_snbt"],
                    "portability": entry.get("portability", "overwrite_only"),
                    "quest_root_type": entry.get("quest_root_type"),
                }
            )
    patchouli_external = discover_patchouli_external_books(instance_root)
    for entry in patchouli_external.get("entries", []):
        sources.append(
            {
                "id": f"patchouli:{entry['book_name']}",
                "type": "instance_dir",
                "path": entry["path"],
                "output_kind": "instance",
                "output_root": f"patchouli_books/{entry['book_name']}",
                "locale": locale,
                "content_kinds": ["patchouli_json", "patchouli_text"],
                "portability": "overwrite_only",
            }
        )
    return {
        "pack": {
            "description": pack_description,
            "pack_format": pack_format,
        },
        "build": {
            "include_generated_by_default": True,
            "include_pending_by_default": True,
            "target_layout": "instance",
        },
        "discovery": {
            "instance_root": str(instance_root.resolve()),
            "mods": mods,
            "openloader_resources": openloader_resources,
            "resourcepacks": resourcepacks,
            "ftbquests": ftbquests,
            "patchouli_external_books": patchouli_external,
        },
        "sources": sources,
    }


def build_instance_content_report(
    instance_root: Path,
    *,
    pack_description: str,
    pack_format: int,
    include_vanilla: bool = False,
    minecraft_version: str = "1.20.1",
    locale: str = "ja_jp",
) -> dict[str, Any]:
    manifest = build_instance_manifest(
        instance_root,
        pack_description=pack_description,
        pack_format=pack_format,
        include_vanilla=include_vanilla,
        minecraft_version=minecraft_version,
        locale=locale,
    )
    mods = manifest["discovery"]["mods"]
    openloader_resources = manifest["discovery"]["openloader_resources"]
    resourcepacks = manifest["discovery"]["resourcepacks"]
    ftbquests = manifest["discovery"]["ftbquests"]
    patchouli_external = discover_patchouli_external_books(instance_root)
    guide_sources = (
        _guide_entries("mods", list(mods.get("entries", [])))
        + _guide_entries("openloader_resources", list(openloader_resources.get("entries", [])))
        + _guide_entries("resourcepacks", list(resourcepacks.get("entries", [])))
    )
    patchouli_sources = (
        _patchouli_entries("mods", list(mods.get("entries", [])))
        + _patchouli_entries("openloader_resources", list(openloader_resources.get("entries", [])))
        + _patchouli_entries("resourcepacks", list(resourcepacks.get("entries", [])))
    )
    for entry in patchouli_external.get("entries", []):
        patchouli_sources.append(
            {
                "location_type": "patchouli_books",
                "source_id": entry.get("book_name"),
                "path": entry.get("path"),
                "patchouli_asset_namespaces": [],
                "patchouli_book_names": [entry.get("book_name")],
                "asset_file_count": len(entry.get("asset_files", [])),
                "data_file_count": len(entry.get("data_files", [])) + (1 if entry.get("has_book_json") else 0),
            }
        )

    portability_report = [
        {
            "source_id": source.get("id"),
            "type": source.get("type"),
            "portability": source.get("portability", "portable"),
            "overwrite_destination": source.get("output_root"),
            "full_pack_destination": source.get("full_pack_output_root") or source.get("output_root") if source.get("portability") == "portable" else None,
        }
        for source in manifest["sources"]
    ]

    return {
        "instance_root": str(instance_root.resolve()),
        "source_count": len(manifest["sources"]),
        "lang_source_count": len([source for source in manifest["sources"] if source.get("output_kind") == "resourcepack"]),
        "manifest": manifest,
        "mods": mods,
        "openloader_resources": openloader_resources,
        "resourcepacks": resourcepacks,
        "ftbquests": ftbquests,
        "patchouli_external_books": patchouli_external,
        "guide_sources": guide_sources,
        "patchouli_sources": patchouli_sources,
        "portability_report": portability_report,
        "override_rules": _instance_override_rules(),
    }


def _direct_repo_roots(search_root: Path) -> list[Path]:
    if not search_root.exists():
        return []
    return sorted(
        path
        for path in search_root.iterdir()
        if path.is_dir() and (path / ".git").exists()
    )


def _discover_repo_resourcepack_sources(
    repo_root: Path,
    *,
    locale: str,
    source_prefix: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sources: list[dict[str, Any]] = []
    discoveries: list[dict[str, Any]] = []
    resourcepack_dirs = sorted(path for path in repo_root.rglob("resourcepacks") if path.is_dir())
    for resourcepack_dir in resourcepack_dirs:
        discovery = discover_resource_packs(resourcepack_dir, source_prefix=source_prefix)
        discoveries.append(
            {
                "repo_root": str(repo_root.resolve()),
                "resourcepacks_root": str(resourcepack_dir.resolve()),
                "discovery": discovery,
            }
        )
        pack_sources = _instance_sources_from_discovery(
            discovery,
            output_kind="resourcepack",
            output_root_factory=lambda entry: "resourcepack",
        )
        for source in pack_sources:
            source["locale"] = locale
            source["merge_priority"] = 10
        sources.extend(pack_sources)
    return sources, discoveries


def _is_gto_translations_repo(repo_root: Path) -> bool:
    tokens = _tokenize_label(repo_root.name)
    normalized = _normalize_label(repo_root.name)
    return ("gto" in tokens and ("translation" in tokens or "translations" in tokens)) or "gtotranslation" in normalized


def _selected_gto_translation_repo_sources(
    repo_root: Path,
    *,
    locale: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    selected_sources: list[dict[str, Any]] = []
    discoveries: list[dict[str, Any]] = []
    selected_namespace_set: set[str] = set()
    for local_repo in _direct_repo_roots(repo_root):
        if not _is_gto_translations_repo(local_repo):
            continue
        pack_sources, pack_discoveries = _discover_repo_resourcepack_sources(
            local_repo,
            locale=locale,
            source_prefix=f"repo-pack:{local_repo.name}",
        )
        selected_from_repo = 0
        for source in pack_sources:
            detected_namespaces = {
                str(namespace).strip().lower()
                for namespace in source.get("detected_namespaces", [])
                if str(namespace).strip()
            }
            matching_namespaces = sorted(detected_namespaces & GTO_TRANSLATIONS_PRIORITY_NAMESPACES)
            if not matching_namespaces:
                continue
            source["merge_priority"] = 30
            source["include_namespaces"] = matching_namespaces
            source["source_role"] = "gto_translations_priority_lang"
            selected_sources.append(source)
            selected_from_repo += 1
            selected_namespace_set.update(matching_namespaces)
        discoveries.append(
            {
                "repo_root": str(local_repo.resolve()),
                "resourcepack_discoveries": pack_discoveries,
                "selected_source_count": selected_from_repo,
            }
        )
    return selected_sources, discoveries, selected_namespace_set


def _is_gregtech_modern_repo(repo_root: Path) -> bool:
    tokens = _tokenize_label(repo_root.name)
    normalized = _normalize_label(repo_root.name)
    return (
        ("gregtech" in tokens and "modern" in tokens)
        or "gregtechmodern" in normalized
        or "gtmodern" in tokens
    )


def _selected_gtm_repo_sources(
    repo_root: Path,
    *,
    pack_description: str,
    pack_format: int,
    minecraft_version: str,
    locale: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    selected_sources: list[dict[str, Any]] = []
    discoveries: list[dict[str, Any]] = []
    selected_namespaces: set[str] = set()
    for local_repo in _direct_repo_roots(repo_root):
        if not _is_gregtech_modern_repo(local_repo):
            continue
        local_manifest = build_local_manifest(
            local_repo,
            pack_description=pack_description,
            pack_format=pack_format,
            include_vanilla=False,
            minecraft_version=minecraft_version,
            locale=locale,
        )
        local_sources = [
            source
            for source in list(local_manifest.get("sources", []))
            if not _source_is_resourcepack_only_repo_source(source)
        ]
        selected_from_repo = 0
        for source in local_sources:
            detected_namespaces = {
                str(namespace).strip().lower()
                for namespace in source.get("detected_namespaces", [])
                if str(namespace).strip()
            }
            matching_namespaces = sorted(detected_namespaces & GTM_PRIORITY_NAMESPACES)
            if not matching_namespaces:
                continue
            source["merge_priority"] = 25
            source["include_namespaces"] = matching_namespaces
            source["source_role"] = "gregtech_modern_priority_lang"
            selected_sources.append(source)
            selected_from_repo += 1
            selected_namespaces.update(matching_namespaces)
        discoveries.append(
            {
                "repo_root": str(local_repo.resolve()),
                "local_manifest_source_count": len(local_sources),
                "selected_source_count": selected_from_repo,
            }
        )
    return selected_sources, discoveries, selected_namespaces


def _instance_mod_archive_sources(
    instance_root: Path,
    *,
    locale: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    mods = discover_mod_archives(instance_root / "mods", source_id="mods-folder")
    sources = _instance_sources_from_discovery(
        mods,
        output_kind="resourcepack",
        output_root_factory=lambda entry: "resourcepack",
    )
    for source in sources:
        source["merge_priority"] = 20
        source["exclude_namespaces"] = sorted(GTO_TRANSLATIONS_PRIORITY_NAMESPACES)
        source["source_role"] = "instance_mod_archive"
    return sources, mods


def build_gto_workflow_manifest(
    instance_root: Path,
    *,
    repo_root: Path,
    pack_description: str,
    pack_format: int,
    include_vanilla: bool = False,
    minecraft_version: str = "1.20.1",
    locale: str = "ja_jp",
) -> dict[str, Any]:
    instance_root = instance_root.resolve()
    repo_root = repo_root.resolve()
    gto_repo_sources, gto_repo_discovery, gto_priority_namespaces = _selected_gto_translation_repo_sources(
        repo_root,
        locale=locale,
    )
    gtm_repo_sources, gtm_repo_discovery, gtm_priority_namespaces = _selected_gtm_repo_sources(
        repo_root,
        pack_description=pack_description,
        pack_format=pack_format,
        minecraft_version=minecraft_version,
        locale=locale,
    )
    repo_sources = gto_repo_sources + gtm_repo_sources
    priority_repo_namespaces = gto_priority_namespaces | gtm_priority_namespaces
    instance_sources, instance_mods = _instance_mod_archive_sources(instance_root, locale=locale)
    for source in instance_sources:
        source["exclude_namespaces"] = sorted(
            set(str(namespace).strip().lower() for namespace in source.get("exclude_namespaces", []))
            | priority_repo_namespaces
        )

    sources: list[dict[str, Any]] = []
    if include_vanilla:
        sources.append(
            {
                "id": f"minecraft-vanilla-{minecraft_version}",
                "type": "minecraft_assets",
                "minecraft_version": minecraft_version,
                "locale": locale,
                "target_namespace": "minecraft",
                "merge_priority": 10,
            }
        )
    sources.extend(repo_sources)
    sources.extend(instance_sources)

    return {
        "pack": {
            "description": pack_description,
            "pack_format": pack_format,
        },
        "build": {
            "include_generated_by_default": True,
            "include_pending_by_default": True,
            "target_layout": "resourcepack",
        },
        "workflow": {
            "type": "gto_instance_repo_merge",
            "instance_root": str(instance_root),
            "repo_root": str(repo_root),
            "merge_rule": "gto_translations_repo_supplies_gto_namespaces_instance_mod_archives_supply_everything_else",
        },
        "discovery": {
            "repo_sources": {
                "gto_translations": gto_repo_discovery,
                "gregtech_modern": gtm_repo_discovery,
                "priority_namespaces": sorted(priority_repo_namespaces),
            },
            "instance_mod_archives": instance_mods,
            "skipped_instance_content_roots": [
                "config/ftbquests/quests",
                "config/openloader/resources",
                "resourcepacks",
            ],
        },
        "sources": sources,
    }


def _source_is_resourcepack_only_repo_source(source: dict[str, Any]) -> bool:
    detected_files = list(source.get("detected_files", []))
    return bool(detected_files) and all("resourcepacks/" in path for path in detected_files)


def _normalize_label(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _tokenize_label(text: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", text.lower())
        if token and token not in GENERIC_MATCH_TOKENS and len(token) > 1
    }


def _packwiz_update_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    update = payload.get("update", {})
    if "curseforge" in update:
        curseforge = update["curseforge"]
        return {
            "source": "curseforge",
            "project_id": curseforge.get("project-id"),
            "file_id": curseforge.get("file-id"),
        }
    if "modrinth" in update:
        modrinth = update["modrinth"]
        return {
            "source": "modrinth",
            "mod_id": modrinth.get("mod-id"),
            "version_id": modrinth.get("version"),
        }
    return {"source": None}


def _parse_packwiz_mod(mod_path: Path) -> dict[str, Any]:
    payload = tomllib.loads(mod_path.read_text(encoding="utf-8"))
    update = _packwiz_update_metadata(payload)
    return {
        "id": mod_path.stem.replace(".pw", ""),
        "name": payload.get("name", mod_path.stem),
        "filename": payload.get("filename", ""),
        "side": payload.get("side", ""),
        "update": update,
        "path": str(mod_path.resolve()),
    }


def discover_packwiz_mods(pack_root: Path) -> list[dict[str, Any]]:
    mods_dir = pack_root / "mods"
    if not mods_dir.exists():
        raise FileNotFoundError(f"packwiz mods directory not found at {mods_dir}")
    return [_parse_packwiz_mod(path) for path in sorted(mods_dir.glob("*.pw.toml"))]


def _repo_match_details(mod: dict[str, Any], source: dict[str, Any]) -> dict[str, Any] | None:
    source_id = str(source.get("id", ""))
    source_norm = _normalize_label(source_id)
    source_tokens = _tokenize_label(source_id)
    source_tokens.update(_tokenize_label(str(Path(source.get("path", "")).name)))
    for namespace in source.get("detected_namespaces", []):
        source_tokens.update(_tokenize_label(str(namespace)))

    mod_id = str(mod.get("id", ""))
    mod_name = str(mod.get("name", ""))
    mod_filename = str(mod.get("filename", ""))
    mod_tokens = _tokenize_label(mod_id)
    mod_tokens.update(_tokenize_label(mod_name))
    mod_tokens.update(_tokenize_label(Path(mod_filename).stem))
    mod_norm_candidates = {
        _normalize_label(mod_id),
        _normalize_label(mod_name),
        _normalize_label(Path(mod_filename).stem),
    }

    reasons: list[str] = []
    score = 0
    for mod_norm in mod_norm_candidates:
        if not mod_norm:
            continue
        if mod_norm == source_norm:
            score += 8
            reasons.append(f"normalized_exact:{mod_norm}")
        elif len(mod_norm) >= 4 and (mod_norm in source_norm or source_norm in mod_norm):
            score += 5
            reasons.append(f"normalized_contains:{mod_norm}")

    overlap = sorted(mod_tokens & source_tokens)
    if overlap:
        source_coverage = len(overlap) / max(1, len(source_tokens))
        mod_coverage = len(overlap) / max(1, len(mod_tokens))
        if len(overlap) >= 3 or min(source_coverage, mod_coverage) >= 0.67:
            score += len(overlap) * 2
            reasons.append(f"token_overlap:{','.join(overlap)}")
            reasons.append(f"token_coverage:{source_coverage:.2f}:{mod_coverage:.2f}")

    if score < 4:
        return None
    return {
        "id": source_id,
        "path": source.get("path"),
        "detected_namespaces": list(source.get("detected_namespaces", [])),
        "score": score,
        "reasons": reasons,
    }


def build_packwiz_translation_report(pack_root: Path, search_root: Path) -> dict[str, Any]:
    local_sources = discover_local_sources(search_root)
    mods_dir = pack_root / "mods"
    if mods_dir.exists():
        archive_source = discover_mod_archives(mods_dir, source_id="mods-folder")
        local_sources.extend(list(archive_source.get("sources", [])))
    mods = discover_packwiz_mods(pack_root)
    enriched_mods: list[dict[str, Any]] = []
    upstream_lookup_candidates: list[dict[str, Any]] = []
    matched_count = 0
    unresolved_count = 0

    for mod in mods:
        matches = sorted(
            (
                match
                for match in (_repo_match_details(mod, source) for source in local_sources)
                if match is not None
            ),
            key=lambda item: (-int(item["score"]), str(item["id"])),
        )
        if matches:
            matched_count += 1
        else:
            unresolved_count += 1
            upstream_lookup_candidates.append(
                {
                    "id": mod["id"],
                    "name": mod["name"],
                    "filename": mod["filename"],
                    "side": mod["side"],
                    "update": mod["update"],
                    "search_terms": _search_terms_for_mod(mod),
                    "source_stub_examples": _source_stub_examples(mod),
                }
            )
        enriched_mod = dict(mod)
        enriched_mod["likely_local_translation_repo_matches"] = matches
        enriched_mod["needs_upstream_lookup"] = not bool(matches)
        enriched_mods.append(enriched_mod)

    return {
        "pack_root": str(pack_root.resolve()),
        "search_root": str(search_root.resolve()),
        "local_source_count": len(local_sources),
        "mod_count": len(enriched_mods),
        "mods_with_likely_local_translation_repo": matched_count,
        "mods_without_likely_local_translation_repo": unresolved_count,
        "local_sources": local_sources,
        "upstream_lookup_candidates": upstream_lookup_candidates,
        "mods": enriched_mods,
    }
