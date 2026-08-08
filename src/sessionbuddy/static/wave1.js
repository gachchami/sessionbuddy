(() => {
  "use strict";
  const state = { context: null, csrfToken: null, program: null, form: null };
  const byId = (id) => document.getElementById(id);
  const jsonHeaders = () => ({ "content-type": "application/json" });
  const adminHeaders = () => ({ ...jsonHeaders(), "x-csrf-token": state.csrfToken });
  const key = () => `${crypto.randomUUID()}-${crypto.randomUUID()}`;
  async function api(path, options = {}) {
    const started = performance.now();
    const response = await fetch(path, options);
    if (response.status === 204) return null;
    const body = await response.json();
    if (!response.ok) {
      const error = new Error(body.error?.message || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    const navigation = performance.getEntriesByType("navigation")[0];
    const width = window.innerWidth;
    window.__sessionbuddyTelemetryDraft = {
      schema_version: 1,
      page_template: "/wave-1",
      navigation_type: navigation?.type || "unknown",
      device_class: width < 640 ? "mobile" : width < 1024 ? "tablet" : "desktop",
      sampled: false,
      lcp_ms: null,
      inp_ms: null,
      cls: null,
      ttfb_ms: navigation ? Math.max(0, navigation.responseStart) : null,
      fcp_ms: null,
      route_transition_ms: null,
      critical_api_ms: Math.max(0, performance.now() - started),
      api_request_id: response.headers.get("x-request-id")
    };
    return body;
  }
  function values(form) { return Object.fromEntries(new FormData(form).entries()); }
  function show(id, value) { byId(id).textContent = JSON.stringify(value, null, 2); }
  async function signIn() {
    try {
      byId("sign-in").disabled = true;
      byId("status").textContent = "Creating authenticated local session…";
      const session = await api("/api/v1/demo/session", { method: "POST" });
      state.context = { organization_id: session.organization_id, event_id: session.event_id };
      state.csrfToken = session.csrf_token;
      byId("status").textContent = "Authenticated local organization admin session ready.";
      byId("program-form").querySelector("button").disabled = false;
      byId("program-result").textContent = "Ready to create a program";
      byId("sign-in").textContent = "Refresh organization-admin session";
      byId("sign-in").disabled = false;
      byId("refresh-session").disabled = false;
      byId("logout").disabled = false;
    } catch (error) {
      byId("status").textContent = error.message;
      byId("sign-in").disabled = false;
    }
  }
  byId("program-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      state.program = await api("/api/v1/admin/programs", { method: "POST", headers: { ...adminHeaders(), "idempotency-key": key() }, body: JSON.stringify({ ...state.context, ...values(event.currentTarget) }) });
      const slug = byId("publish-form").elements.slug;
      if (slug.value === "ai-engineer-summit") slug.value = `ai-engineer-summit-${state.program.id.slice(0, 8)}`;
      show("program-result", state.program); byId("publish-form").querySelector("button").disabled = false;
    } catch (error) { byId("status").textContent = error.message; }
  });
  byId("publish-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      state.form = await api(`/api/v1/admin/programs/${state.program.id}/forms/publish`, { method: "POST", headers: { ...adminHeaders(), "idempotency-key": key() }, body: JSON.stringify(values(event.currentTarget)) });
      show("form-result", state.form); byId("submission-form").querySelector("button").disabled = false;
    } catch (error) {
      byId("status").textContent = error.status === 409
        ? "That public slug is already in use. Choose a different slug and publish again."
        : error.message;
    }
  });
  byId("submission-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const submission = await api(`/api/v1/forms/${state.form.slug}/submissions`, { method: "POST", headers: { ...jsonHeaders(), "idempotency-key": key(), "x-public-session-id": crypto.randomUUID() }, body: JSON.stringify(values(event.currentTarget)) });
      show("submission-result", submission); byId("refresh").disabled = false; await refresh();
    } catch (error) { byId("status").textContent = error.message; }
  });
  async function refresh() {
    const result = await api(`/api/v1/admin/programs/${state.program.id}/submissions`);
    const body = byId("submissions"); body.replaceChildren();
    result.data.forEach((item) => { const row = document.createElement("tr"); [item.speaker_name, item.proposal_title, item.status].forEach((value) => { const cell = document.createElement("td"); cell.textContent = value; row.append(cell); }); body.append(row); });
  }
  async function rotateSession() {
    try {
      const session = await api("/api/v1/session/refresh", { method: "POST", headers: adminHeaders(), body: "{}" });
      state.csrfToken = session.csrf_token;
      byId("status").textContent = "Session rotated; the previous session is revoked.";
    } catch (error) { byId("status").textContent = error.message; }
  }
  async function logout() {
    try {
      await api("/api/v1/session/logout", { method: "POST", headers: adminHeaders(), body: "{}" });
      state.context = null; state.csrfToken = null; state.program = null; state.form = null;
      byId("status").textContent = "Signed out. The server-side session is revoked.";
      ["program-form", "publish-form", "submission-form"].forEach((id) => { byId(id).querySelector("button").disabled = true; });
      byId("refresh").disabled = true; byId("refresh-session").disabled = true; byId("logout").disabled = true;
      byId("sign-in").textContent = "Sign in as local demo admin";
    } catch (error) { byId("status").textContent = error.message; }
  }
  byId("refresh").addEventListener("click", refresh);
  byId("refresh-session").addEventListener("click", rotateSession);
  byId("logout").addEventListener("click", logout);
  byId("sign-in").addEventListener("click", signIn);
})();
