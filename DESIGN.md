---
name: SessionBuddy
description: A clear, composed workspace for organizing events, reviewing proposals, and supporting speakers.
colors:
  primary: "#0969da"
  primary-hover: "#0757b8"
  primary-active: "#064a9e"
  on-primary: "#ffffff"
  canvas: "#f6f8fa"
  surface: "#ffffff"
  surface-hover: "#f0f3f6"
  surface-active: "#edf0f4"
  selected-surface: "#ddf4ff"
  selected-text: "#0550ae"
  text: "#1f2328"
  text-secondary: "#59636e"
  text-muted: "#656d76"
  border: "#d1d9e0"
  control-border: "#818b98"
  focus: "#0969da"
  disabled-surface: "#eff1f4"
  disabled-text: "#656d76"
  success-surface: "#dafbe1"
  success-text: "#116329"
  warning-surface: "#fff7df"
  warning-text: "#7a4d00"
  danger: "#b42318"
  danger-hover: "#912018"
  danger-active: "#781b14"
  danger-surface: "#fff1f0"
  danger-text: "#8f1d14"
  info-surface: "#ddf4ff"
  info-text: "#0550ae"
typography:
  page-title:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.02em"
  section-title:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: 1.4
  card-title:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  reading:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 500
    lineHeight: 1.5
  compact:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.5
  table-header:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 500
    lineHeight: 1.5
  metadata:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.5
  identifier:
    fontFamily: "ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.5
  marketing-display:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "3rem"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.03em"
rounded:
  small: "0.25rem"
  control: "0.375rem"
  panel: "0.5rem"
  dialog: "0.75rem"
  pill: "999px"
spacing:
  xs: "0.25rem"
  sm: "0.5rem"
  md: "0.75rem"
  lg: "1rem"
  xl: "1.5rem"
  xxl: "2rem"
  section: "3rem"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "0.5rem 1rem"
    height: "2.5rem"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
    textColor: "{colors.on-primary}"
  button-primary-active:
    backgroundColor: "{colors.primary-active}"
    textColor: "{colors.on-primary}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "0.5rem 1rem"
    height: "2.5rem"
  button-secondary-hover:
    backgroundColor: "{colors.surface-hover}"
    textColor: "{colors.text}"
  button-secondary-active:
    backgroundColor: "{colors.surface-active}"
    textColor: "{colors.text}"
  button-danger:
    backgroundColor: "{colors.danger}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "0.5rem 1rem"
    height: "2.5rem"
  button-danger-hover:
    backgroundColor: "{colors.danger-hover}"
    textColor: "{colors.on-primary}"
  button-danger-active:
    backgroundColor: "{colors.danger-active}"
    textColor: "{colors.on-primary}"
  control-disabled:
    backgroundColor: "{colors.disabled-surface}"
    textColor: "{colors.disabled-text}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0.5rem 0.75rem"
    height: "2.5rem"
  navigation-selected:
    backgroundColor: "{colors.selected-surface}"
    textColor: "{colors.selected-text}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    height: "2.5rem"
  badge-neutral:
    backgroundColor: "{colors.surface-hover}"
    textColor: "{colors.text-secondary}"
    typography: "{typography.compact}"
    rounded: "{rounded.small}"
    padding: "0.125rem 0.5rem"
  badge-success:
    backgroundColor: "{colors.success-surface}"
    textColor: "{colors.success-text}"
  badge-warning:
    backgroundColor: "{colors.warning-surface}"
    textColor: "{colors.warning-text}"
  badge-danger:
    backgroundColor: "{colors.danger-surface}"
    textColor: "{colors.danger-text}"
  badge-info:
    backgroundColor: "{colors.info-surface}"
    textColor: "{colors.info-text}"
  work-panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.panel}"
    padding: "{spacing.xl}"
---

# Design System: SessionBuddy

## Overview

SessionBuddy helps organizers manage events and proposals, reviewers assess
submissions, and speakers prepare their contributions. Visitors discover events
and published agendas. The interface should make the current event, next action,
and state of the work immediately clear.

Use a coherent light interface: white working surfaces, a quiet grey navigation
area, dark readable text, a blue action accent, and compact controls with modest
corners. Give the work more space and emphasis than the surrounding navigation.
Public pages share the same palette and font, with more generous composition.

This is SessionBuddy's visual specification, informed by Primer Product UI.
It does not require installing Primer or replacing the application's framework.
The exact values above are SessionBuddy decisions, not a copy of every Primer
token. Functional requirements and permissions remain in the product specification.

### How to use this document

- Frontmatter defines exact tokens; the sections below define their application.
  State variants inherit their base component's geometry and typography; colored
  badges inherit `badge-neutral` geometry and typography. Implement component
  `height` values as minimum heights, allowing wrapped labels and enlarged text.
- Apply shared changes through the existing theme and components. Inspect their
  consumers before renaming tokens. This file describes the target system; it
  does not assert that the running application already implements it.
- Treat a user's explicit reference or redesign instruction as direction to
  revise the relevant visual rules. Preserve this system during ordinary edits.
- A component refinement stays within its surrounding page. A page redesign may
  change grouping and proportions. When alternatives are requested, vary the
  content arrangement and hierarchy materially; compare the same real content.
- Preserve existing routes, permissions, content, data, and event configuration.
  The page patterns below guide supported features; they do not create new scope.
- Use real product evidence. Do not invent metrics, commercial claims, or an
  authenticated session for a public demonstration.

## Colors

### Role assignments

| Purpose | Tokens | Application |
|---|---|---|
| Page foundation | `canvas`, `surface` | Grey sidebar and quiet grouping areas; white header and main work area. |
| Content | `text`, `text-secondary`, `text-muted` | Titles and data; supporting explanations; incidental metadata. |
| Boundaries | `border`, `control-border` | Subtle layout dividers; stronger borders for inputs and outlined buttons. |
| Interaction | `primary`, `primary-hover`, `primary-active` | Primary buttons and links. Primary buttons are solid, with `on-primary` text. |
| Selection | `selected-surface`, `selected-text` | Active navigation and selected records, accompanied by a label, check, or selection indicator. |
| Neutral interaction | `surface-hover`, `surface-active` | Hover and press feedback for secondary, ghost, and ordinary row actions. |
| Keyboard focus | `focus` | A 2px outline with a 2px surface-colored gap. Keep it visible and unclipped. |

Most of a working screen is neutral. Use blue to indicate action or selection;
it may appear on several links without making every link a filled button.
Primary buttons have no gradient, glow, or colored shadow. Underline links in
prose; selected navigation uses its background, weight, and `aria-current`.

### Status and category meaning

| Meaning | Treatment | Examples, when present in the workflow |
|---|---|---|
| Draft or inactive | Neutral | Draft, not started, archived. |
| Normal progress or information | Info | Submitted, in review. |
| Successful completion | Success | Accepted, confirmed, published. |
| Attention required | Warning | Missing material, approaching deadline. |
| Error or blocking conflict | Danger | Save failed, invalid input, scheduling conflict. |
| Negative decision | Neutral with explicit wording | Rejected or declined is a decision, not a system failure. |

Use the matching `*-surface` and `*-text` pair. Words always communicate the
state. Organizer, Reviewer, and Speaker roles use neutral labels. Tracks may use
consistent category colors where they help interpret a schedule; include track
names or a legend and keep these colors separate from workflow statuses.

Configured event accents belong to published event pages. Scope them to that
event's presentation; they do not recolor organizer navigation, focus, or error
states. Validate accent/text contrast and choose a legible foreground or use the
accent decoratively when it cannot support readable controls.

Text must meet at least 4.5:1 against its actual background, including hover,
selection, helper text, and placeholders. This system uses that target even for
headings. Required control boundaries and focus indicators need at least 3:1
against adjacent surfaces. Decorative dividers may be subtler. Check configured
event themes separately; token checks do not certify the rendered application.

## Typography

Keep the locally bundled **DM Sans**. Load real weights 400, 500, and 600;
verify the font files support them. Use 400 for reading, 500 for controls and
brief emphasis, and 600 for headings. Do not apply medium weight to an entire
table, navigation list, or block of metadata.

| Role | Size / weight | Use |
|---|---|---|
| Page title | 24px / 600 | One clear heading for the current route. |
| Section title | 18px / 600 | Major sections within the page. |
| Card or detail title | 16px / 600 | A grouped object's name or a small subsection. |
| Body | 14px / 400 | Tables, navigation, descriptions, ordinary interface text. |
| Reading | 16px / 400 | Abstracts, longer instructions, speaker biographies. |
| Label | 14px / 500 | Field labels, buttons, short emphasis. |
| Compact | 13px / 400 | Badges and secondary row details. |
| Table header | 13px / 500 | Column labels. |
| Metadata | 12px / 400 | Incidental timestamps and counts, never critical instructions. |

Use sentence case, natural tracking for interface text, and tabular numerals for
aligned dates, times, scores, and counts. Schedule times use DM Sans, not a
decorative machine style. Reserve monospace for identifiers and technical values.
Constrain prose to 65ch. A table's primary record title may use weight 500;
supporting cells stay regular. Allow natural row growth for longer content.

Public display type is 48px / 600 at 64rem and above, 36px / 600 from 40rem,
and 28px / 600 below 40rem. Keep its 1.15 line height. Public body copy uses
`reading`. Display type never appears in workspace forms or navigation.

Use the existing permitted size set when an additional size is necessary:
12, 13, 14, 16, 18, 20, 22, 24, 28, 36, and 48px, expressed in rem. Do not add
sizes simply to differentiate adjacent labels. If a typography gate exists,
inspect it alongside the implementation; do not claim it passed without running it.

## Layout

### A stable workspace shell

Use an attached sidebar and a single header. The sidebar has a quiet canvas
background and a right divider; the main work area and header use `surface`.
Keep the shell's outer corners square. Do not enclose the entire page, header,
or sidebar in separate floating cards.

| Element | Rule |
|---|---|
| Desktop sidebar | 14.5rem wide on every workspace route; independently scrollable if needed. |
| Header | Minimum 4rem high; event context and global utilities. Let wrapped content increase its height. |
| Page gutter | 1.5rem desktop; 1rem below 52rem; up to 2rem at 90rem and above. |
| Heading block | Page title, optional short explanation, and task actions; 1.5rem before the work. |
| Related controls | 0.5rem gap; 1rem between control groups. |
| Content sections | 1.5–2rem between sections; 0.5rem between a label and its content. |
| Table workspace | Available width, up to 90rem; prioritize useful columns over decorative margins. |
| Form | Up to 48rem; align with the page heading. |
| Overview or detail | Up to 76rem; a contextual column is optional. |
| Reading content | Up to 65ch within its containing layout. |

Keep header and content in a shared layout flow. Sticky elements use measured
shared offsets; avoid per-route spacer heights. Put alerts below the header in
normal flow. They must not push content behind a fixed element.

### Visual balance

The main task occupies the largest useful area. At desktop sizes, an optional
context column is 18–22rem wide and the main column takes the remaining space.
Use it only if at least 36rem remains for the work; otherwise stack it below.
Align short context panels to the top. Do not stretch them to mimic a tall table.

Maintain a clear reading order: page heading, relevant action or alert, working
content, supporting detail. Use shared left edges and consistent gutters. A
large bold number, colored block, or banner must earn its prominence through
task importance. Do not give every summary metric the same area and emphasis.
Use whitespace and dividers for grouping before adding another panel.

### Page patterns

| Surface | Main composition | Supporting context |
|---|---|---|
| Organizer home | Event list and actionable work; small factual summary where useful. | Upcoming deadlines or recent activity. |
| Event overview | Event identity and stage, then tasks requiring attention and relevant records. | Schedule milestones or publishing readiness. |
| Proposals / submissions | Title and action; optional status tabs; filter toolbar; one data table; pagination. | Open a selected proposal in a detail pane or its own route. |
| People / speakers | Searchable list with name, relevant role, and actionable completion state. | Biography, contact details, and related sessions in the detail view. |
| Reviewer | Proposal title, abstract, and review material as the primary reading column. | A clearly grouped score and comment form; respect any blind-review restrictions. |
| Event / proposal editor | Labeled form sections with concise help and validation. | Optional preview or contextual help when room permits. |
| Agenda | Day and track controls, unscheduled items, then the time-by-track working grid. | Selection details and explicit conflicts. |
| Speaker portal | Event context, deadlines, assigned tasks, and submission details. | Only navigation and actions available to that speaker. |
| Published event | Event identity, program, speaker details, and clear dates/time zone. | Event-specific accent and public navigation. |

Prefer one primary action in each active task area. A modal or a focused review
form can have its own primary action; subordinate or hide the underlying page
action while that context is active. Do not manufacture an action solely to fill
an empty corner of the heading.

### Responsive behavior

Below 52rem, use a dismissible navigation drawer with a visible close control,
focus containment, Escape support, and focus restoration. Reuse the established
40rem, 52rem, and 64rem stops for new layout work; retain other existing stops
where behavior depends on them. Avoid adding nearly identical breakpoints.

Stack heading actions and contextual columns when they stop fitting. Toolbars
wrap into intentional groups. Inputs use 16px text below 64rem. Touch controls
have at least a 44px target, even if a compact desktop counterpart is smaller.
Preserve text resizing and zoom; heights below are minimums, not clipping boxes.

Tables and schedule grids may scroll horizontally inside labeled regions; the
page itself must not overflow. Keep the primary record identifier visible where
practical and provide essential row actions without hover. On narrow schedules,
offer a day/track list presentation when supported; preserve time and track
context. Never shrink labels until a desktop grid fits a phone.

## Elevation & Depth

Use borders and spacing for resting surfaces. A normal row, panel, or button has
no shadow. Use a shadow when an element overlays other content:

- Menus and popovers: `0 4px 12px rgba(31,35,40,0.12)`.
- Dialogs and drawers: `0 12px 32px rgba(31,35,40,0.18)`.
- Modal backdrop: `rgba(31,35,40,0.40)`.

Keep overlays above sticky chrome. Reuse the application's layering tokens.
Hover feedback changes background or border; it does not lift rows or make
records shift position. Selected rows keep their selected treatment on hover.

## Shapes

Use 4px corners for badges, 6px for controls, 8px for grouped panels, and 12px
for dialogs. Pill rounding is reserved for avatars and compact count capsules.
Buttons are modest rectangles. All token values are defined in `rounded`.

One border belongs to one group. A table can have one enclosing border; its
rows use separators. A record inside a panel usually needs spacing or a divider,
not another rounded container. Real images remain image assets.

## Components

### Buttons and controls

The shared button base is neutral. **Primary styling requires an explicit
variant.** Secondary buttons use `control-border`; ghost buttons are transparent
with `text-secondary`, and use the neutral hover/active surfaces. Use the same
states for icon buttons. Provide an accessible label for every icon-only action.
Reuse one existing icon family: 16px within controls and 20px for navigation.
Keep icon weight consistent and let labels carry the meaning.

Desktop controls are at least 40px high with 14px / 500 labels. A 32px compact
variant is available for dense toolbars on fine-pointer devices. Both use 6px
corners. Reserve filled danger buttons for the final action in a destructive
confirmation. Use a neutral or danger-text action to open that confirmation.

Focus is independent of hover and remains visible on selected or pressed
controls. Disabled controls use the disabled pair and the appropriate disabled
semantics; retain a readable explanation of unavailable actions. Loading actions
keep their width and label context, expose a busy state, and prevent duplicates.

### Tables, lists, and filters

- Default rows have a minimum height of 48px and 14px / 400 text. Use at least
  64px for two-line records. A compact 40px row retains 14px primary text.
- Headers are at least 40px high, with 13px / 500 text, a quiet canvas background,
  and a bottom divider. Use 12–16px horizontal cell padding consistently.
- Allocate the most width to proposal titles or person names. Align text left
  and comparable numeric values right. Keep selection and action columns narrow.
- Allow important titles to wrap. If a compact view truncates a title, make its
  full value available through a keyboard-accessible detail link or disclosure.
- Use neutral hover feedback and the selected surface with checked selection
  controls. Sorting exposes direction and accessible table semantics.
- Keep search, filters, active-filter count, and clear/reset near the table.
  Preserve filter, sort, selection, and pagination state when opening a record.
- On selection, show bulk actions in the same toolbar region with a count and
  clear selection. Explicitly distinguish this page from all filtered results.
- Provide row links and actions as separate valid controls. Do not make a
  clickable row intercept its own checkboxes, links, or menus.

### Forms and review panels

Use visible labels, 8px between label and field, and 24px between field groups.
Keep complex or long fields in one column. Pair short related fields only where
their labels and errors fit. Inputs have a 1px `control-border`; helper and
placeholder text use `text-secondary`. Textareas grow beyond a single-line
control's minimum height.

Preserve entered values after errors. Associate inline errors with their fields,
set `aria-invalid`, and show a linked error summary for long failed forms. Make
required and optional expectations clear without relying on color. Save status
uses words such as Saving, Saved, or Could not save, backed by real state.

Keep the main save/submit action near the end of the work; a sticky action bar
is appropriate for long forms if it cannot cover fields or validation messages.
Review scoring follows the existing rubric and permissions. Do not hide the
abstract beneath oversized author imagery or present every field as a card.

### Scheduling

Use an explicit time axis and track headers. State the displayed time zone and
the event date. Keep times and track names visible during scrolling where
possible. Session blocks emphasize title, then speaker and timing. Their size
must follow the schedule's time scale, not a decorative uniform-card grid.

Show conflicts with text or an icon and the danger treatment. Keep selection,
category color, and conflict indicators distinguishable. Support a keyboard
path to assign or move sessions; dragging cannot be the only interaction. Check
overlaps, long titles, empty tracks, and partially filled schedules.

### Navigation, panels, and states

Navigation labels use body text; the current item uses the selected component.
Event identity and switching remain in the same place across routes. Do not
surface organizer actions to visitors, reviewers, or speakers without permission.

Use a work panel for a distinct grouped task or summary. Its default padding is
24px, reduced to 16px on small screens. Table panels use table cell padding and
do not add that padding around the entire grid.

Distinguish an empty collection from no filter matches. Offer the relevant next
action or reset. Loading preserves the eventual structure; errors offer a clear
recovery action. Announce asynchronous status changes appropriately without
moving focus unexpectedly. Dialogs have a clear heading, named controls, focus
containment and restoration, and an appropriate close or cancel path.

### Public pages and product film

Public layouts share the workspace's font, neutral foundation, and blue action
accent. Use 48–64px section spacing on desktop and 32px on mobile. Keep content
within 76rem and use 16px gutters on mobile, 24–32px otherwise. Give product
screens room to explain the service; avoid applying these display proportions
to work pages. Event-configured accents remain scoped as described above.

Preserve the existing product film: six real captured screens in the sequence
workspace → event → call for proposals → proposals → review → agenda. Keep its
24-second loop, four seconds per screen, overlapping fades, stable shell, and
WebP data-URI assets. Maintain the first screen for `prefers-reduced-motion`.
Include a visible pause/play control for the automatic loop. Use appropriate
alternative text; screenshots do not replace essential explanatory content.

This film is the public page's single decorative loop. Workspace motion only
communicates interaction or state. Use 120–180ms feedback transitions and a
200ms drawer transition; suppress nonessential movement for reduced motion.

## Do's and Don'ts

### Implementation priorities

1. Read the relevant source, existing tokens, component variants, and layout
   contracts before editing. Map old token consumers to the semantic roles above;
   do not leave unresolved variables or maintain two competing active palettes.
2. Apply the palette and neutral button base through shared components. Then
   establish the shell, type weights, and spacing before refining individual pages.
3. Use one representative proposals table, reviewer page, and speaker form to
   check the system. Keep their real content and behavior. If a reference is
   supplied, compare against its actual product screens at matching dimensions.
4. Update related components together. Inspect any existing typography and
   release-readiness gates; do not silently weaken them to accommodate a change.

### Visual acceptance

- Inspect representative desktop and mobile renders, plus the widths where the
  sidebar, toolbar, or context column changes arrangement. Use realistic long
  titles, multiple statuses, errors, and empty results.
- At a small grayscale preview, the page title and main work remain distinct;
  navigation, repeated badges, and supporting activity do not dominate.
- Headers, content, and controls align consistently across routes. There are no
  accidental empty columns, stretched short panels, clipped focus rings, covered
  controls, or page-level horizontal scroll.
- Verify contrast on actual rendered backgrounds and all interactive states.
  Confirm keyboard access, zoom/reflow, mobile input sizing, and reduced motion.
- If alternatives were requested, they must differ in composition or hierarchy
  while preserving the task. Reject alternatives whose only changes are hue,
  corner radius, or decoration. A preferred reference takes priority over novelty.
- Compare the result with the starting screen. Fix the largest visible mismatch
  first. Report unverified behavior or missing reference assets plainly; a valid
  Markdown file alone is not evidence of a successful interface.

### Avoid

- Automatic primary styling on bare buttons, gradients on action buttons, and
  filled color on every badge, metric, or navigation item.
- One rounded container inside another simply to create visual separation.
- Weight 500 or 600 across whole tables, all-caps machine labels for ordinary
  navigation, or oversized display headings inside working screens.
- Moving the sidebar or changing its width between organizer routes.
- Generic summary-card grids that push the actual work below the first screen.
- Requiring hover to discover essential actions, hiding state in color alone,
  or discarding user input after validation fails.

### References

Design principles and component behavior can be checked against
[Primer Product UI](https://primer.style/product/), including its
[color guidance](https://primer.style/product/getting-started/foundations/color-usage/),
[layout guidance](https://primer.style/product/getting-started/foundations/layout/),
[typography guidance](https://primer.style/product/getting-started/foundations/typography/),
[tables](https://primer.style/product/components/data-table/), and
[forms](https://primer.style/product/ui-patterns/forms/).
Accessibility targets reference
[WCAG text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html),
[non-text contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html),
and [motion controls](https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html).
