"""Tests for PPT / transcript timeline alignment."""

from __future__ import annotations

from cli.ppt import align_transcript_to_slides, group_slides_by_image, slide_at


def make_slides(*pairs):
    return [
        {"index": i, "seconds": sec, "time": f"t{sec}", "url": f"u{idx}", "keywords": [f"k{idx}"]}
        for i, (sec, idx) in enumerate(pairs, start=1)
    ]


def make_transcript(*seconds):
    return [
        {"seconds": s, "time": f"t{s}", "raw_text": f"text{s}", "normalized_text": f"text{s}"}
        for s in seconds
    ]


class TestGroupSlidesByImage:
    def test_groups_repeated_images(self):
        slides = make_slides((10, "a"), (30, "b"), (50, "a"))
        groups = group_slides_by_image(slides)
        assert len(groups) == 2
        repeated = [g for g in groups if g["appearance_count"] == 2]
        assert len(repeated) == 1
        assert [a["seconds"] for a in repeated[0]["appearances"]] == [10, 50]

    def test_slides_without_url_are_not_merged(self):
        slides = [
            {"index": 1, "seconds": 10, "url": None},
            {"index": 2, "seconds": 20, "url": None},
        ]
        assert len(group_slides_by_image(slides)) == 2

    def test_keeps_richest_keyword_set(self):
        slides = [
            {"index": 1, "seconds": 10, "url": "u", "keywords": ["a"]},
            {"index": 2, "seconds": 20, "url": "u", "keywords": ["a", "b", "c"]},
        ]
        groups = group_slides_by_image(slides)
        assert groups[0]["keywords"] == ["a", "b", "c"]

    def test_empty_input(self):
        assert group_slides_by_image([]) == []


class TestAlignTranscriptToSlides:
    def test_basic_segmentation(self):
        slides = make_slides((10, "a"), (30, "b"))
        transcript = make_transcript(5, 12, 20, 35)
        segments = align_transcript_to_slides(slides, transcript)

        # Leading segment (before the first slide) + one per slide.
        assert len(segments) == 3
        assert segments[0].slide_index is None
        assert [e["seconds"] for e in segments[0].entries] == [5]

        assert segments[1].slide_index == 1
        assert segments[1].start_seconds == 10
        assert segments[1].end_seconds == 30
        assert [e["seconds"] for e in segments[1].entries] == [12, 20]

        assert segments[2].slide_index == 2
        assert [e["seconds"] for e in segments[2].entries] == [35]

    def test_end_time_is_next_slide_start(self):
        slides = make_slides((0, "a"), (60, "b"))
        segments = align_transcript_to_slides(slides, make_transcript(10, 70))
        assert segments[0].end_seconds == 60
        assert segments[0].end_seconds == segments[1].start_seconds

    def test_end_time_is_labelled_as_inferred(self):
        slides = make_slides((0, "a"), (60, "b"))
        segments = align_transcript_to_slides(slides, make_transcript(10))
        assert segments[0].to_dict()["end_time_source"] == "inferred_next_slide"

    def test_no_transcript_is_dropped(self):
        slides = make_slides((10, "a"))
        transcript = make_transcript(1, 2, 11, 12, 99)
        segments = align_transcript_to_slides(slides, transcript)
        seen = [e["seconds"] for s in segments for e in s.entries]
        assert sorted(seen) == [1, 2, 11, 12, 99]

    def test_repeated_slide_appearances_are_numbered(self):
        slides = make_slides((10, "a"), (30, "b"), (50, "a"))
        segments = align_transcript_to_slides(slides, make_transcript(15, 35, 55))
        by_slide = {}
        for segment in segments:
            if segment.slide_index is not None:
                by_slide.setdefault(segment.slide_index, []).append(segment)
        assert by_slide[1][0].appearance_number == 1
        assert by_slide[1][0].appearance_count == 2
        assert by_slide[3][0].appearance_number == 2
        assert by_slide[3][0].appearance_count == 2

    def test_media_duration_takes_priority_for_last_slide(self):
        slides = make_slides((10, "a"), (30, "b"))
        segments = align_transcript_to_slides(
            slides, make_transcript(15, 35), media_duration=100
        )
        assert segments[-1].end_seconds == 100

    def test_no_slides_still_yields_one_segment(self):
        segments = align_transcript_to_slides([], make_transcript(5, 10))
        assert len(segments) == 1
        assert segments[0].slide_index is None
        assert len(segments[0].entries) == 2

    def test_no_transcript_and_no_slides(self):
        assert align_transcript_to_slides([], []) == []

    def test_untimed_slides_are_ignored(self):
        slides = [
            {"index": 1, "seconds": None, "url": "u"},
            {"index": 2, "seconds": 10, "url": "v"},
        ]
        segments = align_transcript_to_slides(slides, make_transcript(15))
        assert all(s.slide_index != 1 for s in segments)

    def test_segments_are_renumbered_sequentially(self):
        slides = make_slides((10, "a"), (30, "b"))
        segments = align_transcript_to_slides(slides, make_transcript(1, 15, 35))
        assert [s.index for s in segments] == list(range(1, len(segments) + 1))

    def test_segment_text_joins_normalized_entries(self):
        slides = make_slides((0, "a"))
        transcript = [
            {"seconds": 1, "raw_text": "raw1", "normalized_text": "n1"},
            {"seconds": 2, "raw_text": "raw2", "normalized_text": "n2"},
        ]
        segments = align_transcript_to_slides(slides, transcript)
        assert segments[0].text == "n1 n2"

    def test_segment_text_falls_back_to_raw(self):
        slides = make_slides((0, "a"))
        transcript = [{"seconds": 1, "raw_text": "rawonly", "normalized_text": ""}]
        segments = align_transcript_to_slides(slides, transcript)
        assert segments[0].text == "rawonly"

    def test_time_range_helpers(self):
        slides = make_slides((65, "a"), (130, "b"))
        segments = align_transcript_to_slides(slides, make_transcript(70))
        assert segments[0].start_time == "01:05"
        assert segments[0].end_time == "02:10"
        assert segments[0].time_range == "01:05 - 02:10"


class TestSlideAt:
    def test_finds_covering_segment(self):
        slides = make_slides((10, "a"), (30, "b"))
        segments = align_transcript_to_slides(slides, make_transcript(15, 35))
        assert slide_at(segments, 15).slide_index == 1
        assert slide_at(segments, 35).slide_index == 2

    def test_boundary_belongs_to_the_later_segment(self):
        slides = make_slides((10, "a"), (30, "b"))
        segments = align_transcript_to_slides(slides, make_transcript(15, 35))
        found = slide_at(segments, 30)
        assert found is not None and found.slide_index == 2

    def test_none_and_out_of_range(self):
        slides = make_slides((10, "a"))
        segments = align_transcript_to_slides(slides, make_transcript(15))
        assert slide_at(segments, None) is None
        assert slide_at(segments, 1) is None
        assert slide_at([], 5) is None
