(() => {
  "use strict";

  function attach(biography, toggle) {
    if (!biography || !toggle) return () => {};
    const measure = () => {
      if (toggle.getAttribute("aria-expanded") === "true") return;
      toggle.hidden = biography.scrollHeight <= biography.clientHeight + 1;
    };
    const onToggle = () => {
      const expanded = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!expanded));
      toggle.textContent = expanded ? "Show more" : "Show less";
      biography.classList.toggle("is-collapsed", expanded);
      if (expanded) requestAnimationFrame(measure);
    };
    toggle.addEventListener("click", onToggle);
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    observer?.observe(biography);
    requestAnimationFrame(measure);
    document.fonts?.ready.then(measure);
    return () => { toggle.removeEventListener("click", onToggle); observer?.disconnect(); };
  }

  window.SessionBuddyBiographyDisclosure = { attach };
})();
