/* The public demo executes real paired workers with explicit mock proposals. */
(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const startButton = byId("start-btn");
  if (!startButton) return;
  const projectId = startButton.dataset.projectId;
  let pendingId = null, probeId = null;

  async function request(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 35000);
    try {
      const response = await fetch(url, {...options, credentials: "same-origin", signal: controller.signal});
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        let detail = typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`;
        if (response.status === 401) detail += ". Your session expired. Sign in again, then return to the demo.";
        const error = new Error(detail); error.status = response.status; throw error;
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("The request timed out. Retry; an existing investigation will be checked before another is created.");
      throw error;
    } finally { clearTimeout(timer); }
  }
  const post = (url, body) => request(url, {method: "POST", ...(body ? {headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {})});
  function showError(element, error) { element.textContent = error.message; element.style.display = "block"; }

  byId("approve-btn").addEventListener("click", async () => {
    const button = byId("approve-btn"); button.disabled = true;
    try {
      await post(`/api/projects/${projectId}/contract/approve`);
      byId("approve-status").style.display = "";
    } catch (error) { showError(byId("start-error"), error); }
    finally { button.disabled = false; }
  });
  startButton.addEventListener("click", async () => {
    startButton.disabled = true; byId("start-spinner").style.display = ""; byId("start-error").style.display = "none";
    try {
      await post(`/api/projects/${projectId}/contract/approve`);
      if (pendingId) {
        const existing = await request(`/api/investigations/${pendingId}`);
        if (existing.status === "running" || existing.status === "completed") {
          window.location.href = `/investigations/${pendingId}`; return;
        }
        if (existing.status !== "draft") pendingId = null;
      }
      if (!pendingId) {
        const inv = await post("/api/investigations", {project_id: projectId, model_mode: "mock", max_candidates: 50, max_runs: 400});
        pendingId = inv.id;
      }
      await post(`/api/investigations/${pendingId}/start`);
      window.location.href = `/investigations/${pendingId}`;
    } catch (error) {
      showError(byId("start-error"), error);
      if (error.status === 401) byId("demo-signin").hidden = false;
    } finally { startButton.disabled = false; byId("start-spinner").style.display = "none"; }
  });

  const runButton = byId("run-btn");
  document.querySelectorAll("[data-signal-preset]").forEach((button) => {
    button.addEventListener("click", () => {
      byId("signal-values").value = button.dataset.signalPreset;
      byId("error-msg").style.display = "none";
      byId("result-section").style.display = "none";
    });
  });
  byId("reset-exploration").addEventListener("click", () => {
    probeId = null; byId("reset-exploration").hidden = true;
    byId("error-msg").style.display = "none"; byId("result-section").style.display = "none";
  });
  runButton.addEventListener("click", async () => {
    const errorElement = byId("error-msg");
    byId("result-section").style.display = "none";
    const parts = byId("signal-values").value.split(",").map((value) => value.trim());
    const signal = parts.map(Number);
    if (parts.length !== 8 || parts.some((value) => value === "") || signal.some((value) => !Number.isFinite(value) || value < -10 || value > 10)) {
      showError(errorElement, new Error("Enter exactly eight numbers between −10 and 10, separated by commas.")); return;
    }
    errorElement.style.display = "none"; byId("run-spinner").style.display = ""; runButton.disabled = true;
    try {
      if (!probeId) {
        const inv = await post("/api/investigations", {project_id: projectId, model_mode: "mock", max_candidates: 1, max_runs: 20});
        probeId = inv.id;
      }
      const data = await post(`/api/investigations/${probeId}/run-pair`, {input_json: {signal}});
      const oldResult = data.old_run?.outputs?.result || {}, newResult = data.new_run?.outputs?.result || {};
      const comparison = data.comparison || {};
      const measurement = (outcome, output) => outcome == null ? "Result unavailable" : `${outcome} | ratio: ${output.ratio == null ? "missing" : String(output.ratio)} | dtype: ${output.intermediate_dtype || "missing"}`;
      byId("old-result").textContent = measurement(comparison.old_conclusion, oldResult);
      byId("new-result").textContent = measurement(comparison.new_conclusion, newResult);
      byId("verdict-row").textContent = (comparison.verdict_hint || "inconclusive").replaceAll("_", " ");
      byId("result-section").style.display = "block";
    } catch (error) {
      showError(errorElement, error);
      if (error.status === 429) byId("reset-exploration").hidden = false;
      if (error.status === 401) byId("demo-signin").hidden = false;
    } finally { byId("run-spinner").style.display = "none"; runButton.disabled = false; }
  });
})();
