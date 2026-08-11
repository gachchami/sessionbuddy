(() => {
  "use strict";

  function searchValue(person, field) {
    const nameParts = String(person.display_name || "").trim().split(/\s+/).filter(Boolean);
    const values = {
      name: [person.display_name],
      first_name: [person.first_name || nameParts[0]],
      last_name: [person.last_name || nameParts.slice(1).join(" ")],
      email: [person.email],
      company: [person.company],
    };
    return (field === "all"
      ? [...values.name, ...values.email, ...values.company]
      : values[field] || []
    ).filter(Boolean).join(" ").toLocaleLowerCase();
  }

  function matches(person, query, field = "all") {
    const terms = String(query).trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const value = searchValue(person, field);
    return terms.every((term) => value.includes(term));
  }

  globalThis.SessionBuddyPeopleSearch = Object.freeze({ matches });
})();
