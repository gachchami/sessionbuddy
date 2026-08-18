# SessionBuddy logo assets

The mark is a **timeline spine with three session rows**, where the live session
runs longest and at full strength. It says "schedule" without asking anyone to
decode a letterform — which matters, because an abstract `S` is exactly what the
unrelated *Session Buddy* browser extension would use, and we would rather not
invite the comparison.

## Source of truth

`svg/` holds the only hand-maintained artwork. Everything in `png/`, `ico/`, and
`pdf/` is generated from those masters:

```sh
uv sync --group logo
uv run python scripts/build_logo_assets.py           # regenerate
uv run python scripts/build_logo_assets.py --check   # fail if stale (CI)
```

Edit a master, re-run the script, commit both. Never hand-edit a generated file.

## Formats

- `svg/` — editable masters for product UI, print, and future exports.
- `png/` — transparent raster exports at common favicon, app-icon, and presentation sizes.
- `ico/` — multi-resolution browser favicon (16–256 px).
- `pdf/` — vector handoff files for print and presentation tools.

## Recommended files

| Use | File |
| --- | --- |
| Product navigation | `svg/sessionbuddy-lockup-color.svg` |
| Compact UI / app icon | `svg/sessionbuddy-icon-color.svg` |
| Browser favicon | `svg/sessionbuddy-favicon.svg` (this is the only asset the running product serves) |
| One-colour printing | `svg/sessionbuddy-lockup-mono.svg` |
| Dark backgrounds | `svg/sessionbuddy-lockup-reversed.svg` |

### What the product actually serves

Exactly one file: `/landing/assets/sessionbuddy-favicon.svg`, returned by
`sessionbuddy_favicon()` in `api/app.py` from a copy that lives at
`src/sessionbuddy/static/sessionbuddy-favicon.svg`. If you change the mark,
copy it across and re-run `scripts/embed_console_assets.py`, or the Worker will
keep serving the old artwork.

Everything else in this folder is handoff material — print, presentations, app
store listings, README embeds. Nothing serves it at runtime. In particular the
`.ico`, the 180 px Apple touch icon and the 192/512 px PWA icons are generated
but **not wired up**: there is no `apple-touch-icon` link, no web manifest, and
no `og:image` in any page head. Adding an iOS home-screen icon, an Android
install icon, or a link-preview image means linking those files, not
regenerating them.

## Clear space and minimum size

Keep clear space around the mark equal to one quarter of its height.

- Icon: **16 px or larger**. The 6-unit strokes and 6-unit gaps land on whole
  pixels at 16 px, which is why the rows stay open rather than filling in.
- Lockup: **150 px wide or larger**. Below that the wordmark's cap height drops
  under 11 px and the counters start to close.

Do not recolour individual strokes, stretch the artwork, add effects, or place
the colour icon on a competing violet background.

## Colour

The mark uses the product's own tokens, defined in
`src/sessionbuddy/static/app_shell.css` — it is deliberately not a standalone
brand palette.

| Token | Value | Role |
| --- | --- | --- |
| Purple | `#8B3FF5` | Tile gradient start |
| Sky | `#5D8CFF` | Tile gradient end |
| Violet | `#6D4AFF` | Shell accent, mark shadow |
| Row lavender | `#C9D3FF` | Inactive session rows |
| Ink | `#182230` | Wordmark, monochrome artwork |
| White | `#FFFFFF` | Spine and live session row |

The tile gradient is `135deg, #8B3FF5 → #5D8CFF`, matching
`.review-state__icon` and `.brand-mark` elsewhere in the product.

> **Known divergence:** the marketing site (`landing.css`) still runs a separate
> indigo palette (`#465BC6`, `#4F63C1`) with Georgia serif headings, and its hero
> glow is `rgba(75, 95, 202, …)` — derived from the retired `#4B5FCA` logo colour.
> The mark now matches the application shell rather than the marketing site.
> Converging the two is a deliberate brand decision that has not been made yet.

## Wordmark

Set in **Manrope ExtraBold** at -1.8% tracking, cap height 26 units against the
64-unit icon, with a 20-unit gap.

The wordmark is **converted to outlines** in every lockup master. Nothing
renders live text, so no font needs to be installed to build or display these
assets, and the lockup cannot silently fall back to a different typeface — which
is what the previous `font-family="Inter, Arial, sans-serif"` lockups did on any
machine without Inter.

Manrope is licensed under the [SIL Open Font License 1.1](https://github.com/sharanda/manrope/blob/master/LICENSE)
by Mikhail Sharanda. Outlines derived from an OFL font may be used in a logo;
the font software itself is not redistributed in this repository. If you set new
type in the brand typeface, use Manrope and outline it before committing.

## Product typography

Product UI uses the operating-system sans-serif stack declared by `--font-ui`.
Its fixed type scale is 12, 13, 14, 16, 18, 22, 28, 36, and 48 pixels. The
13-pixel step is reserved for dense operational metadata; ordinary body copy
uses 14 or 16 pixels, and interactive text inputs remain at least 16 pixels to
avoid mobile browser zoom. Supported weights are 400, 500, 600, 700, and 800.

## Using the mark

SessionBuddy's source is open, but the logo is the project's identity. Please:

- **Do** use the mark to link to, credit, or refer to SessionBuddy.
- **Do** use it unmodified, with the clear space above.
- **Don't** use it as the logo for your own project, product, or fork, or in a
  way that suggests the project endorses you.

If you fork SessionBuddy and ship it, replace the artwork in `svg/` with your
own and re-run the build script — everything else regenerates.
