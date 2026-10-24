"""Tests for Markdown rendering, SRT export and the full pack tree."""

from __future__ import annotations

import json

from cli.packer import (
    pack,
    render_ai_prompt,
    render_full_course_markdown,
    render_index_markdown,
    render_session_markdown,
    render_slide_timeline,
    render_summary_markdown,
    session_filename,
    slugify,
)


class TestSlugify:
    def test_keeps_cjk_readable(self):
        assert slugify("第11节") == "第11节"

    def test_strips_path_separators(self):
        assert "/" not in slugify("a/b\\c")
        assert ":" not in slugify("a:b")

    def test_collapses_whitespace_to_underscore(self):
        assert slugify("a   b") == "a_b"

    def test_empty_falls_back(self):
        assert slugify(None, fallback="session") == "session"
        assert slugify("   ", fallback="session") == "session"
        assert slugify("///", fallback="session") == "session"

    def test_truncates(self):
        assert len(slugify("x" * 200)) <= 60


class TestSessionFilename:
    def test_composes_date_and_section(self, minimal_manifest):
        session = minimal_manifest["sessions"][0]
        assert session_filename(session) == "2026-03-04_第1节.md"

    def test_srt_extension(self, minimal_manifest):
        session = minimal_manifest["sessions"][0]
        assert session_filename(session, extension=".srt") == "2026-03-04_第1节.srt"

    def test_falls_back_when_fields_missing(self):
        assert session_filename({}) == "session.md"


class TestRenderSummary:
    def test_marks_platform_ai_clearly(self, minimal_manifest):
        text = render_summary_markdown(minimal_manifest["sessions"][0])
        assert "平台 AI 自动生成" in text
        assert "不是教师原话" in text

    def test_includes_keywords_overview_and_chapters(self, minimal_manifest):
        text = render_summary_markdown(minimal_manifest["sessions"][0])
        assert "关键词甲" in text
        assert "示例概要。" in text
        assert "章节一" in text

    def test_handles_missing_summary(self):
        text = render_summary_markdown({"id": "x", "summary": None})
        assert "没有可用的平台 AI 概要" in text


class TestRenderSession:
    def test_contains_metadata_and_transcript(self, minimal_manifest):
        text = render_session_markdown(minimal_manifest["sessions"][0])
        assert "示例楼101" in text
        assert "第一条原始转写。" in text
        assert "[00:05]" in text

    def test_labels_ppt_end_time_inference(self, minimal_manifest):
        text = render_session_markdown(minimal_manifest["sessions"][0])
        assert "结束时间由下一张 PPT 的开始时间推断" in text

    def test_emits_ppt_headings(self, minimal_manifest):
        text = render_session_markdown(minimal_manifest["sessions"][0])
        assert "[PPT 001]" in text

    def test_handles_empty_transcript(self):
        text = render_session_markdown({"id": "x", "transcript": [], "slides": []})
        assert "没有采集到 ASR 文本" in text

    def test_platform_summary_does_not_leak_into_transcript_section(self, minimal_manifest):
        text = render_session_markdown(minimal_manifest["sessions"][0])
        transcript_section = text.split("## 课堂实录", 1)[1]
        assert "示例概要。" not in transcript_section


class TestRenderFullCourse:
    def test_has_title_and_conventions(self, minimal_manifest):
        text = render_full_course_markdown(minimal_manifest)
        assert text.startswith("# 示例课程")
        assert "阅读约定" in text
        assert "不是教师原话" in text

    def test_includes_every_session(self, minimal_manifest):
        minimal_manifest["sessions"].append(
            dict(minimal_manifest["sessions"][0], id="second", date="2026-03-11", section="第2节")
        )
        text = render_full_course_markdown(minimal_manifest)
        assert "2026-03-04" in text
        assert "2026-03-11" in text

    def test_empty_manifest_still_renders(self):
        text = render_full_course_markdown(
            {"course": {"title": "空课程"}, "sessions": [], "schema_version": 1}
        )
        assert "# 空课程" in text


class TestRenderIndex:
    def test_links_to_split_files(self, minimal_manifest):
        text = render_index_markdown(minimal_manifest)
        assert "full_course.md" in text
        assert "transcripts/2026-03-04_第1节.md" in text
        assert "ai_prompt.md" in text


class TestRenderSlideTimeline:
    def test_numbers_appearances(self, minimal_manifest):
        session = minimal_manifest["sessions"][0]
        session["slides"].append(dict(session["slides"][0], index=3, seconds=60))
        text = render_slide_timeline(session)
        assert "第 1/2 次出现" in text
        assert "第 2/2 次出现" in text

    def test_no_data_case(self):
        text = render_slide_timeline({"id": "x", "slides": [], "transcript": []})
        assert "没有可用于对齐" in text


class TestRenderAiPrompt:
    def test_states_the_ground_rules(self, minimal_manifest):
        text = render_ai_prompt(minimal_manifest)
        assert "必须保留原始 ASR" in text
        assert "【ASR存疑】" in text
        assert "全胜" in text  # the canonical homophone example
        assert "不得凭常识" in text

    def test_includes_the_course_title(self, minimal_manifest):
        assert "示例课程" in render_ai_prompt(minimal_manifest)

    def test_mentions_personal_use_constraint(self, minimal_manifest):
        assert "个人学习" in render_ai_prompt(minimal_manifest)


class TestPack:
    def test_writes_the_documented_tree(self, tmp_path, minimal_manifest):
        result = pack(minimal_manifest, tmp_path / "out")
        out = tmp_path / "out"

        for rel in [
            "manifest.json",
            "full_course.md",
            "index.md",
            "ai_prompt.md",
            "transcripts/2026-03-04_第1节.md",
            "summaries/2026-03-04_第1节.md",
            "subtitles/2026-03-04_第1节.srt",
            "slides/2026-03-04_第1节.timeline.md",
            "slides/alignment.json",
        ]:
            assert (out / rel).exists(), f"missing {rel}"
        assert result["file_count"] == len(result["files_written"])

    def test_manifest_round_trips(self, tmp_path, minimal_manifest):
        pack(minimal_manifest, tmp_path / "out")
        written = json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))
        assert written["course"]["title"] == "示例课程"

    def test_alignment_json_is_machine_readable(self, tmp_path, minimal_manifest):
        pack(minimal_manifest, tmp_path / "out")
        alignment = json.loads((tmp_path / "out" / "slides" / "alignment.json").read_text(encoding="utf-8"))
        session_id = minimal_manifest["sessions"][0]["id"]
        assert session_id in alignment
        assert alignment[session_id][0]["end_time_source"] in {
            "inferred_next_slide",
            "unknown",
        }

    def test_srt_is_valid_looking(self, tmp_path, minimal_manifest):
        pack(minimal_manifest, tmp_path / "out")
        srt = (tmp_path / "out" / "subtitles" / "2026-03-04_第1节.srt").read_text(encoding="utf-8")
        assert srt.startswith("1\n00:00:05,000 -->")
        assert "-->" in srt

    def test_pack_is_idempotent(self, tmp_path, minimal_manifest):
        first = pack(minimal_manifest, tmp_path / "out")
        second = pack(minimal_manifest, tmp_path / "out")
        assert first["file_count"] == second["file_count"]

    def test_session_without_transcript_skips_srt(self, tmp_path, minimal_manifest):
        minimal_manifest["sessions"][0]["transcript"] = []
        pack(minimal_manifest, tmp_path / "out")
        assert not (tmp_path / "out" / "subtitles" / "2026-03-04_第1节.srt").exists()

    def test_copies_slide_images_when_local_path_exists(self, tmp_path, minimal_manifest):
        image = tmp_path / "src" / "slide.jpg"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
        minimal_manifest["sessions"][0]["slides"][0]["local_path"] = str(image)

        pack(minimal_manifest, tmp_path / "out")
        copied = list((tmp_path / "out" / "slides").glob("0001_*.jpg"))
        assert len(copied) == 1

    def test_missing_slide_image_is_skipped_silently(self, tmp_path, minimal_manifest):
        minimal_manifest["sessions"][0]["slides"][0]["local_path"] = str(tmp_path / "nope.jpg")
        result = pack(minimal_manifest, tmp_path / "out")
        assert result["file_count"] > 0
