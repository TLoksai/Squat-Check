"""Record a captioned demo of the real app (no mocks): drives Microsoft Edge
with Playwright, records the screen, and logs which parts are "waiting" so
tools/edit_demo.py can speed them up.

    python -m uvicorn app.main:app --port 8000      # in another terminal
    python -m tools.record_demo                     # -> demo/raw/*.webm + demo/timeline.json
    python -m tools.edit_demo                       # -> demo/squat-check-demo.mp4

The rule-change step edits criteria.yaml (back angle 35-55 -> 35-65) and
always restores it afterwards.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
URL = "http://localhost:8000/"
OUT = ROOT / "demo"
RAW = OUT / "raw"
SAMPLE = ROOT / "samples" / "common_sample.mp4"
HARD = ROOT / "samples" / "hard" / "dark_blurry.mp4"
CRITERIA = ROOT / "skill" / "squat-standard" / "criteria.yaml"
W, H = 1280, 800

OVERLAY_JS = """
() => {
  if (document.getElementById('demo-cap')) return;
  const st = document.createElement('style');
  st.textContent = `
    #demo-cap{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);z-index:2147483646;max-width:980px;
      background:rgba(10,20,18,.88);color:#fff;font:600 21px/1.4 Inter,system-ui,sans-serif;padding:14px 24px;border-radius:16px;
      box-shadow:0 10px 40px rgba(0,0,0,.35);text-align:center;pointer-events:none;transition:opacity .25s}
    #demo-cap small{display:block;font-weight:500;font-size:15px;opacity:.75;margin-top:4px}
    #demo-cap .ff{display:inline-block;background:#2dd4bf;color:#04201c;border-radius:8px;padding:0 8px;margin-left:10px;font-size:14px;vertical-align:2px}
    #demo-cur{position:fixed;left:0;top:0;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;z-index:2147483647;
      background:rgba(13,148,136,.35);border:3px solid #fff;box-shadow:0 2px 10px rgba(0,0,0,.45);pointer-events:none;
      transition:transform .55s cubic-bezier(.3,.7,.3,1)}
    #demo-cur.tap{animation:tap .35s ease-out}
    @keyframes tap{0%{box-shadow:0 0 0 0 rgba(45,212,191,.8)}100%{box-shadow:0 0 0 22px rgba(45,212,191,0)}}`;
  document.head.appendChild(st);
  const c = document.createElement('div'); c.id = 'demo-cap'; c.style.opacity = 0; document.body.appendChild(c);
  const k = document.createElement('div'); k.id = 'demo-cur'; k.style.transform = `translate(${innerWidth*0.6}px, ${innerHeight*0.55}px)`;
  document.body.appendChild(k);
}
"""


class Demo:
    def __init__(self, page: Page):
        self.page = page
        self.t0 = time.monotonic()
        self.events: list[dict] = []

    def now(self) -> float:
        return round(time.monotonic() - self.t0, 2)

    def mark(self, kind: str, **kw) -> None:
        self.events.append({"t": self.now(), "kind": kind, **kw})

    def overlay(self) -> None:
        self.page.evaluate(OVERLAY_JS)

    def cap(self, text: str, sub: str = "", hold: float = 0.0, fast: str = "") -> None:
        self.overlay()
        self.page.evaluate("([t,s,f]) => { const c=document.getElementById('demo-cap');"
                           " c.innerHTML = t + (f ? `<span class=ff>${f}</span>` : '') + (s ? `<small>${s}</small>` : ''); c.style.opacity = 1; }",
                           [text, sub, fast])
        if hold:
            self.page.wait_for_timeout(int(hold * 1000))

    def hide_cap(self) -> None:
        self.page.evaluate("() => { const c=document.getElementById('demo-cap'); if (c) c.style.opacity = 0; }")

    def move(self, selector: str) -> None:
        self.overlay()
        loc = self.page.locator(selector).first
        loc.scroll_into_view_if_needed()
        box = loc.bounding_box()
        if not box:
            return
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.page.evaluate("([x,y]) => document.getElementById('demo-cur').style.transform = `translate(${x}px, ${y}px)`", [x, y])
        self.page.wait_for_timeout(650)

    def click(self, selector: str, pause: float = 0.6) -> None:
        self.move(selector)
        self.page.evaluate("() => { const k=document.getElementById('demo-cur'); k.classList.remove('tap'); void k.offsetWidth; k.classList.add('tap'); }")
        self.page.locator(selector).first.click()
        self.page.wait_for_timeout(int(pause * 1000))

    def scroll_to(self, selector: str, block: str = "start", pause: float = 0.8) -> None:
        self.page.evaluate("([s,b]) => document.querySelector(s)?.scrollIntoView({behavior:'smooth', block:b})", [selector, block])
        self.page.wait_for_timeout(int(pause * 1000))


def card(title: str, lines: list[str], footer: str = "") -> str:
    items = "".join(f"<li>{x}</li>" for x in lines)
    return f"""<!doctype html><html><head><meta charset=utf-8>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
<style>body{{margin:0;height:100vh;display:grid;place-items:center;background:radial-gradient(120% 90% at 20% 0%,#134e48 0%,#0b1210 60%);
color:#e8f0ed;font-family:Inter,system-ui,sans-serif}}.w{{max-width:980px;padding:40px}}
.logo{{display:inline-flex;align-items:center;gap:14px;font-weight:800;font-size:26px;letter-spacing:-.02em;margin-bottom:28px}}
.m{{width:44px;height:44px;border-radius:12px;background:#2dd4bf;position:relative}}.m:before,.m:after{{content:"";position:absolute;top:12px;width:6px;height:20px;border-radius:3px;background:#0b1210}}
.m:before{{left:5px}}.m:after{{right:5px}}.m i{{position:absolute;left:9px;right:9px;top:19px;height:6px;border-radius:3px;background:#0b1210}}
h1{{font-size:54px;line-height:1.08;letter-spacing:-.035em;margin:0 0 22px;font-weight:800}}
ul{{list-style:none;padding:0;margin:0;display:grid;gap:12px;font-size:22px;color:#c2cfca}}li:before{{content:"→ ";color:#2dd4bf}}
.f{{margin-top:34px;font-size:20px;color:#5eead4;font-weight:600}}</style></head>
<body><div class=w><div class=logo><span class=m><i></i></span>Squat Check</div><h1>{title}</h1><ul>{items}</ul>
{f'<div class=f>{footer}</div>' if footer else ''}</div></body></html>"""


def main() -> None:
    shutil.rmtree(RAW, ignore_errors=True)
    RAW.mkdir(parents=True)
    original = CRITERIA.read_text(encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1,
                                  record_video_dir=str(RAW), record_video_size={"width": W, "height": H},
                                  color_scheme="light")
        page = ctx.new_page()
        d = Demo(page)
        try:
            # 0. title
            page.set_content(card("AI squat analysis,<br>grounded in the standard",
                                  ["Upload a side-view squat video",
                                   "We track the body and the bar, and find every rep",
                                   "Each rep is checked against the reference, with page numbers"],
                                  "A working prototype, recorded live"))
            page.wait_for_timeout(5200)

            # 1. upload page
            page.goto(URL)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(600)
            d.cap("Upload a side-view video of a barbell back squat",
                  "Formats, limits and recording tips are shown before you upload", hold=3.2)
            d.move(".tips-card .tip-list li:nth-child(1)")
            d.move(".tips-card .tip-list li:nth-child(3)")
            page.wait_for_timeout(1200)
            d.cap("We'll use the assignment's common sample video", "A real upload, real processing: nothing is mocked")
            d.move("#dropzone")
            page.wait_for_timeout(900)
            page.set_input_files("#file-input", str(SAMPLE))

            # 2. progress (sped up in editing)
            page.wait_for_selector("#view-progress:not([hidden])")
            page.wait_for_timeout(1500)
            d.mark("fast_start", factor=6)
            d.cap("Body points (MediaPipe, run twice), bar tracking, reps, rules, then the AI coach",
                  "About a minute for an 8-second clip", fast="6× speed")
            page.wait_for_selector("#view-result:not([hidden])", timeout=240_000)
            page.wait_for_timeout(500)
            d.mark("fast_end")

            # 3. report
            d.cap("The report: what needs work, what looks good, and what couldn't be checked",
                  "Results come from geometry checked against the rulebook; the AI coach only explains them", hold=5.5)
            d.scroll_to("#report-grid", "start", 1.2)
            d.cap("Annotated video: body points, bar path, midfoot line and a rep timeline", "Solid dots were seen; hollow dots are estimates")
            d.click("#btn-play", 3.8)
            d.click("#btn-play", 0.6)
            d.cap("“What to work on first”, from the coach, citing the reference pages", hold=2.4)
            d.click("#priorities [data-open]", 1.0)
            d.cap("Every finding shows its evidence",
                  "The rule and page · what we measured · the target (from the document, or the app's interpretation) · the uncertainty", hold=5.0)
            d.click("#finding-groups details[open] [data-seek]", 1.4)
            d.cap("…and jumps to the exact frame that was measured", hold=2.0)
            d.click("#rep-chips [data-rep='2']", 1.6)
            d.click("#tab-overview", 0.8)
            d.scroll_to("#glance", "center", 1.0)
            d.cap("Too close to call? It says “can't assess” instead of guessing",
                  "Depth here is within the measurement margin, so no verdict is given")
            d.click(".res-dot[data-open='1|depth']", 1.0)
            d.scroll_to("#finding-groups details[open]", "center", 3.2)
            d.click("#tab-na", 0.6)
            d.cap("What a side view can't show is listed with the reason and the pages", hold=3.6)

            # 4. rulebook
            page.evaluate("() => window.scrollTo({top:0, behavior:'smooth'})")
            page.wait_for_timeout(700)
            d.cap("The rulebook: the skill the app and the AI coach both use",
                  "Every rule from the PDF with pages, a target, a margin and coaching tips")
            d.click("#btn-rulebook", 2.6)
            d.click("#rb-search", 0.3)
            page.keyboard.type("depth", delay=90)
            page.wait_for_timeout(2200)
            page.fill("#rb-search", "")
            page.dispatch_event("#rb-search", "input")
            d.click("[data-rb='na']", 2.4)
            d.click("#rulebook-close", 0.6)

            # 5. change a rule -> re-check
            d.cap("Change a rule, then re-check: no code change, no re-processing",
                  "Here: the back-angle target goes from 35-55° to 35-65°", hold=3.0)
            CRITERIA.write_text(original.replace("target: {min: 35, max: 55}", "target: {min: 35, max: 65}", 1), encoding="utf-8")
            d.click("#btn-recheck", 0.4)
            d.mark("fast_start", factor=6)
            d.cap("Re-checking with the edited rulebook…", fast="6× speed")
            page.wait_for_selector("#toast:not([hidden])", timeout=180_000)
            d.mark("fast_end")
            d.scroll_to("#glance", "center", 0.8)
            d.cap("The back-angle results change straight away", "The rule is restored afterwards", hold=4.5)
            CRITERIA.write_text(original, encoding="utf-8")

            # 6. a bad video
            page.evaluate("() => window.scrollTo({top:0})")
            d.click("#view-result .head-actions [data-action=restart]", 0.8)
            d.cap("A bad video: too dark, blurry and small", "It should be rejected, not given made-up numbers")
            page.wait_for_timeout(1500)
            page.set_input_files("#file-input", str(HARD))
            page.wait_for_selector("#view-progress:not([hidden])")
            d.mark("fast_start", factor=6)
            d.cap("Analysing…", fast="6× speed")
            page.wait_for_selector("#view-result:not([hidden])", timeout=240_000)
            d.mark("fast_end")
            d.cap("Rejected, with the reason and how to re-record", hold=4.5)

            # 7. end card
            d.hide_cap()
            page.set_content(card("Squat Check",
                                  ["Rules taken from the reference, each with page citations",
                                   "Honest “can't assess” instead of invented precision",
                                   "Tested on the sample, 4 real clips and 4 difficult clips"],
                                  "github.com/TLoksai/Squat-Check"))
            page.wait_for_timeout(5500)
        finally:
            CRITERIA.write_text(original, encoding="utf-8")  # always restore the rulebook
            (OUT / "timeline.json").write_text(json.dumps(d.events, indent=1), encoding="utf-8")
            ctx.close()
            browser.close()
    print("recorded:", [str(x) for x in RAW.glob("*.webm")])


if __name__ == "__main__":
    main()
