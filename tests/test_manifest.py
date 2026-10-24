"""Tests for the manifest schema, safety checks and validation."""

from __future__ import annotations

import json

import pytest

from cli.manifest import (
    ManifestError,
    assert_no_secrets,
    iter_sessions,
    load_manifest,
    manifest_stats,
    new_manifest,
    save_manifest,
    validate_manifest,
)


class TestNewManifest:
    def test_has_required_keys(self):
        manifest = new_manifest()
        assert manifest["schema_version"] == 1
        assert manifest["platform"] == "sjtu-classroom-replay"
        assert manifest["sessions"] == []
        validate_manifest(manifest)

    def test_accepts_course_details(self):
        manifest = new_manifest("示例课程", "示例教师")
        assert manifest["course"]["title"] == "示例课程"
        assert manifest["course"]["teacher"] == "示例教师"


class TestRoundTrip:
    def test_save_and_load(self, tmp_path, minimal_manifest):
        path = tmp_path / "manifest.json"
        save_manifest(minimal_manifest, path)
        assert path.exists()
        loaded = load_manifest(path)
        assert loaded == minimal_manifest

    def test_preserves_non_ascii(self, tmp_path, minimal_manifest):
        path = tmp_path / "manifest.json"
        save_manifest(minimal_manifest, path)
        assert "示例课程" in path.read_text(encoding="utf-8")

    def test_tolerates_utf8_bom(self, tmp_path):
        path = tmp_path / "bom.json"
        path.write_text('\ufeff{"schema_version": 1}', encoding="utf-8")
        assert load_manifest(path)["schema_version"] == 1

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ManifestError):
            load_manifest(tmp_path / "nope.json")

    def test_invalid_json_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ManifestError):
            load_manifest(path)

    def test_non_object_top_level_raises(self, tmp_path):
        path = tmp_path / "arr.json"
        path.write_text("[1,2,3]", encoding="utf-8")
        with pytest.raises(ManifestError):
            load_manifest(path)


class TestValidation:
    def test_minimal_manifest_is_valid(self, minimal_manifest):
        warnings = validate_manifest(minimal_manifest)
        assert isinstance(warnings, list)

    @pytest.mark.parametrize("key", ["schema_version", "platform", "course", "sessions"])
    def test_missing_required_key(self, minimal_manifest, key):
        del minimal_manifest[key]
        with pytest.raises(ManifestError, match="missing required key"):
            validate_manifest(minimal_manifest)

    def test_wrong_type_for_required_key(self, minimal_manifest):
        minimal_manifest["sessions"] = "not a list"
        with pytest.raises(ManifestError, match="must be list"):
            validate_manifest(minimal_manifest)

    def test_future_schema_is_rejected(self, minimal_manifest):
        minimal_manifest["schema_version"] = 99
        with pytest.raises(ManifestError, match="newer than this tool supports"):
            validate_manifest(minimal_manifest)

    def test_future_schema_can_be_allowed(self, minimal_manifest):
        minimal_manifest["schema_version"] = 99
        validate_manifest(minimal_manifest, allow_future_schema=True)

    def test_session_must_have_id(self, minimal_manifest):
        minimal_manifest["sessions"][0].pop("id")
        with pytest.raises(ManifestError, match="missing 'id'"):
            validate_manifest(minimal_manifest)

    def test_transcript_entry_must_keep_raw_text(self, minimal_manifest):
        del minimal_manifest["sessions"][0]["transcript"][0]["raw_text"]
        with pytest.raises(ManifestError, match="raw_text"):
            validate_manifest(minimal_manifest)

    def test_transcript_must_be_a_list(self, minimal_manifest):
        minimal_manifest["sessions"][0]["transcript"] = {"a": 1}
        with pytest.raises(ManifestError, match="transcript must be a list"):
            validate_manifest(minimal_manifest)

    def test_seconds_must_be_numeric_or_null(self, minimal_manifest):
        minimal_manifest["sessions"][0]["transcript"][0]["seconds"] = "soon"
        with pytest.raises(ManifestError, match="seconds must be a number"):
            validate_manifest(minimal_manifest)

    def test_unsorted_transcript_produces_a_warning(self, minimal_manifest):
        transcript = minimal_manifest["sessions"][0]["transcript"]
        transcript.reverse()
        warnings = validate_manifest(minimal_manifest)
        assert any("not sorted" in w for w in warnings)

    def test_missing_normalized_text_produces_a_warning(self, minimal_manifest):
        minimal_manifest["sessions"][0]["transcript"][0].pop("normalized_text")
        warnings = validate_manifest(minimal_manifest)
        assert any("normalized_text" in w for w in warnings)

    def test_duplicate_session_id_produces_a_warning(self, minimal_manifest):
        minimal_manifest["sessions"].append(dict(minimal_manifest["sessions"][0]))
        warnings = validate_manifest(minimal_manifest)
        assert any("duplicate session id" in w for w in warnings)

    def test_summary_must_be_marked_platform_generated(self, minimal_manifest):
        minimal_manifest["sessions"][0]["summary"]["generated_by_platform_ai"] = False
        warnings = validate_manifest(minimal_manifest)
        assert any("generated_by_platform_ai" in w for w in warnings)

    def test_empty_title_produces_a_warning(self, minimal_manifest):
        minimal_manifest["course"]["title"] = ""
        warnings = validate_manifest(minimal_manifest)
        assert any("course.title" in w for w in warnings)

    def test_no_sessions_produces_a_warning(self, minimal_manifest):
        minimal_manifest["sessions"] = []
        warnings = validate_manifest(minimal_manifest)
        assert any("no sessions" in w for w in warnings)

    def test_video_download_flag_produces_a_warning(self, minimal_manifest):
        minimal_manifest["sessions"][0]["video"]["download_implemented"] = True
        warnings = validate_manifest(minimal_manifest)
        assert any("out of scope" in w for w in warnings)


class TestSecretSafety:
    """The promise: no cookie, token or signed URL may ever reach a manifest."""

    def test_clean_manifest_passes(self, minimal_manifest):
        assert_no_secrets(minimal_manifest)

    def test_signed_url_in_transcript_is_caught(self):
        manifest = {
            "sessions": [
                {
                    "transcript": [
                        {
                            "raw_text": "x",
                            "url": "https://m.invalid/a.mp4?key=0123456789abcdef0123",
                        }
                    ]
                }
            ]
        }
        with pytest.raises(ManifestError, match="signed URL"):
            assert_no_secrets(manifest)

    def test_token_field_is_caught(self):
        manifest = {"sessions": [{"cookie": "JSESSIONID=abcdef"}]}
        with pytest.raises(ManifestError, match="credential-like key"):
            assert_no_secrets(manifest)

    @pytest.mark.parametrize(
        "key",
        ["Cookie", "authorization", "jwt_token", "api-key", "user_password", "session_id"],
    )
    def test_various_credential_key_names_are_caught(self, key):
        manifest = {"sessions": [{"meta": {key: "a-real-value"}}]}
        with pytest.raises(ManifestError):
            assert_no_secrets(manifest)

    def test_empty_credential_field_is_tolerated(self):
        # A schema may mention the field with no value; that leaks nothing.
        assert_no_secrets({"sessions": [{"cookie": ""}]})

    def test_boolean_flag_named_like_a_secret_is_tolerated(self):
        assert_no_secrets({"sessions": [{"video": {"url_had_signature": True}}]})

    def test_redacted_url_is_accepted(self):
        assert_no_secrets(
            {"sessions": [{"video": {"url": "https://m.invalid/a.mp4?key=REDACTED&redacted=1"}}]}
        )

    def test_validation_calls_the_secret_check(self, minimal_manifest):
        minimal_manifest["sessions"][0]["cookie"] = "JSESSIONID=abc"
        with pytest.raises(ManifestError):
            validate_manifest(minimal_manifest)


class TestHelpers:
    def test_iter_sessions(self, minimal_manifest):
        assert len(list(iter_sessions(minimal_manifest))) == 1

    def test_iter_sessions_skips_non_dicts(self):
        assert list(iter_sessions({"sessions": [None, "x", {}]})) == [{}]

    def test_manifest_stats(self, minimal_manifest):
        stats = manifest_stats(minimal_manifest)
        assert stats["course_title"] == "示例课程"
        assert stats["sessions"] == 1
        assert stats["transcript_entries"] == 2
        assert stats["slides"] == 2
        assert stats["sessions_with_summary"] == 1
        assert stats["schema_version"] == 1


class TestExampleManifest:
    def test_shipped_example_is_valid_and_anonymised(self):
        from pathlib import Path

        example = Path(__file__).resolve().parent.parent / "examples" / "manifest.example.json"
        assert example.exists(), "examples/manifest.example.json must ship with the repo"
        manifest = json.loads(example.read_text(encoding="utf-8"))
        validate_manifest(manifest)
        assert_no_secrets(manifest)

    def test_shipped_example_uses_placeholders_only(self):
        """The example must follow the ``示例…`` placeholder convention.

        Real course data is git-ignored, so it cannot be detected by name here.
        Instead we assert the *convention*: every Chinese free-text field in the
        shipped example is built from placeholder vocabulary. A reviewer can
        additionally supply a private denylist via ``local_samples/denylist.txt``
        (one term per line, git-ignored) and this test will enforce it too.
        """
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        text = (repo / "examples" / "manifest.example.json").read_text(encoding="utf-8")

        # Placeholder vocabulary allowed in the public example.
        allowed_markers = ("示例", "章节", "第", "关键词", "原始转写", "时间轴", "PPT")
        import re

        # Structural, non-content strings that are legitimately allowed.
        platform_strings = {"本字幕由 AI 生成，仅供参考"}
        weekday_re = re.compile(r"周[一二三四五六日天]")

        # Any run of >=2 CJK characters that looks like a proper noun must be a
        # placeholder. We check the values, not the keys.
        suspicious = []
        for value in re.findall(r'"[^"]*[\u4e00-\u9fff][^"]*"', text):
            inner = value.strip('"')
            if any(marker in inner for marker in allowed_markers):
                continue
            if inner in platform_strings or weekday_re.fullmatch(inner):
                continue
            # The AI disclaimer is our own boilerplate, authored in this repo.
            if "平台 AI 自动生成内容" in inner and "not the teacher" in inner:
                continue
            suspicious.append(inner)
        assert not suspicious, f"non-placeholder text in public example: {suspicious}"

        denylist = repo / "local_samples" / "denylist.txt"
        if denylist.exists():
            for term in denylist.read_text(encoding="utf-8").splitlines():
                term = term.strip()
                if term:
                    assert term not in text, f"private denylist term leaked: {term}"
