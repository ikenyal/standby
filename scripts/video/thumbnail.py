"""YouTube thumbnail (1280x720) -> ~/Downloads/standby-video/thumbnail.png
  uv run --no-project --with playwright python scripts/video/thumbnail.py
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

V = Path.home() / "Downloads" / "standby-video"
HTML = f"""<html><head><meta charset='utf-8'><style>
*{{box-sizing:border-box}} body{{margin:0;width:1280px;height:720px;background:#141311;color:#f1ede4;
font-family:-apple-system,'Helvetica Neue',sans-serif;overflow:hidden;position:relative}}
.t{{position:absolute;left:70px;top:70px;font-size:132px;font-weight:800;letter-spacing:-.02em}}
.s{{position:absolute;left:74px;top:236px;font-size:46px;font-weight:600;line-height:1.2;width:600px}}
.a{{color:#f0b45a}}
.k{{position:absolute;left:74px;bottom:62px;font-size:22px;letter-spacing:.12em;text-transform:uppercase;color:#9a9488}}
.cells{{position:absolute;right:70px;top:48px;display:flex;flex-direction:column;align-items:center;gap:6px}}
.cell{{display:flex;flex-direction:column;align-items:center;gap:12px}}
.cell img{{width:380px;border-radius:12px}}
.lab{{font-size:24px;color:#9a9488}}
.arrow{{font-size:64px;color:#f0b45a;font-weight:800;line-height:1}}
</style></head><body>
<div class='t'>Standby</div>
<div class='s'>The waitlist that<br>lives in <span class='a'>your calendar</span></div>
<div class='k'>Serverless · AWS Step Functions · Amazon SES</div>
<div class='cells'>
 <div class='cell'><img src='{(V / "thumb_L.png").as_uri()}'><div class='lab'>Attendee 1 taps No</div></div>
 <div class='arrow'>↓</div>
 <div class='cell'><img src='{(V / "thumb_R.png").as_uri()}'><div class='lab'>seconds later: attendee 2</div></div>
</div></body></html>"""

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    page = b.new_page(viewport={"width": 1280, "height": 720})
    tmp = V / "thumb.html"
    tmp.write_text(HTML)
    page.goto(tmp.as_uri(), wait_until="load")
    page.wait_for_timeout(200)
    page.screenshot(path=str(V / "thumbnail.png"))
    tmp.unlink()
    b.close()
print(V / "thumbnail.png")
