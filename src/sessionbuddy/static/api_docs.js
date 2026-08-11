(() => {
  "use strict";

  const CONTRACT_PATH = "/api/v1/openapi.json";
  const METHODS = ["get", "post", "put", "patch", "delete", "options", "head"];
  const state = { operations: [], selectedTag: "all", query: "" };
  const byId = (id) => document.getElementById(id);

  function make(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }

  function normalizedText(value, fallback) {
    return typeof value === "string" && value.trim() ? value.trim() : fallback;
  }

  function collectOperations(contract) {
    if (!contract || typeof contract !== "object" || !contract.paths || typeof contract.paths !== "object") {
      throw new Error("invalid contract");
    }
    const operations = [];
    for (const [path, pathItem] of Object.entries(contract.paths)) {
      if (!pathItem || typeof pathItem !== "object") continue;
      for (const method of METHODS) {
        const operation = pathItem[method];
        if (!operation || typeof operation !== "object") continue;
        const tags = Array.isArray(operation.tags)
          ? operation.tags.filter((tag) => typeof tag === "string" && tag.trim()).map((tag) => tag.trim())
          : [];
        const parameters = [
          ...(Array.isArray(pathItem.parameters) ? pathItem.parameters : []),
          ...(Array.isArray(operation.parameters) ? operation.parameters : [])
        ];
        operations.push({
          method,
          path,
          summary: normalizedText(operation.summary, normalizedText(operation.operationId, "Documented operation")),
          description: normalizedText(operation.description, "The checked contract contains the complete request and response definition."),
          operationId: normalizedText(operation.operationId, "Not specified"),
          tags: tags.length ? tags : ["Other"],
          parameters: parameters
            .filter((parameter) => parameter && typeof parameter === "object")
            .map((parameter) => normalizedText(parameter.name, "unnamed")),
          responses: operation.responses && typeof operation.responses === "object"
            ? Object.keys(operation.responses)
            : [],
          hasRequestBody: Boolean(operation.requestBody)
        });
      }
    }
    return operations.sort((left, right) => left.path.localeCompare(right.path) || METHODS.indexOf(left.method) - METHODS.indexOf(right.method));
  }

  function tagInventory(operations) {
    const counts = new Map();
    for (const operation of operations) {
      for (const tag of operation.tags) counts.set(tag, (counts.get(tag) || 0) + 1);
    }
    return [...counts.entries()].sort(([left], [right]) => left.localeCompare(right));
  }

  function detail(label, value, wide = false) {
    const wrapper = make("div");
    if (wide) wrapper.className = "operation-details__wide";
    wrapper.append(make("dt", label), make("dd", value));
    return wrapper;
  }

  function chipList(label, values) {
    const wrapper = make("div");
    wrapper.className = "operation-details__wide";
    wrapper.append(make("dt", label));
    const description = make("dd");
    const list = make("ul");
    for (const value of values.length ? values : ["None"]) list.append(make("li", value));
    description.append(list);
    wrapper.append(description);
    return wrapper;
  }

  function operationCard(operation) {
    const card = make("article", undefined, "operation-card");
    card.dataset.method = operation.method;

    const route = make("div", undefined, "operation-card__route");
    route.append(make("span", operation.method, "method-badge"), make("code", operation.path));
    card.append(route, make("h3", operation.summary), make("p", operation.description, "operation-card__description"));

    const disclosure = make("details");
    disclosure.append(make("summary", "Contract details"));
    const details = make("dl", undefined, "operation-details");
    details.append(
      detail("Operation ID", operation.operationId),
      detail("Request body", operation.hasRequestBody ? "Defined in the checked JSON contract" : "None"),
      chipList("Capabilities", operation.tags),
      chipList("Parameters", operation.parameters),
      chipList("Documented responses", operation.responses)
    );
    disclosure.append(details);
    card.append(disclosure);
    return card;
  }

  function visibleOperations() {
    const query = state.query.toLocaleLowerCase();
    return state.operations.filter((operation) => {
      const matchesTag = state.selectedTag === "all" || operation.tags.includes(state.selectedTag);
      const haystack = [operation.path, operation.summary, operation.description, operation.operationId, ...operation.tags]
        .join(" ")
        .toLocaleLowerCase();
      return matchesTag && (!query || haystack.includes(query));
    });
  }

  function renderOperations() {
    const list = byId("operation-list");
    const operations = visibleOperations();
    list.replaceChildren();
    if (!operations.length) {
      list.append(make("p", "No operations match this filter. Clear the search or choose another capability.", "docs-empty"));
    } else {
      list.append(...operations.map(operationCard));
    }
    byId("result-count").textContent = `${operations.length.toLocaleString()} operation${operations.length === 1 ? "" : "s"} shown`;
  }

  function renderTags() {
    const tags = tagInventory(state.operations);
    const list = byId("tag-list");
    list.replaceChildren();
    const choices = [["all", "All capabilities", state.operations.length], ...tags.map(([tag, count]) => [tag, tag, count])];
    for (const [value, label, count] of choices) {
      const button = make("button", undefined, "tag-filter");
      button.type = "button";
      button.dataset.tag = value;
      button.setAttribute("aria-current", value === state.selectedTag ? "true" : "false");
      button.append(make("span", label), make("span", count));
      button.addEventListener("click", () => {
        state.selectedTag = value;
        for (const candidate of list.querySelectorAll("button")) {
          candidate.setAttribute("aria-current", candidate.dataset.tag === value ? "true" : "false");
        }
        renderOperations();
      });
      list.append(button);
    }
    byId("tag-count").textContent = String(tags.length);
  }

  function showFailure() {
    const list = byId("operation-list");
    list.replaceChildren(make("p", "The checked contract could not be loaded. Open the raw JSON, then verify this environment allows development documentation.", "docs-error"));
    byId("result-count").textContent = "Contract unavailable";
    byId("operation-title").closest(".operation-panel").setAttribute("aria-busy", "false");
  }

  async function loadContract() {
    try {
      const contract = await window.SessionBuddyApi.request(
        CONTRACT_PATH,
        { cache: "no-store" },
      );
      state.operations = collectOperations(contract);
      byId("contract-version").textContent = normalizedText(contract.info?.version, "Not specified");
      byId("route-count").textContent = Object.keys(contract.paths).length.toLocaleString();
      byId("operation-count").textContent = state.operations.length.toLocaleString();
      renderTags();
      renderOperations();
      byId("operation-title").closest(".operation-panel").setAttribute("aria-busy", "false");
    } catch (_error) {
      showFailure();
    }
  }

  byId("operation-search").addEventListener("input", (event) => {
    state.query = String(event.currentTarget.value || "").trim();
    renderOperations();
  });
  loadContract();
})();
