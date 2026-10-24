#!/usr/bin/env python3
"""Normalize user-supplied videos and create the annotator manifest."""
import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("--fps", type=int, default=30)
    args = parser.parse_args()
    if args.fps <= 0:
        parser.error("--fps must be positive")
    videos = sorted(p for p in args.input_dir.iterdir()
                    if p.suffix.lower() in {".mp4", ".mov", ".wmv", ".avi", ".mkv"})
    if not videos:
        parser.error("No supported videos found in input_dir")
    base = ROOT / "data/processed/individual_tackles"
    manifest = base / "labels/all.jsonl"
    clips = base / "clips"
    if manifest.exists() or clips.exists():
        parser.error("Dataset already exists; use a fresh checkout to avoid replacing annotations")
    clips.mkdir(parents=True)
    rows = []
    for i, source in enumerate(videos, 1):
        sample_id = f"individual_tackle_{i:03d}"
        destination = clips / f"{sample_id}.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-n",
                        "-i", str(source.resolve()), "-an", "-vf",
                        f"fps={args.fps},scale=trunc(iw/2)*2:trunc(ih/2)*2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", str(destination)], check=True)
        rows.append({"id": sample_id, "clip_path": destination.relative_to(ROOT).as_posix(),
                     "normalized_media": {"frame_rate": f"{args.fps}/1"}})
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    print(f"Prepared {len(rows)} videos: {manifest}")

if __name__ == "__main__":
    main()
