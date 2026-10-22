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
  async function projectApi(path, options = {}) {
    const response = await timedFetch("/api" + path, {
      ...options,
      headers: { Authorization: `Bearer ${state.jwt}`, "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data.detail;
      throw new Error(typeof detail === "string" ? detail : detail?.reasons?.join(" ") || "The project request failed.");
    }
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
    await Promise.all([loadQueue(), loadRecentReviews(), loadRecentProjectRuns(), loadProfiles(), loadOperatorProfiles()]);
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
    const next = document.createElement("div"); next.className = "review-next";
    body.append(next);
    if (result.freshReview) configureRunOptions(result, change, next).catch((error) => status(error.message));
    else next.append(text("p", "This saved review records an older commit. Review the current PR again before checking whether it can run."));
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
    body.append(text("p", `${result.scope || "A bounded diff review."} No repository code was executed. This is not a reproduced regression.`, "review-scope"));
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
  async function loadRecentProjectRuns() {
    const list = $("recent-project-runs");
    try {
      const data = await projectApi("/project-runs");
      clear(list);
      if (!data.runs?.length) { empty(list, "No approved project runs yet."); return; }
      data.runs.forEach((job) => {
        const spec = job.spec_json || {};
        const cost = Number.isFinite(job.estimated_sandbox_cost_usd)
          ? ` · ${job.usage_uncertain ? "up to " : "estimated "}$${job.estimated_sandbox_cost_usd.toFixed(6)}` : "";
        const button = text("button", `${spec.repository || "Project"} #${spec.change_number || "?"} · ${job.status} · ${(spec.head_sha || "").slice(0, 12)}${cost}`, "review-change-item");
        button.type = "button";
        button.addEventListener("click", () => openSavedProjectRun(job.id));
        const link = text("a", "Saved link ↗");
        link.href = `/workspace?project_run=${encodeURIComponent(job.id)}`;
        const row = text("div", "", "profile-list-item");
        row.append(button, link); list.append(row);
      });
    } catch (error) { empty(list, `Saved runs unavailable: ${error.message}`); }
  }
  async function openSavedProjectRun(jobId) {
    const slot = $("saved-project-run"); clear(slot); slot.hidden = false;
    const card = text("div", "", "approved-plan");
    const title = text("h4", "Saved run");
    const message = text("p", "Loading the saved run…");
    card.append(title, message); slot.append(card);
    slot.scrollIntoView({behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "center"});
    try {
      const job = await projectApi(`/project-runs/${encodeURIComponent(jobId)}`);
      const spec = job.spec_json || {};
      title.textContent = `Saved run · ${spec.repository || "project"} #${spec.change_number || "?"}`;
      const revision = text("p", "Checking the current PR revision…");
      card.append(revision);
      try {
        const current = await projectApi(`/project-runs/${encodeURIComponent(jobId)}/revision`);
        revision.textContent = current.changed
          ? `The PR moved since this run. This evidence belongs to head ${current.saved_head_sha.slice(0, 12)}; check the new revision before running again.`
          : `Current PR revision matches this saved run (${current.saved_head_sha.slice(0, 12)}).`;
      } catch { revision.textContent = "Current PR revision could not be checked. The saved evidence remains pinned to its recorded commits."; }
      await watchApprovedRun(jobId, message, card);
    } catch (error) { message.textContent = error.message; }
  }
  async function loadProfiles() {
    const list = $("profile-list");
    try {
      const data = await projectApi("/project-profiles");
      clear(list);
      if (!data.profiles?.length) { empty(list, "No project profiles yet. Save a draft above to begin operator review."); return; }
      data.profiles.forEach((profile) => {
        const card = text("div", "", "profile-list-item");
        card.append(text("strong", profile.draft_json?.name || profile.repository));
        card.append(text("small", `${profile.repository} · version ${profile.version} · ${profile.status}`));
        if (profile.status !== "approved") card.append(text("p", "A draft cannot execute. An operator must validate the sample and prepare both pinned environments."));
        list.append(card);
      });
    } catch (error) { empty(list, `Project profiles unavailable: ${error.message}`); }
  }
  async function loadOperatorProfiles() {
    const response = await timedFetch("/api/project-profiles/operator/pending", {
      headers: {Authorization: `Bearer ${state.jwt}`},
    }).catch(() => null);
    if (!response || response.status === 403) return;
    const section = $("operator-profiles");
    section.hidden = false;
    const list = $("operator-profile-list");
    if (!response.ok) { empty(list, "The approval queue is temporarily unavailable."); return; }
    const data = await response.json();
    clear(list);
    if (!data.profiles?.length) { empty(list, "No drafts are waiting for review."); return; }
    data.profiles.forEach((profile) => {
      const draft = profile.draft_json || {};
      const card = text("details", "", "profile-list-item");
      card.append(text("summary", `${draft.name || profile.repository} · ${profile.repository} · version ${profile.version}`));
      const facts = [
        ["Owner", profile.owner_id], ["Adapter", `${draft.adapter_module}.${draft.adapter_function}`],
        ["Dependency file", draft.dependency_path], ["Input sample", JSON.stringify(draft.sample_input)],
        ["Output", draft.contract?.output_selector?.field_name],
        ["Decision", `${draft.contract?.conclusion?.operator} ${draft.contract?.conclusion?.threshold}`],
        ["Source transfer consent", draft.source_transfer_consent ? "Granted" : "Missing"],
        ["Profile SHA-256", profile.profile_digest], ["Contract SHA-256", profile.contract_hash],
      ];
      const details = text("dl", "", "approved-plan-details");
      facts.forEach(([label, value]) => details.append(text("dt", label), text("dd", value ?? "Not supplied")));
      card.append(details);
      configureOperatorValidation(profile, card);
      list.append(card);
    });
  }
  function operatorField(form, labelText, name, placeholder) {
    const label = text("label", labelText);
    const input = document.createElement("input");
    input.name = name; input.required = true; input.placeholder = placeholder;
    input.autocomplete = "off";
    label.append(input); form.append(label);
    return input;
  }
  function configureOperatorValidation(profile, card) {
    const path = `/project-profiles/${encodeURIComponent(profile.id)}/validations`;
    const panel = text("div", "", "operator-validation");
    panel.append(text("h4", "Validate the sample"));
    panel.append(text("p", "Review the source at the exact commit and both prepared environment digests. Validation runs only after the pilot execution gate is enabled."));
    const form = text("form", "", "profile-form");
    const source = operatorField(form, "Reviewed source commit SHA", "source_sha", "40-character commit SHA");
    source.pattern = "[0-9a-f]{40,64}";
    const oldTemplate = operatorField(form, "NumPy 1.26.4 template ID", "old_template", "Prepared E2B template ID");
    const oldLock = operatorField(form, "NumPy 1.26.4 lock SHA-256", "old_lock", "64-character lock digest");
    const newTemplate = operatorField(form, "NumPy 2.0.2 template ID", "new_template", "Prepared E2B template ID");
    const newLock = operatorField(form, "NumPy 2.0.2 lock SHA-256", "new_lock", "64-character lock digest");
    for (const input of [oldLock, newLock]) input.pattern = "[0-9a-f]{64}";
    const submit = text("button", "Start bounded sample validation ↗", "review-button review-button-primary");
    submit.type = "submit"; form.append(submit);
    const message = text("p", "No validation has run for this draft.");
    const refresh = text("button", "Refresh validation status", "review-button review-button-secondary");
    refresh.type = "button";
    const finding = text("div", "", "operator-validation-result");
    panel.append(form, message, refresh, finding); card.append(panel);
    async function showLatest() {
      const data = await projectApi(path);
      const job = data.validations?.[0];
      clear(finding);
      if (!job) { message.textContent = "No validation has run for this draft."; return; }
      message.textContent = `Validation ${job.id} · ${job.status}${job.error_code ? ` · ${job.error_code}` : ""}`;
      if (job.status !== "completed" || job.result_json?.sample_validated !== true) return;
      const result = job.result_json;
      finding.append(text("p", `Sample validated at ${result.source_sha.slice(0, 12)}. Source SHA-256: ${result.source_sha256}. Review the measured outcome before approval.`));
      finding.append(text("p", `Old: ${JSON.stringify(result.old || {})} · New: ${JSON.stringify(result.new || {})}`));
      const approval = text("form", "", "profile-form");
      const note = operatorField(approval, "Operator review note", "review_note", "What source, adapter, lock and result did you verify?");
      note.minLength = 8; note.maxLength = 1000;
      const consent = document.createElement("input");
      consent.type = "checkbox"; consent.required = true;
      const confirmation = text("label", " I checked the exact source, prepared environments and sample output.");
      confirmation.prepend(consent); approval.append(confirmation);
      const approve = text("button", "Approve this project version ↗", "review-button review-button-primary");
      approve.type = "submit"; approval.append(approve);
      approval.addEventListener("submit", async (event) => {
        event.preventDefault(); approve.disabled = true;
        try {
          await projectApi(`/project-profiles/${encodeURIComponent(profile.id)}/approve`, {
            method: "POST", body: JSON.stringify({profile_digest: profile.profile_digest,
              validation_job_id: job.id, old: job.spec_json.old, new: job.spec_json.new,
              review_note: note.value.trim()}),
          });
          await Promise.all([loadProfiles(), loadOperatorProfiles()]);
          status(`${profile.repository} version ${profile.version} approved.`, true);
        } catch (error) { message.textContent = error.message; approve.disabled = false; }
      });
      finding.append(approval);
    }
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); submit.disabled = true;
      try {
        const values = Object.fromEntries(new FormData(form));
        await projectApi(path, {method: "POST", body: JSON.stringify({
          source_sha: values.source_sha.trim(),
          old: {template_id: values.old_template.trim(), python_version: "3.12", numpy_version: "1.26.4", lock_sha256: values.old_lock.trim()},
          new: {template_id: values.new_template.trim(), python_version: "3.12", numpy_version: "2.0.2", lock_sha256: values.new_lock.trim()},
          idempotency_key: crypto.randomUUID(),
        })});
        await showLatest();
      } catch (error) { message.textContent = error.message; }
      finally { submit.disabled = false; }
    });
    refresh.addEventListener("click", () => showLatest().catch((error) => { message.textContent = error.message; }));
    card.addEventListener("toggle", () => { if (card.open) showLatest().catch((error) => { message.textContent = error.message; }); });
  }
  function setupProfileForm() {
    const form = $("profile-form");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const message = $("profile-form-status");
      const submit = form.querySelector("button[type=submit]");
      submit.disabled = true;
      try {
        const values = Object.fromEntries(new FormData(form));
        if (!state.connections.some((source) => source.provider === values.provider)) throw new Error(`Connect ${values.provider} in source settings first.`);
        let sample;
        try { sample = JSON.parse(values.sample_value); } catch { throw new Error("Sample must be a JSON number or numeric array."); }
        if (!(typeof sample === "number" && Number.isFinite(sample)) &&
            !(Array.isArray(sample) && sample.length >= 1 && sample.length <= 1000 && sample.every((value) => typeof value === "number" && Number.isFinite(value)))) {
          throw new Error("Sample must be one finite number or a one-dimensional numeric array of 1–1,000 values.");
        }
        const minimum = Number(values.min_value), maximum = Number(values.max_value), threshold = Number(values.threshold);
        if (![minimum, maximum, threshold].every(Number.isFinite) || minimum > maximum) throw new Error("Input bounds and threshold must be finite; minimum cannot exceed maximum.");
        const draft = {
          provider: values.provider, repository: values.repository.trim(), name: values.name.trim(),
          adapter_module: values.adapter_module.trim(), adapter_function: values.adapter_function.trim(),
          dependency_path: values.dependency_path.trim(), sample_input: {[values.input_name.trim()]: sample},
          source_transfer_consent: values.source_transfer_consent === "on",
          contract: {
            schema_version: "1", input_schema: {fields: [{name: values.input_name.trim(), dtype: values.input_dtype,
              shape: Array.isArray(sample) ? [sample.length] : [], min_value: minimum, max_value: maximum,
              allow_nan: false, allow_inf: false}], cross_constraints: []},
            output_selector: {field_name: values.output_name.trim()},
            conclusion: {operator: values.operator, threshold, tie_behavior: "inconclusive"},
            comparison: {atol: 1e-7, rtol: 1e-5, nan_equal: false, inf_equal: true, dtype_must_match: false},
            resource_limits: {max_wall_seconds: 30, max_memory_mb: 512, max_cpu_seconds: 25},
            assumptions: [], review_provenance: "owner_draft",
          },
        };
        const saved = await projectApi("/project-profiles", {method: "POST", body: JSON.stringify(draft)});
        message.textContent = `Draft ${saved.id} saved. It cannot run until an operator validates the sample and approves the prepared environments.`;
        await loadProfiles();
      } catch (error) { message.textContent = error.message; }
      finally { submit.disabled = false; }
    });
  }
  async function configureRunOptions(result, change, parent) {
    try {
      const data = await projectApi("/project-profiles");
      if (state.selectedChange?.repoKey !== change.repoKey || state.selectedChange?.number !== change.number) return;
      const reviewedRepository = result.repository || change.repoKey;
      const profiles = (data.profiles || []).filter((profile) =>
        profile.status === "approved" && profile.provider === change.provider && profile.repository === reviewedRepository);
      if (profiles.length) {
        configureApprovedRun(change, profiles, parent);
        return;
      }
    } catch (error) {
      status(`Approved project profiles could not be loaded: ${error.message}`);
    }
    await configureReplay(result, change, parent);
  }
  function configureApprovedRun(change, profiles, parent) {
    const panel = document.createElement("section");
    panel.className = "review-run-gate approved-run";
    panel.append(text("h3", "Run an approved project investigation"));
    const stages = text("ol", "", "approved-stages");
    ["Choose change", "Check run support", "Review contract and cost", "Run", "Read finding"]
      .forEach((label, index) => stages.append(text("li", `${index + 1}. ${label}`)));
    panel.append(stages);
    const profileLabel = text("label", "Approved project profile");
    const select = document.createElement("select");
    profiles.forEach((profile) => {
      const option = text("option", `${profile.draft_json?.name || profile.repository} · version ${profile.version}`);
      option.value = profile.id; select.append(option);
    });
    profileLabel.append(select);
    const searchLabel = text("label", "Search scope");
    const searchSelect = document.createElement("select");
    [["1", "One sample"], ["8", "Up to 8 inputs"], ["16", "Up to 16 inputs"], ["32", "Up to 32 inputs"]]
      .forEach(([value, label]) => { const option = text("option", label); option.value = value; searchSelect.append(option); });
    searchLabel.append(searchSelect);
    const strategyLabel = text("label", "Input selection");
    const strategySelect = document.createElement("select");
    [["sweep", "Range sweep"], ["seeded_random", "Seeded random"]]
      .forEach(([value, label]) => { const option = text("option", label); option.value = value; strategySelect.append(option); });
    strategyLabel.append(strategySelect);
    const checkButton = text("button", "Check this run ↗", "review-button review-button-primary");
    checkButton.type = "button";
    const message = text("p", "Review the exact change before spending sandbox time.");
    message.setAttribute("role", "status");
    const details = document.createElement("div");
    panel.append(profileLabel, searchLabel, strategyLabel, checkButton, message, details);
    parent.append(panel);
    select.addEventListener("change", () => { clear(details); message.textContent = "Check the selected profile again."; });
    searchSelect.addEventListener("change", () => { clear(details); message.textContent = "Check the selected search scope again."; });
    strategySelect.addEventListener("change", () => { clear(details); message.textContent = "Check the input selection again."; });
    checkButton.addEventListener("click", async () => {
      const checkedProfile = select.value;
      const checkedBudget = searchSelect.value;
      const checkedStrategy = strategySelect.value;
      checkButton.disabled = true;
      clear(details);
      message.textContent = "Checking the current commits, dependency pins and approved profile…";
      try {
        const plan = await projectApi(`/project-runs/profiles/${encodeURIComponent(checkedProfile)}/changes/${change.number}/plan?candidate_budget=${encodeURIComponent(checkedBudget)}&candidate_strategy=${encodeURIComponent(checkedStrategy)}`);
        if (state.selectedChange?.repoKey !== change.repoKey || state.selectedChange?.number !== change.number || select.value !== checkedProfile || searchSelect.value !== checkedBudget || strategySelect.value !== checkedStrategy) return;
        if (!plan.eligible) {
          message.textContent = "Diff review only: " + plan.reasons.join(" ");
          return;
        }
        message.textContent = "This revision matches the approved project profile. Review the limits below.";
        const card = text("div", "", "approved-plan");
        card.append(text("h4", "Exact run plan"));
        const fields = document.createElement("dl"); fields.className = "review-contract-details";
        [["Source", `${plan.repository} · ${plan.base_sha.slice(0, 12)} → ${plan.head_sha.slice(0, 12)}`],
          ["Environment", `NumPy ${plan.old_environment.numpy_version} → ${plan.new_environment.numpy_version}`],
          ["Code", plan.source_scope], ["Contract SHA-256", plan.contract_hash],
          ["Search", `Up to ${plan.candidate_budget} contract-valid input${plan.candidate_budget === 1 ? "" : "s"} · ${plan.candidate_strategy} · two runs per environment`],
          ["Sandbox time", `${plan.max_sandbox_seconds || 0}s total · ${plan.max_wall_seconds}s wall limit`],
          ["Cost ceiling", plan.rate_configured ? `Up to $${plan.cost_ceiling_usd.toFixed(4)} estimated sandbox cost` : "Not configured"]]
          .forEach(([name, value]) => fields.append(text("dt", name), text("dd", value)));
        card.append(fields, text("p", plan.source_transfer_notice));
        if (!plan.rate_configured || !plan.execution_enabled) {
          card.append(text("p", "Project execution is not enabled yet. The diff review above is still available.", "review-run-unavailable"));
          details.append(card); return;
        }
        const form = document.createElement("form");
        const consent = document.createElement("input"); consent.type = "checkbox"; consent.required = true;
        const label = document.createElement("label");
        label.append(consent, document.createTextNode(" I approve this source transfer, exact commits, contract and cost ceiling."));
        const start = text("button", "Start bounded investigation ↗", "review-button review-button-primary"); start.type = "submit";
        const runState = text("p", ""); runState.setAttribute("role", "status");
        form.append(label, start, runState); card.append(form); details.append(card);
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          if (!consent.checked) return;
          start.disabled = true; runState.textContent = "Recording approval and queuing this exact revision…";
          try {
            const job = await projectApi("/project-runs", { method: "POST", body: JSON.stringify({
              profile_id: checkedProfile, change_number: change.number,
              expected_base_sha: plan.base_sha, expected_head_sha: plan.head_sha,
              approved_contract_hash: plan.contract_hash,
              approved_cost_ceiling_usd: plan.cost_ceiling_usd,
              candidate_strategy: plan.candidate_strategy, candidate_budget: plan.candidate_budget,
              candidate_seed: plan.candidate_seed,
              source_transfer_approved: true, idempotency_key: crypto.randomUUID(),
            }) });
            start.hidden = true; consent.disabled = true;
            const savedLink = text("a", "Open saved run ↗");
            savedLink.href = `/workspace?project_run=${encodeURIComponent(job.id)}`;
            card.append(savedLink);
            await watchApprovedRun(job.id, runState, card);
          } catch (error) { runState.textContent = error.message; start.disabled = false; }
        });
      } catch (error) { message.textContent = error.message; }
      finally { checkButton.disabled = false; }
    });
  }
  function renderApprovedFinding(card, finding, job) {
    const search = finding.kind === "approved_project_search";
    const shown = search ? (finding.witness || finding.observations?.find((item) => !item.repeatable) || finding.observations?.[0] || {}) : finding;
    const tested = search ? Number(finding.attempted) || 0 : 1;
    const result = text("section", "", "approved-finding");
    result.setAttribute("aria-label", "Executed project evidence");
    result.append(text("span", search ? `EXECUTED SEARCH / ${tested} INPUTS` : "EXECUTED SAMPLE / ONE INPUT", "review-kicker"));
    result.append(text("h4", finding.changed_conclusion
      ? (search ? "A changed outcome was found" : "The declared outcome changed for this sample")
      : (finding.inconclusive ? "Inconclusive: repeat runs disagreed" : search ? "No violation found within tested scope" : "No outcome change for this sample")));
    result.append(text("p", search
      ? `${tested} contract-valid input${tested === 1 ? "" : "s"} ran against the same source snapshot in two pinned environments. ${finding.witness ? "The comparison below is the first observed witness." : finding.inconclusive ? "The comparison below is an input whose repeats disagreed." : "The comparison below is the first tested input."}`
      : "These measurements came from the same source snapshot in two pinned environments. They describe this input only."));
    const comparison = text("div", "", "approved-comparison");
    [["Before", shown.old, job.spec_json?.old_environment_lock_sha256],
      ["After", shown.new, job.spec_json?.new_environment_lock_sha256]].forEach(([label, observation, lock]) => {
      const column = text("div", "", "approved-observation");
      column.append(text("small", label), text("strong", Number.isFinite(observation?.ratio) ? String(observation.ratio) : "Not recorded"));
      column.append(text("span", observation?.conclusion || "No decision recorded"));
      if (lock) column.append(text("code", `Environment ${lock.slice(0, 12)}`));
      comparison.append(column);
    });
    result.append(comparison);
    const ladder = text("ol", "", "approved-evidence-ladder");
    const steps = [
      ["01", "Outcome", finding.changed_conclusion ? "The declared decision differs between environments for this input." : finding.inconclusive ? "One or more within-environment repeats disagreed, so no negative finding is claimed." : `No declared decision difference was found across ${tested} tested input${tested === 1 ? "" : "s"}.`],
      ["02", finding.changed_conclusion ? "Exact witness" : "Example input", `Input SHA-256: ${shown.input_sha256 || "Not recorded"}. Source SHA-256: ${finding.source_sha256 || "Not recorded"}.`],
      ["03", "Repeatability", finding.repeatable === undefined && !search ? "Not tested in this historical one-sample run." : finding.inconclusive ? "Some repeated inputs disagreed within an environment. Review those observations before drawing conclusions." : "Each tested input ran twice in each environment; within-environment decisions and measurements agreed within the approved tolerance."],
      ["04", "Mechanism", "Not tested. The run does not isolate which dependency behavior caused the measurement."],
      ["05", "Repair", "Not tested. No candidate repair was executed for this project run."],
      ["06", "Limits", `${Number.isFinite(finding.sandbox_seconds) ? finding.sandbox_seconds + "s" : "Unrecorded"} of sandbox time; estimated cost ${Number.isFinite(finding.estimated_sandbox_cost_usd) ? "$" + finding.estimated_sandbox_cost_usd : "not recorded"}. ${search ? `Search used ${finding.strategy} with seed ${finding.seed}; ${tested} of ${finding.candidate_budget} permitted inputs ran, twice per environment.` : finding.repeatable === undefined ? "One sample ran once per environment." : "One sample ran twice per environment."} This is not a general regression verdict.`],
    ];
    steps.forEach(([number, title, description]) => {
      const item = document.createElement("li");
      item.append(text("span", number), text("strong", title), text("p", description));
      ladder.append(item);
    });
    result.append(ladder);
    if (job.result_sha256) result.append(text("p", `Saved finding SHA-256: ${job.result_sha256}`, "approved-result-digest"));
    card.append(result);
  }
  function configureShare(jobId, card) {
    const panel = text("section", "", "approved-share");
    panel.append(text("h4", "Share a redacted finding"));
    panel.append(text("p", "A share copy omits the repository, commits, source, inputs, hashes and measurements. Anyone with its link can read the summary until you revoke it."));
    const previewButton = text("button", "Preview public copy ↗", "review-button review-button-secondary");
    previewButton.type = "button";
    const previewSlot = text("div", "", "approved-share-preview");
    const history = text("div", "", "approved-share-history");
    panel.append(previewButton, previewSlot, history); card.append(panel);
    async function loadShares() {
      const data = await projectApi(`/project-runs/${encodeURIComponent(jobId)}/shares`);
      clear(history);
      if (!data.shares?.length) { history.append(text("p", "No share copies yet.")); return; }
      data.shares.forEach((share) => {
        const row = text("div", "", "profile-list-item");
        row.append(text("span", `Share ${share.id.slice(0, 8)} · ${share.revoked_at ? "revoked" : "active"}`));
        if (!share.revoked_at) {
          const revoke = text("button", "Revoke link", "review-button review-button-secondary");
          revoke.type = "button";
          revoke.addEventListener("click", async () => {
            revoke.disabled = true;
            try {
              await projectApi(`/project-runs/${encodeURIComponent(jobId)}/shares/${encodeURIComponent(share.id)}`, {method: "DELETE"});
              await loadShares();
            } catch (error) { row.append(text("p", error.message)); revoke.disabled = false; }
          });
          row.append(revoke);
        }
        history.append(row);
      });
    }
    previewButton.addEventListener("click", async () => {
      previewButton.disabled = true; clear(previewSlot);
      try {
        const copy = await projectApi(`/project-runs/${encodeURIComponent(jobId)}/share-preview`);
        previewSlot.append(text("p", `${copy.scope}. ${copy.tested_inputs} input${copy.tested_inputs === 1 ? "" : "s"} tested. ${copy.inconclusive ? "Inconclusive repeats." : copy.changed_conclusion ? "A changed conclusion was observed." : "No violation found within tested scope."}`));
        copy.limitations.forEach((limit) => previewSlot.append(text("p", limit)));
        const label = text("label", " I understand this summary is public to anyone with the link.");
        const checkbox = document.createElement("input"); checkbox.type = "checkbox";
        label.prepend(checkbox);
        const create = text("button", "Create revocable link ↗", "review-button review-button-primary");
        create.type = "button"; create.disabled = true;
        checkbox.addEventListener("change", () => { create.disabled = !checkbox.checked; });
        create.addEventListener("click", async () => {
          create.disabled = true;
          try {
            const share = await projectApi(`/project-runs/${encodeURIComponent(jobId)}/shares`, {
              method: "POST", body: JSON.stringify({acknowledge_public_share: true}),
            });
            clear(previewSlot);
            previewSlot.append(text("p", "Save this link now. The token is shown once."));
            const link = text("a", "Open redacted report ↗");
            link.href = share.url; link.target = "_blank"; link.rel = "noopener noreferrer";
            previewSlot.append(link);
            await loadShares();
          } catch (error) { previewSlot.append(text("p", error.message)); create.disabled = false; }
        });
        previewSlot.append(label, create);
      } catch (error) { previewSlot.append(text("p", error.message)); previewButton.disabled = false; }
    });
    loadShares().catch((error) => { history.append(text("p", error.message)); });
  }
  async function watchApprovedRun(jobId, message, card) {
    const cancel = text("button", "Cancel run", "review-button review-button-secondary"); cancel.type = "button";
    card.append(cancel);
    cancel.addEventListener("click", async () => {
      cancel.disabled = true;
      try { await projectApi(`/project-runs/${jobId}/cancel`, {method: "POST"}); message.textContent = "Cancellation requested. Waiting for sandbox cleanup…"; }
      catch (error) { message.textContent = error.message; cancel.disabled = false; }
    });
    for (let attempt = 0; attempt < 300; attempt += 1) {
      const job = await projectApi(`/project-runs/${jobId}`);
      if (job.status === "completed") {
        cancel.remove();
        const finding = job.result_json || {};
        message.textContent = "The bounded run completed. Read what was observed and what remains untested.";
        renderApprovedFinding(card, finding, job);
        configureShare(jobId, card); return;
      }
      if (["failed", "cancelled"].includes(job.status)) {
        cancel.remove(); message.textContent = job.status === "cancelled" ? "Run cancelled." : `Run failed: ${job.error_code || "unknown error"}. No scientific verdict was recorded.`;
        return;
      }
      message.textContent = job.cancel_requested_at ? "Cancellation requested; waiting for cleanup…" : `Run ${job.status}. You can leave this page and return to the saved run.`;
      await new Promise((resolve) => window.setTimeout(resolve, 3000));
    }
    message.textContent = "This run is taking longer than expected. Its saved status remains available in your project runs.";
  }
  async function configureReplay(result, change, parent) {
    const panel = document.createElement("section"); panel.className = "review-run-gate";
    panel.append(text("h3", "Can this change run a paired investigation?"));
    const explanation = text("p", "This read-only review did not execute code. A paired run needs the supported numerical kernel at both commits and exact NumPy pins.");
    panel.append(explanation);
    parent.append(panel);
    const paths = result.dependency_files || [];
    if (!paths.length) {
      panel.append(text("p", "No supported dependency manifest was found in this change. There is no paired run to start for this PR; use the diff findings below and the repository's own tests.", "review-run-unavailable"));
      return;
    }
    const path = document.createElement("select"); path.required = true; path.setAttribute("aria-label", "Dependency manifest to check");
    paths.forEach((file) => { const option = text("option", file); option.value = file; path.append(option); });
    const pathLabel = text("label", "Dependency manifest"); pathLabel.append(path);
    const checkButton = text("button", "Check run support ↗", "review-button review-button-secondary"); checkButton.type = "button";
    const message = text("p", ""); message.setAttribute("role", "status");
    const contractSlot = document.createElement("div");
    panel.append(pathLabel, checkButton, message, contractSlot);
    path.addEventListener("change", () => { clear(contractSlot); message.textContent = "Check the selected manifest again before approving a run."; });
    async function check() {
      clear(contractSlot);
      checkButton.disabled = true; message.textContent = "Checking both commits, the numerical kernel and NumPy pins…";
      try {
        const selectedPath = path.value || paths[0];
        const eligibility = await api(`/${change.provider}/paired-replay/eligibility`, {method: "POST", body: JSON.stringify({
          repository: change.repoKey, number: change.number, expected_head_sha: result.head_sha, dependency_path: selectedPath,
        })});
        if (state.selectedChange && (state.selectedChange.provider !== change.provider || state.selectedChange.repoKey !== change.repoKey || state.selectedChange.number !== change.number)) return;
        if (!eligibility.eligible) { message.textContent = `Paired run unavailable: ${eligibility.reason}`; return; }
        const response = await timedFetch("/api/projects/00000000-0000-0000-0000-000000000001/contract", {headers: {Authorization: `Bearer ${state.jwt}`}});
        const approved = await response.json();
        if (!response.ok) throw new Error(approved.detail || "Supported contract unavailable");
        message.textContent = "This exact change qualifies. Review the run contract and approve it below.";
        path.disabled = true; checkButton.hidden = true;
      const form = document.createElement("form");
      form.className = "review-contract";
      form.append(text("h3", "Review this paired-run contract"));
      form.append(text("p", `Base ${eligibility.base_sha.slice(0, 12)}: NumPy ${eligibility.numpy_versions.base} · Head ${eligibility.head_sha.slice(0, 12)}: NumPy ${eligibility.numpy_versions.head}. Only the validated numerical kernel runs; other repository code is excluded.`));
      const contract = approved.contract || {};
      const field = contract.input_schema?.fields?.[0] || {};
      const details = document.createElement("dl"); details.className = "review-contract-details";
      const rule = contract.conclusion || {}, policy = contract.comparison || {}, limits = contract.resource_limits || {};
      [["Input", `${field.name || "signal"} · ${field.dtype || "float32"}[${field.shape?.[0] ?? 8}] · ${field.min_value ?? -10} to ${field.max_value ?? 10}`],
        ["Decision", `${contract.output_selector?.field_name || "ratio"} ${rule.operator || "gt"} ${rule.threshold ?? 0.5}; tie: ${rule.tie_behavior || "inconclusive"}`],
        ["Tolerance", `absolute ${policy.atol ?? 0}; relative ${policy.rtol ?? 0}`],
        ["Limits", `Up to 400 executions · ${limits.max_wall_seconds ?? 600}s · 2 model calls · $0.10 maximum model cost`],
        ["Contract SHA-256", approved.contract_hash]].forEach(([name, value]) => details.append(text("dt", name), text("dd", value)));
      form.append(details);
      form.append(text("p", "Pinned workers run the same approved input in both environments. The configured model may receive curated evidence within the stated cost limit. Agreement across versions is not proof of scientific correctness."));
      const strategy = document.createElement("select"); strategy.setAttribute("aria-label", "Search strategy");
      [["sweep", "Known-boundary search"], ["random", "Seeded random search"], ["adaptive", "Adaptive search (experimental)"]].forEach(([value, label]) => {
        const option = text("option", label); option.value = value; strategy.append(option);
      });
      const strategyLabel = text("label", "Search strategy"); strategyLabel.append(strategy);
      const label = document.createElement("label"), checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.required = true;
      label.append(checkbox, document.createTextNode(" I approve this exact contract, scope and budget."));
      const submit = text("button", "Approve and start paired run ↗", "review-button review-button-primary"); submit.type = "submit";
      const runMessage = text("p", ""); runMessage.setAttribute("role", "status");
      form.append(strategyLabel, label, submit, runMessage); contractSlot.append(form);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!checkbox.checked) { runMessage.textContent = "Review and approve the contract before starting."; return; }
        submit.disabled = true; runMessage.textContent = "Recording approval and starting the pinned investigation…";
        try {
          const approval = await timedFetch("/api/projects/00000000-0000-0000-0000-000000000001/contract/approve", {
            method: "POST", credentials: "same-origin", headers: {Authorization: `Bearer ${state.jwt}`},
          });
          if (!approval.ok) { const problem = await approval.json().catch(() => ({})); throw new Error(problem.detail || "Contract approval failed"); }
          const job = await api(`/${change.provider}/paired-replay`, {method: "POST", body: JSON.stringify({repository: change.repoKey, number: change.number, expected_head_sha: result.head_sha, dependency_path: selectedPath, contract_hash: approved.contract_hash, search_strategy: strategy.value || "sweep"})});
          location.href = `/investigations/${encodeURIComponent(job.investigation_id)}`;
        } catch (error) { runMessage.textContent = error.message; submit.disabled = false; }
      });
      } catch (error) { message.textContent = `Run support could not be checked: ${error.message}`; }
      finally { checkButton.disabled = false; }
    }
    checkButton.addEventListener("click", check);
    if (paths.length === 1) await check();
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
    $("start-investigation-label").textContent = "Review change";
    $("change-link-help").textContent = "Change selected. Review its exact diff to see whether a paired investigation is supported.";
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
      renderReview({...result, freshReview: true}, change);
      loadRecentReviews();
      $("change-link-help").textContent = "Diff observations are ready below.";
      $("change-link-help").classList.remove("is-error");
    } catch (error) {
      if (nonce !== state.reviewNonce) return;
      empty($("review-result-body"), `${error.message} Check source access and try this link again.`);
    } finally {
      if (nonce === state.reviewNonce) {
        button.disabled = false;
        $("start-investigation-label").textContent = "Review change again";
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
      setupProfileForm();
      await api("/session/investigation", {method: "POST"});
      await loadConnections();
      const savedRun = new URLSearchParams(window.location.search).get("project_run");
      if (savedRun && /^[0-9a-f-]{36}$/i.test(savedRun)) {
        openSavedProjectRun(savedRun).catch((error) => status(error.message));
      }
    } catch (error) {
      $("workspace-loading").hidden = true;
      if (state.jwt) { $("workspace-content").hidden = false; status(`${error.message} Reload to try again.`); }
      else $("workspace-signin").hidden = false;
    }
  }
  start();
})();
