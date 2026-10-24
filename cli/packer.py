"""Turn a manifest into an AI-ready, human-browsable course corpus.

Output tree (see README for the annotated version)::

    course_export/
    ├─ manifest.json
    ├─ full_course.md          <- the file you hand to an LLM
    ├─ index.md
    ├─ ai_prompt.md
    ├─ transcripts/<date>_<section>.md
    ├─ subtitles/<date>_<section>.srt
    ├─ summaries/<date>_<section>.md
    └─ slides/slide_timeline.md (+ copied images, when local_path is set)

Two rules govern all rendering:

1. Platform AI text is *always* inside a block that says so.  It must never be
   mistakeable for something the teacher said.
2. Inferred values (slide end times, subtitle end times) are labelled as
   inferred, never presented as platform data.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from adapters.textutil import format_time
from cli.manifest import iter_sessions, manifest_stats, save_manifest
from cli.ppt import align_transcript_to_slides
from cli.transcript import build_srt

__all__ = [
    "slugify",
    "session_filename",
    "render_session_markdown",
    "render_summary_markdown",
    "render_slide_timeline",
    "render_full_course_markdown",
    "render_index_markdown",
    "render_ai_prompt",
    "pack",
]

PLATFORM_AI_BANNER = (
    "> ⚠️ **以下为 SJTU 平台 AI 自动生成内容，不是教师原话。**\n"
    "> Platform-generated AI summary. This is NOT the teacher's speech.\n"
)

INFERENCE_NOTE = (
    "_说明：平台只提供每张 PPT 的出现时间，段落的结束时间由下一张 PPT 的开始时间推断。_"
)


def slugify(value: str | None, *, fallback: str = "session", max_length: int = 60) -> str:
    """Make a filesystem-safe name that still keeps CJK characters readable."""
    if not value:
        return fallback
    # Windows forbids \ / : * ? " < > | ; also drop control characters.
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", value)
    cleaned = re.sub(r"\s+", "_", cleaned.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._")
    if not cleaned:
        return fallback
    return cleaned[:max_length].strip("-._") or fallback


def session_filename(session: dict, *, extension: str = ".md") -> str:
    """Build ``2026-09-16_第11节.md``-style filenames."""
    parts = []
    if session.get("date"):
        parts.append(str(session["date"]))
    label = session.get("section") or session.get("title") or session.get("id")
    parts.append(slugify(label, fallback="session"))
    return "_".join(parts) + extension


def _session_heading(session: dict) -> str:
    bits = []
    if session.get("week") is not None:
        bits.append(f"第{session['week']}周")
    if session.get("date"):
        bits.append(str(session["date"]))
    if session.get("section"):
        bits.append(str(session["section"]))
    elif session.get("title"):
        bits.append(str(session["title"]))
    return " ".join(bits) or str(session.get("id") or "未命名课次")


def _session_meta_lines(session: dict) -> list[str]:
    lines = []
    if session.get("room"):
        lines.append(f"- 教室：{session['room']}")
    if session.get("start_time"):
        lines.append(f"- 开始时间：{session['start_time']}")
    if session.get("weekday"):
        lines.append(f"- 星期：{session['weekday']}")
    if session.get("title"):
        lines.append(f"- 平台课次标题：{session['title']}")
    lines.append(f"- 课次 ID：`{session.get('id')}`")
    transcript = session.get("transcript") or []
    lines.append(f"- ASR 条目数：{len(transcript)}")
    if transcript:
        lines.append(
            f"- 时间范围：{transcript[0].get('time')} – {transcript[-1].get('time')}"
        )
    slides = session.get("slides") or []
    if slides:
        lines.append(f"- PPT 采样帧数：{len(slides)}")
    return lines


def render_summary_markdown(session: dict) -> str:
    """Render only the platform AI summary section (概要)."""
    summary = session.get("summary")
    lines = [f"# {_session_heading(session)} — 平台 AI 概要", ""]
    if not summary:
        lines += ["_该课次没有可用的平台 AI 概要。_", ""]
        return "\n".join(lines)

    lines += [PLATFORM_AI_BANNER, ""]
    keywords = summary.get("keywords") or []
    if keywords:
        lines += ["## 关键词", ""]
        lines += [f"- {k}" for k in keywords]
        lines += [""]

    if summary.get("overview"):
        lines += ["## 内容概要", "", str(summary["overview"]), ""]

    chapters = summary.get("chapters") or []
    if chapters:
        lines += ["## 章节导航", ""]
        lines += ["| # | 时间 | 标题 |", "| --- | --- | --- |"]
        for chapter in chapters:
            title = (chapter.get("title") or "").replace("|", "\\|")
            lines.append(
                f"| {chapter.get('index', '')} | {chapter.get('time') or ''} | {title} |"
            )
        lines += [""]
        lines += ["### 各章节摘要", ""]
        for chapter in chapters:
            lines.append(
                f"**{chapter.get('time') or ''} {chapter.get('title') or ''}**"
            )
            lines.append("")
            if chapter.get("summary"):
                lines.append(str(chapter["summary"]))
                lines.append("")
    return "\n".join(lines)


def render_session_markdown(session: dict, *, include_summary: bool = True) -> str:
    """Render one session as a self-contained Markdown document."""
    segments = align_transcript_to_slides(
        session.get("slides") or [], session.get("transcript") or []
    )

    lines = [f"# {_session_heading(session)}", ""]
    lines += _session_meta_lines(session)
    lines += [""]

    if include_summary and session.get("summary"):
        lines += ["## 平台 AI 概要", "", PLATFORM_AI_BANNER, ""]
        summary = session["summary"]
        if summary.get("keywords"):
            lines += ["**关键词**：" + "、".join(summary["keywords"]), ""]
        if summary.get("overview"):
            lines += ["**内容概要**：", "", str(summary["overview"]), ""]
        if summary.get("chapters"):
            lines += ["**章节导航**：", ""]
            for chapter in summary["chapters"]:
                lines.append(
                    f"- `{chapter.get('time') or ''}` {chapter.get('title') or ''}"
                )
            lines += [""]

    lines += ["## 课堂实录", ""]
    transcript = session.get("transcript") or []
    if not transcript:
        lines += ["_该课次没有采集到 ASR 文本。_", ""]
        return "\n".join(lines)

    if session.get("slides"):
        lines += [INFERENCE_NOTE, ""]

    current_slide = None
    for entry in transcript:
        seconds = entry.get("seconds")
        # Emit a heading whenever the slide changes.
        if segments:
            from cli.ppt import slide_at  # local import keeps module import cheap

            segment = slide_at(segments, seconds if isinstance(seconds, (int, float)) else None)
            marker = segment.slide_index if segment else None
            if marker != current_slide:
                current_slide = marker
                if segment is not None and segment.slide_index is not None:
                    label = f"[PPT {segment.slide_index:03d}]"
                    if segment.appearance_count > 1:
                        label += f"（第 {segment.appearance_number}/{segment.appearance_count} 次出现）"
                    if segment.keywords:
                        label += " " + "、".join(segment.keywords)
                    lines += [f"### {segment.start_time} {label}", ""]
                elif segment is not None:
                    lines += [f"### {segment.start_time} [无 PPT]", ""]

        text = entry.get("normalized_text") or entry.get("raw_text") or ""
        lines += [f"**[{entry.get('time') or format_time(seconds)}]** {text}", ""]

    return "\n".join(lines)


def render_slide_timeline(session: dict) -> str:
    """Render the slide/transcript alignment for one session."""
    segments = align_transcript_to_slides(
        session.get("slides") or [], session.get("transcript") or []
    )
    lines = [f"# {_session_heading(session)} — PPT / 语音时间轴对齐", ""]
    if not segments:
        lines += ["_没有可用于对齐的 PPT 或语音数据。_", ""]
        return "\n".join(lines)

    lines += [INFERENCE_NOTE, ""]
    for segment in segments:
        if segment.slide_index is None:
            heading = f"## {segment.time_range} — 开场 / 无 PPT"
        else:
            heading = (
                f"## PPT {segment.slide_index:03d} · {segment.time_range}"
                f"（第 {segment.appearance_number}/{segment.appearance_count} 次出现）"
            )
        lines += [heading, ""]
        if segment.url:
            lines += [f"- 图像：`{segment.url}`", ""]
        if segment.keywords:
            lines += [f"- 关键词：{'、'.join(segment.keywords)}", ""]
        lines += [f"本段 ASR 条目数：{len(segment.entries)}", ""]
        if segment.entries:
            for entry in segment.entries:
                text = entry.get("normalized_text") or entry.get("raw_text") or ""
                lines.append(f"- `[{entry.get('time')}]` {text}")
            lines += [""]
    return "\n".join(lines)


def render_full_course_markdown(manifest: dict) -> str:
    """Render the whole course as one searchable Markdown file."""
    course = manifest.get("course") or {}
    stats = manifest_stats(manifest)
    title = course.get("title") or "未命名课程"

    lines = [f"# {title}", ""]
    lines += [
        "> 本文件由 **SJTU Course Exporter** 从 SJTU 课堂实录平台导出，供个人学习与 AI 辅助整理使用。",
        "> This file was exported for personal study and AI-assisted note taking.",
        "",
    ]
    meta = [
        f"- 教师：{course.get('teacher') or '未知'}",
        f"- 导出时间：{manifest.get('exported_at') or '未知'}",
        f"- 课次数：{stats['sessions']}",
        f"- ASR 条目数：{stats['transcript_entries']}",
        f"- PPT 采样帧数：{stats['slides']}",
        f"- Schema 版本：{manifest.get('schema_version')}",
    ]
    lines += meta + [""]
    lines += [
        "> **阅读约定 / Reading conventions**",
        "> - 「课堂实录」是平台 ASR 原始转写，未经语义纠正，存在同音字与断句错误。",
        "> - 「平台 AI 概要」是平台自动生成的内容，**不是**教师原话。",
        "> - `[PPT nnn]` 标记该时间段屏幕上展示的课件帧；段落结束时间由下一张 PPT 推断。",
        "",
        "---",
        "",
    ]

    for session in iter_sessions(manifest):
        lines += [f"## {_session_heading(session)}", ""]
        lines += _session_meta_lines(session)
        lines += [""]

        summary = session.get("summary")
        if summary:
            lines += ["### 平台 AI 概要", "", PLATFORM_AI_BANNER, ""]
            if summary.get("keywords"):
                lines += ["**关键词**：" + "、".join(summary["keywords"]), ""]
            if summary.get("overview"):
                lines += [str(summary["overview"]), ""]
            if summary.get("chapters"):
                for chapter in summary["chapters"]:
                    lines.append(
                        f"- `{chapter.get('time') or ''}` {chapter.get('title') or ''}"
                    )
                lines += [""]

        lines += ["### 课堂实录", ""]
        transcript = session.get("transcript") or []
        if not transcript:
            lines += ["_该课次没有采集到 ASR 文本。_", "", "---", ""]
            continue

        segments = align_transcript_to_slides(
            session.get("slides") or [], transcript
        )
        if session.get("slides"):
            lines += [INFERENCE_NOTE, ""]

        from cli.ppt import slide_at  # local import keeps module import cheap

        current_slide = None
        for entry in transcript:
            seconds = entry.get("seconds")
            segment = slide_at(
                segments, seconds if isinstance(seconds, (int, float)) else None
            )
            marker = segment.slide_index if segment else None
            if marker != current_slide:
                current_slide = marker
                if segment is not None and segment.slide_index is not None:
                    label = f"[PPT {segment.slide_index:03d}]"
                    if segment.appearance_count > 1:
                        label += (
                            f"（第 {segment.appearance_number}/"
                            f"{segment.appearance_count} 次出现）"
                        )
                    if segment.keywords:
                        label += " " + "、".join(segment.keywords)
                    lines += [f"#### {segment.start_time} {label}", ""]
                elif segment is not None:
                    lines += [f"#### {segment.start_time} [无 PPT]", ""]

            text = entry.get("normalized_text") or entry.get("raw_text") or ""
            lines += [f"**[{entry.get('time') or format_time(seconds)}]** {text}", ""]

        lines += ["---", ""]

    return "\n".join(lines)


def render_index_markdown(manifest: dict) -> str:
    """Render a table of contents with relative links to the split files."""
    course = manifest.get("course") or {}
    stats = manifest_stats(manifest)
    lines = [f"# {course.get('title') or '未命名课程'} — 课程索引", ""]
    lines += [
        "| # | 日期 | 节次 | 教室 | ASR 条目 | PPT | 文件 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for position, session in enumerate(iter_sessions(manifest), start=1):
        name = session_filename(session)
        lines.append(
            "| {p} | {d} | {s} | {r} | {t} | {sl} | [{n}](transcripts/{n}) |".format(
                p=position,
                d=session.get("date") or "",
                s=session.get("section") or session.get("title") or "",
                r=session.get("room") or "",
                t=len(session.get("transcript") or []),
                sl=len(session.get("slides") or []),
                n=name,
            )
        )
    lines += [""]
    lines += [
        "## 全部资料",
        "",
        "- [full_course.md](full_course.md) — 整门课程合并版（推荐直接喂给 AI）",
        "- [ai_prompt.md](ai_prompt.md) — 下游 AI 提示词与使用约定",
        "- [manifest.json](manifest.json) — 结构化原始数据",
        "- [subtitles/](subtitles) — SRT 字幕",
        "- [summaries/](summaries) — 平台 AI 概要",
        "- [slides/](slides) — PPT 时间轴对齐与图片",
        "",
        "## 统计",
        "",
        f"- 课次：{stats['sessions']}（页面发现 {stats['discovered_sessions']}）",
        f"- ASR 条目：{stats['transcript_entries']}",
        f"- PPT 采样帧：{stats['slides']}",
        f"- 带平台 AI 概要的课次：{stats['sessions_with_summary']}",
        "",
    ]
    return "\n".join(lines)


def render_ai_prompt(manifest: dict) -> str:
    """Render the downstream-model briefing (see project brief section 12)."""
    course = manifest.get("course") or {}
    title = course.get("title") or "本课程"
    return f"""# AI 使用说明 / Prompt for downstream models

把本数据包交给 ChatGPT / Claude / Gemini / NotebookLM / 本地 LLM 时，
请把本文件一并提供。下面的说明可以直接复制粘贴。

---

## 这个数据包是什么

这是《{title}》的课堂语料包，包含三类**性质完全不同**的材料：

| 材料 | 来源 | 可信度 |
| --- | --- | --- |
| 课堂实录 ASR | SJTU 课堂实录平台的语音识别 | 内容忠实，但**用字不可靠** |
| PPT 课件帧 | 平台采样并标注了出现时间 | 教师实际展示的内容，可信 |
| 平台 AI 概要 | 平台自动生成 | 是**二次加工**，可能有错，不是教师原话 |

数据来源：SJTU 课堂实录平台（v.sjtu.edu.cn）。仅包含账号本人有权访问的课程。

---

## 必须遵守的处理规则

1. **必须保留原始 ASR。** `manifest.json` 中每条 `raw_text` 是原始识别结果，
   不得覆盖。如需修订，写到新字段里。
2. **可以结合 PPT 与上下文纠正明显错误**，但只能纠正"明显是识别错误"的地方。
3. **无法确定时标记 `【ASR存疑】`**，不要静默改写。
4. **不得凭常识偷偷补充老师没有讲的内容。** 可以补充，但必须显著标注为你的推断。
5. **严格区分四类内容**：
   - 老师课堂内容（ASR）
   - PPT 内容
   - 平台 AI 概要
   - 下游模型的推断与补充

ASR 常见错误类型：

- 同音字：例如「全胜」可能被识别为「全称」「全省」「权称」
- 断句错误、缺少标点
- 专有名词错误：人名、篇名、战例名
- 古文识别错误：引文尤其容易出错
- 口语重复与语气词

---

## 期望的下游产出

请基于本数据包：

1. 整理一份完整的、可读的课程讲义（按课次与时间顺序）
2. 生成知识结构 / 思维导图大纲
3. 建立索引：人物、概念、篇名、战例、关键词
4. 生成**开卷考试用的 Ctrl+F 速查资料**（术语 → 出处时间点）
5. 汇总老师**反复强调**的内容
6. 汇总**考试要求**（时间、形式、范围、题型）
7. 汇总**论文 / 作业要求**（主题、字数、截止时间、格式）
8. 汇总**容易出判断 / 选择 / 简答**的考点
9. 标出所有 `【ASR存疑】` 处，便于人工复核

---

## 数据文件

- `full_course.md` — 全文合并版，适合直接喂给模型
- `transcripts/*.md` — 单课次实录
- `subtitles/*.srt` — 字幕（时间戳来自平台，结束时间为推断值）
- `summaries/*.md` — 平台 AI 概要（请明确标注其性质）
- `slides/slide_timeline.md` — PPT 与语音的时间轴对齐
- `manifest.json` — 结构化原始数据

---

## 重要提醒

- 课程录像、PPT、教师讲话可能受版权、隐私及学校规章约束。
- 本数据包仅供**个人学习与备份**，请勿公开传播或上传到公共服务器。
- 不要用本数据包训练或微调公开发布的模型。
"""


def pack(manifest: dict, out_dir: str | Path) -> dict:
    """Write the complete export tree and return a summary of what was written."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "transcripts").mkdir(exist_ok=True)
    (out / "subtitles").mkdir(exist_ok=True)
    (out / "summaries").mkdir(exist_ok=True)
    (out / "slides").mkdir(exist_ok=True)

    written: list[str] = []

    def write(rel: str, text: str) -> None:
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(rel)

    write("full_course.md", render_full_course_markdown(manifest))
    write("index.md", render_index_markdown(manifest))
    write("ai_prompt.md", render_ai_prompt(manifest))
    save_manifest(manifest, out / "manifest.json")
    written.append("manifest.json")

    # Machine-readable alignment, useful for tooling beyond Markdown.
    alignments: dict[str, list] = {}

    for session in iter_sessions(manifest):
        name = session_filename(session)
        stem = name[:-3] if name.endswith(".md") else name

        write(f"transcripts/{name}", render_session_markdown(session))
        write(f"summaries/{stem}.md", render_summary_markdown(session))
        write(f"slides/{stem}.timeline.md", render_slide_timeline(session))

        transcript = session.get("transcript") or []
        if transcript:
            srt = build_srt(transcript)
            if srt.strip():
                write(f"subtitles/{stem}.srt", srt)

        segments = align_transcript_to_slides(session.get("slides") or [], transcript)
        alignments[session.get("id") or stem] = [s.to_dict() for s in segments]

        # Copy slide images that the userscript already downloaded.
        for slide in session.get("slides") or []:
            local = slide.get("local_path")
            if not local:
                continue
            candidate = Path(local)
            if not candidate.is_absolute():
                candidate = out / local
            if candidate.exists() and candidate.is_file():
                index = slide.get("index") or 0
                seconds = slide.get("seconds") or 0
                safe_time = format_time(seconds).replace(":", "-")
                target = out / "slides" / f"{index:04d}_{safe_time}{candidate.suffix}"
                shutil.copy2(candidate, target)
                written.append(str(target.relative_to(out)))

    write(
        "slides/alignment.json",
        json.dumps(alignments, ensure_ascii=False, indent=2) + "\n",
    )

    return {"output_dir": str(out), "files_written": written, "file_count": len(written)}
