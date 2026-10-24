"""Manifest schema: loading, validation and safety checks.

The manifest is the contract between the browser side (which collects) and the
Python side (which repackages).  Keeping one validator here means the userscript
and the CLI cannot drift apart.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from adapters.selectors import PLATFORM_NAME, SCHEMA_VERSION

__all__ = [
    "ManifestError",
    "load_manifest",
    "save_manifest",
    "validate_manifest",
    "new_manifest",
    "assert_no_secrets",
    "iter_sessions",
    "manifest_stats",
]

# Keys that must never appear in a manifest anywhere, at any depth.
#
# Detection normalises the key first (camelCase -> snake_case, hyphens/dots to
# underscores) and then looks for whole words, so `api-key`, `apiKey` and
# `API_KEY` are all caught without also flagging innocent names like
# `keyPoints` (the summary keyword list) or `url_had_signature` (a boolean).
_FORBIDDEN_EXACT = {
    "cookie", "cookies", "authorization", "auth", "password", "passwd", "pwd",
    "secret", "jwt", "token", "sessionid", "credential", "credentials",
    "signature", "signture", "apikey", "access_token", "refresh_token",
    "set_cookie", "authorization_header",
}
_FORBIDDEN_WORDS = {
    "cookie", "cookies", "authorization", "password", "passwd", "pwd", "secret",
    "jwt", "token", "credential", "credentials", "signature", "signture",
    "apikey",
}

_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def _normalize_key(key) -> str:
    text = _CAMEL_BOUNDARY_RE.sub("_", str(key))
    return _NON_WORD_RE.sub("_", text.lower()).strip("_")


def _is_forbidden_key(key) -> bool:
    normalized = _normalize_key(key)
    if not normalized:
        return False
    if normalized in _FORBIDDEN_EXACT:
        return True
    # Also catch multi-word spellings of a single term: api_key -> apikey,
    # session_id -> sessionid, access_token -> accesstoken.
    if normalized.replace("_", "") in _FORBIDDEN_EXACT:
        return True
    return bool(set(normalized.split("_")) & _FORBIDDEN_WORDS)


# A signed URL parameter that survived sanitisation.
_SIGNED_URL_RE = re.compile(
    r"[?&](key|token|jwt|signture|signature|access_token)=([^&=\s]{12,})",
    re.IGNORECASE,
)


class ManifestError(ValueError):
    """Raised when a manifest is structurally invalid or unsafe."""


def new_manifest(course_title: str | None = None, teacher: str | None = None) -> dict:
    """Create an empty, schema-valid manifest."""
    return {
        "schema_version": SCHEMA_VERSION,
        "platform": PLATFORM_NAME,
        "exporter": {"name": "sjtu-course-exporter", "version": "0.1.0"},
        "exported_at": None,
        "source": {"kind": "unknown", "name": None, "capture_scope": "unknown"},
        "course": {"id": None, "title": course_title, "teacher": teacher},
        "discovered_sessions": [],
        "discovered_session_count": 0,
        "sessions": [],
        "extraction": {"method": "dom", "notes": None},
        "warnings": [],
    }


def load_manifest(path: str | Path) -> dict:
    """Read a manifest JSON file, tolerating a UTF-8 BOM."""
    p = Path(path)
    if not p.exists():
        raise ManifestError(f"manifest not found: {p}")
    try:
        text = p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:  # pragma: no cover - unusual
        raise ManifestError(f"{p} is not valid UTF-8: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{p} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"{p}: top level must be a JSON object")
    return data


def save_manifest(manifest: dict, path: str | Path) -> Path:
    """Write *manifest* as UTF-8 JSON with non-ASCII text preserved."""
    p = Path(path)
    if p.parent and str(p.parent):
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return p


def assert_no_secrets(manifest: dict) -> None:
    """Raise :class:`ManifestError` if anything credential-shaped is present.

    This is a hard gate: the project promises never to persist cookies, tokens
    or signed URLs, and an exported manifest is exactly the artefact most likely
    to be shared or committed by accident.
    """
    problems: list[str] = []

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key_path = f"{path}.{key}" if path else key
                if _is_forbidden_key(key):
                    # "url_had_signature" and similar booleans are fine; only a
                    # *populated* credential field is a problem.
                    if isinstance(value, str) and value:
                        problems.append(f"{key_path}: credential-like key holds a value")
                walk(value, key_path)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")
        elif isinstance(node, str):
            m = _SIGNED_URL_RE.search(node)
            if m:
                problems.append(f"{path}: signed URL parameter {m.group(1)!r} leaked")

    walk(manifest, "")
    if problems:
        raise ManifestError(
            "manifest contains data that must never be exported:\n  - "
            + "\n  - ".join(problems)
        )


_REQUIRED_TOP_LEVEL = {
    "schema_version": int,
    "platform": str,
    "course": dict,
    "sessions": list,
}


def validate_manifest(manifest: dict, *, allow_future_schema: bool = False) -> list[str]:
    """Validate structure and return a list of non-fatal warnings.

    Raises :class:`ManifestError` for anything that would make downstream
    packaging produce silently wrong output.
    """
    if not isinstance(manifest, dict):
        raise ManifestError("manifest must be a JSON object")

    for key, expected in _REQUIRED_TOP_LEVEL.items():
        if key not in manifest:
            raise ManifestError(f"manifest is missing required key: {key!r}")
        if not isinstance(manifest[key], expected):
            raise ManifestError(
                f"manifest[{key!r}] must be {expected.__name__}, "
                f"got {type(manifest[key]).__name__}"
            )

    version = manifest["schema_version"]
    if not isinstance(version, int):
        raise ManifestError("schema_version must be an integer")
    if version > SCHEMA_VERSION and not allow_future_schema:
        raise ManifestError(
            f"manifest schema_version {version} is newer than this tool supports "
            f"({SCHEMA_VERSION}); upgrade sjtu-course-exporter"
        )

    warnings: list[str] = []

    if not manifest.get("course", {}).get("title"):
        warnings.append("course.title is empty")
    if not manifest["sessions"]:
        warnings.append("manifest contains no sessions")

    seen_ids: set[str] = set()
    for si, session in enumerate(manifest["sessions"]):
        where = f"sessions[{si}]"
        if not isinstance(session, dict):
            raise ManifestError(f"{where} must be an object")

        sid = session.get("id")
        if not sid:
            raise ManifestError(f"{where} is missing 'id'")
        if sid in seen_ids:
            warnings.append(f"{where}: duplicate session id {sid!r}")
        seen_ids.add(sid)

        transcript = session.get("transcript") or []
        if not isinstance(transcript, list):
            raise ManifestError(f"{where}.transcript must be a list")

        last_seconds = -1
        for ti, entry in enumerate(transcript):
            if not isinstance(entry, dict):
                raise ManifestError(f"{where}.transcript[{ti}] must be an object")
            if "raw_text" not in entry:
                raise ManifestError(
                    f"{where}.transcript[{ti}] is missing 'raw_text' "
                    "(raw ASR must always be preserved)"
                )
            seconds = entry.get("seconds")
            if seconds is not None:
                if not isinstance(seconds, (int, float)):
                    raise ManifestError(
                        f"{where}.transcript[{ti}].seconds must be a number or null"
                    )
                if int(seconds) < last_seconds:
                    warnings.append(
                        f"{where}.transcript is not sorted by timestamp at index {ti}"
                    )
                    last_seconds = int(seconds)
                else:
                    last_seconds = int(seconds)
            if entry.get("normalized_text") is None and entry.get("raw_text"):
                warnings.append(
                    f"{where}.transcript[{ti}] has no normalized_text "
                    "(packaging will fall back to raw_text)"
                )

        summary = session.get("summary")
        if summary is not None:
            if not isinstance(summary, dict):
                raise ManifestError(f"{where}.summary must be an object or null")
            if not summary.get("generated_by_platform_ai"):
                warnings.append(
                    f"{where}.summary is not marked generated_by_platform_ai; "
                    "platform AI text could be mistaken for the teacher's words"
                )

        slides = session.get("slides") or []
        if not isinstance(slides, list):
            raise ManifestError(f"{where}.slides must be a list")
        for li, slide in enumerate(slides):
            if not isinstance(slide, dict):
                raise ManifestError(f"{where}.slides[{li}] must be an object")

        video = session.get("video")
        if video is not None:
            if not isinstance(video, dict):
                raise ManifestError(f"{where}.video must be an object or null")
            if video.get("download_implemented"):
                warnings.append(
                    f"{where}.video.download_implemented is set; video capture is "
                    "out of scope for this version"
                )

    assert_no_secrets(manifest)
    return warnings


def iter_sessions(manifest: dict):
    """Yield sessions in manifest order."""
    for session in manifest.get("sessions") or []:
        if isinstance(session, dict):
            yield session


def manifest_stats(manifest: dict) -> dict:
    """Summarise a manifest for display."""
    sessions = list(iter_sessions(manifest))
    transcripts = sum(len(s.get("transcript") or []) for s in sessions)
    slides = sum(len(s.get("slides") or []) for s in sessions)
    with_summary = sum(1 for s in sessions if s.get("summary"))
    return {
        "course_title": (manifest.get("course") or {}).get("title"),
        "sessions": len(sessions),
        "discovered_sessions": manifest.get("discovered_session_count", 0),
        "transcript_entries": transcripts,
        "slides": slides,
        "sessions_with_summary": with_summary,
        "exported_at": manifest.get("exported_at"),
        "schema_version": manifest.get("schema_version"),
    }
