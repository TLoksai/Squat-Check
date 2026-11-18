/* Squat Check front end: upload -> progress -> annotated video + findings.
   No framework: three views, a polling loop, and a canvas drawn over the video. */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const RESULT_LABEL = { meets: "Meets", does_not_meet: "Does not meet", cannot_assess: "Can't assess" };
  const RESULT_ICON = { meets: "✓", does_not_meet: "✗", cannot_assess: "?" };
  const STAGE_ORDER = ["upload", "prepare", "pose", "bar", "measure", "coach"];

  let config = null;
  let state = { jobId: null, result: null, frames: [], fps: 30, n: 0, size: [720, 1280], focus: null, selected: null };

  // ------------------------------------------------------------ views
  function show(view) {
    for (const v of ["upload", "progress", "result"]) $(`#view-${v}`).hidden = v !== view;
    window.scrollTo({ top: 0 });
  }

  function restart() {
    const v = $("#video");
    v.pause(); v.removeAttribute("src"); v.load();
    state = { ...state, jobId: null, result: null, frames: [], focus: null, selected: null };
    history.replaceState(null, "", location.pathname);
    $("#file-input").value = "";
    $("#upload-error").hidden = true;
    show("upload");
  }
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-action=restart]")) restart();
  });

  // ------------------------------------------------------------ config + upload screen
  async function loadConfig() {
    try {
      config = await (await fetch("api/config")).json();
      const L = config.limits;
      $("#limits-text").textContent =
        `${L.formats.map((f) => f.toUpperCase()).join(", ")} · up to ${L.max_duration_s} s · max ${L.max_upload_mb} MB`;
      $("#list-assessable").innerHTML = config.skill.assessable.map((c) => `<li>${esc(c.title)}</li>`).join("");
      $("#list-not-assessable").innerHTML = config.skill.not_assessable.map((c) => `<li>${esc(c.title)}</li>`).join("");
      $("#btn-sample").hidden = !config.sample_available;
    } catch {
      $("#limits-text").textContent = "MP4 or MOV, up to 60 seconds";
    }
  }

  function uploadError(msg) {
    const el = $("#upload-error");
    el.textContent = msg; el.hidden = false;
  }

  function videoDuration(file) {
    return new Promise((resolve) => {
      const v = document.createElement("video");
      const url = URL.createObjectURL(file);
      const done = (d) => { URL.revokeObjectURL(url); resolve(d); };
      v.preload = "metadata";
      v.onloadedmetadata = () => done(v.duration);
      v.onerror = () => done(null); // browser can't read it; the server will try
      setTimeout(() => done(null), 4000);
      v.src = url;
    });
  }

  async function handleFile(file) {
    $("#upload-error").hidden = true;
    if (!file) return;
    const L = config?.limits;
    const ext = file.name.split(".").pop().toLowerCase();
    if (L && !L.formats.includes(ext)) return uploadError(`“.${ext}” files aren't supported. Use ${L.formats.join(", ")}.`);
    if (L && file.size > L.max_upload_mb * 1024 * 1024) return uploadError(`This file is ${(file.size / 1048576).toFixed(0)} MB. The limit is ${L.max_upload_mb} MB.`);
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
  function setProgress(stage, frac, label) {
    const idx = STAGE_ORDER.indexOf(stage);
    document.querySelectorAll("#stages li").forEach((li) => {
      const i = STAGE_ORDER.indexOf(li.dataset.stage);
      li.classList.toggle("done", i < idx || stage === "done");
      li.classList.toggle("active", i === idx && stage !== "done");
    });
    const pct = Math.round(Math.max(0, Math.min(1, frac)) * 100);
    $("#progress-bar").style.width = pct + "%";
    $("#progress-pct").textContent = `${pct}% · ${label || ""}`;
  }

  function progressError(msg) {
    $("#progress-error-text").textContent = msg;
    $("#progress-error").hidden = false;
    document.querySelectorAll("#stages li.active").forEach((li) => li.classList.remove("active"));
  }

  function beginProgress(name) {
    $("#progress-error").hidden = true;
    $("#progress-file").textContent = name;
    show("progress");
  }

  function startUpload(file) {
    beginProgress(file.name);
    setProgress("upload", 0, "Uploading");
    const form = new FormData();
    form.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "api/jobs");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) setProgress("upload", (e.loaded / e.total) * 0.1, `Uploading ${Math.round((e.loaded / e.total) * 100)}%`);
    };
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText); } catch {}
      if (xhr.status >= 200 && xhr.status < 300 && body.id) poll(body.id);
      else progressError(body.detail || `Upload failed (HTTP ${xhr.status}).`);
    };
    xhr.onerror = () => progressError("The upload failed. Check your connection and try again.");
    xhr.send(form);
  }

  async function startSample() {
    beginProgress("Common sample video (from the assignment)");
    setProgress("upload", 0.1, "Using the sample video");
    try {
      const r = await fetch("api/jobs/sample", { method: "POST" });
      const body = await r.json();
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
      } catch {
        await sleep(1500); continue; // brief network blip: keep trying
      }
      if (st.status === "failed") return progressError(st.error || "Analysis failed.");
      if (st.status === "done") { setProgress("done", 1, "Done"); return loadResult(id); }
      // server progress covers 10%..100% of the bar (upload took 0..10%)
      setProgress(st.status === "queued" ? "prepare" : st.stage, 0.1 + 0.9 * (st.progress || 0),
        st.status === "queued" ? "Waiting for the previous video to finish" : st.message);
      await sleep(700);
    }
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ------------------------------------------------------------ results
  async function loadResult(id) {
    const [res, st] = await Promise.all([
      fetch(`api/jobs/${id}/result`).then((r) => (r.ok ? r.json() : Promise.reject(r))),
      fetch(`api/jobs/${id}`).then((r) => r.json()).catch(() => ({})),
    ]).catch(() => [null, null]);
    if (!res) return progressError("We couldn't load the result. Please try again.");
    state.result = res;
    state.jobId = id;
    renderResult(res, st);
    show("result");
  }

  function fmtTime(s) {
    const m = Math.floor(s / 60);
    return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
  }

  function pageList(sources) {
    const pages = [...new Set(sources.map((s) => s.page))].sort((a, b) => a - b);
    const title = sources.map((s) => `p.${s.page}: ${s.section}`).join("\n");
    return `<span class="page-ref" title="${esc(title)}">Ref. p. ${pages.join(", ")}</span>`;
  }

  function renderResult(res, st) {
    const a = res.analysis, ev = res.evaluation, fb = res.feedback || {};
    state.frames = a.frames || [];
    state.fps = a.fps || 30;
    state.n = a.frame_count || state.frames.length;
    state.size = a.frame_size || [res.video.width, res.video.height];
    state.focus = null; state.selected = null;
    const fbMap = new Map((fb.findings_feedback || []).map((x) => [`${x.rep}|${x.criterion_id}`, x]));
    state.fbMap = fbMap;

    const reps = a.reps || [];
    $("#result-meta").textContent = [
      st?.filename, `${res.video.duration_s.toFixed(1)} s video`,
      a.status === "ok" ? `${reps.length} rep${reps.length === 1 ? "" : "s"} found` : null,
      `analysed in ${Math.round(res.timings.total_s)} s`,
    ].filter(Boolean).join(" · ");

    const ok = a.status === "ok";
    $("#unsuitable").hidden = ok;
    $("#summary-card").hidden = !ok;
    $("#unsuitable-text").textContent = ok ? "" : a.message;

    // summary
    const c = ev.counts || {};
    $("#counts").innerHTML = ["meets", "does_not_meet", "cannot_assess"].map((k) =>
      `<span class="chip ${k}">${RESULT_ICON[k]} ${c[k] || 0} ${RESULT_LABEL[k].toLowerCase()}</span>`).join("");
    const aiName = { claude: "Claude", gemini: "Gemini" }[fb.source];
    $("#coach-source").textContent = aiName
      ? `Feedback written by the AI coach (${aiName}) from the rulebook`
      : (fb.note || "Feedback taken directly from the rulebook");
    $("#summary-text").textContent = fb.overall_summary || "";
    $("#priorities").innerHTML = (fb.top_priorities || []).map((p, i) => `
      <div class="prio"><h3>${i + 1}. ${esc(p.title)}</h3>
        <p>${esc(p.cue)} <span class="page-ref">p. ${esc(p.page)}</span></p>
        <p class="muted small">${esc(p.why)}</p></div>`).join("");

    // player
    const [w, h] = state.size;
    const player = $("#player");
    player.style.setProperty("--ar", `${w} / ${h}`);
    player.style.setProperty("--ar-num", String(w / h));
    const v = $("#video");
    v.src = `api/jobs/${state.jobId}/video`;
    v.playbackRate = parseFloat($("#speed").value);
    $("#scrub").max = Math.max(0, state.n - 1);
    $("#scrub").value = 0;

    // rep strip
    $("#rep-strip").innerHTML = reps.map((r) => {
      const left = (r.start_frame / state.n) * 100, width = ((r.end_frame - r.start_frame) / state.n) * 100;
      const tick = ((r.bottom_frame - r.start_frame) / Math.max(1, r.end_frame - r.start_frame)) * 100;
      return `<button class="rep-seg" type="button" data-frame="${r.bottom_frame}" style="left:${left}%;width:${width}%"
        title="Rep ${r.index}: bottom at ${fmtTime(r.bottom_s)}">Rep ${r.index}<span class="bottom-tick" style="left:${tick}%"></span></button>`;
    }).join("");

    renderMatrix(ev, reps);

    $("#na-list").innerHTML = (ev.not_assessable || []).map((n) =>
      `<li><b>${esc(n.title)}</b>${esc(n.reason)} ${pageList(n.sources)}</li>`).join("");

    const how = [];
    if (ok) {
      how.push(`Camera sees the lifter's <b>${esc(a.side)}</b> side; the lifter faces <b>${esc(a.facing)}</b>.`);
      how.push(`Body points: MediaPipe Pose, run twice per frame (as recorded and mirrored) and averaged. Where the two runs disagree, the check's uncertainty margin grows by half the difference. Points marked hollow were hidden or derived.`);
      if (a.quality) how.push(`Video quality around the lifter: leg ${Math.round(a.quality.leg_px)} px long, brightness ${a.quality.brightness}, sharpness ${a.quality.sharpness}.`);
      how.push(`Bar: the weight plate is found in each frame (circle detection, then appearance matching for blurred frames). Bar measured in ${Math.round(a.bar_detected_ratio * 100)}% of frames.`);
      how.push(`Torso line for the back angle: ${esc(a.torso_line)}.`);
      how.push(`Feet don't move during a squat, so the heel, toe and midfoot are averaged over the whole video.`);
      if (a.scale?.px_per_cm) how.push(`Centimetres are approximate: ${esc(a.scale.how)}.`);
      how.push(`Camera angle check: left and right hips appear ${(a.camera.hip_separation).toFixed(2)} thigh lengths apart (0 = perfect side view). This widens the uncertainty of the bar-over-midfoot check by ${(a.camera.parallax_foot_lengths).toFixed(2)} foot lengths.`);
    }
    for (const i of a.issues || []) how.push(`⚠ ${esc(i.message)}`);
    how.push(`Results: decided by fixed geometry rules from <code>criteria.yaml</code> (rulebook v${esc(res.skill.version)}). ${aiName ? `The AI coach (${esc(fb.model || aiName)}) only writes the explanations and tips; it cannot change a result.` : "The AI coach did not run, so tips come straight from the rulebook."}`);
    const t = res.timings;
    how.push(`Timing: prepare ${t.prepare_s}s, body ${t.pose_s}s, bar ${t.bar_s}s, coach ${t.coach_s}s.`);
    if (fb.usage) {
      // list price shown only for the default Claude model; other models: tokens only
      const cost = fb.source === "claude" && String(fb.model || "").startsWith("claude-opus-5-5")
        ? `, about $${((fb.usage.input_tokens * 4 + fb.usage.output_tokens * 20) / 1e6).toFixed(3)} at list price` : "";
      how.push(`AI coach usage: ${fb.usage.input_tokens} input tokens (${fb.usage.cache_read_input_tokens} from cache), ${fb.usage.output_tokens} output tokens${cost}.`);
    }
    if (fb.validation?.dropped_items) how.push(`The coach's output had ${fb.validation.dropped_items} item(s) that weren't grounded in the rulebook; they were replaced with rulebook text.`);
    $("#how-body").innerHTML = `<ul>${how.map((x) => `<li>${x}</li>`).join("")}</ul>`;
    $("#detail").hidden = true;

    resizeCanvas();
    if (ok) {
      // open the first failed check, else the first finding, so evidence is visible right away
      const first = ev.findings.find((f) => f.result === "does_not_meet") || ev.findings[0];
      if (first) selectFinding(first, false);
    }
  }

  function renderMatrix(ev, reps) {
    const crit = [];
    for (const f of ev.findings) if (!crit.find((c) => c.id === f.criterion_id)) crit.push({ id: f.criterion_id, title: f.title, sources: f.sources });
    const byKey = new Map(ev.findings.map((f) => [`${f.rep}|${f.criterion_id}`, f]));
    state.byKey = byKey;
    const head = `<tr><th>Check</th>${reps.map((r) => `<th>Rep ${r.index}</th>`).join("")}</tr>`;
    const rows = crit.map((c) => `<tr><td class="crit"><div class="crit-title">${esc(c.title)}</div>${pageList(c.sources)}</td>${
      reps.map((r) => {
        const f = byKey.get(`${r.index}|${c.id}`);
        return `<td>${f ? `<button class="badge ${f.result}" type="button" data-key="${r.index}|${c.id}">${RESULT_ICON[f.result]} ${RESULT_LABEL[f.result]}</button>` : ""}</td>`;
      }).join("")}</tr>`).join("");
    $("#matrix").innerHTML = `<thead>${head}</thead><tbody>${rows}</tbody>`;
  }

  $("#matrix").addEventListener("click", (e) => {
    const b = e.target.closest(".badge");
    if (b) selectFinding(state.byKey.get(b.dataset.key), true);
  });

  function selectFinding(f, scroll) {
    state.selected = `${f.rep}|${f.criterion_id}`;
    state.focus = f.criterion_id;
    document.querySelectorAll(".badge").forEach((b) => b.classList.toggle("selected", b.dataset.key === state.selected));
    const fb = state.fbMap.get(state.selected) || {};
    const m = f.measurement || {};
    const val = m.value == null ? "not measured" : `${Number(m.value).toFixed(2)} ${esc(m.unit.split(" (")[0])}`
      + (m.extra?.approx_cm != null ? ` (≈ ${m.extra.approx_cm} cm)` : "");
    const srcTag = f.threshold_source === "document"
      ? `<span class="tag doc">from the document</span>`
      : `<span class="tag interp">app interpretation</span>`;
    const d = $("#detail");
    d.innerHTML = `
      <div class="d-head">
        <div><h3>${esc(f.title)}: rep ${f.rep}</h3>${pageList(f.sources)}</div>
        <span class="badge ${f.result}">${RESULT_ICON[f.result]} ${RESULT_LABEL[f.result]}</span>
      </div>
      <dl>
        <dt>The standard</dt><dd>${esc(f.requirement)}</dd>
        <dt>What we saw</dt><dd>${esc(fb.explanation || f.explanation)}</dd>
        <dt>Measurement</dt><dd>${val} at <button class="linkbtn" type="button" data-seek="${f.frame}">${fmtTime(f.time_s)} (frame ${f.frame})</button>${m.observed === false ? " · <i>inputs hidden or estimated</i>" : ""}</dd>
        <dt>Target</dt><dd>${esc(f.target_text)} ${srcTag}
          ${f.threshold_note ? `<details><summary class="small">Where this number comes from</summary><p class="small muted">${esc(f.threshold_note)}</p></details>` : ""}</dd>
        <dt>Uncertainty</dt><dd>Values within ±${f.uncertainty_band} of a limit are reported as “can't assess”.${fb.uncertainty ? " " + esc(fb.uncertainty) : ""}</dd>
        <dt>Calculation</dt><dd class="small muted">${esc(f.explanation)}</dd>
      </dl>
      ${fb.feedback ? `<div class="feedback-box"><p><b>Coach:</b> ${esc(fb.feedback)}</p></div>` : ""}`;
    d.hidden = false;
    seekFrame(f.frame);
    if (scroll && window.innerWidth < 900) $("#player").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  $("#detail").addEventListener("click", (e) => {
    const b = e.target.closest("[data-seek]");
    if (b) seekFrame(+b.dataset.seek);
  });
  $("#rep-strip").addEventListener("click", (e) => {
    const b = e.target.closest(".rep-seg");
    if (b) seekFrame(+b.dataset.frame);
  });

  // ------------------------------------------------------------ player + overlay
  const video = $("#video"), canvas = $("#overlay"), ctx = canvas.getContext("2d");
  const frameOf = (t) => Math.max(0, Math.min(state.n - 1, Math.floor(t * state.fps + 1e-3)));
  function seekFrame(k) {
    video.pause();
    video.currentTime = (k + 0.5) / state.fps;
    draw(k);
  }
  $("#btn-play").addEventListener("click", () => (video.paused ? video.play() : video.pause()));
  video.addEventListener("play", () => { $("#btn-play").innerHTML = "&#10074;&#10074;"; $("#btn-play").ariaLabel = "Pause"; });
  video.addEventListener("pause", () => { $("#btn-play").innerHTML = "&#9654;"; $("#btn-play").ariaLabel = "Play"; });
  $("#btn-prev").addEventListener("click", () => seekFrame(frameOf(video.currentTime) - 1));
  $("#btn-next").addEventListener("click", () => seekFrame(frameOf(video.currentTime) + 1));
  $("#scrub").addEventListener("input", (e) => seekFrame(+e.target.value));
  $("#speed").addEventListener("change", (e) => (video.playbackRate = parseFloat(e.target.value)));
  ["lay-body", "lay-bar", "lay-measure"].forEach((id) => $(`#${id}`).addEventListener("change", () => draw(frameOf(video.currentTime))));
  ["seeked", "loadeddata", "pause", "timeupdate"].forEach((ev) => video.addEventListener(ev, () => draw(frameOf(video.currentTime))));
  if ("requestVideoFrameCallback" in HTMLVideoElement.prototype) {
    const onFrame = (_now, meta) => { draw(frameOf(meta.mediaTime)); video.requestVideoFrameCallback(onFrame); };
    video.requestVideoFrameCallback(onFrame);
  } else {
    const loop = () => { if (!video.paused) draw(frameOf(video.currentTime)); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  }

  function resizeCanvas() {
    const r = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(r.width * dpr));
    canvas.height = Math.max(1, Math.round(r.height * dpr));
    draw(frameOf(video.currentTime || 0));
  }
  window.addEventListener("resize", resizeCanvas);
  new ResizeObserver(resizeCanvas).observe($("#player"));

  const C = { body: "#ffffff", joint: "#f59e0b", bar: "#a855f7", mid: "#22d3ee", good: "#4ade80", bad: "#fb923c", torso: "#facc15" };

  function repAt(t) {
    return (state.result?.analysis.reps || []).find((r) => t >= r.start_frame - 2 && t <= r.end_frame + 2);
  }

  function draw(t) {
    const W = canvas.width, H = canvas.height;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const F = state.frames[t];
    $("#scrub").value = t;
    $("#time").textContent = fmtTime(t / state.fps);
    if (!F) { $("#frame-tag").textContent = ""; return; }
    const s = W / state.size[0];
    const u = (window.devicePixelRatio || 1) / s; // one CSS pixel, in video pixels
    ctx.setTransform(s, 0, 0, s, 0, 0);
    const a = state.result.analysis;
    const rep = repAt(t);
    const atBottom = rep && Math.abs(t - rep.bottom_frame) <= 3;
    $("#frame-tag").textContent = `${fmtTime(t / state.fps)} · frame ${t}${rep ? ` · rep ${rep.index}` : ""}${atBottom ? " · bottom" : ""}`;
    const focus = state.focus;

    const line = (p, q, color, w, dash) => {
      if (!p || !q) return;
      ctx.beginPath(); ctx.setLineDash(dash ? dash.map((d) => d * u) : []);
      ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]);
      ctx.strokeStyle = "rgba(0,0,0,.55)"; ctx.lineWidth = (w + 2) * u; ctx.stroke();
      ctx.strokeStyle = color; ctx.lineWidth = w * u; ctx.stroke();
      ctx.setLineDash([]);
    };
    const dot = (p, est, color = C.joint, r = 5) => {
      if (!p) return;
      ctx.beginPath(); ctx.arc(p[0], p[1], r * u, 0, Math.PI * 2);
      ctx.lineWidth = 2.5 * u; ctx.strokeStyle = color;
      if (est) { ctx.fillStyle = "rgba(0,0,0,.35)"; ctx.fill(); ctx.stroke(); }
      else { ctx.fillStyle = color; ctx.fill(); ctx.strokeStyle = "rgba(0,0,0,.6)"; ctx.lineWidth = 1.2 * u; ctx.stroke(); }
    };
    const label = (text, x, y, color = "#fff", bg = "rgba(0,0,0,.7)") => {
      ctx.font = `600 ${13 * u}px system-ui, sans-serif`;
      const w = ctx.measureText(text).width + 10 * u, h = 20 * u;
      const bx = Math.min(Math.max(2 * u, x), state.size[0] - w - 2 * u), by = Math.min(Math.max(2 * u, y - h / 2), state.size[1] - h - 2 * u);
      ctx.fillStyle = bg; ctx.beginPath(); ctx.roundRect(bx, by, w, h, 5 * u); ctx.fill();
      ctx.fillStyle = color; ctx.textBaseline = "middle"; ctx.fillText(text, bx + 5 * u, by + h / 2);
    };

    const feet = a.feet;
    const feetSeen = feet.seen !== false;
    const mid = feet.midfoot, heel = feet.heel, toe = feet.toe;

    // --- measurements (under the body so joints stay readable)
    if ($("#lay-measure").checked) {
      // midfoot vertical line (only when the feet were actually seen)
      if (feetSeen) {
        const topY = F.bar ? Math.min(F.bar[1], mid[1]) - 60 : mid[1] - state.size[1] * 0.8;
        line([mid[0], mid[1]], [mid[0], Math.max(0, topY)], C.mid, focus === "bar_over_midfoot" ? 3 : 2, [6, 5]);
        label("midfoot", mid[0] + 6 * u, mid[1] - 14 * u, "#0b2530", "rgba(34,211,238,.9)");
      }
      // Only the selected check gets its measurement drawn, so labels never pile up.
      // depth: horizontal line through the knee, from knee to past the hip
      if (F.knee_s && F.hip_s && (focus === "depth" || (!focus && atBottom))) {
        const below = F.hip_s[1] > F.knee_s[1];
        const x0 = F.knee_s[0], x1 = F.hip_s[0] + (F.hip_s[0] - F.knee_s[0]) * 0.35;
        line([x0, F.knee_s[1]], [x1, F.knee_s[1]], below ? C.good : C.bad, 3, [5, 4]);
        const m = rep && state.byKey.get(`${rep.index}|depth`)?.measurement;
        const cm = atBottom ? m?.extra?.approx_cm : null;
        label(`hip ${below ? "below" : "above"} knee${cm != null ? ` (${cm} cm)` : ""}`, Math.min(x0, x1), F.knee_s[1] + 22 * u,
          "#111", below ? "rgba(74,222,128,.92)" : "rgba(251,146,60,.92)");
      }
      // back angle / hip drive: torso line + horizontal reference at the hip
      if (F.hip_s && F.torso_top && (focus === "back_angle_bottom" || focus === "hip_drive")) {
        line(F.hip_s, F.torso_top, C.torso, 3);
        const dir = Math.sign(F.torso_top[0] - F.hip_s[0]) || 1;
        line(F.hip_s, [F.hip_s[0] + dir * 90 * u, F.hip_s[1]], C.torso, 1.5, [4, 4]);
        const mx = (F.hip_s[0] + F.torso_top[0]) / 2, my = (F.hip_s[1] + F.torso_top[1]) / 2;
        label(`back ${Math.round(F.back_angle)}° from floor`, mx - dir * 120 * u, my, "#111", "rgba(250,204,21,.92)");
      }
      // knee vs toe
      if (focus === "knee_position_bottom" && F.knee_s) {
        line([toe[0], toe[1]], [toe[0], F.knee_s[1] - 30 * u], C.mid, 2, [3, 4]);
        line([F.knee_s[0], F.knee_s[1]], [toe[0], F.knee_s[1]], C.mid, 2);
        label("knee vs toe", Math.min(F.knee_s[0], toe[0]), F.knee_s[1] - 22 * u, "#0b2530", "rgba(34,211,238,.92)");
      }
      // heels
      if (focus === "heels_down") {
        dot(heel, false, C.mid, 8);
        label("heel", heel[0] + 10 * u, heel[1] - 18 * u, "#0b2530", "rgba(34,211,238,.92)");
      }
      // pause at the bottom
      if (focus === "no_pause_bottom" && rep && F.hip_s) {
        const m = state.byKey.get(`${rep.index}|no_pause_bottom`)?.measurement;
        if (m?.value != null) label(`time at the bottom: ${Number(m.value).toFixed(2)} s`, F.hip_s[0] - 60 * u, F.hip_s[1] - 30 * u);
      }
    }

    // --- body
    if ($("#lay-body").checked) {
      const torsoTop = F.shoulder;
      line(torsoTop, F.hip, C.body, 3);
      line(F.hip, F.knee, C.body, 3);
      line(F.knee, F.ankle, C.body, 3);
      dot(F.shoulder, F.shoulder_est);
      dot(F.hip, F.hip_est);
      dot(F.knee, F.knee_est);
      dot(F.ankle, F.ankle_est);
      if (feetSeen) {
        line(F.ankle, heel, C.body, 3);
        line(heel, toe, C.body, 3);
        dot(heel, true, C.joint, 4);
        dot(toe, true, C.joint, 4);
        dot(mid, true, C.mid, 4);
      }
    }

    // --- bar path for the current rep (or everything so far, outside reps)
    if ($("#lay-bar").checked) {
      const start = rep ? rep.start_frame : 0;
      ctx.lineCap = "round";
      for (let k = Math.max(start, 1); k <= t; k++) {
        const p = state.frames[k - 1]?.bar, q = state.frames[k]?.bar;
        if (!p || !q) continue;
        const interp = state.frames[k].bar_status === "interpolated";
        ctx.beginPath(); ctx.setLineDash(interp ? [4 * u, 4 * u] : []);
        ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]);
        ctx.strokeStyle = C.bar; ctx.lineWidth = (focus === "bar_path_vertical" || focus === "bar_over_midfoot" ? 5 : 4) * u; ctx.stroke();
      }
      ctx.setLineDash([]);
      if (F.bar) {
        if (F.bar_r) {
          ctx.beginPath(); ctx.arc(F.bar[0], F.bar[1], F.bar_r, 0, Math.PI * 2);
          ctx.strokeStyle = "rgba(168,85,247,.85)"; ctx.lineWidth = 2 * u; ctx.stroke();
        }
        dot(F.bar, F.bar_status === "interpolated", C.bar, 6);
        if ($("#lay-measure").checked && feetSeen && (focus === "bar_over_midfoot" || focus === "bar_path_vertical")) {
          line([F.bar[0], F.bar[1]], [mid[0], F.bar[1]], C.mid, 2);
          const cmPerPx = a.scale?.px_per_cm ? 1 / a.scale.px_per_cm : null;
          const dx = Math.abs(F.bar[0] - mid[0]);
          label(`bar ${cmPerPx ? Math.round(dx * cmPerPx) + " cm" : Math.round(dx) + " px"} ${Math.sign((F.bar[0] - mid[0]) * (a.facing === "right" ? 1 : -1)) > 0 ? "ahead of" : "behind"} midfoot`,
            Math.min(F.bar[0], mid[0]), F.bar[1] + F.bar_r * 0.0 + 26 * u, "#0b2530", "rgba(34,211,238,.92)");
        }
      } else {
        label("bar not tracked in this frame", 8 * u, 40 * u);
      }
    }
  }

  // ------------------------------------------------------------ misc
  $("#btn-recheck").addEventListener("click", async () => {
    const btn = $("#btn-recheck");
    btn.disabled = true; btn.textContent = "Re-checking…";
    try {
      const r = await fetch(`api/jobs/${state.jobId}/reassess`, { method: "POST" });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || "Re-check failed");
      const st = await fetch(`api/jobs/${state.jobId}`).then((x) => x.json()).catch(() => ({}));
      state.result = body;
      renderResult(body, st);
      btn.textContent = `Re-checked (rulebook loaded ${body.skill.loaded_at.slice(11)})`;
    } catch (e) {
      alert(e.message);
      btn.textContent = "Re-check with current rulebook";
    } finally {
      btn.disabled = false;
      setTimeout(() => (btn.textContent = "Re-check with current rulebook"), 6000);
    }
  });

  $("#btn-delete").addEventListener("click", async () => {
    if (!state.jobId || !confirm("Delete this video and its analysis from the server? This can't be undone.")) return;
    await fetch(`api/jobs/${state.jobId}`, { method: "DELETE" }).catch(() => {});
    restart();
  });

  const dlg = $("#rulebook-dialog");
  $("#btn-rulebook").addEventListener("click", async () => {
    dlg.showModal();
    try { $("#rulebook-text").textContent = await (await fetch("api/skill")).text(); }
    catch { $("#rulebook-text").textContent = "Couldn't load the rulebook."; }
  });
  $("#rulebook-close").addEventListener("click", () => dlg.close());

  // resume a job from the URL (refresh-safe), and follow #job= links
  function openFromHash() {
    const m = location.hash.match(/job=([a-f0-9]+)/);
    if (m && m[1] !== state.jobId) { beginProgress("Loading analysis"); poll(m[1]); }
  }
  window.addEventListener("hashchange", openFromHash);
  loadConfig().then(openFromHash);
})();
