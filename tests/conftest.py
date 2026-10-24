"""Shared pytest fixtures and path setup.

The project uses a flat layout (``cli/`` and ``adapters/`` at the repo root), so
tests insert the repository root on ``sys.path`` rather than requiring an
editable install.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def fixture_path() -> Path:
    """The anonymised structural fixture committed to the repository."""
    path = FIXTURES / "sample_page.html"
    assert path.exists(), f"missing fixture: {path}"
    return path


@pytest.fixture(scope="session")
def fixture_html(fixture_path: Path) -> str:
    return fixture_path.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def adapter(fixture_path: Path):
    from adapters.sjtu_replay import SJTUReplayAdapter

    return SJTUReplayAdapter.from_file(str(fixture_path))


@pytest.fixture(scope="session")
def real_sample() -> Path | None:
    """The real saved page, when the developer has it locally.

    Real course material is deliberately git-ignored, so tests that need it skip
    on a clean checkout. This lets us verify against genuine platform markup
    locally without ever committing a student's course content.
    """
    candidates = [
        REPO_ROOT / "local_samples" / "课堂实录.html",
        REPO_ROOT / "课堂实录.html",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


@pytest.fixture()
def minimal_manifest() -> dict:
    """A small, fully synthetic manifest used by the packaging tests."""
    return {
        "schema_version": 1,
        "platform": "sjtu-classroom-replay",
        "exporter": {"name": "sjtu-course-exporter", "version": "0.1.0"},
        "exported_at": "2026-03-04T00:00:00+00:00",
        "source": {"kind": "test", "name": "fixture", "capture_scope": "single_page_snapshot"},
        "course": {"id": None, "title": "示例课程", "teacher": "示例教师"},
        "discovered_sessions": [],
        "discovered_session_count": 2,
        "sessions": [
            {
                "id": "2026-03-04-第1节-示例楼101-aaaa1111",
                "id_source": "synthetic",
                "platform_id": None,
                "order": 1,
                "title": "第2周 - 周三 第1节",
                "date": "2026-03-04",
                "start_time": "08:00",
                "week": 2,
                "weekday": "周三",
                "section": "第1节",
                "section_number": 1,
                "room": "示例楼101",
                "has_platform_ai": True,
                "is_current_in_snapshot": True,
                "transcript": [
                    {
                        "index": 0,
                        "time": "00:05",
                        "seconds": 5,
                        "raw_text": "第一条原始转写。",
                        "normalized_text": "第一条原始转写。",
                        "source": "platform_asr",
                    },
                    {
                        "index": 1,
                        "time": "00:20",
                        "seconds": 20,
                        "raw_text": "第二条 原始   转写，，含重复标点。",
                        "normalized_text": "第二条 原始 转写，含重复标点。",
                        "source": "platform_asr",
                    },
                ],
                "summary": {
                    "generated_by_platform_ai": True,
                    "disclaimer": "platform generated",
                    "keywords": ["关键词甲", "关键词乙"],
                    "overview": "示例概要。",
                    "chapters": [
                        {
                            "index": 1,
                            "time": "00:10",
                            "seconds": 10,
                            "title": "章节一",
                            "summary": "章节摘要一。",
                            "end_seconds": 30,
                            "time_range": "00:10-00:30",
                        }
                    ],
                },
                "slides": [
                    {
                        "index": 1,
                        "time": "00:10",
                        "seconds": 10,
                        "url": "https://example.invalid/slides/a.jpg",
                        "local_path": None,
                        "title": None,
                        "keywords": ["关键词甲"],
                    },
                    {
                        "index": 2,
                        "time": "00:30",
                        "seconds": 30,
                        "url": "https://example.invalid/slides/b.jpg",
                        "local_path": None,
                        "title": None,
                        "keywords": ["关键词乙"],
                    },
                ],
                "video": {
                    "download_implemented": False,
                    "url": "https://media.invalid/x.mp4?key=REDACTED&redacted=1",
                    "url_had_signature": True,
                },
                "notices": ["本字幕由 AI 生成，仅供参考"],
            }
        ],
        "extraction": {"method": "dom", "notes": None},
        "warnings": [],
    }
