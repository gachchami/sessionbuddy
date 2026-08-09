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
      throw new ApiError(safeMessage(response.status, body, fallback, bodyRequestId), {
        status: response.status,
        code: typeof body?.error?.code === "string" ? body.error.code : "request_failed",
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
    const markRequiredFields = (root = document) => {
      for (const control of root.querySelectorAll?.("input:required, textarea:required, select:required") || []) {
        const escaped = window.CSS?.escape ? CSS.escape(control.id || "") : control.id;
        const label = control.closest("label") || (escaped ? document.querySelector(`label[for="${escaped}"]`) : null);
        if (!label || label.querySelector(".required-marker")) continue;
        const marker = document.createElement("span");
        marker.className = "required-marker";
        marker.setAttribute("aria-hidden", "true");
        marker.textContent = "*";
        if (label.contains(control)) label.insertBefore(marker, control);
        else label.append(" ", marker);
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
    new MutationObserver((records) => {
      for (const record of records) for (const node of record.addedNodes) {
        if (node.nodeType === Node.ELEMENT_NODE) markRequiredFields(node);
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
    }, true);
  }

  installFormValidation();

  window.SessionBuddyApi = Object.freeze({
    ApiError,
    message,
    parseResponse,
    redirectIfSignedOut,
    request,
    signInPath
  });
})();
