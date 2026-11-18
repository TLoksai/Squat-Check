# How AI was used

The whole project was built in one Claude Code session (Claude Opus 5.5 as the
coding agent). Claude Code wrote the code and the skill; every result below was
checked against rendered video frames and the reference text during the session.

> Note to self before submitting: re-read this file and rewrite it in my own
> words, adding what I personally checked and changed.

## Delegated to the agent

* Reading the 35-page reference and drafting `criteria.yaml`: requirements in
  plain words, page citations, which criteria a side view can and can't show.
* Writing the pipeline (ffmpeg normalisation, MediaPipe pose, plate tracking,
  rep detection, measurements), the rules engine, the Claude coach with output
  validation, the FastAPI server and the front end.
* Generating difficult test clips and the evidence renderer.

## Inspected and verified

* **Every rule against the reference text**: each criterion cites the page it
  came from; numbers the document doesn't give are marked
  `app_interpretation` and shown that way in the UI.
* **Landmark placement**: rendered the bottom frame of each rep with the hip,
  knee, ankle, heel and toe drawn on, and checked them by eye.
* **Bar path**: compared the bar position over the shoe when standing vs at the
  bottom (standing: over the middle of the shoe; bottom: over the toes), which
  confirmed the "bar drifts forward" finding is real and not a camera artefact.
* **Overlay alignment in the browser**: plate circle on the plate, midfoot
  line through the shoe.
* **Coach grounding**: unit tests prove invented page numbers and unknown
  findings are removed.

## Mistakes caught and corrected

1. **Depth result flipped when the video was mirrored (the main one).** The
   first version called depth a clear "does not meet" on both reps of the
   common sample (hip ~5 cm above the knee). A consistency check, running a
   mirrored copy that should give the same answer, reported the hip **3.5 cm
   below** the knee, so the result depended on which way the lifter faced. The
   pose model's own error at the bottom of a squat was far larger than the
   2 cm band first assumed. Fix: run the pose model on both the normal and the
   mirrored frame, average them, and widen each check's uncertainty band by half
   their disagreement. Depth on the sample is now honestly "cannot assess",
   and original and mirrored videos agree on every pass/fail.
2. **Bar found in only 55% of frames.** The plate is plainly visible
   throughout, so the detector was wrong, not the video. Diagnosis: motion blur
   during the fast part of each rep hides the circle edge, and a rowing
   machine's round fan in the background was a competing circle. Fix: search
   only near the last position at the last size, and fall back to matching the
   plate's appearance on blurred frames. Now 100% measured, with each frame
   labelled detected / tracked / interpolated.
3. **Depth reference was biased.** The first draft estimated the top of the
   kneecap as a fixed offset above the knee joint but compared it with the hip
   *joint*, not the hip crease, which made the check too lenient. Both document
   points sit on the top of the thigh, so the offsets cancel; the app now
   compares joint centres and says so.
4. **Shoulder hidden behind the plate.** The pose model still reported the
   shoulder with 99% "visibility". The app now tests whether the shoulder lies
   inside the plate circle and, if so, measures the torso from hip to bar.
5. **Cropped feet still produced foot-based verdicts, plus a phantom third
   rep.** Foot checks are now "cannot assess" when the feet aren't seen, and
   reps shorter than 0.3 s down or up are rejected as tracking glitches.
6. **The same rep gave different bar results after re-encoding.** Comparing
   the two bar tracks frame by frame showed the circle sometimes snapping to
   the plate's inner ring (radius 118 vs 110 px), moving the centre ~15 px in
   single frames. Measurements that took the maximum over a rep amplified this.
   Fix: lock the plate size in a second tracking pass, and use the 95th
   percentile instead of the maximum. Bar results now agree to within ~1 cm
   across original, mirrored and re-encoded copies.
7. **A dark, blurry, low-resolution clip got confident fails.** The two pose
   passes agreed with each other but were both wrong, so the mirror check
   didn't catch it. Added a quality gate (size, brightness, sharpness) that
   rejects such videos with a plain explanation.
8. **The AI coach crashed on its first live run.** Unit tests and a direct
   API call both worked, but inside the app the coach fell back to rulebook
   tips with a `TypeError`. The measurements still held numpy numbers when the
   coach turned them into JSON for the prompt. Tests had loaded the
   already-converted saved file, so they never hit it. Fix: convert to plain
   numbers before the coach runs, plus a regression test, and log coach errors
   in full instead of swallowing them.
9. **A plastic stick was tracked as a barbell.** On a clip using a PVC pipe
   (no plate), the app reported the bar "found" in 66% of frames: the
   appearance tracker had drifted onto the hands and head. Fix: a minimum plate
   size (from the measured plate-to-leg ratio of real clips), appearance
   matching only for gaps up to 0.5 s, and "no plate found" when the circle
   itself is rarely detected.
10. **The plate hid the hips at the bottom, and the app measured depth at the
    wrong moment.** On a clip with the lifter farther away, the hip point
    jumped *up* exactly at the deepest moment, so one rep was split in two
    and depth was judged at a shallower frame. Close-up crops at the app's
    "bottom" frame first seemed to confirm its "does not meet". Looking at the
    frames around it showed the real bottom was deeper, with the hips behind
    the plate. Fix: when the plate is well tracked, reps and bottoms come from
    the bar's lowest point; if the hip is behind the plate at the bottom,
    depth is "cannot assess".
11. **A walk-out from the rack was counted as part of the first rep**, which
    inflated bar drift to more than a foot length. Fix: a rep starts where
    the descent begins (looking back at most 3 s), and foot positions are
    taken per rep.
12. **A rack crossbar hiding the legs produced fake reps and impossible
    numbers** (a heel "lifted 6.8 foot lengths"). Fix: a rep must actually
    bend the knees, both pose passes must see the same hip drop, impossible
    values count as tracking errors, and reps that couldn't be tracked are
    reported to the user instead of silently dropped.

## Selected prompts

* "Give me what this is in simple, with a picture" → plain-language
  explanation and flow diagrams before building.
* "Let's start building the squat app and push it to GitHub once it's ready."
* "I want to try with the new videos" → four real side-view clips from other
  lifters, which exposed mistakes 9-12: the derived test clips had all come
  from one lifter and one camera.
* The mirrored-video consistency test was the agent's proposal after
  reviewing its own first results. It is the check that found mistake 1.
