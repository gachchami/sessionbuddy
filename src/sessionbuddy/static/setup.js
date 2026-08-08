(() => {
  "use strict";
  const form = document.getElementById("setup-form");
  const configured = document.getElementById("configured");
  const status = document.getElementById("status");
  const submit = document.getElementById("complete-setup");

  async function json(response) {
    const body = await response.json();
    if (!response.ok) {
      const error = new Error(body?.error?.message || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return body;
  }

  function showConfigured(message) {
    form.hidden = true;
    configured.hidden = false;
    status.textContent = message;
    status.className = "status success";
  }

  async function initialize() {
    const state = await json(await fetch("/api/v1/setup/status", { credentials: "same-origin" }));
    if (state.configured) {
      showConfigured("Setup has already been completed.");
      return;
    }
    form.hidden = false;
    const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (timeZone) form.elements.admin_time_zone.value = timeZone;
    status.textContent = "This instance is empty and ready to configure.";
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    form.setAttribute("aria-busy", "true");
    status.className = "status";
    status.textContent = "Completing setup…";
    const values = Object.fromEntries(new FormData(form).entries());
    const deploymentKey = String(values.deployment_key || "");
    form.elements.deployment_key.value = "";
    const payload = {
      organization_name: values.organization_name,
      admin_name: values.admin_name,
      admin_email: values.admin_email,
      admin_job_title: values.admin_job_title || null,
      admin_company: values.organization_name,
      admin_time_zone: values.admin_time_zone || null
    };
    try {
      await json(await fetch("/api/v1/bootstrap", {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json", "x-bootstrap-token": deploymentKey },
        body: JSON.stringify(payload)
      }));
      const signIn = await fetch("/api/v1/auth/magic-links", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: values.admin_email, redirect_path: "/admin" })
      });
      showConfigured(signIn.ok
        ? `Setup complete. Check ${values.admin_email} for your sign-in link.`
        : "Setup complete. Sign in with the administrator email.");
    } catch (error) {
      status.className = "status error";
      status.textContent = error.status === 404
        ? "The deployment setup key is missing or incorrect."
        : error.status === 409
          ? "Setup has already been completed."
          : error.message;
      status.focus();
      submit.disabled = false;
      form.setAttribute("aria-busy", "false");
    }
  });

  initialize().catch((error) => {
    status.className = "status error";
    status.textContent = error.message;
    status.focus();
  });
})();
