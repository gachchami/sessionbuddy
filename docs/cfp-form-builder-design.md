# CFP form builder: conditional question groups

Status: proposed design  
Scope: CFP authoring, published form schema, speaker submission UI, validation, and review display  
Audience: product, design, frontend, API, persistence, and test owners

## Decision

SessionBuddy should model a CFP as an ordered tree of **question groups** and
**questions**, not as one flat list of fields. A group may be unconditional or
may be gated by answers to earlier choice questions. A question may also have
its own display rule, but the normal authoring path is to put related follow-up
questions in one conditional group.

The primary example is:

```text
Proposal basics
  Session format: [Presentation, Workshop]

When Session format is Presentation
  Presentation details
    Audience level
    Learning outcomes
    Demo requirements

When Session format is Workshop
  Workshop details
    Workshop duration
    Prerequisites
    Room setup
    Maximum participants
```

This produces one CFP with branches rather than separate, drifting forms for
each session format.

## Reference and adaptation

Sessionboard's current form-builder documentation establishes several useful
patterns:

- an ordered builder with distinct setup, session information, participant
  information, settings, and notification steps;
- reusable fields plus form-specific labels, placeholders, help text, and
  required state;
- section headers, rich text, and dividers as first-class layout elements;
- question rules that show a target field from an earlier answer;
- choice-driven conditional limits and a preview before publishing.

References:

- [Building Your Submission Form](https://learn.sessionboard.com/en/knowledge-base/building-your-submission-form)
- [Does Sessionboard offer conditional logic?](https://learn.sessionboard.com/en/knowledge-base/8103110-does-sessionboard-offer-conditional-logic)
- [Sessionboard Call for Papers](https://www.sessionboard.com/products/call-for-papers)

SessionBuddy should adopt the clarity of those patterns without copying the
interface. The important extension is that a rule can target an entire group,
not only one field. That is the cleanest way to express “Workshop questions”
versus “Presentation questions.”

## What is wrong with the current builder

The current schema is flat:

```text
fields[]
conditions[]: source_key + operator + value + target_key
```

The builder attaches a single optional display rule to each custom question.
This creates several problems:

- five workshop questions require five repeated rules;
- related questions have no shared title or instructions;
- the organizer cannot see the branch structure at a glance;
- moving, deleting, or changing the source choice can invalidate many hidden
  field-level rules;
- “custom question” and “layout section” are incorrectly treated as unrelated
  concepts;
- the speaker form is visually flat even when its logic is not;
- the same concept is implemented separately in builder, speaker, organizer,
  and reviewer rendering.

## Authoring model

### 1. Groups are first-class nodes

Every question belongs to exactly one group. A group contains:

- internal name;
- speaker-facing heading;
- optional rich-text instructions;
- ordered child questions;
- optional display rule set;
- stable opaque ID;
- display settings such as start-collapsed in preview only.

System identity questions and proposal basics live in locked system groups.
Organizers can add, rename, reorder, duplicate, and delete custom groups.

### 2. Questions remain reusable definitions

A question definition owns its stable data meaning:

- key and stable ID;
- answer type;
- choice definitions;
- validation constraints;
- review visibility and sensitive-data classification.

Its placement in a form owns presentation:

- group;
- order;
- label override;
- placeholder;
- help text;
- required state;
- optional question-level display rule.

Custom questions may use all supported answer types, including:

- short text;
- long text;
- email, URL, and phone;
- single choice;
- multiple choice;
- checkbox;
- number;
- file;
- image.

Single choice, multiple choice, and checkbox questions can be rule sources.
Number questions may be added later with numeric comparison operators.

### 3. Choices have stable IDs

Rules must reference a choice ID, never its visible label. Renaming “Workshop”
to “Hands-on workshop” must not break its branch.

```json
{
  "id": "choice_workshop",
  "value": "workshop",
  "label": "Workshop"
}
```

The public answer payload stores the stable value. Labels are display data.

### 4. Rules target groups or questions

A rule set has one target and one or more predicates:

```json
{
  "id": "rule_workshop_group",
  "target": {"kind": "group", "id": "group_workshop"},
  "effect": "show",
  "match": "all",
  "predicates": [
    {
      "source_question_id": "question_session_format",
      "operator": "includes",
      "choice_id": "choice_workshop"
    }
  ]
}
```

Initial operators:

| Source type | Operators |
|---|---|
| Single choice | is, is not |
| Multiple choice | includes, does not include |
| Checkbox | is checked, is not checked |

The schema supports `match: all` and `match: any`, although the first UI may
ship with one predicate and “all” as the default. Multiple matching rule sets
for one target are ORed. Predicates inside a rule set follow its `match` mode.

### 5. Required means required when active

A required question is validated only when its group and its own rule are
active. Inactive answers are excluded from submission validation and upload
validation. The server is authoritative and uses the same condition evaluator
as the browser.

When a previously visible branch becomes hidden, its answers remain in the
local draft for easy recovery but are omitted from save/submit payloads. The UI
must clearly state this behavior. Published submission snapshots retain only
active answers.

## Proposed published schema

The canonical form document should move to a versioned node model:

```json
{
  "schema_version": 2,
  "groups": [
    {
      "id": "group_basics",
      "key": "proposal_basics",
      "title": "Proposal basics",
      "description_html": "",
      "question_ids": ["question_title", "question_format"]
    },
    {
      "id": "group_workshop",
      "key": "workshop_details",
      "title": "Workshop details",
      "description_html": "Tell us what attendees need for the hands-on session.",
      "question_ids": ["question_prerequisites", "question_room_setup"]
    }
  ],
  "questions": [
    {
      "id": "question_session_format",
      "key": "session_type",
      "type": "select",
      "label": "Session format",
      "required": true,
      "choices": [
        {"id": "choice_presentation", "value": "presentation", "label": "Presentation"},
        {"id": "choice_workshop", "value": "workshop", "label": "Workshop"}
      ]
    }
  ],
  "rule_sets": [
    {
      "id": "rule_workshop_group",
      "target": {"kind": "group", "id": "group_workshop"},
      "effect": "show",
      "match": "all",
      "predicates": [
        {
          "source_question_id": "question_session_format",
          "operator": "includes",
          "choice_id": "choice_workshop"
        }
      ]
    }
  ]
}
```

The published schema is immutable by version. Draft edits produce a new schema
version. Existing submissions retain the schema version against which they
were submitted.

## Builder information architecture

The builder should show form structure, not a catalog of disconnected fields.

```text
+-----------------------------------------------------------------------+
| Call for Proposals                         Preview       Publish       |
+----------------------+------------------------------------------------+
| FORM OUTLINE         | Proposal form                                  |
|                      |                                                |
| ▾ Proposal basics    |  [Group: Proposal basics]           ···        |
|   Title              |  Always shown                                  |
|   Session format     |   ┌ Session format ───────────────────────┐    |
|                      |   │ Single choice · Required              │    |
| ▾ Presentation       |   └───────────────────────────────────────┘    |
|   Audience level     |                                                |
|   Learning outcomes  |  [Group: Workshop details]          ···        |
|                      |  Shown when Session format is Workshop         |
| ▾ Workshop details   |   ┌ Workshop prerequisites ──────────────┐    |
|   Prerequisites      |   └───────────────────────────────────────┘    |
|   Room setup         |                                                |
|                      |  + Add question     + Add group                |
+----------------------+------------------------------------------------+
```

### Core interactions

- `+ Add question` opens one menu: reuse question or create custom question.
- `+ Add group` creates a visible section with optional instructions.
- Drag handles reorder groups and questions; keyboard move controls provide the
  equivalent accessible interaction.
- Dropping a question into a group changes only its placement.
- `Add display rule` is available on both a group and a question.
- A rule summary is always visible on a gated group: “Shown when Session
  format is Workshop.” The organizer does not need to open an advanced panel
  to understand the branch.
- Selecting a choice question offers a shortcut: `Create a group for each
  choice`. This creates empty gated groups for Workshop, Presentation, and any
  other choices in one action.
- A preview switcher lets the organizer choose simulated answers and see the
  exact branch without publishing.

### Question editor

The editor uses plain language:

```text
Question                 Workshop prerequisites
Answer format            Long text
Required                 On
Placeholder              e.g. Python 3.12 and a laptop
Help text                What should attendees install beforehand?
Review visibility        Visible in blind review
```

For choice questions, choices are rows rather than a comma-separated text
field. Each row has label, stable value, drag handle, and remove action. This
prevents commas in labels, makes reordering clear, and gives rules stable
choice references.

### Rule editor

```text
Show this group when
  [Session format] [is] [Workshop]
  + Add condition

Match [all] conditions
```

The editor only offers earlier source questions. Unsupported source types do
not appear. If an existing dependency becomes invalid, the raw rule remains
visible with “Needs repair”; saving and publishing are blocked until it is
repaired or explicitly removed.

## Shared rendering architecture

One pure form engine should own:

- schema parsing;
- rule evaluation;
- active group/question calculation;
- answer formatting;
- validation;
- upload-field activation;
- accessible group and question rendering.

Surfaces configure the engine with a mode:

| Surface | Mode | Allowed actions |
|---|---|---|
| Public CFP | create | answer, save draft, submit |
| Speaker proposal | owner edit/read | edit while allowed, withdraw when allowed |
| Organizer proposal | read | view only; create starts at CFP |
| Reviewer assignment | review | view proposal, edit only review fields |

The organizer and reviewer do not receive disabled edit forms. They receive a
read-only semantic rendering of the same schema and answer data. Server routes
still enforce every mutation boundary; a rendering mode is not authorization.

## Server invariants

- A rule source must precede its target group/question.
- The dependency graph must be acyclic.
- Rule source, target, and choice IDs must exist in the same schema version.
- Only supported answer types may be rule sources.
- System identity fields cannot be conditional.
- A question belongs to exactly one group.
- Group keys and question keys are unique.
- Hidden required fields never fail validation.
- Hidden file/image fields never require or accept upload references.
- Unknown answer keys and answers for inactive questions are rejected or
  normalized away according to one documented API policy.
- Organizer proposal APIs are read-only; organizer-authored creation goes
  through the published CFP contract.
- Reviewers can access only assigned proposals and mutate only their review.

## Accessibility and responsive behavior

- Groups render as `fieldset`/`legend` where appropriate, with headings for
  purely presentational sections.
- Showing a new branch announces its heading once through a polite live region;
  focus does not jump automatically.
- Hiding a branch containing focus returns focus to the controlling question.
- Rule summaries are text, not color-only badges.
- Drag-and-drop has Move up, Move down, Move to group, and screen-reader status
  equivalents.
- At phone widths the outline becomes a compact “Form structure” drawer; the
  active group remains in one column with a persistent Add action.
- Preview supports keyboard selection of simulated answers and reduced motion.

## Failure states

Publishing is blocked with direct repair links when:

- a rule references a removed question or choice;
- a source appears after its target;
- a dependency cycle exists;
- a choice question has fewer than two choices;
- a group is empty and has no explanatory content;
- a required system question was removed or changed to an incompatible type.

Changing or deleting a choice that gates a group requires confirmation:

```text
“Workshop” controls 1 group and 4 questions.
Choose a replacement value or remove the display rule before deleting it.
```

No autosave may silently delete a rule.

## Delivery sequence

1. Introduce schema v2 models, stable IDs, group validation, and one shared rule
   evaluator with exhaustive unit tests.
2. Implement a read-only adapter from the current v1 flat schema for fixtures
   and design development only. Because SessionBuddy supports fresh installs,
   the final database release remains a rebased canonical baseline, not an
   incremental production migration.
3. Build group and choice-row authoring, including keyboard reordering.
4. Use the shared engine in public CFP and exact speaker proposal pages.
5. Use the same engine in read-only mode for organizer and reviewer proposal
   detail.
6. Add preview answer simulation, publish validation, and dependency repair.
7. Recreate development databases, apply the canonical baseline once, prove a
   repeat no-op, and run the release gate before deployment.

## Required test matrix

### Schema and security

- group/question/choice IDs are unique and stable;
- cross-form IDs cannot be referenced;
- forward references and cycles fail closed;
- unsupported rule-source types are rejected;
- organizer update of speaker proposal is 404/405;
- reviewer proposal mutation is 404/405;
- reviewer access remains exact-assignment scoped.

### Behavioral form tests

- Workshop shows the Workshop group and hides Presentation;
- Presentation shows the Presentation group and hides Workshop;
- multiselect `includes` and checkbox checked/unchecked match identically on
  client and server;
- changing a source answer hides the old group and removes its answers from the
  outgoing payload;
- hidden required text and upload questions do not produce a 422;
- browser and server drafts restore each branch independently;
- renamed choice labels do not break rules;
- deleting a referenced choice blocks publish until repaired;
- public create and speaker owner-edit render the same fields in the same order;
- organizer and reviewer render identical proposal answers read-only.

### UX quality

- create three groups and twelve questions without losing the Add controls;
- keyboard-only group/question reorder;
- no hidden controls appear in the accessibility tree;
- 320px and 390px layouts have no horizontal overflow;
- Axe passes for builder, preview, public CFP, speaker proposal, organizer view,
  and reviewer view;
- a long group title, long choice label, and 100-question form remain usable.

## Acceptance example

An organizer can create one `Session format` single-choice question, choose
“Create a group for each choice,” add different custom choice and text
questions to Workshop and Presentation, preview both branches, and publish.
A speaker sees only the branch selected by their Session format. The server
validates exactly that branch. The organizer and assigned reviewer see the
submitted branch with the same labels and grouping but cannot edit its answers.
