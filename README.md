# Squat Check

Upload a side-view video of a barbell back squat. Squat Check tracks the hips,
knees, ankles and the bar, finds each rep and its bottom position, and checks
every rep against **the supplied reference document** (the low-bar squat
chapter, reference pages 1-35). Every result cites its page, says whether it
**meets**, **does not meet**, or **cannot be assessed**, and links to the exact
video frame that was measured.

* **Rulebook (agent skill):** [`skill/squat-standard/`](skill/squat-standard/)
* **Build note** (architecture, costs, trade-offs, limits): [`docs/BUILD_NOTES.md`](docs/BUILD_NOTES.md)
* **How AI was used** (including a mistake caught): [`docs/AI_USAGE.md`](docs/AI_USAGE.md)
* **Evidence** (common sample + difficult clips): [`docs/EVIDENCE.md`](docs/EVIDENCE.md)

## What it does

```
upload ─► normalise (ffmpeg) ─► body points ×2 (MediaPipe, normal + mirrored)
       ─► bar (plate circle + appearance tracking) ─► quality gate
       ─► reps + measurements ─► rules from criteria.yaml ─► AI coach writes tips
       ─► report: annotated video + rep-by-rep findings with page numbers
```

* The **rules decide the results**, with fixed geometry and the numbers in
  [`criteria.yaml`](skill/squat-standard/criteria.yaml). The **AI coach**
  (Gemini or Claude, whichever key is set) reads the same skill and writes the
  explanations and tips. It cannot change a result, and anything it cites that
  isn't in the skill is dropped.
* A value close to a limit, within the measurement uncertainty, is reported as
  **cannot assess**, not guessed. The uncertainty grows when the two pose passes
  disagree, when the camera isn't square to the lifter, or when points were
  hidden.
* Criteria a side view can't show (knees out, stance width, grip, eye gaze, …)
  are listed as not assessable, with the reason and page.

## Run it locally

Requires Python 3.12+ (developed on 3.14, Windows 11). No GPU needed.

```bash
git clone https://github.com/TLoksai/Squat-Check.git
cd Squat-Check
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Download the pose model (Google MediaPipe, ~9 MB):

```bash
mkdir models
curl -L -o models/pose_landmarker_full.task https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task
```

Configure the AI coach with **one** of the keys below (optional; without a key
the app still works and labels the tips "rulebook tips"):

```bash
cp .env.example .env      # then fill in GEMINI_API_KEY or ANTHROPIC_API_KEY
```

| Variable | Required | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | one of the two keys | Google Gemini API key (coach = Gemini) |
| `GEMINI_MODEL` | no | Gemini model; default: newest stable `gemini-*-flash` the key can use |
| `ANTHROPIC_API_KEY` | one of the two keys | Claude API key (coach = Claude; wins if both are set) |
| `CLAUDE_MODEL` | no | Claude model, default `claude-opus-5-5` |
| `COACH_PROVIDER` | no | force `gemini`, `claude` or `rulebook` |

Get the assignment's common sample (optional, enables the "Try the common
sample video" button) and start the server:

```bash
python -m tools.get_sample
python -m uvicorn app.main:app --port 8000
```

Open <http://localhost:8000>.

**Hosting:** this prototype runs locally; it is not deployed yet. Analysis is
CPU-heavy (about 25-30 s for an 8 s clip on a laptop), so it needs a host with a
couple of CPU cores, for example a Hugging Face Space or a small VM. See the
build note.

## Supported videos

Shown in the app before upload:

* MP4, MOV, M4V, WEBM, AVI, MKV · 2 to 60 seconds · up to 200 MB
* Side view, camera at hip height, level and still
* Whole body and the near weight plate in the picture, feet included
* 1 to 10 reps, good light

Videos that fail these (too dark, too blurry, too small, not a side view, no
complete rep, no person) get a plain explanation instead of numbers.

## Change a rule, see the result change

1. Open [`skill/squat-standard/criteria.yaml`](skill/squat-standard/criteria.yaml)
   and edit a rule. For example, change `back_angle_bottom` → `target` from
   `{min: 35, max: 55}` to `{min: 35, max: 65}`.
2. On a finished report, click **Re-check with current rulebook**. The rules,
   measurements and coach re-run in seconds; the video is not re-tracked.

Command-line equivalent: `python -m tools.analyse_file <video.mp4>`.

## Project layout

```
app/
  main.py        web server: upload, job status, results, re-check, delete
  pipeline.py    the critical path, stage by stage
  video.py       limits + ffmpeg normalisation
  pose.py        MediaPipe pose, normal + mirrored passes
  bar.py         plate detection and tracking
  quality.py     size / brightness / sharpness gate
  analysis.py    reps, bottom positions, measurements (deterministic)
  rules.py       evaluate measurements against criteria.yaml
  agent.py       Claude coach + output validation + rulebook fallback
skill/squat-standard/
  SKILL.md       instructions for the coach agent
  criteria.yaml  the rules: requirement, pages, targets, cues, assessability
web/             the front end (plain HTML/CSS/JS, canvas overlay)
tools/           sample download, difficult-clip generator, evidence renderer, CLI
tests/           unit tests for the judge, the skill file and the validator
```

## Tests

```bash
python -m pytest -q
```

## What is not done

See [Known limitations](docs/BUILD_NOTES.md#known-limitations). The main ones:
depth is often too close to call with the current pose model; absolute bar
position is sensitive to camera angle and perspective; only one lifter and the
near-side plate are supported; the app is not deployed.
