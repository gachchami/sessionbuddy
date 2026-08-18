(() => {
  "use strict";
  const form = document.getElementById("sign-in-form");
  const entry = document.getElementById("sign-in-entry");
  const confirmation = document.getElementById("sign-in-sent");
  const status = document.getElementById("status");
  const sentMessage = document.getElementById("sent-message");
  const email = form.elements.email;
  const password = form.elements.password;
  const passwordButton = document.getElementById("password-sign-in");
  const passwordToggle = document.getElementById("toggle-password");
  const linkButton = document.getElementById("send-sign-in-link");
  const changeEmail = document.getElementById("change-sign-in-email");
  const requested = new URLSearchParams(location.search).get("redirect") || "/";
  const redirect = requested.startsWith("/") && !requested.startsWith("//") && !requested.includes("\\") ? requested : "/";

  function showStatus(message, error = false) {
    status.textContent = message;
    status.classList.toggle("error", error);
    status.hidden = !message;
  }

  function setSending(sending, mode = "password") {
    form.setAttribute("aria-busy", String(sending));
    email.disabled = sending;
    password.disabled = sending;
    passwordButton.disabled = sending;
    passwordToggle.disabled = sending;
    linkButton.disabled = sending;
    passwordButton.textContent = sending && mode === "password" ? "Signing in…" : "Sign in";
    linkButton.textContent = sending && mode === "link" ? "Sending your link…" : "Send a sign-in link";
  }

  passwordToggle.addEventListener("click", () => {
    const showing = password.type === "text";
    password.type = showing ? "password" : "text";
    passwordToggle.textContent = showing ? "Show" : "Hide";
    passwordToggle.setAttribute("aria-pressed", String(!showing));
    password.focus();
  });

  changeEmail.addEventListener("click", () => {
    confirmation.hidden = true;
    entry.hidden = false;
    email.disabled = false;
    password.disabled = false;
    passwordButton.disabled = false;
    passwordToggle.disabled = false;
    linkButton.disabled = false;
    passwordButton.textContent = "Sign in";
    linkButton.textContent = "Send a sign-in link";
    showStatus("");
    password.value = "";
    password.type = "password";
    passwordToggle.textContent = "Show";
    passwordToggle.setAttribute("aria-pressed", "false");
    email.focus();
    email.select();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const address = email.value;
    setSending(true, "password");
    showStatus("");
    try {
      const session = await window.SessionBuddyApi.request("/api/v1/auth/password/sign-in", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: address, password: password.value, redirect_path: redirect })
      });
      window.SessionBuddyApi.prepareForSessionReplacement();
      // The sign-in response and the next document request can reach different
      // D1 replicas immediately after an authority change. Confirm the freshly
      // created session before following a role-scoped redirect so a stale
      // grant projection cannot send the browser back into a revoked workspace.
      let destination = session.redirect_path || redirect;
      try {
        const authoritative = await window.SessionBuddyApi.request("/api/v1/auth/session");
        const recovery = authoritative.workspace_path === null;
        if (recovery) destination = "/account?workspace=recovery";
      } catch (_) {
        // The cookie is already established. Let the server resolve `/`
        // instead of claiming sign-in failed or trusting a possibly stale path.
        destination = "/";
      }
      location.assign(destination);
    } catch (error) {
      showStatus(error.status === 401
        ? "Email or password is incorrect. Try again or request a sign-in link."
        : window.SessionBuddyApi.message(error, "We could not sign you in. Try again."), true);
      status.focus();
      setSending(false);
    }
  });

  linkButton.addEventListener("click", async () => {
    if (!email.reportValidity()) return;
    const address = email.value;
    setSending(true, "link");
    showStatus("");
    try {
      await window.SessionBuddyApi.request("/api/v1/auth/magic-links", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: address, redirect_path: redirect })
      });
      sentMessage.textContent = `We sent a sign-in link to ${address}.`;
      entry.hidden = true;
      confirmation.hidden = false;
      confirmation.focus();
    } catch (error) {
      showStatus(window.SessionBuddyApi.message(error, "We could not send the sign-in link. Try again."), true);
      status.focus();
      setSending(false);
    }
  });
})();
