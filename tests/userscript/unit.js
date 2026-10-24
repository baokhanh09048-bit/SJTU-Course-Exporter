/**
 * Unit tests for the pure output-layer helpers inside the userscript.
 *
 * These run in-process (no child process, no freeze detection needed) by
 * evaluating the real userscript with its gated test seam enabled:
 * `globalThis.__SCE_TEST_HOOK__ = true` before evaluating exposes the pure
 * helpers. In a normal browser that flag is never set and nothing is exposed.
 *
 * The ZIP archive is verified independently by Python's `zipfile` in
 * tests/test_userscript_runtime.py, which also validates every CRC.
 */

'use strict';

const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const SCRIPT = path.resolve(__dirname, '..', '..', 'userscript', 'sjtu-course-exporter.user.js');

function loadApi() {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'outside-only',
    // A non-player route keeps the panel (and its timers) out of the way.
    url: 'https://v.sjtu.edu.cn/jy-application-resourcemanage-ui/#/list-video',
  });
  const win = dom.window;
  win.__SCE_TEST_HOOK__ = true;
  win.GM_getValue = (key, def) => def;
  win.GM_setValue = () => {};
  win.GM_registerMenuCommand = () => {};
  if (typeof win.TextEncoder === 'undefined') win.TextEncoder = TextEncoder;
  win.eval(fs.readFileSync(SCRIPT, 'utf8'));
  const api = win.__SCE_TEST__;
  if (!api) throw new Error('the userscript did not expose its test seam');
  return api;
}

function sampleManifest() {
  return {
    schema_version: 1,
    platform: 'sjtu-classroom-replay',
    exported_at: '2026-03-04T00:00:00.000Z',
    course: { id: null, title: '示例课程', teacher: '示例教师' },
    discovered_session_count: 2,
    sessions: [
      {
        id: '2026-03-04-第1节-示例楼101-aaaa1111',
        week: 2,
        date: '2026-03-04',
        section: '第1节',
        title: '第2周 - 周三 第1节',
        room: '示例楼101',
        start_time: '08:00',
        transcript: [
          { index: 0, time: '00:05', seconds: 5, raw_text: '第一条。', normalized_text: '第一条。' },
          { index: 1, time: '01:02', seconds: 62, raw_text: '第二条。', normalized_text: '第二条。' },
        ],
        summary: {
          generated_by_platform_ai: true,
          keywords: ['关键词甲'],
          overview: '示例概要。',
          chapters: [{ index: 1, time: '00:10', seconds: 10, title: '章节一', summary: '摘要一。' }],
        },
        slides: [
          { index: 1, time: '00:10', seconds: 10, url: 'https://example.invalid/a.jpg', keywords: ['甲'] },
          { index: 2, time: '01:00', seconds: 60, url: 'https://example.invalid/b.jpg', keywords: ['乙'] },
        ],
      },
    ],
  };
}

function runUnitChecks(check) {
  const api = loadApi();
  const manifest = sampleManifest();
  const session = manifest.sessions[0];

  // ---------------------------------------------------------------- naming
  check(
    'sessionFileStem pads the week (第02周_第1节)',
    api.sessionFileStem(session) === '第02周_第1节',
    api.sessionFileStem(session),
  );
  check(
    'sessionFolderStem uses the date (2026-03-04_第1节)',
    api.sessionFolderStem(session) === '2026-03-04_第1节',
    api.sessionFolderStem(session),
  );
  check(
    'courseFolderName sanitises the course title',
    api.courseFolderName({ title: 'a/b:c' }) === 'a-b-c',
    api.courseFolderName({ title: 'a/b:c' }),
  );
  check(
    'courseFolderName falls back for a missing title',
    api.courseFolderName({}) === 'sjtu-course',
    api.courseFolderName({}),
  );
  check(
    'safeRelativePath sanitises each segment but keeps slashes',
    api.safeRelativePath('课程/transcripts/a:b.md') === '课程/transcripts/a-b.md',
    api.safeRelativePath('课程/transcripts/a:b.md'),
  );
  check(
    'extensionForMime maps jpeg to jpg',
    api.extensionForMime('image/jpeg', '') === 'jpg',
    api.extensionForMime('image/jpeg', ''),
  );
  check(
    'extensionForMime falls back to the URL suffix',
    api.extensionForMime('', 'https://x/y.png?v=2') === 'png',
    api.extensionForMime('', 'https://x/y.png?v=2'),
  );

  // ------------------------------------------------- content option filter
  const all = api.applyContentOptions(session, api.defaultContent());
  check(
    'applyContentOptions keeps everything when all boxes are ticked',
    all.transcript.length === 2 && all.slides.length === 2 && all.summary.chapters.length === 1,
    JSON.stringify({ t: all.transcript.length, s: all.slides.length, c: all.summary.chapters.length }),
  );
  const noTranscript = api.applyContentOptions(session, { transcript: false, slides: true, summary: true, chapters: true });
  check('applyContentOptions drops the transcript when unticked', noTranscript.transcript.length === 0, noTranscript.transcript.length);
  const noSlides = api.applyContentOptions(session, { transcript: true, slides: false, summary: true, chapters: true });
  check('applyContentOptions drops the slides when unticked', noSlides.slides.length === 0, noSlides.slides.length);
  const chaptersOnly = api.applyContentOptions(session, { transcript: true, slides: true, summary: false, chapters: true });
  check(
    'summary off + chapters on keeps only the chapters',
    chaptersOnly.summary && chaptersOnly.summary.overview === null &&
      chaptersOnly.summary.keywords.length === 0 && chaptersOnly.summary.chapters.length === 1,
    JSON.stringify(chaptersOnly.summary),
  );
  const summaryOnly = api.applyContentOptions(session, { transcript: true, slides: true, summary: true, chapters: false });
  check(
    'chapters off + summary on keeps only keywords/overview',
    summaryOnly.summary && summaryOnly.summary.chapters.length === 0 && !!summaryOnly.summary.overview,
    JSON.stringify(summaryOnly.summary),
  );
  const neither = api.applyContentOptions(session, { transcript: true, slides: true, summary: false, chapters: false });
  check('summary off + chapters off removes the summary object', neither.summary === null, neither.summary);
  check(
    'applyContentOptions does not mutate the captured session',
    session.transcript.length === 2 && session.slides.length === 2 && session.summary.chapters.length === 1,
    'input mutated',
  );
  check(
    'summary stays flagged as platform AI after filtering',
    chaptersOnly.summary.generated_by_platform_ai === true,
    chaptersOnly.summary.generated_by_platform_ai,
  );

  // -------------------------------------------------------- output planning
  const slideFiles = {
    [session.id]: [
      { index: 1, seconds: 10, mime: 'image/jpeg', ext: 'jpg', bytes: new Uint8Array([1, 2, 3]) },
      { index: 2, seconds: 60, mime: 'image/jpeg', ext: 'jpg', bytes: new Uint8Array([4, 5, 6]) },
    ],
  };
  const paths = (plan) => plan.files.map((f) => f.path).sort();

  const coursePlan = api.planOutput(manifest, {
    scope: 'course', content: api.defaultContent(), format: 'pack',
    keepManifest: true, slideFiles, exportedAt: manifest.exported_at,
  });
  check('course layout root folder is the course title', coursePlan.rootFolder === '示例课程', coursePlan.rootFolder);
  check(
    'course layout produces the documented flat tree',
    JSON.stringify(paths(coursePlan)) ===
      JSON.stringify([
        '示例课程/ai_prompt.md',
        '示例课程/full_course.md',
        '示例课程/index.md',
        '示例课程/manifest.json',
        '示例课程/slides/第02周_第1节/001.jpg',
        '示例课程/slides/第02周_第1节/002.jpg',
        '示例课程/summaries/第02周_第1节.md',
        '示例课程/transcripts/第02周_第1节.md',
      ]),
    paths(coursePlan).join('\n'),
  );
  check('course layout omits SRT unless requested', !paths(coursePlan).some((p) => p.endsWith('.srt')), 'srt present');

  const srtPlan = api.planOutput(manifest, {
    scope: 'course', content: api.defaultContent(), format: 'pack',
    wantSrt: true, keepManifest: true, slideFiles, exportedAt: manifest.exported_at,
  });
  check(
    'SRT is written only when the advanced box is ticked',
    paths(srtPlan).indexOf('示例课程/subtitles/第02周_第1节.srt') !== -1,
    paths(srtPlan).join(','),
  );

  const currentPlan = api.planOutput(manifest, {
    scope: 'current', content: api.defaultContent(), format: 'pack',
    keepManifest: true, slideFiles, exportedAt: manifest.exported_at,
  });
  check(
    'current-session layout nests under sessions/ and stays tidy',
    JSON.stringify(paths(currentPlan)) ===
      JSON.stringify([
        '示例课程/ai_prompt.md',
        '示例课程/manifest.json',
        '示例课程/sessions/2026-03-04_第1节/slides/001.jpg',
        '示例课程/sessions/2026-03-04_第1节/slides/002.jpg',
        '示例课程/sessions/2026-03-04_第1节/summary.md',
        '示例课程/sessions/2026-03-04_第1节/transcript.md',
      ]),
    paths(currentPlan).join('\n'),
  );
  check(
    'current-session layout does not scatter files at the root',
    !paths(currentPlan).some((p) => /^示例课程\/[^/]+\.md$/.test(p) && !p.endsWith('ai_prompt.md')),
    paths(currentPlan).join(','),
  );

  const manifestOnly = api.planOutput(manifest, {
    scope: 'course', content: api.defaultContent(), format: 'manifest',
    keepManifest: true, slideFiles,
  });
  check(
    'manifest format emits exactly one file',
    paths(manifestOnly).length === 1 && paths(manifestOnly)[0] === '示例课程/manifest.json',
    paths(manifestOnly).join(','),
  );

  const noManifest = api.planOutput(manifest, {
    scope: 'course', content: api.defaultContent(), format: 'pack',
    keepManifest: false, slideFiles, exportedAt: manifest.exported_at,
  });
  check(
    'unticking "keep manifest.json" removes it from the pack',
    !paths(noManifest).some((p) => p.endsWith('manifest.json')),
    paths(noManifest).join(','),
  );

  const noSlidesPlan = api.planOutput(
    Object.assign({}, manifest, {
      sessions: [api.applyContentOptions(session, { transcript: true, slides: false, summary: true, chapters: true })],
    }),
    { scope: 'course', content: { transcript: true, slides: false, summary: true, chapters: true },
      format: 'pack', keepManifest: true, slideFiles, exportedAt: manifest.exported_at },
  );
  check(
    'unticking PPT removes the slide images from the plan',
    !paths(noSlidesPlan).some((p) => /\.(jpg|png)$/.test(p)),
    paths(noSlidesPlan).join(','),
  );
  check(
    'plan stats count text and binary files separately',
    coursePlan.stats.textCount === 6 && coursePlan.stats.slideCount === 2,
    JSON.stringify(coursePlan.stats),
  );

  // -------------------------------------------------------------- renderers
  const mdAll = api.renderSessionMarkdown(session, api.defaultContent());
  check('session markdown includes the AI banner', mdAll.indexOf('不是教师原话') !== -1, 'missing banner');
  check('session markdown includes transcript lines', mdAll.indexOf('**[00:05]**') !== -1, 'missing line');
  const mdNoSummary = api.renderSessionMarkdown(session, { transcript: true, slides: true, summary: false, chapters: false });
  check('session markdown omits the summary block when unticked', mdNoSummary.indexOf('平台 AI 概要') === -1, 'summary leaked');
  check(
    'session markdown marks the PPT end-time inference',
    mdAll.indexOf('结束时间由下一张 PPT 的开始时间推断') !== -1,
    'missing note',
  );
  const idx = api.renderIndexMarkdown(manifest);
  check('index markdown has a session table row', idx.indexOf('| 1 | 2026-03-04 | 第1节 |') !== -1, idx.slice(0, 120));

  // ------------------------------------------------------------------ ZIP
  const zip = api.buildZip(
    [
      { path: '示例课程/hello.txt', text: 'hello world' },
      { path: '示例课程/sub/binary.bin', data: new Uint8Array([0, 1, 2, 250, 255]) },
    ],
    new Date(2026, 2, 4, 8, 0, 0),
  );
  // The userscript is evaluated inside the jsdom realm, so its Uint8Array has a
  // different prototype than Node's; ArrayBuffer.isView works across realms.
  check('buildZip returns bytes', ArrayBuffer.isView(zip) && zip.length > 0, zip && zip.length);
  check(
    'buildZip starts with a local file header signature',
    zip[0] === 0x50 && zip[1] === 0x4b && zip[2] === 0x03 && zip[3] === 0x04,
    Array.from(zip.slice(0, 4)).join(','),
  );
  const end = zip.length - 22;
  check(
    'buildZip ends with an end-of-central-directory record',
    zip[end] === 0x50 && zip[end + 1] === 0x4b && zip[end + 2] === 0x05 && zip[end + 3] === 0x06,
    Array.from(zip.slice(end, end + 4)).join(','),
  );
  const entryCount = zip[end + 10] | (zip[end + 11] << 8);
  check('buildZip records both entries in the central directory', entryCount === 2, entryCount);
  check('crc32 of an empty buffer is 0', api.crc32(new Uint8Array(0)) === 0, api.crc32(new Uint8Array(0)));
  check(
    'toUtf8 encodes CJK as multi-byte UTF-8',
    api.toUtf8('中').length === 3,
    api.toUtf8('中').length,
  );

  // ---------------------------------------------------------------- storage
  check(
    'splitPath separates directories from the file name',
    JSON.stringify(api.splitPath('a/b/c.txt')) === JSON.stringify({ directories: ['a', 'b'], name: 'c.txt' }),
    JSON.stringify(api.splitPath('a/b/c.txt')),
  );
  check('SAVE_MODE exposes folder and zip', api.SAVE_MODE.FOLDER === 'folder' && api.SAVE_MODE.ZIP === 'zip');
}

module.exports = { runUnitChecks, loadApi, sampleManifest };
