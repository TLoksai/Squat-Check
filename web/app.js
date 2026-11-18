/* Squat Check front end: upload -> progress -> report (annotated video + findings).
   No framework: three views, a polling loop, and a canvas drawn over the video. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icon = (name, cls = "") => `<svg class="ic ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const RES = {
    does_not_meet: { group: "Needs work", short: "Does not meet", icon: "x", tile: "bad" },
    meets: { group: "Looks good", short: "Meets", icon: "check", tile: "good" },
    cannot_assess: { group: "Couldn't check", short: "Can't assess", icon: "help", tile: "unk" },
  };
  const ORDER = ["does_not_meet", "meets", "cannot_assess"];
  const STAGE_ORDER = ["upload", "prepare", "pose", "bar", "measure", "coach"];

  let config = null;
  let state = freshState();
  function freshState() {
    return { jobId: null, result: null, frames: [], fps: 30, n: 0, size: [720, 1280], focus: null, selected: null,
             byKey: new Map(), fbMap: new Map(), repFilter: "all" };
  }

  // ------------------------------------------------------------ views
  function show(view) {
    for (const v of ["upload", "progress", "result"]) $(`#view-${v}`).hidden = v !== view;
    window.scrollTo({ top: 0 });
  }
  function restart() {
    const v = $("#video");
    v.pause(); v.removeAttribute("src"); v.load();
    stopPreview();
    state = freshState();
    history.replaceState(null, "", location.pathname);
    $("#file-input").value = "";
    $("#upload-error").hidden = true;
    show("upload");
  }
  document.addEventListener("click", (e) => { if (e.target.closest("[data-action=restart]")) restart(); });

  function toast(msg, ms = 3200) {
    const t = $("#toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
  }

  // ------------------------------------------------------------ upload screen
  async function loadConfig() {
    try {
      config = await (await fetch("api/config")).json();
      const L = config.limits;
      $("#limits-text").textContent = `${L.formats.map((f) => f.toUpperCase()).join(", ")} · ${L.min_duration_s}-${L.max_duration_s} s · up to ${L.max_upload_mb} MB`;
      $("#list-assessable").innerHTML = config.skill.assessable.map((c) => `<li>${esc(c.title)}</li>`).join("");
      $("#list-not-assessable").innerHTML = config.skill.not_assessable.map((c) => `<li>${esc(c.title)}</li>`).join("");
      $("#btn-sample").hidden = !config.sample_available;
    } catch { /* keep defaults */ }
  }
  function uploadError(msg) { const el = $("#upload-error"); el.textContent = msg; el.hidden = false; }
  function videoDuration(file) {
    return new Promise((resolve) => {
      const v = document.createElement("video"), url = URL.createObjectURL(file);
      const done = (d) => { URL.revokeObjectURL(url); resolve(d); };
      v.preload = "metadata"; v.onloadedmetadata = () => done(v.duration); v.onerror = () => done(null);
      setTimeout(() => done(null), 4000); v.src = url;
    });
  }
  async function handleFile(file) {
    $("#upload-error").hidden = true;
    if (!file) return;
    const L = config?.limits, ext = file.name.split(".").pop().toLowerCase();
    if (L && !L.formats.includes(ext)) return uploadError(`“.${ext}” files aren't supported. Use ${L.formats.join(", ")}.`);
    if (L && file.size > L.max_upload_mb * 1048576) return uploadError(`This file is ${(file.size / 1048576).toFixed(0)} MB; the limit is ${L.max_upload_mb} MB.`);
    const dur = await videoDuration(file);
    if (L && dur && isFinite(dur) && dur > L.max_duration_s + 0.5) return uploadError(`This video is ${Math.round(dur)} s long. Please trim it to ${L.max_duration_s} s or less.`);
    if (L && dur && isFinite(dur) && dur < L.min_duration_s) return uploadError(`This video is only ${dur.toFixed(1)} s. Record at least one full rep.`);
    startUpload(file);
  }
  const dz = $("#dropzone");
  $("#file-input").addEventListener("change", (e) => handleFile(e.target.files[0]));
  dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("#file-input").click(); } });
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
  dz.addEventListener("drop", (e) => handleFile(e.dataTransfer.files[0]));
  $("#btn-sample").addEventListener("click", startSample);

  // ------------------------------------------------------------ progress
  let elapsedTimer = null, previewUrl = null;
  function setProgress(stage, frac, label) {
    const idx = STAGE_ORDER.indexOf(stage);
    $$("#stages li").forEach((li) => {
      const i = STAGE_ORDER.indexOf(li.dataset.stage);
      li.classList.toggle("done", i < idx || stage === "done");
      li.classList.toggle("active", i === idx && stage !== "done");
    });
    const pct = Math.round(Math.max(0, Math.min(1, frac)) * 100);
    $("#progress-bar").style.width = pct + "%";
    $("#progress-pct").textContent = pct + "%";
    $("#progress-label").textContent = label || "";
  }
  function progressError(msg) {
    clearInterval(elapsedTimer);
    $("#progress-error-text").textContent = msg;
    $("#progress-error").hidden = false;
    $("#progress-title").textContent = "Something went wrong";
    $$("#stages li.active").forEach((li) => li.classList.remove("active"));
  }
  function beginProgress(name, file) {
    $("#progress-error").hidden = true;
    $("#progress-title").textContent = "Analysing your squat";
    $("#progress-file").textContent = name;
    stopPreview();
    if (file) {
      previewUrl = URL.createObjectURL(file);
      const pv = $("#preview-video"); pv.src = previewUrl; pv.play().catch(() => {});
    }
    const t0 = Date.now();
    clearInterval(elapsedTimer);
    elapsedTimer = setInterval(() => ($("#progress-elapsed").textContent = `${Math.round((Date.now() - t0) / 1000)} s`), 1000);
    $("#progress-elapsed").textContent = "0 s";
    show("progress");
  }
  function stopPreview() {
    const pv = $("#preview-video"); pv.pause(); pv.removeAttribute("src"); pv.load();
    if (previewUrl) { URL.revokeObjectURL(previewUrl); previewUrl = null; }
  }
  function startUpload(file) {
    beginProgress(file.name, file);
    setProgress("upload", 0, "Uploading");
    const form = new FormData(); form.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "api/jobs");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) setProgress("upload", (e.loaded / e.total) * 0.1, `Uploading ${Math.round((e.loaded / e.total) * 100)}%`); };
    xhr.onload = () => {
      let body = {}; try { body = JSON.parse(xhr.responseText); } catch {}
      if (xhr.status >= 200 && xhr.status < 300 && body.id) poll(body.id);
      else progressError(body.detail || `Upload failed (HTTP ${xhr.status}).`);
    };
    xhr.onerror = () => progressError("The upload failed. Check your connection and try again.");
    xhr.send(form);
  }
  async function startSample() {
    beginProgress("Assignment sample video");
    setProgress("upload", 0.1, "Using the sample video");
    try {
      const r = await fetch("api/jobs/sample", { method: "POST" }); const body = await r.json();
      if (!r.ok) throw new Error(body.detail || "Couldn't start the sample.");
      poll(body.id);
    } catch (e) { progressError(e.message); }
  }
  async function poll(id) {
    state.jobId = id;
    history.replaceState(null, "", `#job=${id}`);
    while (state.jobId === id) {
      let st;
      try {
        const r = await fetch(`api/jobs/${id}`);
        if (r.status === 404) return progressError("This analysis no longer exists. It may have been deleted.");
        st = await r.json();
      } catch { await sleep(1500); continue; }
      if (st.status === "failed") return progressError(st.error || "Analysis failed.");
      if (st.status === "done") { setProgress("done", 1, "Done"); clearInterval(elapsedTimer); return loadResult(id); }
      setProgress(st.status === "queued" ? "prepare" : st.stage, 0.1 + 0.9 * (st.progress || 0),
        st.status === "queued" ? "Waiting for the previous video to finish" : st.message);
      await sleep(700);
    }
  }

  // ------------------------------------------------------------ report
  async function loadResult(id) {
    const [res, st] = await Promise.all([
      fetch(`api/jobs/${id}/result`).then((r) => (r.ok ? r.json() : Promise.reject(r))),
      fetch(`api/jobs/${id}`).then((r) => r.json()).catch(() => ({})),
    ]).catch(() => [null, null]);
    if (!res) return progressError("We couldn't load the result. Please try again.");
    stopPreview();
    state.result = res; state.jobId = id;
    renderResult(res, st);
    show("result");
  }
  const fmtTime = (s) => { const m = Math.floor(s / 60); return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`; };
  function pageRef(sources) {
    const pages = [...new Set(sources.map((s) => s.page))].sort((a, b) => a - b);
    const title = sources.map((s) => `p.${s.page}: ${s.section}`).join("\n");
    return `<span class="page-ref" title="${esc(title)}">${icon("book")}p. ${pages.join(", ")}</span>`;
  }
  function measureText(f) {
    const m = f.measurement || {};
    if (m.value == null) return "not measured";
    const unit = (String(m.unit || "").match(/^(thigh lengths|foot lengths|degrees|seconds)/) || [])[1] || String(m.unit || "").split(" (")[0];
    const v = Number(m.value);
    const txt = unit === "degrees" ? `${v.toFixed(0)}°` : unit === "seconds" ? `${v.toFixed(2)} s` : `${v.toFixed(2)} ${unit}`;
    return txt + (m.extra?.approx_cm != null ? ` (≈ ${m.extra.approx_cm} cm)` : "");
  }

  function renderResult(res, st) {
    const a = res.analysis, ev = res.evaluation, fb = res.feedback || {};
    Object.assign(state, {
      frames: a.frames || [], fps: a.fps || 30, n: a.frame_count || (a.frames || []).length,
      size: a.frame_size || [res.video.width, res.video.height], focus: null, selected: null, repFilter: "all",
      fbMap: new Map((fb.findings_feedback || []).map((x) => [`${x.rep}|${x.criterion_id}`, x])),
      byKey: new Map((ev.findings || []).map((f) => [`${f.rep}|${f.criterion_id}`, f])),
    });
    const reps = a.reps || [];
    const ok = a.status === "ok";

    $("#result-meta").textContent = [st?.filename, `${res.video.duration_s.toFixed(1)} s video`,
      ok ? `${reps.length} rep${reps.length === 1 ? "" : "s"}` : null, `analysed in ${Math.round(res.timings.total_s)} s`]
      .filter(Boolean).join(" · ");
    $("#report-title").textContent = ok ? `Your squat, rep by rep` : `This video couldn't be assessed`;
    $("#unsuitable").hidden = ok;
    $("#verdict").hidden = !ok;
    $("#report-grid").hidden = !ok;
    $("#unsuitable-text").textContent = ok ? "" : a.message;
    if (!ok) return;

    // verdict tiles + coach
    const c = ev.counts || {};
    $("#counts").innerHTML = ORDER.map((k) => `
      <div class="stat ${RES[k].tile}"><span class="stat-ic">${icon(RES[k].icon)}</span>
        <b>${c[k] || 0}</b><span>${RES[k].group}</span></div>`).join("");
    const aiName = { claude: "Claude", gemini: "Gemini" }[fb.source];
    $("#coach-source").textContent = aiName ? `Written by the AI coach (${aiName}) from the rulebook` : (fb.note || "Tips taken straight from the rulebook");
    $("#summary-text").textContent = fb.overall_summary || "";

    setupPlayer(reps);
    renderOverview(ev, fb, reps);
    renderReps(ev, reps);
    renderNA(ev);
    renderHow(res, a, fb);
    selectTab("overview");

    const fails = ev.findings.filter((f) => f.result === "does_not_meet").length;
    $("#reps-count").textContent = fails ? String(fails) : "";
    $("#reps-count").hidden = !fails;
    // show the first problem straight away so the overlay has something to point at
    const first = ev.findings.find((f) => f.result === "does_not_meet") || ev.findings[0];
    if (first) selectFinding(first, { seek: true });
    resizeCanvas();
  }

  // ---------- overview
  function renderOverview(ev, fb, reps) {
    const prios = fb.top_priorities || [];
    $("#priorities").innerHTML = prios.length ? prios.map((p, i) => {
      const f = ev.findings.find((x) => x.criterion_id === p.criterion_id && x.result === "does_not_meet");
      return `<div class="prio"><span class="prio-num">${i + 1}</span>
        <div><h3>${esc(p.title)}</h3><p>${esc(p.cue)} <span class="page-ref">${icon("book")}p. ${esc(p.page)}</span></p>
          <p class="why">${esc(p.why)}</p></div>
        ${f ? `<button class="btn btn-soft" type="button" data-open="${f.rep}|${f.criterion_id}">Show me ${icon("arrow")}</button>` : ""}</div>`;
    }).join("") : `<div class="empty-good">${icon("check")} Nothing measurable failed the standard. Check the “Couldn't check” items too.</div>`;

    const crit = [];
    for (const f of ev.findings) if (!crit.find((x) => x.id === f.criterion_id)) crit.push({ id: f.criterion_id, title: f.title, sources: f.sources });
    $("#glance").innerHTML = `<thead><tr><th>Check</th>${reps.map((r) => `<th class="rep-col">Rep ${r.index}</th>`).join("")}</tr></thead>
      <tbody>${crit.map((cr) => `<tr><td><span class="c-title">${esc(cr.title)}</span><br>${pageRef(cr.sources)}</td>${reps.map((r) => {
        const f = state.byKey.get(`${r.index}|${cr.id}`);
        return `<td class="rep-col">${f ? `<button class="res-dot ${f.result}" type="button" data-open="${r.index}|${cr.id}"
          title="Rep ${r.index}: ${esc(RES[f.result].short)}" aria-label="${esc(cr.title)}, rep ${r.index}: ${esc(RES[f.result].short)}">${icon(RES[f.result].icon)}</button>` : ""}</td>`;
      }).join("")}</tr>`).join("")}</tbody>`;
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-open]");
    if (b) openFinding(b.dataset.open);
  });

  // ---------- rep by rep
  function renderReps(ev, reps) {
    const fails = (i) => ev.findings.filter((f) => f.rep === i && f.result === "does_not_meet").length;
    $("#rep-chips").innerHTML = [`<button type="button" data-rep="all" class="${state.repFilter === "all" ? "on" : ""}">All reps</button>`]
      .concat(reps.map((r) => `<button type="button" data-rep="${r.index}" class="${state.repFilter === r.index ? "on" : ""}">Rep ${r.index}<span class="mini">${fails(r.index) ? `· ${fails(r.index)} ✗` : ""}</span></button>`))
      .join("");
    const list = ev.findings.filter((f) => state.repFilter === "all" || f.rep === state.repFilter);
    $("#finding-groups").innerHTML = ORDER.map((k) => {
      const items = list.filter((f) => f.result === k);
      if (!items.length) return "";
      return `<section class="group"><h3 class="group-h ${k}">${icon(RES[k].icon)} ${RES[k].group} <span class="count">${items.length}</span></h3>
        ${items.map(findingCard).join("")}</section>`;
    }).join("");
  }
  function findingCard(f) {
    const key = `${f.rep}|${f.criterion_id}`, fb = state.fbMap.get(key) || {}, m = f.measurement || {};
    const tag = f.threshold_source === "document" ? `<span class="tag doc">document</span>` : `<span class="tag interp">app interpretation</span>`;
    return `<details class="finding ${f.result}" data-key="${key}">
      <summary><span class="st-ic">${icon(RES[f.result].icon)}</span>
        <span><span class="f-title">${esc(f.title)}</span><span class="f-sub">Rep ${f.rep} · ${fmtTime(f.time_s)} · ${esc(measureText(f))}</span></span>
        ${icon("right", "f-chev")}</summary>
      <div class="f-body">
        ${fb.feedback ? `<div class="f-coach">${icon("spark")}<span>${esc(fb.feedback)}</span></div>` : ""}
        <dl class="f-dl">
          <dt>The standard</dt><dd>${esc(f.requirement)} ${pageRef(f.sources)}</dd>
          <dt>What we saw</dt><dd>${esc(fb.explanation || f.explanation)}</dd>
          <dt>Measured</dt><dd>${esc(measureText(f))}${m.observed === false ? " · <i>points hidden or estimated</i>" : ""}</dd>
          <dt>Target</dt><dd>${esc(f.target_text)} ${tag}</dd>
          <dt>Uncertainty</dt><dd>Within ±${f.uncertainty_band} of a limit counts as “can't assess”.${fb.uncertainty ? " " + esc(fb.uncertainty) : ""}</dd>
        </dl>
        <div class="f-actions"><button class="btn btn-soft" type="button" data-seek="${f.frame}">${icon("play")} Show this moment (${fmtTime(f.time_s)})</button></div>
        <details class="f-calc"><summary>How this was calculated</summary><p>${esc(f.explanation)}</p>
          ${f.threshold_note ? `<p><b>Where the number comes from:</b> ${esc(f.threshold_note)}</p>` : ""}</details>
      </div></details>`;
  }
  $("#rep-chips").addEventListener("click", (e) => {
    const b = e.target.closest("[data-rep]"); if (!b) return;
    state.repFilter = b.dataset.rep === "all" ? "all" : Number(b.dataset.rep);
    renderReps(state.result.evaluation, state.result.analysis.reps);
    const rep = state.result.analysis.reps.find((r) => r.index === state.repFilter);
    if (rep) seekFrame(rep.bottom_frame);
  });
  $("#finding-groups").addEventListener("toggle", (e) => {
    const d = e.target; if (!(d instanceof HTMLDetailsElement) || !d.classList.contains("finding") || !d.open) return;
    const f = state.byKey.get(d.dataset.key); if (f) selectFinding(f, { seek: true });
  }, true);
  function openFinding(key) {
    const f = state.byKey.get(key); if (!f) return;
    state.selected = key;
    if (state.repFilter !== "all" && state.repFilter !== f.rep) state.repFilter = f.rep;
    renderReps(state.result.evaluation, state.result.analysis.reps);
    selectTab("reps");
    selectFinding(f, { seek: true });
    const el = $(`#finding-groups details[data-key="${CSS.escape(key)}"]`);
    if (el) { el.open = true; el.scrollIntoView({ behavior: "smooth", block: "center" }); }
  }

  // ---------- not checkable + how
  function renderNA(ev) {
    $("#na-list").innerHTML = (ev.not_assessable || []).map((n) => `<li><span class="st-ic" style="background:var(--unk-soft);color:var(--unk-ink)">${icon("camera")}</span>
      <div><b>${esc(n.title)}</b><p>${esc(n.reason)}</p>${pageRef(n.sources)}</div></li>`).join("");
  }
  function renderHow(res, a, fb) {
    const rows = [];
    const add = (ic, html, cls = "") => rows.push(`<li class="${cls}"><span class="tip-ic">${icon(ic)}</span><div>${html}</div></li>`);
    for (const i of a.issues || []) add("alert", esc(i.message), "warn");
    add("person", `<b>Body points.</b> MediaPipe Pose, run twice per frame (as recorded and mirrored) and averaged. Where the two runs disagree, a check's uncertainty margin grows. Hollow dots were hidden or derived.`);
    add("target", `<b>Bar.</b> The weight plate is found in each frame (circle detection, with appearance matching for blurred frames). Bar measured in ${Math.round((a.bar_detected_ratio || 0) * 100)}% of frames.`);
    add("ruler", `<b>Torso line.</b> ${esc(a.torso_line)}. The lifter faces ${esc(a.facing)}; the camera sees their ${esc(a.side)} side.`);
    if (a.scale?.px_per_cm) add("ruler", `<b>Centimetres are approximate:</b> ${esc(a.scale.how)}.`);
    add("camera", `<b>Camera angle.</b> Left and right hips appear ${(a.camera?.hip_separation ?? 0).toFixed(2)} thigh lengths apart (0 = perfect side view); this widens the bar-over-midfoot margin by ${(a.camera?.parallax_foot_lengths ?? 0).toFixed(2)} foot lengths.`);
    if (a.quality) add("sun", `<b>Video quality</b> around the lifter: leg ${Math.round(a.quality.leg_px)} px long, brightness ${a.quality.brightness}, sharpness ${a.quality.sharpness}.`);
    const aiName = { claude: "Claude", gemini: "Gemini" }[fb.source];
    add("book", `<b>Results</b> come from fixed geometry rules in <code>criteria.yaml</code> (rulebook v${esc(res.skill.version)}). ${aiName ? `The AI coach (${esc(fb.model || aiName)}) only writes the explanations and tips; it cannot change a result.` : "The AI coach did not run, so tips come straight from the rulebook."}`);
    if (fb.usage) add("spark", `<b>AI coach usage:</b> ${fb.usage.input_tokens} input tokens (${fb.usage.cache_read_input_tokens} cached), ${fb.usage.output_tokens} output tokens.`);
    if (fb.validation?.dropped_items) add("alert", `${fb.validation.dropped_items} coach item(s) weren't grounded in the rulebook and were replaced with rulebook text.`, "warn");
    const t = res.timings;
    add("clock", `<b>Timing:</b> prepare ${t.prepare_s}s · body ${t.pose_s}s · bar ${t.bar_s}s · coach ${t.coach_s}s.`);
    $("#how-body").innerHTML = `<ul class="how-list">${rows.join("")}</ul>`;
  }

  // ---------- tabs
  function selectTab(name) {
    for (const t of ["overview", "reps", "na", "how"]) {
      $(`#tab-${t}`).setAttribute("aria-selected", String(t === name));
      $(`#panel-${t}`).hidden = t !== name;
    }
  }
  $$(".tabs [role=tab]").forEach((b) => b.addEventListener("click", () => selectTab(b.id.replace("tab-", ""))));
  $(".tabs").addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const tabs = $$(".tabs [role=tab]"), i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    const next = tabs[(i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
    next.focus(); next.click();
  });

  // ---------- selection -> overlay
  function selectFinding(f, { seek } = {}) {
    state.selected = `${f.rep}|${f.criterion_id}`;
    state.focus = f.criterion_id;
    $$(".res-dot").forEach((b) => b.classList.toggle("selected", b.dataset.open === state.selected));
    const ft = $("#focus-tag");
    ft.hidden = false;
    ft.textContent = `${RES[f.result].short === "Meets" ? "✓" : RES[f.result].short === "Does not meet" ? "✗" : "?"} ${f.title} · rep ${f.rep}`;
    if (seek) seekFrame(f.frame); else draw(frameOf(video.currentTime));
  }

  // ------------------------------------------------------------ player + overlay
  const video = $("#video"), canvas = $("#overlay"), ctx = canvas.getContext("2d");
  const frameOf = (t) => Math.max(0, Math.min(state.n - 1, Math.floor(t * state.fps + 1e-3)));
  function setupPlayer(reps) {
    const [w, h] = state.size, player = $("#player");
    player.style.setProperty("--ar", `${w} / ${h}`);
    player.style.setProperty("--ar-num", String(w / h));
    video.src = `api/jobs/${state.jobId}/video`;
    video.playbackRate = Number($(".speed .on")?.dataset.speed || 0.5);
    $("#scrub").max = Math.max(0, state.n - 1); $("#scrub").value = 0;
    $("#rep-strip").innerHTML = reps.map((r) => {
      const left = (r.start_frame / state.n) * 100, width = ((r.end_frame - r.start_frame) / state.n) * 100;
      const tick = ((r.bottom_frame - r.start_frame) / Math.max(1, r.end_frame - r.start_frame)) * 100;
      return `<button class="rep-seg" type="button" data-frame="${r.bottom_frame}" style="left:${left}%;width:${width}%"
        title="Rep ${r.index}: bottom at ${fmtTime(r.bottom_s)}"><span class="bottom-tick" style="left:${tick}%"></span><span class="rep-lbl">${r.index}</span></button>`;
    }).join("");
  }
  function seekFrame(k) {
    k = Math.max(0, Math.min(state.n - 1, k));
    video.pause();
    video.currentTime = (k + 0.5) / state.fps;
    draw(k);
  }
  function togglePlay() { video.paused ? video.play() : video.pause(); }
  $("#btn-play").addEventListener("click", togglePlay);
  video.addEventListener("play", () => { $("#btn-play").innerHTML = icon("pause"); $("#btn-play").setAttribute("aria-label", "Pause (space)"); });
  video.addEventListener("pause", () => { $("#btn-play").innerHTML = icon("play"); $("#btn-play").setAttribute("aria-label", "Play (space)"); });
  $("#btn-prev").addEventListener("click", () => seekFrame(frameOf(video.currentTime) - 1));
  $("#btn-next").addEventListener("click", () => seekFrame(frameOf(video.currentTime) + 1));
  $("#scrub").addEventListener("input", (e) => seekFrame(+e.target.value));
  $("#rep-strip").addEventListener("click", (e) => { const b = e.target.closest(".rep-seg"); if (b) seekFrame(+b.dataset.frame); });
  $(".speed").addEventListener("click", (e) => {
    const b = e.target.closest("[data-speed]"); if (!b) return;
    $$(".speed button").forEach((x) => x.classList.toggle("on", x === b));
    video.playbackRate = Number(b.dataset.speed);
  });
  document.addEventListener("click", (e) => { const b = e.target.closest("[data-seek]"); if (b) seekFrame(+b.dataset.seek); });
  ["lay-body", "lay-bar", "lay-measure"].forEach((id) => $(`#${id}`).addEventListener("change", () => draw(frameOf(video.currentTime))));
  ["seeked", "loadeddata", "pause", "timeupdate"].forEach((ev) => video.addEventListener(ev, () => draw(frameOf(video.currentTime))));
  if ("requestVideoFrameCallback" in HTMLVideoElement.prototype) {
    const onFrame = (_now, meta) => { draw(frameOf(meta.mediaTime)); video.requestVideoFrameCallback(onFrame); };
    video.requestVideoFrameCallback(onFrame);
  } else {
    const loop = () => { if (!video.paused) draw(frameOf(video.currentTime)); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  }
  document.addEventListener("keydown", (e) => {
    const el = e.target instanceof Element ? e.target : null;
    if ($("#view-result").hidden || el?.closest("input, textarea, select, [role=tab], summary, button") || $("#rulebook-dialog").open) return;
    if (e.key === " " || e.key === "k") { e.preventDefault(); togglePlay(); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); seekFrame(frameOf(video.currentTime) - 1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); seekFrame(frameOf(video.currentTime) + 1); }
  });
  function resizeCanvas() {
    const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(r.width * dpr)); canvas.height = Math.max(1, Math.round(r.height * dpr));
    draw(frameOf(video.currentTime || 0));
  }
  window.addEventListener("resize", resizeCanvas);
  new ResizeObserver(resizeCanvas).observe($("#player"));

  const C = { body: "#ffffff", joint: "#f59e0b", bar: "#a855f7", mid: "#22d3ee", good: "#4ade80", bad: "#fb923c", torso: "#facc15" };
  const repAt = (t) => (state.result?.analysis.reps || []).find((r) => t >= r.start_frame - 2 && t <= r.end_frame + 2);

  function draw(t) {
    const W = canvas.width, H = canvas.height;
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, W, H);
    const F = state.frames[t];
    if (!state.result) return;
    $("#scrub").value = t;
    $("#time").innerHTML = `${fmtTime(t / state.fps)}<span class="t-total"> / ${fmtTime(state.n / state.fps)}</span>`;
    if (!F) { $("#frame-tag").textContent = ""; return; }
    const s = W / state.size[0], u = (window.devicePixelRatio || 1) / s;
    ctx.setTransform(s, 0, 0, s, 0, 0);
    const a = state.result.analysis, rep = repAt(t), atBottom = rep && Math.abs(t - rep.bottom_frame) <= 3, focus = state.focus;
    $("#frame-tag").textContent = `frame ${t}${rep ? ` · rep ${rep.index}` : ""}${atBottom ? " · bottom" : ""}`;

    const line = (p, q, color, w, dash) => {
      if (!p || !q) return;
      ctx.beginPath(); ctx.setLineDash(dash ? dash.map((d) => d * u) : []);
      ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]);
      ctx.strokeStyle = "rgba(0,0,0,.55)"; ctx.lineWidth = (w + 2) * u; ctx.stroke();
      ctx.strokeStyle = color; ctx.lineWidth = w * u; ctx.stroke(); ctx.setLineDash([]);
    };
    const dot = (p, est, color = C.joint, r = 5) => {
      if (!p) return;
      ctx.beginPath(); ctx.arc(p[0], p[1], r * u, 0, Math.PI * 2);
      if (est) { ctx.fillStyle = "rgba(0,0,0,.35)"; ctx.fill(); ctx.lineWidth = 2.5 * u; ctx.strokeStyle = color; ctx.stroke(); }
      else { ctx.fillStyle = color; ctx.fill(); ctx.strokeStyle = "rgba(0,0,0,.6)"; ctx.lineWidth = 1.2 * u; ctx.stroke(); }
    };
    const label = (text, x, y, color = "#fff", bg = "rgba(0,0,0,.72)") => {
      ctx.font = `600 ${13 * u}px Inter, system-ui, sans-serif`;
      const w = ctx.measureText(text).width + 12 * u, h = 22 * u;
      const bx = Math.min(Math.max(2 * u, x), state.size[0] - w - 2 * u), by = Math.min(Math.max(2 * u, y - h / 2), state.size[1] - h - 2 * u);
      ctx.fillStyle = bg; ctx.beginPath(); ctx.roundRect(bx, by, w, h, 6 * u); ctx.fill();
      ctx.fillStyle = color; ctx.textBaseline = "middle"; ctx.fillText(text, bx + 6 * u, by + h / 2);
    };

    const feet = rep?.feet || a.feet, feetSeen = feet.seen !== false;
    const mid = feet.midfoot, heel = feet.heel, toe = feet.toe;

    if ($("#lay-measure").checked) {
      if (feetSeen) {
        const topY = F.bar ? Math.min(F.bar[1], mid[1]) - 60 : mid[1] - state.size[1] * 0.8;
        line([mid[0], mid[1]], [mid[0], Math.max(0, topY)], C.mid, focus === "bar_over_midfoot" ? 3 : 2, [6, 5]);
        label("midfoot", mid[0] + 6 * u, mid[1] - 14 * u, "#0b2530", "rgba(34,211,238,.92)");
      }
      if (F.knee_s && F.hip_s && (focus === "depth" || (!focus && atBottom))) {
        const below = F.hip_s[1] > F.knee_s[1], x0 = F.knee_s[0], x1 = F.hip_s[0] + (F.hip_s[0] - F.knee_s[0]) * 0.35;
        line([x0, F.knee_s[1]], [x1, F.knee_s[1]], below ? C.good : C.bad, 3, [5, 4]);
        const cm = atBottom ? (rep && state.byKey.get(`${rep.index}|depth`)?.measurement?.extra?.approx_cm) : null;
        label(`hip ${below ? "below" : "above"} knee${cm != null ? ` (${cm} cm)` : ""}`, Math.min(x0, x1), F.knee_s[1] + 24 * u, "#111", below ? "rgba(74,222,128,.94)" : "rgba(251,146,60,.94)");
      }
      if (F.hip_s && F.torso_top && (focus === "back_angle_bottom" || focus === "hip_drive")) {
        line(F.hip_s, F.torso_top, C.torso, 3);
        const dir = Math.sign(F.torso_top[0] - F.hip_s[0]) || 1;
        line(F.hip_s, [F.hip_s[0] + dir * 90 * u, F.hip_s[1]], C.torso, 1.5, [4, 4]);
        const mx = (F.hip_s[0] + F.torso_top[0]) / 2, my = (F.hip_s[1] + F.torso_top[1]) / 2;
        label(`back ${Math.round(F.back_angle)}° from floor`, mx - dir * 130 * u, my, "#111", "rgba(250,204,21,.94)");
      }
      if (focus === "knee_position_bottom" && F.knee_s && feetSeen) {
        line([toe[0], toe[1]], [toe[0], F.knee_s[1] - 30 * u], C.mid, 2, [3, 4]);
        line([F.knee_s[0], F.knee_s[1]], [toe[0], F.knee_s[1]], C.mid, 2);
        label("knee vs toe", Math.min(F.knee_s[0], toe[0]), F.knee_s[1] - 24 * u, "#0b2530", "rgba(34,211,238,.94)");
      }
      if (focus === "heels_down" && feetSeen) { dot(heel, false, C.mid, 8); label("heel", heel[0] + 10 * u, heel[1] - 20 * u, "#0b2530", "rgba(34,211,238,.94)"); }
      if (focus === "no_pause_bottom" && rep && F.hip_s) {
        const m = state.byKey.get(`${rep.index}|no_pause_bottom`)?.measurement;
        if (m?.value != null) label(`time at the bottom: ${Number(m.value).toFixed(2)} s`, F.hip_s[0] - 60 * u, F.hip_s[1] - 32 * u);
      }
    }
    if ($("#lay-body").checked) {
      line(F.shoulder, F.hip, C.body, 3); line(F.hip, F.knee, C.body, 3); line(F.knee, F.ankle, C.body, 3);
      dot(F.shoulder, F.shoulder_est); dot(F.hip, F.hip_est); dot(F.knee, F.knee_est); dot(F.ankle, F.ankle_est);
      if (feetSeen) { line(F.ankle, heel, C.body, 3); line(heel, toe, C.body, 3); dot(heel, true, C.joint, 4); dot(toe, true, C.joint, 4); dot(mid, true, C.mid, 4); }
    }
    if ($("#lay-bar").checked) {
      const start = rep ? rep.start_frame : 0;
      ctx.lineCap = "round";
      for (let k = Math.max(start, 1); k <= t; k++) {
        const p = state.frames[k - 1]?.bar, q = state.frames[k]?.bar;
        if (!p || !q) continue;
        ctx.beginPath(); ctx.setLineDash(state.frames[k].bar_status === "interpolated" ? [4 * u, 4 * u] : []);
        ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]);
        ctx.strokeStyle = C.bar; ctx.lineWidth = (focus === "bar_path_vertical" || focus === "bar_over_midfoot" ? 5 : 4) * u; ctx.stroke();
      }
      ctx.setLineDash([]);
      if (F.bar) {
        if (F.bar_r) { ctx.beginPath(); ctx.arc(F.bar[0], F.bar[1], F.bar_r, 0, Math.PI * 2); ctx.strokeStyle = "rgba(168,85,247,.85)"; ctx.lineWidth = 2 * u; ctx.stroke(); }
        dot(F.bar, F.bar_status === "interpolated", C.bar, 6);
        if ($("#lay-measure").checked && feetSeen && (focus === "bar_over_midfoot" || focus === "bar_path_vertical")) {
          line([F.bar[0], F.bar[1]], [mid[0], F.bar[1]], C.mid, 2);
          const dx = Math.abs(F.bar[0] - mid[0]), cmPer = a.scale?.px_per_cm ? 1 / a.scale.px_per_cm : null;
          const ahead = Math.sign((F.bar[0] - mid[0]) * (a.facing === "right" ? 1 : -1)) > 0;
          label(`bar ${cmPer ? Math.round(dx * cmPer) + " cm" : Math.round(dx) + " px"} ${ahead ? "ahead of" : "behind"} midfoot`, Math.min(F.bar[0], mid[0]), F.bar[1] + 28 * u, "#0b2530", "rgba(34,211,238,.94)");
        }
      } else if (a.bar_detected_ratio > 0) {
        label("bar not tracked in this frame", 8 * u, 44 * u);
      }
    }
  }

  // ------------------------------------------------------------ actions
  $("#btn-recheck").addEventListener("click", async () => {
    const btn = $("#btn-recheck"), lbl = btn.querySelector("span");
    btn.disabled = true; lbl.textContent = "Re-checking…";
    try {
      const r = await fetch(`api/jobs/${state.jobId}/reassess`, { method: "POST" });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || "Re-check failed");
      const st = await fetch(`api/jobs/${state.jobId}`).then((x) => x.json()).catch(() => ({}));
      state.result = body; renderResult(body, st);
      toast(`Re-checked with the rulebook loaded at ${body.skill.loaded_at.slice(11)}`);
    } catch (e) { toast(e.message, 5000); }
    finally { btn.disabled = false; lbl.textContent = "Re-check with rulebook"; }
  });
  $("#btn-delete").addEventListener("click", async () => {
    if (!state.jobId || !confirm("Delete this video and its analysis from the server? This can't be undone.")) return;
    await fetch(`api/jobs/${state.jobId}`, { method: "DELETE" }).catch(() => {});
    restart(); toast("Video and analysis deleted");
  });
  // ------------------------------------------------------------ rulebook viewer
  const dlg = $("#rulebook-dialog");
  const shortUnit = (u) => (String(u || "").match(/^(thigh lengths|foot lengths|degrees|seconds)/) || [])[1] || String(u || "").split(" (")[0];
  const fmtTarget = (t = {}, unit) => {
    const u = shortUnit(unit);
    if (t.min != null && t.max != null) return `between ${t.min} and ${t.max} ${u}`;
    if (t.min != null) return `more than ${t.min} ${u}`;
    return `at most ${t.max} ${u}`;
  };
  const PHASE = { bottom: "At the bottom of each rep", whole_rep: "Across the whole rep", early_ascent: "Start of the way up" };
  const SETTINGS = {
    video_quality: ["Video quality gate", "Videos below these limits are rejected with an explanation instead of being measured.", {
      min_leg_px: ["Smallest lifter size", "Hip-to-ankle length in the picture", "px"],
      min_brightness: ["Darkest allowed", "Average grey level around the lifter (0-255)", ""],
      min_sharpness: ["Blurriest allowed", "Sharpness score around the lifter", ""],
      warn_sharpness: ["Blur warning below", "Shows a 'less precise' warning", ""],
    }],
    estimates: ["Measurement assumptions", "App choices used to turn body points into the points the reference talks about. None of these are document rules.", {
      min_landmark_visibility: ["Body-point confidence needed", "Below this a point counts as not seen", ""],
      min_bar_detection_ratio: ["Bar must be measured in", "Share of a rep's frames; below this, bar checks aren't judged", "%"],
      foot_thigh_ratio: ["Foot / thigh length", "Used only when the feet are out of the picture", "×"],
      hip_width_thigh_ratio: ["Hip width / thigh length", "Used to estimate the camera angle", "×"],
      plate_to_foot_lateral_foot_lengths: ["Plate-to-foot sideways gap", "Feeds the camera-angle allowance for bar over midfoot", "foot lengths"],
      plate_perspective_factor: ["Plate perspective factor", "The plate is nearer the camera; widens the bar-over-midfoot margin", "×"],
    }],
  };
  const pageChips = (sources) => sources.map((s) =>
    `<span class="page-ref" title="${esc(s.section)}">${icon("book")}p. ${esc(s.page)} · ${esc(s.section)}</span>`).join("");
  const searchText = (c) => [c.id, c.title, c.requirement, c.reason, c.check?.unit, c.check?.note,
    ...(c.sources || []).flatMap((s) => [`p. ${s.page}`, `p.${s.page}`, s.section]),
    ...(c.cues_if_not_met || []).map((q) => q.text)].filter(Boolean).join(" ").toLowerCase();

  function ruleCard(c) {
    const k = c.check || {};
    const tag = k.source === "document" ? `<span class="tag doc">document</span>` : `<span class="tag interp">app interpretation</span>`;
    const cues = c.cues_if_not_met || [];
    return `<article class="rule checked" data-search="${esc(searchText(c))}">
      <div class="rule-head"><span class="st-ic">${icon("check")}</span>
        <div><h3>${esc(c.title)}</h3><span class="rule-id">${esc(c.id)}</span></div></div>
      <p class="rule-req">${esc(c.requirement)}</p>
      <div class="rule-pages">${pageChips(c.sources || [])}</div>
      <div class="rule-target">
        <div class="kv"><span class="kv-l">Target</span><b>${esc(fmtTarget(k.target, k.unit))}</b>${tag}</div>
        <div class="kv"><span class="kv-l">Margin</span><b>± ${esc(k.uncertainty)} ${esc(shortUnit(k.unit))}</b><span class="kv-note">closer than this to a limit = “can't assess”</span></div>
        <div class="kv"><span class="kv-l">Checked</span><b>${esc(PHASE[c.phase] || c.phase || "")}</b></div>
      </div>
      ${k.note ? `<details><summary>Where the number comes from</summary><p>${esc(k.note)}</p></details>` : ""}
      ${cues.length ? `<details><summary>Coaching tips used when it's not met (${cues.length})</summary><ul class="cue-list">${
        cues.map((q) => `<li>${esc(q.text)} <span class="page-ref">${icon("book")}p. ${esc(q.page)}</span></li>`).join("")}</ul></details>` : ""}
    </article>`;
  }
  function naCard(c) {
    return `<article class="rule na" data-search="${esc(searchText(c))}">
      <div class="rule-head"><span class="st-ic">${icon("camera")}</span>
        <div><h3>${esc(c.title)}</h3><span class="rule-id">${esc(c.id)}</span></div></div>
      <p class="rule-req">${esc(c.requirement)}</p>
      <div class="rule-why">${icon("info")}<span><b>Why it isn't checked:</b> ${esc(c.reason)}</span></div>
      <div class="rule-pages">${pageChips(c.sources || [])}</div>
    </article>`;
  }
  function settingsHtml(rb) {
    return Object.entries(SETTINGS).map(([key, [title, blurb, labels]]) => {
      const vals = rb[key] || {};
      const rows = Object.entries(vals).map(([k, v]) => {
        const [lbl, hint, unit] = labels[k] || [k, "", ""];
        const shown = unit === "%" ? `${Math.round(v * 100)}%` : unit === "×" ? `${v}×` : unit ? `${v} ${unit}` : String(v);
        return `<tr><td><b>${esc(lbl)}</b><br><span class="muted small">${esc(hint)}</span> <code>${esc(k)}</code></td><td>${esc(shown)}</td></tr>`;
      }).join("");
      return `<section class="settings-group"><h3>${esc(title)}</h3><p>${esc(blurb)}</p><table class="settings-table">${rows}</table></section>`;
    }).join("");
  }
  function renderRulebook(rb) {
    const src = rb.source || {};
    $("#rb-source").textContent = [src.title, src.page_numbering, `rulebook v${rb.version}`].filter(Boolean).join(" · ");
    const checked = (rb.criteria || []).filter((c) => c.assessable), na = (rb.criteria || []).filter((c) => !c.assessable);
    $("#rb-n-checked").textContent = checked.length;
    $("#rb-n-na").textContent = na.length;
    $("#rb-checked").innerHTML = checked.map(ruleCard).join("");
    $("#rb-na").innerHTML = na.map(naCard).join("");
    $("#rb-settings").innerHTML = settingsHtml(rb);
    applyRbSearch();
  }
  let rbTab = "checked";
  function selectRbTab(name) {
    rbTab = name;
    $$(".rb-tabs [data-rb]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.rb === name)));
    $$("[data-rb-panel]").forEach((p) => (p.hidden = p.dataset.rbPanel !== name));
    $(".rb-search").hidden = name === "settings" || name === "raw";
    applyRbSearch();
  }
  function applyRbSearch() {
    const q = $("#rb-search").value.trim().toLowerCase();
    let shown = 0, total = 0;
    $$(`[data-rb-panel="${rbTab}"] .rule`).forEach((card) => {
      total++;
      const hit = !q || q.split(/\s+/).every((w) => card.dataset.search.includes(w));
      card.hidden = !hit; if (hit) shown++;
    });
    $("#rb-empty").hidden = !(total && !shown);
  }
  async function openRulebook() {
    if (!dlg.open) dlg.showModal();
    selectRbTab(rbTab);
    try {
      const [rb, raw] = await Promise.all([fetch("api/skill.json").then((r) => r.json()), fetch("api/skill").then((r) => r.text())]);
      renderRulebook(rb);
      $("#rulebook-text").textContent = raw;
    } catch { $("#rb-source").textContent = "Couldn't load the rulebook."; }
  }
  $("#btn-rulebook").addEventListener("click", openRulebook);
  $(".rb-tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-rb]"); if (b) selectRbTab(b.dataset.rb); });
  $("#rb-search").addEventListener("input", applyRbSearch);
  $("#rulebook-close").addEventListener("click", () => dlg.close());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });

  // ------------------------------------------------------------ routing
  function openFromHash() {
    const m = location.hash.match(/job=([a-f0-9]+)/);
    if (m && m[1] !== state.jobId) { beginProgress("Loading analysis"); poll(m[1]); }
  }
  window.addEventListener("hashchange", openFromHash);
  loadConfig().then(openFromHash);
})();
