"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

require("../../src/sessionbuddy/static/people_search.js");

const person = {
  display_name: "Alex Morgan",
  email: "alex@example.test",
  company: "Northstar Labs",
  organization_roles: ["Organizer"],
  event_associations: [{ event_name: "AI Summit", status: "accepted" }],
};

test("all fields searches only the advertised identity fields", () => {
  const { matches } = globalThis.SessionBuddyPeopleSearch;
  assert.equal(matches(person, "alex", "all"), true);
  assert.equal(matches(person, "example.test", "all"), true);
  assert.equal(matches(person, "northstar", "all"), true);
  assert.equal(matches(person, "organizer", "all"), false);
  assert.equal(matches(person, "summit", "all"), false);
});

test("each selectable field is isolated and case-insensitive", () => {
  const { matches } = globalThis.SessionBuddyPeopleSearch;
  assert.equal(matches(person, "ALEX MOR", "name"), true);
  assert.equal(matches(person, "alex", "first_name"), true);
  assert.equal(matches(person, "morgan", "last_name"), true);
  assert.equal(matches(person, "morgan", "first_name"), false);
  assert.equal(matches(person, "alex@example", "email"), true);
  assert.equal(matches(person, "LABS", "company"), true);
  assert.equal(matches(person, "northstar", "name"), false);
  assert.equal(matches(person, "alex", "company"), false);
});

test("multi-word queries may match separate parts of one selected field", () => {
  const { matches } = globalThis.SessionBuddyPeopleSearch;
  assert.equal(matches(person, "morgan alex", "name"), true);
  assert.equal(matches(person, "alex labs", "all"), true);
  assert.equal(matches(person, "alex missing", "all"), false);
  assert.equal(matches(person, "", "all"), true);
});
