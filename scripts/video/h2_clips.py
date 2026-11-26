"""Recording scenes for the H2 video -> ~/Downloads/standby-hackathon/video/clips/{s2,s7}.mp4

The two Well-Architected Review runs were screen-recorded on a 4096x2304 display. Only the Claude Code
conversation column is kept (no sidebar, no session panel, no other windows), and only up to the moment
the final summary appears: the summaries list unrelated security findings. The result frames below are
cropped further to the H2 parts of those summaries. Run after `assemble.py --audio-only`, so segment
lengths follow the real narration (falls back to an estimate without it).

  uv run --no-project python scripts/video/h2_clips.py
"""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
H = Path.home() / "Downloads" / "standby-hackathon"
V = H / "video"
OUT = V / "clips"
SLIDES = V / "slides"
NARRATION = json.loads((ROOT / "docs" / "video" / "h2-narration.json").read_text())
LEAD, TAIL = 0.4, 0.8  # same as assemble.py
FONT = "/System/Library/Fonts/Helvetica.ttc"
BG = "0x141311"

# Conversation column of each recording (x, y, w, h) and the part of the run that is shown (seconds).
RUNS = {
    "before": dict(src=H / "wa-review-before.mov", crop=(600, 100, 1170, 1520), end=455.0),
    "after": dict(src=H / "wa-review-after.mov", crop=(520, 66, 1250, 1590), end=438.0),
}
# Result frames: (recording, time, region inside the conversation crop as x, y, w, h).
FREEZE = {
    "h2": ("before", 475.0, (60, 176, 1020, 224)),       # H2 bullet only (stops above "Strengths")
    "table": ("after", 487.0, (100, 64, 1040, 391)),     # results table (before, after) + "What the fix closed"
    "m9": ("after", 487.0, (140, 650, 970, 100)),        # M9 bullet only
}


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def dur(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         check=True, capture_output=True, text=True).stdout
    return float(out)


def sentence_starts(i):
    """Scene length and sentence start times, computed exactly like assemble.py's captions."""
    text = NARRATION[i]
    mp3 = V / "parts" / f"s{i + 1}.mp3"
    a = dur(mp3) if mp3.exists() else len(text.split()) / 2.5
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    starts, t = [], LEAD
    for s in sents:
        starts.append(t)
        t += a * len(s) / sum(len(x) for x in sents)
    return LEAD + a + TAIL, starts


def labels(path, items):
    """Transparent 1920x1080 PNG with text (this ffmpeg build has no drawtext).
    items: (x, y, text, size, color, box color or None); x may be "center"."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for x, y, text, size, color, box in items:
        f = ImageFont.truetype(FONT, size)
        w = int(f.getlength(text))
        x = (1920 - w) // 2 if x == "center" else x
        if box:
            d.rounded_rectangle((x - 14, y - 8, x + w + 14, y + size + 12), radius=10, fill=box)
        d.text((x, y), text, font=f, fill=color)
    im.save(path)
    return path


def timelapse(name, length, out, bg_png, label):
    """The run, sped up to `length` seconds, as a panel on the left of a slide."""
    r = RUNS[name]
    x, y, w, h = r["crop"]
    ph = 800
    pw = round(w * ph / h / 2) * 2
    speed = r["end"] / length
    lab = labels(OUT / f"{out.stem}_labels.png", [
        (140, 852, label, 26, (154, 148, 136, 255), None),
        (140 + pw - 100, 60, f"×{speed:.0f}", 34, (20, 19, 17, 255), (240, 180, 90, 255))])
    fc = (f"[1:v]crop={w}:{h}:{x}:{y},scale={pw}:{ph}:flags=lanczos,setpts=(PTS-STARTPTS)/{speed:.4f},fps=30[p];"
          f"[0:v][p]overlay=140:40:shortest=1,"
          f"drawbox=x=138:y=38:w={pw + 4}:h={ph + 4}:color=0x3a362f:t=2[t];"
          f"[t][2:v]overlay=0:0,format=yuv420p[v]")
    run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", "30", "-i", str(bg_png),
         "-t", f"{r['end']:.2f}", "-i", str(r["src"]), "-i", str(lab), "-filter_complex", fc, "-map", "[v]",
         "-t", f"{length:.3f}", "-c:v", "libx264", "-crf", "18", "-an", str(out)])
    print(f"{out.name}: {r['end']:.0f}s of recording at x{speed:.1f} -> {length:.1f}s")


def still(key, width):
    name, t, (fx, fy, fw, fh) = FREEZE[key]
    r = RUNS[name]
    x, y, w, h = r["crop"]
    png = OUT / f"freeze_{key}.png"
    run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(r["src"]), "-frames:v", "1",
         "-vf", f"crop={fw}:{fh}:{x + fx}:{y + fy},scale={width}:-2:flags=lanczos", str(png)])
    return png


def freeze(length, out, items, kicker):
    """Result frames stacked on a plain background, with a kicker line above."""
    inputs, fc, last = [], [f"color=c={BG}:s=1920x1080:r=30:d={length:.3f}[b0]"], "b0"
    ytop = 130
    for k, (png, w, h) in enumerate(items):
        inputs += ["-i", str(png)]
        fc.append(f"[{last}][{k}:v]overlay=x=(W-w)/2:y={ytop}:shortest=0[b{k + 1}]")
        fc.append(f"[b{k + 1}]drawbox=x=(iw-{w})/2-3:y={ytop - 3}:w={w + 6}:h={h + 6}:color=0x3a362f:t=2[d{k + 1}]")
        last, ytop = f"d{k + 1}", ytop + h + 50
    lab = labels(OUT / f"{out.stem}_labels.png", [((1920 - items[0][1]) // 2, 84, kicker, 30, (154, 148, 136, 255), None)])
    inputs += ["-i", str(lab)]
    fc.append(f"[{last}][{len(items)}:v]overlay=0:0,format=yuv420p[v]")
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(fc), "-map", "[v]",
         "-t", f"{length:.3f}", "-c:v", "libx264", "-crf", "18", "-an", str(out)])


def size(png):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(png)],
                         check=True, capture_output=True, text=True).stdout.strip()
    return tuple(int(v) for v in out.split(","))


def slide_clip(png, length, out):
    run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", "30", "-i", str(png), "-t", f"{length:.3f}",
         "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "18", str(out)])


def concat(parts, out):
    lst = OUT / f"{out.stem}_list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    print(f"{out.name}: {dur(out):.1f}s")


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    # Scene 2: the review run, then the H2 finding when the narration reaches "and raised H2".
    total, st = sentence_starts(1)
    cut = st[1] + (total - st[1]) * 0.45
    timelapse("before", cut, OUT / "s2_a.mp4", SLIDES / "s2.png", "Recorded run, review of d7095ba")
    png = still("h2", 1640)
    freeze(total - cut, OUT / "s2_b.mp4", [(png, *size(png))], "The finding · review of d7095ba")
    concat([OUT / "s2_a.mp4", OUT / "s2_b.mp4"], OUT / "s2.mp4")

    # Scene 7: the re-run with the numbers beside it; the result table and M9; the closing line.
    total, st = sentence_starts(6)
    t_m9, t_end = st[2], st[4]
    timelapse("after", t_m9, OUT / "s7_a.mp4", SLIDES / "s7.png", "Recorded run, review of the fixed code")
    tab, m9 = still("table", 1300), still("m9", 1300)
    freeze(t_end - t_m9, OUT / "s7_b.mp4", [(tab, *size(tab)), (m9, *size(m9))], "Review of the fixed code · columns: before, after")
    slide_clip(SLIDES / "end.png", total - t_end, OUT / "s7_c.mp4")
    concat([OUT / "s7_a.mp4", OUT / "s7_b.mp4", OUT / "s7_c.mp4"], OUT / "s7.mp4")


if __name__ == "__main__":
    main()
