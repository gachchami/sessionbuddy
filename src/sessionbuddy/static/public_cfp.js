(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const slug = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
  const publicSession = crypto.randomUUID();
  const key = `${crypto.randomUUID()}-${crypto.randomUUID()}`;
  function recordTelemetry(started, response) {
    const navigation = performance.getEntriesByType("navigation")[0];
    const width = innerWidth;
    window.__sessionbuddyTelemetryDraft = { schema_version: 1, page_template: "/cfp/{slug}", navigation_type: navigation?.type || "unknown", device_class: width < 640 ? "mobile" : width < 1024 ? "tablet" : "desktop", sampled: false, lcp_ms: null, inp_ms: null, cls: null, ttfb_ms: navigation?.responseStart ?? null, fcp_ms: null, route_transition_ms: null, critical_api_ms: Math.max(0, performance.now() - started), api_request_id: response.headers.get("x-request-id") };
  }
  async function api(path, options = {}) {
    const started = performance.now();
    const response = await fetch(path, options);
    recordTelemetry(started, response);
    const body = await response.json();
    if (!response.ok) throw new Error(body.error?.message || `Request failed (${response.status})`);
    return body;
  }
  async function load() {
    try {
      const form = await api(`/api/v1/forms/${encodeURIComponent(slug)}`);
      byId("welcome").textContent = form.welcome_text;
      byId("status").textContent = "Published form ready.";
      byId("proposal-form").querySelector("button").disabled = false;
    } catch (error) {
      byId("status").textContent = error.message;
      byId("status").classList.add("error");
    }
  }
  byId("proposal-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      const submission = await api(`/api/v1/forms/${encodeURIComponent(slug)}/submissions`, { method: "POST", headers: { "content-type": "application/json", "idempotency-key": key, "x-public-session-id": publicSession }, body: JSON.stringify(Object.fromEntries(new FormData(form))) });
      byId("status").textContent = "Proposal submitted.";
      byId("receipt").textContent = `Receipt ${submission.id} · ${submission.status}`;
      form.querySelectorAll("input, textarea").forEach((field) => { field.disabled = true; });
    } catch (error) {
      byId("status").textContent = error.message;
      byId("status").classList.add("error");
      button.disabled = false;
    }
  });
  load();
})();
