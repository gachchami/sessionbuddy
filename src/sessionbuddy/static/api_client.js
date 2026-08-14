(() => {
  "use strict";

  const DEFAULT_MESSAGES = {
    400: "We could not process that request. Check the information and try again.",
    401: "Your session has expired. Sign in again.",
    403: "You do not have permission to do that.",
    404: "The requested information could not be found.",
    409: "This information changed elsewhere. Reload and try again.",
    413: "That upload is too large.",
    415: "That file or request format is not supported.",
    422: "Check the information you entered and try again.",
    429: "Too many requests were made. Wait a moment and try again.",
    503: "This service is temporarily unavailable. Try again shortly."
  };

  class ApiError extends Error {
    constructor(message, { status = 0, code = "request_failed", requestId = "", retryable = false, field = "", details = [], cause } = {}) {
      super(message, cause ? { cause } : undefined);
      this.name = "ApiError";
      this.status = status;
      this.code = code;
      this.requestId = requestId;
      this.retryable = retryable;
      this.field = field;
      this.details = details;
    }
  }

  function withReference(message, requestId) {
    return requestId ? `${message} Reference: ${requestId}` : message;
  }

  function safeMessage(status, body, fallback, requestId) {
    if (status >= 500) {
      return withReference("Something went wrong on our side. Try again.", requestId);
    }
    const serverMessage = body?.error?.message;
    const message = typeof serverMessage === "string" && serverMessage.trim()
      ? serverMessage.trim()
      : DEFAULT_MESSAGES[status] || fallback || "The request could not be completed. Try again.";
    return withReference(message, requestId);
  }

  async function parseResponse(response, { fallback, expectJson = true } = {}) {
    const requestId = response.headers.get("x-request-id") || "";
    const contentType = (response.headers.get("content-type") || "").toLowerCase();
    let body = null;

    if (response.status !== 204 && contentType.includes("json")) {
      try {
        body = await response.json();
      } catch (cause) {
        throw new ApiError(
          withReference("The server returned an unexpected response. Try again.", requestId),
          { status: response.status, code: "invalid_response", requestId, retryable: true, cause }
        );
      }
    } else if (response.ok && response.status !== 204 && expectJson) {
      throw new ApiError(
        withReference("The server returned an unexpected response. Try again.", requestId),
        { status: response.status, code: "invalid_response", requestId, retryable: true }
      );
    }

    if (!response.ok) {
      const bodyRequestId = typeof body?.request_id === "string" ? body.request_id : requestId;
      const conflictType = response.headers.get("x-conflict-type");
      throw new ApiError(safeMessage(response.status, body, fallback, bodyRequestId), {
        status: response.status,
        code: conflictType
          ? `${conflictType}_conflict`
          : typeof body?.error?.code === "string" ? body.error.code : "request_failed",
        requestId: bodyRequestId,
        retryable: response.status === 408 || response.status === 429 || response.status >= 500,
        field: typeof body?.error?.field === "string" ? body.error.field : "",
        details: Array.isArray(body?.error?.metadata?.details) ? body.error.metadata.details : []
      });
    }
    return body;
  }

  let inFlightSessionRequest = null;

  async function performRequest(path, options = {}, behavior = {}) {
    const headers = new Headers(options.headers || {});
    if (!headers.has("accept")) headers.set("accept", "application/json");
    let response;
    try {
      response = await fetch(path, { credentials: "same-origin", ...options, headers });
    } catch (cause) {
      throw new ApiError("We could not connect. Check your connection and try again.", {
        code: "network_error",
        retryable: true,
        cause
      });
    }
    if (typeof behavior.onResponse === "function") behavior.onResponse(response);
    return parseResponse(response, behavior);
  }

  function request(path, options = {}, behavior = {}) {
    const method = String(options.method || "GET").toUpperCase();
    if (path === "/api/v1/auth/session" && method === "GET") {
      if (!inFlightSessionRequest) {
        inFlightSessionRequest = performRequest(path, options, behavior)
          .finally(() => { inFlightSessionRequest = null; });
      }
      return inFlightSessionRequest;
    }
    return performRequest(path, options, behavior);
  }

  function signInPath() {
    return `/sign-in?redirect=${encodeURIComponent(location.pathname + location.search)}`;
  }

  function redirectIfSignedOut(error) {
    if (error?.status !== 401) return false;
    location.assign(signInPath());
    return true;
  }

  function message(error, fallback = "The request could not be completed. Try again.") {
    return error instanceof ApiError ? error.message : fallback;
  }

  function installFormValidation() {
    let counterSequence = 0;
    const updateCharacterCounter = (control) => {
      const counterId = control.dataset.characterCounterId;
      const counter = counterId ? document.getElementById(counterId) : null;
      if (!counter) return;
      counter.textContent = `${control.value.length.toLocaleString()} of ${control.maxLength.toLocaleString()} characters`;
    };
    const installCharacterCounters = (root = document) => {
      const selector = 'textarea[maxlength]:not([readonly]):not([data-character-counter="manual"])';
      const controls = root.matches?.(selector) ? [root] : [];
      controls.push(...(root.querySelectorAll?.(selector) || []));
      for (const control of controls) {
        if (!control.dataset.characterCounterId) {
          const counter = document.createElement("small");
          counter.id = `character-counter-${++counterSequence}`;
          counter.className = "character-counter";
          counter.setAttribute("aria-live", "polite");
          control.dataset.characterCounterId = counter.id;
          const describedBy = new Set((control.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
          describedBy.add(counter.id);
          control.setAttribute("aria-describedby", [...describedBy].join(" "));
          control.after(counter);
          control.addEventListener("input", () => updateCharacterCounter(control));
        }
        updateCharacterCounter(control);
      }
    };
    const requiredLabel = (label, control) => {
      const existing = Array.from(label.children).find((child) => child.classList.contains("field-label"));
      if (existing) return existing;
      const wrapper = document.createElement("span");
      wrapper.className = "field-label";
      if (!label.contains(control)) {
        wrapper.append(...label.childNodes);
        label.append(wrapper);
        return wrapper;
      }
      let controlBranch = control;
      while (controlBranch.parentElement && controlBranch.parentElement !== label) controlBranch = controlBranch.parentElement;
      if (label.firstChild === controlBranch) {
        while (controlBranch.nextSibling) wrapper.append(controlBranch.nextSibling);
        controlBranch.after(wrapper);
        return wrapper;
      }
      while (label.firstChild && label.firstChild !== controlBranch) wrapper.append(label.firstChild);
      label.insertBefore(wrapper, controlBranch);
      return wrapper;
    };
    const markRequiredFields = (root = document) => {
      const selector = "input:required, textarea:required, select:required";
      const controls = root.matches?.(selector) ? [root] : [];
      controls.push(...(root.querySelectorAll?.(selector) || []));
      for (const control of controls) {
        const escaped = window.CSS?.escape ? CSS.escape(control.id || "") : control.id;
        const label = control.closest("label") || (escaped ? document.querySelector(`label[for="${escaped}"]`) : null);
        if (!label || label.querySelector(".required-marker")) continue;
        const marker = document.createElement("span");
        marker.className = "required-marker";
        marker.setAttribute("aria-hidden", "true");
        marker.textContent = "*";
        requiredLabel(label, control).append(marker);
      }
    };
    const validateRequiredText = (form) => {
      for (const control of form.querySelectorAll('input:required:not([type="checkbox"]):not([type="radio"]):not([type="file"]), textarea:required')) {
        const missing = !String(control.value || "").trim();
        control.setCustomValidity(missing ? "This field is required." : "");
        if (missing) control.setAttribute("aria-invalid", "true");
      }
    };
    markRequiredFields();
    installCharacterCounters();
    new MutationObserver((records) => {
      for (const record of records) for (const node of record.addedNodes) {
        if (node.nodeType === Node.ELEMENT_NODE) {
          markRequiredFields(node);
          installCharacterCounters(node);
        }
      }
    }).observe(document.documentElement, { childList: true, subtree: true });
    document.addEventListener("click", (event) => {
      const submit = event.target.closest?.('button:not([type]), button[type="submit"], input[type="submit"]');
      const form = submit?.form;
      if (form) {
        form.classList.add("validation-attempted");
        validateRequiredText(form);
      }
    }, true);
    // A blocked submit used to be invisible: the browser's own bubble is not in
    // the DOM or the accessibility tree, so the only signals were a red border
    // and a focus jump. A form that refuses to submit and says nothing reads as
    // a dead button - and the retry that follows is how duplicate records get
    // created.
    let errorSeq = 0;
    const errorNodeFor = (control) => {
      if (!control.id) control.id = `sb-field-${++errorSeq}`;
      const id = `${control.id}-error`;
      let node = document.getElementById(id);
      if (!node) {
        node = document.createElement("p");
        node.id = id;
        node.className = "field-error";
        node.setAttribute("role", "alert");
        node.hidden = true;
        // Keep the error associated through aria-errormessage, but outside the
        // <label>. Text appended inside a label becomes part of the control's
        // accessible name (for example "Maximum rating Maximum rating") and
        // makes otherwise distinct controls ambiguous to assistive tools.
        const label = control.closest("label");
        if (label) label.after(node);
        else control.parentElement?.append(node);
      }
      return node;
    };
    const showFieldError = (control) => {
      if (!control.validationMessage && control.validity?.valid) return;
      const node = errorNodeFor(control);
      node.textContent = control.validationMessage || "This field is required.";
      node.hidden = false;
      control.setAttribute("aria-errormessage", node.id);
      control.setAttribute("aria-invalid", "true");
      // Reveal the control: a message inside a collapsed disclosure helps nobody.
      let box = control.closest("details");
      while (box) {
        box.open = true;
        box = box.parentElement?.closest("details") ?? null;
      }
    };
    const clearFieldError = (control) => {
      const node = control.id ? document.getElementById(`${control.id}-error`) : null;
      if (node) { node.textContent = ""; node.hidden = true; }
      control.removeAttribute?.("aria-errormessage");
    };
    const summaryFor = (form) => {
      let node = form.querySelector(":scope > .form-error-summary");
      if (!node) {
        node = document.createElement("p");
        node.className = "form-error-summary";
        node.setAttribute("role", "alert");
        node.hidden = true;
        form.prepend(node);
      }
      return node;
    };
    // Read validity without calling checkValidity(): that method dispatches a
    // fresh `invalid` event for every bad control. Calling it while building
    // the summary would schedule another summary forever.
    const invalidControls = (form) =>
      [...form.elements].filter((el) => el.willValidate && !el.validity.valid);
    // Recomputes the summary from the form's current state. Creates the node
    // only when there is something to say, so ordinary typing does not litter
    // every form with an empty alert.
    const refreshFormSummary = (form, { create = false } = {}) => {
      if (!form) return null;
      const existing = form.querySelector(":scope > .form-error-summary");
      const invalid = invalidControls(form);
      if (!invalid.length) {
        if (existing) { existing.textContent = ""; existing.hidden = true; }
        return null;
      }
      if (!existing && !create) return invalid[0];
      const node = summaryFor(form);
      const first = invalid[0];
      const name = first.labels?.[0]?.textContent?.replace(/\*$/, "").trim() || "a required field";
      node.textContent = invalid.length === 1
        ? `This form was not submitted: ${name} - ${first.validationMessage || "This field is required."}`
        : `This form was not submitted: ${invalid.length} fields need attention, starting with ${name}.`;
      node.hidden = false;
      return first;
    };
    const announceFormErrors = (form) => {
      const invalid = invalidControls(form);
      invalid.forEach(showFieldError);
      return refreshFormSummary(form, { create: true });
    };
    const showServerValidationErrors = (form, error) => {
      if (!form || !(error instanceof ApiError)) return false;
      const details = error.details.length
        ? error.details
        : error.field ? [{ field: error.field, message: error.message }] : [];
      let first = null;
      for (const detail of details) {
        const path = String(detail.field || "");
        const rootName = path.split(".")[0];
        const escapedPath = window.CSS?.escape ? CSS.escape(path) : path;
        const escapedName = window.CSS?.escape ? CSS.escape(rootName) : rootName;
        const control = form.querySelector(`[data-field-path="${escapedPath}"]`)
          || form.querySelector(`[name="${escapedName}"]`);
        if (!control) continue;
        const node = errorNodeFor(control);
        node.textContent = String(detail.message || error.message);
        node.hidden = false;
        control.setAttribute("aria-errormessage", node.id);
        control.setAttribute("aria-invalid", "true");
        let box = control.closest("details");
        while (box) {
          box.open = true;
          box = box.parentElement?.closest("details") ?? null;
        }
        first ||= control;
      }
      first?.focus();
      return Boolean(first);
    };
    // Native validation fires `invalid` per control and never dispatches
    // `submit`, so the summary has to be built from here. Batch one frame so a
    // form with four empty fields produces one announcement, not four.
    const summaryPending = new Set();
    const scheduleSummary = (form) => {
      if (!form || summaryPending.has(form)) return;
      summaryPending.add(form);
      requestAnimationFrame(() => {
        summaryPending.delete(form);
        const first = refreshFormSummary(form, { create: true });
        if (first && form.contains(document.activeElement) === false) first.focus();
      });
    };
    document.addEventListener("submit", (event) => {
      validateRequiredText(event.target);
      if (!event.target.checkValidity()) {
        event.preventDefault();
        (announceFormErrors(event.target) ?? event.target.querySelector(":invalid"))?.focus();
      } else {
        const summary = event.target.querySelector(":scope > .form-error-summary");
        if (summary) summary.hidden = true;
      }
    }, true);
    document.addEventListener("invalid", (event) => {
      const control = event.target;
      control.form?.classList.add("validation-attempted");
      control.setAttribute?.("aria-invalid", "true");
      showFieldError(control);
      scheduleSummary(control.form);
    }, true);
    const clearValidState = (event) => {
      const control = event.target;
      if (control.matches?.('input:required:not([type="checkbox"]):not([type="radio"]):not([type="file"]), textarea:required')) {
        control.setCustomValidity(String(control.value || "").trim() ? "" : "This field is required.");
      }
      if (control.validity?.valid) {
        control.removeAttribute?.("aria-invalid");
        clearFieldError(control);
        // The summary is stale the moment a field is corrected; recompute it
        // rather than leaving "this form was not submitted" on screen.
        refreshFormSummary(control.form);
      }
    };
    document.addEventListener("input", clearValidState, true);
    document.addEventListener("change", clearValidState, true);
    document.addEventListener("reset", (event) => {
      const form = event.target;
      if (!form?.elements) return;
      // Reset happens before the fields are cleared, so tidy up afterwards.
      requestAnimationFrame(() => {
        form.classList.remove("validation-attempted");
        for (const control of form.elements) {
          control.setCustomValidity?.("");
          control.removeAttribute?.("aria-invalid");
          clearFieldError(control);
        }
        const summary = form.querySelector(":scope > .form-error-summary");
        if (summary) { summary.textContent = ""; summary.hidden = true; }
        installCharacterCounters(form);
      });
    }, true);
    document.addEventListener("focusin", (event) => {
      if (event.target.matches?.('textarea[maxlength]:not([readonly])')) updateCharacterCounter(event.target);
    }, true);
    window.addEventListener("pageshow", () => installCharacterCounters());

    return { installCharacterCounters, showServerValidationErrors };
  }

  const formValidation = installFormValidation();

  window.SessionBuddyApi = Object.freeze({
    ApiError,
    message,
    parseResponse,
    refreshCharacterCounters: formValidation.installCharacterCounters,
    showValidationErrors: formValidation.showServerValidationErrors,
    redirectIfSignedOut,
    request,
    signInPath
  });
})();
