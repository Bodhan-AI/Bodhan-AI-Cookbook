"""Build a small synthetic test video: speech-like bursts (150 Hz), silence, and a quiet music bed."""
import subprocess
from pathlib import Path


def make_video(path: Path, bursts=((1.0, 4.0), (6.0, 11.0), (13.0, 16.0)), duration=18.0, music=True, texts=()) -> Path:
    """texts: (text, x, y, start, end) drawn in white on a dark background, for the on-screen-text tests."""
    expr = "+".join(f"0.5*between(t,{a},{b})*(0.6+0.4*sin(2*PI*4*t))*sin(2*PI*150*t)" for a, b in bursts)
    if music:
        expr += "+0.05*sin(2*PI*660*t)"
    src = f"testsrc=size=320x240:rate=25:duration={duration}"
    if texts:
        font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        src = f"color=c=0x181818:s=640x360:r=25:d={duration}," + ",".join(
            f"drawtext=fontfile={font}:text='{t}':x={px}:y={py}:fontsize=30:fontcolor=white:enable='between(t,{a},{b})'"
            for t, px, py, a, b in texts)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", src,
           "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={duration}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", str(path)]
    subprocess.run(cmd, check=True)
    return path
