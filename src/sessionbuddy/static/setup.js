(() => {
  "use strict";
  const form = document.getElementById("setup-form");
  const card = document.getElementById("setup-card");
  const status = document.getElementById("status");
  const submit = document.getElementById("complete-setup");
  const completion = document.getElementById("setup-complete");
  const completionTitle = document.getElementById("setup-complete-title");
  const completionMessage = document.getElementById("setup-complete-message");
  const completionEmail = document.getElementById("setup-complete-email");
  const completionHelp = document.getElementById("setup-complete-help");
  const completionRecovery = document.getElementById("setup-complete-recovery");

  function validTimeZone(value) {
    if (!value) return true;
    try { new Intl.DateTimeFormat(undefined, { timeZone: value }).format(); return true; }
    catch (_) { return false; }
  }

  function showCompletion(address, delivered) {
    form.hidden = true;
    status.hidden = true;
    completion.hidden = false;
    card.setAttribute("aria-labelledby", "setup-complete-title");
    completionEmail.textContent = address;
    completionTitle.textContent = delivered ? "Check your email." : "Setup is complete.";
    completionMessage.textContent = delivered
      ? "We sent your one-time administrator sign-in link to:"
      : "Your organization and administrator were created, but the sign-in email could not be requested for:";
    completionHelp.textContent = delivered
      ? "Open the newest link within 15 minutes to continue to the administrator workspace. You can close this page."
      : "Use the sign-in page to request a new link. You do not need to run setup again.";
    completionRecovery.hidden = delivered;
    completion.focus();
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
    const firstName = form.elements.admin_first_name;
    const lastName = form.elements.admin_last_name;
    timeZone.setCustomValidity(validTimeZone(timeZone.value.trim()) ? "" : "Enter a valid IANA time zone, such as Asia/Kolkata.");
    firstName.setCustomValidity(firstName.value.trim() ? "" : "Enter the administrator's first name.");
    lastName.setCustomValidity(lastName.value.trim() ? "" : "Enter the administrator's last name.");
    if (!form.reportValidity()) return;
    submit.disabled = true;
    form.setAttribute("aria-busy", "true");
    status.className = "status";
    status.textContent = "Completing setup…";
    const values = Object.fromEntries(new FormData(form).entries());
    const deploymentKey = String(values.deployment_key || "");
    const adminFirstName = String(values.admin_first_name).trim();
    const adminLastName = String(values.admin_last_name).trim();
    const adminName = `${adminFirstName} ${adminLastName}`;
    const payload = {
      organization_name: values.organization_name,
      admin_name: adminName,
      admin_first_name: adminFirstName,
      admin_last_name: adminLastName,
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
      // Clear the secret only once it has done its job; a failed attempt keeps
      // it in place so the operator can retry without re-fetching the key.
      form.elements.deployment_key.value = "";
      await window.SessionBuddyApi.request("/api/v1/auth/magic-links", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: values.admin_email, redirect_path: "/admin" })
      });
      showCompletion(values.admin_email, true);
    } catch (error) {
      if (setupCompleted || error.status === 409) {
        showCompletion(values.admin_email, false);
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
