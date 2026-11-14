"""Render the video slides (1920x1080, content above the 200 px caption band) -> ~/Downloads/standby-video/slides/

  uv run --no-project --with playwright python scripts/video/slides.py
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
IMG = (ROOT / "docs" / "images").as_uri()
OUT = Path.home() / "Downloads" / "standby-video" / "slides"

CSS = """
*{box-sizing:border-box} body{margin:0;width:1920px;height:1080px;background:#141311;color:#f1ede4;
font-family:-apple-system,'Helvetica Neue',sans-serif;overflow:hidden}
.stage{position:absolute;left:0;top:0;width:1920px;height:880px;padding:90px 140px;display:flex;flex-direction:column;justify-content:center}
.kicker{font-size:30px;letter-spacing:.14em;text-transform:uppercase;color:#9a9488;margin-bottom:28px}
.big{font-size:84px;line-height:1.12;font-weight:700;letter-spacing:-.01em}
.mid{font-size:52px;line-height:1.25;font-weight:600}
.small{font-size:34px;line-height:1.4;color:#c9c3b6}
.accent{color:#f0b45a} .ok{color:#5fbf85} .muted{color:#9a9488}
.rule{height:4px;background:#f1ede4;width:220px;margin:34px 0}
.copy{position:absolute;right:60px;top:40px;font-size:24px;color:#9a9488}
ul{margin:0;padding:0;list-style:none} li{margin:0 0 26px 0}
table{border-collapse:collapse;font-size:38px} td,th{padding:16px 26px;border-bottom:2px solid #2e2b26;text-align:left}
th{color:#9a9488;font-weight:600;font-size:28px;letter-spacing:.06em;text-transform:uppercase}
.row{display:flex;gap:70px;align-items:center;height:100%}
"""

SLIDES = {
"s1": """<div class="stage"><div class="kicker">From running community meetups</div>
<div class="big">A seat offered on the day<br>is a seat <span class="accent">nobody takes.</span></div></div>""",

"s2": """<div class="stage"><ul class="mid">
<li><span class="muted">Venues</span> need the visitor list or badges <span class="accent">2 days ahead</span></li>
<li><span class="muted">My first version</span> moved the seat in 3 seconds, on the day</li></ul>
<div class="rule"></div>
<div class="mid">The problem isn't that people cancel.<br>It's that <span class="accent">we find out too late.</span></div></div>""",

"s3": """<div class="copy">© ikenyal</div><div class="stage"><div class="kicker">AWS Weekend Challenge · No Pipeline, No Problem</div>
<div class="big" style="font-size:150px">Standby</div><div class="rule"></div>
<div class="mid">The waitlist that lives in your calendar.</div>
<div class="small" style="margin-top:24px">Every seat is a calendar invite. Yes keeps it. No, or silence, passes it on.</div></div>""",

"s4": """<div class="stage"><div class="kicker">Live, on real calendars</div></div>""",

"s5": f"""<div class="stage" style="padding:50px 120px"><div class="row">
<img src="{IMG}/calendar-reconfirm.png" style="height:740px;border-radius:28px">
<div><div class="kicker">Asked again, days before</div><ul class="mid" style="font-size:46px">
<li>Re-invite → calendar shows <span class="accent">awaiting reply</span></li>
<li>Silence frees the seat <span class="muted">while the waitlist can still plan</span></li>
<li>Roster deadline → seats <span class="ok">stop moving</span></li></ul></div></div></div>""",

"s6": f"""<div class="stage" style="padding:30px 120px"><div class="row" style="gap:80px">
<img src="{IMG}/architecture.png" style="height:820px;background:#fff;border-radius:16px;padding:10px">
<div><div class="kicker">All serverless · one sam deploy</div><ul class="small" style="font-size:38px">
<li><b>Step Functions</b> · one execution per seat hold, waiting on a task token</li>
<li><b>SES</b> · sends the invite, receives the calendar's reply</li>
<li><b>Lambda</b> · reads the reply, resumes exactly one hold</li>
<li><b>DynamoDB</b> · a seat only inside a transaction that checks one is free</li>
<li><b>API Gateway · S3</b> · organizer API, raw replies</li></ul></div></div></div>""",

"s7": """<div class="stage"><div class="kicker">Measured · 50 people, 30 seats, 10 trials each</div>
<table><tr><th></th><th>Seats given</th><th>Wrong</th></tr>
<tr><td>Check-then-act</td><td>30, 29, 31, 30, 31, 29, 30, 31, 28, 31</td><td class="accent">7 / 10</td></tr>
<tr><td>Condition in the transaction</td><td>30 every time</td><td class="ok">0 / 10</td></tr></table>
<div class="small" style="margin-top:56px">Found later in the execution history: <span class="accent">2 people lost a seat</span> to a Lambda throttle.<br>
Throttling is retried now. 0 since, across 150 holds.</div></div>""",

"s8": """<div class="copy">© ikenyal</div><div class="stage"><div class="kicker">One event · 80 sign-ups · 50 seats</div>
<div class="big">≈ $0.03 <span class="small" style="font-size:40px">mostly email · nothing billed by the hour</span></div>
<div class="rule"></div><div class="mid">Standby</div>
<div class="small">github.com/ikenyal/standby</div></div>""",
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        page = b.new_page(viewport={"width": 1920, "height": 1080})
        for sid, body in SLIDES.items():
            tmp = OUT / f"{sid}.html"   # a file:// page may load file:// images; about:blank may not
            tmp.write_text(f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>")
            page.goto(tmp.as_uri(), wait_until="load")
            page.wait_for_timeout(200)
            page.screenshot(path=str(OUT / f"{sid}.png"))
            tmp.unlink()
            print(sid)
        b.close()


if __name__ == "__main__":
    main()
