"""SJTU classroom-replay adapter.

The adapter is the only place that knows how to turn a platform page into the
project's data model.  It has two back ends:

``DOM adapter`` (implemented here)
    Parses a saved page or live ``document.documentElement.outerHTML``.  This is
    the path that is verified end-to-end against the real sample.

``API adapter`` (documented, not enabled by default)
    The platform exposes cleaner JSON endpoints (see
    :mod:`adapters.selectors`), but their exact request contracts have not been
    confirmed against live traffic yet.  Rather than invent parameters, this
    module exposes :meth:`SJTUReplayAdapter.api_candidates` so the userscript can
    try them and fall back to the DOM when a response does not look right.

Consistent with the brief, this module never authenticates, never touches
cookies, and never synthesises data it did not read.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import dom
from .selectors import API, DOM, PLATFORM_NAME, SCHEMA_VERSION, VIDEO_OUT_OF_SCOPE_NOTE
from .textutil import format_time, normalize_text, parse_time

__all__ = [
    "SJTUReplayAdapter",
    "SessionRef",
    "SessionExport",
    "sanitize_url",
    "parse_session_title",
    "parse_session_meta",
]

EXPORTER_NAME = "sjtu-course-exporter"
EXPORTER_VERSION = "0.1.0"

# ---------------------------------------------------------------- parsing ---

_TITLE_RE = re.compile(
    r"第\s*(?P<week>\d+)\s*周\s*[-–—]?\s*周?(?P<weekday>[一二三四五六日天])"
    r"\s*第\s*(?P<section>\d+)\s*节"
)
_TITLE_LOOSE_RE = re.compile(r"第\s*(?P<week>\d+)\s*周")
_SECTION_LOOSE_RE = re.compile(r"第\s*(?P<section>\d+)\s*节")
_META_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})"
    r"(?:\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?))?"
    r"(?:\s+(?P<room>\S.*?))?\s*$"
)

# Anything that looks like a bearer token / signature we must never persist.
_SENSITIVE_QUERY_KEYS = {
    "key", "token", "access_token", "jwt", "jwt-token", "signture", "signature",
    "sig", "auth", "authorization", "session", "sessionid", "ticket", "secret",
}


def sanitize_url(url: str | None) -> str | None:
    """Strip credentials and signed query parameters from a URL.

    Platform media URLs carry a time-limited ``?key=...`` signature.  Persisting
    it would leak a working credential into an exported corpus, so the path is
    kept and every sensitive query parameter is replaced with ``REDACTED``.
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if "?" not in url:
        return url
    base, _, query = url.partition("?")
    kept: list[str] = []
    redacted = False
    for part in query.split("&"):
        if not part:
            continue
        name, sep, _value = part.partition("=")
        if name.lower() in _SENSITIVE_QUERY_KEYS:
            kept.append(f"{name}{sep}REDACTED")
            redacted = True
        else:
            kept.append(part)
    if not kept:
        return base
    suffix = "&".join(kept)
    if redacted:
        suffix += "&redacted=1"
    return f"{base}?{suffix}"


def parse_session_title(title: str | None) -> dict:
    """Split ``"第1周 - 周三 第11节"`` into structured fields."""
    result = {"week": None, "weekday": None, "section": None, "section_number": None}
    if not title:
        return result
    match = _TITLE_RE.search(title)
    if match:
        result["week"] = int(match.group("week"))
        result["weekday"] = f"周{match.group('weekday')}"
        result["section_number"] = int(match.group("section"))
        result["section"] = f"第{match.group('section')}节"
        return result
    # Looser fallbacks so a layout tweak degrades instead of crashing.
    week = _TITLE_LOOSE_RE.search(title)
    if week:
        result["week"] = int(week.group("week"))
    section = _SECTION_LOOSE_RE.search(title)
    if section:
        result["section_number"] = int(section.group("section"))
        result["section"] = f"第{section.group('section')}节"
    return result


def parse_session_meta(meta: str | None) -> dict:
    """Split ``"2026-09-16 17:55 示例教室"`` into date / time / room."""
    result = {"date": None, "start_time": None, "room": None}
    if not meta:
        return result
    match = _META_RE.search(meta.strip())
    if not match:
        return result
    result["date"] = match.group("date")
    result["start_time"] = match.group("time")
    room = match.group("room")
    result["room"] = room.strip() if room else None
    return result


def _first_text(node: dom.Element | None) -> str | None:
    if node is None:
        return None
    text = node.text.strip()
    if text:
        return text
    title = node.get("title")
    return title.strip() if title else None


def _deep_get(node: dom.Element | None, selectors: list[str]) -> dom.Element | None:
    if node is None:
        return None
    return dom.find_first(node, selectors)


def _deep_all(node: dom.Element | None, selectors: list[str]) -> list[dom.Element]:
    if node is None:
        return []
    return dom.find_all(node, selectors)


# ------------------------------------------------------------- data model ---


@dataclass
class SessionRef:
    """A discoverable class session (课次) and where it sits in the DOM."""

    index: int
    title: str | None = None
    date: str | None = None
    start_time: str | None = None
    room: str | None = None
    week: int | None = None
    weekday: str | None = None
    section: str | None = None
    section_number: int | None = None
    has_ai: bool = False
    is_current: bool = False
    platform_id: str | None = None

    @property
    def id(self) -> str:
        """Deterministic, human-readable session identifier.

        The saved page does not expose a stable platform session id (only an
        ordinal such as ``list-item-1``), so we synthesise one from the fields
        that *are* stable.  It is marked ``synthetic`` in the manifest.
        """
        parts = [self.date or "undated"]
        if self.section:
            parts.append(self.section)
        elif self.section_number is not None:
            parts.append(f"第{self.section_number}节")
        if self.room:
            parts.append(self.room)
        digest = hashlib.sha1(
            "|".join(
                str(p) for p in (self.date, self.start_time, self.title, self.index)
            ).encode("utf-8")
        ).hexdigest()[:8]
        return "-".join(parts) + f"-{digest}"

    def to_manifest(self) -> dict:
        return {
            "id": self.id,
            "id_source": "synthetic",
            "platform_id": self.platform_id,
            "order": self.index,
            "title": self.title,
            "date": self.date,
            "start_time": self.start_time,
            "week": self.week,
            "weekday": self.weekday,
            "section": self.section,
            "section_number": self.section_number,
            "room": self.room,
            "has_platform_ai": self.has_ai,
            "is_current_in_snapshot": self.is_current,
        }


@dataclass
class SessionExport:
    """Everything extracted for one session from one page snapshot."""

    ref: SessionRef
    transcript: list[dict] = field(default_factory=list)
    summary: dict | None = None
    slides: list[dict] = field(default_factory=list)
    video: dict | None = None
    notices: list[str] = field(default_factory=list)

    def to_manifest(self) -> dict:
        payload = self.ref.to_manifest()
        payload.update(
            {
                "transcript": self.transcript,
                "summary": self.summary,
                "slides": self.slides,
                "video": self.video,
                "notices": self.notices,
            }
        )
        return payload


# ---------------------------------------------------------------- adapter ---


class SJTUReplayAdapter:
    """Read a SJTU replay page into the project's data model."""

    def __init__(self, root: dom.Element, source_name: str | None = None) -> None:
        self.root = root
        self.source_name = source_name
        self.warnings: list[str] = []

    # -- construction ------------------------------------------------------

    @classmethod
    def from_html(cls, html: str, source_name: str | None = None) -> "SJTUReplayAdapter":
        return cls(dom.parse(html), source_name=source_name)

    @classmethod
    def from_file(cls, path: str) -> "SJTUReplayAdapter":
        return cls(dom.parse_file(path), source_name=str(path))

    # -- course level ------------------------------------------------------

    def get_course_title(self) -> str | None:
        node = _deep_get(self.root, DOM["course_title"])
        return _first_text(node)

    def get_course_teacher(self) -> str | None:
        node = _deep_get(self.root, DOM["course_teacher"])
        return _first_text(node)

    def get_course_time_range(self) -> str | None:
        node = _deep_get(self.root, DOM["course_time_range"])
        return _first_text(node)

    # -- sessions ----------------------------------------------------------

    def discover_sessions(self) -> list[SessionRef]:
        """Find every session (课次) the page exposes, in display order."""
        items = _deep_all(self.root, DOM["session_item"])
        refs: list[SessionRef] = []
        for position, item in enumerate(items, start=1):
            classes = item.classes
            title_el = _deep_get(item, DOM["session_item_title"])
            meta_el = _deep_get(item, DOM["session_item_meta"])
            index_el = _deep_get(item, DOM["session_item_index"])

            title = _first_text(title_el)
            meta = _first_text(meta_el)
            index_text = _first_text(index_el)

            ordinal = position
            if index_text and index_text.isdigit():
                ordinal = int(index_text)

            ref = SessionRef(index=ordinal, title=title)
            ref.date = parse_session_meta(meta)["date"]
            ref.start_time = parse_session_meta(meta)["start_time"]
            ref.room = parse_session_meta(meta)["room"]
            parsed = parse_session_title(title)
            ref.week = parsed["week"]
            ref.weekday = parsed["weekday"]
            ref.section = parsed["section"]
            ref.section_number = parsed["section_number"]
            ref.has_ai = bool(_deep_all(item, DOM["session_item_ai_flag"]))
            ref.is_current = "selected" in classes or "active" in classes
            refs.append(ref)
        return refs

    def current_session(self, refs: list[SessionRef] | None = None) -> SessionRef | None:
        refs = refs if refs is not None else self.discover_sessions()
        for ref in refs:
            if ref.is_current:
                return ref
        return refs[0] if refs else None

    # -- transcript --------------------------------------------------------

    def get_transcript(self, ref: SessionRef | None = None) -> list[dict]:
        """Extract ASR lines with timestamps.  ``raw_text`` is never rewritten."""
        wrapper = _deep_get(self.root, DOM["transcript_wrapper"])
        scope = _deep_get(wrapper, DOM["transcript_container"]) or wrapper or self.root
        cards = _deep_all(scope, DOM["transcript_card"])
        if not cards:
            cards = _deep_all(self.root, DOM["transcript_card"])

        entries: list[dict] = []
        for position, card in enumerate(cards):
            time_el = _deep_get(card, DOM["transcript_time"])
            text_el = _deep_get(card, DOM["transcript_text"])
            raw = text_el.text if text_el is not None else ""
            raw = raw or ""
            time_text = _first_text(time_el)

            card_id = card.get("id") or ""
            ordinal = position
            if card_id.startswith("card") and card_id[4:].isdigit():
                ordinal = int(card_id[4:])

            entries.append(
                {
                    "index": ordinal,
                    "time": time_text,
                    "seconds": parse_time(time_text),
                    "raw_text": raw,
                    "normalized_text": normalize_text(raw),
                    "source": "platform_asr",
                }
            )

        # Guard against a reordered DOM: timestamps define the real order.
        if any(e["seconds"] is not None for e in entries):
            entries.sort(
                key=lambda e: (e["seconds"] is None, e["seconds"] or 0, e["index"])
            )
        return entries

    def get_transcript_notice(self) -> str | None:
        node = _deep_get(self.root, DOM["transcript_tip"])
        return _first_text(node)

    # -- summary -----------------------------------------------------------

    def get_summary(self) -> dict | None:
        """Extract the platform's AI summary (概要): keywords, overview, chapters."""
        wrapper = _deep_get(self.root, DOM["summary_wrapper"])
        if wrapper is None:
            return None

        keywords: list[str] = []
        for node in _deep_all(wrapper, DOM["summary_keyword"]):
            value = _first_text(node)
            if value and value not in keywords:
                keywords.append(value)

        overview_el = _deep_get(wrapper, DOM["summary_overview"])
        overview = _first_text(overview_el)

        chapters: list[dict] = []
        for item in _deep_all(wrapper, DOM["summary_chapter_item"]):
            time_el = _deep_get(item, DOM["summary_chapter_time"])
            title_el = _deep_get(item, DOM["summary_chapter_title"])
            content_el = _deep_get(item, DOM["summary_chapter_content"])
            time_text = _first_text(time_el)
            chapters.append(
                {
                    "index": len(chapters) + 1,
                    "time": time_text,
                    "seconds": parse_time(time_text),
                    "title": _first_text(title_el),
                    "summary": _first_text(content_el),
                }
            )

        if not keywords and overview is None and not chapters:
            return None

        # The platform only publishes a chapter start time; the end is the next
        # chapter's start.  Make that inference explicit and labelled.
        for i, chapter in enumerate(chapters):
            if i + 1 < len(chapters):
                chapter["end_seconds"] = chapters[i + 1]["seconds"]
            else:
                chapter["end_seconds"] = None
            if chapter.get("seconds") is not None and chapter.get("end_seconds"):
                chapter["time_range"] = (
                    f"{format_time(chapter['seconds'])}-"
                    f"{format_time(chapter['end_seconds'])}"
                )
            else:
                chapter["time_range"] = None
        if chapters:
            chapters[-1]["end_boundary"] = "end_of_session"

        return {
            "generated_by_platform_ai": True,
            "disclaimer": "SJTU 平台 AI 自动生成内容，非教师原话。Platform AI generated; not the teacher's words.",
            "keywords": keywords,
            "overview": overview,
            "chapters": chapters,
        }

    # -- slides ------------------------------------------------------------

    def get_slides(self) -> list[dict]:
        """Extract the PPT strip: image source, appearance time, keywords."""
        wrapper = _deep_get(self.root, DOM["ppt_wrapper"]) or self.root
        container = _deep_get(wrapper, DOM["ppt_container"]) or wrapper
        cards = _deep_all(container, DOM["ppt_card"])
        if not cards:
            cards = _deep_all(self.root, DOM["ppt_card"])

        slides: list[dict] = []
        for position, card in enumerate(cards, start=1):
            image_el = _deep_get(card, DOM["ppt_image"])
            time_el = _deep_get(card, DOM["ppt_time"])
            label_el = _deep_get(card, DOM["ppt_caption_label"])

            card_id = card.get("id") or ""
            ordinal = position
            match = re.search(r"(\d+)$", card_id)
            if match:
                ordinal = int(match.group(1)) + 1  # ids are 0-based

            time_text = _first_text(time_el)
            keywords: list[str] = []
            for kw in _deep_all(label_el, DOM["ppt_caption_keyword"]):
                value = _first_text(kw)
                if value and value not in keywords:
                    keywords.append(value)

            classes = card.classes
            slides.append(
                {
                    "index": ordinal,
                    "time": time_text,
                    "seconds": parse_time(time_text),
                    "url": sanitize_url(image_el.get("src")) if image_el is not None else None,
                    "local_path": None,
                    "title": None,
                    "keywords": keywords,
                    "caption_label": _first_text(label_el),
                    "is_current": "active" in classes,
                    "source": "page_snapshot",
                }
            )
        return slides

    # -- media -------------------------------------------------------------

    def get_video(self) -> dict | None:
        """Record that a video exists without persisting its signature."""
        node = _deep_get(self.root, DOM["video_element"])
        if node is None:
            return None
        src = node.get("src")
        return {
            "download_implemented": False,
            "note": VIDEO_OUT_OF_SCOPE_NOTE,
            "url": sanitize_url(src),
            "url_had_signature": bool(src and "?" in src),
            "poster": sanitize_url(node.get("poster")),
        }

    # -- API candidates ----------------------------------------------------

    @staticmethod
    def api_candidates() -> dict[str, dict]:
        """Return the unverified-but-real endpoint catalogue.

        Exposed so the browser side can probe these in a logged-in tab and
        confirm the contract before any production code depends on it.
        """
        return {name: dict(spec) for name, spec in API.items()}

    # -- assembly ----------------------------------------------------------

    def build_session_export(self, ref: SessionRef | None = None) -> SessionExport:
        refs = self.discover_sessions()
        target = ref or self.current_session(refs)
        if target is None:
            target = SessionRef(index=1, title="unknown session")
            self.warnings.append(
                "No session list found in this page; produced a single unnamed session."
            )
        return SessionExport(
            ref=target,
            transcript=self.get_transcript(target),
            summary=self.get_summary(),
            slides=self.get_slides(),
            video=self.get_video(),
            notices=[n for n in [self.get_transcript_notice()] if n],
        )

    def build_manifest(self, exported_at: str | None = None) -> dict:
        """Produce a complete v1 manifest for the page currently loaded."""
        refs = self.discover_sessions()
        export = self.build_session_export(self.current_session(refs))

        documents = [
            {
                "kind": "transcript",
                "entries": len(export.transcript),
                "first_time": export.transcript[0]["time"] if export.transcript else None,
                "last_time": export.transcript[-1]["time"] if export.transcript else None,
            },
            {"kind": "summary", "present": export.summary is not None},
            {"kind": "slides", "entries": len(export.slides)},
        ]

        return {
            "schema_version": SCHEMA_VERSION,
            "platform": PLATFORM_NAME,
            "exporter": {"name": EXPORTER_NAME, "version": EXPORTER_VERSION},
            "exported_at": exported_at
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": {
                "kind": "saved_html" if self.source_name else "live_dom",
                "name": self.source_name,
                "capture_scope": "single_page_snapshot",
            },
            "course": {
                "id": None,
                "title": self.get_course_title(),
                "teacher": self.get_course_teacher(),
            },
            "discovered_sessions": [r.to_manifest() for r in refs],
            "discovered_session_count": len(refs),
            "documents": documents,
            "warnings": list(self.warnings),
            "sessions": [export.to_manifest()],
            "extraction": {
                "method": "dom",
                "notes": (
                    "Only the session that was displayed when the page was saved is "
                    "present in a saved HTML snapshot. Use the userscript's "
                    "'export whole course' action, which clicks through each session, "
                    "to populate the remaining entries."
                ),
            },
        }

    def build_course_manifest(
        self, exports: list[SessionExport], exported_at: str | None = None
    ) -> dict:
        """Assemble a full-course manifest from per-session exports."""
        refs = self.discover_sessions()
        return {
            "schema_version": SCHEMA_VERSION,
            "platform": PLATFORM_NAME,
            "exporter": {"name": EXPORTER_NAME, "version": EXPORTER_VERSION},
            "exported_at": exported_at
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": {
                "kind": "live_dom",
                "name": self.source_name,
                "capture_scope": "whole_course",
            },
            "course": {
                "id": None,
                "title": self.get_course_title(),
                "teacher": self.get_course_teacher(),
            },
            "discovered_sessions": [r.to_manifest() for r in refs],
            "discovered_session_count": len(refs),
            "sessions": [e.to_manifest() for e in exports],
            "extraction": {"method": "dom", "notes": "Whole-course sweep."},
        }
