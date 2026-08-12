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
  criteria: { key: string; label: string; weight: number }[];
  criterion_scores: Record<string, number>;
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
  internal_reason: string;
  reviews: {
    evaluator_name: string;
    state: "not_started" | "draft" | "final";
    rating: number | null;
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

function computePreview(
  form: HTMLFormElement,
  assignment: Assignment,
): number | null {
  const values = Object.fromEntries(new FormData(form));
  if (!assignment.criteria.length) {
    const raw = String(values.rating ?? "").trim();
    return raw === "" ? null : Number(raw);
  }
  let total = 0;
  for (const criterion of assignment.criteria) {
    const raw = String(values[`criterion_${criterion.key}`] ?? "").trim();
    if (raw === "") return null;
    total += Number(raw) * criterion.weight;
  }
  return Math.round(total / 100);
}

function ReviewWorkspace() {
  const [csrf, setCsrf] = useState("");
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [status, setStatus] = useState("Loading your assigned reviews…");
  const [cardStatus, setCardStatus] = useState<Record<string, string>>({});
  const [previews, setPreviews] = useState<Record<string, number | null>>({});
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
      criterion_scores: assignment.criterion_scores ?? {},
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
    if (state === "final") {
      if (!form.reportValidity()) {
        setCard(
          assignment.id,
          "Choose a valid rating and recommendation before finalizing.",
        );
        return;
      }
    } else {
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
    const criterionScores = Object.fromEntries(
      assignment.criteria
        .filter(
          (criterion) =>
            String(values[`criterion_${criterion.key}`] ?? "").trim() !== "",
        )
        .map((criterion) => [
          criterion.key,
          Number(values[`criterion_${criterion.key}`]),
        ]),
    );
    const allScored =
      assignment.criteria.length > 0 &&
      Object.keys(criterionScores).length === assignment.criteria.length;
    const directRating = String(values.rating ?? "").trim();
    const rating = assignment.criteria.length
      ? allScored
        ? Math.round(
            assignment.criteria.reduce(
              (total, criterion) =>
                total + criterionScores[criterion.key] * criterion.weight,
              0,
            ) / 100,
          )
        : null
      : directRating === ""
        ? null
        : Number(directRating);
    const recommendation = String(values.recommendation ?? "").trim() || null;
    await api(`/api/v1/evaluator/assignments/${assignment.id}/evaluation`, {
      method: "PUT",
      headers: mutationHeaders(csrf),
      body: JSON.stringify({
        rating,
        criterion_scores: criterionScores,
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
  function handleFormInput(form: HTMLFormElement, assignment: Assignment) {
    setDirty((current) =>
      current[assignment.id] ? current : { ...current, [assignment.id]: true },
    );
    setPreviews((current) => ({
      ...current,
      [assignment.id]: computePreview(form, assignment),
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
        {visibleAssignments
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
              onSubmit={(event) => finalize(event, assignment)}
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
                      <small>{criterion.weight}% of overall score</small>
                      <input
                        name={`criterion_${criterion.key}`}
                        type="number"
                        min={assignment.rating_min}
                        max={assignment.rating_max}
                        defaultValue={
                          assignment.criterion_scores[criterion.key] ?? ""
                        }
                        required
                        disabled={assignment.evaluation_state === "final"}
                      />
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
                    Overall rating preview:{" "}
                    <strong>
                      {(previews[assignment.id] ?? assignment.rating) ?? "—"}
                    </strong>{" "}
                    (weighted mean, submitted on finalize)
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
    setStatus(
      `${body.completed_count} of ${body.assigned_count} evaluations finalized.`,
    );
  }
  async function signIn() {
    try {
      const body = await api<{ csrf_token: string }>("/api/v1/auth/session");
      setCsrf(body.csrf_token);
      await load();
    } catch (error) {
      if (!window.SessionBuddyApi.redirectIfSignedOut(error)) throw error;
    }
  }
  async function decide(
    submission: SubmissionResult,
    decision: "accepted" | "rejected",
  ) {
    const reasonInput = document.getElementById(
      `reason-${submission.submission_id}`,
    ) as HTMLTextAreaElement;
    const reason = reasonInput.value.trim();
    const override = submission.completed_count < submission.assigned_count;
    reasonInput.setCustomValidity(
      override && !reason
        ? "An internal reason is required when overriding incomplete reviews."
        : "",
    );
    if (!reasonInput.reportValidity()) return;
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
    const select = document.getElementById(
      "round-add-evaluator",
    ) as HTMLSelectElement;
    select.setCustomValidity(select.value ? "" : "Choose a reviewer to add.");
    if (!select.reportValidity()) return;
    const result = await api<{ assignment_count: number }>(
      `/api/v1/admin/evaluation-rounds/${roundId}/evaluators`,
      {
        method: "POST",
        headers: mutationHeaders(csrf),
        body: JSON.stringify({ evaluator_user_id: select.value }),
      },
    );
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
    <main>
      <section className="hero">
        <h1>{results?.round_name || "Round progress"}</h1>
        <p>
          Monitor completion, resolve conflicts, and decide which proposals move
          forward.
        </p>
      </section>
      <div className="toolbar">
        <p role="status">{status}</p>
        <div className="actions">
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
            disabled={!results || results.status !== "open"}
            onClick={() => {
              if (!closeReady) {
                setShowForceClose(true);
                return;
              }
              closeRound(false).catch((error) => setStatus(errorMessage(error)));
            }}
          >
            {closeReady ? "Close round" : "Force close round"}
          </button>
        </div>
      </div>
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
          <nav className="workflow" aria-label="Event workflow">
            <a
              href={`/admin/events/${encodeURIComponent(results.event_id)}/submissions`}
            >
              Proposals
            </a>
            <a
              href={`/admin/events/${encodeURIComponent(results.event_id)}/onboarding`}
            >
              Speaker onboarding
            </a>
            <a
              href={`/admin/events/${encodeURIComponent(results.event_id)}/agenda`}
            >
              Build agenda
            </a>
          </nav>
          <section className="metrics">
            <article>
              <span>Completed</span>
              <strong>
                {results.completed_count}/{results.assigned_count}
              </strong>
            </article>
            <article>
              <span>Overall mean</span>
              <strong>{results.average_rating ?? "—"}</strong>
            </article>
            <article>
              <span>Round status</span>
              <strong>{results.status}</strong>
            </article>
          </section>
          <div className="toolbar">
            <h2>Reviewer progress</h2>
            {results.status === "open" && (
              <div className="actions">
                <label className="compact">
                  Add reviewer
                  <select id="round-add-evaluator" defaultValue="">
                    <option value="">Choose…</option>
                    {results.available_evaluators
                      .filter(
                        (candidate) =>
                          !results.evaluators.some(
                            (current) =>
                              current.evaluator_user_id === candidate.user_id &&
                              current.assigned_count > 0,
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
                  onClick={() =>
                    addEvaluator().catch((error) =>
                      setStatus(errorMessage(error)),
                    )
                  }
                >
                  Add
                </button>
              </div>
            )}
          </div>
          <section className="metrics" aria-label="Evaluator progress">
            {results.evaluators
              .filter(
                (evaluator) =>
                  evaluator.assigned_count > 0 || evaluator.completed_count > 0,
              )
              .map((evaluator) => (
                <article key={evaluator.evaluator_user_id}>
                  <strong>{evaluator.display_name}</strong>
                  <span>
                    {evaluator.completed_count}/{evaluator.assigned_count}{" "}
                    finalized
                  </span>
                  <span>{evaluator.conflict_count} conflicts</span>
                  {results.status === "open" &&
                    evaluator.completed_count < evaluator.assigned_count && (
                      <button
                        className="secondary"
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
                        className="secondary"
                        onClick={() =>
                          removeEvaluator(evaluator).catch((error) =>
                            setStatus(errorMessage(error)),
                          )
                        }
                      >
                        Remove
                      </button>
                    )}
                </article>
              ))}
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
          <div className="meta">
            <h2>Proposal results</h2>
            <label>
              Sort by{" "}
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
          <section className="grid" aria-label="Proposal results">
            {sortedSubmissions.map((submission) => {
              const complete =
                submission.assigned_count > 0 &&
                submission.completed_count === submission.assigned_count;
              const decided = submission.decision !== null;
              const pending =
                pendingDecision?.submission.submission_id ===
                submission.submission_id;
              return (
                <article key={submission.submission_id}>
                  <div className="meta">
                    <span>
                      {decided
                        ? "decision locked"
                        : complete
                          ? "ready for decision"
                          : "review in progress"}
                    </span>
                    <span>{submission.decision || "undecided"}</span>
                  </div>
                  <h3>{submission.proposal_title}</h3>
                  <p className="speaker">{submission.speaker_name}</p>
                  <p>
                    <strong>{submission.average_rating ?? "—"}</strong> mean ·{" "}
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
                            ? `${review.rating ?? "—"} · ${review.recommendation || "No recommendation"}`
                            : review.state.replace("_", " ")}
                        </p>
                        {review.internal_comment && (
                          <p>{review.internal_comment}</p>
                        )}
                      </article>
                    ))}
                  </details>
                  {decided ? (
                    <p>
                      <strong>Internal decision reason:</strong>{" "}
                      {submission.internal_reason || "No reason recorded."}
                    </p>
                  ) : (
                    <label>
                      Internal decision reason
                      <textarea
                        id={`reason-${submission.submission_id}`}
                        rows={3}
                        maxLength={2000}
                        required={!complete}
                      />
                    </label>
                  )}
                  <p className="help">
                    {decided
                      ? "This decision is permanent."
                      : complete
                        ? "Accepting creates onboarding tasks; rejecting closes outstanding tasks."
                        : "Reviews are incomplete. An organizer may override with a required internal reason; the override is audited."}
                  </p>
                  {decided ? null : pending ? (
                    <div className="confirmation" role="alert">
                      <strong>
                        Confirm permanent {pendingDecision.decision}
                        {complete ? "" : " with organizer override"}
                      </strong>
                      <p>This cannot be changed later.</p>
                      <label className="check">
                        <input
                          type="checkbox"
                          checked={sendEmail}
                          onChange={(event) =>
                            setSendEmail(event.target.checked)
                          }
                        />{" "}
                        Email the speaker
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
                    <div className="actions">
                      <button
                        className="secondary"
                        onClick={() => {
                          setPendingDecision({
                            submission,
                            decision: "rejected",
                          });
                          setSendEmail(true);
                        }}
                      >
                        Reject
                      </button>
                      <button
                        onClick={() => {
                          setPendingDecision({
                            submission,
                            decision: "accepted",
                          });
                          setSendEmail(true);
                        }}
                      >
                        Accept
                      </button>
                    </div>
                  )}
                </article>
              );
            })}
          </section>
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
