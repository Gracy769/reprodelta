(() => {
  const account = document.getElementById("nav-account");
  if (!account) return;
  fetch("/oauth/session", { credentials: "include" })
    .then(async (response) => {
      if (response.ok) return response.json();
      const legacy = await fetch("/auth/get-session", { credentials: "include" });
      return legacy.ok ? legacy.json() : null;
    })
    .then((session) => {
      if (!session?.user) return;
      document.getElementById("nav-investigation").hidden = false;
      document.getElementById("nav-signin").hidden = false;
      account.href = "/workspace";
      account.innerHTML = "Workspace <span aria-hidden=\"true\">↗</span>";
      document.querySelectorAll('[data-auth-action="workspace"]').forEach((link) => {
        link.href = "/investigations/new";
        link.innerHTML = "Open your investigations <span aria-hidden=\"true\">↗</span>";
      });
      document.querySelectorAll('[data-auth-action="investigation"]').forEach((link) => {
        link.href = "/investigations/new";
        link.innerHTML = "Launch investigation <span aria-hidden=\"true\">↗</span>";
      });
    })
    .catch(() => {});
})();
