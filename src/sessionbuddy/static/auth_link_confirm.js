document.documentElement.classList.add("js");

const form = document.querySelector("form[data-auto-submit]");
if (form instanceof HTMLFormElement) {
  form.addEventListener("submit", () => {
    const submit = form.querySelector('button[type="submit"]');
    if (submit instanceof HTMLButtonElement) {
      submit.disabled = true;
      submit.textContent = "Signing you in…";
    }
  }, { once: true });
  window.requestAnimationFrame(() => form.requestSubmit());
}
