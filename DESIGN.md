---
name: SessionBuddy
description: Reference-led conference workspaces with detached white panels and restrained blue actions.
colors:
  primary: "#225c9e"
  primary-hover: "#184d89"
  action-top: "#4c78a5"
  action-bottom: "#18559b"
  canvas: "#f4f7f9"
  surface: "#ffffff"
  selected: "#e8eff6"
  ink: "#182230"
  secondary-text: "#475467"
  muted-text: "#596570"
  border: "#e1e6ea"
  strong-border: "#d0d5dd"
  focus: "#2563eb"
  success-surface: "#edf9f2"
  success-text: "#17603f"
  warning-surface: "#fff8eb"
  warning-text: "#8a4800"
  danger-surface: "#fff1ef"
  danger-text: "#7a271a"
typography:
  page-title:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-.025em"
  section-title:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-.02em"
  body:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
  compact:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: ".875rem"
    fontWeight: 500
    lineHeight: 1.5
  metadata:
    fontFamily: "DM Sans, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: ".75rem"
    fontWeight: 500
    lineHeight: 1.45
rounded:
  field: ".5rem"
  inset-panel: ".85rem"
  panel: "1rem"
  pill: "99rem"
spacing:
  field: ".5rem"
  group: "1rem"
  workspace-gap: "1.25rem"
  panel: "1.5rem"
  section: "2rem"
components:
  button-primary:
    textColor: "{colors.surface}"
    rounded: "{rounded.pill}"
    padding: ".65rem 1.15rem"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.secondary-text}"
    rounded: "{rounded.pill}"
    padding: ".65rem 1.15rem"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.field}"
    padding: ".68rem .75rem"
  navigation-selected:
    backgroundColor: "{colors.selected}"
    textColor: "{colors.primary}"
    rounded: "{rounded.field}"
  chip:
    backgroundColor: "{colors.selected}"
    textColor: "#22558b"
    rounded: "{rounded.pill}"
    padding: ".28rem .6rem"
  work-panel:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.panel}"
    padding: "clamp(1rem, 2vw, 1.5rem)"
---

# Design System: SessionBuddy

## Overview

Purpose: record the implemented visual vocabulary for consistent interface work.
Lifecycle: in-progress. Authority: repository instructions and executable
contracts govern behavior; the approved TaskFlow reference governs visual
direction. This record describes a local implementation under verification,
not a deployment or release approval. `docs/product-status.md` owns capability
and release status.

**Creative North Star: "TaskFlow's detached workspace"**

SessionBuddy adapts the approved [TaskFlow calendar dashboard](https://dribbble.com/shots/27112768-TaskFlow-Team-Calendar-Event-Management-Dashboard-SaaS-Web)
through pale blue canvas, detached white navigation and header, rounded work
panels, compact sans-serif type, and tactile blue primary actions. The reference
is a visual authority, not permission to invent calendars, meetings, statistics,
or commercial features.

The system serves real organizer, reviewer, speaker, and visitor tasks. Public
and account-entry surfaces share its material and typography without fabricating
an authenticated workspace. Source styles remain authoritative for specialized
component details not captured by this reusable subset.

Key characteristics:

- Detached white surfaces separated by cool canvas.
- Compact, medium-weight navigation and headings.
- Blue selection and primary actions; pastel supporting states.
- Existing content and truthful state before decoration.

## Colors

### Primary

Workspace Blue identifies primary actions and selected navigation. Its deeper
hover tone maintains the same role. The action-top and action-bottom primitives
form the vertical primary-button gradient; white text uses the darker endpoint
range required for readable contrast.

### Neutral

Cool Canvas separates independent white surfaces. Ink carries primary content;
secondary and muted text retain hierarchy without treating essential information
as disabled. Hairline borders define panels and stronger borders identify fields.

### States

Success, warning, and danger pair pale surfaces with darker semantic text.
Discovery and other supporting records may use quiet blue, lavender, and green
backgrounds for visual grouping; those tints alone are not status indicators.
Organizer-home activity instead uses neutral timeline markers on white, without
pastel record cards.

**The State Has Words Rule.** Color supports a label or value; it never replaces
the text that explains a state. Published event surfaces may inherit the event's
configured accent rather than the workspace blue.

## Typography

DM Sans is locally bundled and shared across the interface. Platform sans-serif
fallbacks preserve usability while it loads. The frontmatter captures reusable
page, section, body, compact, and metadata roles; individual views may use nearby
established sizes for their specific hierarchy.

Operating headings stay compact. The landing headline has a larger desktop
role, with a compact mobile treatment, but product forms and navigation do not
inherit marketing scale. Paragraph measures generally stop at 65 characters;
tables and working grids can occupy the available width. Dates, counts, and
scores use tabular numerals. Monospace is reserved for code, identifiers, and
specific measured data, not the general interface voice.

Organizer home uses semibold (600) event titles (1.125rem / 18px) on desktop and
mobile, and medium-weight panel titles (20px). Event locations, dates, and
activity timestamps use .8125rem / 13px with 1.4 line height. Activity copy uses
.875rem / 14px with 1.5 line height, subordinate to the event titles. Dates and
readiness counts use tabular numerals.
Its topbar title is 1.5rem on desktop and 1.25rem on mobile; these
home-specific roles do not replace the shared page-title or metadata tokens.

## Layout

Authenticated navigation occupies a detached sidebar (14.5rem); the detached
header has a minimum height of 4.25rem. Desktop frame gaps are 1.25rem. Main
content clears the header using shared chrome-height variables, including the
extra offset required by sticky form controls.

Organizer home overrides the shared frame with a 13rem (208px) sidebar whose
brand and navigation are separate white panels. Its 5rem (80px) topbar holds
the page title and live attention summary above the work panel; on mobile,
the topbar grows to 7rem (112px) so attention remains visible. Organization
search and switching remain in the navigation panel even with one organization.
Narrower screens also expose a select control in the event panel. Status fills
remain compact inside 44px interactive targets; event details use tighter local
grouping than the space separating event rows.
At 40rem (640px) and below, Sort options becomes a native disclosure beside
event search; its select opens in a bounded popover. Above 640px the sort select
stays visible. Mobile event rows stack identity, location, date, next action,
and workflow links; the next action expands without hiding the secondary tools.

Organizer home, event overview, editor, and agenda can place main work beside
supporting context in a 2.2:1 grid where their content supports it. This is not a
requirement for every route: long tables, forms, and public reading surfaces use
their own appropriate composition. Short work panels align to the top rather
than stretching to a long neighboring activity list.

The sidebar becomes a dismissible drawer at 52rem, with a smaller outer gap.
Other capability layouts stack at content-specific breakpoints, including 76rem
for other organizer work/context arrangements, 70rem for organizer home's
work/activity grid, and 48rem for several form and table
layouts. Mobile tables either adapt their rows or scroll inside their own
focusable containers; headers remain available to assistive technology.

## Elevation & Depth

White panels use borders and canvas separation rather than ambient shadows.
Primary buttons deliberately retain the reference's white rim and offset soft
shadow (`0 .45rem .8rem rgba(21, 42, 84, .16)`). Menus, dialogs, and transient
feedback retain functional elevation appropriate to their layer.

**The Work Stays Still Rule.** Motion communicates interaction or state, not a
page-load performance. Shared controls use short transitions (180ms); the
navigation drawer uses 200ms. Reduced-motion rules disable nonessential movement.
An existing progress-width transition represents actual completion state; it is
not a reusable decorative animation prescription.

## Shapes

Independent panels have gently rounded corners; inset discovery and supporting
records outside organizer home's neutral timeline use the smaller panel shape.
Pill silhouettes belong to primary and
secondary actions and compact state chips. Fields retain modest corners and
visible strokes. Avatars and time markers can be circular, while photographs
remain real images rather than CSS approximations.

## Components

### Buttons

Primary actions use the vertical blue gradient, white rim, soft offset shadow,
medium-weight text, and a minimum 2.75rem target height. Hover deepens the blue;
press shifts the control by one pixel. Secondary actions are white with a
stronger border and no primary-action shadow. Danger and text-only controls
retain distinct semantics. Focus remains visible and disabled states do not
claim an action is available.

### Navigation

Rows occupy the full available sidebar width. Selection is a muted blue fill
with blue text, not a thin decorative edge. Icons share the existing outlined
stroke language. The same authorized destinations feed desktop navigation and
the mobile drawer; incomplete onboarding may correctly have no sidebar.

### Panels and records

Work panels carry a hairline border, white surface, and responsive internal
padding. Pastel call records organize real supporting content.
Avoid nesting extra panels merely to manufacture visual complexity.
Organizer home's activity panel previews up to four real records and retains
the route to the full activity view. Its records have transparent backgrounds,
neutral circular markers, and a hairline connector rather than inset cards.
When the work/activity grid stacks at 70rem, activity uses two columns without
connectors; at 40rem it becomes one column. Event actions, readiness links, filters,
and activity navigation use targets at least 2.75rem (44px) high.

### Organizer-home event workbench

Compact event rows use no decorative date tile. The semantic full date remains
alongside the location, without a left metadata indent. Rows have .75rem (12px)
vertical and 1.25rem (20px) horizontal padding; horizontal padding reduces to
16px at 40rem. Panel headings use content-driven height, with 8px mobile gaps.
The home grid prioritizes event width at 2.8:1 with a 16rem activity minimum.
Panel headings, filters, and event content share the same horizontal inset.
Readiness details keep a consistent minimum line height whether linked or plain
text. Only the row boundary has a divider; proximity groups facts and actions.
The linked title carries one compact event-status chip. CFP and agenda details
use dark green for ready and amber for setup-needed text, underlined when they
link to a published page, rather than
competing colored badges. Location and full date are
secondary and adjacent on desktop. Selected filters use the same pale blue as organization selection.

Each row has one next action: Review the actual pending proposal count for a
non-archived event with pending reviews, otherwise Open event. Both use a
compact 13rem action column shared across desktop rows, centered vertically
against the left information block. Mobile keeps actions after the information.
The review action uses a
solid blue attention treatment; Open event is outlined. Manage CFP, Manage agenda,
Speakers, Reviewers, Manage and eligible Clone actions sit inside the native More
disclosure, with a bounded scrollable menu. Readiness precedes actions in DOM and
mobile reading order. Escape closes the disclosure and
returns focus to its summary. Mobile Sort options has the same Escape/focus
behavior. Preserve organization search and switching even with one organization.

### Fields and chips

Fields use white surfaces, stronger strokes, readable placeholders, and blue
focus indication. Chips are compact and explicitly labeled. Required,
validation, loading, saved, and error states preserve their existing behavior.

## Do's and Don'ts

- Do preserve the reference's detached panels, compact type, and blue actions.
- Do keep authorization, keyboard access, and truthful saved/error states intact.
- Do verify text contrast and desktop/mobile layout against rendered content.
- Do retain meaningful labels and semantic table headers when changing layout.
- Don't invent product data or features to resemble the reference screenshot.
- Don't use gradients as a substitute for content or decorate ordinary panels.
- Don't assume a detector finding proves a visible defect: hidden image slots
  can await real data, and existing semantic status accents require contextual
  review. Conversely, this record does not certify a clean detector or release.
- Don't treat a source-level design record as proof of deployed behavior.
