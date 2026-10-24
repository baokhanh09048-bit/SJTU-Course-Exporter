"""SJTU Course Exporter command line interface.

Usage::

    python -m cli.main inspect        <saved.html>
    python -m cli.main build-manifest <saved.html> --out manifest.json
    python -m cli.main validate       <manifest.json>
    python -m cli.main merge          <manifest.json> --out full_course.md
    python -m cli.main srt            <manifest.json> --out <dir>
    python -m cli.main pack           <manifest.json> --out <dir>
    python -m cli.main api-candidates

Written with :mod:`argparse` on purpose: the tool has zero runtime
dependencies, so a student only needs a Python interpreter.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from adapters.selectors import API, SCHEMA_VERSION
from adapters.sjtu_replay import SJTUReplayAdapter
from cli.manifest import (
    ManifestError,
    load_manifest,
    manifest_stats,
    save_manifest,
    validate_manifest,
)
from cli.packer import (
    pack,
    render_ai_prompt,
    render_full_course_markdown,
    session_filename,
)
from cli.transcript import build_srt

__all__ = ["main", "build_parser"]

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_DATA_ERROR = 3


def _print_header(text: str) -> None:
    print()
    print(text)
    print("-" * max(12, len(text)))


def cmd_inspect(args: argparse.Namespace) -> int:
    """Report what the offline parser can actually see in a saved page."""
    path = Path(args.html)
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        return EXIT_USAGE

    adapter = SJTUReplayAdapter.from_file(str(path))

    _print_header("Course")
    print(f"  title   : {adapter.get_course_title()}")
    print(f"  teacher : {adapter.get_course_teacher()}")
    print(f"  time    : {adapter.get_course_time_range()}")

    refs = adapter.discover_sessions()
    _print_header(f"Sessions ({len(refs)} discovered)")
    for ref in refs:
        marker = "*" if ref.is_current else " "
        print(
            f" {marker} [{ref.index:>2}] {ref.title or '(no title)'}\n"
            f"        date={ref.date} start={ref.start_time} room={ref.room} "
            f"week={ref.week} section={ref.section} platform_ai={ref.has_ai}"
        )
        print(f"        synthetic_id={ref.id}")
    if not refs:
        print("  (none found - the page may not have the 节次 list rendered)")

    transcript = adapter.get_transcript()
    _print_header(f"Transcript ({len(transcript)} entries)")
    if transcript:
        print(f"  first : [{transcript[0]['time']}] {transcript[0]['raw_text'][:60]}")
        print(f"  last  : [{transcript[-1]['time']}] {transcript[-1]['raw_text'][:60]}")
        missing = sum(1 for e in transcript if e.get("seconds") is None)
        print(f"  unparseable timestamps: {missing}")
        ordered = all(
            (transcript[i].get("seconds") or 0) <= (transcript[i + 1].get("seconds") or 0)
            for i in range(len(transcript) - 1)
        )
        print(f"  sorted by timestamp  : {ordered}")
    notice = adapter.get_transcript_notice()
    if notice:
        print(f"  platform notice      : {notice}")

    summary = adapter.get_summary()
    _print_header("Summary")
    if summary:
        print(f"  keywords : {len(summary['keywords'])}")
        for keyword in summary["keywords"]:
            print(f"      - {keyword}")
        overview = summary.get("overview") or ""
        print(f"  overview : {overview[:100]}{'...' if len(overview) > 100 else ''}")
        print(f"  chapters : {len(summary['chapters'])}")
        for chapter in summary["chapters"]:
            rng = (
                f"{chapter['time']}-{chapter['end_seconds']}"
                if chapter.get("end_seconds")
                else str(chapter.get("time"))
            )
            print(f"      [{rng}] {chapter.get('title')}")
    else:
        print("  (no platform AI summary found)")

    slides = adapter.get_slides()
    _print_header(f"Slides ({len(slides)})")
    for slide in slides[: args.max_slides]:
        print(f"  {slide['index']:>3} [{slide['time']}] {slide['url']}")
        if slide["keywords"]:
            print(f"        keywords: {'、'.join(slide['keywords'])}")
    if len(slides) > args.max_slides:
        print(f"  ... and {len(slides) - args.max_slides} more (use --max-slides)")

    video = adapter.get_video()
    _print_header("Video")
    if video:
        print(f"  url (sanitised): {video['url']}")
        print(f"  had signed query parameter: {video['url_had_signature']}")
        print("  download: NOT implemented (out of scope for this version)")
    else:
        print("  (no <video> element found)")
    return EXIT_OK


def cmd_build_manifest(args: argparse.Namespace) -> int:
    """Turn a saved page into a schema-valid manifest."""
    path = Path(args.html)
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        return EXIT_USAGE

    adapter = SJTUReplayAdapter.from_file(str(path))
    manifest = adapter.build_manifest()

    warnings = validate_manifest(manifest)
    out_path = Path(args.out) if args.out else path.with_name("manifest.json")
    save_manifest(manifest, out_path)

    stats = manifest_stats(manifest)
    print(f"wrote {out_path}")
    print(
        f"  course={stats['course_title']!r} sessions={stats['sessions']} "
        f"asr_entries={stats['transcript_entries']} slides={stats['slides']}"
    )
    for warning in warnings:
        print(f"  warning: {warning}", file=sys.stderr)
    print(
        "  note: a saved HTML snapshot only contains the session that was on "
        "screen. Use the userscript's '导出整门课程' to capture the rest.",
        file=sys.stderr,
    )
    return EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        manifest = load_manifest(args.manifest)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR
    try:
        warnings = validate_manifest(
            manifest, allow_future_schema=args.allow_future_schema
        )
    except ManifestError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR

    stats = manifest_stats(manifest)
    print("VALID")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    for warning in warnings:
        print(f"  warning: {warning}", file=sys.stderr)
    return EXIT_OK


def _load_checked(path: str) -> dict:
    manifest = load_manifest(path)
    validate_manifest(manifest)
    return manifest


def cmd_merge(args: argparse.Namespace) -> int:
    try:
        manifest = _load_checked(args.manifest)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR
    text = render_full_course_markdown(manifest)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({len(text):,} chars)")
    return EXIT_OK


def cmd_srt(args: argparse.Namespace) -> int:
    try:
        manifest = _load_checked(args.manifest)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for session in manifest.get("sessions") or []:
        transcript = session.get("transcript") or []
        if not transcript:
            continue
        text = build_srt(transcript)
        if not text.strip():
            continue
        name = session_filename(session, extension=".srt")
        (out_dir / name).write_text(text, encoding="utf-8")
        count += 1
        print(f"wrote {out_dir / name}")
    if count == 0:
        print("no transcript data available; nothing written", file=sys.stderr)
        return EXIT_DATA_ERROR
    return EXIT_OK


def cmd_pack(args: argparse.Namespace) -> int:
    try:
        manifest = _load_checked(args.manifest)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR

    result = pack(manifest, args.out)
    print(f"packed {result['file_count']} files into {result['output_dir']}")
    for rel in result["files_written"]:
        print(f"  {rel}")
    return EXIT_OK


def cmd_api_candidates(args: argparse.Namespace) -> int:
    """Print the endpoint catalogue extracted from the platform's own bundles."""
    print(json.dumps(API, ensure_ascii=False, indent=2))
    print(
        "\nNOTE: 'verified-in-bundle' means the path literal appears in the "
        "platform's shipped JavaScript. Request parameters are NOT confirmed. "
        "Confirm in DevTools before relying on any of these.",
        file=sys.stderr,
    )
    return EXIT_OK


def cmd_ai_prompt(args: argparse.Namespace) -> int:
    try:
        manifest = _load_checked(args.manifest)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR
    text = render_ai_prompt(manifest)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sjtu-course-exporter",
        description=(
            "Turn SJTU classroom replay material into an AI-ready local course "
            "corpus. Exports only what the signed-in user may already access."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python -m cli.main inspect 课堂实录.html\n"
            "  python -m cli.main build-manifest 课堂实录.html --out manifest.json\n"
            "  python -m cli.main pack manifest.json --out course_export\n"
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"sjtu-course-exporter (schema {SCHEMA_VERSION})"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser(
        "inspect", help="report what can be extracted from a saved page"
    )
    p_inspect.add_argument("html", help="saved page (HTML)")
    p_inspect.add_argument(
        "--max-slides", type=int, default=10, help="how many slides to list (default 10)"
    )
    p_inspect.set_defaults(func=cmd_inspect)

    p_build = sub.add_parser(
        "build-manifest", help="build a manifest from a saved page"
    )
    p_build.add_argument("html", help="saved page (HTML)")
    p_build.add_argument("--out", help="output manifest path")
    p_build.set_defaults(func=cmd_build_manifest)

    p_validate = sub.add_parser("validate", help="validate a manifest")
    p_validate.add_argument("manifest")
    p_validate.add_argument("--allow-future-schema", action="store_true")
    p_validate.set_defaults(func=cmd_validate)

    p_merge = sub.add_parser("merge", help="merge all sessions into one Markdown file")
    p_merge.add_argument("manifest")
    p_merge.add_argument("--out", default="full_course.md")
    p_merge.set_defaults(func=cmd_merge)

    p_srt = sub.add_parser("srt", help="write one SRT file per session")
    p_srt.add_argument("manifest")
    p_srt.add_argument("--out", default="subtitles")
    p_srt.set_defaults(func=cmd_srt)

    p_pack = sub.add_parser("pack", help="write the full AI-ready export tree")
    p_pack.add_argument("manifest")
    p_pack.add_argument("--out", default="course_export")
    p_pack.set_defaults(func=cmd_pack)

    p_prompt = sub.add_parser("ai-prompt", help="print the downstream AI briefing")
    p_prompt.add_argument("manifest")
    p_prompt.add_argument("--out")
    p_prompt.set_defaults(func=cmd_ai_prompt)

    p_api = sub.add_parser(
        "api-candidates",
        help="print internal endpoint candidates found in the platform bundles",
    )
    p_api.set_defaults(func=cmd_api_candidates)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:  # pragma: no cover
        print("\ninterrupted", file=sys.stderr)
        return 130
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
