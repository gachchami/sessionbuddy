document.documentElement.classList.add("js");

const form = document.querySelector("form");
const tokenField = form?.querySelector('input[name="token"]');
const submit = form?.querySelector('button[type="submit"]');
const fragment = new URLSearchParams(window.location.hash.slice(1));
const token = fragment.get("token") || "";

window.history.replaceState(null, "", window.location.pathname);

if (
  form instanceof HTMLFormElement
  && tokenField instanceof HTMLInputElement
  && submit instanceof HTMLButtonElement
) {
  if (/^[A-Za-z0-9_-]{32,128}$/.test(token)) {
    tokenField.value = token;
    form.addEventListener("submit", () => {
      submit.disabled = true;
      submit.textContent = "Signing you in…";
    }, { once: true });
  } else {
    submit.disabled = true;
    const error = document.createElement("div");
    error.className = "status error";
    error.setAttribute("role", "alert");
    error.textContent = "This sign-in link is incomplete or invalid. Request a new link.";
    form.before(error);
  }
}
