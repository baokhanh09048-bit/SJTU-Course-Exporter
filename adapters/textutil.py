"""Pure, dependency-free text primitives shared by the adapter and the CLI.

These live in ``adapters`` (not ``cli``) so the layering stays one-directional:
``cli`` may import ``adapters``, never the other way round.

Design rules that come straight from the project brief:

* ``raw_text`` is sacred.  It is what the platform's ASR produced and it is
  never overwritten.
* Normalisation is *mechanical only*: whitespace, duplicated punctuation, HTML
  entities, line breaks and Unicode NFC.  We never "fix" a word.  Turning
  "全称" into "全胜" is a semantic judgement and belongs to a downstream model,
  not to the collector.
"""

from __future__ import annotations

import html
import re
import unicodedata

__all__ = [
    "parse_time",
    "format_time",
    "format_srt_time",
    "normalize_text",
    "TranscriptError",
]


class TranscriptError(ValueError):
    """Raised for malformed timestamps."""


_TIME_RE = re.compile(
    r"^\s*(?:(\d+)\s*:\s*)?(\d{1,2})\s*:\s*(\d{1,2})(?:[.,](\d{1,3}))?\s*$"
)


def parse_time(value: str | int | float | None) -> int | None:
    """Convert a platform timestamp to whole seconds.

    Accepts ``"MM:SS"``, ``"HH:MM:SS"``, ``"MM:SS.mmm"`` and bare values that
    are already seconds.  Returns ``None`` when the value cannot be understood;
    callers decide whether that is fatal, because a single unparseable
    timestamp must not abort a whole-course export.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool is an int subclass; reject it explicitly
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)

    match = _TIME_RE.match(text)
    if not match:
        return None

    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def format_time(seconds: int | float | None, *, always_hours: bool = False) -> str:
    """Render seconds as ``MM:SS`` (or ``HH:MM:SS`` when needed)."""
    if seconds is None:
        return ""
    total = int(seconds)
    if total < 0:
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours or always_hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_srt_time(seconds: int | float | None) -> str:
    """Render seconds as an SRT timestamp ``HH:MM:SS,mmm``."""
    if seconds is None:
        seconds = 0
    total_ms = int(round(float(seconds) * 1000))
    if total_ms < 0:
        total_ms = 0
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


_WS_RE = re.compile(r"\s+")
_REPEAT_PUNCT_RE = re.compile(r"([，。！？、；：,.!?;:])\1{1,}")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([，。！？、；：）】》」』,.!?;:)])")
_SPACE_AFTER_OPEN_RE = re.compile(r"([（【《「『(])\s+")
# Chinese typography has no space after a full-width punctuation mark.
_SPACE_AFTER_CJK_PUNCT_RE = re.compile(r"([，。！？、；：）】》」』])\s+")


def normalize_text(raw: str | None) -> str:
    """Mechanically tidy a transcript line WITHOUT changing any wording.

    Only these transformations are applied:

    1. HTML entity decoding, because a saved page may still contain entities the
       browser did not resolve.
    2. Unicode NFC composition, so identical-looking text compares equal.
    3. Runs of whitespace (including newlines) collapse to a single space.
    4. Duplicated punctuation collapses (``。。。`` -> ``。``).
    5. Spaces directly before closing punctuation, or directly after opening
       punctuation, are removed -- an artefact of ASR segmentation.
    6. Spaces directly after full-width CJK punctuation are removed, matching
       Chinese typography.

    Deliberately NOT done: homophone correction, word substitution, grammar
    repair, or inserting any content the teacher did not say.
    """
    if not raw:
        return ""
    text = html.unescape(raw)
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u3000", " ")  # ideographic space
    text = _WS_RE.sub(" ", text)
    text = _REPEAT_PUNCT_RE.sub(r"\1", text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = _SPACE_AFTER_OPEN_RE.sub(r"\1", text)
    text = _SPACE_AFTER_CJK_PUNCT_RE.sub(r"\1", text)
    return text.strip()
