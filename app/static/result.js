(() => {
  "use strict";
  const page = document.getElementById("result-page");
  if (!page) return;
  const id = page.dataset.investigationId;
  const view = page.dataset.view;
  const reportUrl = `/api/investigations/${encodeURIComponent(id)}/report`;
  const evidenceUrl = `/api/investigations/${encodeURIComponent(id)}/evidence`;
  const byId = (name) => document.getElementById(name);
  const node = (tag, className, value) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value !== undefined && value !== null) element.textContent = String(value);
    return element;
  };
  const append = (parent, ...children) => children.forEach((child) => parent.appendChild(child));
  const plain = (value, fallback = "Not recorded") => value === null || value === undefined || value === "" ? fallback : String(value);
  const format = (value) => value === null || value === undefined ? "Not recorded" : String(value);
  const titleCase = (value) => plain(value).replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
  const outcome = (value) => ({above_threshold: "Above threshold", below_threshold: "Below threshold", inconclusive: "Inconclusive"})[value] || titleCase(value);
  const modeLabel = (mode) => ({pinned_workers: "Pinned NumPy workers", local_simulation: "Local simulation", unverified: "Runtime unverified"})[mode] || "Runtime unverified";
  const verdicts = {
    supported_conclusion_change: ["Conclusion changed on replay", "A valid input produced different declared outcomes in the two environments."],
    observed_drift: ["Numerical drift observed", "The computed values changed, but the recorded evidence does not establish a changed declared outcome."],
    pre_existing_contract_violation: ["Existing contract violation", "The recorded checks found a contract violation that was already present before the dependency change."],
    repair_passes_stated_checks: ["Repair passed stated checks", "A candidate repair passed the recorded witness and holdout checks; this does not establish safety for all inputs."],
    no_violation_found: ["No change found in this search", "The bounded search did not find a changed declared outcome."],
    inconclusive: ["Investigation inconclusive", "The available evidence is insufficient to establish a conclusion change."],
    setup_failure: ["Investigation could not complete", "A setup or execution failure prevented a reliable result."]
  };

  function addCheck(label, heading, description) {
    const row = node("div", "result-check");
    append(row, node("span", "result-check-label", label));
    const copy = node("div");
    append(copy, node("strong", "", heading), node("p", "", description));
    append(row, copy);
    byId("result-checks").appendChild(row);
  }
  function addRepair(status, title, text) {
    const row = node("article", `result-repair ${status}`);
    const body = node("div", "result-repair-body");
    const head = node("div", "result-repair-head");
    append(head, node("h3", "", title), node("span", "result-repair-status", status === "is-pass" ? "Passed stated checks" : status === "is-fail" ? "Did not pass" : "Not verified"));
    append(body, head, node("p", "", text));
    row.appendChild(body);
    byId("result-repairs").appendChild(row);
  }
  function disclosure(title, build) {
    const details = node("details", "result-disclosure");
    const summary = node("summary", "", title);
    const body = node("div", "result-disclosure-body");
    build(body);
    append(details, summary, body);
    byId("result-disclosures").appendChild(details);
  }
  function paragraph(body, value) { body.appendChild(node("p", "", value)); }
  function list(body, values) {
    const ul = node("ul");
    values.forEach((value) => ul.appendChild(node("li", "", value)));
    body.appendChild(ul);
  }
  function render(data) {
    const witness = data.witness;
    const verdict = verdicts[data.verdict] || ["Investigation recorded", "Review the recorded observations and checks below."];
    const strongFinding = data.verdict === "supported_conclusion_change" || data.verdict === "repair_passes_stated_checks";
    byId("result-mode").textContent = modeLabel(data.run_mode);
    byId("result-status").textContent = strongFinding ? (data.run_mode === "pinned_workers" ? "REPRODUCED FINDING" : data.run_mode === "local_simulation" ? "SIMULATED FINDING" : "RECORDED FINDING") : "BOUNDED RESULT";
    if (!strongFinding) byId("result-status").classList.add("is-uncertain");
    byId("result-title").textContent = verdict[0];
    byId("result-intro").textContent = verdict[1];
    byId("result-project").textContent = plain(data.project_name, "Investigation");
    byId("result-id").textContent = plain(data.investigation_id);
    if (data.timestamp) byId("result-date").textContent = new Date(data.timestamp).toLocaleString();
    const secondary = byId("result-secondary");
    secondary.href = `/investigations/${encodeURIComponent(id)}/${view === "report" ? "evidence" : "report"}`;
    secondary.textContent = view === "report" ? "Evidence view ↗" : "Printable report ↗";
    if (view === "report") byId("result-print").hidden = false;
    byId("result-download").href = view === "report" ? reportUrl : evidenceUrl;
    byId("result-download").setAttribute("download", `reprodelta-${id}-${view}.json`);
    if (witness) {
      byId("observation-caption").textContent = "The witness input was replayed in each environment. These are the recorded outputs, not a model prediction.";
      [["Before", witness.old_run], ["After", witness.new_run]].forEach(([label, run]) => {
        const card = node("div", "result-state");
        append(card, node("span", "result-state-top", `${label} · NumPy ${plain(run.numpy_version)}`), node("h3", "", outcome(run.conclusion)), node("div", "result-number", `Ratio ${format(run.ratio)} · ${plain(run.intermediate_dtype)} intermediate`));
        byId("result-comparison").appendChild(card);
      });
      const note = byId("result-mechanism");
      append(note, node("strong", "", "Why this matters"));
      const dtypeChanged = witness.old_run.intermediate_dtype && witness.new_run.intermediate_dtype && witness.old_run.intermediate_dtype !== witness.new_run.intermediate_dtype;
      note.appendChild(node("span", "", dtypeChanged ? `The intermediate type changed from ${witness.old_run.intermediate_dtype} to ${witness.new_run.intermediate_dtype}. The recorded ratios are ${format(witness.old_run.ratio)} and ${format(witness.new_run.ratio)}. ${data.intervention && data.intervention.assessment === "supported" ? "A controlled change also removed the observed conclusion difference for this input." : "The cause remains a hypothesis until a controlled change supports it."}` : "The recorded values show the environment difference for this input. The underlying cause needs a supported intervention before it can be treated as established."));
    } else {
      byId("observation-title").textContent = "No changed outcome recorded";
      byId("observation-caption").textContent = "No conclusion-changing input was recorded within this run's search budget.";
      byId("result-comparison").appendChild(node("p", "", "There is no before/after witness to compare. This is a search limit, not proof that all inputs behave the same."));
      byId("result-comparison").classList.add("is-empty");
      byId("result-mechanism").hidden = true;
    }
    const controls = data.controls || [];
    if (controls.length) {
      const good = controls.filter((c) => c.repeatability_confirmed).length;
      addCheck("Repeatability", `${good} of ${controls.length} controls repeated`, good === controls.length ? "Each control returned the same declared outcome on its two runs in the same environment." : "At least one control differed on repeat. Treat the witness with caution until the run is stable.");
    } else addCheck("Repeatability", "No repeatability controls recorded", "The result cannot be treated as repeatable from this record.");
    if (data.intervention) {
      const intervention = data.intervention;
      addCheck("Mechanism test", intervention.assessment === "supported" ? "Prediction supported" : intervention.assessment === "contradicted" ? "Prediction contradicted" : "Test inconclusive", intervention.assessment === "supported" ? `The controlled ${titleCase(intervention.kind).toLowerCase()} change produced ${outcome(intervention.old_run?.conclusion)} in the old environment and ${outcome(intervention.new_run?.conclusion)} in the new one.` : "The controlled change did not establish the proposed mechanism. Review the full record before drawing a causal conclusion.");
    } else addCheck("Mechanism test", "No controlled test recorded", "Model suggestions alone do not establish why the result changed.");
    const reduction = data.reduction;
    if (reduction && witness) addCheck("Input reduction", `${format(reduction.size_before)} → ${format(reduction.size_after)} values`, reduction.size_after < reduction.size_before ? "The witness was reduced while preserving the changed outcome." : "The search tried to reduce the input but could not remove values while preserving the witness property.");
    const repairs = data.patch_results || [];
    repairs.forEach((repair) => {
      const passed = repair.verdict === "repair_passes_stated_checks";
      const failed = repair.verdict === "repair_fails_stated_checks";
      const checks = repair.holdout_results || [];
      const passing = checks.filter((check) => check.check_status === "pass").length;
      const text = checks.length ? `${passing} of ${checks.length} holdout checks passed. ${plain(repair.details, "Review individual checks in the record below.")}` : plain(repair.details, "No holdout results were recorded.");
      addRepair(passed ? "is-pass" : failed ? "is-fail" : "", `${titleCase(repair.patch_kind)} candidate`, text);
    });
    if (data.model_patch_result) {
      const model = data.model_patch_result;
      const passed = model.verdict === "repair_passes_stated_checks";
      addRepair(passed ? "is-pass" : model.verdict === "invalid_patch" ? "is-fail" : "", "Model proposed diff", model.verdict === "invalid_patch" ? "The proposed diff was rejected as invalid. No patch was applied or executed." : passed ? "The proposed diff passed its recorded checks. Inspect the verification record before considering it for your code." : `Verification result: ${titleCase(model.verdict)}. This is not an accepted repair.`);
    }
    if (!repairs.length && !data.model_patch_result) addRepair("", "No verified repair", "This record does not contain a candidate patch that passed stated checks.");

    if (witness) disclosure("Witness input and exact measurements", (body) => {
      const signal = witness.input_json?.signal;
      paragraph(body, `Input: ${Array.isArray(signal) ? signal.join(", ") : JSON.stringify(witness.input_json || {})}`);
      paragraph(body, `Input SHA-256: ${plain(witness.input_sha256)}`);
      paragraph(body, `Before: ${format(witness.old_run.ratio)} · ${plain(witness.old_run.intermediate_dtype)} · ${outcome(witness.old_run.conclusion)}`);
      paragraph(body, `After: ${format(witness.new_run.ratio)} · ${plain(witness.new_run.intermediate_dtype)} · ${outcome(witness.new_run.conclusion)}`);
    });
    disclosure("Control runs", (body) => {
      if (!controls.length) return paragraph(body, "No controls recorded.");
      list(body, controls.map((control) => `${plain(control.run1?.environment)}: ${outcome(control.run1?.conclusion)} / ${outcome(control.run2?.conclusion)} · ${control.repeatability_confirmed ? "repeated" : "did not repeat"}`));
    });
    if (reduction) disclosure("Input reduction steps", (body) => {
      paragraph(body, `${format(reduction.size_before)} values before, ${format(reduction.size_after)} after.`);
      list(body, (reduction.steps || []).map((step) => `Step ${format(step.step_id)}: ${plain(step.reason)} (${step.accepted ? "kept" : "rejected"})`));
    });
    if (repairs.length) disclosure("Patch holdout checks", (body) => repairs.forEach((repair) => {
      paragraph(body, `${titleCase(repair.patch_kind)}: ${titleCase(repair.verdict)}`);
      list(body, (repair.holdout_results || []).map((check, index) => `Holdout ${index + 1}: ${titleCase(check.check_status)} · before ${outcome(check.patched_old_conclusion)}, after ${outcome(check.patched_new_conclusion)}`));
    }));
    const proposals = data.model_proposals || [];
    if (proposals.length) disclosure("Model contribution", (body) => {
      paragraph(body, "A model suggested an explanation or patch. Its unedited response is excluded from this human-readable report. Only recorded replay and verification results support the findings above.");
      list(body, proposals.map((proposal, index) => `Suggestion ${index + 1}: ${proposal.is_mock ? "simulation" : "live model"} · ${plain(proposal.model_id)}`));
    });
    const failures = data.stage_failures || [];
    if (failures.length) disclosure("Steps that could not complete", (body) => list(body, failures.map((failure) => `${titleCase(failure.stage)}: ${plain(failure.error)}`)));
    if (data.demonstration_comparison) disclosure("Equal-budget comparison", (body) => {
      const comparison = data.demonstration_comparison;
      paragraph(body, `Status: ${titleCase(comparison.status)}`);
      list(body, (comparison.rows || []).map((row) => `${plain(row.case)} · ${titleCase(row.strategy)}: ${format(row.completed_pairs)}/${format(row.pair_budget)} pairs; first witness ${plain(row.first_witness_pair, "none")}`));
      list(body, comparison.limitations || []);
    });
    if (data.replay_instructions) disclosure("Replay instructions", (body) => body.appendChild(node("pre", "", data.replay_instructions)));
    const limitations = data.limitations || [];
    if (limitations.length) disclosure("Scope and limitations", (body) => list(body, limitations));
    byId("result-boundary").textContent = limitations[0] || "This investigation is bounded by its input search, environments, and stated checks. A passing patch is not a general correctness guarantee.";
    byId("result-contract").textContent = `Scope: ${plain(data.scope)} · Contract ${plain(data.contract_hash).slice(0, 16)}…`;
    byId("result-loading").hidden = true;
    byId("result-content").hidden = false;
    page.classList.add("is-ready");
  }
  fetch(reportUrl, {credentials: "same-origin"})
    .then((response) => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); })
    .then(render)
    .catch(() => {
      byId("result-loading").hidden = true;
      byId("result-error").hidden = false;
      byId("result-error").textContent = "The findings could not be loaded. This investigation may still be running. Return to the investigation and try again when it completes.";
    });
  const openedForPrint = [];
  byId("result-print").addEventListener("click", () => window.print());
  window.addEventListener("beforeprint", () => {
    document.querySelectorAll(".result-disclosure:not([open])").forEach((details) => {
      details.open = true;
      openedForPrint.push(details);
    });
  });
  window.addEventListener("afterprint", () => {
    openedForPrint.splice(0).forEach((details) => { details.open = false; });
  });
})();
