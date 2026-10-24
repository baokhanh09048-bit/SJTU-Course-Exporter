# Public source boundary

This repository contains the exporter, synthetic examples, and anonymised structural test fixtures. Real course transcripts, slide images, saved authenticated pages, session data, cookies and credentials are excluded. The fixture's invented teacher/course labels and `.invalid` media URLs are test data. Private reverse-engineering notes are not distributed.

The Python and userscript tests run offline. Optional tests that require a private saved page may skip in a public checkout.

The extraction-core integrity test now ignores both version declarations. The old committed and current extraction cores were compared after this normalization and were identical; no capture logic was changed.

2026-10-24 validation: 258 Python tests passed, 2 private-page tests skipped. Node.js and jsdom runtime/ZIP/browser mocks were enabled. The underlying JavaScript runner is invoked by this suite; its checks overlap with the Python wrappers. No live platform session was used.
