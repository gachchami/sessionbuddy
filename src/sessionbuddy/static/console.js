(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const text = (value) => document.createTextNode(String(value));
  const cell = (tag, value) => { const node = document.createElement(tag); node.append(text(value)); return node; };
  const vitals = { lcp_ms: null, inp_ms: null, cls: 0, fcp_ms: null };

  function observeVitals() {
    if (!("PerformanceObserver" in window)) return;
    const watch = (type, callback) => {
      try { new PerformanceObserver(callback).observe({ type, buffered: true }); } catch (_) { /* unsupported */ }
    };
    watch("largest-contentful-paint", (list) => {
      const entries = list.getEntries();
      if (entries.length) vitals.lcp_ms = entries[entries.length - 1].startTime;
    });
    watch("event", (list) => {
      for (const entry of list.getEntries()) vitals.inp_ms = Math.max(vitals.inp_ms || 0, entry.duration);
    });
    watch("layout-shift", (list) => {
      for (const entry of list.getEntries()) if (!entry.hadRecentInput) vitals.cls += entry.value;
    });
    watch("paint", (list) => {
      const fcp = list.getEntries().find((entry) => entry.name === "first-contentful-paint");
      if (fcp) vitals.fcp_ms = fcp.startTime;
    });
  }

  function parseServerTiming(header) {
    if (!header) return [];
    return header.split(",").flatMap((part) => {
      const match = part.trim().match(/^([a-z]+);dur=([0-9.]+)$/);
      return match ? [{ name: match[1], duration: Number(match[2]) }] : [];
    });
  }

  function telemetryDraft(requestId, criticalApiMs) {
    const navigation = performance.getEntriesByType("navigation")[0];
    const width = window.innerWidth;
    return {
      schema_version: 1,
      page_template: "/engine-room",
      navigation_type: navigation?.type || "unknown",
      device_class: width < 640 ? "mobile" : width < 1024 ? "tablet" : "desktop",
      sampled: false,
      lcp_ms: vitals.lcp_ms,
      inp_ms: vitals.inp_ms,
      cls: vitals.cls,
      ttfb_ms: navigation ? Math.max(0, navigation.responseStart) : null,
      fcp_ms: vitals.fcp_ms,
      critical_api_ms: criticalApiMs,
      api_request_id: requestId || null
    };
  }

  async function load() {
    const started = performance.now();
    try {
      const response = await fetch("/api/v1/engine-room/status", { headers: { accept: "application/json" } });
      if (!response.ok) throw new Error(`Status API returned ${response.status}`);
      const model = await response.json();
      const requestId = response.headers.get("x-request-id");
      const requestMs = Math.max(0, performance.now() - started);
      byId("api-state").textContent = "Healthy response";
      byId("environment").textContent = `${model.environment} · ${model.data_classification}`;
      byId("classification-badge").textContent = model.data_classification.replace("/", " / ");
      byId("runtime").textContent = model.runtime;
      byId("request-id").textContent = requestId || "Unavailable";
      byId("notice").textContent = "Current API evidence loaded. Historical charts are not connected yet.";

      const timings = parseServerTiming(response.headers.get("server-timing"));
      const timingList = byId("timings"); timingList.replaceChildren();
      (timings.length ? timings : [{ name: "round trip", duration: requestMs }]).forEach((item) => {
        const wrap = document.createElement("div");
        wrap.append(cell("dt", item.name), cell("dd", `${item.duration.toFixed(1)} ms`));
        timingList.append(wrap);
      });

      const sloBody = byId("slos"); sloBody.replaceChildren();
      model.slos.forEach((slo) => {
        const row = document.createElement("tr");
        [slo.route, slo.owner, `${slo.p95_ms} ms`, `${(slo.error_rate_max * 100).toFixed(2)}%`, slo.benchmark]
          .forEach((value) => row.append(cell("td", value)));
        sloBody.append(row);
      });

      const coverage = byId("coverage"); coverage.replaceChildren();
      model.coverage.forEach((item) => {
        const row = document.createElement("li");
        row.append(cell("span", `${item.signal} · ${item.source}`));
        const state = cell("strong", item.state); state.className = `state-${item.state}`;
        row.append(state); coverage.append(row);
      });

      const progress = byId("build-progress"); progress.replaceChildren();
      model.build_progress.forEach((item) => {
        const row = document.createElement("tr");
        [item.area, item.capability, item.state.replace("_", " "), item.evidence]
          .forEach((value) => row.append(cell("td", value)));
        progress.append(row);
      });

      // Privacy-safe payload is intentionally not transmitted until an audited,
      // rate-limited ingestion endpoint exists. It is available to integration tests.
      window.__sessionbuddyTelemetryDraft = telemetryDraft(requestId, requestMs);
    } catch (error) {
      byId("api-state").textContent = "Unavailable";
      byId("notice").textContent = `Telemetry missing: ${error.message}`;
      byId("notice").classList.add("error");
    }
  }
  observeVitals();
  load();
})();
