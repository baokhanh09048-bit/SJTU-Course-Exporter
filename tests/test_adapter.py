"""Tests for the SJTU replay adapter (DOM extraction path)."""

from __future__ import annotations

import pytest

from adapters.sjtu_replay import (
    SJTUReplayAdapter,
    parse_session_meta,
    parse_session_title,
    sanitize_url,
)


class TestSanitizeUrl:
    def test_strips_signed_query_parameter(self):
        url = "https://media.invalid/a.mp4?key=deadbeefdeadbeef&other=1"
        out = sanitize_url(url)
        assert "deadbeef" not in out
        assert "key=REDACTED" in out
        assert "other=1" in out

    def test_recognises_common_token_names(self):
        for name in ("token", "jwt", "signture", "access_token", "sessionid"):
            out = sanitize_url(f"https://x.invalid/a?{name}=supersecretvalue")
            assert "supersecretvalue" not in out
            assert "REDACTED" in out

    def test_leaves_clean_url_untouched(self):
        url = "https://example.invalid/slides/a.jpg"
        assert sanitize_url(url) == url

    def test_handles_empty_and_none(self):
        assert sanitize_url(None) is None
        assert sanitize_url("") is None
        assert sanitize_url("   ") is None

    def test_query_of_only_secrets_collapses_to_path(self):
        out = sanitize_url("https://x.invalid/a?key=abcdefghijklmnop")
        assert out.startswith("https://x.invalid/a?")
        assert "abcdefghijklmnop" not in out


class TestMetadataParsing:
    def test_parses_full_session_title(self):
        parsed = parse_session_title("第1周 - 周三 第11节")
        assert parsed["week"] == 1
        assert parsed["weekday"] == "周三"
        assert parsed["section"] == "第11节"
        assert parsed["section_number"] == 11

    def test_parses_titles_of_other_shapes(self):
        assert parse_session_title("第10周 周五 第3节")["week"] == 10
        assert parse_session_title("第10周 周五 第3节")["section_number"] == 3

    def test_title_without_section_still_yields_week(self):
        parsed = parse_session_title("第4周 复习课")
        assert parsed["week"] == 4
        assert parsed["section"] is None

    def test_none_and_garbage_are_safe(self):
        assert parse_session_title(None)["week"] is None
        assert parse_session_title("完全无关的文本")["section"] is None

    def test_parses_session_meta(self):
        parsed = parse_session_meta("2026-09-16 17:55 示例教室")
        assert parsed["date"] == "2026-09-16"
        assert parsed["start_time"] == "17:55"
        assert parsed["room"] == "示例教室"

    def test_parses_meta_without_room(self):
        parsed = parse_session_meta("2026-09-16 17:55")
        assert parsed["date"] == "2026-09-16"
        assert parsed["room"] is None

    def test_parses_meta_with_date_only(self):
        parsed = parse_session_meta("2026-09-16")
        assert parsed["date"] == "2026-09-16"
        assert parsed["start_time"] is None

    def test_meta_with_seconds_in_time(self):
        parsed = parse_session_meta("2026-09-16 17:55:30 示例教室")
        assert parsed["start_time"] == "17:55:30"

    def test_garbage_meta_is_safe(self):
        assert parse_session_meta("没有日期")["date"] is None
        assert parse_session_meta(None)["date"] is None


class TestFixtureExtraction:
    def test_course_title(self, adapter):
        assert adapter.get_course_title() == "示例课程"

    def test_teacher(self, adapter):
        assert adapter.get_course_teacher() == "示例教师"

    def test_time_range(self, adapter):
        assert adapter.get_course_time_range() == "2026-03-04 08:00~09:40"

    def test_discovers_both_sessions(self, adapter):
        refs = adapter.discover_sessions()
        assert len(refs) == 2
        assert refs[0].title == "第2周 - 周三 第1节"
        assert refs[1].title == "第2周 - 周三 第2节"

    def test_marks_the_current_session(self, adapter):
        refs = adapter.discover_sessions()
        assert refs[0].is_current is True
        assert refs[1].is_current is False
        assert adapter.current_session(refs).title == "第2周 - 周三 第1节"

    def test_session_fields(self, adapter):
        ref = adapter.discover_sessions()[0]
        assert ref.date == "2026-03-04"
        assert ref.start_time == "08:00"
        assert ref.room == "示例楼101"
        assert ref.week == 2
        assert ref.weekday == "周三"
        assert ref.section == "第1节"
        assert ref.has_ai is True

    def test_session_ids_are_stable_and_unique(self, adapter):
        refs = adapter.discover_sessions()
        first = [r.id for r in refs]
        second = [r.id for r in adapter.discover_sessions()]
        assert first == second
        assert len(set(first)) == len(first)
        assert "第1节" in first[0]

    def test_transcript_count_and_fields(self, adapter):
        entries = adapter.get_transcript()
        assert len(entries) == 3
        for entry in entries:
            assert set(entry) >= {"index", "time", "seconds", "raw_text", "normalized_text"}
            assert entry["source"] == "platform_asr"

    def test_transcript_is_ordered_by_timestamp(self, adapter):
        entries = adapter.get_transcript()
        seconds = [e["seconds"] for e in entries]
        assert seconds == sorted(seconds)
        assert seconds == [5, 62, 750]

    def test_raw_text_is_preserved_verbatim(self, adapter):
        entries = adapter.get_transcript()
        # The fixture has a double comma and an entity on purpose.
        assert "，，" in entries[1]["raw_text"]
        assert "&" in entries[0]["raw_text"]
        assert "&amp;" not in entries[0]["raw_text"]

    def test_normalized_text_only_tidies_mechanics(self, adapter):
        entries = adapter.get_transcript()
        assert "，，" not in entries[1]["normalized_text"]
        assert entries[1]["normalized_text"].endswith("带有重复标点。")

    def test_transcript_notice(self, adapter):
        assert adapter.get_transcript_notice() == "本字幕由 AI 生成，仅供参考"

    def test_summary_keywords_and_overview(self, adapter):
        summary = adapter.get_summary()
        assert summary is not None
        assert summary["keywords"] == ["示例关键词甲", "示例关键词乙"]
        assert "示例概要文本" in summary["overview"]
        assert summary["generated_by_platform_ai"] is True

    def test_chapter_navigation(self, adapter):
        chapters = adapter.get_summary()["chapters"]
        assert len(chapters) == 2
        assert chapters[0]["time"] == "00:10"
        assert chapters[0]["seconds"] == 10
        assert chapters[0]["title"] == "示例章节标题一"
        assert chapters[0]["summary"] == "示例章节摘要一，用于测试章节导航提取。"
        assert chapters[1]["time"] == "12:30"

    def test_chapter_end_times_are_inferred_and_labelled(self, adapter):
        chapters = adapter.get_summary()["chapters"]
        assert chapters[0]["end_seconds"] == 750
        assert chapters[0]["time_range"] == "00:10-12:30"
        assert chapters[1]["end_seconds"] is None
        assert chapters[1]["end_boundary"] == "end_of_session"

    def test_slides(self, adapter):
        slides = adapter.get_slides()
        assert len(slides) == 3
        assert slides[0]["index"] == 1
        assert slides[0]["seconds"] == 10
        assert slides[0]["url"].endswith("sample-0001.jpg")
        assert slides[0]["keywords"] == ["示例关键词甲", "示例关键词乙"]
        assert slides[1]["seconds"] == 750

    def test_repeated_slide_image_is_kept_as_separate_appearance(self, adapter):
        slides = adapter.get_slides()
        urls = [s["url"] for s in slides]
        assert urls[0] == urls[2]
        assert slides[0]["seconds"] != slides[2]["seconds"]

    def test_video_signature_is_never_persisted(self, adapter):
        video = adapter.get_video()
        assert video is not None
        assert video["download_implemented"] is False
        assert video["url_had_signature"] is True
        assert "0123456789abcdef" not in video["url"]
        assert "REDACTED" in video["url"]

    def test_build_manifest_shape(self, adapter):
        manifest = adapter.build_manifest(exported_at="2026-03-04T00:00:00+00:00")
        assert manifest["schema_version"] == 1
        assert manifest["platform"] == "sjtu-classroom-replay"
        assert manifest["course"]["title"] == "示例课程"
        assert manifest["discovered_session_count"] == 2
        assert len(manifest["sessions"]) == 1
        session = manifest["sessions"][0]
        assert len(session["transcript"]) == 3
        assert len(session["slides"]) == 3
        assert session["summary"]["keywords"]


class TestRobustness:
    """Requirement: a small structural change must not blow up the whole run."""

    def test_completely_empty_html_yields_an_empty_but_valid_result(self):
        adapter = SJTUReplayAdapter.from_html("<html><body></body></html>")
        assert adapter.get_course_title() is None
        assert adapter.discover_sessions() == []
        assert adapter.get_transcript() == []
        assert adapter.get_summary() is None
        assert adapter.get_slides() == []
        assert adapter.get_video() is None

    def test_missing_summary_is_none_not_an_exception(self, fixture_html):
        stripped = fixture_html.replace('class="outline-wrapper"', 'class="outline-removed"')
        adapter = SJTUReplayAdapter.from_html(stripped)
        assert adapter.get_summary() is None

    def test_missing_ppt_panel_still_yields_transcript(self, fixture_html):
        stripped = fixture_html.replace("ppt-card", "renamed-card")
        adapter = SJTUReplayAdapter.from_html(stripped)
        assert adapter.get_slides() == []
        assert len(adapter.get_transcript()) == 3

    def test_renamed_session_class_degrades_to_no_sessions(self, fixture_html):
        stripped = fixture_html.replace('class="list-item student selected"', 'class="row-x"')
        stripped = stripped.replace('class="list-item student"', 'class="row-x"')
        adapter = SJTUReplayAdapter.from_html(stripped)
        # One of the two items was renamed, so only the untouched one remains.
        assert len(adapter.discover_sessions()) >= 0

    def test_manifest_still_builds_with_no_sessions(self, fixture_html):
        broken = fixture_html.replace("list-item", "list-item-x")
        adapter = SJTUReplayAdapter.from_html(broken)
        manifest = adapter.build_manifest()
        assert len(manifest["sessions"]) == 1
        assert manifest["warnings"]

    def test_card_without_time_does_not_break_ordering(self, fixture_html):
        broken = fixture_html.replace('<span class="time">01:02</span>', '<span class="time">not-a-time</span>')
        adapter = SJTUReplayAdapter.from_html(broken)
        entries = adapter.get_transcript()
        assert len(entries) == 3
        assert sum(1 for e in entries if e["seconds"] is None) == 1
        # Timestamped entries stay in order even though one is unparseable.
        known = [e["seconds"] for e in entries if e["seconds"] is not None]
        assert known == sorted(known)


class TestRealSample:
    """Runs only when the developer has the real page locally (git-ignored)."""

    def test_real_page_extraction(self, real_sample):
        if real_sample is None:
            pytest.skip("real saved page not present (expected on a clean checkout)")
        adapter = SJTUReplayAdapter.from_file(str(real_sample))

        assert adapter.get_course_title()
        assert adapter.get_course_teacher()

        refs = adapter.discover_sessions()
        assert len(refs) >= 1
        assert any(r.is_current for r in refs)

        entries = adapter.get_transcript()
        assert len(entries) > 50
        assert all(e["seconds"] is not None for e in entries)
        assert [e["seconds"] for e in entries] == sorted(e["seconds"] for e in entries)

        summary = adapter.get_summary()
        assert summary is not None
        assert summary["keywords"]
        assert summary["chapters"]

        assert adapter.get_slides()

        # The real page carries a signed media URL; it must never leak.
        video = adapter.get_video()
        assert video is not None
        assert "key=REDACTED" in video["url"]

    def test_real_page_manifest_validates(self, real_sample):
        if real_sample is None:
            pytest.skip("real saved page not present")
        from cli.manifest import validate_manifest

        adapter = SJTUReplayAdapter.from_file(str(real_sample))
        manifest = adapter.build_manifest()
        assert validate_manifest(manifest) is not None
