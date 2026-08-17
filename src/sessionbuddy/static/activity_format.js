(() => {
  "use strict";

  const RESOURCE_LABELS = Object.freeze({
    organization: "organization",
    event: "event",
    proposal: "proposal",
    invitation: "invitation",
    call_for_speaker_form: "call for proposals",
    evaluation_round: "evaluation round",
    evaluation: "review",
    evaluation_assignment: "review assignment",
    event_speaker: "speaker",
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

  function sentence(activity) {
    const actor = String(activity.actor_name || "Someone");
    const subject = activity.subject_name ? ` ${activity.subject_name}` : "";
    return `${actor} ${verb(activity.operation)} ${resourceLabel(activity.resource_type)}${subject}`;
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

  window.SessionBuddyActivityFormat = Object.freeze({ RESOURCE_LABELS, resourceLabel, verb, sentence, relativeTime });
})();
