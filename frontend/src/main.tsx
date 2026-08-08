import { FormEvent, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Assignment = {
  id: string; round_name: string; proposal_title: string; proposal_abstract: string;
  speaker_name: string; rating_min: number; rating_max: number; recommendations: string[];
  evaluator_guidance: string; evaluation_state: "not_started" | "draft" | "final";
  rating: number | null; recommendation: string | null; internal_comment: string;
};
type SubmissionResult = {
  submission_id: string; speaker_name: string; proposal_title: string; assigned_count: number;
  completed_count: number; average_rating: number | null; decision: "accepted" | "rejected" | null;
};
type EvaluatorProgress = { evaluator_user_id: string; display_name: string; assigned_count: number; completed_count: number; conflict_count: number };
type ConflictProgress = { assignment_id: string; evaluator_user_id: string; evaluator_name: string; proposal_title: string; conflict_type: string; replacement_required: boolean };
type RoundResults = {
  round_id: string; round_name: string; status: "draft" | "open" | "closed";
  assigned_count: number; completed_count: number; average_rating: number | null;
  submissions: SubmissionResult[]; evaluators: EvaluatorProgress[]; conflicts: ConflictProgress[];
};

declare global { interface Window { __sessionbuddyTelemetryDraft?: Record<string, unknown> } }

function telemetry(pageTemplate: string) {
  const navigation = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
  const width = innerWidth;
  window.__sessionbuddyTelemetryDraft = {
    schema_version: 1, page_template: pageTemplate, navigation_type: navigation?.type || "unknown",
    device_class: width < 640 ? "mobile" : width < 1024 ? "tablet" : "desktop", sampled: false,
    lcp_ms: null, inp_ms: null, cls: null, ttfb_ms: navigation?.responseStart ?? null,
    fcp_ms: null, route_transition_ms: null, critical_api_ms: null, api_request_id: null
  };
}

function mutationHeaders(csrf: string) {
  return { "content-type": "application/json", "x-csrf-token": csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}` };
}

async function responseBody(response: Response, fallback: string) {
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message || fallback);
  return body;
}

function ReviewWorkspace() {
  const [csrf, setCsrf] = useState("");
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [status, setStatus] = useState("Loading your assigned reviews…");

  async function loadAssignments() {
    const body = await responseBody(await fetch("/api/v1/evaluator/assignments"), "Could not load assignments");
    setAssignments(body.data);
    setStatus(`${body.data.length} assigned review${body.data.length === 1 ? "" : "s"}.`);
  }
  async function signIn() {
    const response = await fetch("/api/v1/auth/session");
    if (!response.ok) { location.assign(`/sign-in?redirect=${encodeURIComponent(location.pathname)}`); return; }
    const session = await response.json(); setCsrf(session.csrf_token); await loadAssignments();
  }
  useEffect(() => { telemetry("/reviews"); signIn().catch(() => undefined); }, []);

  async function save(form: HTMLFormElement, assignment: Assignment, state: "draft" | "final") {
    const values = Object.fromEntries(new FormData(form));
    await responseBody(await fetch(`/api/v1/evaluator/assignments/${assignment.id}/evaluation`, {
      method: "PUT", headers: mutationHeaders(csrf), body: JSON.stringify({
        rating: Number(values.rating), recommendation: values.recommendation,
        internal_comment: values.internal_comment, state
      })
    }), "Could not save evaluation");
    await loadAssignments(); setStatus(state === "final" ? "Evaluation finalized." : "Draft saved.");
  }
  async function declareConflict(assignment: Assignment) {
    const conflictType = (document.getElementById(`conflict-type-${assignment.id}`) as HTMLSelectElement).value;
    const explanation = (document.getElementById(`conflict-note-${assignment.id}`) as HTMLTextAreaElement).value;
    await responseBody(await fetch(`/api/v1/evaluator/assignments/${assignment.id}/conflict`, {
      method: "POST", headers: mutationHeaders(csrf),
      body: JSON.stringify({ conflict_type: conflictType, explanation })
    }), "Could not declare conflict");
    await loadAssignments(); setStatus("Conflict declared. The assignment is ready for reassignment.");
  }
  function finalize(event: FormEvent<HTMLFormElement>, assignment: Assignment) {
    event.preventDefault(); save(event.currentTarget, assignment, "final").catch((error) => setStatus(error.message));
  }

  return <main><section className="hero"><h1>Reviews</h1><p>Score assigned proposals and finalize when ready.</p></section><div className="toolbar"><p role="status">{status}</p><button className="secondary" onClick={() => signIn().catch((error) => setStatus(error.message))}>Refresh</button></div><section className="grid" aria-label="Assigned proposals">{assignments.map((assignment) => <article key={assignment.id}><div className="meta"><span>{assignment.round_name}</span><span>{assignment.evaluation_state.replace("_", " ")}</span></div><h2>{assignment.proposal_title}</h2><p className="speaker">{assignment.speaker_name}</p><p>{assignment.proposal_abstract}</p>{assignment.evaluator_guidance && <aside>{assignment.evaluator_guidance}</aside>}<form onSubmit={(event) => finalize(event, assignment)}><label>Rating<input name="rating" type="number" min={assignment.rating_min} max={assignment.rating_max} defaultValue={assignment.rating ?? ""} required disabled={assignment.evaluation_state === "final"} /></label><label>Recommendation<select name="recommendation" defaultValue={assignment.recommendation ?? ""} required disabled={assignment.evaluation_state === "final"}><option value="">Choose…</option>{assignment.recommendations.map((choice) => <option key={choice}>{choice}</option>)}</select></label><label>Internal comment<textarea name="internal_comment" rows={4} defaultValue={assignment.internal_comment} disabled={assignment.evaluation_state === "final"} /></label><div className="actions"><button type="button" className="secondary" disabled={assignment.evaluation_state === "final"} onClick={(event) => save(event.currentTarget.form!, assignment, "draft").catch((error) => setStatus(error.message))}>Save draft</button><button type="submit" disabled={assignment.evaluation_state === "final"}>Finalize</button></div></form>{assignment.evaluation_state !== "final" && <details><summary>Declare a conflict of interest</summary><label>Conflict type<select id={`conflict-type-${assignment.id}`}><option value="speaker_relationship">Speaker relationship</option><option value="same_company">Same company</option><option value="financial">Financial</option><option value="other">Other</option></select></label><label>Explanation<textarea id={`conflict-note-${assignment.id}`} rows={3} required /></label><button className="secondary" onClick={() => declareConflict(assignment).catch((error) => setStatus(error.message))}>Remove my assignment</button></details>}</article>)}</section></main>;
}

function AdminRoundDashboard({ roundId }: { roundId: string }) {
  const [csrf, setCsrf] = useState("");
  const [results, setResults] = useState<RoundResults | null>(null);
  const [status, setStatus] = useState("Loading round progress…");
  const [pendingDecision, setPendingDecision] = useState<{ submission: SubmissionResult; decision: "accepted" | "rejected" } | null>(null);
  const [sendEmail, setSendEmail] = useState(true);
  const [speakerMessage, setSpeakerMessage] = useState("");

  async function load() {
    const body = await responseBody(await fetch(`/api/v1/admin/evaluation-rounds/${roundId}/results`), "Could not load round");
    setResults(body); setStatus(`${body.completed_count} of ${body.assigned_count} evaluations finalized.`);
  }
  async function signIn() {
    const response = await fetch("/api/v1/auth/session");
    if (!response.ok) { location.assign(`/sign-in?redirect=${encodeURIComponent(location.pathname)}`); return; }
    const body = await response.json(); setCsrf(body.csrf_token); await load();
  }
  async function decide(submission: SubmissionResult, decision: "accepted" | "rejected") {
    const reason = (document.getElementById(`reason-${submission.submission_id}`) as HTMLTextAreaElement).value;
    const body = await responseBody(await fetch(`/api/v1/admin/evaluation-rounds/${roundId}/submissions/${submission.submission_id}/decision`, {
      method: "POST", headers: mutationHeaders(csrf), body: JSON.stringify({
        decision, internal_reason: reason, send_email: sendEmail, speaker_message: speakerMessage
      })
    }), "Could not record decision");
    setPendingDecision(null); setSpeakerMessage(""); await load();
    setStatus(`Decision recorded as ${decision}.${body.communication_queued ? " Speaker email queued." : " No email sent."}`);
  }
  async function reassign(conflict: ConflictProgress) {
    const evaluatorId = (document.getElementById(`replacement-${conflict.assignment_id}`) as HTMLSelectElement).value;
    await responseBody(await fetch(`/api/v1/admin/evaluation-assignments/${conflict.assignment_id}/reassign`, {
      method: "POST", headers: mutationHeaders(csrf), body: JSON.stringify({ evaluator_user_id: evaluatorId })
    }), "Could not reassign evaluation");
    await load(); setStatus("Conflicted assignment reassigned.");
  }
  async function closeRound() {
    await responseBody(await fetch(`/api/v1/admin/evaluation-rounds/${roundId}/close`, {
      method: "POST", headers: mutationHeaders(csrf), body: "{}"
    }), "Round cannot be closed yet");
    await load(); setStatus("Round closed. All evaluations are permanently read-only.");
  }
  useEffect(() => { telemetry("/admin/evaluation-rounds/{round_id}"); signIn().catch(() => undefined); }, []);
  const closeReady = !!results && results.status === "open" && results.assigned_count > 0
    && results.completed_count === results.assigned_count
    && !results.conflicts.some((conflict) => conflict.replacement_required);

  return <main><section className="hero"><h1>{results?.round_name || "Round progress"}</h1><p>Monitor completion, resolve conflicts, and decide which sessions move forward.</p></section><div className="toolbar"><p role="status">{status}</p><div className="actions"><button className="secondary" onClick={() => signIn().catch((error) => setStatus(error.message))}>Refresh</button><button disabled={!closeReady} onClick={() => closeRound().catch((error) => setStatus(error.message))}>Close round</button></div></div>{results && <><section className="metrics"><article><span>Completed</span><strong>{results.completed_count}/{results.assigned_count}</strong></article><article><span>Overall mean</span><strong>{results.average_rating ?? "—"}</strong></article><article><span>Round status</span><strong>{results.status}</strong></article></section><h2>Evaluator progress</h2><section className="metrics" aria-label="Evaluator progress">{results.evaluators.map((evaluator) => <article key={evaluator.evaluator_user_id}><strong>{evaluator.display_name}</strong><span>{evaluator.completed_count}/{evaluator.assigned_count} finalized</span><span>{evaluator.conflict_count} conflicts</span></article>)}</section>{results.conflicts.length > 0 && <><h2>Conflicts</h2><section className="grid" aria-label="Declared conflicts">{results.conflicts.map((conflict) => <article key={conflict.assignment_id}><div className="meta"><span>{conflict.conflict_type.replace("_", " ")}</span><span>{conflict.replacement_required ? "replacement required" : "covered"}</span></div><h3>{conflict.proposal_title}</h3><p>{conflict.evaluator_name}</p>{conflict.replacement_required && <><label>Replacement evaluator<select id={`replacement-${conflict.assignment_id}`}><option value="">Choose…</option>{results.evaluators.filter((evaluator) => evaluator.evaluator_user_id !== conflict.evaluator_user_id).map((evaluator) => <option key={evaluator.evaluator_user_id} value={evaluator.evaluator_user_id}>{evaluator.display_name}</option>)}</select></label><button onClick={() => reassign(conflict).catch((error) => setStatus(error.message))}>Reassign</button></>}</article>)}</section></>}<h2>Submission results</h2><section className="grid" aria-label="Submission results">{results.submissions.map((submission) => {
    const complete = submission.assigned_count > 0 && submission.completed_count === submission.assigned_count;
    const decided = submission.decision !== null;
    const pending = pendingDecision?.submission.submission_id === submission.submission_id;
    return <article key={submission.submission_id}><div className="meta"><span>{decided ? "decision locked" : complete ? "ready for decision" : "review in progress"}</span><span>{submission.decision || "undecided"}</span></div><h3>{submission.proposal_title}</h3><p className="speaker">{submission.speaker_name}</p><p><strong>{submission.average_rating ?? "—"}</strong> mean · {submission.completed_count}/{submission.assigned_count} complete</p><label>Internal decision reason<textarea id={`reason-${submission.submission_id}`} rows={3} disabled={decided} /></label><p className="help">{decided ? "This decision is permanent." : "Accepting creates onboarding tasks; rejecting closes outstanding tasks."}</p>{pending ? <div className="confirmation" role="alert"><strong>Confirm permanent {pendingDecision.decision}</strong><p>This cannot be changed later.</p><label className="check"><input type="checkbox" checked={sendEmail} onChange={(event) => setSendEmail(event.target.checked)} /> Email the speaker</label>{sendEmail && <label>Message <span className="optional">Optional</span><textarea rows={3} maxLength={4000} value={speakerMessage} onChange={(event) => setSpeakerMessage(event.target.value)} placeholder="Leave blank to use the standard decision message." /></label>}<div className="actions"><button className="secondary" onClick={() => setPendingDecision(null)}>Cancel</button><button onClick={() => decide(submission, pendingDecision.decision).catch((error) => setStatus(error.message))}>Confirm {pendingDecision.decision}</button></div></div> : <div className="actions"><button className="secondary" disabled={!complete || decided} onClick={() => { setPendingDecision({ submission, decision: "rejected" }); setSendEmail(true); }}>Reject</button><button disabled={!complete || decided} onClick={() => { setPendingDecision({ submission, decision: "accepted" }); setSendEmail(true); }}>Accept</button></div>}</article>;
  })}</section></>}</main>;
}

function App() {
  const match = location.pathname.match(/^\/admin\/evaluation-rounds\/([^/]+)$/);
  return match ? <AdminRoundDashboard roundId={decodeURIComponent(match[1])} /> : <ReviewWorkspace />;
}

createRoot(document.getElementById("root")!).render(<App />);
