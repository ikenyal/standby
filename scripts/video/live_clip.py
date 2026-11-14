"""Build the live scene from the screen recording: the Dec 18 cell of each real calendar, side by side.

Only the two day cells are kept (nothing else from either calendar). The calendars were reloaded every
5 s during the recording, so the segments below skip the reload flashes; everything else is real time.
  uv run --no-project --with pillow python scripts/video/live_clip.py
"""
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

V = Path.home() / "Downloads" / "standby-video"
SRC = V / "live30.mp4"   # live.mov converted to a constant 30 fps (screen recordings only store changed frames)
CELL = {"L": (1211, 1047), "R": (2921, 1047)}   # top-left of each Dec 18 cell in the recording (px)
W, H, S = 237, 309, 2.0                            # cell size and scale
SEGMENTS = [  # (start, end, badge) in recording seconds; timed to the narration of scene 4
    (21.6, 22.6, None),                                 # both empty
    (23.2, 25.6, "Seat offered to attendee 1"),
    (59.4, 61.9, "Attendee 1 taps No (10:07:42)"),      # the reply leaves at 10:07:42
    (64.7, 66.7, "Declined: the reply email reaches SES"),
    (69.9, 74.7, "Seat moved to attendee 2"),
]
FONT = "/System/Library/Fonts/Helvetica.ttc"


def png(path, draw_fn):
    im = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(im))
    im.save(path)
    return path


def main():
    sw, sh = int(W * S), int(H * S)
    lx, rx, top = 960 - 60 - sw, 960 + 60, 90
    f_lab = ImageFont.truetype(FONT, 34)
    f_small = ImageFont.truetype(FONT, 24)

    def base(d):
        d.rectangle((0, 0, 1920, 1080), fill=(20, 19, 17, 255))
        for x, t in ((lx, "Attendee 1 · Gmail"), (rx, "Attendee 2 · Google Workspace")):
            d.text((x, top - 52), t, font=f_lab, fill=(241, 237, 228, 255))
        d.text((lx, top + sh + 18), "Real Google Calendars, Dec 18 cell only. Recorded live; cuts skip page reloads.",
               font=f_small, fill=(154, 148, 136, 255))
    bg = png(V / "live_bg.png", base)

    parts = []
    for i, (a, b, badge) in enumerate(SEGMENTS):
        bpath = V / f"live_badge{i}.png"
        png(bpath, lambda d: badge and (
            d.rounded_rectangle((lx, top + sh + 64, lx + 24 + int(f_lab.getlength(badge)) + 24, top + sh + 120),
                                radius=12, fill=(240, 180, 90, 255)),
            d.text((lx + 24, top + sh + 72), badge, font=f_lab, fill=(20, 19, 17, 255))))
        out = V / f"live_seg{i}.mp4"
        (lx0, ly0), (rx0, ry0) = CELL["L"], CELL["R"]
        fc = (f"[1:v]split[a][b];[a]crop={W}:{H}:{lx0}:{ly0},scale={sw}:{sh}:flags=lanczos[l];"
              f"[b]crop={W}:{H}:{rx0}:{ry0},scale={sw}:{sh}:flags=lanczos[r];"
              f"[0:v][l]overlay={lx}:{top}[t1];[t1][r]overlay={rx}:{top}[t2];[t2][2:v]overlay=0:0,fps=30,format=yuv420p[v]")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(bg), "-ss", str(a), "-t", f"{b - a:.2f}",
                        "-i", str(SRC), "-loop", "1", "-i", str(bpath), "-filter_complex", fc, "-map", "[v]",
                        "-t", f"{b - a:.2f}", "-c:v", "libx264", "-crf", "18", "-an", str(out)], check=True)
        parts.append(out)
    lst = V / "live_list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                    str(V / "live_clip.mp4")], check=True)
    print("live_clip.mp4", sum(b - a for a, b, _ in SEGMENTS), "s")


if __name__ == "__main__":
    main()
