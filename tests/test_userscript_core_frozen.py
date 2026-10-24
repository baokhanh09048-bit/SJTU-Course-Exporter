"""Guard the verified extraction core against accidental edits.

This round deliberately rewrote the userscript's interaction and output layers
while promising not to touch the capture logic. A promise in a commit message is
not a guarantee; this test is.

The extraction core is everything above section 6 of the userscript: the
selectors, the small utilities, session-metadata parsing, the extractors
(``extractTranscript`` / ``extractSummary`` / ``extractSlides`` /
``extractVideo`` / ``buildSessionExport``) and the condition-based waiting
strategy. Its SHA-1 is pinned below.

If this test fails, one of two things happened:

1. Somebody edited the extraction core by accident — revert it, or
2. Somebody edited it on purpose — then re-derive the hash, **and** explain in
   the commit message why the capture logic needed to change, because every
   adapter/manifest test and the whole reverse-engineering document were written
   against the old behaviour.

Both the metadata header and executable version strings are normalised out, so bumping the userscript version does not
invalidate the pin.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
USERSCRIPT = REPO / "userscript" / "sjtu-course-exporter.user.js"

BANNER = "  // " + "=" * 70
CORE_ENDS_AT = "  // 6. Output planning"

# SHA-1 of the extraction core with the version string normalised.
EXPECTED_CORE_SHA1 = "67b53b2518cd4fb3242539cb475c144180427163"

EXPECTED_CORE_LINES = 645


def _extraction_core() -> str:
    src = USERSCRIPT.read_text(encoding="utf-8")
    cut = src.rindex(BANNER, 0, src.index(CORE_ENDS_AT))
    return src[:cut]


@pytest.fixture(scope="module")
def core() -> str:
    assert USERSCRIPT.exists(), f"missing {USERSCRIPT}"
    return _extraction_core()


class TestExtractionCoreIntegrity:
    def test_core_hash_is_pinned(self, core: str):
        normalised = re.sub(r"const VERSION = '[^']*';", "const VERSION = '<any>';", core)
        normalised = re.sub(r"(?m)^(// @version\s+).*$", r"\1<any>", normalised)
        actual = hashlib.sha1(normalised.encode("utf-8")).hexdigest()
        assert actual == EXPECTED_CORE_SHA1, (
            "the userscript extraction core changed.\n"
            f"  expected sha1 {EXPECTED_CORE_SHA1}\n"
            f"  actual   sha1 {actual}\n"
            "The capture logic must stay frozen; see this module's docstring."
        )

    def test_core_size_is_what_we_expect(self, core: str):
        assert core.count("\n") + 1 == EXPECTED_CORE_LINES

    @pytest.mark.parametrize(
        "symbol",
        [
            "function discoverSessions",
            "function extractTranscript",
            "function extractSummary",
            "function extractSlides",
            "function extractVideo",
            "async function buildSessionExport",
            "function contentSignature",
            "async function waitForSessionChange",
            "function selectedSessionKey",
            "function sanitizeUrl",
            "function normalizeText",
            "function parseTime",
            "const SEL = {",
        ],
    )
    def test_extractor_symbol_is_present_in_the_core(self, core: str, symbol: str):
        assert symbol in core, f"{symbol} moved out of the frozen extraction core"

    def test_output_layer_lives_below_the_core(self, core: str):
        # The new interaction/output layers must not be inside the frozen part.
        for symbol in ("function planOutput", "function buildZip", "function buildPanel"):
            assert symbol not in core, f"{symbol} is in the frozen core"

    def test_core_still_defers_all_extraction_to_the_unchanged_functions(self):
        """The orchestration below the core must call the frozen extractors."""
        src = USERSCRIPT.read_text(encoding="utf-8")
        tail = src[src.index(CORE_ENDS_AT) :]
        assert "buildSessionExport(ref, courseTitle)" in tail
        assert "applyContentOptions(payload" in tail, "filtering must happen after capture"
        # Filtering happens on the captured payload, never inside an extractor.
        assert "applyContentOptions(" not in _extraction_core()
