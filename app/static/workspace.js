(() => {
  "use strict";
  const root = document.getElementById("workspace-root");
  if (!root) return;
  const authBase = root.dataset.authBase?.replace(/\/$/, "");
  const state = { jwt: null, connections: [], repositories: [], selectedRepo: null, selectedChange: null, reviewNonce: 0, repoNonce: 0 };
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
  async function timedFetch(url, options = {}, timeout = 100000) {
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
      if (response.has_more) status(response.scope || "Repository listing is incomplete; paste a direct change URL.");
      return (response.repositories || []).map((repo) => ({ ...repo, provider: connection.provider,
        key: connection.provider === "github" ? repo.name : String(repo.id) }));
    }));
    state.repositories = lists.flatMap((entry) => entry.status === "fulfilled" ? entry.value : []);
    const failed = lists.find((entry) => entry.status === "rejected");
    if (failed) status(`Some repositories could not be loaded: ${failed.reason.message}`);
    renderRepos();
    await Promise.all([loadQueue(), loadRecentReviews()]);
  }
  function changeButton(change, repository, compact = false) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "review-change-item";
    const details = text("small", `${repository.name} · ${repository.provider === "github" ? "PR" : "MR"} #${change.number}${change.draft ? " · draft" : ""}`);
    button.append(text("strong", change.title || `Change #${change.number}`), details, text("span", "Choose ↗"));
    button.addEventListener("click", () => selectChange({ provider: repository.provider, repoKey: repository.key,
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
      if (data.errors?.length) status(data.errors.map((item) => `${item.provider}: ${item.error}`).join("; "));
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
      $("selected-repository-status").textContent = `${result.changes.length} open ${repo.provider === "github" ? "pull requests" : "merge requests"}. Choose one, then analyze the changed files.`;
      if (!result.changes.length) { empty(list, "No open changes here. You can still paste a direct link to a closed PR or MR above."); return; }
      result.changes.forEach((change) => list.append(changeButton(change, repo, true)));
      if (result.has_more) status("Showing the first 1,000 open changes in this repository. Paste a direct link for another one.");
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
    const changes = result.dependency_changes || [];
    const heading = changes.length ? `${changes.length} dependency declaration changes observed` : "No dependency version change established";
    const description = `${result.evidence_level || "Diff review"} · ${result.complete ? "Complete retrieval" : "Incomplete evidence"} · ${count} files · Commit ${(result.head_sha || "unknown").slice(0, 12)}. Declared constraints are not resolved environments or reproduced regressions.`;
    summary.append(text("strong", heading), text("p", description));
    body.append(summary);
    (result.findings || []).forEach((finding, index) => {
      const item = document.createElement("article"); item.className = "review-finding";
      const content = document.createElement("div");
      content.append(text("h3", finding.title || "Finding"));
      if (finding.evidence) content.append(text("code", finding.evidence));
      const evidenceUrl = safeSourceUrl(finding.url, change.provider);
      if (evidenceUrl) { const link = text("a", "Open evidence at this commit"); link.href = evidenceUrl; link.target = "_blank"; link.rel = "noopener noreferrer"; content.append(link); }
      content.append(text("p", finding.action || "Review this part of the change."));
      item.append(text("span", String(index + 1).padStart(2, "0"), "review-finding-index"), content);
      body.append(item);
    });
    const limits = text("ul", "");
    (result.limitations || []).forEach((value) => limits.append(text("li", value)));
    body.append(text("h3", "Limits and missing information"), limits);
    const actions = text("ol", "");
    (result.next_actions || []).forEach((value) => actions.append(text("li", value)));
    body.append(text("h3", "What to do next"), actions);
    const next = document.createElement("div"); next.className = "review-next";
    next.append(text("p", `${result.scope || "A bounded diff review."} No repository code was executed. This is not a reproduced regression.`, "review-scope"));
    const demo = document.createElement("a"); demo.href = "/demo/investigation"; demo.textContent = "See a real paired replay ↗";
    next.append(demo); body.append(next);
    const configure = text("button", "Configure supported paired replay", "review-button review-button-secondary");
    configure.type = "button";
    configure.addEventListener("click", () => configureReplay(result, change, next, configure));
    next.append(configure);
    $("review-result-meta").textContent = `${change.provider.toUpperCase()} · ${change.repoKey} · ${change.provider === "github" ? "PR" : "MR"} #${change.number}`;
  }
  async function loadRecentReviews() {
    const list = $("recent-reviews");
    try {
      const data = await api("/reviews"); clear(list);
      if (!data.reviews.length) { empty(list, "Your completed diff reviews will appear here."); return; }
      data.reviews.forEach((review) => {
        const button = text("button", `${review.repository} #${review.number} · ${review.head_sha.slice(0, 12)}`, "review-change-item");
        button.type = "button";
        button.addEventListener("click", async () => {
          try {
            const saved = await api(`/reviews/${encodeURIComponent(review.id)}`);
            const change = {provider: saved.provider, repoKey: saved.repository, number: saved.number,
              title: `Saved review #${saved.number}`, url: `https://${saved.provider === "github" ? "github.com" : "gitlab.com"}/${saved.repository}/${saved.provider === "github" ? "pull" : "-/merge_requests"}/${saved.number}`};
            selectChange(change); $("review-result").hidden = false; renderReview(saved, change);
            status("Historical review loaded. Replay rechecks the latest PR revisions.", true);
          } catch (error) { status(error.message); }
        }); list.append(button);
      });
    } catch (error) { empty(list, `Saved reviews unavailable: ${error.message}`); }
  }
  async function configureReplay(result, change, parent, trigger) {
    trigger.disabled = true;
    try {
      const response = await timedFetch("/api/projects/00000000-0000-0000-0000-000000000001/contract", {headers: {Authorization: `Bearer ${state.jwt}`}});
      const approved = await response.json();
      if (!response.ok) throw new Error(approved.detail || "Supported contract unavailable");
      const form = document.createElement("form");
      form.append(text("p", "Supported scope: the exact fixture/project/pipeline.py kernel at both commits, with an explicit NumPy 1.26.4 to 2.0.2 pin. Other project code and dependency changes are excluded."));
      form.append(text("p", "Contract: 8 finite float32 signal values in [-10, 10]; ratio > 0.5, exact ties inconclusive. Up to 400 executions, 600 seconds and 2 model calls; model cost cap $0.10. Only curated data reaches the configured model provider."));
      const path = document.createElement("select"); path.required = true; path.setAttribute("aria-label", "Dependency manifest containing the NumPy pins");
      (result.dependency_files || []).forEach((file) => {const option = text("option", file); option.value = file; path.append(option);});
      if (!path.options.length) { form.append(text("p", "No supported dependency manifest was found. A pinned manifest and validated numerical adapter are required before replay.")); parent.append(form); return; }
      const label = document.createElement("label"), checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.required = true;
      label.append(checkbox, document.createTextNode(" I approve this exact contract, scope and budget."));
      const submit = text("button", "Run approved paired investigation", "review-button review-button-primary"); submit.type = "submit";
      const message = text("p", ""); message.setAttribute("role", "status");
      form.append(path, label, submit, message); parent.append(form);
      form.addEventListener("submit", async (event) => {
        event.preventDefault(); submit.disabled = true; message.textContent = "Verifying immutable source and environment pins…";
        try {
          const job = await api(`/${change.provider}/paired-replay`, {method: "POST", body: JSON.stringify({repository: change.repoKey, number: change.number, expected_head_sha: result.head_sha, dependency_path: path.value, contract_hash: approved.contract_hash})});
          location.href = `/investigations/${encodeURIComponent(job.investigation_id)}`;
        } catch (error) { message.textContent = error.message; submit.disabled = false; }
      });
    } catch (error) { status(error.message); trigger.disabled = false; }
  }
  function selectChange(change) {
    if (!state.connections.some((connection) => connection.provider === change.provider)) {
      $("change-link-help").textContent = `Connect ${change.provider === "github" ? "GitHub" : "GitLab"} below before analyzing this link.`;
      $("change-link-help").classList.add("is-error");
      $("source-settings").open = true;
      $("source-settings").scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    state.selectedChange = change;
    state.reviewNonce += 1;
    $("review-result").hidden = true;
    $("chosen-change").hidden = false;
    $("chosen-change-title").textContent = change.title;
    $("chosen-change-meta").textContent = `${change.provider.toUpperCase()} · ${change.repoKey} · ${change.provider === "github" ? "PR" : "MR"} #${change.number}`;
    const sourceUrl = safeSourceUrl(change.url, change.provider);
    $("chosen-source-link").hidden = !sourceUrl;
    if (sourceUrl) $("chosen-source-link").href = sourceUrl;
    const button = $("start-investigation-btn");
    button.disabled = false;
    $("start-investigation-label").textContent = "Analyze changed files";
    $("change-link-help").textContent = "Change selected. Analyze the changed files below.";
    $("change-link-help").classList.remove("is-error");
    $("chosen-change").scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "center" });
    button.focus({ preventScroll: true });
  }
  async function reviewChange(change) {
    const nonce = ++state.reviewNonce;
    const button = $("start-investigation-btn");
    button.disabled = true;
    $("start-investigation-label").textContent = "Analyzing changed files…";
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
      loadRecentReviews();
      $("change-link-help").textContent = "Diff observations are ready below.";
      $("change-link-help").classList.remove("is-error");
    } catch (error) {
      if (nonce !== state.reviewNonce) return;
      empty($("review-result-body"), `${error.message} Check source access and try this link again.`);
    } finally {
      if (nonce === state.reviewNonce) {
        button.disabled = false;
        $("start-investigation-label").textContent = "Analyze latest revision";
      }
    }
  }
  $("start-investigation-btn").addEventListener("click", () => {
    if (state.selectedChange) reviewChange(state.selectedChange);
  });
  $("change-link-form").addEventListener("submit", (event) => {
    event.preventDefault();
    try {
      const change = parseChangeUrl($("change-url").value);
      $("change-link-help").classList.remove("is-error");
      selectChange(change);
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
        state.selectedRepo = null; state.selectedChange = null; state.reviewNonce += 1;
        $("selected-repository").hidden = true; $("chosen-change").hidden = true; $("review-result").hidden = true;
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
      await api("/session/investigation", {method: "POST"});
      await loadConnections();
    } catch (error) {
      $("workspace-loading").hidden = true;
      if (state.jwt) { $("workspace-content").hidden = false; status(`${error.message} Reload to try again.`); }
      else $("workspace-signin").hidden = false;
    }
  }
  start();
})();
