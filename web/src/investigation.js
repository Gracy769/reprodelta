/**
 * investigation.js — Agent Replay Lab frontend for the investigation setup page.
 *
 * Wires up:
 *   - "Run Pair" button → calls /api/investigations/demo/run-pair
 *   - "Approve Contract" button → calls /api/projects/{id}/contract/approve
 *   - Displays results using design-system status colours
 */

"use strict";

/**
 * Read the 8 signal values from the DOM inputs.
 * @returns {number[]} Array of 8 floats.
 */
function readSignalValues() {
  const values = [];
  for (let i = 0; i < 8; i++) {
    const el = document.getElementById("s" + i);
    values.push(parseFloat(el ? el.value : "0"));
  }
  return values;
}

/**
 * Display a single-run record inside a container element.
 * @param {HTMLElement} container
 * @param {object} record  SingleRunRecord from the API
 */
function renderRunRecord(container, record) {
  const result = (record.outputs && record.outputs.result) || {};
  const conclusion = result.conclusion || record.outputs.conclusion || "—";
  const ratio = result.ratio != null ? result.ratio.toFixed(6) : "—";
  const numpy = result.numpy_version || record.numpy_version || "—";
  const status = record.status || "—";

  const statusColor =
    status === "ok" ? "var(--supported)" :
    status === "timeout" ? "var(--warning)" :
    "var(--failure)";

  container.innerHTML = `
    <table style="width:100%; border-collapse:collapse; font-size:13px;">
      <tr>
        <td style="color:var(--text-muted); padding:3px 0; width:110px;">Status</td>
        <td style="color:${statusColor}; font-weight:600;">${status}</td>
      </tr>
      <tr>
        <td style="color:var(--text-muted); padding:3px 0;">Conclusion</td>
        <td style="font-family:var(--font-mono);">${conclusion}</td>
      </tr>
      <tr>
        <td style="color:var(--text-muted); padding:3px 0;">Ratio</td>
        <td style="font-family:var(--font-mono);">${ratio}</td>
      </tr>
      <tr>
        <td style="color:var(--text-muted); padding:3px 0;">NumPy</td>
        <td style="font-family:var(--font-mono);">${numpy}</td>
      </tr>
    </table>
  `;
}

/**
 * Render the verdict badge.
 * @param {HTMLElement} container
 * @param {object} comparison  comparison dict from the API
 */
function renderVerdict(container, comparison) {
  const hint = comparison.verdict_hint || "";
  const changed = comparison.conclusion_changed;

  let badgeClass = "verdict-no-change";
  let label = hint;
  if (hint === "supported_conclusion_change" || changed === true) {
    badgeClass = "verdict-supported";
  } else if (hint === "setup_failure") {
    badgeClass = "verdict-failure";
  }

  container.innerHTML = `
    <strong style="font-size:13px;">Verdict hint:</strong>
    <span class="verdict-badge ${badgeClass}">${label || (changed ? "CONCLUSION CHANGED" : "NO VIOLATION FOUND")}</span>
    ${changed === true
      ? '<p style="margin-top:8px; font-size:13px; color:var(--supported);">⚠ The two environments produced <strong>different conclusions</strong>.</p>'
      : changed === false
        ? '<p style="margin-top:8px; font-size:13px; color:var(--text-muted);">Both environments returned the same conclusion.</p>'
        : ""}
  `;
}

/**
 * Call the run-pair endpoint and display results.
 * @param {number[]} signalValues
 * @returns {Promise<void>}
 */
async function runPair(signalValues) {
  const spinner = document.getElementById("run-spinner");
  const errorMsg = document.getElementById("error-msg");
  const resultSection = document.getElementById("result-section");
  const oldResult = document.getElementById("old-result");
  const newResult = document.getElementById("new-result");
  const verdictRow = document.getElementById("verdict-row");

  if (spinner) spinner.style.display = "inline";
  if (errorMsg) errorMsg.style.display = "none";

  try {
    const response = await fetch("/api/investigations/demo/run-pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input_json: { signal: signalValues } }),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(err.detail || response.statusText);
    }

    const data = await response.json();

    if (resultSection) resultSection.style.display = "block";
    if (oldResult) renderRunRecord(oldResult, data.old_run);
    if (newResult) renderRunRecord(newResult, data.new_run);
    if (verdictRow) renderVerdict(verdictRow, data.comparison || {});

  } catch (err) {
    if (errorMsg) {
      errorMsg.textContent = "Error: " + err.message;
      errorMsg.style.display = "block";
    }
  } finally {
    if (spinner) spinner.style.display = "none";
  }
}

/**
 * Approve the contract for a given project.
 * @param {string} projectId
 * @returns {Promise<void>}
 */
async function approveContract(projectId) {
  try {
    const response = await fetch(
      `/api/projects/${projectId}/contract/approve`,
      { method: "POST", headers: { "Content-Type": "application/json" } }
    );
    if (!response.ok) throw new Error(response.statusText);
    const status = document.getElementById("approve-status");
    if (status) status.style.display = "inline";
    const btn = document.getElementById("approve-btn");
    if (btn) btn.disabled = true;
  } catch (err) {
    alert("Approval failed: " + err.message);
  }
}

// ---------------------------------------------------------------------------
// DOM wiring
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", function () {
  const runBtn = document.getElementById("run-btn");
  if (runBtn) {
    runBtn.addEventListener("click", function () {
      const values = readSignalValues();
      runPair(values);
    });
  }
});
