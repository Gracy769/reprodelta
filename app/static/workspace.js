(() => {
  "use strict";
  const root = document.getElementById("workspace-root");
  if (!root) return;
  const authBase = root.dataset.authBase?.replace(/\/$/, "");
  const state = { jwt: null, connections: [], repositories: [], selectedRepo: null, reviewNonce: 0, repoNonce: 0 };
  const $ = (id) => document.getElementById(id);
  const clear = (element) => { while (element.firstChild) element.firstChild.remove(); };
  const text = (tag, value, className = "") => {
    const element = document.createElement(tag);
    element.textContent = String(value);
    if (className) element.className = className;
    return element;
  };
  const empty = (element, message) => { clear(element); element.append(text("p", message, "review-empty")); };
  const status = (message, success = false) => {
    const box = $("workspace-alert");
    box.textContent = message;
    box.classList.toggle("success", success);
    box.hidden = false;
  };
  async function timedFetch(url, options = {}, timeout = 12000) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeout);
    try { return await fetch(url, { ...options, signal: controller.signal }); }
    catch (error) { if (error.name === "AbortError") throw new Error("The request took too long. Try again."); throw error; }
    finally { window.clearTimeout(timer); }
  }
  async function account(path) {
    let response = await timedFetch("/auth" + path, { credentials: "include" }, 8000);
    let data = await response.json().catch(() => null);
    if (authBase && (response.status === 401 || data === null)) {
      response = await timedFetch(authBase + path, { credentials: "include" }, 8000);
      data = await response.json().catch(() => null);
    }
    if (!response.ok) throw new Error(data?.message || data?.error || "The account service is unavailable.");
    return data;
  }
  async function api(path, options = {}) {
    const response = await timedFetch("/api/connections" + path, {
      ...options,
      headers: { Authorization: `Bearer ${state.jwt}`, "Content-Type": "application/json", ...(options.headers || {}) },
    });
    if (response.status === 204) return null;
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "The source request failed.");
    return data;
  }
  function safeSourceUrl(value, provider) {
    try {
      const url = new URL(value);
      if (url.protocol !== "https:") return null;
      if (provider === "github" && url.hostname !== "github.com") return null;
      if (provider === "gitlab" && url.hostname !== "gitlab.com") return null;
      return url.href;
    } catch { return null; }
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
    state.connections = result.connections || [];
    ["github", "gitlab"].forEach((provider) => connectionCard(provider, state.connections.find((item) => item.provider === provider)));
    $("source-gate").hidden = state.connections.length > 0;
    if (!state.connections.length) $("source-settings").open = true;
    const lists = await Promise.allSettled(state.connections.map(async (connection) => {
      const response = await api(`/${connection.provider}/repositories`);
      return (response.repositories || []).map((repo) => ({ ...repo, provider: connection.provider,
        key: connection.provider === "github" ? repo.name : String(repo.id) }));
    }));
    state.repositories = lists.flatMap((entry) => entry.status === "fulfilled" ? entry.value : []);
    const failed = lists.find((entry) => entry.status === "rejected");
    if (failed) status(`Some repositories could not be loaded: ${failed.reason.message}`);
    renderRepos();
    await loadQueue();
  }
  function changeButton(change, repository, compact = false) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "review-change-item";
    const details = text("small", `${repository.name} · ${repository.provider === "github" ? "PR" : "MR"} #${change.number}${change.draft ? " · draft" : ""}`);
    button.append(text("strong", change.title || `Change #${change.number}`), details, text("span", "Review ↗"));
    button.addEventListener("click", () => reviewChange({ provider: repository.provider, repoKey: repository.key,
      number: change.number, title: change.title || `Change #${change.number}`, url: change.url }));
    if (compact) button.dataset.compact = "true";
    return button;
  }
  async function loadQueue() {
    const list = $("queue-list");
    if (!state.connections.length) {
      $("queue-scope").textContent = "Connect a source to see recent changes.";
      empty(list, "After connecting, recent open PRs and MRs will appear here. You can also paste a link above.");
      return;
    }
    empty(list, "Looking for open changes in recently updated projects…");
    try {
      const data = await api("/review-queue");
      $("queue-scope").textContent = `Checked ${data.projects_checked} of ${data.projects_available} connected projects. Paste a link above to reach any other change.`;
      clear(list);
      if (!data.changes.length) {
        empty(list, "No open changes in those recent projects. Paste a PR or MR link above, or browse a repository below.");
        return;
      }
      data.changes.forEach((change) => list.append(changeButton(change, {
        name: change.repository, provider: change.provider, key: change.repository_key,
      })));
    } catch (error) {
      $("queue-scope").textContent = "Recent changes are unavailable.";
      empty(list, "Paste a PR or MR link above, or browse a connected repository below.");
      status(error.message);
    }
  }
  function renderRepos() {
    const list = $("repo-list");
    const query = $("repo-search").value.toLowerCase().trim();
    const matches = state.repositories.filter((repo) => repo.name.toLowerCase().includes(query));
    $("repo-count").textContent = query ? `${matches.length} matching projects` : `Showing ${Math.min(10, matches.length)} of ${state.repositories.length} connected projects. Type to find another.`;
    clear(list);
    if (!matches.length) { empty(list, state.connections.length ? "No matching repository in the connected list. Paste a PR link above instead." : "Connect a source first."); return; }
    matches.slice(0, query ? 30 : 10).forEach((repo) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "review-repo-item";
      button.setAttribute("aria-selected", String(state.selectedRepo?.provider === repo.provider && state.selectedRepo?.key === repo.key));
      button.append(text("strong", repo.name), text("small", `${repo.provider.toUpperCase()}${repo.private ? " · PRIVATE" : ""}`));
      button.addEventListener("click", () => selectRepo(repo));
      list.append(button);
    });
  }
  async function selectRepo(repo) {
    state.selectedRepo = repo;
    const nonce = ++state.repoNonce;
    renderRepos();
    $("selected-repository").hidden = false;
    $("selected-repository-name").textContent = repo.name;
    $("selected-repository-status").textContent = "Loading open PRs or MRs…";
    empty($("change-list"), "Checking this repository…");
    $("selected-repository").scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "nearest" });
    try {
      const result = await api(`/${repo.provider}/changes?repository=${encodeURIComponent(repo.key)}`);
      if (nonce !== state.repoNonce) return;
      const list = $("change-list");
      clear(list);
      $("selected-repository-status").textContent = `${result.changes.length} open ${repo.provider === "github" ? "pull requests" : "merge requests"}. Choose one to review its diff.`;
      if (!result.changes.length) { empty(list, "No open changes here. You can still paste a direct link to a closed PR or MR above."); return; }
      result.changes.forEach((change) => list.append(changeButton(change, repo, true)));
      if (result.has_more) status("Showing the first 50 open changes in this repository. Paste a direct link for another one.");
    } catch (error) {
      if (nonce !== state.repoNonce) return;
      $("selected-repository-status").textContent = "Changes could not be loaded.";
      empty($("change-list"), error.message);
    }
  }
  function parseChangeUrl(value) {
    let url;
    try { url = new URL(value.trim()); } catch { throw new Error("Paste a complete GitHub PR or GitLab MR URL."); }
    if (url.protocol !== "https:") throw new Error("Use an HTTPS link to a pull request or merge request.");
    if (url.hostname === "github.com") {
      const match = url.pathname.match(/^\/([^/]+)\/([^/]+)\/pull\/(\d+)\/?$/);
      if (!match) throw new Error("Use a GitHub pull request link, such as github.com/owner/repo/pull/123.");
      return { provider: "github", repoKey: `${match[1]}/${match[2]}`, number: Number(match[3]),
        title: `Pull request #${match[3]}`, url: `https://github.com/${match[1]}/${match[2]}/pull/${match[3]}` };
    }
    if (url.hostname === "gitlab.com") {
      const match = url.pathname.match(/^\/(.+)\/-\/merge_requests\/(\d+)\/?$/);
      if (!match) throw new Error("Use a GitLab merge request link ending in /-/merge_requests/123.");
      if (!/^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)+$/.test(match[1]) || match[1].split("/").some((part) => part === "." || part === "..")) {
        throw new Error("This GitLab project path is not supported.");
      }
      return { provider: "gitlab", repoKey: match[1], number: Number(match[2]),
        title: `Merge request #${match[2]}`, url: `https://gitlab.com/${match[1]}/-/merge_requests/${match[2]}` };
    }
    throw new Error("Use a link from github.com or gitlab.com.");
  }
  function renderReview(result, change) {
    const body = $("review-result-body");
    clear(body);
    const summary = document.createElement("div"); summary.className = "review-result-summary";
    const count = result.changed_file_count_checked ?? 0;
    const dependencyCount = (result.dependency_files || []).length;
    let heading, description;
    if (dependencyCount) {
      heading = `${dependencyCount} dependency file${dependencyCount === 1 ? "" : "s"} changed`;
      description = `We checked ${count} changed file${count === 1 ? "" : "s"} and found a dependency manifest or lockfile. Review the resolved versions and the assertions affected by them.`;
    } else if (result.numpy_mentioned) {
      heading = "NumPy appears in this diff";
      description = `We checked ${count} changed file${count === 1 ? "" : "s"}. NumPy appears in a changed patch, but a version change or behavior change has not been established.`;
    } else {
      heading = "No supported dependency signal found";
      description = `We checked ${count} changed file${count === 1 ? "" : "s"}. That is not a pass or a safety verdict; inspect the full change and its tests before deciding.`;
    }
    summary.append(text("strong", heading), text("p", description));
    body.append(summary);
    (result.findings || []).forEach((finding, index) => {
      const item = document.createElement("article"); item.className = "review-finding";
      const content = document.createElement("div");
      content.append(text("h3", finding.title || "Finding"));
      if (finding.evidence) content.append(text("code", finding.evidence));
      content.append(text("p", finding.action || "Review this part of the change."));
      item.append(text("span", String(index + 1).padStart(2, "0"), "review-finding-index"), content);
      body.append(item);
    });
    const next = document.createElement("div"); next.className = "review-next";
    next.append(text("p", `${result.scope || "A bounded diff review."} No repository code was executed. This is not a reproduced regression.`, "review-scope"));
    const demo = document.createElement("a"); demo.href = "/demo/investigation"; demo.textContent = "See a real paired replay ↗";
    next.append(demo); body.append(next);
    $("review-result-meta").textContent = `${change.provider.toUpperCase()} · ${change.repoKey} · ${change.provider === "github" ? "PR" : "MR"} #${change.number}`;
  }
  async function reviewChange(change) {
    if (!state.connections.some((connection) => connection.provider === change.provider)) {
      $("change-link-help").textContent = `Connect ${change.provider === "github" ? "GitHub" : "GitLab"} below before reviewing this link.`;
      $("change-link-help").classList.add("is-error");
      $("source-settings").open = true;
      $("source-settings").scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    const nonce = ++state.reviewNonce;
    const resultSection = $("review-result");
    resultSection.hidden = false;
    $("review-result-title").textContent = change.title;
    $("review-result-meta").textContent = `${change.provider.toUpperCase()} · ${change.repoKey} · ${change.provider === "github" ? "PR" : "MR"} #${change.number}`;
    const sourceUrl = safeSourceUrl(change.url, change.provider);
    $("review-source-link").hidden = !sourceUrl;
    if (sourceUrl) $("review-source-link").href = sourceUrl;
    empty($("review-result-body"), "Reading the changed files. This usually takes a few seconds…");
    resultSection.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start" });
    try {
      const result = await api(`/${change.provider}/advice?repository=${encodeURIComponent(change.repoKey)}&number=${change.number}`);
      if (nonce !== state.reviewNonce) return;
      renderReview(result, change);
      $("change-link-help").textContent = "Review ready below. The original change remains available from the result panel.";
      $("change-link-help").classList.remove("is-error");
    } catch (error) {
      if (nonce !== state.reviewNonce) return;
      empty($("review-result-body"), `${error.message} Check source access and try this link again.`);
    }
  }
  $("change-link-form").addEventListener("submit", (event) => {
    event.preventDefault();
    try {
      const change = parseChangeUrl($("change-url").value);
      $("change-link-help").classList.remove("is-error");
      reviewChange(change);
    } catch (error) {
      $("change-link-help").textContent = error.message;
      $("change-link-help").classList.add("is-error");
    }
  });
  $("repo-search").addEventListener("input", renderRepos);
  document.querySelectorAll("[data-source]").forEach((card) => {
    const provider = card.dataset.source;
    card.querySelector(".connect-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const input = card.querySelector("input");
      const button = card.querySelector("form button"); button.disabled = true;
      try {
        await api(`/${provider}`, { method: "POST", body: JSON.stringify({ token: input.value.trim() }) });
        input.value = "";
        status(`${provider === "github" ? "GitHub" : "GitLab"} connected. Loading changes…`, true);
        await loadConnections();
      } catch (error) { status(error.message); }
      finally { button.disabled = false; }
    });
    card.querySelector(".disconnect").addEventListener("click", async () => {
      try {
        await api(`/${provider}`, { method: "DELETE" });
        state.selectedRepo = null; state.reviewNonce += 1;
        $("selected-repository").hidden = true; $("review-result").hidden = true;
        status(`${provider === "github" ? "GitHub" : "GitLab"} disconnected.`, true);
        await loadConnections();
      } catch (error) { status(error.message); }
    });
  });
  async function start() {
    try {
      const oauth = await timedFetch("/oauth/session", { credentials: "include" }, 8000)
        .then((response) => response.ok ? response.json() : null).catch(() => null);
      if (oauth?.user && oauth?.token) state.jwt = oauth.token;
      else {
        const session = await account("/get-session");
        if (!session?.user) { $("workspace-loading").hidden = true; $("workspace-signin").hidden = false; return; }
        const token = await account("/token");
        if (!token?.token) throw new Error("The account service did not issue a workspace token.");
        state.jwt = token.token;
      }
      $("workspace-loading").hidden = true;
      $("workspace-content").hidden = false;
      await loadConnections();
    } catch (error) {
      $("workspace-loading").hidden = true;
      if (state.jwt) { $("workspace-content").hidden = false; status(`${error.message} Reload to try again.`); }
      else $("workspace-signin").hidden = false;
    }
  }
  start();
})();
