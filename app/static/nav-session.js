(() => {
  const guest = document.getElementById("nav-account");
  const profile = document.getElementById("nav-profile");
  if (!guest || !profile) return;

  async function session() {
    const firstParty = await fetch("/oauth/session", { credentials: "include" });
    if (firstParty.ok) return firstParty.json();
    const legacy = await fetch("/auth/get-session", { credentials: "include" });
    return legacy.ok ? legacy.json() : null;
  }

  session().then((data) => {
    if (!data?.user) { guest.style.visibility = "visible"; return; }
    guest.hidden = true;
    profile.hidden = false;
    document.getElementById("nav-investigation").hidden = false;
    document.getElementById("nav-profile-name").textContent = data.user.name || "Your account";
    document.getElementById("nav-profile-email").textContent = data.user.masked_email || "Email unavailable";
    if (data.user.avatar_url) document.getElementById("nav-avatar").src = data.user.avatar_url;
    document.querySelectorAll('[data-auth-action="workspace"]').forEach((link) => {
      link.href = "/workspace";
      link.textContent = "Open your workspace ↗";
    });
    document.querySelectorAll('[data-auth-action="investigation"]').forEach((link) => {
      link.href = "/investigations/new";
      link.textContent = "Review a change ↗";
    });
  }).catch(() => { guest.style.visibility = "visible"; });

  document.getElementById("nav-signout").addEventListener("click", async () => {
    await fetch("/oauth/signout", {
      method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
    }).catch(() => {});
    await fetch("/auth/sign-out", { method: "POST", credentials: "include" }).catch(() => {});
    window.location.assign("/");
  });
})();
