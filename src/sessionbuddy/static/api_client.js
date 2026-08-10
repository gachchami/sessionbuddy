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
    constructor(message, { status = 0, code = "request_failed", requestId = "", retryable = false, details = [], cause } = {}) {
      super(message, cause ? { cause } : undefined);
      this.name = "ApiError";
      this.status = status;
      this.code = code;
      this.requestId = requestId;
      this.retryable = retryable;
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
        details: Array.isArray(body?.error?.details) ? body.error.details : []
      });
    }
    return body;
  }

  async function request(path, options = {}, behavior = {}) {
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
    document.addEventListener("submit", (event) => {
      validateRequiredText(event.target);
      if (!event.target.checkValidity()) {
        event.preventDefault();
        event.target.querySelector(":invalid")?.focus();
      }
    }, true);
    document.addEventListener("invalid", (event) => {
      const control = event.target;
      control.form?.classList.add("validation-attempted");
      control.setAttribute?.("aria-invalid", "true");
    }, true);
    const clearValidState = (event) => {
      const control = event.target;
      if (control.matches?.('input:required:not([type="checkbox"]):not([type="radio"]):not([type="file"]), textarea:required')) {
        control.setCustomValidity(String(control.value || "").trim() ? "" : "This field is required.");
      }
      if (control.validity?.valid) control.removeAttribute?.("aria-invalid");
    };
    document.addEventListener("input", clearValidState, true);
    document.addEventListener("change", clearValidState, true);
    document.addEventListener("reset", (event) => {
      event.target.classList?.remove("validation-attempted");
      for (const control of event.target.querySelectorAll?.('[aria-invalid="true"]') || []) {
        control.removeAttribute("aria-invalid");
      }
      requestAnimationFrame(() => installCharacterCounters(event.target));
    }, true);
    document.addEventListener("focusin", (event) => {
      if (event.target.matches?.('textarea[maxlength]:not([readonly])')) updateCharacterCounter(event.target);
    }, true);
    window.addEventListener("pageshow", () => installCharacterCounters());

    return { installCharacterCounters };
  }

  const formValidation = installFormValidation();

  window.SessionBuddyApi = Object.freeze({
    ApiError,
    message,
    parseResponse,
    refreshCharacterCounters: formValidation.installCharacterCounters,
    redirectIfSignedOut,
    request,
    signInPath
  });
})();
