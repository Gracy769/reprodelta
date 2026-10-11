(() => {
  const root = document.getElementById("workspace-root");
  const base = root.dataset.authBase?.replace(/\/$/, "");
  const alertBox = document.getElementById("workspace-alert");
  const state = { jwt: null, repositories: [], currentRepo: null, currentChange: null, filter: "all" };
  const $ = (id) => document.getElementById(id);
  const show = (message, success = false) => {
    alertBox.textContent = message;
    alertBox.classList.toggle("success", success);
    alertBox.hidden = false;
  };
  const clear = (element) => { while (element.firstChild) element.firstChild.remove(); };
  const empty = (element, text) => {
    clear(element);
    const p = document.createElement("p"); p.className = "empty-state"; p.textContent = text; element.append(p);
  };
  async function account(path, options = {}) {
    if (!base) throw new Error("Sign-in is not configured yet.");
    let response = await fetch("/auth" + path, { credentials: "include", ...options });
    let data = await response.json().catch(() => null);
    // Social sign-in callbacks originate at the managed Auth domain. Browsers
    // that retain its partitioned cookie can complete the session directly.
    if (path !== "/sign-out" && (response.status === 401 || data === null)) {
      response = await fetch(base + path, { credentials: "include", ...options });
      data = await response.json().catch(() => null);
    }
    if (!response.ok) throw new Error(data?.message || data?.error || "Account service is unavailable.");
    return data;
  }
  async function api(path, options = {}) {
    const response = await fetch("/api/connections" + path, {
      ...options,
      headers: { "Authorization": "Bearer " + state.jwt, "Content-Type": "application/json", ...(options.headers || {}) },
    });
    if (response.status === 204) return null;
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "The source request failed.");
    return data;
  }
  function connectionCard(provider, connected) {
    const card = document.querySelector(`[data-source="${provider}"]`);
    card.querySelector(".source-status").textContent = connected ? "Connected" : "Not connected";
    card.querySelector(".source-status").classList.toggle("connected", Boolean(connected));
    card.querySelector(".source-account").textContent = connected?.account_name || "";
    card.querySelector(".source-account").hidden = !connected;
    card.querySelector(".connect-form").hidden = Boolean(connected);
    const oauthLink = card.querySelector(".oauth-source-link");
    if (oauthLink) oauthLink.hidden = Boolean(connected);
    card.querySelector(".source-note").hidden = Boolean(connected);
    card.querySelector(".disconnect").hidden = !connected;
  }
  async function loadConnections() {
    const result = await api("");
    for (const provider of ["github", "gitlab"]) {
      const connected = result.connections.find((x) => x.provider === provider);
      connectionCard(provider, connected);
    }
    const lists = await Promise.allSettled(result.connections.map(async (connection) => {
      const response = await api(`/${connection.provider}/repositories`);
      return response.repositories.map((repo) => ({ ...repo, provider: connection.provider,
        key: connection.provider === "github" ? repo.name : String(repo.id) }));
    }));
    state.repositories = lists.flatMap((entry) => entry.status === "fulfilled" ? entry.value : []);
    const failed = lists.find((entry) => entry.status === "rejected");
    if (failed) show(failed.reason.message);
    renderRepos();
  }
  function renderRepos() {
    const list = $("repo-list"); clear(list);
    const query = $("repo-search").value.toLowerCase().trim();
    const filtered = state.repositories.filter((repo) => repo.name.toLowerCase().includes(query) && (state.filter === "all" || Boolean(repo[state.filter])));
    $("repo-count").textContent = `${filtered.length} shown${state.repositories.length >= 100 ? " · first 100 owned and starred per source" : ""}`;
    if (!filtered.length) { empty(list, state.repositories.length ? "No repositories match your filter." : "Connect GitHub or GitLab to see your repositories."); return; }
    for (const repo of filtered) {
      const button = document.createElement("button"); button.type = "button"; button.className = "repo-item";
      button.setAttribute("aria-selected", String(state.currentRepo?.provider === repo.provider && state.currentRepo?.key === repo.key));
      const body = document.createElement("span"); const name = document.createElement("strong"); name.textContent = repo.name;
      const details = document.createElement("small"); details.textContent = `${repo.provider.toUpperCase()} · ${repo.fork ? "Fork · " : ""}${repo.starred ? "Starred · " : ""}${repo.description || "No description"}`;
      body.append(name, details); const pill = document.createElement("span"); pill.className = "pill";
      const selected = state.currentRepo?.provider === repo.provider && state.currentRepo?.key === repo.key;
      pill.textContent = `${selected ? "SELECTED · " : ""}${repo.private ? "PRIVATE" : "PUBLIC"}`;
      button.append(body, pill); button.addEventListener("click", () => selectRepo(repo)); list.append(button);
    }
  }
  async function selectRepo(repo) {
    state.currentRepo = repo; state.currentChange = null; $("review-change").disabled = true; $("advice-card").hidden = true;
    $("selected-repository").hidden = false;
    $("selected-repository-name").textContent = repo.name;
    $("selected-repository-status").textContent = "Loading pull requests and issues…";
    const repositoryLink = $("selected-repository-link");
    repositoryLink.hidden = !/^https:\/\/(github\.com|gitlab\.com)\//.test(repo.url || "");
    if (!repositoryLink.hidden) repositoryLink.href = repo.url;
    renderRepos(); empty($("change-list"), "Loading open changes…");
    empty($("issue-list"), "Loading open issues…");
    $("selected-repository").scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "center" });
    const loaded = { changes: null, issues: null };
    const updateStatus = () => {
      if (state.currentRepo !== repo) return;
      const label = (count, noun) => count === null ? `${noun} loading` : count === -1 ? `${noun} unavailable` : `${count} open ${noun}`;
      $("selected-repository-status").textContent = `${label(loaded.changes, "PRs")} · ${label(loaded.issues, "issues")}`;
    };
    api(`/${repo.provider}/issues?repository=${encodeURIComponent(repo.key)}`).then((result) => {
      if (state.currentRepo !== repo) return;
      loaded.issues = result.issues.length; updateStatus();
      const list = $("issue-list"); clear(list);
      if (!result.issues.length) { empty(list, "No open issues in this repository."); return; }
      for (const issue of result.issues) {
        const link = document.createElement("a"); link.className = "change-item"; link.href = issue.url;
        link.target = "_blank"; link.rel = "noopener noreferrer";
        link.textContent = `#${issue.number} ${issue.title} ↗`; list.append(link);
      }
    }).catch((error) => { if (state.currentRepo === repo) { loaded.issues = -1; updateStatus(); empty($("issue-list"), error.message); } });
    try {
      const result = await api(`/${repo.provider}/changes?repository=${encodeURIComponent(repo.key)}`);
      if (state.currentRepo !== repo) return;
      loaded.changes = result.changes.length; updateStatus();
      const list = $("change-list"); clear(list);
      if (!result.changes.length) { empty(list, "No open PRs or merge requests in this repository."); return; }
      for (const change of result.changes) {
        const button = document.createElement("button"); button.type = "button"; button.className = "change-item";
        const body = document.createElement("span"); const title = document.createElement("strong"); title.textContent = `#${change.number} ${change.title}`;
        const details = document.createElement("small"); details.textContent = change.draft ? "Draft change" : "Open change";
        body.append(title, details); const arrow = document.createElement("span"); arrow.textContent = "↗";
        button.append(body, arrow); button.addEventListener("click", () => {
          state.currentChange = change; $("review-change").disabled = false; $("advice-card").hidden = true;
          list.querySelectorAll("button").forEach((item) => item.setAttribute("aria-selected", String(item === button)));
        }); list.append(button);
      }
      if (result.has_more) show("Showing the first 50 open changes. Narrow your choice in the source host if it is missing.");
    } catch (error) { if (state.currentRepo === repo) { loaded.changes = -1; updateStatus(); empty($("change-list"), error.message); } }
  }
  $("review-change").addEventListener("click", async () => {
    const repo = state.currentRepo, change = state.currentChange;
    if (!repo || !change) return;
    $("review-change").disabled = true; show("Reading the selected diff…", true);
    try {
      const result = await api(`/${repo.provider}/advice?repository=${encodeURIComponent(repo.key)}&number=${change.number}`);
      const list = $("advice-list"); clear(list);
      for (const line of result.advice) { const item = document.createElement("li"); item.textContent = line; list.append(item); }
      $("advice-scope").textContent = `${result.scope} No repository code was executed. ${result.next_step}`;
      $("advice-card").hidden = false;
      show("Scoped advice is ready. Open the source change for full context.", true);
    } catch (error) { show(error.message); }
    finally { $("review-change").disabled = false; }
  });
  $("repo-search").addEventListener("input", renderRepos);
  document.querySelectorAll(".repo-filter").forEach((button) => button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    document.querySelectorAll(".repo-filter").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    renderRepos();
  }));
  document.querySelectorAll("[data-source]").forEach((card) => {
    const provider = card.dataset.source;
    card.querySelector("form").addEventListener("submit", async (event) => {
      event.preventDefault(); const token = card.querySelector("input").value.trim();
      const button = card.querySelector("form button"); button.disabled = true;
      try { await api(`/${provider}`, { method: "POST", body: JSON.stringify({ token }) });
        card.querySelector("input").value = ""; show(`${provider} connected. Loading repositories…`, true); await loadConnections();
      } catch (error) { show(error.message); } finally { button.disabled = false; }
    });
    card.querySelector(".disconnect").addEventListener("click", async () => {
      try { await api(`/${provider}`, { method: "DELETE" }); state.currentRepo = null; state.currentChange = null; $("selected-repository").hidden = true;
        empty($("change-list"), "Select a repository to load open changes."); $("advice-card").hidden = true;
        show(`${provider} disconnected and its stored token removed.`, true); await loadConnections();
      } catch (error) { show(error.message); }
    });
  });
  $("signout").addEventListener("click", async () => {
    if (state.jwt) {
      await fetch("/oauth/signout", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" } }).catch(() => {});
    }
    try { await account("/sign-out", { method: "POST" }); } catch { /* Always leave the workspace. */ }
    if (base) try { await fetch(base + "/sign-out", { method: "POST", credentials: "include" }); } catch { /* Social cookies may be unavailable. */ }
    window.location.assign("/signin");
  });
  async function start() {
    try {
      const oauth = await fetch("/oauth/session", { credentials: "include" }).then((response) => response.ok ? response.json() : null).catch(() => null);
      if (oauth?.user && oauth?.token) {
        $("workspace-user").textContent = `${oauth.user.name} · ${oauth.user.provider}`;
        $("signout").hidden = false;
        $("workspace-loading").hidden = true; $("workspace-content").hidden = false;
        state.jwt = oauth.token;
        await loadConnections();
        return;
      }
      const session = await account("/get-session");
      if (!session?.user) { $("workspace-loading").hidden = true; $("workspace-signin").hidden = false; return; }
      $("workspace-user").textContent = session.user.name || session.user.email || "Signed in";
      $("signout").hidden = false;
      $("workspace-loading").hidden = true; $("workspace-content").hidden = false;
      try {
        const token = await account("/token");
        if (!token?.token) throw new Error("The account service did not issue a workspace token.");
        state.jwt = token.token;
        await loadConnections();
      } catch (error) { show(`${error.message} Repository connections are unavailable until the account service issues a token.`); }
    } catch (error) { $("workspace-loading").hidden = true; $("workspace-signin").hidden = false; }
  }
  start();
})();
