# Demo script

The demo video (`demo/squat-check-demo.mp4`, made with `tools/record_demo.py` +
`tools/edit_demo.py`) is a captioned recording of the real app. To add your own
voice, read the lines below over it (about 2 minutes, at a relaxed pace).

## Voice-over (matches the video, section by section)

**Title (0:00)**
"This is Squat Check: AI squat analysis that's grounded in the coaching standard
we were given, not in generic advice."

**Upload (0:05)**
"You upload a side-view video of a barbell squat. The page says up front which
formats and lengths work and how to film. I'm using the assignment's sample
video, a real upload with real processing."

**Processing (0:15)**
"The server finds the body points with MediaPipe, twice per frame, as recorded
and mirrored, so it knows how sure it is. It tracks the weight plate, finds
each rep, checks the rules, and then an AI coach writes the explanation."

**Report (0:25)**
"The results come from geometry checked against the rulebook. The AI never
decides a pass or fail, it only explains. Here's the annotated video: joints,
bar path, the midfoot line, and a rep timeline."

**Evidence (0:40)**
"Every finding shows its evidence: the rule and its page in the reference,
what was measured, the target, and whether that number comes from the document
or is the app's own reading. It jumps to the exact frame that was measured."

**Can't assess (0:55)**
"When a value is within the measurement margin, like depth here, the app says
'can't assess' rather than guessing. Things a side view can't show are listed
with the reason."

**Rulebook (1:05)**
"This is the rulebook, the skill that both the app and the AI coach use. Every
rule from the PDF, with pages, a target, a margin and coaching tips."

**Rule change (1:15)**
"If the standard changes, I edit one line in the rulebook and re-check. No code
change and no re-processing: the results update straight away."

**Bad video (1:30)**
"And a bad video, too dark and blurry, is rejected with the reason and how to
re-record. No made-up numbers."

**End (1:40)**
"Rules from the reference with page citations, honest 'can't assess' results,
and tested on the sample, four real clips and four difficult ones. The code is
on GitHub."

## Uploading it for the job form

The form asks for a link it can open without asking for access:

1. **YouTube** → Create → Upload video → choose `demo/squat-check-demo.mp4` →
   Visibility **Unlisted** → copy the link.
   *or* **Google Drive** → upload → Share → General access **Anyone with the
   link** → copy the link.
2. Paste the link into "Link to a demo video" on the application form.
3. Open the link in a private/incognito window to check it plays without
   signing in.

## The 5-minute customer pitch (for the interview)

1. **The problem (1 min).** Lifters can't see their own squat; coaches are
   expensive and not there for every set; generic form-check apps give
   confident advice that may be wrong.
2. **What Squat Check does (1 min).** Upload a phone video and get rep-by-rep
   checks against a named coaching standard, each with a page reference and
   the exact frame as evidence.
3. **Why trust it (1 min).** It says "can't assess" instead of guessing, shows
   observed vs estimated points, and the AI only explains decisions made by
   transparent rules. Show one finding's evidence.
4. **Who it's for (1 min).** Coaches who review clients' videos remotely (saves
   time per video, and every flag comes with evidence); gyms and online
   programmes that teach a specific method.
5. **What's next (1 min).** A front-view camera for knees out and stance, a
   heavier pose model for depth, hosted deployment, and a coach dashboard.
   Be honest about the limits: depth is often too close to call from the side.
