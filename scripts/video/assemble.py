"""Assemble the demo video -> ~/Downloads/standby-video/standby-demo.mp4

House style of the earlier Builder Center videos: Amazon Polly (Brian, generative), captions burned in
one sentence at a time, short fades. Scene 4 is the live recording (scripts/video/live_clip.py).
  AWS_PROFILE=... uv run --no-project --with playwright python scripts/video/assemble.py

Other videos reuse it through environment variables (defaults build the original video):
  STANDBY_VIDEO_DIR  work and output folder          (~/Downloads/standby-video)
  STANDBY_NARRATION  narration JSON, one entry/scene (docs/video/narration.json)
  STANDBY_CLIPS      JSON {scene: clip path relative to the video dir} ({"s4": "live_clip.mp4"})
  STANDBY_OUTPUT     final file name                 (standby-demo.mp4)
  --audio-only       synthesize the narration and print scene durations, nothing else
Narration audio is cached per scene and only re-synthesized when its text changes.
"""
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
V = Path(os.environ.get("STANDBY_VIDEO_DIR", Path.home() / "Downloads" / "standby-video")).expanduser()
PARTS = V / "parts"
VOICE, LEAD, TAIL = "Brian", 0.4, 0.8
NARRATION = json.loads(Path(os.environ.get("STANDBY_NARRATION", ROOT / "docs" / "video" / "narration.json")).read_text())
CLIPS = {k: V / v for k, v in json.loads(os.environ.get("STANDBY_CLIPS", '{"s4": "live_clip.mp4"}')).items()}
OUTPUT = os.environ.get("STANDBY_OUTPUT", "standby-demo.mp4")
BAND_CSS = """body{margin:0;width:1920px;height:200px;background:#141311;display:flex;align-items:center;justify-content:center}
.cap{font-family:-apple-system,'Helvetica Neue',sans-serif;font-size:40px;line-height:1.3;font-weight:600;color:#f1ede4;
text-align:center;padding:0 120px}"""


def run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def duration(p):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]))


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def narrate(i):
    """Polly audio for scene i, cached: re-synthesized only when the text changes."""
    sid, text = f"s{i + 1}", NARRATION[i]
    audio, stamp = PARTS / f"{sid}.mp3", PARTS / f"{sid}.txt"
    if not (audio.exists() and stamp.exists() and stamp.read_text() == text):
        run(["aws", "polly", "synthesize-speech", "--region", "us-east-1", "--engine", "generative", "--voice-id", VOICE,
             "--output-format", "mp3", "--sample-rate", "24000", "--text", text, str(audio)])
        stamp.write_text(text)
    return audio


def scene(page, i):
    sid, text = f"s{i + 1}", NARRATION[i]
    audio = narrate(i)
    a = duration(audio)
    clip = CLIPS.get(sid)
    total = max(LEAD + a + TAIL, duration(clip) / 1.25 if clip else 0)  # never speed a live clip past 1.25x
    sents = sentences(text)
    caps = []
    for k, s in enumerate(sents):
        page.set_content(f"<html><head><meta charset='utf-8'><style>{BAND_CSS}</style></head>"
                         f"<body><div class='cap'>{html.escape(s)}</div></body></html>")
        png = PARTS / f"{sid}-cap{k}.png"
        page.screenshot(path=str(png))
        caps.append(png)
    starts, t = [], LEAD
    for s in sents:
        starts.append(t)
        t += a * len(s) / sum(len(x) for x in sents)
    ends = starts[1:] + [total]
    starts[0] = 0.0
    cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", f"{total:.3f}", "-i", str(V / "slides" / f"{sid}.png")]
    filters, last, idx = [], "0:v", 1
    if clip:
        speed = max(1.0, duration(clip) / total)
        cmd += ["-i", str(clip)]
        filters.append(f"[{idx}:v]setpts=PTS/{speed},crop=1920:880:0:0[cl]")
        filters.append(f"[{last}][cl]overlay=0:0:eof_action=repeat[b0]")
        last, idx = "b0", idx + 1
    for k, png in enumerate(caps):
        cmd += ["-i", str(png)]
        filters.append(f"[{last}][{idx}:v]overlay=x=0:y=880:enable='between(t,{starts[k]:.3f},{ends[k]:.3f})'[c{k}]")
        last, idx = f"c{k}", idx + 1
    cmd += ["-i", str(audio)]
    filters.append(f"[{idx}:a]adelay={int(LEAD * 1000)}:all=1,apad=whole_dur={total:.3f}[a]")
    filters.append(f"[{last}]fps=30,format=yuv420p[v]")
    out = PARTS / f"{sid}.mp4"
    cmd += ["-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-crf", "18", "-c:a", "aac", "-ar", "48000", "-b:a", "160k", str(out)]
    run(cmd)
    print(f"{sid} narration {a:5.1f}s scene {total:5.1f}s captions {len(sents)}")
    return out


def main():
    PARTS.mkdir(parents=True, exist_ok=True)
    if "--audio-only" in sys.argv:
        for i in range(len(NARRATION)):
            a = duration(narrate(i))
            print(f"s{i + 1} narration {a:5.1f}s scene {LEAD + a + TAIL:5.1f}s")
        return
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        page = b.new_page(viewport={"width": 1920, "height": 200})
        parts = [scene(page, i) for i in range(len(NARRATION))]
        b.close()
    lst = PARTS / "list.txt"
    lst.write_text("".join(f"file '{x}'\n" for x in parts))
    joined = PARTS / "joined.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])
    d = duration(joined)
    final = V / OUTPUT
    run(["ffmpeg", "-y", "-v", "error", "-i", str(joined),
         "-vf", f"fade=t=in:st=0:d=0.5,fade=t=out:st={d - 0.6:.2f}:d=0.6",
         "-af", f"afade=t=in:st=0:d=0.3,afade=t=out:st={d - 0.6:.2f}:d=0.6",
         "-c:v", "libx264", "-crf", "18", "-c:a", "aac", "-b:a", "160k", "-map_metadata", "-1", str(final)])
    print(final, f"{duration(final):.1f}s")


if __name__ == "__main__":
    main()
