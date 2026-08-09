(() => {
  "use strict";
  const form = document.getElementById("setup-form");
  const status = document.getElementById("status");
  const submit = document.getElementById("complete-setup");

  function validTimeZone(value) {
    if (!value) return true;
    try { new Intl.DateTimeFormat(undefined, { timeZone: value }).format(); return true; }
    catch (_) { return false; }
  }

  async function initialize() {
    const state = await window.SessionBuddyApi.request("/api/v1/setup/status");
    if (state.configured) {
      location.replace("/");
      return;
    }
    form.hidden = false;
    const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (timeZone) form.elements.admin_time_zone.value = timeZone;
    status.textContent = "This instance is empty and ready to configure.";
  }

  form.addEventListener("input", (event) => event.target.setCustomValidity?.(""));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const timeZone = form.elements.admin_time_zone;
    timeZone.setCustomValidity(validTimeZone(timeZone.value.trim()) ? "" : "Enter a valid IANA time zone, such as Asia/Kolkata.");
    if (!form.reportValidity()) return;
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
    let setupCompleted = false;
    try {
      await window.SessionBuddyApi.request("/api/v1/bootstrap", {
        method: "POST",
        headers: { "content-type": "application/json", "x-bootstrap-token": deploymentKey },
        body: JSON.stringify(payload)
      });
      setupCompleted = true;
      await window.SessionBuddyApi.request("/api/v1/auth/magic-links", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: values.admin_email, redirect_path: "/admin" })
      });
      location.replace("/");
    } catch (error) {
      if (setupCompleted || error.status === 409) {
        location.replace("/");
        return;
      }
      status.className = "status error";
      status.textContent = error.status === 404
        ? "The deployment setup key is missing or incorrect."
        : window.SessionBuddyApi.message(error);
      status.focus();
      submit.disabled = false;
      form.setAttribute("aria-busy", "false");
    }
  });

  initialize().catch((error) => {
    status.className = "status error";
    status.textContent = window.SessionBuddyApi.message(error);
    status.focus();
  });
})();
