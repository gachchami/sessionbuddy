(() => {
  "use strict";
  const section = document.getElementById("account-organization-invitations");
  async function load() {
    const data = await window.SessionBuddyApi.request("/api/v1/account/invitations");
    const list = section.querySelector("ul");
    for (const invitation of data.pending_organization_invitations) {
      const row = document.createElement("li");
      const link = document.createElement("a");
      link.href = `/organization-admin-invitations?invitation=${encodeURIComponent(invitation.id)}`;
      link.textContent = `Review invitation to administer ${invitation.organization_name}`;
      const note=document.createElement("p");
      note.textContent=invitation.needs_reissue ? "Needs reissue: ask a current admin for a replacement." : `Expires ${new Date(invitation.expires_at_ms).toLocaleString()}. Accepting is a separate action.`;
      row.append(link,note); list.append(row);
    }
    section.hidden = !list.children.length;
  }
  load().catch((error) => {
    if(error.status === 401) return;
    section.hidden=false; section.querySelector("[role=status]").textContent=window.SessionBuddyApi.message(error);
  });
})();
