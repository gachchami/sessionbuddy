(() => {
  "use strict";
  const form = document.getElementById("sign-in-form");
  const status = document.getElementById("status");
  const email = form.elements.email;
  const button = document.getElementById("send-sign-in-link");
  const changeEmail = document.getElementById("change-sign-in-email");
  const requested = new URLSearchParams(location.search).get("redirect") || "/";
  const redirect = requested.startsWith("/") && !requested.startsWith("//") && !requested.includes("\\") ? requested : "/";

  function setSending(sending) {
    form.setAttribute("aria-busy", String(sending));
    email.disabled = sending;
    button.disabled = sending;
  }

  changeEmail.addEventListener("click", () => {
    changeEmail.hidden = true;
    email.disabled = false;
    button.disabled = false;
    button.textContent = "Send sign-in link";
    status.textContent = "Enter a different email address.";
    email.focus();
    email.select();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const address = email.value;
    setSending(true);
    changeEmail.hidden = true;
    status.textContent = "Sending your sign-in link…";
    try {
      const response = await fetch("/api/v1/auth/magic-links", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email: address, redirect_path: redirect }) });
      if (!response.ok) throw new Error(`Request failed (${response.status})`);
      status.textContent = "Check your email. The one-time link expires in 15 minutes.";
      form.setAttribute("aria-busy", "false");
      button.textContent = "Email sent";
      changeEmail.hidden = false;
    } catch (error) {
      status.textContent = error.message;
      status.focus();
      setSending(false);
    }
  });
})();
