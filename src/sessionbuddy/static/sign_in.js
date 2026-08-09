(() => {
  "use strict";
  const form = document.getElementById("sign-in-form");
  const entry = document.getElementById("sign-in-entry");
  const confirmation = document.getElementById("sign-in-sent");
  const status = document.getElementById("status");
  const sentMessage = document.getElementById("sent-message");
  const email = form.elements.email;
  const button = document.getElementById("send-sign-in-link");
  const changeEmail = document.getElementById("change-sign-in-email");
  const requested = new URLSearchParams(location.search).get("redirect") || "/";
  const redirect = requested.startsWith("/") && !requested.startsWith("//") && !requested.includes("\\") ? requested : "/";

  function showStatus(message, error = false) {
    status.textContent = message;
    status.classList.toggle("error", error);
    status.hidden = !message;
  }

  function setSending(sending) {
    form.setAttribute("aria-busy", String(sending));
    email.disabled = sending;
    button.disabled = sending;
    button.textContent = sending ? "Sending…" : "Sign in";
  }

  changeEmail.addEventListener("click", () => {
    confirmation.hidden = true;
    entry.hidden = false;
    email.disabled = false;
    button.disabled = false;
    button.textContent = "Sign in";
    showStatus("");
    email.focus();
    email.select();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const address = email.value;
    setSending(true);
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
