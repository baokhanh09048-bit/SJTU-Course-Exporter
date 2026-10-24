"""Tests for timestamp handling, conservative normalisation and SRT output."""

from __future__ import annotations

import pytest

from adapters.textutil import format_srt_time, format_time, normalize_text, parse_time
from cli.transcript import build_srt, entry_bounds


class TestParseTime:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("00:05", 5),
            ("01:31", 91),
            ("53:51", 3231),
            ("59:59", 3599),
            ("1:00:00", 3600),
            ("01:02:03", 3723),
            ("10:00:00", 36000),
            ("00:05.500", 5),
            ("00:05,500", 5),
            ("  00:05  ", 5),
            ("90", 90),
            (90, 90),
            (90.7, 90),
        ],
    )
    def test_parses_supported_formats(self, value, expected):
        assert parse_time(value) == expected

    @pytest.mark.parametrize(
        "value",
        [None, "", "   ", "abc", "1:2:3:4", "::", "not-a-time", True, False, [], {}],
    )
    def test_returns_none_for_unusable_input(self, value):
        assert parse_time(value) is None

    def test_mm_ss_is_not_misread_as_hh_mm(self):
        # "53:51" is 53 minutes 51 seconds, not 53 hours.
        assert parse_time("53:51") == 53 * 60 + 51


class TestFormatTime:
    @pytest.mark.parametrize(
        "seconds,expected",
        [(0, "00:00"), (5, "00:05"), (91, "01:31"), (3599, "59:59"), (3600, "01:00:00"), (3723, "01:02:03")],
    )
    def test_formats(self, seconds, expected):
        assert format_time(seconds) == expected

    def test_none_and_negative(self):
        assert format_time(None) == ""
        assert format_time(-5) == "00:00"

    def test_always_hours(self):
        assert format_time(5, always_hours=True) == "00:00:05"


class TestFormatSrtTime:
    def test_zero(self):
        assert format_srt_time(0) == "00:00:00,000"

    def test_seconds_and_millis(self):
        assert format_srt_time(5) == "00:00:05,000"
        assert format_srt_time(3723.5) == "01:02:03,500"

    def test_none_is_zero(self):
        assert format_srt_time(None) == "00:00:00,000"


class TestNormalizeText:
    def test_collapses_whitespace_and_newlines(self):
        assert normalize_text("a   b\n\tc") == "a b c"

    def test_decodes_html_entities(self):
        assert normalize_text("a &amp; b &lt;c&gt;") == "a & b <c>"

    def test_collapses_duplicated_punctuation(self):
        assert normalize_text("你好，，世界。。") == "你好，世界。"

    def test_removes_space_before_closing_punctuation(self):
        assert normalize_text("你好 ， 世界") == "你好，世界"

    def test_removes_space_after_opening_bracket(self):
        assert normalize_text("（ 示例 ）") == "（示例）"

    def test_ideographic_space_is_treated_as_space(self):
        assert normalize_text("a\u3000b") == "a b"

    def test_does_not_perform_semantic_correction(self):
        # The whole point: we must NOT "fix" 全称 into 全胜.
        raw = "全称思想是孙子兵法的核心"
        assert normalize_text(raw) == raw

    def test_does_not_drop_or_add_words(self):
        raw = "老师说这个字是错别字"
        assert normalize_text(raw) == raw

    def test_empty_inputs(self):
        assert normalize_text(None) == ""
        assert normalize_text("") == ""
        assert normalize_text("   ") == ""

    def test_nfc_normalisation(self):
        # e + combining acute composes to é
        assert normalize_text("e\u0301") == "\u00e9"


class TestEntryBounds:
    def test_end_is_next_start(self):
        entries = [{"seconds": 5}, {"seconds": 20}]
        assert entry_bounds(0, entries) == (5, 20)

    def test_last_entry_gets_default_tail(self):
        entries = [{"seconds": 5}, {"seconds": 20}]
        start, end = entry_bounds(1, entries)
        assert start == 20
        assert end == 25

    def test_last_entry_uses_duration_when_known(self):
        entries = [{"seconds": 5}, {"seconds": 20}]
        assert entry_bounds(1, entries, duration=100) == (20, 100)

    def test_non_increasing_next_start_falls_back(self):
        entries = [{"seconds": 20}, {"seconds": 20}]
        start, end = entry_bounds(0, entries)
        assert end > start


class TestBuildSrt:
    def test_renders_sequential_blocks(self):
        entries = [
            {"seconds": 5, "raw_text": "one", "normalized_text": "one"},
            {"seconds": 20, "raw_text": "two", "normalized_text": "two"},
        ]
        srt = build_srt(entries)
        assert srt.startswith("1\n00:00:05,000 --> 00:00:20,000\none")
        assert "2\n00:00:20,000 --> 00:00:25,000\ntwo" in srt

    def test_skips_entries_without_timestamp(self):
        entries = [
            {"seconds": None, "raw_text": "no time", "normalized_text": "no time"},
            {"seconds": 10, "raw_text": "ok", "normalized_text": "ok"},
        ]
        srt = build_srt(entries)
        assert "no time" not in srt
        assert srt.startswith("1\n")

    def test_skips_empty_text(self):
        entries = [
            {"seconds": 5, "raw_text": "   ", "normalized_text": ""},
            {"seconds": 10, "raw_text": "kept", "normalized_text": "kept"},
        ]
        srt = build_srt(entries)
        assert srt.count("-->") == 1
        assert "kept" in srt

    def test_prefers_raw_text_when_asked(self):
        entries = [{"seconds": 5, "raw_text": "RAW,,", "normalized_text": "normalized"}]
        assert "RAW,," in build_srt(entries, use_normalized=False)
        assert "normalized" in build_srt(entries, use_normalized=True)

    def test_falls_back_to_raw_when_normalized_missing(self):
        entries = [{"seconds": 5, "raw_text": "only raw"}]
        assert "only raw" in build_srt(entries)

    def test_empty_input(self):
        assert build_srt([]) == ""
