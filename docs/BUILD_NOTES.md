# Build note

## Architecture

```
browser (plain HTML/JS)                    server (Python, FastAPI)
───────────────────────                    ────────────────────────────────────────────
upload (XHR, % progress)  ── POST /api/jobs ─►  save file, queue job (1 worker, CPU-bound)
poll status every 0.7 s   ◄─ GET  /api/jobs/id   pipeline.run():
                                                  1 video.normalise   ffmpeg → H.264, 30 fps, ≤1280 px
                                                  2 pose.run_pose     MediaPipe Pose, normal + mirrored pass
                                                  3 quality.measure   size / brightness / sharpness
                                                  4 bar.track_bar     Hough circle on the plate,
                                                                      template match on blurred frames
                                                  5 analysis.analyse  reps, bottoms, measurements
                                                  6 rules.evaluate    criteria.yaml → meets / not / can't
                                                  7 agent.coach       Claude writes explanations + tips
video + canvas overlay    ◄─ GET  /api/jobs/id/video, /result
"re-check" button         ── POST /api/jobs/id/reassess  → steps 5-7 again with the current skill
```

The browser plays the **same normalised file** the server analysed, so overlay
coordinates line up exactly (no rotation or variable-frame-rate drift). The
overlay is drawn per frame on a canvas (`requestVideoFrameCallback`).

### Where decisions are made

| Decision | Made by | Why |
|---|---|---|
| Body points | MediaPipe Pose Landmarker (full model), two passes | Free, CPU-only, good enough on a clear side view |
| Bar position | OpenCV, plate circle + appearance tracking | From the side the bar end *is* a circle; no training needed |
| Reps, bottoms, angles, distances | Deterministic geometry (`analysis.py`) | Reproducible; explainable in an interview |
| Pass / fail / can't assess | `rules.py` reading `criteria.yaml` | The document drives the result; editable without code |
| Explanations, tips, summary | Claude (`claude-opus-5-5`) with the skill as system prompt | Plain-language coaching grounded in the document |

The model never decides a result. Its output is validated: feedback for a
(rep, check) that doesn't exist, or a page number the criterion doesn't cite,
is dropped and replaced with the rulebook text (`agent._validate`, unit-tested).

## The skill

`skill/squat-standard/` holds:

* `criteria.yaml`, the rules: 8 checks assessable from a side view, 9 that
  aren't (each with the reason). Every rule has a paraphrased requirement,
  reference page numbers and sections, the target, **whether the number comes
  from the document or is the app's interpretation**, a measurement
  uncertainty band, and coaching cues with pages.
* `SKILL.md`, the coach's instructions: never change a result, only use the
  reference, label app interpretations, be honest about uncertainty.

The document gives few numbers ("about 45 degrees", "just a little in front of
the toes", "directly over the midfoot"). Where it doesn't give one, the app's
number is marked `app_interpretation` and shown with an "app interpretation"
tag in the UI, never presented as a document requirement.

## Uncertainty handling

A result is "cannot assess" when:

1. a needed point was hidden or out of the picture (e.g. feet cropped), or the
   bar was measured in < 60% of the rep's frames;
2. the value is within the uncertainty band of a limit. The band is
   *rule band* + *half the disagreement between the normal and mirrored pose
   passes* + (bar over midfoot only) *camera-angle parallax + perspective*;
3. one of the two pose passes failed to find the rep.

Whole videos are rejected with an explanation when: no person, not a side
view, too small / dark / blurry, or no complete rep.

## Time spent

* **Build: about 1 hour of wall-clock time**, 2026-11-18 15:29 → 16:25 IST
  (first command → first push), including tests and these notes. Fast because
  the coding agent did the typing; most of the time went on checking results
  against video frames and fixing what that turned up.
* Reading the assignment and planning happened in an earlier session (about
  20 minutes, not counted above).
* Not yet done inside that hour: running the AI coach with a real API key,
  and hosting.

## Costs

* **Build:** Claude Code (subscription). The only download costs are the pose
  model (~9 MB) and the sample video.
* **Per video, compute:** about 25-30 s of CPU for an 8 s clip on a laptop
  (pose ~18 s for two passes, bar ~2 s, ffmpeg ~5 s). Scales roughly linearly
  with video length.
* **Per video, AI coach (estimate):** system prompt ≈ 5.5k tokens (skill,
  cached), results ≈ 4k tokens, output ≈ 2-4k tokens → roughly **$0.08-0.12 per
  video** on Claude Opus 5.5 ($4 / $20 per M tokens). Actual token usage is
  shown in the report ("How this was measured") once an API key is set. Haiku
  would cut this ~40×; I kept the most capable model because the coach's job
  is careful grounding, and the cost per video is already small.

## Key trade-offs

* **Server-side processing vs in-browser.** Server: one Python pipeline with
  OpenCV for the bar, easy to test from the CLI. Cost: needs a CPU host; videos
  leave the user's device (stated in the UI; delete button provided).
* **Two pose passes (2× pose time) for honest uncertainty.** Without it, depth
  on the common sample flipped between "5 cm above" and "3.5 cm below" the
  knee depending on mirroring (see `AI_USAGE.md`).
* **Joint centres for depth.** The document compares the hip crease with the
  top of the kneecap; both sit about equally above the joint centres the model
  tracks, so the app compares joint centres and widens the band. This is an
  assumption, stated in `criteria.yaml`.
* **Plate centre as the bar.** It's directly measurable. The true bar-on-back
  point lies further along the bar axis, so absolute bar positions carry
  perspective error; bar *path* and *drift* are much less affected.
* **Plain HTML/JS front end.** No build step, easy to read in an interview.

## Known limitations

* **Depth is often "cannot assess"** near parallel: the two pose passes
  disagree by up to ~±6 cm at the bottom on the common sample. A heavier pose
  model or a thigh-contour measurement would help.
* **Bar over midfoot** depends on the camera being square to the lifter and
  on perspective (plate nearer the camera than the foot). Both are estimated
  and widen the band; neither is calibrated.
* **Torso line** uses hip → bar when the shoulder is hidden behind the plate
  (as in the common sample). This is reasonable for a low-bar squat but not
  identical to the document's "plane of the torso".
* Only the **near-side plate**, one lifter, a static camera and a level horizon
  are supported. A tilted phone shifts every angle.
* Thresholds marked `app_interpretation` are judgement calls; the document
  doesn't give tolerances.
* The criteria a side view cannot assess (knees out, stance, grip, gaze, back
  rounding, bar placement, unracking, rack height, breathing) are listed, not
  measured.
* Not deployed; runs locally. One analysis at a time.
* Tested on the common sample plus four clips derived from it (mirrored, feet
  cropped, dark/blurry/small, no rep). A second real lifter would be a better
  test; see `EVIDENCE.md`.

## Mocked components and manual steps

None in the upload → analysis → annotation → feedback flow. The "Try the
common sample" button only copies the sample file into a new job on the server;
it runs the full pipeline. Without `ANTHROPIC_API_KEY`, the coach step is
replaced by the rulebook's own cues and the UI says so.
