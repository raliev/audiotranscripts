#!/usr/bin/env python3
"""Audio/video files as a source: probing, a time range, frames pulled out of the video.

Everything goes through ffmpeg (native_helpers.ffmpeg_exe), so any container it reads works:
mp3, m4a, wav, mp4, mov, mkv, webm...  Times are seconds in the file.

    python media_file.py talk.mp4            # duration and whether there is a video stream
    python media_file.py talk.mp4 --frame 75 --out /tmp/f.png
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import native_helpers

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


def parse_time(value: str | float | None) -> float | None:
    """'90', '90.5', '1:30', '01:02:03.5' → seconds. None / '' → None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    parts = str(value).strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"bad time: {value!r}")
    try:
        sec = 0.0
        for p in parts:
            sec = sec * 60 + float(p)
    except ValueError:
        raise ValueError(f"bad time: {value!r} (expected seconds, MM:SS or HH:MM:SS)") from None
    if sec < 0:
        raise ValueError(f"bad time: {value!r}")
    return sec


def fmt_time(sec: float) -> str:
    sec = max(0.0, sec)
    h, rem = divmod(int(sec), 3600)
    return f"{h:02d}:{rem // 60:02d}:{rem % 60:02d}"


def _ffmpeg() -> str:
    exe = native_helpers.ffmpeg_exe()
    if not exe:
        raise RuntimeError("ffmpeg not found: install it (https://ffmpeg.org) or pip install imageio-ffmpeg")
    return exe


@dataclass
class MediaInfo:
    duration: float | None      # seconds; None if ffmpeg couldn't tell
    video: bool                 # has a (real) video stream, not just cover art
    width: int = 0
    height: int = 0


def probe(path: Path) -> MediaInfo:
    """Duration and video stream, from `ffmpeg -i` (no ffprobe needed: imageio-ffmpeg has none)."""
    r = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(path)], stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    err = r.stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None
    video, w, h = False, 0, 0
    for line in err.splitlines():
        if "Stream #" in line and "Video:" in line and "attached pic" not in line:
            video = True
            size = re.search(r",\s*(\d{2,5})x(\d{2,5})", line)
            if size:
                w, h = int(size.group(1)), int(size.group(2))
            break
    if duration is None and "Invalid data" in err:
        raise RuntimeError(err.strip().splitlines()[-1])
    return MediaInfo(duration, video, w, h)


def range_args(start: float, end: float | None) -> list[str]:
    """ffmpeg input options for [start, end): fast input seek, then a duration."""
    out = ["-ss", f"{start:.3f}"] if start > 0 else []
    if end is not None:
        out += ["-t", f"{max(0.0, end - start):.3f}"]
    return out


def grab_frame(path: Path, at: float, out: Path) -> Path:
    """One frame at *at* seconds, full resolution, lossless PNG."""
    r = subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-ss", f"{max(0.0, at):.3f}", "-i", str(path),
                        "-frames:v", "1", "-an", str(out)],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(r.stderr.strip() or f"no frame at {at:.1f}s")
    return out


class FrameReader:
    """Frames of [start, end) every *interval* seconds, full resolution, as JPEG bytes.

    One ffmpeg process decodes the range and writes MJPEG (quality 2: visually lossless)
    to a pipe; frames are split on the JPEG end marker (FFD9 can't occur inside the
    entropy-coded data, where 0xFF is always followed by 0x00 or a restart marker).
    Yields (t, jpeg_bytes) with t = seconds from *start*.
    """

    def __init__(self, path: Path, start: float, end: float | None, interval: float):
        self.interval = interval
        self.proc = subprocess.Popen(
            [_ffmpeg(), "-loglevel", "error", "-nostdin", *range_args(start, end), "-i", str(path),
             "-an", "-sn", "-vf", f"fps=1/{interval:g}", "-q:v", "2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._err: list[str] = []
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        for line in self.proc.stderr:
            self._err.append(line.decode("utf-8", "replace").strip())

    def __iter__(self):
        buf = b""
        k = 0
        while True:
            chunk = self.proc.stdout.read1(1 << 20)
            if not chunk:
                break
            buf += chunk
            while True:
                soi = buf.find(b"\xff\xd8")
                eoi = buf.find(b"\xff\xd9", soi + 2) if soi >= 0 else -1
                if eoi < 0:
                    break
                yield k * self.interval, buf[soi:eoi + 2]
                k += 1
                buf = buf[eoi + 2:]

    @property
    def error(self) -> str:
        return "\n".join(x for x in self._err if x)

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", type=Path)
    ap.add_argument("--frame", type=parse_time, help="Save the frame at this time")
    ap.add_argument("--out", type=Path, default=Path("frame.png"))
    a = ap.parse_args()
    info = probe(a.file)
    print(f"duration={info.duration} video={info.video} size={info.width}x{info.height}")
    if a.frame is not None:
        print(grab_frame(a.file, a.frame, a.out))


if __name__ == "__main__":
    sys.exit(main())
