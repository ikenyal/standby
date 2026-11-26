"""Slides for the H2 video (Well-Architected Review finding, fault injection, fix, re-run)
-> ~/Downloads/standby-hackathon/video/slides/  (same look as the original video: slides.py)

  uv run --no-project --with playwright python scripts/video/h2_slides.py
Numbers come from results/README.md §7 and results/wa-review-h2-excerpt.md.
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from slides import CSS  # noqa: E402  (shared house style)

OUT = Path.home() / "Downloads" / "standby-hackathon" / "video" / "slides"

EXTRA = """
.flow{display:flex;align-items:center;gap:26px;margin-top:10px}
.box{border:3px solid #3a362f;border-radius:18px;padding:22px 28px;font-size:36px;line-height:1.25;font-weight:600;min-width:250px}
.box.bad{border-color:#f0b45a;color:#f0b45a} .box.good{border-color:#5fbf85;color:#5fbf85}
.arr{font-size:44px;color:#9a9488}
.stat{font-size:124px;font-weight:800;line-height:1;white-space:nowrap}
.statlab{font-size:32px;color:#c9c3b6;margin-top:10px}
.grid{display:flex;gap:90px;margin-top:40px}
.tag{display:inline-block;font-size:26px;letter-spacing:.08em;text-transform:uppercase;padding:8px 18px;border-radius:10px;
background:#2e2b26;color:#c9c3b6;margin-right:14px}
"""

SLIDES = {
"s1": """<div class="copy">© ikenyal</div><div class="stage"><div class="kicker">Serverless · calendar-RSVP waitlist</div>
<div class="big" style="font-size:150px">Standby</div><div class="rule"></div>
<div class="mid">The waitlist that lives in your calendar.</div>
<div class="small" style="margin-top:24px">Every seat is a calendar invite. Yes keeps it. No, or silence, passes it on.</div>
<div style="margin-top:40px"><span class="tag">Step Functions</span><span class="tag">SES</span><span class="tag">Lambda</span><span class="tag">DynamoDB</span></div></div>""",

"s2": """<div class="stage" style="align-items:flex-end;text-align:right">
<div style="width:900px"><div class="kicker">Claude Code · Agent Toolkit</div>
<div class="mid">Well-Architected Review skill</div>
<div class="small" style="margin-top:18px">Full review · live framework<br>261 best practices · 57 questions</div></div></div>""",

"s3": """<div class="stage"><div class="kicker">H2 · Reliability · High</div>
<div class="mid">Never gives a seat to two people.<br><span class="accent">Could give a seat to nobody.</span></div>
<div class="rule"></div>
<div class="flow"><div class="box">Hold expires<br><span class="muted" style="font-size:28px">release frees the seat</span></div>
<div class="arr">→</div><div class="box bad">Cancel email fails</div>
<div class="arr">→</div><div class="box">Step retried<br><span class="muted" style="font-size:28px">"already released" → stop</span></div>
<div class="arr">→</div><div class="box bad">Nobody invited</div></div>
<div class="small" style="margin-top:36px">REL04-BP04 idempotent operations · REL11-BP03 automated healing</div></div>""",

"s4": """<div class="stage"><div class="kicker">Live stack · 30 people · 10 seats · 60 s holds · nobody answers</div>
<div class="small">Natural run: <span class="ok">not reproduced</span> — Lambda concurrency limit 10 spread the emails out.<br>
So: a temporary permission block on sending the cancellation email.</div>
<div class="grid"><div><div class="stat accent">10 / 10</div><div class="statlab">seats empty, every trial</div></div>
<div><div class="stat">10 / 30</div><div class="statlab">people offered a seat</div></div>
<div><div class="stat ok" style="font-size:84px;line-height:1.48">SUCCEEDED</div><div class="statlab">every execution: no alarm would fire</div></div></div>
<div class="small muted" style="margin-top:34px">Before the fix · 3 of 3 trials</div></div>""",

"s5": """<div class="stage"><div class="kicker">The fix</div>
<div class="big">Seat first, <span class="accent">then</span> email.<br>Both again on every retry.</div><div class="rule"></div>
<ul class="small" style="font-size:38px">
<li>Throttled email → retried with backoff, not given away</li>
<li>Reconciler every 5 min → repairs whatever a failed step left behind</li>
<li>Releases only a hold still in the status it read → never frees a confirmed seat</li></ul></div>""",

"s6": """<div class="stage"><div class="kicker">Same fault · fixed code · 3 trials each</div>
<table><tr><th></th><th>Seats empty</th><th>People offered</th><th>Executions</th></tr>
<tr><td>Before</td><td class="accent">10 / 10</td><td>10 / 30</td><td>all SUCCEEDED</td></tr>
<tr><td>After</td><td class="ok">0</td><td class="ok">30 / 30</td><td class="accent">FAILED, visibly</td></tr></table>
<div class="small" style="margin-top:56px">The seat matters more than the courtesy email.</div></div>""",

"s7": """<div class="stage" style="align-items:flex-end;text-align:right">
<div style="width:900px"><div class="kicker">Review, run again on the fixed code</div>
<div class="mid">H2 <span class="accent">High</span> → <span class="ok">Implemented</span></div>
<div class="small" style="margin-top:18px">Implemented best practices<br><span style="font-size:72px;font-weight:700;color:#f1ede4">54 → <span class="ok">59</span></span></div>
<div class="small" style="margin-top:18px">New: <span class="accent">M9</span> · a failed cancellation is never resent</div></div></div>""",

"end": """<div class="copy">© ikenyal</div><div class="stage"><div class="kicker">Standby</div>
<div class="big">Never gave a seat to two people.<br><span class="ok">Now never gives a seat to nobody.</span></div>
<div class="rule"></div><div class="small">github.com/ikenyal/standby · results/wa-review-h2-excerpt.md</div></div>""",
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        page = b.new_page(viewport={"width": 1920, "height": 1080})
        for sid, body in SLIDES.items():
            tmp = OUT / f"{sid}.html"
            tmp.write_text(f"<html><head><meta charset='utf-8'><style>{CSS}{EXTRA}</style></head><body>{body}</body></html>")
            page.goto(tmp.as_uri(), wait_until="load")
            page.wait_for_timeout(200)
            page.screenshot(path=str(OUT / f"{sid}.png"))
            tmp.unlink()
            print(sid)
        b.close()


if __name__ == "__main__":
    main()
