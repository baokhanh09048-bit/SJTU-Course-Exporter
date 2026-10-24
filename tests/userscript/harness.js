/**
 * Execution harness for the SJTU Course Exporter userscript.
 *
 * Loads the real userscript into a jsdom window together with the anonymised
 * structural fixture, stubs the Tampermonkey APIs, optionally installs a mock
 * File System Access API, drives the UI, and reports what happened.
 *
 * Two properties matter most:
 *
 *  1. A freeze. If the script creates an unbounded MutationObserver feedback
 *     loop the event loop never yields, the watchdog below never fires, and the
 *     parent runner kills this process. That IS the freeze signal.
 *
 *  2. Real artefacts. Everything the script produced is written to --out-dir so
 *     an independent tool (Python's zipfile) can verify the output rather than
 *     trusting the script's own summary.
 *
 * Usage:
 *   node harness.js [--fixture PATH] [--script PATH]
 *                   [--scope current|group|course]
 *                   [--format pack|manifest]
 *                   [--save-mode folder|zip]
 *                   [--fake-fsa] [--want-srt] [--no-manifest]
 *                   [--content transcript,slides,summary,chapters]
 *                   [--strip-player] [--out-dir DIR]
 * Prints a single JSON object on stdout.
 */

'use strict';

const fs = require('fs');
const path = require('path');

const REPO = path.resolve(__dirname, '..', '..');

function arg(name, fallback) {
  const i = process.argv.indexOf(name);
  return i === -1 ? fallback : process.argv[i + 1];
}
function flag(name) {
  return process.argv.includes(name);
}

const FIXTURE = arg('--fixture', path.join(REPO, 'tests', 'fixtures', 'sample_page.html'));
const SCRIPT = arg('--script', path.join(REPO, 'userscript', 'sjtu-course-exporter.user.js'));
const OUT_DIR = arg('--out-dir', null);
const SCOPE = arg('--scope', null);
const FORMAT = arg('--format', null);
const SAVE_MODE = arg('--save-mode', null);
const CONTENT = arg('--content', null);
const FAKE_FSA = flag('--fake-fsa');
const STRIP_PLAYER = flag('--strip-player');
/** How long to let the export run before snapshotting. */
const BUDGET = parseInt(arg('--budget', '0'), 10) || (SCOPE === 'course' ? 6000 : 1200);

const { JSDOM, VirtualConsole } = require('jsdom');

const result = {
  ok: false,
  stage: 'init',
  errors: [],
  panelInjected: false,
  observerCallbacks: 0,
  observerCallbacksAfterSettle: null,
  idleMutationDelta: undefined,
  downloads: [],
  written: [],
  fsaSupported: false,
  panelText: null,
  progress: null,
  console: [],
};

function fail(stage, err) {
  const text = String((err && err.stack) || err);
  // jsdom's CSS parser noise must not be reported as a script failure.
  if (/css-tree|Could not parse CSS stylesheet|"\)" is expected/.test(text)) {
    result.errors.push(`[jsdom css parser noise] ${text.split('\n')[0]}`);
    return;
  }
  result.stage = stage;
  result.errors.push(text);
}

/** Condense a manifest for assertions about what was actually exported. */
function summarizeManifest(data) {
  const session = (data.sessions || [])[0] || {};
  const summary = session.summary || {};
  return {
    schema_version: data.schema_version,
    platform: data.platform,
    course_title: (data.course || {}).title,
    teacher: (data.course || {}).teacher,
    discovered_session_count: data.discovered_session_count,
    sessions: (data.sessions || []).length,
    transcript_entries: (session.transcript || []).length,
    slides: (session.slides || []).length,
    keywords: (summary.keywords || []).length,
    chapters: (summary.chapters || []).length,
    has_summary: !!session.summary,
    summary_flagged_platform_ai: summary.generated_by_platform_ai === true,
    raw_texts: (session.transcript || []).map((e) => e.raw_text),
    normalized_texts: (session.transcript || []).map((e) => e.normalized_text),
    seconds: (session.transcript || []).map((e) => e.seconds),
    video_url: (session.video || {}).url || null,
    leaks_signed_key: JSON.stringify(data).indexOf('0123456789abcdef') !== -1,
    download_implemented: (session.video || {}).download_implemented,
    export_options: data.export_options || null,
  };
}

/** Minimal in-memory FileSystemDirectoryHandle implementation. */
function makeMockFileSystem() {
  const files = new Map(); // 'a/b/c.txt' -> {kind, text|base64}
  const dirs = new Set();

  function dirHandle(name, prefix) {
    return {
      kind: 'directory',
      name,
      async queryPermission() { return 'granted'; },
      async requestPermission() { return 'granted'; },
      async getDirectoryHandle(child, opts) {
        const p = prefix ? `${prefix}/${child}` : child;
        if (!opts || !opts.create) {
          if (!dirs.has(p)) {
            const err = new Error(`directory not found: ${p}`);
            err.name = 'NotFoundError';
            throw err;
          }
        }
        dirs.add(p);
        return dirHandle(child, p);
      },
      async getFileHandle(child) {
        const p = prefix ? `${prefix}/${child}` : child;
        return {
          kind: 'file',
          name: child,
          async createWritable() {
            return {
              async write(data) {
                let payload;
                if (data && typeof data === 'object' && typeof data.arrayBuffer === 'function') {
                  payload = { kind: 'bytes', base64: Buffer.from(await data.arrayBuffer()).toString('base64') };
                } else if (data instanceof Uint8Array) {
                  payload = { kind: 'bytes', base64: Buffer.from(data).toString('base64') };
                } else {
                  payload = { kind: 'text', text: String(data) };
                }
                files.set(p, payload);
              },
              async close() {},
            };
          },
        };
      },
    };
  }

  return {
    root: dirHandle('SJTU-Course-Exports', ''),
    files,
    tree() {
      return Array.from(files.keys()).sort();
    },
  };
}

async function main() {
  let html = fs.readFileSync(FIXTURE, 'utf8');
  const code = fs.readFileSync(SCRIPT, 'utf8');

  // jsdom parses every stylesheet eagerly and its CSS parser throws on the
  // modern CSS in the real saved page (~1.7 MB of inlined design tokens).
  // The userscript injects its own panel styles and never reads the page's CSS,
  // so dropping it changes nothing about what is under test and turns a hard
  // crash into a fast parse.
  if (!flag('--keep-styles')) {
    const before = html.length;
    html = html.replace(/<style[\s\S]*?<\/style>/gi, '<!-- stylesheet stripped by harness -->');
    result.stylesStripped = before - html.length;
  }

  const virtualConsole = new VirtualConsole();
  const IGNORABLE = [
    /Not implemented/i,
    // jsdom's CSS parser chokes on modern stylesheets in the real saved page.
    // It is noise: the script never depends on jsdom's CSS support.
    /Could not parse CSS stylesheet/i,
    /"\)" is expected/,
    /css-tree/,
  ];
  virtualConsole.on('jsdomError', (e) => {
    const text = String((e && e.stack) || (e && e.message) || e);
    if (IGNORABLE.some((re) => re.test(text))) return;
    result.errors.push(text);
  });
  for (const level of ['error', 'warn', 'log', 'info']) {
    virtualConsole.on(level, (...args) => {
      result.console.push(`${level}: ${args.map((a) => String(a)).join(' ')}`.slice(0, 400));
    });
  }

  const dom = new JSDOM(html, {
    runScripts: 'outside-only',
    url:
      process.env.SCE_URL ||
      'https://v.sjtu.edu.cn/jy-application-resourcemanage-ui/#/play-center',
    pretendToBeVisual: true,
    virtualConsole,
  });

  const win = dom.window;

  if (flag('--single-session')) {
    const extra = win.document.querySelector('#list-item-1');
    if (extra) extra.remove();
  }
  if (flag('--missing-slide-url')) {
    const image = win.document.querySelector('#ppt-horizontal-2 img');
    if (image) image.removeAttribute('src');
  }

  if (STRIP_PLAYER) {
    for (const sel of [
      '.ai-caption-wrapper', '.outline-wrapper', '.course-list-wrapper',
      '.ppt-horizontal-wrapper', 'video',
    ]) {
      win.document.querySelectorAll(sel).forEach((node) => node.remove());
    }
    result.strippedPlayerDom = true;
  }

  // ---- Tampermonkey API stubs -------------------------------------------
  const store = new Map();
  const fast = flag('--fast-settings');
  if (fast) {
    // Keep the batch test quick: the fixture has no real SPA, so switching to
    // the second session can never succeed and we want that failure fast.
    store.set('setting.sessionTimeout', 1200);
    store.set('setting.quietMs', 250);
    store.set('setting.betweenSessionsMs', 0);
    store.set('setting.maxRetries', 0);
  }
  win.GM_getValue = (key, def) => (store.has(key) ? store.get(key) : def);
  win.GM_setValue = (key, value) => { store.set(key, value); };
  win.GM_registerMenuCommand = () => {};
  win.__SCE_TEST_HOOK__ = true;

  // ---- count MutationObserver deliveries --------------------------------
  const RealMO = win.MutationObserver;
  win.MutationObserver = class CountingObserver extends RealMO {
    constructor(cb) {
      super(function wrapped(...args) {
        result.observerCallbacks += 1;
        return cb.apply(this, args);
      });
    }
  };

  // ---- download plumbing + optional File System Access mock -------------
  const captured = [];
  let lastBlob = null;
  win.URL.createObjectURL = (blob) => { lastBlob = blob; return 'blob:stub'; };
  win.URL.revokeObjectURL = () => {};
  win.HTMLAnchorElement.prototype.click = function click() {
    if (this.download) {
      result.downloads.push(this.download);
      captured.push({ name: this.download, blob: lastBlob });
      lastBlob = null;
    }
  };

  // ---- fetch stub so the PPT binary path is actually exercised ----------
  // jsdom has no fetch. Without this the slide downloads fail silently and the
  // folder tree ends up with no images at all, which would hide real bugs.
  const fetchedUrls = [];
  win.fetch = async (url) => {
    fetchedUrls.push(String(url));
    const name = String(url).split('/').pop() || 'slide';
    // A tiny but well-formed JPEG header plus a per-file marker so ordering is
    // verifiable downstream.
    const marker = name.replace(/[^0-9a-zA-Z]/g, '').slice(0, 8);
    const bytes = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46]);
    const tail = new TextEncoder().encode(marker);
    const body = new Uint8Array(bytes.length + tail.length);
    body.set(bytes, 0);
    body.set(tail, bytes.length);
    return {
      ok: true,
      status: 200,
      headers: { get: () => 'image/jpeg' },
      async blob() {
        return new win.Blob([body], { type: 'image/jpeg' });
      },
      async arrayBuffer() {
        return body.buffer;
      },
    };
  };

  let mockFs = null;
  if (FAKE_FSA) {
    mockFs = makeMockFileSystem();
    win.showDirectoryPicker = async () => mockFs.root;
    result.fsaSupported = true;
  } else {
    result.fsaSupported = typeof win.showDirectoryPicker === 'function';
  }

  if (typeof win.TextEncoder === 'undefined') win.TextEncoder = TextEncoder;

  // ---- evaluate the userscript ------------------------------------------
  result.stage = 'evaluating';
  result.url = win.location.href;
  result.hash = win.location.hash;
  win.eval(code);

  result.stage = 'awaiting-dom-ready';
  // The real saved page is ~2 MB, so jsdom needs longer to finish parsing and
  // fire DOMContentLoaded than the small fixture does. Poll for the panel
  // rather than guessing a fixed delay.
  result.readyStateAtEval = win.document.readyState;
  {
    const deadline = Date.now() + (parseInt(arg('--panel-wait', '0'), 10) || 15000);
    while (Date.now() < deadline) {
      if (win.document.getElementById('sce-panel')) break;
      await new Promise((resolve) => win.setTimeout(resolve, 200));
    }
  }

  result.panelInjected = !!win.document.getElementById('sce-panel');
  if (!result.panelInjected) {
    if (!STRIP_PLAYER) {
      result.errors.push('panel #sce-panel was not injected');
      return;
    }
    result.stage = 'done';
    result.ok = true;
    return;
  }

  const $ = (sel) => win.document.querySelector(sel);
  const setRadio = (name, value) => {
    const el = win.document.querySelector(`input[name="${name}"][value="${value}"]`);
    if (!el) throw new Error(`radio not found: ${name}=${value}`);
    el.checked = true;
    el.dispatchEvent(new win.Event('change', { bubbles: true }));
  };
  const setCheckbox = (sel, value) => {
    const el = $(sel);
    if (!el) throw new Error(`checkbox not found: ${sel}`);
    el.checked = value;
    el.dispatchEvent(new win.Event('change', { bubbles: true }));
  };

  try {
    // Give the user a folder before choosing folder mode.
    if (FAKE_FSA && !flag('--skip-pick-folder')) {
      result.stage = 'pick-folder';
      $('#sce-pick-folder').click();
      await new Promise((resolve) => win.setTimeout(resolve, 150));
    }
    if (SCOPE) setRadio('sce-scope', SCOPE);
    if (FORMAT) setRadio('sce-format', FORMAT);
    if (SAVE_MODE) setRadio('sce-savemode', SAVE_MODE);
    if (CONTENT) {
      const wanted = CONTENT.split(',').map((s) => s.trim());
      for (const key of ['transcript', 'slides', 'summary', 'chapters']) {
        setCheckbox(`#sce-content-${key}`, wanted.indexOf(key) !== -1);
      }
    }
    if (flag('--want-srt')) setCheckbox('#sce-want-srt', true);
    if (flag('--no-manifest')) setCheckbox('#sce-keep-manifest', false);

    result.options = {
      scope: (win.document.querySelector('input[name="sce-scope"]:checked') || {}).value,
      format: (win.document.querySelector('input[name="sce-format"]:checked') || {}).value,
      saveMode: (win.document.querySelector('input[name="sce-savemode"]:checked') || {}).value,
      wantSrt: $('#sce-want-srt').checked,
      keepManifest: $('#sce-keep-manifest').checked,
    };

    result.stage = 'export';
    const started = Date.now();
    $('#sce-start-btn').click();

    // Poll for completion rather than guessing a fixed budget: refresh() keeps
    // #sce-start-btn disabled for exactly as long as the export is running.
    const cap = parseInt(arg('--budget', '0'), 10) || 45000;
    for (;;) {
      await new Promise((resolve) => win.setTimeout(resolve, 100));
      const btn = $('#sce-start-btn');
      if (!btn || !btn.disabled) break;
      if (Date.now() - started > cap) {
        result.exportTimedOut = true;
        break;
      }
    }
    // Let the post-run panel updates and the observer quiet down.
    await new Promise((resolve) => win.setTimeout(resolve, 500));
    result.exportElapsedMs = Date.now() - started;
    if (flag('--repeat-export')) {
      result.firstExportFileCount = mockFs ? mockFs.files.size : result.downloads.length;
      result.firstFetchCount = fetchedUrls.length;
      $('#sce-start-btn').click();
      for (;;) {
        await new Promise((resolve) => win.setTimeout(resolve, 100));
        if (!$('#sce-start-btn').disabled) break;
        if (Date.now() - started > cap) {
          result.exportTimedOut = true;
          break;
        }
      }
      await new Promise((resolve) => win.setTimeout(resolve, 500));
      result.repeatExportFileCount = mockFs ? mockFs.files.size : result.downloads.length;
      result.repeatFetchCount = fetchedUrls.length;
      result.repeatExportLog = ($('#sce-log') || {}).textContent || '';
    }
  } catch (err) {
    fail('driving-ui', err);
  }

  // ---- watchdog: only reached if the event loop is still alive ----------
  result.stage = 'measuring';
  const before = result.observerCallbacks;
  await new Promise((resolve) => win.setTimeout(resolve, 400));
  result.observerCallbacksAfterSettle = result.observerCallbacks;
  result.idleMutationDelta = result.observerCallbacks - before;

  const panel = win.document.getElementById('sce-panel');
  result.panelText = panel ? panel.textContent.replace(/\s+/g, ' ').trim().slice(0, 4000) : null;
  const log = win.document.getElementById('sce-log');
  result.logText = log ? log.textContent.slice(-1200) : null;
  result.logHidden = log ? log.hidden : null;
  result.progress = {
    phase: ($('#sce-phase') || {}).textContent,
    scope: ($('#sce-scope-shown') || {}).textContent,
    text: ($('#sce-progress-text') || {}).textContent,
    barWidth: ($('#sce-progress') || { style: {} }).style.width,
    current: ($('#sce-processing') || {}).textContent,
    asr: ($('#sce-current-asr') || {}).textContent,
    slides: ($('#sce-current-slides') || {}).textContent,
    success: ($('#sce-success') || {}).textContent,
    fail: ($('#sce-fail') || {}).textContent,
    progressBoxHidden: ($('#sce-progress-box') || {}).hidden,
    stopVisible: !($('#sce-stop-btn') || {}).hidden,
  };
  result.settingsPersisted = {
    sessionTimeout: store.get('setting.sessionTimeout'),
    maxRetries: store.get('setting.maxRetries'),
  };
  result.testApiExposed = !!(win.__SCE_TEST__ && win.__SCE_TEST__.buildZip);

  // ---- write real artefacts for independent verification ----------------
  if (OUT_DIR) {
    fs.mkdirSync(OUT_DIR, { recursive: true });
    for (const entry of captured) {
      if (!entry.blob) continue;
      const bytes = Buffer.from(await entry.blob.arrayBuffer());
      fs.writeFileSync(path.join(OUT_DIR, entry.name.replace(/[\\/:*?"<>|]/g, '_')), bytes);
    }
    if (mockFs) {
      const tree = {};
      for (const [p, payload] of mockFs.files.entries()) tree[p] = payload;
      fs.writeFileSync(path.join(OUT_DIR, 'fsa-tree.json'), JSON.stringify(tree, null, 2), 'utf8');
      result.written = mockFs.tree();

      // Surface what the manifest says, including any non-fatal warnings, so a
      // silently-degraded run cannot pass unnoticed.
      for (const [p, payload] of mockFs.files.entries()) {
        if (payload.kind === 'text' && p.endsWith('manifest.json')) {
          try {
            const data = JSON.parse(payload.text);
            result.manifestWarnings = data.warnings || [];
            result.manifestSummary = summarizeManifest(data);
          } catch (_) { /* not JSON we care about */ }
        }
      }
    }
    for (const entry of captured) {
      if (!entry.blob || !entry.name.endsWith('.json')) continue;
      try {
        const text = Buffer.from(await entry.blob.arrayBuffer()).toString('utf8');
        const data = JSON.parse(text);
        result.manifestWarnings = data.warnings || [];
        result.manifestSummary = summarizeManifest(data);
      } catch (_) { /* not a manifest */ }
    }
    result.fetchedUrls = fetchedUrls;
    fs.writeFileSync(path.join(OUT_DIR, 'harness-result.json'), JSON.stringify(result, null, 2), 'utf8');
  }

  result.stage = 'done';
  result.ok = result.errors.length === 0;
  try { win.close(); } catch (_) {}
}

main()
  .catch((err) => fail('unhandled', err))
  .then(() => {
    process.stdout.write(JSON.stringify(result, null, 2) + '\n');
    process.exit(0);
  });
