(() => {
  "use strict";

  const RESOURCE_LABELS = Object.freeze({
    organization: "organization",
    event: "event",
    proposal: "proposal",
    invitation: "invitation",
    resource_access_grant: "organizer access",
    call_for_speaker_form: "call for proposals",
    evaluation_round: "evaluation round",
    evaluation: "review",
    evaluation_assignment: "review assignment",
    event_speaker: "speaker",
    speaker_asset_version: "speaker file",
    accepted_session: "session",
    speaker_task: "speaker task",
    schedule_revision: "schedule",
    agenda_item: "agenda item",
  });

  const VERBS = Object.freeze({ create: "created", read: "viewed", update: "updated", delete: "removed" });

  function resourceLabel(resourceType) {
    return RESOURCE_LABELS[resourceType] || String(resourceType || "item").replaceAll("_", " ");
  }

  function verb(operation) {
    return VERBS[operation] || String(operation || "changed");
  }

  function sameName(left, right) {
    return String(left || "").trim().toLowerCase() === String(right || "").trim().toLowerCase();
  }

  // "Nora updated invitation Nora" reads as an echo; when the actor is the
  // subject, the sentence says "their" instead of repeating the name.
  function ownRecord(activity) {
    return Boolean(activity.subject_name) && sameName(activity.actor_name, activity.subject_name);
  }

  // With no subject the bare label reads as a fragment ("updated schedule"), so
  // an unnamed thing takes an article: "a" for something new, "the" otherwise.
  function article(operation, label) {
    if (operation === "create") return /^[aeiou]/i.test(label) ? "an " : "a ";
    return "the ";
  }

  function sentence(activity) {
    const actor = String(activity.actor_name || "Someone");
    const label = resourceLabel(activity.resource_type);
    if (ownRecord(activity)) return `${actor} ${verb(activity.operation)} their ${label}`;
    if (!activity.subject_name) return `${actor} ${verb(activity.operation)} ${article(activity.operation, label)}${label}`;
    return `${actor} ${verb(activity.operation)} ${label} ${activity.subject_name}`;
  }

  function relativeTime(occurredAtMs, now = Date.now()) {
    const elapsedSeconds = Math.round((Number(occurredAtMs) - now) / 1000);
    const intervals = [
      [31_536_000, "year"], [2_592_000, "month"], [604_800, "week"],
      [86_400, "day"], [3_600, "hour"], [60, "minute"],
    ];
    const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    for (const [seconds, unit] of intervals) {
      if (Math.abs(elapsedSeconds) >= seconds) return formatter.format(Math.round(elapsedSeconds / seconds), unit);
    }
    return formatter.format(elapsedSeconds, "second");
  }

  window.SessionBuddyActivityFormat = Object.freeze({ RESOURCE_LABELS, resourceLabel, verb, ownRecord, article, sentence, relativeTime });
})();
