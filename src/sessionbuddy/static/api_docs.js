(() => {
  "use strict";
  const METHODS = ["get", "post", "put", "patch", "delete", "options", "head"];
  const state = { contract: null, operations: [], selectedTag: "all", query: "" };
  const byId = (id) => document.getElementById(id);
  const make = (tag, text, className) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text); if (className) node.className = className; return node; };
  const text = (value, fallback = "") => typeof value === "string" && value.trim() ? value.trim() : fallback;

  function resolve(value) {
    if (!value?.$ref || !state.contract || !value.$ref.startsWith("#/")) return value;
    return value.$ref.slice(2).split("/").reduce((item, part) => item?.[part.replaceAll("~1", "/").replaceAll("~0", "~")], state.contract) || value;
  }
  function typeName(schema) {
    if (!schema) return "unspecified";
    if (schema.$ref) return schema.$ref.split("/").at(-1);
    const value = resolve(schema);
    if (value.enum) return `${value.type || "string"} enum`;
    if (value.type === "array") return `array<${typeName(value.items)}>`;
    if (value.anyOf) return value.anyOf.map(typeName).join(" | ");
    if (value.oneOf) return value.oneOf.map(typeName).join(" | ");
    return value.type || (value.properties ? "object" : "unspecified");
  }
  function example(schema, seen = new Set(), depth = 0) {
    if (!schema || depth > 5) return null;
    for (const key of ["example", "default", "const"]) if (schema[key] !== undefined) return schema[key];
    if (schema.enum?.length) return schema.enum[0];
    if (schema.$ref) { if (seen.has(schema.$ref)) return null; const next = new Set(seen); next.add(schema.$ref); return example(resolve(schema), next, depth + 1); }
    if (schema.anyOf) return example(schema.anyOf.find((item) => resolve(item)?.type !== "null") || schema.anyOf[0], seen, depth + 1);
    if (schema.oneOf) return example(schema.oneOf[0], seen, depth + 1);
    if (schema.type === "array") return [example(schema.items, seen, depth + 1)];
    if (schema.type === "object" || schema.properties) return Object.fromEntries(Object.entries(schema.properties || {}).slice(0, 20).map(([name, value]) => [name, example(value, seen, depth + 1)]));
    if (["integer", "number"].includes(schema.type)) return schema.minimum ?? 0;
    if (schema.type === "boolean") return true;
    return { "date-time": "2027-05-12T09:00:00Z", date: "2027-05-12", email: "person@example.com", uuid: "00000000-0000-4000-8000-000000000000" }[schema.format] || "string";
  }
  function collect(contract) {
    const result = [];
    if (!contract?.paths) throw new Error("invalid contract");
    for (const [path, pathItem] of Object.entries(contract.paths)) for (const method of METHODS) {
      const operation = pathItem?.[method]; if (!operation) continue;
      const tags = (operation.tags || []).filter((tag) => typeof tag === "string" && tag.trim());
      result.push({ method, path, summary: text(operation.summary, text(operation.operationId, "Documented operation")), description: text(operation.description), operationId: text(operation.operationId, "Not specified"), tags: tags.length ? tags : ["Other"], parameters: [...(pathItem.parameters || []), ...(operation.parameters || [])], requestBody: operation.requestBody, responses: operation.responses || {}, security: operation.security === undefined ? contract.security || [] : operation.security });
    }
    return result.sort((a, b) => a.path.localeCompare(b.path) || METHODS.indexOf(a.method) - METHODS.indexOf(b.method));
  }
  function section(title) { const node = make("section", undefined, "contract-section"); node.append(make("h4", title)); return node; }
  function table(headers, rows) {
    const wrap = make("div", undefined, "schema-table-wrap"); const node = make("table", undefined, "schema-table"); const head = make("thead"); const headerRow = make("tr");
    headers.forEach((label) => headerRow.append(make("th", label))); head.append(headerRow); node.append(head); const body = make("tbody");
    rows.forEach((values) => { const row = make("tr"); values.forEach((value) => row.append(make("td", value))); body.append(row); }); node.append(body); wrap.append(node); return wrap;
  }
  function schemaView(schema) {
    const value = resolve(schema) || {}; const required = new Set(value.required || []); const properties = Object.entries(value.properties || {});
    if (!properties.length) return make("p", `${typeName(schema)}${value.description ? ` — ${value.description}` : ""}`, "schema-summary");
    return table(["Field", "Type", "Required", "Description"], properties.map(([name, raw]) => {
      const property = resolve(raw) || raw; const details = [];
      if (property.description) details.push(property.description); if (property.enum) details.push(`Allowed: ${property.enum.join(", ")}`); if (property.minimum !== undefined || property.maximum !== undefined) details.push(`Range: ${property.minimum ?? "−∞"}–${property.maximum ?? "∞"}`); if (property.minLength !== undefined || property.maxLength !== undefined) details.push(`Length: ${property.minLength ?? 0}–${property.maxLength ?? "unbounded"}`);
      return [name, typeName(raw), required.has(name) ? "Yes" : "No", details.join(" · ") || "—"];
    }));
  }
  function exampleView(schema, explicit) { const value = explicit !== undefined ? explicit : example(schema); if (value === null) return null; const node = make("div", undefined, "example-block"); node.append(make("strong", "Example"), make("pre", JSON.stringify(value, null, 2))); return node; }
  function renderSecurity(operation, root) {
    const node = section("Authentication"); const requirements = operation.security || [];
    if (!requirements.length) node.append(make("p", "No authentication scheme is declared for this operation.", "schema-summary"));
    else { const list = make("ul", undefined, "security-list"); for (const name of new Set(requirements.flatMap((item) => Object.keys(item)))) { const scheme = resolve(state.contract.components?.securitySchemes?.[name]) || {}; list.append(make("li", `${name} · ${scheme.type || "scheme"}${scheme.scheme ? ` (${scheme.scheme})` : ""}`)); } node.append(list); }
    root.append(node);
  }
  function renderParameters(operation, root) {
    if (!operation.parameters.length) return; const node = section("Parameters");
    node.append(table(["Name", "Location", "Type", "Required", "Description"], operation.parameters.map(resolve).map((parameter) => [parameter.name || "unnamed", parameter.in || "—", typeName(parameter.schema), parameter.required ? "Yes" : "No", text(parameter.description, "—")]))); root.append(node);
  }
  function renderRequest(operation, root) {
    if (!operation.requestBody) return; const request = resolve(operation.requestBody); const node = section(`Request body${request.required ? " · required" : ""}`);
    for (const [mediaType, media] of Object.entries(request.content || {})) { node.append(make("p", mediaType, "media-type"), schemaView(media.schema)); const sample = exampleView(media.schema, media.example); if (sample) node.append(sample); } root.append(node);
  }
  function renderResponses(operation, root) {
    const node = section("Responses");
    for (const [status, raw] of Object.entries(operation.responses)) { const response = resolve(raw); const block = make("div", undefined, "response-block"); const heading = make("div", undefined, "response-block__heading"); heading.append(make("code", status), make("strong", text(response.description, "Documented response"))); block.append(heading); for (const [mediaType, media] of Object.entries(response.content || {})) { block.append(make("p", mediaType, "media-type"), schemaView(media.schema)); const sample = exampleView(media.schema, media.example); if (sample) block.append(sample); } node.append(block); } root.append(node);
  }
  function card(operation) {
    const node = make("article", undefined, "operation-card"); node.dataset.method = operation.method; const route = make("div", undefined, "operation-card__route"); route.append(make("span", operation.method, "method-badge"), make("code", operation.path)); node.append(route, make("h3", operation.summary)); if (operation.description) node.append(make("p", operation.description, "operation-card__description"));
    const disclosure = make("details"); disclosure.append(make("summary", "Request and response reference")); const meta = make("div", undefined, "operation-meta"); meta.append(make("span", `Operation ID: ${operation.operationId}`), make("span", operation.tags.join(" · "))); disclosure.append(meta); const reference = make("div", undefined, "contract-reference"); renderSecurity(operation, reference); renderParameters(operation, reference); renderRequest(operation, reference); renderResponses(operation, reference); disclosure.append(reference); node.append(disclosure); return node;
  }
  function visible() {
    const query = state.query.toLowerCase(); return state.operations.filter((operation) => (state.selectedTag === "all" || operation.tags.includes(state.selectedTag)) && (!query || [operation.path, operation.summary, operation.description, operation.operationId, ...operation.tags, ...operation.parameters.map((value) => resolve(value)?.name || "")].join(" ").toLowerCase().includes(query)));
  }
  function renderOperations() { const operations = visible(); const list = byId("operation-list"); list.replaceChildren(...(operations.length ? operations.map(card) : [make("p", "No operations match this filter. Clear the search or choose another capability.", "docs-empty")])); byId("result-count").textContent = `${operations.length.toLocaleString()} operation${operations.length === 1 ? "" : "s"} shown`; }
  function renderTags() {
    const counts = new Map(); state.operations.forEach((operation) => operation.tags.forEach((tag) => counts.set(tag, (counts.get(tag) || 0) + 1))); const tags = [...counts].sort(([a], [b]) => a.localeCompare(b)); const list = byId("tag-list"); list.replaceChildren();
    for (const [value, label, count] of [["all", "All capabilities", state.operations.length], ...tags.map(([tag, amount]) => [tag, tag, amount])]) { const button = make("button", undefined, "tag-filter"); button.type = "button"; button.dataset.tag = value; button.setAttribute("aria-current", value === state.selectedTag ? "true" : "false"); button.append(make("span", label), make("span", count)); button.addEventListener("click", () => { state.selectedTag = value; list.querySelectorAll("button").forEach((candidate) => candidate.setAttribute("aria-current", candidate.dataset.tag === value ? "true" : "false")); renderOperations(); }); list.append(button); }
    byId("tag-count").textContent = String(tags.length);
  }
  async function load() {
    try { state.contract = await window.SessionBuddyApi.request("/api/v1/openapi.json", { cache: "no-store" }); state.operations = collect(state.contract); byId("contract-version").textContent = text(state.contract.info?.version, "Not specified"); byId("route-count").textContent = Object.keys(state.contract.paths).length.toLocaleString(); byId("operation-count").textContent = state.operations.length.toLocaleString(); renderTags(); renderOperations(); byId("operation-title").closest(".operation-panel").setAttribute("aria-busy", "false"); }
    catch (_error) { byId("operation-list").replaceChildren(make("p", "The checked contract could not be loaded. Open the raw JSON, then verify this environment allows development documentation.", "docs-error")); byId("result-count").textContent = "Contract unavailable"; }
  }
  byId("operation-search").addEventListener("input", (event) => { state.query = String(event.currentTarget.value || "").trim(); renderOperations(); }); load();
})();
