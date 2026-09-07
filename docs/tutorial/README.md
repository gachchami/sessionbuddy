# SessionBuddy role tutorial

This directory is the maintainable source for the SessionBuddy three-role
tutorial and its print edition.

## Source of truth

- `index.html` is the semantic, accessible reading edition.
- `print.css` is the shared screen/print design system.
- `screenshots.json` records provenance and recapture requirements for every
  instructional figure.
- `coverage.json` maps the guide to product capabilities and evidence.
- `tools/build_pdf.mjs` prints the HTML edition to a visibly labelled design-draft PDF.
- `tools/verify_tutorial.mjs` enforces structural and manifest checks.
- `tools/validate_pdf.mjs` checks PDF tags, structure, outlines, links, and extractable text.
- `tools/render_pdf.mjs` deterministically renders every page and a PNG contact sheet.

The guide targets revision `859bfbcbaa6b6ccf1cfc0b684a6eef6f08ce39b0`
but is a design draft. Every figure was recaptured on 2026-09-08 from the local
Worker against the DevFlow Conf 2027 fixture, at the contract viewport of
1440 x 1000, after the interface moved to the current design system; the earlier
2026-08-17 and 2026-08-18 images no longer resembled the product. The working
tree was unfrozen at capture time, so each figure records
`revision: unfrozen-working-tree` and stays marked `recapture_required`; the
final release filename is deliberately withheld until one frozen-fixture
capture passes the manifest verifier.

## Build

Container-first, using the checked-in browser harness:

```sh
docker compose run --rm --no-deps e2e sh -lc \
  'npm ci && node ../docs/tutorial/tools/verify_tutorial.mjs && node ../docs/tutorial/tools/build_pdf.mjs'
```

For a workstation that already has the harness dependencies and Playwright
browsers installed:

```sh
node docs/tutorial/tools/verify_tutorial.mjs
node docs/tutorial/tools/build_pdf.mjs
node docs/tutorial/tools/render_pdf.mjs
```

`SESSIONBUDDY_PLAYWRIGHT_MODULE` may name an alternate compatible Playwright
module, and `SESSIONBUDDY_TUTORIAL_OUTPUT` may set an alternate output path.
The build requires Poppler's `pdfinfo` and `pdftotext`; the render step requires
`pdftoppm`. When they are not on `PATH`, set `SESSIONBUDDY_PDFINFO`,
`SESSIONBUDDY_PDFTOTEXT`, and `SESSIONBUDDY_PDFTOPPM` to their executable paths.
`build_pdf.mjs` invokes `validate_pdf.mjs` automatically and fails rather than
leaving an unvalidated artifact.

The draft PDF is written to
`output/pdf/sessionbuddy-three-role-field-guide-design-draft.pdf`. Rendered page images,
`contact-sheet.html`, and `contact-sheet.png` are written below
`tmp/pdfs/sessionbuddy-tutorial/`.

## Capture contract

Use a clean local fixture, 1440 x 1000 viewport, and the role named in
`screenshots.json`. Crop to the task surface, keep navigation context, remove
tokens and private data, and update `captured_at`, `revision`, and
`recapture_required` after replacing an image.

For a release build, all figures must share the exact `required_revision` and
`fixture_version`, meet the pixel/DPI thresholds, have numbered callouts, and
set `recapture_required` to `false`. Set `coverage.json` `artifact_status` to
`RELEASE` only after those facts are true; the verifier fails any release with
stale evidence.

## Accessibility strategy

The semantic HTML is the primary accessible edition: landmarks, heading order,
real lists, descriptive links, image alt text, and visible text equivalents for
numbered callouts are maintained in source. The Chromium builder requests a
tagged PDF and heading outline/bookmarks. The release gate must confirm `/MarkInfo`
tagging, a non-empty structure tree, bookmarks, link annotations, text
extraction, and reading order. If the deployed Chromium cannot preserve those
features, publish HTML as the accessible edition and label the PDF as a visual
print companion rather than claiming PDF accessibility.
