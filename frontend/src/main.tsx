import { FormEvent, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Assignment = {
  id: string;
  round_name: string;
  proposal_title: string;
  proposal_abstract: string;
  speaker_name: string;
  rating_min: number;
  rating_max: number;
  recommendations: string[];
  evaluator_guidance: string;
  evaluation_state: "not_started" | "draft" | "final";
  comment_required: boolean;
  rating: number | null;
  recommendation: string | null;
  internal_comment: string;
  criteria: { key: string; label: string; response_type: "score" | "select" | "text"; required: boolean; weight: number | null; options: string[] }[];
  criterion_responses: Record<string, number | string>;
  blind_review: boolean;
  review_closes_at_ms: number | null;
  answers: { label: string; value: string }[];
  hidden_answer_count: number;
};
type SubmissionResult = {
  submission_id: string;
  speaker_name: string;
  proposal_title: string;
  assigned_count: number;
  completed_count: number;
  average_rating: number | null;
  decision: "accepted" | "rejected" | null;
  decision_round_id: string | null;
  internal_reason: string;
  correction_reason: string;
  reviews: {
    evaluator_name: string;
    state: "not_started" | "draft" | "final";
    rating: number | null;
    weighted_score?: number | null;
    recommendation: string | null;
    internal_comment: string;
  }[];
};
type EvaluatorProgress = {
  evaluator_user_id: string;
  display_name: string;
  assigned_count: number;
  completed_count: number;
  conflict_count: number;
};
type Evaluator = { user_id: string; display_name: string };
type ConflictProgress = {
  assignment_id: string;
  evaluator_user_id: string;
  evaluator_name: string;
  proposal_title: string;
  conflict_type: string;
  replacement_required: boolean;
};
type RoundResults = {
  round_id: string;
  event_id: string;
  round_name: string;
  status: "draft" | "open" | "closed";
  assigned_count: number;
  completed_count: number;
  average_rating: number | null;
  submissions: SubmissionResult[];
  submission_count: number;
  next_cursor: string | null;
  evaluators: EvaluatorProgress[];
  available_evaluators: Evaluator[];
  conflicts: ConflictProgress[];
};

type ApiClient = {
  message(error: unknown, fallback?: string): string;
  redirectIfSignedOut(error: unknown): boolean;
  redirectIfDocumentAccessChanged(error: unknown): boolean;
  redirectIfWorkspaceUnavailable(error: unknown): boolean;
  request<T>(
    path: string,
    options?: RequestInit,
    behavior?: Record<string, unknown>,
  ): Promise<T>;
};

declare global {
  interface Window {
    __sessionbuddyTelemetryDraft?: Record<string, unknown>;
    SessionBuddyApi: ApiClient;
  }
}

const api = <T = Record<string, never>,>(
  path: string,
  options: RequestInit = {},
) => window.SessionBuddyApi.request<T>(path, options);
const errorMessage = (error: unknown) => window.SessionBuddyApi.message(error);

function telemetry(pageTemplate: string) {
  const navigation = performance.getEntriesByType("navigation")[0] as
    PerformanceNavigationTiming | undefined;
  const width = innerWidth;
  window.__sessionbuddyTelemetryDraft = {
    schema_version: 1,
    page_template: pageTemplate,
    navigation_type: navigation?.type || "unknown",
    device_class: width < 640 ? "mobile" : width < 1024 ? "tablet" : "desktop",
    sampled: false,
    lcp_ms: null,
    inp_ms: null,
    cls: null,
    ttfb_ms: navigation?.responseStart ?? null,
    fcp_ms: null,
    route_transition_ms: null,
    critical_api_ms: null,
    api_request_id: null,
  };
}

function mutationHeaders(csrf: string) {
  return {
    "content-type": "application/json",
    "x-csrf-token": csrf,
    "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}`,
  };
}

function weightedScore(
  criteria: Assignment["criteria"],
  responses: Record<string, number | string>,
): number | null {
  let total = 0;
  let answered = 0;
  const scored = criteria.filter((criterion) => criterion.response_type === "score");
  if (!scored.length) return null;
  for (const criterion of scored) {
    const raw = String(responses[criterion.key] ?? "").trim();
    if (raw === "") continue;
    total += Number(raw) * (criterion.weight ?? 0);
    answered += criterion.weight ?? 0;
  }
  if (!answered) return null;
  return Math.round((total / answered) * 100) / 100;
}

function scorecardComplete(
  criteria: Assignment["criteria"],
  responses: Record<string, number | string>,
): boolean {
  const scored = criteria.filter((criterion) => criterion.response_type === "score");
  return (
    scored.length > 0 &&
    scored.every(
      (criterion) => String(responses[criterion.key] ?? "").trim() !== "",
    )
  );
}

function computePreview(
  form: HTMLFormElement,
  assignment: Assignment,
): number | null {
  const values = Object.fromEntries(new FormData(form));
  if (!assignment.criteria.length) {
    const raw = String(values.rating ?? "").trim();
    return raw === "" ? null : Number(raw);
  }
  return weightedScore(
    assignment.criteria,
    Object.fromEntries(
      assignment.criteria.map((criterion) => [
        criterion.key,
        String(values[`criterion_${criterion.key}`] ?? ""),
      ]),
    ),
  );
}

function formatScore(value: number | null | undefined): string {
  return value == null ? "—" : value.toFixed(2);
}

function ReviewWorkspace() {
  const [csrf, setCsrf] = useState("");
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [status, setStatus] = useState("Loading your assigned reviews…");
  const [cardStatus, setCardStatus] = useState<Record<string, string>>({});
  const [previews, setPreviews] = useState<Record<string, number | null>>({});
  const [previewComplete, setPreviewComplete] = useState<Record<string, boolean>>({});
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const [showFinalized, setShowFinalized] = useState(false);
  const [selectedAssignmentId, setSelectedAssignmentId] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(
    "loading",
  );

  const dirtyCount = Object.values(dirty).filter(Boolean).length;
  useEffect(() => {
    if (!dirtyCount) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    addEventListener("beforeunload", warn);
    return () => removeEventListener("beforeunload", warn);
  }, [dirtyCount]);

  function setCard(assignmentId: string, message: string) {
    setCardStatus((current) => ({ ...current, [assignmentId]: message }));
  }

  async function loadAssignments(cursor: string | null = null) {
    const body = await api<{
      data: Assignment[];
      next_cursor: string | null;
      total: number;
      completed_count: number;
    }>(
      `/api/v1/evaluator/assignments${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`,
    );
    // Default every field the renderer maps or indexes so one missing key
    // in a payload can never take down the whole workspace.
    const assignments = body.data.map((assignment) => ({
      ...assignment,
      answers: assignment.answers ?? [],
      hidden_answer_count: assignment.hidden_answer_count ?? 0,
      criteria: assignment.criteria ?? [],
      recommendations: assignment.recommendations ?? [],
      criterion_responses: assignment.criterion_responses ?? {},
    }));
    setAssignments((current) => (cursor ? [...current, ...assignments] : assignments));
    setNextCursor(body.next_cursor);
    setLoadState("ready");
    setStatus(
      body.total === 0
        ? "New assignments will appear here, and we’ll notify you by email."
        : `${body.completed_count} of ${body.total} review${body.total === 1 ? "" : "s"} finalized.`,
    );
  }
  async function signIn() {
    try {
      const session = await api<{ csrf_token: string }>("/api/v1/auth/session");
      setCsrf(session.csrf_token);
      await loadAssignments();
    } catch (error) {
      if (!window.SessionBuddyApi.redirectIfSignedOut(error)) throw error;
    }
  }
  useEffect(() => {
    telemetry("/reviews");
    signIn().catch((error) => {
      setLoadState("error");
      setStatus(window.SessionBuddyApi.message(error));
    });
  }, []);

  function refreshAssignments() {
    setLoadState("loading");
    setStatus("Refreshing your reviews…");
    signIn().catch((error) => {
      setLoadState("error");
      setStatus(errorMessage(error));
    });
  }

  async function save(
    form: HTMLFormElement,
    assignment: Assignment,
    state: "draft" | "final",
  ) {
    if (state === "final" && !form.checkValidity()) {
      form.reportValidity();
      setCard(
        assignment.id,
        assignment.criteria.length
          ? "Complete every required scorecard response with a valid value before finalizing."
          : "Choose a valid rating and recommendation before finalizing.",
      );
      return;
    }
    if (state === "draft") {
      // A review draft must still contain the core score and recommendation;
      // "draft" means editable, not an empty placeholder write.
      if (!form.reportValidity()) {
        setCard(
          assignment.id,
          "Choose a valid rating and recommendation before saving your draft.",
        );
        return;
      }
      const filledInvalid = Array.from(form.elements).find(
        (element): element is HTMLInputElement =>
          element instanceof HTMLInputElement &&
          element.value.trim() !== "" &&
          !element.checkValidity(),
      );
      if (filledInvalid) {
        filledInvalid.reportValidity();
        setCard(
          assignment.id,
          "Fix the out-of-range score before saving your draft.",
        );
        return;
      }
    }
    const values = Object.fromEntries(new FormData(form));
    if (
      state === "final" &&
      assignment.comment_required &&
      !String(values.internal_comment || "").trim()
    ) {
      setCard(assignment.id, "Add the required reviewer comment before finalizing.");
      return;
    }
    const criterionResponses = Object.fromEntries(
      assignment.criteria
        .filter(
          (criterion) =>
            String(values[`criterion_${criterion.key}`] ?? "").trim() !== "",
        )
        .map((criterion) => [
          criterion.key,
          criterion.response_type === "score"
            ? Number(values[`criterion_${criterion.key}`])
            : String(values[`criterion_${criterion.key}`]),
        ]),
    );
    const directRating = String(values.rating ?? "").trim();
    const rating = assignment.criteria.length
      ? null
      : directRating === ""
        ? null
        : Number(directRating);
    const recommendation = String(values.recommendation ?? "").trim() || null;
    await api(`/api/v1/evaluator/assignments/${assignment.id}/evaluation`, {
      method: "PUT",
      headers: mutationHeaders(csrf),
      body: JSON.stringify({
        rating,
        criterion_responses: criterionResponses,
        recommendation,
        internal_comment: values.internal_comment,
        state,
      }),
    });
    setDirty((current) => ({ ...current, [assignment.id]: false }));
    await loadAssignments();
    setCard(
      assignment.id,
      state === "final" ? "Evaluation finalized." : "Draft saved.",
    );
  }
  async function declareConflict(assignment: Assignment) {
    const conflictType = (
      document.getElementById(
        `conflict-type-${assignment.id}`,
      ) as HTMLSelectElement
    ).value;
    const explanationInput = document.getElementById(
      `conflict-note-${assignment.id}`,
    ) as HTMLTextAreaElement;
    const explanation = explanationInput.value.trim();
    explanationInput.setCustomValidity(
      explanation ? "" : "Explain the conflict before removing the assignment.",
    );
    if (!explanationInput.reportValidity()) return;
    if (
      !window.confirm(
        `Remove your assignment for “${assignment.proposal_title}”? An organizer will need to reassign it, and you cannot undo this yourself.`,
      )
    )
      return;
    await api(`/api/v1/evaluator/assignments/${assignment.id}/conflict`, {
      method: "POST",
      headers: mutationHeaders(csrf),
      body: JSON.stringify({ conflict_type: conflictType, explanation }),
    });
    setDirty((current) => ({ ...current, [assignment.id]: false }));
    await loadAssignments();
    setStatus("Conflict declared. The assignment is ready for reassignment.");
  }
  function finalize(event: FormEvent<HTMLFormElement>, assignment: Assignment) {
    event.preventDefault();
    save(event.currentTarget, assignment, "final").catch((error) =>
      setCard(assignment.id, errorMessage(error)),
    );
  }
  function handleInvalid(
    event: FormEvent<HTMLFormElement>,
    assignment: Assignment,
  ) {
    const field = event.target as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
    if (event.currentTarget.querySelector(":invalid") !== field) return;
    const message = field.name === "recommendation"
      ? "Choose a recommendation before finalizing."
      : field.name === "rating" || field.name.startsWith("criterion_")
        ? "Complete every required scorecard response with a valid value before finalizing."
        : "Complete the required review fields before finalizing.";
    setCard(assignment.id, message);
  }
  function handleFormInput(form: HTMLFormElement, assignment: Assignment) {
    setDirty((current) =>
      current[assignment.id] ? current : { ...current, [assignment.id]: true },
    );
    setPreviews((current) => ({
      ...current,
      [assignment.id]: computePreview(form, assignment),
    }));
    const values = Object.fromEntries(new FormData(form));
    setPreviewComplete((current) => ({
      ...current,
      [assignment.id]: scorecardComplete(
        assignment.criteria,
        Object.fromEntries(
          assignment.criteria.map((criterion) => [
            criterion.key,
            String(values[`criterion_${criterion.key}`] ?? ""),
          ]),
        ),
      ),
    }));
  }

  function closeReview(assignment: Assignment) {
    if (
      dirty[assignment.id] &&
      !window.confirm("Close this review without saving your latest changes?")
    ) return;
    setSelectedAssignmentId(null);
  }

  const visibleAssignments = showFinalized
    ? assignments
    : assignments.filter(
        (assignment) => assignment.evaluation_state !== "final",
      );
  const finalizedCount = assignments.filter(
    (assignment) => assignment.evaluation_state === "final",
  ).length;
  const remainingCount = assignments.length - finalizedCount;

  return (
    <main>
      <section className="hero review-hero">
        <div>
          <h1>Assigned reviews</h1>
          <p>Open a proposal to record or revisit your assessment.</p>
        </div>
      </section>
      {loadState === "loading" && assignments.length === 0 ? (
        <section className="review-state" aria-busy="true" aria-live="polite">
          <span className="review-state__icon" aria-hidden="true">…</span>
          <h2>Loading your reviews</h2>
          <p role="status">{status}</p>
        </section>
      ) : loadState === "error" ? (
        <section className="review-state review-state--error" aria-live="polite">
          <span className="review-state__icon" aria-hidden="true">!</span>
          <h2>Reviews could not be loaded</h2>
          <p role="status">{status}</p>
          <button className="secondary" onClick={refreshAssignments}>Try again</button>
        </section>
      ) : loadState === "ready" && assignments.length === 0 ? (
        <section className="review-state" aria-live="polite">
          <span className="review-state__icon" aria-hidden="true">✓</span>
          <h2>No reviews assigned</h2>
          <p role="status">{status}</p>
          <button className="secondary" onClick={refreshAssignments}>Refresh</button>
        </section>
      ) : (
        <div className={`review-docket-status${remainingCount === 0 ? " review-docket-status--complete" : ""}`}>
          <p role="status">
            <strong>{remainingCount === 0 ? "All reviews complete" : `${remainingCount} remaining`}</strong>
            <span>{remainingCount === 0 ? `${finalizedCount} finalized` : `${finalizedCount} of ${assignments.length} finalized`}</span>
          </p>
          {finalizedCount > 0 && (
            <label className="check">
              <input
                type="checkbox"
                checked={showFinalized}
                onChange={(event) => setShowFinalized(event.target.checked)}
              />{" "}
              Show finalized
            </label>
          )}
        </div>
      )}
      {!selectedAssignmentId && (
        <section className="review-list" aria-label="Assigned proposals">
          {visibleAssignments.map((assignment) => (
            <article className={`review-summary${assignment.evaluation_state === "final" ? " review-summary--final" : ""}`} key={assignment.id}>
              <div className="meta">
                <span>{assignment.round_name}</span>
                <span>{assignment.evaluation_state === "final" ? "Finalized" : assignment.evaluation_state.replace("_", " ")}</span>
              </div>
              <h2>{assignment.proposal_title}</h2>
              {assignment.review_closes_at_ms && (
                <p className="help">
                  Due {new Date(assignment.review_closes_at_ms).toLocaleString()} (your local time)
                </p>
              )}
              <div className="actions">
                <button
                  type="button"
                  className={assignment.evaluation_state === "final" ? "secondary" : ""}
                  onClick={() => setSelectedAssignmentId(assignment.id)}
                >
                  {assignment.evaluation_state === "final" ? "View" : "Open review"}
                </button>
              </div>
            </article>
          ))}
        </section>
      )}
      <section className="review-detail" aria-label="Open review">
        {/* Read the unfiltered list, never `visibleAssignments`. "Show finalized"
            filters the *queue*; it must not decide what stays open in the detail
            pane. Finalizing the review being read flips it to `final`, and while
            the selection is still set the list above is suppressed -- so drawing
            this pane from the filtered list blanks the whole page at the moment
            the reviewer commits, with no confirmation that the write landed. */}
        {assignments
          .filter((assignment) => assignment.id === selectedAssignmentId)
          .map((assignment) => (
          <article key={assignment.id}>
            <button
              type="button"
              className="secondary review-back"
              onClick={() => closeReview(assignment)}
            >
              Back to assigned proposals
            </button>
            <div className="meta">
              <span>{assignment.round_name}</span>
              <span>{assignment.evaluation_state.replace("_", " ")}</span>
            </div>
            <h2>{assignment.proposal_title}</h2>
            <p className="speaker">{assignment.speaker_name}</p>
            {assignment.blind_review && (
              <p className="help">Speaker identity is hidden for this round.</p>
            )}
            <p>{assignment.proposal_abstract}</p>
            {assignment.answers.length > 0 && (
              <details className="answers">
                <summary>
                  Full proposal ({assignment.answers.length} answer
                  {assignment.answers.length === 1 ? "" : "s"})
                </summary>
                <dl>
                  {assignment.answers.map((answer, index) => (
                    <div key={index}>
                      <dt>{answer.label}</dt>
                      <dd>{answer.value}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            )}
            {assignment.hidden_answer_count > 0 && (
              <p className="help">
                {assignment.hidden_answer_count} answer
                {assignment.hidden_answer_count === 1 ? " is" : "s are"} hidden
                to protect blind review.
              </p>
            )}
            {assignment.review_closes_at_ms && (
              <p className="help">
                Due {new Date(assignment.review_closes_at_ms).toLocaleString()}{" "}
                (your local time)
              </p>
            )}
            {assignment.evaluator_guidance && (
              <aside>{assignment.evaluator_guidance}</aside>
            )}
            <form
              noValidate
              onSubmit={(event) => finalize(event, assignment)}
              onInvalidCapture={(event) => handleInvalid(event, assignment)}
              onInput={(event) =>
                handleFormInput(event.currentTarget, assignment)
              }
            >
              {assignment.criteria.length ? (
                <fieldset className="scorecard">
                  <legend>Scorecard</legend>
                  {assignment.criteria.map((criterion) => (
                    <label className="scorecard__criterion" key={criterion.key}>
                      <span>{criterion.label}</span>
                      {criterion.response_type === "score" && <small>{criterion.weight}% of overall score</small>}
                      {criterion.response_type === "score" ? (
                        <input name={`criterion_${criterion.key}`} type="number" min={assignment.rating_min} max={assignment.rating_max} defaultValue={assignment.criterion_responses[criterion.key] ?? ""} required={criterion.required} disabled={assignment.evaluation_state === "final"} />
                      ) : criterion.response_type === "select" ? (
                        <select name={`criterion_${criterion.key}`} defaultValue={assignment.criterion_responses[criterion.key] ?? ""} required={criterion.required} disabled={assignment.evaluation_state === "final"}>
                          <option value="">Choose…</option>
                          {criterion.options.map((option) => <option key={option}>{option}</option>)}
                        </select>
                      ) : (
                        <textarea name={`criterion_${criterion.key}`} rows={4} maxLength={5000} defaultValue={assignment.criterion_responses[criterion.key] ?? ""} required={criterion.required} disabled={assignment.evaluation_state === "final"} />
                      )}
                    </label>
                  ))}
                </fieldset>
              ) : (
                <label>
                  Rating
                  <input
                    name="rating"
                    type="number"
                    min={assignment.rating_min}
                    max={assignment.rating_max}
                    defaultValue={assignment.rating ?? ""}
                    required
                    disabled={assignment.evaluation_state === "final"}
                  />
                </label>
              )}
              <div className="review-fields">
                <label>
                  Recommendation
                  <select
                    name="recommendation"
                    defaultValue={assignment.recommendation ?? ""}
                    required
                    disabled={assignment.evaluation_state === "final"}
                  >
                    <option value="">Choose…</option>
                    {assignment.recommendations.map((choice) => (
                      <option key={choice}>{choice}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Internal comment
                  <textarea
                    name="internal_comment"
                    rows={4}
                    maxLength={5000}
                    defaultValue={assignment.internal_comment}
                    disabled={assignment.evaluation_state === "final"}
                  />
                </label>
              </div>
              {assignment.criteria.length > 0 &&
                assignment.evaluation_state !== "final" && (
                  <p className="help">
                    {(Object.prototype.hasOwnProperty.call(
                      previewComplete,
                      assignment.id,
                    )
                      ? previewComplete[assignment.id]
                      : scorecardComplete(
                          assignment.criteria,
                          assignment.criterion_responses,
                        ))
                      ? "Weighted score preview"
                      : "Partial weighted score preview"}
                    :{" "}
                    <strong>
                      {formatScore(
                        Object.prototype.hasOwnProperty.call(
                          previews,
                          assignment.id,
                        )
                          ? previews[assignment.id]
                          : weightedScore(
                              assignment.criteria,
                              assignment.criterion_responses,
                            ),
                      )}
                    </strong>{" "}
                    (calculated from the scorecard)
                  </p>
                )}
              {cardStatus[assignment.id] && (
                <p className="help" role="status">
                  {cardStatus[assignment.id]}
                </p>
              )}
              {assignment.evaluation_state === "final" ? (
                <p className="review-finalized" role="status">
                  <strong>Review finalized</strong>
                  Scores and comments are now read-only.
                </p>
              ) : (
                <div className="actions">
                  <button
                    type="button"
                    className="secondary"
                    onClick={(event) =>
                      save(event.currentTarget.form!, assignment, "draft").catch(
                        (error) => setCard(assignment.id, errorMessage(error)),
                      )
                    }
                  >
                    Save draft
                  </button>
                  <button type="submit">Finalize</button>
                </div>
              )}
            </form>
            {assignment.evaluation_state !== "final" && (
              <details>
                <summary>Declare a conflict of interest</summary>
                <label>
                  Conflict type
                  <select id={`conflict-type-${assignment.id}`}>
                    <option value="speaker_relationship">
                      Speaker relationship
                    </option>
                    <option value="same_company">Same company</option>
                    <option value="financial">Financial</option>
                    <option value="other">Other</option>
                  </select>
                </label>
                <label>
                  Explanation
                  <textarea
                    id={`conflict-note-${assignment.id}`}
                    rows={3}
                    maxLength={1000}
                    required
                  />
                </label>
                <button
                  className="secondary"
                  onClick={() =>
                    declareConflict(assignment).catch((error) =>
                      setStatus(errorMessage(error)),
                    )
                  }
                >
                  Remove my assignment
                </button>
              </details>
            )}
          </article>
        ))}
      </section>
      {nextCursor && (
        <div className="actions">
          <button
            className="secondary"
            onClick={() =>
              loadAssignments(nextCursor).catch((error) =>
                setStatus(errorMessage(error)),
              )
            }
          >
            Load more reviews
          </button>
        </div>
      )}
    </main>
  );
}

type ResultSort = "submitted" | "score_desc" | "score_asc";

function AdminRoundDashboard({ roundId }: { roundId: string }) {
  const [csrf, setCsrf] = useState("");
  const [results, setResults] = useState<RoundResults | null>(null);
  const [status, setStatus] = useState("Loading round progress…");
  const [showForceClose, setShowForceClose] = useState(false);
  const [forceCloseReason, setForceCloseReason] = useState("");
  const [pendingDecision, setPendingDecision] = useState<{
    submission: SubmissionResult;
    decision: "accepted" | "rejected";
  } | null>(null);
  const [sendEmail, setSendEmail] = useState(true);
  const [speakerMessage, setSpeakerMessage] = useState("");
  const [resultSort, setResultSort] = useState<ResultSort>("submitted");
  const [selectedEvaluatorId, setSelectedEvaluatorId] = useState("");

  // Chairs rank proposals by score; the API returns them newest-submitted first, so the
  // ordering the committee actually works from is applied here over the loaded page(s).
  const sortedSubmissions = useMemo(() => {
    const rows = results ? [...results.submissions] : [];
    if (resultSort === "submitted") return rows;
    const direction = resultSort === "score_desc" ? -1 : 1;
    return rows.sort((left, right) => {
      // Unscored proposals sort last in both directions rather than clustering at zero.
      if (left.average_rating === null && right.average_rating === null) return 0;
      if (left.average_rating === null) return 1;
      if (right.average_rating === null) return -1;
      if (left.average_rating === right.average_rating) {
        return left.proposal_title.localeCompare(right.proposal_title);
      }
      return (left.average_rating - right.average_rating) * direction;
    });
  }, [results, resultSort]);

  async function load(cursor: string | null = null) {
    const body = await api<RoundResults>(
      `/api/v1/admin/evaluation-rounds/${roundId}/results${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`,
    );
    setResults((current) =>
      cursor && current
        ? { ...body, submissions: [...current.submissions, ...body.submissions] }
        : body,
    );
    setStatus("");
  }
  async function signIn() {
    try {
      const body = await api<{ csrf_token: string }>("/api/v1/auth/session");
      setCsrf(body.csrf_token);
      await load();
    } catch (error) {
      if (window.SessionBuddyApi.redirectIfSignedOut(error)) return;
      if (window.SessionBuddyApi.redirectIfWorkspaceUnavailable(error)) return;
      if (window.SessionBuddyApi.redirectIfDocumentAccessChanged(error)) return;
      throw error;
    }
  }
  async function decide(
    submission: SubmissionResult,
    decision: "accepted" | "rejected",
  ) {
    const unchanged = submission.decision === decision;
    if (unchanged) {
      setPendingDecision(null);
      setSpeakerMessage("");
      setStatus(`The final decision remains ${decision}; no correction was needed.`);
      return;
    }
    const reasonInput = document.getElementById(
      `reason-${submission.submission_id}`,
    ) as HTMLTextAreaElement;
    const reason = reasonInput.value.trim();
    const correction = submission.decision !== null;
    const override = submission.completed_count < submission.assigned_count;
    reasonInput.setCustomValidity(
      (override || correction) && !reason
        ? correction
          ? "An internal reason is required to correct a final decision."
          : "An internal reason is required when overriding incomplete reviews."
        : "",
    );
    if (!reasonInput.reportValidity()) return;
    if (correction) {
      const body = await api<{
        communication_queued: boolean;
        accepted_session_lifecycle_status: "active" | "withdrawn" | null;
      }>(
        `/api/v1/admin/events/${encodeURIComponent(results!.event_id)}/submissions/${encodeURIComponent(submission.submission_id)}/decision-corrections`,
        {
          method: "POST",
          headers: mutationHeaders(csrf),
          body: JSON.stringify({
            corrected_decision: decision,
            reason,
            send_email: sendEmail,
            speaker_message: speakerMessage,
          }),
        },
      );
      setPendingDecision(null);
      setSpeakerMessage("");
      await load();
      setStatus(
        `Decision corrected to ${decision}. The original decision remains in the audit history.${body.accepted_session_lifecycle_status === "withdrawn" && decision === "accepted" ? " The session remains withdrawn until speaker participation is restored." : ""}${body.communication_queued ? " Speaker email queued." : " No email sent."}`,
      );
      return;
    }
    const body = await api<{ communication_queued: boolean }>(
      `/api/v1/admin/evaluation-rounds/${roundId}/submissions/${submission.submission_id}/decision`,
      {
        method: "POST",
        headers: mutationHeaders(csrf),
        body: JSON.stringify({
          decision,
          internal_reason: reason,
          send_email: sendEmail,
          speaker_message: speakerMessage,
          override_incomplete_reviews:
            submission.completed_count < submission.assigned_count,
        }),
      },
    );
    setPendingDecision(null);
    setSpeakerMessage("");
    await load();
    setStatus(
      `Decision recorded as ${decision}.${body.communication_queued ? " Speaker email queued." : " No email sent."}`,
    );
  }
  async function reassign(conflict: ConflictProgress) {
    const evaluator = document.getElementById(
      `replacement-${conflict.assignment_id}`,
    ) as HTMLSelectElement;
    const evaluatorId = evaluator.value;
    evaluator.setCustomValidity(
      evaluatorId ? "" : "Choose a replacement reviewer.",
    );
    if (!evaluator.reportValidity()) return;
    await api(
      `/api/v1/admin/evaluation-assignments/${conflict.assignment_id}/reassign`,
      {
        method: "POST",
        headers: mutationHeaders(csrf),
        body: JSON.stringify({ evaluator_user_id: evaluatorId }),
      },
    );
    await load();
    setStatus("Conflicted assignment reassigned.");
  }
  async function addEvaluator() {
    if (!selectedEvaluatorId) return;
    const result = await api<{ assignment_count: number }>(
      `/api/v1/admin/evaluation-rounds/${roundId}/evaluators`,
      {
        method: "POST",
        headers: mutationHeaders(csrf),
        body: JSON.stringify({ evaluator_user_id: selectedEvaluatorId }),
      },
    );
    setSelectedEvaluatorId("");
    await load();
    setStatus(
      `Reviewer added with ${result.assignment_count} assignment${result.assignment_count === 1 ? "" : "s"}.`,
    );
  }
  async function removeEvaluator(evaluator: EvaluatorProgress) {
    if (
      !window.confirm(
        `Remove ${evaluator.display_name} from this round? Their unsaved assignments will be removed.`,
      )
    )
      return;
    await api(
      `/api/v1/admin/evaluation-rounds/${roundId}/evaluators/${encodeURIComponent(evaluator.evaluator_user_id)}/remove`,
      {
        method: "POST",
        headers: mutationHeaders(csrf),
        body: "{}",
      },
    );
    await load();
    setStatus("Reviewer removed from the round.");
  }
  async function closeRound(force = false) {
    const defaultReason =
      "Organizer closed the round before every review was final.";
    const reason = force ? forceCloseReason.trim() || defaultReason : "";
    await api(`/api/v1/admin/evaluation-rounds/${roundId}/close`, {
      method: "POST",
      headers: mutationHeaders(csrf),
      body: JSON.stringify({ force, reason }),
    });
    setShowForceClose(false);
    setForceCloseReason("");
    await load();
    setStatus("Round closed. All evaluations are permanently read-only.");
  }
  async function remindEvaluator(evaluator: EvaluatorProgress) {
    await api(
      `/api/v1/admin/evaluation-rounds/${roundId}/evaluators/${encodeURIComponent(evaluator.evaluator_user_id)}/reminder`,
      {
        method: "POST",
        headers: mutationHeaders(csrf),
        body: "{}",
      },
    );
    setStatus(`Reminder queued for ${evaluator.display_name}.`);
  }
  useEffect(() => {
    telemetry("/admin/evaluation-rounds/{round_id}");
    signIn().catch((error) => setStatus(window.SessionBuddyApi.message(error)));
  }, []);
  const closeReady =
    !!results &&
    results.status === "open" &&
    results.assigned_count > 0 &&
    results.completed_count === results.assigned_count &&
    !results.conflicts.some((conflict) => conflict.replacement_required);

  return (
    <main className="round-desk">
      <header className="round-desk__header">
        <div>
          <p className="eyebrow">Evaluation round</p>
          <h1>{results?.round_name || "Round progress"}</h1>
          <p>
            Track reviewer progress and decide which proposals move forward.
          </p>
        </div>
        <div className="round-desk__actions">
          <a
            className="button secondary"
            href={`/api/v1/admin/evaluation-rounds/${encodeURIComponent(roundId)}/export.csv`}
          >
            Export CSV
          </a>
          <button
            className="secondary"
            onClick={() =>
              signIn().catch((error) => setStatus(errorMessage(error)))
            }
          >
            Refresh
          </button>
          <button
            className={closeReady ? "" : "danger-outline"}
            disabled={!results || results.status !== "open"}
            onClick={() => {
              if (!closeReady) {
                setShowForceClose(true);
                return;
              }
              closeRound(false).catch((error) => setStatus(errorMessage(error)));
            }}
          >
            {closeReady ? "Close round" : "Close round early"}
          </button>
        </div>
      </header>
      <p className="round-desk__status" role="status" aria-live="polite">
        {status}
      </p>
      {showForceClose && (
        <section className="confirmation" role="alert">
          <h2>Close this round early?</h2>
          <p>
            Incomplete reviews will remain unfinished. The round becomes
            permanently read-only.
          </p>
          <label>
            Reason
            <textarea
              rows={3}
              maxLength={2000}
              value={forceCloseReason}
              onChange={(event) => setForceCloseReason(event.target.value)}
              placeholder="Organizer closed the round before every review was final."
            />
          </label>
          <div className="actions">
            <button
              className="secondary"
              onClick={() => setShowForceClose(false)}
            >
              Cancel
            </button>
            <button
              className="danger"
              onClick={() =>
                closeRound(true).catch((error) =>
                  setStatus(errorMessage(error)),
                )
              }
            >
              Confirm force close
            </button>
          </div>
        </section>
      )}
      {results && (
        <>
          <nav className="round-desk__links" aria-label="Related event workspaces">
            <a
              href={`/admin/events/${encodeURIComponent(results.event_id)}/submissions`}
            >
              <span aria-hidden="true">←</span> Proposal inbox
            </a>
            <a
              href={`/admin/events/${encodeURIComponent(results.event_id)}/onboarding`}
            >
              Speaker onboarding
            </a>
            <a
              href={`/admin/events/${encodeURIComponent(results.event_id)}/agenda`}
            >
              Agenda
            </a>
          </nav>
          <section className="round-progress" aria-label="Round progress">
            <div className="round-progress__summary">
              <div>
                <strong>
                  {results.assigned_count === 0
                    ? "0"
                    : `${results.completed_count}/${results.assigned_count}`}
                </strong>
                <span>
                  {results.assigned_count === 0
                    ? "reviews assigned"
                    : "reviews finalized"}
                </span>
              </div>
              <div>
                <strong>{formatScore(results.average_rating)}</strong>
                <span>round average</span>
              </div>
              <span
                className={`round-status round-status--${
                  closeReady ? "ready" : results.status
                }`}
              >
                {closeReady
                  ? "Ready for decisions"
                  : results.status === "open"
                    ? "Review in progress"
                    : `${results.status.charAt(0).toUpperCase()}${results.status.slice(1)}`}
              </span>
            </div>
            {results.assigned_count === 0 ? (
              <p className="help round-progress__empty">No reviews assigned yet.</p>
            ) : (
              <div
                className="round-progress__track"
                role="progressbar"
                aria-label="Finalized reviews"
                aria-valuemin={0}
                aria-valuemax={results.assigned_count}
                aria-valuenow={results.completed_count}
                aria-valuetext={`${results.completed_count} of ${results.assigned_count} reviews finalized`}
              >
                <span
                  style={{
                    width: `${(results.completed_count / results.assigned_count) * 100}%`,
                  }}
                />
              </div>
            )}
          </section>
          {results.status === "draft" && (
            <p className="help round-draft-note" role="note">
              This round is still a draft. Reviewers cannot see assignments or begin
              reviewing until it is opened.{" "}
              <a
                href={`/admin/events/${encodeURIComponent(results.event_id)}/submissions`}
              >
                Return to the proposal inbox
              </a>{" "}
              to finish assignments and open the round.
            </p>
          )}
          <section className="round-section" aria-labelledby="reviewer-progress-title">
            <div className="round-section__heading">
              <div>
                <p className="eyebrow">People</p>
                <h2 id="reviewer-progress-title">Reviewer progress</h2>
              </div>
            {results.status === "open" && !closeReady && (
              <div className="round-add-reviewer">
                <label>
                  <span className="visually-hidden">Reviewer to add</span>
                  <select
                    id="round-add-evaluator"
                    value={selectedEvaluatorId}
                    onChange={(event) => setSelectedEvaluatorId(event.target.value)}
                  >
                    <option value="">Choose reviewer…</option>
                    {results.available_evaluators
                      .filter(
                        (candidate) =>
                          !results.evaluators.some(
                            (current) =>
                              current.evaluator_user_id === candidate.user_id,
                          ),
                      )
                      .map((candidate) => (
                        <option
                          key={candidate.user_id}
                          value={candidate.user_id}
                        >
                          {candidate.display_name}
                        </option>
                      ))}
                  </select>
                </label>
                <button
                  className="secondary"
                  disabled={!selectedEvaluatorId}
                  onClick={() =>
                    addEvaluator().catch((error) =>
                      setStatus(errorMessage(error)),
                    )
                  }
                >
                  Add reviewer
                </button>
              </div>
            )}
            {results.status === "open" && closeReady && (
              <details className="round-add-reviewer-disclosure">
                <summary>Add another reviewer</summary>
                <div className="round-add-reviewer">
                  <label>
                    <span className="visually-hidden">Reviewer to add</span>
                    <select
                      value={selectedEvaluatorId}
                      onChange={(event) =>
                        setSelectedEvaluatorId(event.target.value)
                      }
                    >
                      <option value="">Choose reviewer…</option>
                      {results.available_evaluators
                        .filter(
                          (candidate) =>
                            !results.evaluators.some(
                              (current) =>
                                current.evaluator_user_id === candidate.user_id,
                            ),
                        )
                        .map((candidate) => (
                          <option key={candidate.user_id} value={candidate.user_id}>
                            {candidate.display_name}
                          </option>
                        ))}
                    </select>
                  </label>
                  <button
                    className="secondary"
                    disabled={!selectedEvaluatorId}
                    onClick={() =>
                      addEvaluator().catch((error) =>
                        setStatus(errorMessage(error)),
                      )
                    }
                  >
                    Add reviewer
                  </button>
                </div>
                <p className="help">
                  This adds a new assignment and moves the round back into review.
                </p>
              </details>
            )}
            </div>
          <div className="reviewer-list" aria-label="Evaluator progress">
            {results.evaluators.length === 0 && (
              <p className="help reviewer-list__empty">
                No reviewers attached to this round yet.
              </p>
            )}
            {results.evaluators.map((evaluator) => (
                <article className="reviewer-row" key={evaluator.evaluator_user_id}>
                  <div className="reviewer-row__identity">
                    <span className="reviewer-row__avatar" aria-hidden="true">
                      {evaluator.display_name.slice(0, 1).toUpperCase()}
                    </span>
                    <div>
                      <strong>{evaluator.display_name}</strong>
                      <span>
                        {evaluator.assigned_count === 0
                          ? "No proposals assigned"
                          : `${evaluator.completed_count}/${evaluator.assigned_count} finalized`}
                        {evaluator.conflict_count > 0 && ` · ${evaluator.conflict_count} conflicts`}
                      </span>
                    </div>
                  </div>
                  <div className="reviewer-row__actions">
                  {results.status === "open" &&
                    evaluator.completed_count < evaluator.assigned_count && (
                      <button
                        className="tertiary"
                        onClick={() =>
                          remindEvaluator(evaluator).catch((error) =>
                            setStatus(errorMessage(error)),
                          )
                        }
                      >
                        Send reminder
                      </button>
                    )}
                  {results.status === "open" &&
                    evaluator.completed_count === 0 &&
                    evaluator.conflict_count === 0 && (
                      <button
                        className="tertiary danger-text"
                        onClick={() =>
                          removeEvaluator(evaluator).catch((error) =>
                            setStatus(errorMessage(error)),
                          )
                        }
                      >
                        Remove
                      </button>
                    )}
                  </div>
                </article>
              ))}
          </div>
          </section>
          {results.conflicts.length > 0 && (
            <>
              <h2>Conflicts</h2>
              <section className="grid" aria-label="Declared conflicts">
                {results.conflicts.map((conflict) => (
                  <article key={conflict.assignment_id}>
                    <div className="meta">
                      <span>{conflict.conflict_type.replace("_", " ")}</span>
                      <span>
                        {conflict.replacement_required
                          ? "replacement required"
                          : "covered"}
                      </span>
                    </div>
                    <h3>{conflict.proposal_title}</h3>
                    <p>{conflict.evaluator_name}</p>
                    {conflict.replacement_required && (
                      <>
                        <label>
                          Replacement evaluator
                          <select id={`replacement-${conflict.assignment_id}`}>
                            <option value="">Choose…</option>
                            {results.available_evaluators
                              .filter(
                                (evaluator) =>
                                  evaluator.user_id !==
                                  conflict.evaluator_user_id,
                              )
                              .map((evaluator) => (
                                <option
                                  key={evaluator.user_id}
                                  value={evaluator.user_id}
                                >
                                  {evaluator.display_name}
                                </option>
                              ))}
                          </select>
                        </label>
                        <button
                          onClick={() =>
                            reassign(conflict).catch((error) =>
                              setStatus(errorMessage(error)),
                            )
                          }
                        >
                          Reassign
                        </button>
                      </>
                    )}
                  </article>
                ))}
              </section>
            </>
          )}
          <section className="round-section round-section--proposals" aria-labelledby="proposal-results-title">
          <div className="round-section__heading">
            <div>
              <p className="eyebrow">Decisions</p>
              <h2 id="proposal-results-title">Proposal results</h2>
            </div>
            <label className="sort-control">
              <span>Sort by</span>
              <select
                value={resultSort}
                aria-label="Sort proposal results"
                onChange={(event) =>
                  setResultSort(event.target.value as ResultSort)
                }
              >
                <option value="submitted">Submission date (newest first)</option>
                <option value="score_desc">Score (highest first)</option>
                <option value="score_asc">Score (lowest first)</option>
              </select>
            </label>
          </div>
          <div className="proposal-results" aria-label="Proposal results">
            {sortedSubmissions.map((submission) => {
              const complete =
                submission.assigned_count > 0 &&
                submission.completed_count === submission.assigned_count;
              const decided = submission.decision !== null;
              const advisory =
                decided &&
                submission.decision_round_id !== null &&
                submission.decision_round_id !== roundId;
              const decidedOutsideRound =
                decided && submission.decision_round_id === null;
              const pending =
                pendingDecision?.submission.submission_id ===
                submission.submission_id;
              return (
                <article className="proposal-result" key={submission.submission_id}>
                  <div className="proposal-result__state">
                    <span>
                      {decided
                        ? "final decision in effect"
                        : complete
                          ? "ready for decision"
                          : "review in progress"}
                    </span>
                    <span>{submission.decision || "undecided"}</span>
                  </div>
                  <h3>{submission.proposal_title}</h3>
                  <p className="speaker">{submission.speaker_name}</p>
                  <p className="proposal-result__score">
                    <strong>{formatScore(submission.average_rating)}</strong> mean ·{" "}
                    {submission.completed_count}/{submission.assigned_count}{" "}
                    complete
                  </p>
                  <details>
                    <summary>
                      Individual reviews ({submission.reviews.length})
                    </summary>
                    {submission.reviews.map((review, index) => (
                      <article key={`${submission.submission_id}-${index}`}>
                        <strong>{review.evaluator_name}</strong>
                        <p>
                          {review.state === "final"
                            ? `${review.weighted_score != null ? formatScore(review.weighted_score) : review.rating ?? "—"} · ${review.recommendation || "No recommendation"}`
                            : review.state.replace("_", " ")}
                        </p>
                        {review.internal_comment && (
                          <p>{review.internal_comment}</p>
                        )}
                      </article>
                    ))}
                  </details>
                  {decided ? (
                    <>
                      <p>
                        <strong>Original decision reason:</strong>{" "}
                        {submission.internal_reason || "No reason recorded."}
                      </p>
                      {submission.correction_reason && (
                        <p>
                          <strong>Latest correction reason:</strong>{" "}
                          {submission.correction_reason}
                        </p>
                      )}
                    </>
                  ) : null}
                  <p className="help">
                    {decided
                      ? advisory
                        ? "This later round is advisory. Its reviews do not replace the final result; changing that result requires an audited correction."
                        : decidedOutsideRound
                          ? "This final result was recorded without an evaluation round. Reviews here are advisory; changing the result requires an audited correction."
                        : "This round recorded the final result. Any later change requires an audited correction."
                      : complete
                        ? "Accepting creates onboarding tasks; rejecting closes outstanding tasks."
                        : "Reviews are incomplete. An organizer may override with a required internal reason; the override is audited."}
                  </p>
                  {pending ? (
                    <div className="confirmation decision-confirmation" role="alert">
                      <strong>
                        {decided ? "Confirm audited " : "Confirm permanent "}
                        {pendingDecision.decision}
                        {complete ? "" : " with organizer override"}
                      </strong>
                      <p>
                        {decided
                          ? pendingDecision.decision === submission.decision
                            ? "The existing final decision will remain unchanged."
                            : "The original final decision remains in the audit history."
                          : "This cannot be changed later without an audited correction."}
                      </p>
                      <label>
                        Internal decision reason {complete && !decided && <span className="optional">Optional</span>}
                        <textarea
                          id={`reason-${submission.submission_id}`}
                          rows={3}
                          maxLength={2000}
                          required={!complete || (decided && pendingDecision.decision !== submission.decision)}
                          placeholder={decided && pendingDecision.decision !== submission.decision
                            ? "Explain why the later review changes the final decision."
                            : complete
                              ? "Add a private note for the decision record."
                              : "Explain why you are overriding incomplete reviews."}
                        />
                      </label>
                      <label className="check">
                        <input
                          type="checkbox"
                          checked={sendEmail}
                          onChange={(event) =>
                            setSendEmail(event.target.checked)
                          }
                        />{" "}
                        {decided ? "Email the speaker about this correction" : "Email the speaker"}
                      </label>
                      {sendEmail && (
                        <label>
                          Message <span className="optional">Optional</span>
                          <textarea
                            rows={3}
                            maxLength={4000}
                            value={speakerMessage}
                            onChange={(event) =>
                              setSpeakerMessage(event.target.value)
                            }
                            placeholder="Leave blank to use the standard decision message."
                          />
                        </label>
                      )}
                      <div className="actions">
                        <button
                          className="secondary"
                          onClick={() => setPendingDecision(null)}
                        >
                          Cancel
                        </button>
                        <button
                          className={pendingDecision.decision === "rejected" ? "danger" : ""}
                          onClick={() =>
                            decide(submission, pendingDecision.decision).catch(
                              (error) => setStatus(errorMessage(error)),
                            )
                          }
                        >
                          Confirm {pendingDecision.decision}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="actions proposal-result__actions">
                      <button
                        className="secondary"
                        onClick={() => {
                          if (submission.decision === "rejected") {
                            setStatus("The final decision remains rejected; no correction was needed.");
                            return;
                          }
                          setPendingDecision({
                            submission,
                            decision: "rejected",
                          });
                          setSendEmail(true);
                        }}
                      >
                        {decided && submission.decision === "rejected" ? "Keep rejected" : decided ? "Correct to rejected" : "Reject"}
                      </button>
                      <button
                        onClick={() => {
                          if (submission.decision === "accepted") {
                            setStatus("The final decision remains accepted; no correction was needed.");
                            return;
                          }
                          setPendingDecision({
                            submission,
                            decision: "accepted",
                          });
                          setSendEmail(true);
                        }}
                      >
                        {decided && submission.decision === "accepted" ? "Keep accepted" : decided ? "Correct to accepted" : "Accept"}
                      </button>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
          {results.next_cursor && (
            <div className="actions">
              <button
                className="secondary"
                onClick={() =>
                  load(results.next_cursor).catch((error) =>
                    setStatus(errorMessage(error)),
                  )
                }
              >
                Load more results
              </button>
            </div>
          )}
          </section>
        </>
      )}
    </main>
  );
}

function App() {
  const match = location.pathname.match(
    /^\/admin\/evaluation-rounds\/([^/]+)$/,
  );
  return match ? (
    <AdminRoundDashboard roundId={decodeURIComponent(match[1])} />
  ) : (
    <ReviewWorkspace />
  );
}

createRoot(document.getElementById("root")!).render(<App />);
