// ==UserScript==
// @name         SJTU Course Exporter
// @namespace    https://github.com/sjtu-course-exporter
// @version      0.2.1
// @description  Export SJTU classroom replay transcripts, AI summaries and PPT slides into an AI-ready local course dataset. Text and slides only — no video downloading.
// @author       SJTU Course Exporter contributors
// @match        https://v.sjtu.edu.cn/*
// @run-at       document-idle
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @noframes
// ==/UserScript==

/* eslint-disable no-console */
(function () {
  'use strict';

  // ======================================================================
  // 0. Scope and honesty notes
  // ======================================================================
  //
  // This script runs inside the page the user is already logged into. It never
  // reads, stores or transmits cookies, tokens or credentials, and it never
  // calls an API the page itself would not call. Every request it makes is
  // triggered by an explicit button press.
  //
  // Video is deliberately out of scope: the platform embeds a signed MP4 URL
  // with a time-limited `?key=` parameter. We record that a video exists and
  // strip the signature; we never fetch the media.
  //
  // The extraction path is DOM based. The platform also exposes JSON endpoints
  // (see `API` below) which would be more robust, but their request contracts
  // are not confirmed yet — see docs/reverse-engineering-notes.md section 10.

  const VERSION = '0.2.1';
  const SCHEMA_VERSION = 1;
  const PLATFORM = 'sjtu-classroom-replay';

  // ======================================================================
  // 1. Selectors — mirror of adapters/selectors.py
  // ======================================================================
  // Each entry is a priority list. Index 0 is what the real saved page uses.

  const SEL = {
    courseTitle: ['header .tecl-info .top', '.tecl-info > .top'],
    teacher: ['header .tecl-info .bottom > .sle', '.tecl-info > .bottom > .sle'],
    headerTime: ['header .tecl-info .bottom > .time', '.tecl-info > .bottom > .time'],

    sessionList: ['.course-list-wrapper .list-wrap', '#parentEle'],
    sessionItem: ['.course-list-wrapper .list-item', '.list-wrap .list-item'],
    sessionIndex: ['.list-index .index', '.index'],
    sessionTitle: ['.list-item__right-top .title', '.list-item__right .title'],
    sessionMeta: ['.list-item__right-bottom .bottom-left', '.list-item__right-bottom .sle'],
    sessionAiFlag: ['.ai-flag'],

    moduleTab: ['.module-tab .tab-item'],

    transcriptWrapper: ['.ai-caption-wrapper'],
    transcriptContainer: ['#cardWrapper', '.caption-scroll-wrapper'],
    transcriptCard: ['.caption-card'],
    transcriptTime: ['.time-wrapper .time', '.time'],
    transcriptText: ['.caption-text'],
    transcriptTip: ['.tip-wrapper .text'],

    summaryWrapper: ['.outline-wrapper'],
    summaryKeyword: ['.keyword-wrapper .keyword'],
    summaryOverview: ['.summary-content'],
    summaryChapterItem: ['.chapter-navigation .el-timeline-item', '.chapter-navigation li'],
    summaryChapterTime: ['.skim-time .time'],
    summaryChapterTitle: ['.skim-title'],
    summaryChapterContent: ['.skim-content'],

    pptWrapper: ['.ppt-horizontal-wrapper', '#pptHorizontalWrapper'],
    pptContainer: ['#pptHorizontalWrapper', '.ppt-card-wrapper__inner'],
    pptCard: ['.ppt-card'],
    pptImage: ['img.el-image__inner', '.image-wrapper img', 'img'],
    pptTime: ['.image-wrapper .time', '.time'],
    pptLabel: ['.image-bottom'],
    pptKeyword: ['.image-bottom .keyword'],

    video: ['video#kmd-video-player', '.kmd-player video', 'video'],
  };

  // Endpoint candidates extracted from the platform's own JS bundles.
  // status 'bundle' == the literal exists in shipped JS; params UNCONFIRMED.
  const API = {
    vodSubtitle: { method: 'GET', path: '/v1/course_vod_subtitle_with_keyword', status: 'bundle' },
    aiTextSummary: { method: 'GET', path: '/v1/course/ai/textSummary/{courseId}', status: 'bundle' },
    aiSummarySearch: { method: 'GET', path: '/v1/course/ai/summary/search/{courseId}', status: 'bundle' },
    aiPpt: { method: 'GET', path: '/v1/course/ai/ppt', status: 'bundle' },
    aiPptPdf: { method: 'GET', path: '/v1/course/ai/ppt/download/pdf', status: 'bundle' },
    teclPage: { method: 'POST', path: '/v1/course/ai/course/tecl/page', status: 'bundle' },
  };

  // ======================================================================
  // 2. Small utilities
  // ======================================================================

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function q(root, selectors) {
    for (const sel of selectors) {
      try {
        const found = root.querySelector(sel);
        if (found) return found;
      } catch (_) { /* invalid selector: skip */ }
    }
    return null;
  }

  function qa(root, selectors) {
    for (const sel of selectors) {
      try {
        const found = root.querySelectorAll(sel);
        if (found && found.length) return Array.from(found);
      } catch (_) { /* invalid selector: skip */ }
    }
    return [];
  }

  function textOf(node) {
    if (!node) return null;
    const t = (node.textContent || '').trim();
    if (t) return t;
    const title = node.getAttribute && node.getAttribute('title');
    return title ? title.trim() : null;
  }

  function parseTime(value) {
    if (value == null) return null;
    if (typeof value === 'number') return Math.floor(value);
    const s = String(value).trim();
    if (!s) return null;
    if (/^\d+$/.test(s)) return parseInt(s, 10);
    const m = s.match(/^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?$/);
    if (!m) return null;
    const h = m[1] ? parseInt(m[1], 10) : 0;
    return h * 3600 + parseInt(m[2], 10) * 60 + parseInt(m[3], 10);
  }

  function formatTime(seconds) {
    if (seconds == null) return '';
    const total = Math.max(0, Math.floor(seconds));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const pad = (n) => String(n).padStart(2, '0');
    return h ? `${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
  }

  function formatSrtTime(seconds) {
    const totalMs = Math.max(0, Math.round((seconds || 0) * 1000));
    const h = Math.floor(totalMs / 3600000);
    const m = Math.floor((totalMs % 3600000) / 60000);
    const s = Math.floor((totalMs % 60000) / 1000);
    const ms = totalMs % 1000;
    const pad = (n, w) => String(n).padStart(w, '0');
    return `${pad(h, 2)}:${pad(m, 2)}:${pad(s, 2)},${pad(ms, 3)}`;
  }

  // Mechanical only: whitespace, duplicated punctuation, entities, NFC.
  // Never performs homophone or word-level correction.
  function normalizeText(raw) {
    if (!raw) return '';
    let t = String(raw)
      .replace(/&nbsp;/g, ' ')
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/\u3000/g, ' ');
    try { t = t.normalize('NFC'); } catch (_) { /* older engine */ }
    t = t.replace(/\s+/g, ' ');
    t = t.replace(/([，。！？、；：,.!?;:])\1+/g, '$1');
    t = t.replace(/\s+([，。！？、；：）】》」』,.!?;:)])/g, '$1');
    t = t.replace(/([（【《「『(])\s+/g, '$1');
    return t.trim();
  }

  const SENSITIVE_KEYS = new Set([
    'key', 'token', 'access_token', 'jwt', 'jwt-token', 'signture', 'signature',
    'sig', 'auth', 'authorization', 'session', 'sessionid', 'ticket', 'secret',
  ]);

  function sanitizeUrl(url) {
    if (!url) return null;
    const s = String(url).trim();
    if (!s || s.indexOf('?') === -1) return s || null;
    const [base, query] = s.split('?');
    const kept = [];
    let redacted = false;
    for (const part of query.split('&')) {
      if (!part) continue;
      const eq = part.indexOf('=');
      const name = (eq === -1 ? part : part.slice(0, eq)).toLowerCase();
      if (SENSITIVE_KEYS.has(name)) {
        kept.push(`${part.slice(0, eq)}=REDACTED`);
        redacted = true;
      } else {
        kept.push(part);
      }
    }
    let out = base;
    if (kept.length) out += '?' + kept.join('&');
    if (redacted) out += (kept.length ? '&' : '?') + 'redacted=1';
    return out;
  }

  async function sha1Hex(text) {
    if (window.crypto && window.crypto.subtle && window.isSecureContext) {
      try {
        const buf = await window.crypto.subtle.digest('SHA-1', new TextEncoder().encode(text));
        return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, '0')).join('');
      } catch (_) { /* fall through */ }
    }
    // Non-cryptographic fallback; only used to build a stable-ish identifier.
    let h = 0;
    for (let i = 0; i < text.length; i++) {
      h = (Math.imul(31, h) + text.charCodeAt(i)) | 0;
    }
    return (h >>> 0).toString(16).padStart(8, '0');
  }

  function timestampForFilename(date) {
    const d = date || new Date();
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  }

  function safeFilename(name, fallback) {
    const cleaned = String(name || '')
      .replace(/[\\/:*?"<>|\u0000-\u001f]/g, '-')
      .replace(/\s+/g, '_')
      .replace(/-{2,}/g, '-')
      .replace(/^[-._]+|[-._]+$/g, '');
    return cleaned || (fallback || 'export');
  }

  // ======================================================================
  // 3. Parsing helpers for session metadata
  // ======================================================================

  function parseSessionTitle(title) {
    const out = { week: null, weekday: null, section: null, sectionNumber: null };
    if (!title) return out;
    const full = title.match(/第\s*(\d+)\s*周\s*[-–—]?\s*周?([一二三四五六日天])\s*第\s*(\d+)\s*节/);
    if (full) {
      out.week = parseInt(full[1], 10);
      out.weekday = '周' + full[2];
      out.sectionNumber = parseInt(full[3], 10);
      out.section = '第' + full[3] + '节';
      return out;
    }
    const w = title.match(/第\s*(\d+)\s*周/);
    if (w) out.week = parseInt(w[1], 10);
    const s = title.match(/第\s*(\d+)\s*节/);
    if (s) {
      out.sectionNumber = parseInt(s[1], 10);
      out.section = '第' + s[1] + '节';
    }
    return out;
  }

  function parseSessionMeta(meta) {
    const out = { date: null, startTime: null, room: null };
    if (!meta) return out;
    const m = meta.trim().match(/^(\d{4}-\d{2}-\d{2})(?:\s+(\d{1,2}:\d{2}(?::\d{2})?))?(?:\s+(\S.*?))?\s*$/);
    if (!m) return out;
    out.date = m[1] || null;
    out.startTime = m[2] || null;
    out.room = m[3] ? m[3].trim() : null;
    return out;
  }

  // ======================================================================
  // 4. Page model: discovery and extraction
  // ======================================================================

  function getCourseTitle() {
    return textOf(q(document, SEL.courseTitle));
  }

  function getTeacher() {
    return textOf(q(document, SEL.teacher));
  }

  function getHeaderTimeRange() {
    const node = q(document, SEL.headerTime);
    return node ? (node.getAttribute('title') || textOf(node)) : null;
  }

  function getTranscriptNotice() {
    return textOf(q(document, SEL.transcriptTip));
  }

  /** Discover every session (课次) the page exposes. Nothing is hardcoded. */
  function discoverSessions() {
    const items = qa(document, SEL.sessionItem);
    return items.map((item, position) => {
      const titleNode = q(item, SEL.sessionTitle);
      const metaNode = q(item, SEL.sessionMeta);
      const indexNode = q(item, SEL.sessionIndex);
      const title = titleNode
        ? (titleNode.getAttribute('title') || textOf(titleNode))
        : null;
      const meta = metaNode
        ? (metaNode.getAttribute('title') || textOf(metaNode))
        : null;
      const parsedTitle = parseSessionTitle(title);
      const parsedMeta = parseSessionMeta(meta);
      const indexText = textOf(indexNode);
      return {
        element: item,
        domIndex: position,
        order: indexText && /^\d+$/.test(indexText) ? parseInt(indexText, 10) : position + 1,
        title,
        date: parsedMeta.date,
        startTime: parsedMeta.startTime,
        room: parsedMeta.room,
        week: parsedTitle.week,
        weekday: parsedTitle.weekday,
        section: parsedTitle.section,
        sectionNumber: parsedTitle.sectionNumber,
        hasAi: qa(item, SEL.sessionAiFlag).length > 0,
        isCurrent: item.classList.contains('selected') || item.classList.contains('active'),
      };
    });
  }

  function sessionId(ref) {
    const parts = [ref.date || 'undated'];
    parts.push(ref.section || (ref.sectionNumber != null ? '第' + ref.sectionNumber + '节' : 'session'));
    if (ref.room) parts.push(ref.room);
    return parts.join('-');
  }

  function extractTranscript() {
    const wrapper = q(document, SEL.transcriptWrapper) || document;
    const container = q(wrapper, SEL.transcriptContainer) || wrapper;
    let cards = qa(container, SEL.transcriptCard);
    if (!cards.length) cards = qa(document, SEL.transcriptCard);

    const entries = cards.map((card, position) => {
      const timeNode = q(card, SEL.transcriptTime);
      const textNode = q(card, SEL.transcriptText);
      const time = textOf(timeNode);
      const raw = textNode ? textNode.textContent || '' : '';
      const id = card.getAttribute('id') || '';
      const m = id.match(/^card(\d+)$/);
      return {
        index: m ? parseInt(m[1], 10) : position,
        time,
        seconds: parseTime(time),
        raw_text: raw,
        normalized_text: normalizeText(raw),
        source: 'platform_asr',
      };
    });

    if (entries.some((e) => e.seconds != null)) {
      entries.sort((a, b) => {
        const as = a.seconds == null ? Infinity : a.seconds;
        const bs = b.seconds == null ? Infinity : b.seconds;
        return as - bs || a.index - b.index;
      });
    }
    return entries;
  }

  function extractSummary() {
    const wrapper = q(document, SEL.summaryWrapper);
    if (!wrapper) return null;

    const keywords = [];
    for (const node of qa(wrapper, SEL.summaryKeyword)) {
      const value = textOf(node);
      if (value && keywords.indexOf(value) === -1) keywords.push(value);
    }

    const overview = textOf(q(wrapper, SEL.summaryOverview));

    const chapters = qa(wrapper, SEL.summaryChapterItem).map((item, i) => {
      const time = textOf(q(item, SEL.summaryChapterTime));
      return {
        index: i + 1,
        time,
        seconds: parseTime(time),
        title: textOf(q(item, SEL.summaryChapterTitle)),
        summary: textOf(q(item, SEL.summaryChapterContent)),
      };
    });

    // The platform publishes only a start time; be explicit about the inference.
    chapters.forEach((chapter, i) => {
      chapter.end_seconds = i + 1 < chapters.length ? chapters[i + 1].seconds : null;
      chapter.time_range =
        chapter.seconds != null && chapter.end_seconds != null
          ? `${formatTime(chapter.seconds)}-${formatTime(chapter.end_seconds)}`
          : null;
    });

    if (!keywords.length && overview == null && !chapters.length) return null;
    return {
      generated_by_platform_ai: true,
      disclaimer:
        'SJTU 平台 AI 自动生成内容，非教师原话。Platform AI generated; not the teacher\'s words.',
      keywords,
      overview,
      chapters,
    };
  }

  function extractSlides() {
    const wrapper = q(document, SEL.pptWrapper) || document;
    const container = q(wrapper, SEL.pptContainer) || wrapper;
    let cards = qa(container, SEL.pptCard);
    if (!cards.length) cards = qa(document, SEL.pptCard);

    return cards.map((card, position) => {
      const imgNode = q(card, SEL.pptImage);
      const time = textOf(q(card, SEL.pptTime));
      const labelNode = q(card, SEL.pptLabel);
      const keywords = [];
      for (const kw of qa(labelNode, SEL.pptKeyword)) {
        const value = textOf(kw);
        if (value && keywords.indexOf(value) === -1) keywords.push(value);
      }
      const id = card.getAttribute('id') || '';
      const m = id.match(/(\d+)$/);
      const absolute = imgNode
        ? (imgNode.currentSrc || imgNode.src || null)
        : null;
      return {
        index: m ? parseInt(m[1], 10) + 1 : position + 1,
        time,
        seconds: parseTime(time),
        url: sanitizeUrl(absolute),
        local_path: null,
        title: null,
        keywords,
        caption_label: textOf(labelNode),
        is_current: card.classList.contains('active'),
        source: 'live_dom',
      };
    });
  }

  function extractVideo() {
    const node = q(document, SEL.video);
    if (!node) return null;
    const src = node.getAttribute('src');
    return {
      download_implemented: false,
      note:
        'Video download is intentionally not implemented. The platform embeds a ' +
        'signed MP4 URL with a time-limited ?key= parameter; this project only ' +
        'records that a video exists and never stores the signature.',
      url: sanitizeUrl(src),
      url_had_signature: !!(src && src.indexOf('?') !== -1),
      poster: sanitizeUrl(node.getAttribute('poster')),
    };
  }

  async function buildSessionExport(ref, courseTitle) {
    const transcript = extractTranscript();
    const summary = extractSummary();
    const slides = extractSlides();
    const video = extractVideo();
    const notice = getTranscriptNotice();

    const id = sessionId(ref);
    return {
      id: `${id}-${(await sha1Hex(`${ref.date}|${ref.startTime}|${ref.title}`)).slice(0, 8)}`,
      id_source: 'synthetic',
      platform_id: null,
      order: ref.order,
      title: ref.title,
      date: ref.date,
      start_time: ref.startTime,
      week: ref.week,
      weekday: ref.weekday,
      section: ref.section,
      section_number: ref.sectionNumber,
      room: ref.room,
      has_platform_ai: ref.hasAi,
      is_current_in_snapshot: ref.isCurrent,
      course_title: courseTitle || null,
      transcript,
      summary,
      slides,
      video,
      notices: notice ? [notice] : [],
      captured_at: new Date().toISOString(),
    };
  }

  // ======================================================================
  // 5. Waiting: condition based, never a blind fixed sleep
  // ======================================================================

  /**
   * True when *node* lives inside our own panel. Used to ignore mutations the
   * script itself causes, so no observer can ever be fed by its own output.
   */
  function isPanelNode(node) {
    if (!node) return false;
    // Guard against running after the document has been torn down (e.g. the
    // window closing mid-export); a stray observer callback must never throw.
    if (typeof document === 'undefined' || !document) return false;
    const panel = document.getElementById('sce-panel');
    if (!panel) return false;
    const el = node.nodeType === 1 ? node : node.parentElement;
    return !!(el && (el === panel || panel.contains(el)));
  }

  /**
   * Resolve once `predicate()` returns truthy. Uses a MutationObserver on the
   * whole document for responsiveness plus a low-frequency polling fallback for
   * changes that do not fire mutations (e.g. class-only updates on detached
   * nodes). Rejects on timeout.
   */
  function waitUntil(predicate, { timeout = 30000, interval = 250, label = 'condition' } = {}) {
    return new Promise((resolve, reject) => {
      let settled = false;
      const finish = (fn, arg) => {
        if (settled) return;
        settled = true;
        observer.disconnect();
        clearInterval(poller);
        clearTimeout(timer);
        fn(arg);
      };

      const check = () => {
        let value;
        try {
          value = predicate();
        } catch (err) {
          finish(reject, err);
          return;
        }
        if (value) finish(resolve, value);
      };

      const observer = new MutationObserver((records) => {
        // Our own panel writes are irrelevant to every wait condition and
        // would otherwise fire this callback on every log line.
        for (let i = 0; i < records.length; i++) {
          if (!isPanelNode(records[i].target)) {
            check();
            return;
          }
        }
      });
      observer.observe(document.documentElement, {
        childList: true, subtree: true, attributes: true, characterData: true,
      });
      const poller = setInterval(check, interval);
      const timer = setTimeout(
        () => finish(reject, new Error(`timeout after ${timeout}ms waiting for ${label}`)),
        timeout,
      );
      check();
    });
  }

  /**
   * Wait until the page content signature has changed away from `previous` and
   * then stayed unchanged for `quietMs`. This is how we detect "the next
   * session finished loading" without guessing a delay.
   */
  function contentSignature() {
    const cards = qa(document, SEL.transcriptCard);
    const first = cards.length ? textOf(q(cards[0], SEL.transcriptTime)) : null;
    const last = cards.length ? textOf(q(cards[cards.length - 1], SEL.transcriptTime)) : null;
    const header = getHeaderTimeRange();
    const current = document.querySelector('.list-item.selected, .list-item.active');
    const currentTitle = current ? textOf(q(current, SEL.sessionTitle)) : null;
    return [header, currentTitle, cards.length, first, last].join('|');
  }

  /**
   * Wait for the session content to actually CHANGE away from
   * `previousSignature`, and then to stay unchanged for `quietMs`.
   *
   * The order matters and is not negotiable: requiring a *change* first is what
   * stops a click that silently did nothing from being reported as a success.
   * An earlier version began its "is it stable?" clock immediately, so a
   * no-op click looked stable after quietMs and the caller happily exported the
   * PREVIOUS session's transcript under the new session's identity.
   *
   * Rejects on timeout so the caller records the session as failed instead of
   * emitting data attached to the wrong id.
   */
  async function waitForSessionChange(previousSignature, options = {}) {
    const timeout = options.timeout || 45000;
    const quietMs = options.quietMs || 700;
    let changed = false;
    let lastSignature = previousSignature;
    let stableSince = null;

    return waitUntil(() => {
      const signature = contentSignature();

      if (!changed) {
        // Still waiting for evidence that anything happened at all.
        if (signature === previousSignature) return false;
        changed = true;
        lastSignature = signature;
        stableSince = Date.now();
        return false;
      }

      if (signature !== lastSignature) {
        // Still settling; restart the quiet window.
        lastSignature = signature;
        stableSince = Date.now();
        return false;
      }

      if (stableSince != null && Date.now() - stableSince >= quietMs) {
        const cards = qa(document, SEL.transcriptCard);
        if (cards.length) return signature;
      }
      return false;
    }, { timeout, interval: 200, label: 'session content change' });
  }

  /** The session id of whichever row is currently marked selected. */
  function selectedSessionKey() {
    const refs = discoverSessions();
    const current = refs.find((r) => r.isCurrent);
    return current ? sessionId(current) : null;
  }

  function clickSession(ref) {
    const target = ref.element;
    if (!target) throw new Error('session element is gone');
    const clickable = target.querySelector('.list-item__right') || target;
    clickable.click();
  }

  // ======================================================================
  // 6. Output planning — what to keep, and where it goes (pure functions)
  // ======================================================================
  //
  // Everything in this section is deliberately side-effect free and has no DOM
  // or network dependency, so it can be unit tested directly. The extractors in
  // section 4 run unchanged and always capture everything; these functions only
  // decide what is KEPT and how it is laid out on disk.

  /** The 导出内容 checkboxes. All on by default. */
  function defaultContent() {
    return { transcript: true, slides: true, summary: true, chapters: true };
  }

  const CONTENT_LABELS = {
    transcript: '语音转写',
    slides: 'PPT 图片',
    summary: '平台概要',
    chapters: '章节导航',
  };

  /**
   * Apply the 导出内容 choices to an already-captured session.
   *
   * Pure: returns a new object and never mutates the input, so the untouched
   * extraction result is always available for comparison.
   *
   * - transcript: keep the ASR entries (drives transcript.md and .srt)
   * - slides:     keep the PPT frame metadata (drives the slide files)
   * - summary:    keep keywords + overview of the platform AI summary
   * - chapters:   keep the platform AI chapter navigation
   */
  function applyContentOptions(session, content) {
    const c = Object.assign(defaultContent(), content || {});
    const out = Object.assign({}, session);

    out.transcript = c.transcript ? (session.transcript || []).slice() : [];
    out.slides = c.slides ? (session.slides || []).slice() : [];

    const src = session.summary;
    if (src && (c.summary || c.chapters)) {
      out.summary = Object.assign({}, src, {
        keywords: c.summary ? (src.keywords || []).slice() : [],
        overview: c.summary ? src.overview : null,
        chapters: c.chapters ? (src.chapters || []).slice() : [],
      });
      const empty =
        !out.summary.keywords.length && !out.summary.overview && !out.summary.chapters.length;
      if (empty) out.summary = null;
    } else {
      out.summary = null;
    }
    return out;
  }

  /** ``第01周_第11节`` — used for file names in the flat course layout. */
  function sessionFileStem(session) {
    const week = session.week != null ? `第${String(session.week).padStart(2, '0')}周` : null;
    const section = session.section || session.title || session.id || 'session';
    return safeFilename([week, section].filter(Boolean).join('_'), 'session');
  }

  /** ``2026-09-16_第12节`` — used for per-session folders. */
  function sessionFolderStem(session) {
    const parts = [session.date, session.section || session.title || session.id];
    return safeFilename(parts.filter(Boolean).join('_'), 'session');
  }

  function courseFolderName(course) {
    return safeFilename((course && course.title) || 'sjtu-course', 'sjtu-course');
  }

  /** Sanitise every segment of a '/'-joined relative path. */
  function safeRelativePath(relative) {
    return String(relative || '')
      .split('/')
      .filter(Boolean)
      .map((segment) => safeFilename(segment, 'item'))
      .join('/');
  }

  const IMAGE_EXTENSIONS = {
    'image/jpeg': 'jpg',
    'image/jpg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
    'image/gif': 'gif',
  };

  function extensionForMime(mime, fallbackUrl) {
    if (mime && IMAGE_EXTENSIONS[mime]) return IMAGE_EXTENSIONS[mime];
    const match = /\.([a-z0-9]{2,5})(?:\?|#|$)/i.exec(fallbackUrl || '');
    return match ? match[1].toLowerCase() : 'jpg';
  }

  /**
   * Turn a manifest plus the user's choices into a concrete list of files.
   *
   * @returns {{rootFolder: string, files: Array, stats: object}}
   *   Each file is either ``{path, text}`` or ``{path, data, mime}``.
   *
   * Layouts
   * -------
   * scope = course  → the flat, AI-friendly course layout::
   *
   *     <course>/manifest.json, full_course.md, index.md, ai_prompt.md
   *     <course>/transcripts/<第01周_第11节>.md
   *     <course>/subtitles/<第01周_第11节>.srt
   *     <course>/summaries/<第01周_第11节>.md
   *     <course>/slides/<第01周_第11节>/001.jpg
   *
   * scope = current | group → everything for those sessions lives under their
   * own folder so a quick "just this lecture" export never scatters files::
   *
   *     <course>/manifest.json
   *     <course>/ai_prompt.md
   *     <course>/sessions/<2026-09-16_第12节>/transcript.md
   *     <course>/sessions/<2026-09-16_第12节>/summary.md
   *     <course>/sessions/<2026-09-16_第12节>/transcript.srt
   *     <course>/sessions/<2026-09-16_第12节>/slides/001.jpg
   */
  function planOutput(manifest, options) {
    const opts = Object.assign(
      {
        scope: 'current',
        content: defaultContent(),
        format: 'pack',
        wantSrt: false,
        keepManifest: true,
        slideFiles: {},
        exportedAt: new Date().toISOString(),
      },
      options || {},
    );
    const content = Object.assign(defaultContent(), opts.content || {});
    const course = manifest.course || {};
    const rootFolder = courseFolderName(course);
    const sessions = manifest.sessions || [];
    const files = [];
    const add = (relative, payload) => {
      const path = safeRelativePath(`${rootFolder}/${relative}`);
      if (path) files.push(Object.assign({ path }, payload));
    };

    // ---- raw machine-readable manifest ---------------------------------
    if (opts.format === 'manifest' || opts.keepManifest) {
      add('manifest.json', { text: JSON.stringify(manifest, null, 2) + '\n' });
    }

    // A manifest-only export stops here: the point is the JSON, nothing else.
    if (opts.format === 'manifest') {
      return { rootFolder, files, stats: summarizePlan(files, sessions) };
    }

    const slideFiles = opts.slideFiles || {};

    if (opts.scope === 'course') {
      add('full_course.md', { text: renderFullCourseMarkdown(course, sessions, content, opts.exportedAt) });
      add('index.md', { text: renderIndexMarkdown(manifest) });
      add('ai_prompt.md', { text: renderAiPrompt(course) });

      for (const session of sessions) {
        const stem = sessionFileStem(session);
        if (content.transcript && (session.transcript || []).length) {
          add(`transcripts/${stem}.md`, { text: renderSessionMarkdown(session, content) });
          if (opts.wantSrt) {
            const srt = renderSrt(session.transcript);
            if (srt.trim()) add(`subtitles/${stem}.srt`, { text: srt });
          }
        }
        if (session.summary) {
          add(`summaries/${stem}.md`, { text: renderSummaryMarkdown(session, content) });
        }
        if (content.slides && (session.slides || []).length) {
          // Drive the file list from the session's own (already filtered) slide
          // list, so unticking PPT really does leave the images out.
          const wanted = new Set((session.slides || []).map((s) => s.index));
          for (const image of slideFiles[session.id] || []) {
            if (!wanted.has(image.index)) continue;
            add(
              `slides/${stem}/${String(image.index).padStart(3, '0')}.${image.ext}`,
              { data: image.bytes, mime: image.mime },
            );
          }
        }
      }
      return { rootFolder, files, stats: summarizePlan(files, sessions) };
    }

    // ---- per-session layout (current / group) --------------------------
    add('ai_prompt.md', { text: renderAiPrompt(course) });
    if (sessions.length > 1) {
      add('full_course.md', { text: renderFullCourseMarkdown(course, sessions, content, opts.exportedAt) });
      add('index.md', { text: renderIndexMarkdown(manifest) });
    }

    for (const session of sessions) {
      const folder = `sessions/${sessionFolderStem(session)}`;
      if (content.transcript && (session.transcript || []).length) {
        add(`${folder}/transcript.md`, { text: renderSessionMarkdown(session, content) });
        if (opts.wantSrt) {
          const srt = renderSrt(session.transcript);
          if (srt.trim()) add(`${folder}/transcript.srt`, { text: srt });
        }
      }
      if (session.summary) {
        add(`${folder}/summary.md`, { text: renderSummaryMarkdown(session, content) });
      }
      if (content.slides && (session.slides || []).length) {
        const wanted = new Set((session.slides || []).map((s) => s.index));
        for (const image of slideFiles[session.id] || []) {
          if (!wanted.has(image.index)) continue;
          add(
            `${folder}/slides/${String(image.index).padStart(3, '0')}.${image.ext}`,
            { data: image.bytes, mime: image.mime },
          );
        }
      }
    }
    return { rootFolder, files, stats: summarizePlan(files, sessions) };
  }

  function summarizePlan(files, sessions) {
    return {
      fileCount: files.length,
      sessionCount: (sessions || []).length,
      slideCount: files.filter((f) => f.data).length,
      textCount: files.filter((f) => typeof f.text === 'string').length,
    };
  }

  // ======================================================================
  // 7. ZIP writer — dependency free (used when the browser has no
  //    File System Access API, so a whole course is ONE download)
  // ======================================================================

  let CRC_TABLE = null;

  function crcTable() {
    if (CRC_TABLE) return CRC_TABLE;
    const table = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) {
        c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      }
      table[n] = c >>> 0;
    }
    CRC_TABLE = table;
    return table;
  }

  function crc32(bytes) {
    const table = crcTable();
    let crc = 0xffffffff;
    for (let i = 0; i < bytes.length; i++) {
      crc = table[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
    }
    return (crc ^ 0xffffffff) >>> 0;
  }

  function toUtf8(text) {
    if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(text);
    // Very old engines: escape-based UTF-8 encoder.
    const encoded = unescape(encodeURIComponent(text));
    const out = new Uint8Array(encoded.length);
    for (let i = 0; i < encoded.length; i++) out[i] = encoded.charCodeAt(i);
    return out;
  }

  function dosDateTime(date) {
    const d = date || new Date();
    const time =
      (d.getHours() << 11) | (d.getMinutes() << 5) | Math.floor(d.getSeconds() / 2);
    const day = ((d.getFullYear() - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();
    return { time: time & 0xffff, date: day & 0xffff };
  }

  /**
   * Build a single ZIP archive using the "store" method (no compression).
   *
   * Store is the right choice here: the bulk of a pack is already-compressed
   * JPEG slide frames, and text compresses poorly enough that the added
   * deflate dependency is not worth it.
   *
   * @param {Array<{path: string, text?: string, data?: Uint8Array}>} entries
   * @returns {Uint8Array}
   */
  function buildZip(entries, now) {
    const stamp = dosDateTime(now);
    const chunks = [];
    const central = [];
    let offset = 0;

    const push = (bytes) => {
      chunks.push(bytes);
      offset += bytes.length;
    };

    for (const entry of entries) {
      const nameBytes = toUtf8(entry.path);
      const data =
        entry.data !== undefined && entry.data !== null
          ? entry.data instanceof Uint8Array
            ? entry.data
            : new Uint8Array(entry.data)
          : toUtf8(entry.text == null ? '' : String(entry.text));

      const crc = crc32(data);
      const size = data.length;

      const header = new Uint8Array(30 + nameBytes.length);
      const hv = new DataView(header.buffer);
      hv.setUint32(0, 0x04034b50, true); // local file header signature
      hv.setUint16(4, 20, true); // version needed to extract
      hv.setUint16(6, 0x0800, true); // general purpose flag: UTF-8 names
      hv.setUint16(8, 0, true); // method: store
      hv.setUint16(10, stamp.time, true);
      hv.setUint16(12, stamp.date, true);
      hv.setUint32(14, crc, true);
      hv.setUint32(18, size, true); // compressed size
      hv.setUint32(22, size, true); // uncompressed size
      hv.setUint16(26, nameBytes.length, true);
      hv.setUint16(28, 0, true); // extra field length
      header.set(nameBytes, 30);

      const localOffset = offset;
      push(header);
      push(data);

      const cd = new Uint8Array(46 + nameBytes.length);
      const cv = new DataView(cd.buffer);
      cv.setUint32(0, 0x02014b50, true); // central directory signature
      cv.setUint16(4, 20, true); // version made by
      cv.setUint16(6, 20, true); // version needed
      cv.setUint16(8, 0x0800, true); // UTF-8 names
      cv.setUint16(10, 0, true); // store
      cv.setUint16(12, stamp.time, true);
      cv.setUint16(14, stamp.date, true);
      cv.setUint32(16, crc, true);
      cv.setUint32(20, size, true);
      cv.setUint32(24, size, true);
      cv.setUint16(28, nameBytes.length, true);
      cv.setUint16(30, 0, true); // extra
      cv.setUint16(32, 0, true); // comment
      cv.setUint16(34, 0, true); // disk number
      cv.setUint16(36, 0, true); // internal attributes
      cv.setUint32(38, 0, true); // external attributes
      cv.setUint32(42, localOffset, true);
      cd.set(nameBytes, 46);
      central.push(cd);
    }

    const centralOffset = offset;
    let centralSize = 0;
    for (const block of central) {
      push(block);
      centralSize += block.length;
    }

    const end = new Uint8Array(22);
    const ev = new DataView(end.buffer);
    ev.setUint32(0, 0x06054b50, true); // end of central directory
    ev.setUint16(4, 0, true);
    ev.setUint16(6, 0, true);
    ev.setUint16(8, entries.length, true);
    ev.setUint16(10, entries.length, true);
    ev.setUint32(12, centralSize, true);
    ev.setUint32(16, centralOffset, true);
    ev.setUint16(20, 0, true); // comment length
    push(end);

    const total = new Uint8Array(offset);
    let cursor = 0;
    for (const block of chunks) {
      total.set(block, cursor);
      cursor += block.length;
    }
    return total;
  }

  // ======================================================================
  // 8. Renderers (Markdown / SRT), options aware
  // ======================================================================

  function sessionHeading(session) {
    return [
      session.week != null ? `第${session.week}周` : null,
      session.date,
      session.section || session.title,
    ].filter(Boolean).join(' ') || session.id;
  }

  function hasSummaryContent(session) {
    if (!session || !session.summary) return false;
    const s = session.summary;
    return !!(
      (s.keywords && s.keywords.length) ||
      s.overview ||
      (s.chapters && s.chapters.length)
    );
  }

  function renderSessionMarkdown(session, content) {
    const c = Object.assign(defaultContent(), content || {});
    const lines = [];
    lines.push(`# ${sessionHeading(session)}`, '');
    if (session.room) lines.push(`- 教室：${session.room}`);
    if (session.start_time) lines.push(`- 开始时间：${session.start_time}`);
    lines.push(`- 课次 ID：\`${session.id}\``);
    if (c.transcript) {
      lines.push(`- ASR 条目数：${session.transcript.length}`);
      if (session.transcript.length) {
        lines.push(`- 时间范围：${session.transcript[0].time} – ${session.transcript[session.transcript.length - 1].time}`);
      }
    }
    if (c.slides && session.slides.length) lines.push(`- PPT 采样帧数：${session.slides.length}`);
    lines.push('');

    if ((c.summary || c.chapters) && hasSummaryContent(session)) {
      lines.push(...renderSummaryBody(session, c));
    }

    if (!c.transcript) return lines.join('\n').trimEnd() + '\n';

    lines.push('## 课堂实录', '');
    if (c.slides && session.slides.length) {
      lines.push('_说明：平台只提供每张 PPT 的出现时间，段落结束时间由下一张 PPT 的开始时间推断。_', '');
    }

    const timed = (c.slides ? session.slides : [])
      .filter((s) => s.seconds != null)
      .sort((a, b) => a.seconds - b.seconds);

    let currentSlide = null;
    for (const entry of session.transcript) {
      let marker = null;
      let slideAt = null;
      if (entry.seconds != null && timed.length) {
        for (let i = 0; i < timed.length; i++) {
          const start = timed[i].seconds;
          const end = i + 1 < timed.length ? timed[i + 1].seconds : Infinity;
          if (entry.seconds >= start && entry.seconds < end) {
            slideAt = timed[i];
            break;
          }
        }
        marker = slideAt ? slideAt.index : null;
      }
      if (marker !== currentSlide) {
        currentSlide = marker;
        if (slideAt) {
          let label = `[PPT ${String(slideAt.index).padStart(3, '0')}]`;
          if (slideAt.keywords && slideAt.keywords.length) {
            label += ' ' + slideAt.keywords.join('、');
          }
          lines.push(`### ${formatTime(slideAt.seconds)} ${label}`, '');
        }
      }
      lines.push(`**[${entry.time || formatTime(entry.seconds)}]** ${entry.normalized_text || entry.raw_text}`, '');
    }
    return lines.join('\n');
  }

  /** The 平台 AI 概要 block shared by the session and summary renderers. */
  function renderSummaryBody(session, content) {
    const c = Object.assign(defaultContent(), content || {});
    const s = session.summary;
    const lines = [];
    lines.push('## 平台 AI 概要', '');
    lines.push('> ⚠️ **以下为 SJTU 平台 AI 自动生成内容，不是教师原话。**', '');
    if (c.summary && s.keywords && s.keywords.length) {
      lines.push('**关键词**：' + s.keywords.join('、'), '');
    }
    if (c.summary && s.overview) lines.push(s.overview, '');
    if (c.chapters && s.chapters && s.chapters.length) {
      lines.push('**章节导航**：', '');
      for (const chapter of s.chapters) {
        lines.push(`- \`${chapter.time || ''}\` ${chapter.title || ''}`);
      }
      lines.push('');
    }
    return lines;
  }

  function renderSummaryMarkdown(session, content) {
    const lines = [`# ${sessionHeading(session)} — 平台 AI 概要`, ''];
    if (!hasSummaryContent(session)) {
      lines.push('_本次未导出平台 AI 概要。_', '');
      return lines.join('\n');
    }
    lines.push(...renderSummaryBody(session, content));
    return lines.join('\n');
  }

  function renderSrt(transcript) {
    const blocks = [];
    let counter = 0;
    transcript.forEach((entry, i) => {
      const text = (entry.normalized_text || entry.raw_text || '').trim();
      if (!text || entry.seconds == null) return;
      let start = entry.seconds;
      let end;
      if (i + 1 < transcript.length && transcript[i + 1].seconds != null && transcript[i + 1].seconds > start) {
        end = transcript[i + 1].seconds;
      } else {
        end = start + 5;
      }
      counter += 1;
      blocks.push(`${counter}\n${formatSrtTime(start)} --> ${formatSrtTime(end)}\n${text}\n`);
    });
    return blocks.join('\n');
  }

  function renderFullCourseMarkdown(course, sessions, content, exportedAt) {
    const c = Object.assign(defaultContent(), content || {});
    const lines = [`# ${course.title || '未命名课程'}`, ''];
    lines.push('> 本文件由 SJTU Course Exporter 导出，供个人学习与 AI 辅助整理使用。', '');
    lines.push(`- 教师：${course.teacher || '未知'}`);
    lines.push(`- 导出时间：${exportedAt || new Date().toISOString()}`);
    lines.push(`- 课次数：${sessions.length}`);
    lines.push(`- ASR 条目数：${sessions.reduce((n, s) => n + (s.transcript || []).length, 0)}`);
    lines.push(`- PPT 采样帧数：${sessions.reduce((n, s) => n + (s.slides || []).length, 0)}`);
    lines.push('');
    lines.push('> **阅读约定**');
    if (c.transcript) lines.push('> - 「课堂实录」是平台 ASR 原始转写，未经语义纠正。');
    if (c.summary || c.chapters) lines.push('> - 「平台 AI 概要」是平台生成内容，**不是**教师原话。');
    if (c.slides) lines.push('> - `[PPT nnn]` 标记该时间段展示的课件帧，结束时间为推断值。');
    lines.push('', '---', '');
    for (const session of sessions) {
      lines.push(renderSessionMarkdown(session, c), '---', '');
    }
    return lines.join('\n');
  }

  function renderIndexMarkdown(manifest) {
    const course = manifest.course || {};
    const sessions = manifest.sessions || [];
    const lines = [`# ${course.title || '未命名课程'} — 课程索引`, ''];
    lines.push(`- 导出时间：${manifest.exported_at || ''}`);
    lines.push(`- 课次数：${sessions.length}`);
    if (manifest.discovered_session_count != null) {
      lines.push(`- 页面发现课次：${manifest.discovered_session_count}`);
    }
    lines.push('');
    lines.push('| # | 日期 | 节次 | 教室 | ASR | PPT |');
    lines.push('| --- | --- | --- | --- | --- | --- |');
    sessions.forEach((session, i) => {
      lines.push(
        `| ${i + 1} | ${session.date || ''} | ${session.section || session.title || ''} | ` +
          `${session.room || ''} | ${(session.transcript || []).length} | ${(session.slides || []).length} |`,
      );
    });
    lines.push('');
    lines.push('## 文件');
    lines.push('');
    lines.push('- `full_course.md` — 整门课程合并版，推荐直接喂给 AI');
    lines.push('- `ai_prompt.md` — 下游 AI 提示词与纠错守则');
    lines.push('- `transcripts/` — 单课次实录');
    lines.push('- `subtitles/` — SRT 字幕');
    lines.push('- `summaries/` — 平台 AI 概要');
    lines.push('- `slides/` — PPT 采样帧');
    lines.push('- `manifest.json` — 结构化原始数据');
    lines.push('');
    return lines.join('\n');
  }

  function renderAiPrompt(course) {
    return [
      `# AI 使用说明 — ${course.title || '本课程'}`,
      '',
      '本数据包包含三类性质不同的材料：',
      '',
      '| 材料 | 来源 | 可信度 |',
      '| --- | --- | --- |',
      '| 课堂实录 ASR | 平台语音识别 | 内容忠实，但用字不可靠 |',
      '| PPT 课件帧 | 平台标注出现时间 | 教师实际展示内容，可信 |',
      '| 平台 AI 概要 | 平台自动生成 | 二次加工，可能有错 |',
      '',
      '## 必须遵守的规则',
      '',
      '1. 必须保留原始 ASR（`raw_text`），不得覆盖。',
      '2. 可以结合 PPT 与上下文纠正明显识别错误。',
      '3. 无法确定时标记 `【ASR存疑】`，不要静默改写。',
      '4. 不得凭常识补充老师没有讲的内容；如需补充必须显著标注为推断。',
      '5. 严格区分：老师课堂内容 / PPT / 平台 AI 概要 / 模型推断。',
      '',
      'ASR 常见错误：同音字（「全胜」→「全称」「全省」「权称」）、断句错误、',
      '专有名词错误、古文引文错误、口语重复。',
      '',
      '## 期望产出',
      '',
      '1. 完整课程讲义（按课次与时间顺序）',
      '2. 知识结构 / 思维导图大纲',
      '3. 索引：人物、概念、篇名、战例、关键词',
      '4. 开卷考试 Ctrl+F 速查资料（术语 → 出处时间点）',
      '5. 老师反复强调的内容',
      '6. 考试要求（时间、形式、范围、题型）',
      '7. 论文 / 作业要求（主题、字数、截止、格式）',
      '8. 易出判断 / 选择 / 简答的考点',
      '9. 全部 `【ASR存疑】` 清单',
      '',
      '## 提醒',
      '',
      '- 课程录像、PPT、教师讲话可能受版权、隐私及学校规章约束。',
      '- 仅供个人学习与备份，请勿公开传播或上传到公共服务器。',
      '',
    ].join('\n');
  }

  // ======================================================================
  // 9. Saving: File System Access API first, ZIP fallback
  // ======================================================================
  //
  // Browser reality check: the File System Access API deliberately does NOT
  // expose a file's absolute path to the page. We can learn the chosen folder's
  // NAME but never "D:\\...". The UI therefore shows the folder name and the
  // user is told in the README which directory they picked. See README §保存位置.

  const IDB_NAME = 'sjtu-course-exporter';
  const IDB_STORE = 'handles';
  const IDB_KEY = 'exportDirectory';

  // ---- low level download sink ------------------------------------------
  // Only used for the single-archive / single-file path. A normal pack goes
  // through writePlanToDirectory so nothing floods the Downloads folder.

  const downloads = [];

  function downloadBlob(filename, blob) {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.style.display = 'none';
    document.body.appendChild(anchor);
    anchor.click();
    setTimeout(() => {
      anchor.remove();
      URL.revokeObjectURL(url);
    }, 10000);
    downloads.push(filename);
  }

  function downloadText(filename, text, mime) {
    downloadBlob(filename, new Blob([text], { type: (mime || 'text/plain') + ';charset=utf-8' }));
  }

  function downloadJson(filename, data) {
    downloadText(filename, JSON.stringify(data, null, 2) + '\n', 'application/json');
  }

  function supportsDirectoryPicker() {
    try {
      return typeof window.showDirectoryPicker === 'function';
    } catch (_) {
      return false;
    }
  }

  function idbOpen() {
    return new Promise((resolve) => {
      if (typeof indexedDB === 'undefined' || !indexedDB) return resolve(null);
      let request;
      try {
        request = indexedDB.open(IDB_NAME, 1);
      } catch (_) {
        return resolve(null);
      }
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(IDB_STORE)) db.createObjectStore(IDB_STORE);
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => resolve(null);
      request.onblocked = () => resolve(null);
    });
  }

  /** Directory-handle persistence. Silently a no-op without IndexedDB. */
  const handleStore = {
    async save(handle) {
      const db = await idbOpen();
      if (!db) return false;
      return new Promise((resolve) => {
        try {
          const tx = db.transaction(IDB_STORE, 'readwrite');
          tx.objectStore(IDB_STORE).put(handle, IDB_KEY);
          tx.oncomplete = () => resolve(true);
          tx.onerror = () => resolve(false);
          tx.onabort = () => resolve(false);
        } catch (_) {
          resolve(false);
        }
      });
    },
    async load() {
      const db = await idbOpen();
      if (!db) return null;
      return new Promise((resolve) => {
        try {
          const tx = db.transaction(IDB_STORE, 'readonly');
          const get = tx.objectStore(IDB_STORE).get(IDB_KEY);
          get.onsuccess = () => resolve(get.result || null);
          get.onerror = () => resolve(null);
        } catch (_) {
          resolve(null);
        }
      });
    },
    async clear() {
      const db = await idbOpen();
      if (!db) return false;
      return new Promise((resolve) => {
        try {
          const tx = db.transaction(IDB_STORE, 'readwrite');
          tx.objectStore(IDB_STORE).delete(IDB_KEY);
          tx.oncomplete = () => resolve(true);
          tx.onerror = () => resolve(false);
        } catch (_) {
          resolve(false);
        }
      });
    },
  };

  /** Ask the browser what it thinks of our permission on an existing handle. */
  async function directoryPermission(handle) {
    if (!handle || typeof handle.queryPermission !== 'function') return 'unknown';
    try {
      return await handle.queryPermission({ mode: 'readwrite' });
    } catch (_) {
      return 'unknown';
    }
  }

  async function requestDirectoryPermission(handle) {
    if (!handle || typeof handle.requestPermission !== 'function') return false;
    try {
      const state = await handle.requestPermission({ mode: 'readwrite' });
      return state === 'granted';
    } catch (_) {
      return false;
    }
  }

  /** Split a relative path into directory segments plus a file name. */
  function splitPath(relative) {
    const segments = String(relative || '').split('/').filter(Boolean);
    const name = segments.pop();
    return { directories: segments, name: name || 'file' };
  }

  /** Write one UTF-8 text file or binary blob under *root* (creating folders). */
  async function writeFileToDirectory(root, relative, payload) {
    const { directories, name } = splitPath(relative);
    let dir = root;
    for (const segment of directories) {
      dir = await dir.getDirectoryHandle(segment, { create: true });
    }
    const fileHandle = await dir.getFileHandle(name, { create: true });
    const writable = await fileHandle.createWritable();
    try {
      if (payload && payload.data !== undefined) {
        await writable.write(payload.data);
      } else {
        // Write the string directly: the File System Access API encodes it as
        // UTF-8 and the file keeps its real type, whereas wrapping it in a Blob
        // would label every .md/.json file as text/plain.
        await writable.write(String(payload && payload.text != null ? payload.text : ''));
      }
    } finally {
      await writable.close();
    }
    return relative;
  }

  async function writePlanToDirectory(plan, root, onProgress) {
    let written = 0;
    for (const file of plan.files) {
      await writeFileToDirectory(root, file.path, file);
      written += 1;
      if (onProgress) onProgress(written, plan.files.length, file.path);
    }
    return written;
  }

  /**
   * Persist a plan according to the user's 保存方式 choice.
   *
   * @returns {{mode: string, location: string, fileCount: number}}
   */
  async function savePlan(plan, options) {
    const opts = options || {};
    if (opts.saveMode === SAVE_MODE.FOLDER) {
      const handle = opts.directoryHandle;
      if (!handle) {
        throw new Error('尚未选择保存文件夹，请先点击“选择保存文件夹”。');
      }
      const granted = (await directoryPermission(handle)) === 'granted'
        ? true
        : await requestDirectoryPermission(handle);
      if (!granted) {
        throw new Error('没有文件夹写入权限，请重新点击“选择保存文件夹”授权。');
      }
      const count = await writePlanToDirectory(plan, handle, opts.onFile);
      return { mode: SAVE_MODE.FOLDER, location: handle.name || '(已选择的文件夹)', fileCount: count };
    }

    // ZIP: exactly one download, never a flood of loose files.
    //
    // A plan that contains a single file (e.g. "原始 Manifest JSON") is written
    // directly instead of being wrapped in an archive the user would have to
    // unpack just to read one JSON file.
    if (plan.files.length === 1) {
      const only = plan.files[0];
      const name = only.path.split('/').pop();
      if (only.data !== undefined) {
        downloadBlob(name, new Blob([only.data], { type: only.mime || 'application/octet-stream' }));
      } else {
        downloadText(
          name,
          only.text,
          name.endsWith('.json') ? 'application/json' : 'text/markdown',
        );
      }
      return { mode: SAVE_MODE.ZIP, location: name, fileCount: 1, wrapped: false };
    }

    const zipEntries = plan.files.map((file) => ({
      path: file.path,
      text: file.text,
      data: file.data,
    }));
    const zipBytes = buildZip(zipEntries, opts.now);
    const zipName = opts.zipName || `${plan.rootFolder}.zip`;
    downloadBlob(zipName, new Blob([zipBytes], { type: 'application/zip' }));
    return { mode: SAVE_MODE.ZIP, location: zipName, fileCount: plan.files.length, wrapped: true };
  }

  // ======================================================================
  // 10. Settings, UI state and resume bookkeeping
  // ======================================================================

  const SAVE_MODE = { FOLDER: 'folder', ZIP: 'zip' };
  const FORMAT = { PACK: 'pack', MANIFEST: 'manifest' };

  /** Advanced-only knobs. Everything else lives in the visible UI. */
  const DEFAULTS = {
    quietMs: 700,
    sessionTimeout: 45000,
    betweenSessionsMs: 1200,
    maxRetries: 1,
    resume: true,
  };

  const settings = Object.assign({}, DEFAULTS);
  for (const key of Object.keys(DEFAULTS)) {
    const stored = GM_getValue('setting.' + key, undefined);
    if (stored !== undefined) settings[key] = stored;
  }

  function saveSettings() {
    for (const key of Object.keys(DEFAULTS)) GM_setValue('setting.' + key, settings[key]);
  }

  // Resume support: remembers which sessions already exported successfully.
  function resumeKey(courseTitle) {
    return 'done.' + (courseTitle || 'unknown-course');
  }

  function getDone(courseTitle) {
    const raw = GM_getValue(resumeKey(courseTitle), '[]');
    try {
      const list = JSON.parse(raw);
      return Array.isArray(list) ? list : [];
    } catch (_) {
      return [];
    }
  }

  function markDone(courseTitle, sessionKey) {
    const list = getDone(courseTitle);
    if (list.indexOf(sessionKey) === -1) {
      list.push(sessionKey);
      GM_setValue(resumeKey(courseTitle), JSON.stringify(list));
    }
  }

  function clearDone(courseTitle) {
    GM_setValue(resumeKey(courseTitle), '[]');
  }

  const state = {
    running: false,
    starting: false,
    aborted: false,
    current: null,
    currentAsr: 0,
    currentSlides: 0,
    total: 0,
    done: 0,
    failed: 0,
    errors: [],
    exports: [],
    completedKeys: [],
    slideFiles: {},
    phase: '空闲',
    scopeLabel: null,
    lastSave: null,
    directoryHandle: null,
    refreshTimer: null,
    showLog: false,
  };

  // ======================================================================
  // 11. UI panel
  // ======================================================================

  const CSS = `
  #sce-panel{position:fixed;right:16px;bottom:16px;z-index:2147483000;width:336px;
    font:13px/1.55 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;color:#1f2933;
    background:#fff;border:1px solid rgba(0,0,0,.12);border-radius:12px;
    box-shadow:0 8px 28px rgba(0,0,0,.16);overflow:hidden;display:flex;flex-direction:column;
    max-height:calc(100vh - 32px)}
  #sce-panel.sce-collapsed #sce-body{display:none}
  #sce-head{display:flex;align-items:center;justify-content:space-between;gap:8px;
    padding:9px 12px;background:#374A59;color:#fff;cursor:move;user-select:none;flex:0 0 auto}
  #sce-head .sce-title{font-weight:600;font-size:13px;letter-spacing:.2px}
  #sce-head .sce-head-actions{display:flex;gap:2px}
  .sce-icon-btn{background:none;border:0;color:#fff;opacity:.8;cursor:pointer;font-size:14px;
    line-height:1;padding:2px 6px;border-radius:4px}
  .sce-icon-btn:hover{opacity:1;background:rgba(255,255,255,.14)}
  #sce-body{padding:0;overflow-y:auto;flex:1 1 auto}

  .sce-course{padding:11px 14px 10px;background:#f7f9fa;border-bottom:1px solid #e6eaed}
  .sce-course-title{font-size:15px;font-weight:600;color:#1f2933;word-break:break-all}
  .sce-course-sub{margin-top:2px;font-size:12px;color:#7b8794;display:flex;flex-wrap:wrap;gap:5px}
  .sce-course-sub b{color:#3e4c59;font-weight:600}
  .sce-current-row{margin-top:5px;font-size:12px;color:#52606d;word-break:break-all}
  .sce-current-row.empty{color:#9aa5b1}

  .sce-section{padding:11px 14px;border-bottom:1px solid #eef1f3}
  .sce-section:last-child{border-bottom:0}
  .sce-label{font-size:11px;font-weight:600;color:#7b8794;letter-spacing:.6px;
    text-transform:none;margin-bottom:7px}

  .sce-seg{display:flex;background:#eef1f3;border-radius:8px;padding:2px;gap:2px}
  .sce-seg label{flex:1;text-align:center;cursor:pointer}
  .sce-seg input{position:absolute;opacity:0;width:0;height:0}
  .sce-seg span{display:block;padding:5px 2px;font-size:12px;color:#52606d;border-radius:6px;
    transition:background .12s,color .12s;white-space:nowrap}
  .sce-seg input:checked+span{background:#fff;color:#1f2933;font-weight:600;
    box-shadow:0 1px 2px rgba(0,0,0,.12)}
  .sce-seg input:focus-visible+span{outline:2px solid #374A59;outline-offset:1px}

  .sce-checks{display:grid;grid-template-columns:1fr 1fr;gap:6px 10px}
  .sce-checks label,.sce-sub-checks label{display:flex;align-items:center;gap:6px;
    font-size:12px;color:#3e4c59;cursor:pointer}
  .sce-checks input,.sce-sub-checks input{margin:0;accent-color:#374A59;flex:0 0 auto}

  .sce-radios{display:flex;flex-direction:column;gap:6px}
  .sce-radios label{display:flex;align-items:flex-start;gap:7px;font-size:12px;color:#3e4c59;
    cursor:pointer}
  .sce-radios input{margin:2px 0 0;accent-color:#374A59;flex:0 0 auto}
  .sce-radios em{font-style:normal;font-size:10px;color:#7b8794;border:1px solid #d3d9de;
    border-radius:3px;padding:0 4px;margin-left:5px;vertical-align:1px}
  .sce-sub-checks{margin-top:9px;padding-top:9px;border-top:1px dashed #e6eaed;
    display:flex;flex-direction:column;gap:6px}

  .sce-save-row{display:flex;align-items:center;gap:8px}
  .sce-btn{background:#f5f7fa;border:1px solid #cbd2d9;color:#1f2933;border-radius:7px;
    padding:5px 10px;font-size:12px;cursor:pointer;white-space:nowrap}
  .sce-btn:hover{background:#e9edf1;border-color:#9aa5b1}
  .sce-save-path{font-size:12px;color:#3e4c59;word-break:break-all;font-weight:500}
  .sce-save-path.unset{color:#9aa5b1;font-weight:400}
  .sce-note{margin-top:7px;font-size:11px;line-height:1.5;color:#7b8794}

  .sce-actions{padding:12px 14px;display:flex;gap:8px}
  .sce-primary{flex:1;padding:9px 12px;font-size:13px;font-weight:600;cursor:pointer;
    border-radius:8px;border:1px solid #374A59;background:#374A59;color:#fff}
  .sce-primary:hover:not(:disabled){background:#2b3a46;border-color:#2b3a46}
  .sce-primary:disabled{opacity:.5;cursor:not-allowed}
  .sce-danger{flex:0 0 84px;padding:9px 12px;font-size:13px;font-weight:600;cursor:pointer;
    border-radius:8px;border:1px solid #e12d39;background:#fff;color:#e12d39}
  .sce-danger:hover{background:#fdf0f0}
  .sce-danger[hidden]{display:none}

  .sce-prog-scope{font-size:12px;color:#52606d;margin-bottom:5px}
  .sce-prog-scope b{color:#1f2933;font-weight:600}
  .sce-prog-line{display:flex;justify-content:space-between;font-size:12px;color:#3e4c59}
  .sce-prog-line #sce-phase{font-weight:600}
  .sce-bar{height:6px;background:#e4e7eb;border-radius:3px;overflow:hidden;margin:7px 0 9px}
  .sce-bar>i{display:block;height:100%;width:0;background:#374A59;transition:width .2s}
  .sce-prog-grid{display:grid;grid-template-columns:auto 1fr auto 1fr;gap:3px 10px;
    font-size:12px;color:#52606d}
  .sce-prog-grid .k{color:#9aa5b1}
  .sce-prog-grid .v{color:#1f2933;font-weight:500;word-break:break-all}
  .sce-link{margin-top:10px;background:none;border:0;padding:0;color:#52606d;font-size:12px;
    cursor:pointer;text-decoration:underline;text-underline-offset:2px}
  .sce-link:hover{color:#1f2933}
  .sce-log{margin-top:8px;max-height:150px;overflow:auto;background:#f7f9fa;border-radius:6px;
    padding:6px 8px;font:11px/1.55 ui-monospace,Consolas,monospace;color:#3e4c59;
    white-space:pre-wrap;word-break:break-all}
  .sce-log .err{color:#c0392b}
  .sce-log .ok{color:#1b7f4b}
  .sce-log[hidden]{display:none}

  .sce-collapse{background:none;border:0;padding:0;color:#52606d;font-size:12px;font-weight:600;
    cursor:pointer}
  .sce-collapse:hover{color:#1f2933}
  .sce-adv{margin-top:10px;display:flex;flex-direction:column;gap:7px}
  .sce-adv[hidden]{display:none}
  .sce-adv-row{display:flex;align-items:center;justify-content:space-between;gap:8px;
    font-size:12px;color:#3e4c59}
  .sce-adv-row input[type=number]{width:78px;padding:3px 6px;border:1px solid #cbd2d9;
    border-radius:5px;font-size:12px}
  .sce-adv-row input[type=checkbox]{margin:0;accent-color:#374A59}
  .sce-hint{padding:9px 14px;font-size:11px;color:#7b8794;background:#fbfcfd;
    border-top:1px solid #eef1f3;line-height:1.55}
  `;

  let ui = null;

  function buildPanel() {
    const style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    const panel = document.createElement('div');
    panel.id = 'sce-panel';
    panel.innerHTML = `
      <div id="sce-head">
        <span class="sce-title">SJTU 课程导出</span>
        <span class="sce-head-actions">
          <button class="sce-icon-btn" id="sce-advanced-gear" title="高级设置">⚙</button>
          <button class="sce-icon-btn" id="sce-min" title="折叠 / 展开">–</button>
        </span>
      </div>
      <div id="sce-body">
        <div class="sce-course">
          <div class="sce-course-title" id="sce-course">读取中…</div>
          <div class="sce-course-sub">
            <span id="sce-teacher">-</span><span>·</span>
            <span>已发现 <b id="sce-found">0</b> 个课次</span>
          </div>
          <div class="sce-current-row empty" id="sce-current">当前：-</div>
        </div>

        <div class="sce-section">
          <div class="sce-label">导出范围</div>
          <div class="sce-seg" id="sce-scope">
            <label><input type="radio" name="sce-scope" value="current" checked><span>当前节</span></label>
            <label><input type="radio" name="sce-scope" value="group"><span>当前课次组</span></label>
            <label><input type="radio" name="sce-scope" value="course"><span>整门课程</span></label>
          </div>
        </div>

        <div class="sce-section">
          <div class="sce-label">导出内容</div>
          <div class="sce-checks">
            <label><input type="checkbox" id="sce-content-transcript" checked><span>语音转写</span></label>
            <label><input type="checkbox" id="sce-content-slides" checked><span>PPT 图片</span></label>
            <label><input type="checkbox" id="sce-content-summary" checked><span>平台概要</span></label>
            <label><input type="checkbox" id="sce-content-chapters" checked><span>章节导航</span></label>
          </div>
        </div>

        <div class="sce-section">
          <div class="sce-label">输出格式</div>
          <div class="sce-radios">
            <label><input type="radio" name="sce-format" value="pack" checked>
              <span>AI 资料包<em>推荐</em></span></label>
            <label><input type="radio" name="sce-format" value="manifest">
              <span>原始 Manifest JSON<em>开发者</em></span></label>
          </div>
          <div class="sce-sub-checks">
            <label><input type="checkbox" id="sce-want-srt"><span>同时生成 SRT 字幕</span></label>
            <label><input type="checkbox" id="sce-keep-manifest" checked>
              <span>同时保留 manifest.json（Python 工具需要）</span></label>
          </div>
        </div>

        <div class="sce-section">
          <div class="sce-label">保存位置</div>
          <div class="sce-save-row">
            <button class="sce-btn" id="sce-pick-folder">选择保存文件夹</button>
            <div class="sce-save-path unset" id="sce-save-path">未选择</div>
          </div>
          <div class="sce-radios" id="sce-save-mode" style="margin-top:9px">
            <label><input type="radio" name="sce-savemode" value="folder"><span>保存到文件夹</span></label>
            <label><input type="radio" name="sce-savemode" value="zip"><span>下载单个 ZIP</span></label>
          </div>
          <div class="sce-note" id="sce-save-note"></div>
        </div>

        <div class="sce-actions">
          <button class="sce-primary" id="sce-start-btn">开始导出</button>
          <button class="sce-danger" id="sce-stop-btn" hidden>停止</button>
        </div>

        <div class="sce-section" id="sce-progress-box" hidden>
          <div class="sce-label">进度</div>
          <div class="sce-prog-scope">范围：<b id="sce-scope-shown">-</b></div>
          <div class="sce-prog-line">
            <span id="sce-phase">空闲</span>
            <span id="sce-progress-text">0 / 0 课次</span>
          </div>
          <div class="sce-bar"><i id="sce-progress"></i></div>
          <div class="sce-prog-grid">
            <span class="k">当前</span><span class="v" id="sce-processing">-</span>
            <span class="k">ASR</span><span class="v"><span id="sce-current-asr">0</span> 条</span>
            <span class="k">PPT</span><span class="v"><span id="sce-current-slides">0</span> 帧</span>
            <span class="k">成功</span><span class="v" id="sce-success">0</span>
            <span class="k">失败</span><span class="v" id="sce-fail">0</span>
          </div>
          <button class="sce-link" id="sce-log-toggle">查看详细日志</button>
          <div class="sce-log" id="sce-log" hidden></div>
        </div>

        <div class="sce-section">
          <button class="sce-collapse" id="sce-advanced-toggle">高级设置 ▾</button>
          <div class="sce-adv" id="sce-advanced" hidden>
            <label class="sce-adv-row">课次切换静默期(ms)
              <input type="number" id="set-quiet" min="200" step="100"></label>
            <label class="sce-adv-row">单课次超时(ms)
              <input type="number" id="set-timeout" min="5000" step="1000"></label>
            <label class="sce-adv-row">课次间隔(ms)
              <input type="number" id="set-between" min="0" step="100"></label>
            <label class="sce-adv-row">失败重试次数
              <input type="number" id="set-retries" min="0" max="5" step="1"></label>
            <label class="sce-adv-row">断点续跑（跳过已完成的课次）
              <input type="checkbox" id="set-resume"></label>
            <button class="sce-btn" id="sce-clear-btn" style="align-self:flex-start">清除断点数据</button>
          </div>
        </div>

        <div class="sce-hint">仅导出你本人有权访问的内容 · 不下载视频 · 不读取 Cookie</div>
      </div>`;
    document.body.appendChild(panel);

    // Draggable by its header so it never traps the player controls.
    (function makeDraggable() {
      const head = panel.querySelector('#sce-head');
      let dragging = false;
      let offsetX = 0;
      let offsetY = 0;
      head.addEventListener('mousedown', (e) => {
        if (e.target.classList.contains('sce-icon-btn')) return;
        dragging = true;
        const rect = panel.getBoundingClientRect();
        offsetX = e.clientX - rect.left;
        offsetY = e.clientY - rect.top;
        e.preventDefault();
      });
      window.addEventListener('mousemove', (e) => {
        if (!dragging) return;
        panel.style.left = Math.max(0, e.clientX - offsetX) + 'px';
        panel.style.top = Math.max(0, e.clientY - offsetY) + 'px';
        panel.style.right = 'auto';
        panel.style.bottom = 'auto';
      });
      window.addEventListener('mouseup', () => { dragging = false; });
    })();

    const $ = (sel) => panel.querySelector(sel);

    function log(message, kind) {
      const el = $('#sce-log');
      if (!el) return;
      const cls = kind === 'error' ? 'err' : kind === 'ok' ? 'ok' : '';
      const time = new Date().toLocaleTimeString();
      const line = document.createElement('div');
      if (cls) line.className = cls;
      line.textContent = `[${time}] ${String(message)}`;
      el.appendChild(line);
      el.scrollTop = el.scrollHeight;
      if (kind === 'error') {
        state.showLog = true;
        $('#sce-progress-box').hidden = false;
        el.hidden = false;
        $('#sce-log-toggle').textContent = '收起详细日志';
      }
    }

    function refresh() {
      try {
        $('#sce-course').textContent = getCourseTitle() || '未识别到课程';
        $('#sce-teacher').textContent = getTeacher() || '未知教师';
        const refs = discoverSessions();
        const current = refs.find((r) => r.isCurrent) || refs[0];
        const currentEl = $('#sce-current');
        if (current) {
          currentEl.textContent = `当前：${[current.title, current.date && `(${current.date})`].filter(Boolean).join(' ')}`;
          currentEl.classList.remove('empty');
        } else {
          currentEl.textContent = '当前：未发现课次';
          currentEl.classList.add('empty');
        }
        $('#sce-found').textContent = String(refs.length);

        $('#sce-phase').textContent = state.phase;
        $('#sce-scope-shown').textContent = state.scopeLabel || '-';
        $('#sce-success').textContent = String(state.done);
        $('#sce-fail').textContent = String(state.failed);
        $('#sce-processing').textContent = state.current || '-';
        $('#sce-current-asr').textContent = String(state.currentAsr);
        $('#sce-current-slides').textContent = String(state.currentSlides);
        const finished = state.done + state.failed;
        const pct = state.total ? Math.round((finished / state.total) * 100) : 0;
        $('#sce-progress').style.width = pct + '%';
        $('#sce-progress-text').textContent = `${finished} / ${state.total || 0} 课次`;

        $('#sce-progress-box').hidden = !(state.running || state.starting || state.total > 0 || state.showLog);
        $('#sce-start-btn').disabled = state.running || state.starting;
        $('#sce-start-btn').textContent = state.running ? '正在导出…' : state.starting ? '正在准备…' : '开始导出';
        $('#sce-stop-btn').hidden = !state.running;
      } catch (err) {
        // A DOM surprise must never throw out of the panel.
        // eslint-disable-next-line no-console
        console.warn('[SJTU Course Exporter] refresh failed:', err);
      }
    }

    function syncAdvancedInputs() {
      $('#set-quiet').value = settings.quietMs;
      $('#set-timeout').value = settings.sessionTimeout;
      $('#set-between').value = settings.betweenSessionsMs;
      $('#set-retries').value = settings.maxRetries;
      $('#set-resume').checked = !!settings.resume;
    }

    function setAdvancedVisible(visible) {
      $('#sce-advanced').hidden = !visible;
      $('#sce-advanced-toggle').textContent = visible ? '高级设置 ▴' : '高级设置 ▾';
      if (visible) syncAdvancedInputs();
    }

    function refreshSaveControls() {
      const fsa = supportsDirectoryPicker();
      const folderRadio = panel.querySelector('input[name="sce-savemode"][value="folder"]');
      const folderLabel = folderRadio ? folderRadio.closest('label') : null;
      if (folderLabel) folderLabel.hidden = !fsa;
      if (!panel.querySelector('input[name="sce-savemode"]:checked')) {
        setChecked('sce-savemode', fsa ? SAVE_MODE.FOLDER : SAVE_MODE.ZIP);
      }

      const pathEl = $('#sce-save-path');
      if (state.directoryHandle) {
        pathEl.textContent = state.directoryHandle.name || '已选择的文件夹';
        pathEl.classList.remove('unset');
      } else {
        pathEl.textContent = fsa ? '未选择' : '此浏览器不支持文件夹保存';
        pathEl.classList.add('unset');
      }
      $('#sce-pick-folder').hidden = !fsa;

      // Keep the selected save mode valid for this browser.
      const mode = readChecked('sce-savemode', fsa ? SAVE_MODE.FOLDER : SAVE_MODE.ZIP);
      if (mode === SAVE_MODE.FOLDER && !fsa) {
        setChecked('sce-savemode', SAVE_MODE.ZIP);
      }
      const note = $('#sce-save-note');
      const effective = readChecked('sce-savemode', SAVE_MODE.ZIP);
      if (!fsa) {
        note.textContent = '当前浏览器不支持 File System Access API，将以单个 ZIP 文件下载（不会把上百个文件散落到下载目录）。';
      } else if (effective === SAVE_MODE.FOLDER) {
        note.textContent = '浏览器出于安全考虑不会把完整路径告诉网页，这里只显示文件夹名称；请记住你选择的目录。';
      } else {
        note.textContent = '整门课程会打包成一个 ZIP 文件下载，解压后就是完整的目录结构。';
      }
    }

    ui = { panel, log, refresh, syncAdvancedInputs, setAdvancedVisible, refreshSaveControls, $ };

    function readChecked(name, fallback) {
      const el = panel.querySelector(`input[name="${name}"]:checked`);
      return el ? el.value : fallback;
    }
    function setChecked(name, value) {
      const el = panel.querySelector(`input[name="${name}"][value="${value}"]`);
      if (el) el.checked = true;
    }
    ui.readChecked = readChecked;
    ui.setChecked = setChecked;

    // ---- wiring --------------------------------------------------------
    $('#sce-min').addEventListener('click', () => panel.classList.toggle('sce-collapsed'));

    $('#sce-advanced-toggle').addEventListener('click', () => {
      setAdvancedVisible($('#sce-advanced').hidden);
    });
    $('#sce-advanced-gear').addEventListener('click', () => {
      setAdvancedVisible($('#sce-advanced').hidden);
      if (!$('#sce-advanced').hidden) $('#sce-advanced').scrollIntoView({ block: 'nearest' });
    });

    $('#sce-log-toggle').addEventListener('click', () => {
      const el = $('#sce-log');
      el.hidden = !el.hidden;
      $('#sce-log-toggle').textContent = el.hidden ? '查看详细日志' : '收起详细日志';
    });

    $('#sce-start-btn').addEventListener('click', () => run());
    $('#sce-stop-btn').addEventListener('click', () => {
      state.aborted = true;
      state.phase = '正在停止…';
      log('用户请求停止，将在当前课次结束后中止。');
      refresh();
    });

    $('#sce-pick-folder').addEventListener('click', () => pickDirectory());

    panel.querySelectorAll('input[name="sce-savemode"]').forEach((el) => {
      el.addEventListener('change', refreshSaveControls);
    });
    panel.querySelectorAll('input[type=radio]').forEach((el) => {
      el.addEventListener('change', () => { if (!state.running) refresh(); });
    });

    $('#sce-clear-btn').addEventListener('click', () => {
      const course = getCourseTitle() || '';
      if (!askConfirm(`确定要清除「${course || '本课程'}」的断点记录吗？\n清除后下次会重新导出所有课次。`)) {
        return;
      }
      clearDone(course);
      log('已清除本课程的断点记录。', 'ok');
    });

    ['set-quiet', 'set-timeout', 'set-between', 'set-retries', 'set-resume'].forEach((id) => {
      const el = panel.querySelector(`#${id}`);
      if (!el) return;
      el.addEventListener('change', () => {
        settings.quietMs = Math.max(200, parseInt($('#set-quiet').value, 10) || DEFAULTS.quietMs);
        settings.sessionTimeout = Math.max(5000, parseInt($('#set-timeout').value, 10) || DEFAULTS.sessionTimeout);
        settings.betweenSessionsMs = Math.max(0, parseInt($('#set-between').value, 10) || 0);
        settings.maxRetries = Math.min(5, Math.max(0, parseInt($('#set-retries').value, 10) || 0));
        settings.resume = $('#set-resume').checked;
        saveSettings();
      });
    });

    syncAdvancedInputs();
    refreshSaveControls();
    refresh();
  }

  function askConfirm(message) {
    try {
      if (typeof window.confirm === 'function') return !!window.confirm(message);
    } catch (_) {
      /* ignore: fall through to allowing the action */
    }
    return true;
  }

  /** Let the user grant access to an output directory and remember the handle. */
  async function pickDirectory() {
    if (!supportsDirectoryPicker()) return null;
    try {
      const handle = await window.showDirectoryPicker({
        id: 'sjtu-course-exporter',
        mode: 'readwrite',
        startIn: 'documents',
      });
      state.directoryHandle = handle;
      await handleStore.save(handle);
      ui.setChecked('sce-savemode', SAVE_MODE.FOLDER);
      ui.refreshSaveControls();
      ui.log(`保存位置已设为「${handle.name}」。`, 'ok');
      return handle;
    } catch (err) {
      // AbortError simply means the user closed the picker.
      if (err && err.name !== 'AbortError') {
        ui.log(`选择文件夹失败：${err.message}`, 'error');
      }
      return null;
    }
  }

  /** Restore a previously chosen directory and re-check its permission. */
  async function restoreDirectory() {
    if (!supportsDirectoryPicker()) return;
    const handle = await handleStore.load();
    if (!handle) return;
    state.directoryHandle = handle;
    const permission = await directoryPermission(handle);
    if (permission === 'granted') {
      ui.log(`已恢复保存位置「${handle.name}」。`, 'ok');
    } else {
      ui.log(`保存位置「${handle.name}」需要重新授权，导出时会再次询问。`);
    }
  }

  function safeRefresh() {
    try {
      if (ui && ui.refresh) ui.refresh();
    } catch (_) {
      /* the panel going stale is acceptable; a thrown error is not */
    }
  }

  // ======================================================================
  // 12. Orchestration
  // ======================================================================

  /** Read every user choice out of the panel. */
  function readOptions() {
    const advanced = {
      quietMs: settings.quietMs,
      sessionTimeout: settings.sessionTimeout,
      betweenSessionsMs: settings.betweenSessionsMs,
      maxRetries: settings.maxRetries,
      resume: settings.resume,
    };
    return {
      scope: ui.readChecked('sce-scope', 'current'),
      content: {
        transcript: ui.$('#sce-content-transcript').checked,
        slides: ui.$('#sce-content-slides').checked,
        summary: ui.$('#sce-content-summary').checked,
        chapters: ui.$('#sce-content-chapters').checked,
      },
      format: ui.readChecked('sce-format', FORMAT.PACK),
      wantSrt: ui.$('#sce-want-srt').checked,
      keepManifest: ui.$('#sce-keep-manifest').checked,
      saveMode: ui.readChecked('sce-savemode', supportsDirectoryPicker() ? SAVE_MODE.FOLDER : SAVE_MODE.ZIP),
      directoryHandle: state.directoryHandle,
      advanced,
    };
  }

  function collectTargets(scope) {
    const refs = discoverSessions();
    if (!refs.length) return [];
    if (scope === 'current') {
      const current = refs.find((r) => r.isCurrent) || refs[0];
      return [current];
    }
    if (scope === 'group') {
      const current = refs.find((r) => r.isCurrent) || refs[0];
      // "课次组" == the sessions that share the current session's date.
      return refs.filter((r) => r.date && current.date && r.date === current.date);
    }
    return refs;
  }

  async function captureOne(ref) {
    const courseTitle = getCourseTitle();
    const key = sessionId(ref);

    if (ref.isCurrent) {
      // Already displayed: no navigation needed.
      return await buildSessionExport(ref, courseTitle);
    }

    const before = contentSignature();
    clickSession(ref);

    // Wait for the clicked row to become selected, then for the content to
    // settle. Both are conditional; neither is a fixed sleep.
    await waitUntil(
      () => ref.element && (ref.element.classList.contains('selected') || ref.element.classList.contains('active')),
      { timeout: settings.sessionTimeout, interval: 200, label: `session ${key} selection` },
    ).catch(() => { /* the signature check below is the real gate */ });

    await waitForSessionChange(before, {
      timeout: settings.sessionTimeout,
      quietMs: settings.quietMs,
    });

    // Final safety guard. Waiting for a change is necessary but not sufficient:
    // the transcript panel could in principle refresh for an unrelated reason.
    // Never attach the on-screen transcript to a session id unless the page
    // agrees that this is the selected session. Exporting the wrong lecture
    // under the right name is far worse than reporting a failure.
    const landedOn = selectedSessionKey();
    if (landedOn && landedOn !== key) {
      throw new Error(
        `session switch landed on "${landedOn}" but "${key}" was requested — refusing to export mismatched content`,
      );
    }

    // Re-resolve the element: the SPA may have replaced the list nodes.
    const fresh = discoverSessions().find((r) => sessionId(r) === key);
    const target = fresh || ref;
    return await buildSessionExport(target, courseTitle);
  }

  /**
   * Fetch the PPT frames as raw bytes so they can be written into the chosen
   * folder (or into the ZIP). Nothing is written to the browser's Downloads
   * folder here — that decision belongs to savePlan().
   */
  async function fetchSlideFiles(session) {
    const files = [];
    const missing = [];
    const slides = session.slides || [];
    for (let i = 0; i < slides.length; i++) {
      const slide = slides[i];
      if (slide.url) {
        try {
          const response = await fetch(slide.url, { credentials: 'same-origin' });
          if (!response.ok) throw new Error('HTTP ' + response.status);
          const blob = await response.blob();
          const buffer = await blob.arrayBuffer();
          files.push({
            index: slide.index,
            seconds: slide.seconds,
            mime: blob.type || 'image/jpeg',
            ext: extensionForMime(blob.type, slide.url),
            bytes: new Uint8Array(buffer),
          });
        } catch (err) {
          missing.push(`${slide.index}（${err.message}）`);
        }
      } else {
        missing.push(`${slide.index}（无图片地址）`);
      }
      state.currentSlides = i + 1;
      safeRefresh();
      // Be gentle with the platform: sequential, small gap.
      await sleep(180);
    }
    if (missing.length) {
      state.errors.push(
        `${session.section || session.title || session.id}：PPT 图片缺失 ${missing.length}/${slides.length} 张：${missing.join('、')}`,
      );
    }
    return { files, missing };
  }

  function buildManifest(course, refs, sessions, options, scopeLabel) {
    return {
      schema_version: SCHEMA_VERSION,
      platform: PLATFORM,
      exporter: { name: 'sjtu-course-exporter-userscript', version: VERSION },
      exported_at: new Date().toISOString(),
      source: {
        kind: 'live_dom',
        name: location.origin,
        capture_scope:
          scopeLabel === 'course' ? 'whole_course' : scopeLabel === 'group' ? 'session_group' : 'single_session',
      },
      export_options: {
        scope: scopeLabel,
        content: Object.assign({}, options.content),
        format: options.format,
        include_srt: !!options.wantSrt,
        keep_manifest: !!options.keepManifest,
      },
      course,
      discovered_sessions: refs.map((r) => ({
        id: sessionId(r),
        id_source: 'synthetic',
        platform_id: null,
        order: r.order,
        title: r.title,
        date: r.date,
        start_time: r.startTime,
        week: r.week,
        weekday: r.weekday,
        section: r.section,
        section_number: r.sectionNumber,
        room: r.room,
        has_platform_ai: r.hasAi,
        is_current_in_snapshot: r.isCurrent,
      })),
      discovered_session_count: refs.length,
      sessions,
      extraction: {
        method: 'dom',
        notes: 'Extracted in-browser. Video intentionally not captured.',
      },
      warnings: state.errors.slice(),
    };
  }

  const SCOPE_LABELS = { current: '当前节', group: '当前课次组', course: '整门课程' };

  async function run() {
    if (state.running || state.starting) return;

    const options = readOptions();
    const scopeLabel = SCOPE_LABELS[options.scope] || options.scope;

    // A restored folder handle may still be loading when the user clicks.
    state.starting = true;
    safeRefresh();
    try {
      if (options.saveMode === SAVE_MODE.FOLDER && !state.directoryHandle) {
        try {
          await restoreDirectory();
        } catch (err) {
          ui.log(`恢复保存位置失败：${err.message}`, 'error');
        }
      }
    } finally {
      state.starting = false;
      safeRefresh();
    }

    // Validate before touching the page.
    if (options.saveMode === SAVE_MODE.FOLDER && !state.directoryHandle) {
      ui.log('请先点击「选择保存文件夹」，然后再开始导出。', 'error');
      return;
    }
    if (options.format === FORMAT.PACK && !options.content.transcript &&
        !options.content.slides && !options.content.summary && !options.content.chapters) {
      ui.log('请至少勾选一项“导出内容”。', 'error');
      return;
    }

    // Persist the advanced knobs the user may have changed.
    settings.quietMs = readInt('#set-quiet', settings.quietMs, 200);
    settings.sessionTimeout = readInt('#set-timeout', settings.sessionTimeout, 5000);
    settings.betweenSessionsMs = readInt('#set-between', settings.betweenSessionsMs, 0);
    settings.maxRetries = Math.min(5, readInt('#set-retries', settings.maxRetries, 0));
    settings.resume = ui.$('#set-resume').checked;
    saveSettings();
    options.advanced = {
      quietMs: settings.quietMs,
      sessionTimeout: settings.sessionTimeout,
      betweenSessionsMs: settings.betweenSessionsMs,
      maxRetries: settings.maxRetries,
      resume: settings.resume,
    };

    state.running = true;
    state.aborted = false;
    state.done = 0;
    state.failed = 0;
    state.currentAsr = 0;
    state.currentSlides = 0;
    state.errors = [];
    state.exports = [];
    state.completedKeys = [];
    state.slideFiles = {};
    state.current = null;
    state.lastSave = null;
    state.showLog = false;

    const courseTitle = getCourseTitle() || 'sjtu-course';
    const stamp = timestampForFilename();
    const base = safeFilename(courseTitle, 'sjtu-course');

    try {
      const targets = collectTargets(options.scope);
      if (!targets.length) {
        ui.log('未发现课次。请确认已打开课程播放页，并且“节次”列表已加载。', 'error');
        return;
      }

      // Resume only makes sense for a multi-session sweep; re-exporting the
      // lecture you are looking at must always work.
      const useResume = settings.resume && options.scope !== 'current';
      const alreadyDone = useResume ? getDone(courseTitle) : [];
      let pending = targets.filter((t) => alreadyDone.indexOf(sessionId(t)) === -1);
      let skipped = targets.length - pending.length;
      if (!pending.length) {
        // Resume is for an interrupted sweep. An explicit new click after a
        // completed sweep should start a fresh export, not silently do nothing.
        pending = targets;
        skipped = 0;
        ui.log('此前已完成全部课次，本次重新导出。');
      }

      state.total = pending.length;
      state.phase = '导出中';
      state.scopeLabel = scopeLabel;
      ui.log(
        `范围：${scopeLabel} · 目标 ${targets.length} 个课次` +
          (skipped ? `，已完成 ${skipped} 个（断点续跑跳过）` : ''),
      );
      safeRefresh();

      for (let i = 0; i < pending.length; i++) {
        if (state.aborted) {
          ui.log('已按用户请求中止。', 'error');
          break;
        }
        const ref = pending[i];
        const key = sessionId(ref);
        state.current = ref.title || key;
        state.currentAsr = 0;
        state.currentSlides = 0;
        safeRefresh();
        ui.log(`[${i + 1}/${pending.length}] 处理 ${state.current}`);

        let attempt = 0;
        let payload = null;
        while (attempt <= settings.maxRetries && payload == null) {
          if (state.aborted) break;
          try {
            payload = await captureOne(ref);
          } catch (err) {
            attempt += 1;
            if (attempt > settings.maxRetries) {
              state.failed += 1;
              state.errors.push(`${key}: ${err.message}`);
              ui.log(`失败：${key} — ${err.message}`, 'error');
            } else {
              ui.log(`重试 ${attempt}/${settings.maxRetries}：${key}`, 'error');
              await sleep(600 * attempt);
            }
          }
        }

        if (payload) {
          const kept = applyContentOptions(payload, options.content);
          state.currentAsr = kept.transcript.length;
          state.currentSlides = kept.slides.length;
          let slidesComplete = true;

          if (options.content.slides && kept.slides.length) {
            const fetched = await fetchSlideFiles(kept);
            state.slideFiles[kept.id] = fetched.files;
            slidesComplete = fetched.missing.length === 0;
          }

          state.exports.push(kept);
          if (slidesComplete) state.completedKeys.push(key);
          state.done += 1;
          const imageStatus = options.content.slides
            ? `，PPT 图片 ${(state.slideFiles[kept.id] || []).length}/${kept.slides.length} 张`
            : '';
          ui.log(
            `${slidesComplete ? '成功' : '部分完成'}：${key}（ASR ${kept.transcript.length} 条${imageStatus}）`,
            slidesComplete ? 'ok' : 'error',
          );
        }
        safeRefresh();
        if (i + 1 < pending.length) await sleep(settings.betweenSessionsMs);
      }

      // ---- assemble and save --------------------------------------------
      const course = { id: null, title: getCourseTitle(), teacher: getTeacher() };
      const refs = discoverSessions();
      const manifest = buildManifest(course, refs, state.exports, options, options.scope);

      const plan = planOutput(manifest, {
        scope: options.scope,
        content: options.content,
        format: options.format,
        wantSrt: options.wantSrt,
        keepManifest: options.keepManifest || options.format === FORMAT.MANIFEST,
        slideFiles: state.slideFiles,
        exportedAt: manifest.exported_at,
      });

      ui.log(`正在保存 ${plan.files.length} 个文件…`);

      const result = await savePlan(plan, {
        saveMode: options.saveMode,
        directoryHandle: state.directoryHandle,
        zipName: `${base}_${stamp}.zip`,
        onFile: (n, total, path) => {
          state.lastSave = `${n}/${total}`;
          if (n === total || n % 5 === 0) safeRefresh();
          if (n % 10 === 0) ui.log(`保存中 ${n}/${total}：${path}`);
        },
      });

      // A session is resumable only after its output has actually been saved.
      for (const key of state.completedKeys) markDone(courseTitle, key);

      state.lastSave = result;
      ui.log(
        result.mode === SAVE_MODE.FOLDER
          ? `已保存 ${result.fileCount} 个文件到文件夹「${result.location}」。`
          : `已生成 ${result.location}（含 ${result.fileCount} 个文件）。`,
        'ok',
      );
      if (result.mode === SAVE_MODE.ZIP) {
        ui.log('提示：解压后即可得到完整目录结构，可直接交给 ChatGPT / NotebookLM。');
      }
      if (options.format === FORMAT.MANIFEST) {
        ui.log('已按“原始 Manifest JSON”格式导出。');
      } else {
        ui.log('下一步（可选）：用 Python CLI 重新整理 —— python -m cli.main pack manifest.json --out course_export');
      }

      state.phase = state.aborted ? '已中止' : state.failed || state.errors.length ? '完成（有缺失）' : '完成';
      if (state.failed) {
        ui.log(`${state.failed} 个课次失败。再次点击“开始导出”可断点续跑。`, 'error');
      }
      if (state.errors.length) {
        ui.log('错误明细：\n' + state.errors.join('\n'), 'error');
      }
    } catch (err) {
      state.phase = '出错';
      state.errors.push(String(err && err.message ? err.message : err));
      ui.log('发生错误：' + (err && err.message ? err.message : err), 'error');
    } finally {
      state.running = false;
      state.current = null;
      safeRefresh();
    }
  }

  function readInt(selector, fallback, minimum) {
    try {
      const el = ui.$(selector);
      if (!el) return fallback;
      const value = parseInt(el.value, 10);
      if (isNaN(value)) return fallback;
      return minimum != null && value < minimum ? fallback : value;
    } catch (_) {
      return fallback;
    }
  }

  // ======================================================================
  // 13. Boot
  // ======================================================================
  //
  // @match covers the whole site, but the panel must only ever touch a player
  // page. Injecting anywhere else is wasted work at best and, as this script
  // learned the hard way, a way to freeze the course list or the SSO page.
  //
  // This is the platform's own list of player-ish routes, read from its
  // App.vue route set (see docs/reverse-engineering-notes.md section 10).
  const PLAYER_ROUTES = new Set([
    '/play-video',
    '/play-live',
    '/play-center',
    '/video-detail',
    '/live-detail',
    '/vod-share',
    '/live-share',
    '/lms/launch',
    '/course-record-empty',
  ]);

  function currentRoute() {
    const hash = location.hash || '';
    const match = hash.match(/^#(\/[^?]*)/);
    return match ? match[1] : '';
  }

  function isPlayerPage() {
    if (PLAYER_ROUTES.has(currentRoute())) return true;
    // Fallback for a route rename: does a player actually exist on the page?
    try {
      return !!(q(document, SEL.transcriptWrapper) || q(document, SEL.video));
    } catch (_) {
      return false;
    }
  }

  let menuRegistered = false;

  /** Build the panel the first time we see a player page. */
  function ensurePanel() {
    if (!document.body) return false;
    if (document.getElementById('sce-panel')) return true;
    if (!isPlayerPage()) return false;
    buildPanel();
    return true;
  }

  function onRouteChange() {
    try {
      if (ensurePanel() && !menuRegistered) {
        menuRegistered = true;
        GM_registerMenuCommand('SJTU Course Exporter：导出当前节', () => {
          ui.setChecked('sce-scope', 'current');
          run();
        });
        GM_registerMenuCommand('SJTU Course Exporter：导出整门课程', () => {
          ui.setChecked('sce-scope', 'course');
          run();
        });
        ui.log('已加载。请确认“节次”列表已出现，然后选择范围并点击“开始导出”。');
        // Restore a previously granted output folder, if any.
        restoreDirectory().catch(() => {});
      }
      const panel = document.getElementById('sce-panel');
      if (panel) panel.style.display = isPlayerPage() ? '' : 'none';
      if (!state.running) safeRefresh();
    } catch (err) {
      // Never let boot logic break the host page.
      // eslint-disable-next-line no-console
      console.warn('[SJTU Course Exporter] boot failed:', err);
    }
  }

  function boot() {
    // The SPA may still be mounting; retry briefly rather than giving up.
    let attempts = 0;
    const tryBoot = () => {
      attempts += 1;
      onRouteChange();
      if (!document.getElementById('sce-panel') && attempts < 20) {
        setTimeout(tryBoot, 500);
      }
    };
    tryBoot();
  }

  // The panel only needs to react to navigation the user performs; these
  // listeners observe nothing and therefore cannot feed back.
  window.addEventListener('hashchange', onRouteChange);
  window.addEventListener('popstate', onRouteChange);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // ======================================================================
  // 14. Test seam
  // ======================================================================
  //
  // Pure output-layer helpers are exposed ONLY when the runtime test harness
  // opts in by setting the flag before evaluating this script. In a normal
  // browser this block does nothing at all.
  try {
    if (typeof globalThis !== 'undefined' && globalThis.__SCE_TEST_HOOK__) {
      globalThis.__SCE_TEST__ = {
        defaultContent,
        applyContentOptions,
        sessionFileStem,
        sessionFolderStem,
        courseFolderName,
        safeRelativePath,
        extensionForMime,
        planOutput,
        summarizePlan,
        buildZip,
        crc32,
        toUtf8,
        renderSessionMarkdown,
        renderSummaryMarkdown,
        renderFullCourseMarkdown,
        renderIndexMarkdown,
        renderAiPrompt,
        renderSrt,
        splitPath,
        SAVE_MODE,
        FORMAT,
        DEFAULTS,
      };
    }
  } catch (_) {
    /* never let the test seam affect a real page */
  }
})();
