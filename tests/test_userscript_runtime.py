"""Run the userscript for real, then verify its artefacts with Python.

Syntax checking is not enough, and neither is trusting the script's own summary.
This module does two things:

1. Drives ``tests/userscript/run.js``, which loads the real userscript into
   jsdom and exercises it. A frozen page (the historical regression) is detected
   as a hard process timeout, because a page stuck in a microtask loop never
   yields to the event loop.

2. Independently verifies what the script produced. The ZIP archive it writes is
   opened with Python's :mod:`zipfile` — a completely separate implementation —
   which validates every CRC, proves the UTF-8 file names survived, and lets us
   inspect the manifest the browser side generated. The folder tree written
   through the File System Access API mock is verified the same way.

These tests skip when Node.js or jsdom is unavailable, so ``pytest`` still works
on a plain Python checkout.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
USERSCRIPT_DIR = REPO / "tests" / "userscript"
RUNNER = USERSCRIPT_DIR / "run.js"
ARTIFACTS = USERSCRIPT_DIR / ".artifacts"


def _node() -> str | None:
    return shutil.which("node")


def _jsdom_available(node: str) -> bool:
    probe = subprocess.run(
        [node, "-e", "require.resolve('jsdom')"],
        cwd=str(USERSCRIPT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return probe.returncode == 0


def _run(args: list[str], node: str, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(
        [node, *args],
        cwd=str(USERSCRIPT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def node_bin() -> str:
    node = _node()
    if node is None:
        pytest.skip("Node.js is not installed; skipping userscript runtime tests")
    if not _jsdom_available(node):
        pytest.skip("jsdom is not installed. Run: cd tests/userscript && npm install")
    return node


@pytest.fixture(scope="module")
def runner_result(node_bin: str) -> subprocess.CompletedProcess:
    # The runner enforces a 20 s cap per case; this is the outer net.
    return _run([str(RUNNER)], node_bin, timeout=300)


@pytest.fixture(scope="module")
def harness_current(node_bin: str) -> dict:
    """One folder-mode export of the current session, with real artefacts."""
    out = ARTIFACTS / "pytest-current"
    shutil.rmtree(out, ignore_errors=True)
    proc = _run(
        [
            str(USERSCRIPT_DIR / "harness.js"),
            "--fast-settings",
            "--fake-fsa",
            "--scope",
            "current",
            "--save-mode",
            "folder",
            "--out-dir",
            str(out),
        ],
        node_bin,
        timeout=90,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


class TestUserscriptRuntime:
    def test_runner_passes_all_checks(self, runner_result: subprocess.CompletedProcess):
        output = runner_result.stdout + runner_result.stderr
        assert runner_result.returncode == 0, "userscript checks failed:\n" + output
        assert "checks passed" in output

    def test_no_check_failed(self, runner_result: subprocess.CompletedProcess):
        assert "✗" not in runner_result.stdout, (
            "a userscript check failed:\n" + runner_result.stdout
        )

    def test_page_does_not_freeze(self, runner_result: subprocess.CompletedProcess):
        assert "FROZE" not in runner_result.stdout, (
            "the userscript froze the page:\n" + runner_result.stdout
        )

    def test_batch_export_does_not_mislabel_sessions(
        self, runner_result: subprocess.CompletedProcess
    ):
        """Regression: a no-op session switch must never be reported as success.

        The fixture's second session cannot be loaded (there is no SPA), so the
        correct outcome is one exported session plus a recorded failure — not two
        sessions where the second silently reuses the first one's transcript.
        """
        output = runner_result.stdout
        assert "batch sweep reports the session it could not capture" in output
        assert "batch sweep terminates" in output
        assert "NOT written under the wrong name" in output


class TestUiStructure:
    """The point of this round: scope / content / format / save must be separate."""

    def test_scope_content_format_and_save_are_distinct_sections(self, harness_current: dict):
        text = harness_current.get("panelText") or ""
        for section in ("导出范围", "导出内容", "输出格式", "保存位置"):
            assert section in text, f"missing UI section {section}"

    def test_scope_options_are_the_three_documented_ones(self, harness_current: dict):
        text = harness_current.get("panelText") or ""
        for option in ("当前节", "当前课次组", "整门课程"):
            assert option in text

    def test_content_options_are_the_four_documented_ones(self, harness_current: dict):
        text = harness_current.get("panelText") or ""
        for option in ("语音转写", "PPT 图片", "平台概要", "章节导航"):
            assert option in text

    def test_output_formats_are_pack_and_manifest(self, harness_current: dict):
        text = harness_current.get("panelText") or ""
        assert "AI 资料包" in text
        assert "原始 Manifest JSON" in text

    def test_there_is_exactly_one_primary_action(self, harness_current: dict):
        text = harness_current.get("panelText") or ""
        assert "开始导出" in text
        # The old flat button row must be gone.
        for gone in ("导出 AI 语料", "导出 manifest", "导出当前节"):
            assert gone not in text, f"legacy button still present: {gone}"

    def test_progress_area_reports_the_expected_fields(self, harness_current: dict):
        progress = harness_current.get("progress") or {}
        assert progress.get("phase") in {"空闲", "完成", "完成（有失败）"}
        assert "/" in (progress.get("text") or "")
        assert progress.get("asr") == "3"
        assert progress.get("slides") == "3"
        assert progress.get("success") == "1"
        assert progress.get("fail") == "0"
        assert progress.get("stopVisible") is False


@pytest.fixture(scope="module")
def built_zip(node_bin: str):
    """A whole-course ZIP export, opened with Python's zipfile."""
    out = ARTIFACTS / "course-zip"
    shutil.rmtree(out, ignore_errors=True)
    proc = _run(
        [
            str(USERSCRIPT_DIR / "harness.js"),
            "--fast-settings",
            "--scope",
            "course",
            "--save-mode",
            "zip",
            "--out-dir",
            str(out),
        ],
        node_bin,
        timeout=90,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    archives = list(out.glob("*.zip"))
    assert len(archives) == 1, f"expected exactly one archive, found {archives}"
    with zipfile.ZipFile(archives[0]) as zf:
        yield zf


class TestZipArtefacts:
    """Verify the hand-written ZIP writer with an independent implementation."""

    def test_archive_integrity(self, built_zip: zipfile.ZipFile):
        # Python recomputes every CRC32; this is the real test of the writer.
        assert built_zip.testzip() is None

    def test_archive_contains_the_documented_tree(self, built_zip: zipfile.ZipFile):
        names = sorted(built_zip.namelist())
        assert names == [
            "示例课程/ai_prompt.md",
            "示例课程/full_course.md",
            "示例课程/index.md",
            "示例课程/manifest.json",
            "示例课程/slides/第02周_第1节/001.jpg",
            "示例课程/slides/第02周_第1节/002.jpg",
            "示例课程/slides/第02周_第1节/003.jpg",
            "示例课程/summaries/第02周_第1节.md",
            "示例课程/transcripts/第02周_第1节.md",
        ], names

    def test_slide_images_are_real_jpegs(self, built_zip: zipfile.ZipFile):
        for name in built_zip.namelist():
            if "/slides/" not in name:
                continue
            data = built_zip.read(name)
            assert data[:2] == b"\xff\xd8", f"{name} does not start with a JPEG SOI marker"
            assert len(data) > 10

    def test_utf8_filenames_survive(self, built_zip: zipfile.ZipFile):
        for name in built_zip.namelist():
            assert "示例课程" in name

    def test_manifest_inside_the_archive_is_valid_json(self, built_zip: zipfile.ZipFile):
        from cli.manifest import validate_manifest

        data = json.loads(built_zip.read("示例课程/manifest.json").decode("utf-8"))
        assert data["schema_version"] == 1
        assert data["course"]["title"] == "示例课程"
        assert len(data["sessions"]) == 1
        assert len(data["sessions"][0]["transcript"]) == 3
        # The browser-side manifest must satisfy the same schema the CLI enforces.
        validate_manifest(data)

    def test_manifest_records_the_chosen_options(self, built_zip: zipfile.ZipFile):
        data = json.loads(built_zip.read("示例课程/manifest.json").decode("utf-8"))
        assert data["export_options"]["scope"] == "course"
        assert data["export_options"]["format"] == "pack"
        assert data["export_options"]["content"]["transcript"] is True

    def test_markdown_is_utf8_and_non_empty(self, built_zip: zipfile.ZipFile):
        text = built_zip.read("示例课程/full_course.md").decode("utf-8")
        assert text.startswith("# 示例课程")
        assert "不是教师原话" in text

    def test_no_signed_url_leaked_into_the_archive(self, built_zip: zipfile.ZipFile):
        blob = b"".join(built_zip.read(n) for n in built_zip.namelist())
        assert b"0123456789abcdef" not in blob


class TestFolderArtefacts:
    """Verify what the File System Access path writes."""

    def test_tree_matches_the_documented_session_layout(self, harness_current: dict):
        written = harness_current.get("written") or []
        assert written == sorted(
            [
                "示例课程/ai_prompt.md",
                "示例课程/manifest.json",
                "示例课程/sessions/2026-03-04_第1节/slides/001.jpg",
                "示例课程/sessions/2026-03-04_第1节/slides/002.jpg",
                "示例课程/sessions/2026-03-04_第1节/slides/003.jpg",
                "示例课程/sessions/2026-03-04_第1节/summary.md",
                "示例课程/sessions/2026-03-04_第1节/transcript.md",
            ]
        ), written

    def test_nothing_is_written_to_the_downloads_folder(self, harness_current: dict):
        assert harness_current.get("downloads") == []

    def test_ppt_images_were_really_fetched(self, harness_current: dict):
        assert len(harness_current.get("fetchedUrls") or []) == 3

    def test_transcript_markdown_is_readable(self, harness_current: dict):
        tree = json.loads((ARTIFACTS / "pytest-current" / "fsa-tree.json").read_text("utf-8"))
        entry = tree["示例课程/sessions/2026-03-04_第1节/transcript.md"]
        assert entry["kind"] == "text", "text files must not be written as opaque blobs"
        assert entry["text"].startswith("# 第2周 2026-03-04 第1节")
        assert "[00:05]" in entry["text"]


class TestHarnessDiagnostics:
    """Sanity-check the harness itself so it cannot pass vacuously."""

    def test_harness_reports_the_panel_and_a_manifest(self, harness_current: dict):
        assert harness_current["panelInjected"] is True
        summary = harness_current.get("manifestSummary")
        assert summary is not None, "harness did not read back a manifest"
        assert summary["transcript_entries"] == 3
        assert summary["leaks_signed_key"] is False

    def test_harness_detects_a_deliberate_freeze(self, node_bin: str, tmp_path: Path):
        """Prove the harness can actually catch the bug it was built for.

        A page stuck in a MutationObserver feedback loop never yields to the
        event loop, so the child process hangs and must be killed. A timeout here
        is therefore the *expected* outcome — if this test ever completes
        normally, the harness has lost its teeth and the freeze checks above are
        meaningless.
        """
        broken = tmp_path / "freezing.user.js"
        broken.write_text(
            """
            (function () {
              'use strict';
              const d = document.createElement('div');
              d.id = 'loop-probe';
              document.body.appendChild(d);
              let n = 0;
              const mo = new MutationObserver(() => { n += 1; d.textContent = String(n); });
              mo.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
              d.textContent = '0';
            })();
            """,
            encoding="utf-8",
        )
        try:
            _run(
                [str(USERSCRIPT_DIR / "harness.js"), "--script", str(broken)],
                node_bin,
                timeout=12,
            )
        except subprocess.TimeoutExpired:
            return  # detected: the page never yielded
        pytest.fail(
            "harness failed to notice a self-triggering MutationObserver; the "
            "freeze checks are no longer meaningful"
        )
