(() => {
  const account = document.getElementById("nav-account");
  if (!account) return;
  fetch("/auth/get-session", { credentials: "include" })
    .then((response) => response.ok ? response.json() : null)
    .then((session) => {
      if (!session?.user) return;
      document.getElementById("nav-investigation").hidden = false;
      account.href = "/workspace";
      account.innerHTML = "Workspace <span aria-hidden=\"true\">↗</span>";
      document.querySelectorAll('[data-auth-action="workspace"]').forEach((link) => {
        link.href = "/workspace";
        link.innerHTML = "Open your workspace <span aria-hidden=\"true\">↗</span>";
      });
      document.querySelectorAll('[data-auth-action="investigation"]').forEach((link) => {
        link.href = "/investigations/new";
        link.innerHTML = "Launch investigation <span aria-hidden=\"true\">↗</span>";
      });
    })
    .catch(() => {});
})();
