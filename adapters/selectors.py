"""Single source of truth for SJTU replay DOM selectors and HTTP endpoints.

Everything the project knows about the SJTU page layout lives here.  When the
platform is updated, this is the only file that should need editing -- the
adapter, the userscript and the tests all read from it.

Each selector entry is a *priority list*: index 0 is the selector observed in
the real sample page (an anonymised structural sample); the remaining
entries are conservative fallbacks in case the platform renames a CSS class.

Confidence labels
-----------------
VERIFIED   Observed in the real saved page ``课堂实录.html`` (2026-09-22).
INFERRED   Read out of the platform's own JavaScript bundles, not yet confirmed
           against a live response.  Treat as a hypothesis.
UNVERIFIED Purely structural guess.  Should not be relied upon.
"""

from __future__ import annotations

__all__ = ["DOM", "API", "PLATFORMS", "SCHEMA_VERSION"]

SCHEMA_VERSION = 1
PLATFORM_NAME = "sjtu-classroom-replay"

# --------------------------------------------------------------------------
# DOM selectors -- all VERIFIED against local_samples/课堂实录.html
# --------------------------------------------------------------------------

DOM: dict[str, list[str]] = {
    # -- page / course header ---------------------------------------------
    "app_root": ["#app", "div#app"],
    "player_page": [".video-page-wrapper", ".all-in-one-play-link"],
    "course_title": [
        "header .tecl-info .top",
        ".tecl-info > .top",
        ".header-left .top",
    ],
    "course_teacher": [
        "header .tecl-info .bottom > .sle",
        ".tecl-info > .bottom > .sle",
    ],
    "course_time_range": [
        "header .tecl-info .bottom > .time",
        ".tecl-info > .bottom > .time",
    ],
    # -- session list ("节次" tab) ----------------------------------------
    "session_list_root": [".course-list-wrapper", ".course-list-wrapper .list-wrap"],
    "session_list_container": [".course-list-wrapper .list-wrap", "#parentEle"],
    "session_item": [".course-list-wrapper .list-item", ".list-wrap .list-item"],
    "session_item_index": [".list-index .index", ".index"],
    "session_item_title": [".list-item__right-top .title", ".list-item__right .title"],
    "session_item_meta": [
        ".list-item__right-bottom .bottom-left",
        ".list-item__right-bottom .sle",
    ],
    "session_item_ai_flag": [".ai-flag"],
    "session_list_end_tip": [".course-list-wrapper .end-tip", ".end-tip"],
    # -- module tabs -------------------------------------------------------
    "module_tab": [".module-tab .tab-item"],
    # -- transcript ("语音" tab) ------------------------------------------
    "transcript_wrapper": [".ai-caption-wrapper", ".ai-caption-wrapper .main-wrapper"],
    "transcript_container": ["#cardWrapper", ".caption-scroll-wrapper"],
    "transcript_card": ["#cardWrapper .caption-card", ".caption-scroll-wrapper > .caption-card"],
    "transcript_time": [".time-wrapper .time", ".time"],
    "transcript_text": [".caption-text", "span.caption-text"],
    "transcript_tip": [".tip-wrapper .text"],
    # -- summary ("概要" tab) ---------------------------------------------
    "summary_wrapper": [".outline-wrapper"],
    "summary_keyword": [".outline-wrapper .keyword-wrapper .keyword", ".keyword-wrapper .keyword"],
    "summary_overview": [".outline-wrapper .summary-content", ".summary-content"],
    "summary_chapter_item": [
        ".outline-wrapper .chapter-navigation .el-timeline-item",
        ".chapter-navigation .el-timeline-item",
        ".chapter-navigation li",
    ],
    "summary_chapter_time": [".skim-time .time", ".skim-time span.time"],
    "summary_chapter_title": [".skim-title", ".skim-card .skim-title"],
    "summary_chapter_content": [".skim-content", ".skim-card .skim-content"],
    # -- PPT panel ---------------------------------------------------------
    "ppt_wrapper": [".ppt-horizontal-wrapper", "#pptHorizontalWrapper"],
    "ppt_container": ["#pptHorizontalWrapper", ".ppt-card-wrapper__inner"],
    "ppt_card": ["#pptHorizontalWrapper .ppt-card", ".ppt-card-wrapper .ppt-card"],
    "ppt_image": ["img.el-image__inner", ".image-wrapper img", "img"],
    "ppt_time": [".image-wrapper .time", ".time"],
    "ppt_caption_label": [".image-bottom"],
    "ppt_caption_keyword": [".image-bottom .keyword"],
    # -- media -------------------------------------------------------------
    "video_element": ["video#kmd-video-player", ".kmd-player video", "video"],
    "teacher_note": [".teacher-note", ".note-list"],
}

# --------------------------------------------------------------------------
# Internal HTTP API
# --------------------------------------------------------------------------
#
# These paths were extracted from the platform's own bundled JavaScript (see
# the platform DOM selector reference).  They are documented here so
# that the *browser side* can prefer them over DOM scraping once a human has
# confirmed the request/response contract in DevTools.
#
# IMPORTANT: nothing in this repository calls these endpoints on its own.  The
# userscript only issues a request when the user explicitly triggers an export,
# from the page they are already logged into.
#
# status: "verified-in-bundle" == the string literal exists in the shipped JS.
#         "verified-live"       == confirmed against a live network response.

API: dict[str, dict[str, object]] = {
    # -- session (课次) discovery -----------------------------------------
    "course_tecl_page": {
        "method": "POST",
        "path": "/v1/course/ai/course/tecl/page",
        "status": "verified-in-bundle",
        "note": "Paged teaching-class (课次) list for a course.",
    },
    "course_vod_videoinfos": {
        "method": "GET",
        "path": "/v1/course_vod_videoinfos",
        "status": "verified-in-bundle",
        "note": "VOD video info objects for a course.",
    },
    "vod_course_video": {
        "method": "GET",
        "path": "/v1/getVodCourseVideo",
        "status": "verified-in-bundle",
        "note": "Video metadata for a course/session.",
    },
    "course_status_open": {
        "method": "GET",
        "path": "/v1/course/status/open",
        "status": "verified-in-bundle",
    },
    "last_play_info": {
        "method": "GET",
        "path": "/v1/source/vod/lastPlayInfoByTeclId",
        "status": "verified-in-bundle",
    },
    "course_verify": {
        "method": "GET",
        "path": "/v1/course/verify",
        "status": "verified-in-bundle",
        "note": "Query: courId.  Access check for a course.",
    },
    # -- transcript / ASR --------------------------------------------------
    "vod_subtitle_with_keyword": {
        "method": "GET",
        "path": "/v1/course_vod_subtitle_with_keyword",
        "status": "verified-in-bundle",
        "note": (
            "Strongest transcript candidate.  Returns captions together with "
            "keyword positions, matching the .caption-text.keyword highlighting "
            "the page implements.  Exact query parameters are NOT yet verified."
        ),
        "expected_shape": {
            "items": [{"bg": "start, ms", "ed": "end, ms", "res": "text"}],
        },
    },
    "aftertime_captions": {
        "method": "POST",
        "path": "/v1/aftertime/captions/{id}",
        "status": "verified-in-bundle",
        "note": "Caption list by identifier (path segment).",
    },
    "export_course_vod_subtitle": {
        "method": "GET",
        "path": "/v1/exportCourseVodSubtitle",
        "status": "verified-in-bundle",
        "note": "Server-side subtitle export (blob).",
    },
    "ai_course_subtitle_export": {
        "method": "POST",
        "path": "/v1/course/ai/course/subtitle/export",
        "status": "verified-in-bundle",
        "note": "Server-side AI subtitle export. Query: courseId, format (e.g. srt).",
    },
    # -- summary / outline -------------------------------------------------
    "ai_text_summary": {
        "method": "GET",
        "path": "/v1/course/ai/textSummary/{courseId}",
        "status": "verified-in-bundle",
        "note": "Platform AI overview for a course.",
    },
    "ai_summary_search": {
        "method": "GET",
        "path": "/v1/course/ai/summary/search/{courseId}",
        "status": "verified-in-bundle",
        "note": (
            "Returns the AI summary object the page renders as 关键词 / 内容概要 / "
            "章节导航.  Front-end shape (read from chunk 1416): "
            "{summary: {keyPoints: [str], fullOverview: str, "
            "documentSkims: [{content, overview, timeRanges: [{start, end}]}]}}"
        ),
    },
    "ai_summary_ppt_search": {
        "method": "POST",
        "path": "/v1/course/ai/summary/ppt/search",
        "status": "verified-in-bundle",
    },
    "ai_summary_export": {
        "method": "GET",
        "path": "/v1/course/ai/summary/export/{courseId}",
        "status": "verified-in-bundle",
    },
    # -- PPT / slides ------------------------------------------------------
    "ai_ppt": {
        "method": "GET",
        "path": "/v1/course/ai/ppt",
        "status": "verified-in-bundle",
        "note": "PPT slide list used by the horizontal PPT strip.",
    },
    "ai_manage_ppt": {
        "method": "GET",
        "path": "/v1/course/ai/manage/ppt",
        "status": "verified-in-bundle",
    },
    "ai_ppt_pdf": {
        "method": "GET",
        "path": "/v1/course/ai/ppt/download/pdf",
        "status": "verified-in-bundle",
        "note": (
            "Query: courseId.  Downloads the original deck as PDF -- this is the "
            "highest-fidelity slide source available, far better than the sampled "
            "JPEG frames embedded in the saved HTML."
        ),
    },
    "ai_manage_ppt_zip": {
        "method": "GET",
        "path": "/v1/course/ai/manage/ppt/download/zip",
        "status": "verified-in-bundle",
        "note": "Query: courseId.  Bulk slide image archive.",
    },
    "sprite_image": {
        "method": "GET",
        "path": "/v1/spriteImage/get/{source}/{videoId}/{index}",
        "status": "verified-in-bundle",
        "note": "Sprite sheets -- low-resolution video thumbnails, not slides.",
    },
    # -- original courseware attachments ----------------------------------
    "resource_download": {
        "method": "GET",
        "path": "/v1/resource/downLoad",
        "status": "verified-in-bundle",
        "note": (
            "Query: id, vodId, fileName.  Downloads an uploaded courseware "
            "attachment (the teacher's original PPT/PPTX/PDF)."
        ),
    },
    "resource_pack_download": {
        "method": "GET",
        "path": "/v1/resource/pack/downLoad",
        "status": "verified-in-bundle",
        "note": "Query: ids, vodIds, courName.  Bulk courseware archive.",
    },
    "resource_upload_list": {
        "method": "POST",
        "path": "/v1/resource/upload/list",
        "status": "verified-in-bundle",
        "note": "Lists courseware attachments for a session.",
    },
}

# Video is explicitly out of scope for this phase; the field is kept so a later
# version can fill it in without a schema migration.
VIDEO_OUT_OF_SCOPE_NOTE = (
    "Video download is intentionally not implemented. The platform embeds a "
    "signed MP4 URL with a time-limited ?key= parameter; this project only "
    "records that a video exists and never stores the signature."
)
