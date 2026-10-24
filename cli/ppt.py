"""Align PowerPoint slides with the classroom transcript on a shared timeline.

What the platform gives us
--------------------------
The PPT strip is a list of *sampled slide frames*, each with the moment it
appeared on screen.  There is no end time: a slide is on screen until the next
one replaces it.  The same slide image can legitimately appear several times in
one lecture (the lecturer flips back and forth), and the sample page shows
exactly that -- several distinct images share one keyword set.

What this module produces
-------------------------
A chronological list of :class:`SlideSegment` objects, each covering
``[start, next_start)``, carrying the transcript lines spoken while that slide
was on screen.  Re-appearances are preserved rather than collapsed, and each
segment records which appearance it is.

The "end time = next slide's start" rule is an inference, not platform data, and
is labelled as such wherever it reaches the exported Markdown.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from adapters.textutil import format_time

__all__ = [
    "SlideSegment",
    "group_slides_by_image",
    "align_transcript_to_slides",
    "slide_at",
]


@dataclass
class SlideSegment:
    """One stretch of time during which a particular slide was displayed."""

    index: int
    slide_index: int | None
    url: str | None
    start_seconds: int
    end_seconds: int | None
    keywords: list[str] = field(default_factory=list)
    appearance_number: int = 1
    appearance_count: int = 1
    entries: list[dict] = field(default_factory=list)
    title: str | None = None

    @property
    def start_time(self) -> str:
        return format_time(self.start_seconds)

    @property
    def end_time(self) -> str:
        return format_time(self.end_seconds) if self.end_seconds is not None else ""

    @property
    def time_range(self) -> str:
        if self.end_seconds is None:
            return f"从 {self.start_time}"
        return f"{self.start_time} - {self.end_time}"

    @property
    def text(self) -> str:
        return " ".join(
            (e.get("normalized_text") or e.get("raw_text") or "").strip()
            for e in self.entries
            if (e.get("normalized_text") or e.get("raw_text"))
        ).strip()

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "slide_index": self.slide_index,
            "url": self.url,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "start_time": self.start_time,
            "end_time": self.end_time or None,
            "keywords": list(self.keywords),
            "appearance_number": self.appearance_number,
            "appearance_count": self.appearance_count,
            "title": self.title,
            "transcript_entries": len(self.entries),
            "end_time_source": "inferred_next_slide"
            if self.end_seconds is not None
            else "unknown",
        }


def group_slides_by_image(slides: list[dict]) -> list[dict]:
    """Collapse slides that share an image into one group with all appearances.

    Slides with no ``url`` cannot be deduplicated reliably, so each keeps its own
    group.  Order is chronological by first appearance.
    """
    groups: dict[str, dict] = {}
    order: list[str] = []

    for position, slide in enumerate(slides, start=1):
        url = slide.get("url")
        key = url if url else f"__nourl_{position}"
        seconds = slide.get("seconds")
        if key not in groups:
            groups[key] = {
                "url": url,
                "slide_index": slide.get("index", position),
                "keywords": list(slide.get("keywords") or []),
                "caption_label": slide.get("caption_label"),
                "appearances": [],
            }
            order.append(key)
        group = groups[key]
        # Keep the richest keyword set if duplicates disagree.
        if len(slide.get("keywords") or []) > len(group["keywords"]):
            group["keywords"] = list(slide["keywords"])
        if not group.get("caption_label") and slide.get("caption_label"):
            group["caption_label"] = slide["caption_label"]
        group["appearances"].append(
            {
                "seconds": seconds,
                "time": slide.get("time"),
                "slide_index": slide.get("index", position),
            }
        )

    result = []
    for key in order:
        group = groups[key]
        group["appearances"].sort(
            key=lambda a: (a["seconds"] is None, a["seconds"] or 0)
        )
        group["appearance_count"] = len(group["appearances"])
        group["first_seconds"] = group["appearances"][0]["seconds"]
        result.append(group)
    result.sort(key=lambda g: (g["first_seconds"] is None, g["first_seconds"] or 0))
    return result


def _entry_seconds(entry: dict) -> int | None:
    value = entry.get("seconds")
    return int(value) if isinstance(value, (int, float)) else None


def align_transcript_to_slides(
    slides: list[dict],
    transcript: list[dict],
    *,
    media_duration: int | None = None,
) -> list[SlideSegment]:
    """Build the slide/transcript timeline for one session.

    A leading segment with ``slide_index=None`` is emitted when the transcript
    starts before the first slide appears, so no spoken content is ever dropped.
    """
    timed_entries = [e for e in transcript if _entry_seconds(e) is not None]
    timed_slides = [s for s in slides if isinstance(s.get("seconds"), (int, float))]
    timed_slides.sort(key=lambda s: (s["seconds"], s.get("index", 0)))

    # Every (slide, appearance) pair becomes a boundary on the timeline.
    events: list[tuple[int, dict]] = [
        (int(s["seconds"]), s) for s in timed_slides
    ]
    events.sort(key=lambda pair: (pair[0], pair[1].get("index", 0)))

    segments: list[SlideSegment] = []

    # Group metadata for appearance numbering.
    groups = group_slides_by_image(timed_slides)
    appearance_of: dict[str, int] = {}
    count_of: dict[str, int] = {}
    for group in groups:
        key = group["url"] or f"__nourl_{group['slide_index']}"
        count_of[key] = group["appearance_count"]

    def key_for(slide: dict) -> str:
        url = slide.get("url")
        if url:
            return url
        return f"__nourl_{slide.get('index')}"

    cursor = 0
    for position, (start, slide) in enumerate(events):
        # A slide is on screen until the next one replaces it. For the final
        # slide there is no successor, so the window is open-ended: every
        # remaining line belongs to it. `end_seconds` is then only a *display*
        # value (media duration, or a small tail past the last line).
        is_last = position + 1 >= len(events)
        if is_last:
            hard_end = None
            last_seconds = _entry_seconds(timed_entries[-1]) if timed_entries else None
            if media_duration and media_duration > start:
                display_end = media_duration
            elif last_seconds is not None and last_seconds > start:
                display_end = last_seconds + 5
            else:
                display_end = start + 5
        else:
            hard_end = events[position + 1][0]
            display_end = hard_end

        spoken: list[dict] = []
        while cursor < len(timed_entries) and _entry_seconds(timed_entries[cursor]) < start:
            cursor += 1
        probe = cursor
        while probe < len(timed_entries):
            secs = _entry_seconds(timed_entries[probe])
            if secs is None or (hard_end is not None and secs >= hard_end):
                break
            spoken.append(timed_entries[probe])
            probe += 1
        cursor = probe

        key = key_for(slide)
        appearance_of[key] = appearance_of.get(key, 0) + 1
        segments.append(
            SlideSegment(
                index=position + 1,
                slide_index=slide.get("index"),
                url=slide.get("url"),
                start_seconds=start,
                end_seconds=display_end,
                keywords=list(slide.get("keywords") or []),
                appearance_number=appearance_of[key],
                appearance_count=count_of.get(key, 1),
                entries=spoken,
                title=slide.get("title"),
            )
        )

    # Anything spoken before the first slide keeps its own leading segment.
    if segments:
        first_start = segments[0].start_seconds
        leading = [e for e in timed_entries if (_entry_seconds(e) or 0) < first_start]
        if leading:
            segments.insert(
                0,
                SlideSegment(
                    index=0,
                    slide_index=None,
                    url=None,
                    start_seconds=_entry_seconds(leading[0]) or 0,
                    end_seconds=first_start,
                    entries=leading,
                ),
            )
    elif timed_entries:
        # No slides at all: emit a single slide-less segment so the transcript
        # still flows through the same rendering path.
        segments.append(
            SlideSegment(
                index=0,
                slide_index=None,
                url=None,
                start_seconds=_entry_seconds(timed_entries[0]) or 0,
                end_seconds=media_duration
                or (_entry_seconds(timed_entries[-1]) or 0) + 5,
                entries=timed_entries,
            )
        )

    for i, segment in enumerate(segments, start=1):
        segment.index = i
    return segments


def slide_at(segments: list[SlideSegment], seconds: int | float | None) -> SlideSegment | None:
    """Return the segment covering *seconds*, if any."""
    if seconds is None:
        return None
    for segment in segments:
        end = segment.end_seconds
        if segment.start_seconds <= seconds and (end is None or seconds < end):
            return segment
    return None
