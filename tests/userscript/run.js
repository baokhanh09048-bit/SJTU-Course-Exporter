/**
 * Runs the userscript checks.
 *
 *   - unit checks for the pure output/planning/ZIP helpers (in process)
 *   - DOM cases through harness.js, each in a child process under a hard
 *     timeout, because a frozen page never yields to the event loop and must
 *     be detected as a process kill rather than a passing test
 *
 * Real artefacts (the ZIP, and the folder tree when the File System Access mock
 * is used) are written to ARTIFACTS so an independent tool can verify them.
 *
 * Usage: node run.js
 * Exit code 0 = every check passed.
 */

'use strict';

const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const HARNESS = path.join(__dirname, 'harness.js');
const ARTIFACTS = path.join(__dirname, '.artifacts');
const TIMEOUT_MS = 20000;

const checks = [];
function check(name, condition, detail) {
  checks.push({ name, ok: !!condition, detail: detail === undefined ? '' : String(detail) });
}

console.log('SJTU Course Exporter — userscript checks\n');

// ---------------------------------------------------------------------------
// 1. Pure helpers
// ---------------------------------------------------------------------------
console.log('— unit: output planning, content filter, ZIP writer —');
try {
  const { runUnitChecks } = require('./unit.js');
  runUnitChecks(check);
  console.log(`    ${checks.filter((c) => c.ok).length}/${checks.length} unit checks ok\n`);
} catch (err) {
  check('unit checks run', false, (err && err.message) || err);
  console.log('    unit checks crashed:', (err && err.message) || err, '\n');
}

// ---------------------------------------------------------------------------
// 2. DOM cases
// ---------------------------------------------------------------------------
fs.rmSync(ARTIFACTS, { recursive: true, force: true });
fs.mkdirSync(ARTIFACTS, { recursive: true });

const cases = [
  {
    label: 'player page — default (current session, AI pack, folder)',
    args: ['--fast-settings', '--fake-fsa', '--scope', 'current'],
    dir: 'current',
  },
  {
    label: 'player page — whole course into a folder (FSA)',
    args: ['--fast-settings', '--fake-fsa', '--scope', 'course', '--save-mode', 'folder', '--want-srt'],
    dir: 'course-folder',
  },
  {
    label: 'player page — whole course as a single ZIP',
    args: ['--fast-settings', '--scope', 'course', '--save-mode', 'zip'],
    dir: 'course-zip',
  },
  {
    label: 'player page — manifest only, minimal content',
    args: ['--fast-settings', '--scope', 'current', '--format', 'manifest', '--content', 'transcript'],
    dir: 'manifest-only',
  },
  {
    label: 'player page — missing folder shows a visible error',
    args: ['--fast-settings', '--fake-fsa', '--skip-pick-folder', '--scope', 'current', '--save-mode', 'folder'],
    dir: 'missing-folder',
  },
  {
    label: 'player page — completed course can be exported again',
    args: ['--fast-settings', '--fake-fsa', '--single-session', '--repeat-export', '--scope', 'course', '--save-mode', 'folder'],
    dir: 'repeat-course',
  },
  {
    label: 'player page — incomplete PPT is reported',
    args: ['--fast-settings', '--fake-fsa', '--missing-slide-url', '--scope', 'current', '--save-mode', 'folder'],
    dir: 'missing-slide',
  },
  {
    label: 'non-player page (course list)',
    args: ['--strip-player'],
    urlOverride: 'https://v.sjtu.edu.cn/jy-application-resourcemanage-ui/#/list-video',
    dir: 'list-page',
  },
  {
    label: 'unknown route holding a player',
    args: [],
    urlOverride: 'https://v.sjtu.edu.cn/jy-application-resourcemanage-ui/#/play-center-v2',
    dir: 'renamed-route',
  },
];

let anyFreeze = false;
const runs = [];

for (const testCase of cases) {
  const outDir = path.join(ARTIFACTS, testCase.dir);
  const args = [HARNESS, ...testCase.args, '--out-dir', outDir];

  const started = Date.now();
  const proc = spawnSync(process.execPath, args, {
    encoding: 'utf8',
    timeout: TIMEOUT_MS,
    maxBuffer: 40 * 1024 * 1024,
    env: { ...process.env, SCE_URL: testCase.urlOverride || '' },
  });
  const elapsed = Date.now() - started;

  if (proc.error && proc.error.code === 'ETIMEDOUT') {
    anyFreeze = true;
    console.log(`✗ ${testCase.label}: FROZE the page (killed after ${TIMEOUT_MS} ms)`);
    console.log('    The script never yielded to the event loop — an unbounded');
    console.log('    MutationObserver feedback loop.\n');
    runs.push({ label: testCase.label, dir: testCase.dir, froze: true });
    continue;
  }

  let parsed;
  try {
    parsed = JSON.parse(proc.stdout);
  } catch (err) {
    console.log(`✗ ${testCase.label}: harness produced no parseable output`);
    console.log((proc.stdout || '').slice(0, 1500));
    console.log((proc.stderr || '').slice(0, 800));
    runs.push({ label: testCase.label, dir: testCase.dir, parseError: true });
    continue;
  }

  runs.push(Object.assign({ label: testCase.label, dir: testCase.dir }, parsed));
  console.log(`• ${testCase.label} (${elapsed} ms)`);
  console.log(`    panel injected : ${parsed.panelInjected}`);
  console.log(`    observer idle  : delta=${parsed.idleMutationDelta}`);
  if (parsed.downloads && parsed.downloads.length) {
    console.log(`    downloads      : ${parsed.downloads.join(', ')}`);
  }
  if (parsed.written && parsed.written.length) {
    console.log(`    folder files   : ${parsed.written.length}`);
  }
  if (parsed.progress) {
    console.log(`    progress       : ${parsed.progress.text} · 成功 ${parsed.progress.success} · 失败 ${parsed.progress.fail}`);
  }
  if (parsed.errors && parsed.errors.length) {
    console.log(`    errors         : ${parsed.errors.slice(0, 2).join(' | ')}`);
  }
  console.log('');
}

const byDir = (d) => runs.find((r) => r.dir === d) || {};

const current = byDir('current');
const courseFolder = byDir('course-folder');
const courseZip = byDir('course-zip');
const manifestOnly = byDir('manifest-only');
const missingFolder = byDir('missing-folder');
const repeatCourse = byDir('repeat-course');
const missingSlide = byDir('missing-slide');
const listPage = byDir('list-page');
const renamed = byDir('renamed-route');

// ---------------------------------------------------------------------------
// 3. Structural checks
// ---------------------------------------------------------------------------
check('no page ever froze (the original regression)', !anyFreeze, anyFreeze ? 'a page froze' : 'ok');
check('no case reported a runtime error', runs.every((r) => !(r.errors || []).length),
  (runs.find((r) => (r.errors || []).length) || {}).errors);
check('panel injected on the player page', current.panelInjected === true, current.panelInjected);
check('MutationObserver settles (no self-triggering loop)',
  typeof current.idleMutationDelta === 'number' && current.idleMutationDelta < 20,
  `idle delta = ${current.idleMutationDelta}`);
check('panel is NOT injected on a non-player page', listPage.panelInjected === false, listPage.panelInjected);
check('renamed player route still recognised via the DOM fallback',
  renamed.panelInjected === true, renamed.panelInjected);
check('the gated test seam is exposed when the harness opts in', current.testApiExposed === true, current.testApiExposed);

// ---------------------------------------------------------------------------
// 4. UI structure — the whole point of this round
// ---------------------------------------------------------------------------
const panelText = current.panelText || '';
check('UI groups choices under 导出范围', panelText.indexOf('导出范围') !== -1);
check('UI groups choices under 导出内容', panelText.indexOf('导出内容') !== -1);
check('UI groups choices under 输出格式', panelText.indexOf('输出格式') !== -1);
check('UI shows a 保存位置 section', panelText.indexOf('保存位置') !== -1);
check('UI offers the three scopes', 
  panelText.indexOf('当前节') !== -1 && panelText.indexOf('当前课次组') !== -1 && panelText.indexOf('整门课程') !== -1);
check('UI offers the four content options',
  ['语音转写', 'PPT 图片', '平台概要', '章节导航'].every((t) => panelText.indexOf(t) !== -1));
check('UI offers AI pack and raw manifest as formats',
  panelText.indexOf('AI 资料包') !== -1 && panelText.indexOf('原始 Manifest JSON') !== -1);
check('UI offers both save modes',
  panelText.indexOf('保存到文件夹') !== -1 && panelText.indexOf('下载单个 ZIP') !== -1);
check('a single primary action button exists', panelText.indexOf('开始导出') !== -1);
check('advanced settings are present but collapsed', panelText.indexOf('高级设置') !== -1);
check('the old separate action buttons are gone',
  panelText.indexOf('导出 AI 语料') === -1 && panelText.indexOf('导出 manifest') === -1 &&
  panelText.indexOf('导出当前节') === -1);

// ---------------------------------------------------------------------------
// 5. Progress area
// ---------------------------------------------------------------------------
const p = current.progress || {};
check('progress reports the phase', p.phase === '完成' || p.phase === '空闲', p.phase);
check('progress reports n / m 课次', /^\d+ \/ \d+ 课次$/.test(p.text || ''), p.text);
check('progress reports the export scope', p.scope === '当前节', p.scope);
check('progress reports the ASR entry count', p.asr === '3', p.asr);
check('progress reports the PPT frame count', p.slides === '3', p.slides);
check('progress reports success count', p.success === '1', p.success);
check('progress reports failure count', p.fail === '0', p.fail);
check('progress bar has a width', typeof p.barWidth === 'string' && p.barWidth.endsWith('%'), p.barWidth);
check('the stop button is hidden when idle', p.stopVisible === false, p.stopVisible);
check('the progress box is visible after a run', p.progressBoxHidden === false, p.progressBoxHidden);
check('missing folder error is visible when Start is clicked',
  missingFolder.progress && missingFolder.progress.progressBoxHidden === false &&
    missingFolder.logHidden === false &&
    (missingFolder.logText || '').includes('请先点击「选择保存文件夹」'),
  (missingFolder.logText || '').slice(-200));
check('a completed course starts a fresh export on the next click',
  repeatCourse.firstFetchCount === 3 && repeatCourse.repeatFetchCount === 6 &&
    (repeatCourse.repeatExportLog || '').includes('本次重新导出'),
  `fetches ${repeatCourse.firstFetchCount} -> ${repeatCourse.repeatFetchCount}`);
check('missing PPT image is reported as an incomplete export',
  missingSlide.progress && missingSlide.progress.phase === '完成（有缺失）' &&
    missingSlide.logHidden === false &&
    (missingSlide.manifestWarnings || []).some((w) => w.includes('PPT 图片缺失 1/3')),
  JSON.stringify(missingSlide.manifestWarnings || []));

// ---------------------------------------------------------------------------
// 6. Save behaviour
// ---------------------------------------------------------------------------
check('folder mode writes many files and downloads nothing',
  (courseFolder.downloads || []).length === 0 && (courseFolder.written || []).length > 0,
  `downloads=${(courseFolder.downloads || []).length} written=${(courseFolder.written || []).length}`);
check('folder mode writes exactly one ZIP-free directory tree',
  (courseFolder.written || []).every((f) => f.indexOf('示例课程/') === 0),
  (courseFolder.written || []).slice(0, 3).join(','));
check('ZIP mode produces exactly ONE download',
  (courseZip.downloads || []).length === 1, (courseZip.downloads || []).join(','));
check('ZIP mode does not scatter loose files',
  (courseZip.downloads || []).every((n) => n.endsWith('.zip')),
  (courseZip.downloads || []).join(','));
check('manifest-only mode produces exactly one JSON file, not an archive',
  (manifestOnly.downloads || []).length === 1 && manifestOnly.downloads[0].endsWith('.json'),
  (manifestOnly.downloads || []).join(','));
check('the File System Access mock was actually used',
  courseFolder.fsaSupported === true, courseFolder.fsaSupported);
check('advanced timeouts are persisted to GM storage',
  courseFolder.settingsPersisted && courseFolder.settingsPersisted.sessionTimeout === 1200,
  JSON.stringify(courseFolder.settingsPersisted));
check('PPT images were actually fetched',
  (courseFolder.fetchedUrls || []).length === 3, (courseFolder.fetchedUrls || []).length);
check('PPT images are written into per-session slide folders',
  (courseFolder.written || []).filter((f) => /\/slides\/[^/]+\/\d{3}\.jpg$/.test(f)).length === 3,
  (courseFolder.written || []).filter((f) => f.indexOf('/slides/') !== -1).join(','));
check('SRT subtitles are written when requested',
  (courseFolder.written || []).some((f) => f.endsWith('.srt')),
  (courseFolder.written || []).join(','));
check('the exported manifest carries all four content kinds',
  (courseFolder.manifestSummary || {}).transcript_entries === 3 &&
    (courseFolder.manifestSummary || {}).slides === 3 &&
    (courseFolder.manifestSummary || {}).keywords === 2 &&
    (courseFolder.manifestSummary || {}).chapters === 2,
  JSON.stringify(courseFolder.manifestSummary));
check('a single-session export is warning free',
  (current.manifestWarnings || []).length === 0, JSON.stringify(current.manifestWarnings));
check('the manifest honestly records the session it could not capture',
  (courseFolder.manifestWarnings || []).some((w) => w.indexOf('第2节') !== -1 && w.indexOf('timeout') !== -1),
  JSON.stringify(courseFolder.manifestWarnings));
check('the manifest records the export options the user chose',
  courseFolder.manifestSummary && courseFolder.manifestSummary.export_options &&
    courseFolder.manifestSummary.export_options.include_srt === true &&
    courseFolder.manifestSummary.export_options.scope === 'course',
  JSON.stringify(courseFolder.manifestSummary && courseFolder.manifestSummary.export_options));
check('manifest-only export respects the content filter',
  manifestOnly.manifestSummary &&
    manifestOnly.manifestSummary.transcript_entries === 3 &&
    manifestOnly.manifestSummary.has_summary === false &&
    manifestOnly.manifestSummary.slides === 0,
  JSON.stringify(manifestOnly.manifestSummary));
check('the signed video key never reaches the export',
  courseFolder.manifestSummary && courseFolder.manifestSummary.leaks_signed_key === false,
  courseFolder.manifestSummary && courseFolder.manifestSummary.leaks_signed_key);
const mSummary = current.manifestSummary || {};
check('raw_text is preserved verbatim (duplicated punctuation kept)',
  (mSummary.raw_texts || []).some((t) => typeof t === 'string' && t.indexOf('，，') !== -1),
  (mSummary.raw_texts || []).join(' | '));
check('normalized_text tidies punctuation but keeps the wording',
  (mSummary.normalized_texts || []).some((t) => typeof t === 'string' && t.indexOf('带有重复标点') !== -1) &&
    !(mSummary.normalized_texts || []).some((t) => typeof t === 'string' && t.indexOf('，，') !== -1),
  (mSummary.normalized_texts || []).join(' | '));
check('timestamps were converted to seconds, in order',
  JSON.stringify(mSummary.seconds) === JSON.stringify([5, 62, 750]),
  JSON.stringify(mSummary.seconds));
check('video download stays marked as not implemented',
  mSummary.download_implemented === false,
  mSummary.download_implemented);
check('single-session export uses the sessions/ layout, not the root',
  (current.written || []).some((f) => f.indexOf('/sessions/') !== -1) &&
    !(current.written || []).some((f) => /^示例课程\/transcripts\//.test(f)),
  (current.written || []).join(','));

// ---------------------------------------------------------------------------
// 7. Batch sweep must not mislabel sessions
// ---------------------------------------------------------------------------
check('batch sweep terminates (no infinite retry loop)',
  typeof courseFolder.exportElapsedMs === 'number' && courseFolder.exportElapsedMs < 20000,
  `elapsed = ${courseFolder.exportElapsedMs} ms`);
check('batch sweep reports the session it could not capture',
  typeof courseFolder.logText === 'string' &&
    (courseFolder.logText.indexOf('失败') !== -1 || courseFolder.logText.indexOf('timeout') !== -1),
  (courseFolder.logText || '').slice(-160));
check('batch sweep still exports the session that worked',
  (courseFolder.written || []).some((f) => f.indexOf('第1节') !== -1),
  (courseFolder.written || []).slice(0, 4).join(','));
check('a session that could not be loaded is NOT written under the wrong name',
  !(courseFolder.written || []).some((f) => f.indexOf('第2节') !== -1),
  (courseFolder.written || []).filter((f) => f.indexOf('第2节') !== -1).join(','));

// ---------------------------------------------------------------------------
console.log('─'.repeat(66));
let failed = 0;
for (const c of checks) {
  if (c.ok) continue;
  console.log(`✗ ${c.name}${c.detail ? `  (${c.detail})` : ''}`);
  failed += 1;
}
for (const c of checks) {
  if (!c.ok) continue;
  console.log(`✓ ${c.name}`);
}
console.log('─'.repeat(66));
console.log(`${checks.length - failed}/${checks.length} checks passed`);
console.log(`artifacts: ${ARTIFACTS}`);
process.exit(failed === 0 ? 0 : 1);
