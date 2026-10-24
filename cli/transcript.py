"""Transcript export helpers: SRT rendering and transcript-shaped utilities.

The low-level primitives (timestamp parsing, conservative normalisation) live in
:mod:`adapters.textutil` and are re-exported here for convenience, so callers can
keep using ``cli.transcript``.
"""

from __future__ import annotations

from adapters.textutil import (  # noqa: F401  (re-exported)
    TranscriptError,
    format_srt_time,
    format_time,
    normalize_text,
    parse_time,
)

__all__ = [
    "parse_time",
    "format_time",
    "format_srt_time",
    "normalize_text",
    "build_srt",
    "entry_bounds",
    "TranscriptError",
]


def entry_bounds(
    index: int,
    entries: list[dict],
    duration: int | None = None,
) -> tuple[int, int]:
    """Work out ``(start, end)`` seconds for one transcript entry.

    The platform only publishes a *start* time for each ASR line.  The end time
    is therefore the next line's start; the final line gets a short default tail,
    or the media duration when the caller knows it.

    This is an inference, and it is labelled as such wherever it surfaces in the
    exported Markdown.
    """
    start = entries[index].get("seconds")
    if start is None:
        start = 0
    if index + 1 < len(entries):
        nxt = entries[index + 1].get("seconds")
        end = nxt if isinstance(nxt, int) and nxt > start else start + 3
    else:
        end = duration if duration and duration > start else start + 5
    return int(start), int(end)


def build_srt(
    entries: list[dict],
    *,
    duration: int | None = None,
    use_normalized: bool = True,
) -> str:
    """Render transcript *entries* as SRT subtitle text.

    Entries without a usable timestamp are skipped rather than emitted at
    ``00:00:00,000``, which would silently corrupt the subtitle track.
    """
    blocks: list[str] = []
    counter = 0
    for index, entry in enumerate(entries):
        text = entry.get("normalized_text") if use_normalized else None
        if not text:
            text = entry.get("raw_text") or ""
        text = (text or "").strip()
        if not text:
            continue
        if entry.get("seconds") is None:
            continue
        start, end = entry_bounds(index, entries, duration)
        if end <= start:
            end = start + 1
        counter += 1
        blocks.append(
            f"{counter}\n"
            f"{format_srt_time(start)} --> {format_srt_time(end)}\n"
            f"{text}\n"
        )
    return "\n".join(blocks)
