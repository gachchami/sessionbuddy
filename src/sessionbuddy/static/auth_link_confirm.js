document.documentElement.classList.add("js");

const form = document.querySelector("form[data-auto-submit]");
if (form instanceof HTMLFormElement) {
  window.requestAnimationFrame(() => form.requestSubmit());
}
