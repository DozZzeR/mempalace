"""Prepare external API documentation for normal MemPalace mining.

This module intentionally does not mine or search by itself. It stores raw
uploads verbatim, writes derived markdown files under API-oriented rooms, and
lets the existing project miner handle chunking/indexing.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

import yaml

from .config import normalize_wing_name, sanitize_name, strip_lone_surrogates

MAX_PREPARED_BLOCK_CHARS = 120_000
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}

ROOMS = {
    "api_endpoints": "Endpoint, method, path, request, and response documentation",
    "api_schemas": "Object schemas, DTOs, fields, and data shapes",
    "api_guides": "Authentication, pagination, rate limits, setup, and guides",
    "api_quirks": "Known API quirks, broken patterns, and verified workarounds",
    "api_raw": "Raw or uncategorized API documentation",
}

_SAFE_BATCH_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_DANGEROUS_FILENAME_RE = re.compile(r"[/\\]|\.\.|\x00")


def _ok(**kwargs) -> dict:
    return {"success": True, **kwargs}


def _err(exc: Exception) -> dict:
    return {"success": False, "error": str(exc), "error_class": type(exc).__name__}


def _provider_slug(provider: str) -> str:
    raw = sanitize_name(provider, "provider")
    slug = normalize_wing_name(raw.replace(".", "_").replace("'", ""))
    return sanitize_name(slug, "provider")


def _batch_id(batch_id: str | None = None) -> str:
    if batch_id is None or not str(batch_id).strip():
        return f"api_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    batch_id = str(batch_id).strip()
    if not _SAFE_BATCH_RE.match(batch_id) or ".." in batch_id:
        raise ValueError("batch_id contains invalid characters")
    return batch_id


def _safe_filename(filename: str) -> str:
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("filename must be a non-empty string")
    filename = filename.strip()
    if _DANGEROUS_FILENAME_RE.search(filename):
        raise ValueError("filename contains invalid path characters")
    if len(filename) > 180:
        raise ValueError("filename exceeds maximum length of 180 characters")
    return filename


def _batch_dir(palace_path: str, batch_id: str) -> Path:
    return Path(palace_path).expanduser().resolve() / "api_ingest" / "batches" / batch_id


def _manifest_path(batch_dir: Path) -> Path:
    return batch_dir / "manifest.json"


def _load_manifest(batch_dir: Path) -> dict:
    path = _manifest_path(batch_dir)
    if not path.exists():
        return {"files": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"files": []}
    if not isinstance(data, dict):
        return {"files": []}
    files = data.get("files")
    if not isinstance(files, list):
        data["files"] = []
    return data


def _write_manifest(batch_dir: Path, manifest: dict) -> None:
    batch_dir.mkdir(parents=True, exist_ok=True)
    _manifest_path(batch_dir).write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _decode_content(content: str | None, content_base64: str | None) -> bytes:
    if content is not None and content_base64 is not None:
        raise ValueError("pass either content or content_base64, not both")
    if content_base64 is not None:
        try:
            return base64.b64decode(content_base64, validate=True)
        except Exception as exc:
            raise ValueError("content_base64 is not valid base64") from exc
    if content is None:
        raise ValueError("content or content_base64 is required")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    return strip_lone_surrogates(content).encode("utf-8")


def add_file(
    *,
    palace_path: str,
    provider: str,
    filename: str,
    content: str | None = None,
    content_base64: str | None = None,
    batch_id: str | None = None,
    source_url: str | None = None,
) -> dict:
    """Store one raw API doc file under a batch without semantic rewriting."""
    try:
        provider_slug = _provider_slug(provider)
        batch = _batch_id(batch_id)
        safe_filename = _safe_filename(filename)
        raw_bytes = _decode_content(content, content_base64)

        root = _batch_dir(palace_path, batch)
        raw_dir = root / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / safe_filename
        raw_path.write_bytes(raw_bytes)

        sha = hashlib.sha256(raw_bytes).hexdigest()
        manifest = _load_manifest(root)
        manifest["provider"] = provider_slug
        manifest["batch_id"] = batch
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        entry = {
            "filename": safe_filename,
            "raw_path": str(raw_path),
            "raw_sha256": sha,
            "size_bytes": len(raw_bytes),
            "source_url": strip_lone_surrogates(source_url or ""),
        }
        manifest["files"] = [f for f in manifest.get("files", []) if f.get("filename") != safe_filename]
        manifest["files"].append(entry)
        _write_manifest(root, manifest)

        return _ok(
            provider=provider_slug,
            batch_id=batch,
            filename=safe_filename,
            raw_path=str(raw_path),
            raw_sha256=sha,
            size_bytes=len(raw_bytes),
        )
    except Exception as exc:
        return _err(exc)


def batch_status(palace_path: str, batch_id: str) -> dict:
    try:
        batch = _batch_id(batch_id)
        root = _batch_dir(palace_path, batch)
        manifest = _load_manifest(root)
        files = manifest.get("files", [])
        return _ok(
            batch_id=batch,
            provider=manifest.get("provider", ""),
            file_count=len(files),
            files=files,
        )
    except Exception as exc:
        return _err(exc)


def _classify(filename: str, text: str) -> str:
    name = filename.lower()
    head = text[:10000].lower()
    if "quirk" in name or "gotcha" in name or "workaround" in head or "silently" in head:
        return "api_quirks"
    if (
        "openapi" in name
        or "swagger" in name
        or "raml" in name
        or '"paths"' in head
        or "\npaths:" in head
        or "operationid" in head
        or re.search(r"\b(get|post|put|patch|delete)\s+/", head)
        or re.search(r"(?m)^/[^\s:]+:\s*(?:\n\s+.*)*?\n\s+(get|post|put|patch|delete):", head)
    ):
        return "api_endpoints"
    if (
        "schema" in name
        or "components:" in head
        or '"components"' in head
        or "properties:" in head
        or '"properties"' in head
    ):
        return "api_schemas"
    if any(token in head for token in ("auth", "oauth", "bearer", "pagination", "rate limit")):
        return "api_guides"
    return "api_raw"


def _decode_for_prepared(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def _safe_block_title(title: str) -> str:
    title = strip_lone_surrogates(title or "").strip()
    title = re.sub(r"\s+", " ", title)
    return title[:160] or "API documentation block"


def _strip_tags(value: str) -> str:
    value = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return html.unescape(re.sub(r"\s+", " ", value)).strip()


def _html_table_to_text(match: re.Match) -> str:
    rows = []
    for row in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", match.group(0)):
        cells = re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", row)
        if cells:
            rows.append(" | ".join(_strip_tags(cell) for cell in cells))
    return "\n" + "\n".join(row for row in rows if row.strip()) + "\n"


def _html_pre_to_text(match: re.Match) -> str:
    code = html.unescape(re.sub(r"(?s)<[^>]+>", "", match.group(1))).strip()
    if not code:
        return "\n"
    return f"\n```\n{code}\n```\n"


def _html_to_readable_text(value: str) -> str:
    value = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", value)
    value = re.sub(r"(?is)<pre[^>]*>(.*?)</pre>", _html_pre_to_text, value)
    value = re.sub(r"(?is)<table[^>]*>.*?</table>", _html_table_to_text, value)
    value = re.sub(r"(?is)</?(h[1-6]|p|div|section|article|li|ul|ol|br|tr)[^>]*>", "\n", value)
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    value = html.unescape(value)
    lines = []
    previous_blank = False
    for raw_line in value.splitlines():
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if not line:
            if not previous_blank and lines:
                lines.append("")
            previous_blank = True
            continue
        lines.append(line)
        previous_blank = False
    return "\n".join(lines).strip()


def _block(room: str, title: str, text: str, format_kind: str) -> dict:
    return {
        "room": room,
        "title": _safe_block_title(title),
        "text": strip_lone_surrogates(text).strip(),
        "format": format_kind,
    }


def _split_oversized_blocks(blocks: list[dict]) -> list[dict]:
    split = []
    for item in blocks:
        body = item["text"]
        if len(body) <= MAX_PREPARED_BLOCK_CHARS:
            split.append(item)
            continue
        part = 1
        start = 0
        while start < len(body):
            end = min(start + MAX_PREPARED_BLOCK_CHARS, len(body))
            if end < len(body):
                boundary = body.rfind("\n", start, end)
                if boundary > start + (MAX_PREPARED_BLOCK_CHARS // 2):
                    end = boundary
            split.append(
                _block(
                    item["room"],
                    f"{item['title']} part {part}",
                    body[start:end],
                    item["format"],
                )
            )
            start = end
            part += 1
    return split


def _load_yaml_or_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def _dump_yaml(data) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120)


def _looks_like_openapi(data) -> bool:
    return isinstance(data, dict) and isinstance(data.get("paths"), dict) and (
        "openapi" in data or "swagger" in data
    )


def _split_openapi(data: dict, text: str) -> list[dict]:
    blocks = []
    paths = data.get("paths") or {}
    for api_path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            method_l = str(method).lower()
            if method_l not in HTTP_METHODS:
                continue
            title = f"{method_l.upper()} {api_path}"
            blocks.append(
                _block(
                    "api_endpoints",
                    title,
                    f"# {title}\n\n```yaml\n{_dump_yaml({api_path: {method_l: operation}})}```\n",
                    "openapi",
                )
            )
    schemas = {}
    components = data.get("components")
    if isinstance(components, dict) and isinstance(components.get("schemas"), dict):
        schemas.update(components["schemas"])
    if isinstance(data.get("definitions"), dict):
        schemas.update(data["definitions"])
    for name, schema in schemas.items():
        blocks.append(
            _block(
                "api_schemas",
                f"Schema {name}",
                f"# Schema {name}\n\n```yaml\n{_dump_yaml({name: schema})}```\n",
                "openapi",
            )
        )
    if not blocks:
        blocks.append(_block(_classify("openapi.yaml", text), "OpenAPI document", text, "openapi"))
    return _split_oversized_blocks(blocks)


def _line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _split_raml(text: str) -> list[dict]:
    lines = text.splitlines(keepends=True)
    blocks = []

    resource_starts = [i for i, line in enumerate(lines) if re.match(r"^/[^\s:]+.*:\s*$", line)]
    for pos, start in enumerate(resource_starts):
        end = resource_starts[pos + 1] if pos + 1 < len(resource_starts) else len(lines)
        title = lines[start].strip().rstrip(":")
        blocks.append(_block("api_endpoints", title, "".join(lines[start:end]), "raml"))

    for section_name in ("types", "schemas"):
        start = next((i for i, line in enumerate(lines) if line.strip() == f"{section_name}:" and _line_indent(line) == 0), None)
        if start is None:
            continue
        end = len(lines)
        for i in range(start + 1, len(lines)):
            stripped = lines[i].strip()
            if stripped and _line_indent(lines[i]) == 0 and re.match(r"[A-Za-z_][\w.-]*:\s*$|/", stripped):
                end = i
                break
        blocks.append(_block("api_schemas", section_name, "".join(lines[start:end]), "raml"))

    if not blocks:
        blocks.append(_block(_classify("api.raml", text), "RAML document", text, "raml"))
    return _split_oversized_blocks(blocks)


def _split_xml(text: str) -> list[dict]:
    if not text.lstrip().startswith("<"):
        return []
    try:
        ElementTree.fromstring(text.encode("utf-8"))
    except ElementTree.ParseError:
        return []

    blocks = []
    for match in re.finditer(r"(?is)<[^>]*:?operation\b[^>]*>.*?</[^>]*:?operation>|<[^>]*:?operation\b[^>]*/>", text):
        snippet = match.group(0)
        title_match = re.search(r"\bname=['\"]([^'\"]+)", snippet)
        title = f"Operation {title_match.group(1)}" if title_match else "XML operation"
        blocks.append(_block("api_endpoints", title, snippet, "xml"))
    for match in re.finditer(r"(?is)<[^>]*:?message\b[^>]*>.*?</[^>]*:?message>|<[^>]*:?message\b[^>]*/>", text):
        snippet = match.group(0)
        title_match = re.search(r"\bname=['\"]([^'\"]+)", snippet)
        title = f"Message {title_match.group(1)}" if title_match else "XML message"
        blocks.append(_block("api_schemas", title, snippet, "xml"))
    for match in re.finditer(r"(?is)<[^>]*:?element\b[^>]*>.*?</[^>]*:?element>|<[^>]*:?element\b[^>]*/>", text):
        snippet = match.group(0)
        title_match = re.search(r"\bname=['\"]([^'\"]+)", snippet)
        title = f"Element {title_match.group(1)}" if title_match else "XML element"
        blocks.append(_block("api_schemas", title, snippet, "xml"))

    if not blocks:
        blocks.append(_block("api_raw", "XML document", text, "xml"))
    return _split_oversized_blocks(blocks)


def _split_html(text: str) -> list[dict]:
    if not re.search(r"(?is)<html\b|<body\b|<h[1-6]\b", text):
        return []
    headings = list(re.finditer(r"(?is)<h[1-6][^>]*>.*?</h[1-6]>", text))
    blocks = []
    if headings:
        for pos, heading in enumerate(headings):
            end = headings[pos + 1].start() if pos + 1 < len(headings) else len(text)
            section = text[heading.start() : end]
            title = _strip_tags(heading.group(0)) or "HTML section"
            readable = _html_to_readable_text(section)
            room = _classify("section.html", readable)
            blocks.append(_block(room, title, readable, "html"))
    else:
        plain = _html_to_readable_text(text)
        blocks.append(_block(_classify("document.html", plain), "HTML document", plain, "html"))
    return _split_oversized_blocks(blocks)


def _split_markdown_or_text(text: str, filename: str) -> list[dict]:
    headings = list(re.finditer(r"(?m)^#{1,6}\s+(.+)$", text))
    blocks = []
    if headings:
        for pos, heading in enumerate(headings):
            end = headings[pos + 1].start() if pos + 1 < len(headings) else len(text)
            section = text[heading.start() : end]
            title = heading.group(1).strip()
            blocks.append(_block(_classify(filename, section), title, section, "text"))
    else:
        blocks.append(_block(_classify(filename, text), Path(filename).stem or "API document", text, "text"))
    return _split_oversized_blocks(blocks)


def _logical_blocks_for_file(filename: str, text: str) -> list[dict]:
    name = filename.lower()
    data = None
    if name.endswith((".json", ".yaml", ".yml")) or "openapi" in text[:2000].lower() or "swagger" in text[:2000].lower():
        data = _load_yaml_or_json(text)
    if _looks_like_openapi(data):
        return _split_openapi(data, text)
    if name.endswith((".raml", ".yaml", ".yml")) or text.lstrip().startswith("#%RAML"):
        if text.lstrip().startswith("#%RAML"):
            return _split_raml(text)
    html_blocks = _split_html(text)
    if html_blocks:
        return html_blocks
    xml_blocks = _split_xml(text)
    if xml_blocks:
        return xml_blocks
    return _split_markdown_or_text(text, filename)



def _write_config(prepared_dir: Path, wing: str) -> None:
    rooms = "\n".join(
        f"  - name: {name}\n    description: {description!r}\n    keywords: [{name!r}]"
        for name, description in ROOMS.items()
    )
    prepared_dir.mkdir(parents=True, exist_ok=True)
    (prepared_dir / "mempalace.yaml").write_text(f"wing: {wing}\nrooms:\n{rooms}\n", encoding="utf-8")


def _prepared_filename(index: int, source_filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(source_filename).stem).strip("._") or "source"
    return f"{index:03d}_{stem}.md"


def _write_prepared_file(
    *,
    prepared_dir: Path,
    provider: str,
    room: str,
    source_filename: str,
    source_url: str,
    raw_sha256: str,
    text: str,
    index: int,
    block_title: str = "",
    format_kind: str = "",
) -> dict:
    room_dir = prepared_dir / room
    room_dir.mkdir(parents=True, exist_ok=True)
    path = room_dir / _prepared_filename(index, source_filename)
    body = (
        f"Provider: {provider}\n"
        "Kind: external_api_documentation\n"
        f"Room: {room}\n"
        f"Source file: {source_filename}\n"
        f"Source URL: {source_url or ''}\n"
        f"Raw SHA256: {raw_sha256}\n"
        f"Format: {format_kind or 'unknown'}\n"
        f"Block: {block_title or Path(source_filename).stem}\n"
        "\n"
        "---\n\n"
        f"{text}"
    )
    path.write_text(body, encoding="utf-8")
    return {"path": str(path), "room": room, "source_file": source_filename}


def _write_quirk_placeholder(prepared_dir: Path, provider: str) -> dict:
    room = "api_quirks"
    room_dir = prepared_dir / room
    room_dir.mkdir(parents=True, exist_ok=True)
    path = room_dir / "000_quirks.md"
    if not path.exists():
        path.write_text(
            f"Provider: {provider}\n"
            "Kind: api_quirk_registry\n"
            f"Room: {room}\n"
            "Source file: generated-quirks-placeholder.md\n\n"
            "---\n\n"
            "# API quirks\n\n"
            "Record verified integration quirks, broken assumptions, response paths, "
            "filter shapes, and workarounds for this provider here.\n",
            encoding="utf-8",
        )
    return {"path": str(path), "room": room, "source_file": "generated-quirks-placeholder.md"}


def process_batch(palace_path: str, batch_id: str, wing: str | None = None) -> dict:
    """Create prepared files for a staged batch and return mining targets."""
    try:
        batch = _batch_id(batch_id)
        root = _batch_dir(palace_path, batch)
        manifest = _load_manifest(root)
        files = manifest.get("files", [])
        if not files:
            raise ValueError(f"api ingest batch has no files: {batch}")
        provider = manifest.get("provider") or "api"
        target_wing = sanitize_name(normalize_wing_name(wing), "wing") if wing else f"api_{provider}"
        target_wing = sanitize_name(target_wing, "wing")

        prepared_dir = root / "prepared"
        if prepared_dir.exists():
            shutil.rmtree(prepared_dir)
        _write_config(prepared_dir, target_wing)

        prepared_files = []
        rooms_seen = set()
        prepared_files.append(_write_quirk_placeholder(prepared_dir, provider))
        rooms_seen.add("api_quirks")

        file_index = 1
        for entry in files:
            raw_path = Path(entry["raw_path"])
            raw = raw_path.read_bytes()
            text = _decode_for_prepared(raw)
            for logical in _logical_blocks_for_file(entry["filename"], text):
                room = logical["room"]
                rooms_seen.add(room)
                prepared_files.append(
                    _write_prepared_file(
                        prepared_dir=prepared_dir,
                        provider=provider,
                        room=room,
                        source_filename=entry["filename"],
                        source_url=entry.get("source_url", ""),
                        raw_sha256=entry.get("raw_sha256", ""),
                        text=logical["text"],
                        index=file_index,
                        block_title=logical.get("title", ""),
                        format_kind=logical.get("format", ""),
                    )
                )
                file_index += 1

        manifest["prepared_at"] = datetime.now(timezone.utc).isoformat()
        manifest["prepared_dir"] = str(prepared_dir)
        manifest["wing"] = target_wing
        manifest["rooms"] = sorted(rooms_seen)
        _write_manifest(root, manifest)

        return _ok(
            provider=provider,
            batch_id=batch,
            wing=target_wing,
            rooms=sorted(rooms_seen),
            prepared_dir=str(prepared_dir),
            prepared_files=prepared_files,
        )
    except Exception as exc:
        return _err(exc)
