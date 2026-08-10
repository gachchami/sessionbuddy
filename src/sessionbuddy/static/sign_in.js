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
    linkButton.disabled = sending;
    passwordButton.textContent = sending && mode === "password" ? "Signing in…" : "Sign in";
    linkButton.textContent = sending && mode === "link" ? "Sending your link…" : "Email me a sign-in link";
  }

  changeEmail.addEventListener("click", () => {
    confirmation.hidden = true;
    entry.hidden = false;
    email.disabled = false;
    password.disabled = false;
    passwordButton.disabled = false;
    linkButton.disabled = false;
    passwordButton.textContent = "Sign in";
    linkButton.textContent = "Email me a sign-in link";
    showStatus("");
    password.value = "";
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
      location.assign(session.redirect_path || redirect);
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
