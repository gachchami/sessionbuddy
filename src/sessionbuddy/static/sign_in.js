(() => {
  "use strict";
  const form = document.getElementById("sign-in-form");
  const status = document.getElementById("status");
  const requested = new URLSearchParams(location.search).get("redirect") || "/";
  const redirect = requested.startsWith("/") && !requested.startsWith("//") && !requested.includes("\\") ? requested : "/";
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button");
    button.disabled = true;
    status.textContent = "Sending your sign-in link…";
    try {
      const response = await fetch("/api/v1/auth/magic-links", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email: new FormData(form).get("email"), redirect_path: redirect }) });
      if (!response.ok) throw new Error(`Request failed (${response.status})`);
      status.textContent = "Check your email. The one-time link expires in 15 minutes.";
    } catch (error) {
      status.textContent = error.message;
      status.focus();
      button.disabled = false;
    }
  });
})();
