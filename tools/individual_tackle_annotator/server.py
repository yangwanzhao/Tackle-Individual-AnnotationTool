#!/usr/bin/env python3
"""Local annotator for tackler/carrier selection and SAM 2 mask propagation."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import threading
import warnings
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

# Some PyTorch/TorchVision operators used by the models are not implemented on
# Apple MPS. Allow those individual operations to fall back to CPU while SAM 2
# continues to use MPS for supported operations. This must be set before torch
# or torchvision is imported lazily below.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

# SAM 2's optional CUDA connected-components extension is not built on macOS.
# Its own fallback remains active, so hide only that known informational warning.
warnings.filterwarnings(
    "ignore",
    message=r"(?s).*Skipping the post-processing step due to the error above.*",
    category=UserWarning,
    module=r"sam2\.utils\.transforms",
)


ROOT = Path(__file__).resolve().parents[2]
TOOL_DIR = Path(__file__).resolve().parent
DEFAULT_LABELS = ROOT / "data/processed/individual_tackles/labels/all.jsonl"
DEFAULT_OUTPUT = ROOT / "data/processed/individual_tackles/tracking_annotations"
DEFAULT_SAM2_CHECKPOINT = ROOT / "sam2.1_hiera_base_plus.pt"
DEFAULT_SAM2_CONFIG = "configs/sam2.1/sam2.1_hiera_b+.yaml"
DEFAULT_DEVICE = "auto"


def resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"

ROLE_IDS = {"tackler": 1, "ball_carrier": 2}


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def repo_path(path_value: str) -> Path:
    candidate = (ROOT / path_value).resolve()
    if not candidate.is_relative_to(ROOT):
        raise ValueError("path escapes repository root")
    return candidate


def annotation_path(output_dir: Path, sample_id: str) -> Path:
    return output_dir / sample_id / "annotation.json"


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def frame_rate(label: dict) -> float:
    value = str(label.get("normalized_media", {}).get("frame_rate", "30/1"))
    numerator, denominator = value.split("/", 1)
    return float(numerator) / float(denominator)


class ModelRuntime:
    def __init__(self) -> None:
        self.sam = None
        self.lock = threading.Lock()

    def sam_predictor(self, config: str, checkpoint: Path, device: str):
        try:
            from sam2.build_sam import build_sam2_video_predictor
        except ImportError as exc:
            raise RuntimeError(
                "Meta SAM 2 is missing. Install it from https://github.com/facebookresearch/sam2."
            ) from exc
        with self.lock:
            if self.sam is None:
                self.sam = build_sam2_video_predictor(config, str(checkpoint), device=resolve_device(device))
        return self.sam


RUNTIME = ModelRuntime()


class AnnotatorHandler(BaseHTTPRequestHandler):
    labels_path = DEFAULT_LABELS
    output_dir = DEFAULT_OUTPUT
    cache_dir = DEFAULT_OUTPUT / ".frame_cache"
    sam2_config = DEFAULT_SAM2_CONFIG
    sam2_checkpoint: Path | None = DEFAULT_SAM2_CHECKPOINT
    device = DEFAULT_DEVICE

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")

    def send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def request_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self.serve_static("index.html")
        if parsed.path.startswith("/static/"):
            return self.serve_static(parsed.path.removeprefix("/static/"))
        if parsed.path == "/api/items":
            return self.api_items()
        if parsed.path == "/media":
            return self.serve_media(parsed)
        if parsed.path == "/mask":
            return self.serve_mask(parsed)
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/propagate":
                return self.api_propagate()
            if parsed.path == "/api/export-review":
                return self.api_export_review()
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def serve_static(self, name: str) -> None:
        path = (TOOL_DIR / name).resolve()
        if not path.is_relative_to(TOOL_DIR) or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def labels_by_id(self) -> dict[str, dict]:
        return {row["id"]: row for row in read_jsonl(self.labels_path)}

    def api_items(self) -> None:
        items = read_jsonl(self.labels_path)
        saved = 0
        for item in items:
            item["media_url"] = f"/media?path={quote(item['clip_path'])}"
            path = annotation_path(self.output_dir, item["id"])
            item["tracking_annotation"] = json.loads(path.read_text()) if path.exists() else None
            saved += int(path.exists())
        self.send_json(
            {
                "items": items,
                "saved_count": saved,
                "output_dir": display_path(self.output_dir),
                "sam2_ready": self.sam2_checkpoint is not None and self.sam2_checkpoint.is_file(),
            }
        )

    def validate_annotation(self, payload: dict) -> dict:
        sample_id = payload.get("id")
        labels = self.labels_by_id()
        if sample_id not in labels:
            raise ValueError("unknown dataset id")
        initialization = payload.get("initialization", {})
        for role in ROLE_IDS:
            box = initialization.get(role, {}).get("bbox_xyxy")
            if not isinstance(box, list) or len(box) != 4:
                raise ValueError(f"missing {role} initialization box")
        frame = int(initialization.get("frame", -1))
        event_frame = payload.get("event_frame", payload.get("contact_frame"))
        window = payload.get("event_window")
        if frame < 0:
            raise ValueError("invalid initialization frame")
        if event_frame is None or int(event_frame) < 0:
            raise ValueError("missing or invalid conflict frame")
        if not isinstance(window, dict):
            raise ValueError("missing event window")
        start_frame = int(window.get("start_frame", -1))
        end_frame = int(window.get("end_frame", -1))
        if (
            start_frame < 0
            or end_frame < start_frame
            or not start_frame <= int(event_frame) <= end_frame
            or not start_frame <= frame <= end_frame
        ):
            raise ValueError("invalid event window or box frame outside event window")
        for correction in payload.get("corrections", []):
            correction_frame = int(correction.get("frame", -1))
            if not start_frame <= correction_frame <= end_frame:
                raise ValueError("correction frame outside event window")
            if correction.get("role") not in ROLE_IDS:
                raise ValueError("invalid correction role")
        payload["event_frame"] = int(event_frame)
        payload["event_window"]["start_frame"] = start_frame
        payload["event_window"]["end_frame"] = end_frame
        payload["schema_version"] = 2
        payload["video_path"] = labels[sample_id]["clip_path"]
        payload["fps"] = frame_rate(labels[sample_id])
        payload.setdefault("corrections", [])
        payload.setdefault("status", "draft")
        return payload

    def ensure_frames(self, label: dict, start_frame: int, end_frame: int) -> Path:
        directory = self.cache_dir / f"{label['id']}_{start_frame:06d}_{end_frame:06d}"
        marker = directory / ".complete"
        if marker.exists():
            return directory
        directory.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(repo_path(label["clip_path"])),
                "-vf", f"select=between(n\\,{start_frame}\\,{end_frame})",
                "-vsync", "0",
                "-start_number", "0", "-q:v", "2", str(directory / "%06d.jpg"),
            ],
            check=True,
        )
        marker.touch()
        return directory

    def apply_prompts(self, predictor, state, annotation: dict) -> None:
        import numpy as np

        start_frame = int(annotation["event_window"]["start_frame"])
        init_frame = int(annotation["initialization"]["frame"]) - start_frame
        for role, object_id in ROLE_IDS.items():
            box = np.asarray(annotation["initialization"][role]["bbox_xyxy"], dtype=np.float32)
            predictor.add_new_points_or_box(
                inference_state=state,
                frame_idx=init_frame,
                obj_id=object_id,
                box=box,
            )
        for correction in sorted(annotation.get("corrections", []), key=lambda row: row["frame"]):
            positive = correction.get("positive_points", [])
            negative = correction.get("negative_points", [])
            points = positive + negative
            if not points:
                continue
            labels = [1] * len(positive) + [0] * len(negative)
            predictor.add_new_points_or_box(
                inference_state=state,
                frame_idx=int(correction["frame"]) - start_frame,
                obj_id=ROLE_IDS[correction["role"]],
                points=np.asarray(points, dtype=np.float32),
                labels=np.asarray(labels, dtype=np.int32),
            )

    def export_propagation(self, predictor, state, annotation: dict, sample_dir: Path) -> int:
        try:
            import cv2
            import torch
        except ImportError as exc:
            raise RuntimeError("OpenCV and PyTorch are required for propagation.") from exc

        masks_by_frame: dict[int, dict[int, object]] = {}
        start_frame = int(annotation["event_window"]["start_frame"])
        init_frame = int(annotation["initialization"]["frame"]) - start_frame
        directions = [
            {"start_frame_idx": init_frame, "reverse": False},
            {"start_frame_idx": init_frame, "reverse": True},
        ]
        with torch.inference_mode():
            for options in directions:
                for frame_idx, object_ids, logits in predictor.propagate_in_video(state, **options):
                    frame_masks = masks_by_frame.setdefault(int(frame_idx), {})
                    for index, object_id in enumerate(object_ids):
                        frame_masks[int(object_id)] = (logits[index] > 0.0).cpu().numpy().squeeze()

        for role, object_id in ROLE_IDS.items():
            role_dir = sample_dir / "masks" / role
            role_dir.mkdir(parents=True, exist_ok=True)
            for frame_idx, frame_masks in masks_by_frame.items():
                mask = frame_masks.get(object_id)
                if mask is not None:
                    source_frame_idx = frame_idx + start_frame
                    cv2.imwrite(str(role_dir / f"{source_frame_idx:06d}.png"), mask.astype("uint8") * 255)
        return len(masks_by_frame)

    def api_propagate(self) -> None:
        annotation = self.validate_annotation(self.request_json())
        if self.sam2_checkpoint is None or not self.sam2_checkpoint.is_file():
            raise RuntimeError("SAM 2 checkpoint is not configured or does not exist")
        label = self.labels_by_id()[annotation["id"]]
        start_frame = int(annotation["event_window"]["start_frame"])
        end_frame = int(annotation["event_window"]["end_frame"])
        frames_dir = self.ensure_frames(label, start_frame, end_frame)
        predictor = RUNTIME.sam_predictor(self.sam2_config, self.sam2_checkpoint, self.device)
        state = predictor.init_state(video_path=str(frames_dir))
        predictor.reset_state(state)
        self.apply_prompts(predictor, state, annotation)
        sample_dir = self.output_dir / annotation["id"]
        for role in ROLE_IDS:
            role_dir = sample_dir / "masks" / role
            if role_dir.exists():
                for old_mask in role_dir.glob("*.png"):
                    old_mask.unlink()
        count = self.export_propagation(predictor, state, annotation, sample_dir)
        annotation["status"] = "masks_generated"
        annotation["frame_count"] = count
        annotation["masks"] = {
            "format": "binary_png",
            "background_value": 0,
            "foreground_value": 255,
            "tackler": "masks/tackler",
            "ball_carrier": "masks/ball_carrier",
        }
        write_json(sample_dir / "annotation.json", annotation)
        self.send_json({"ok": True, "frame_count": count, "start_frame": start_frame, "end_frame": end_frame})

    def api_export_review(self) -> None:
        request = self.request_json()
        sample_id = request.get("id")
        labels = self.labels_by_id()
        if sample_id not in labels:
            raise ValueError("unknown dataset id")
        saved_path = annotation_path(self.output_dir, sample_id)
        if not saved_path.is_file():
            raise ValueError("complete SAM 2 tracking before generating a review video")
        annotation = json.loads(saved_path.read_text(encoding="utf-8"))
        if annotation.get("status") != "masks_generated" or not annotation.get("event_window"):
            raise ValueError("the saved annotation does not contain completed masks")

        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("OpenCV and NumPy are required for review video export") from exc

        start_frame = int(annotation["event_window"]["start_frame"])
        end_frame = int(annotation["event_window"]["end_frame"])
        expected_frames = list(range(start_frame, end_frame + 1))
        sample_dir = self.output_dir / sample_id
        for role in ROLE_IDS:
            missing = [
                frame for frame in expected_frames
                if not (sample_dir / "masks" / role / f"{frame:06d}.png").is_file()
            ]
            if missing:
                raise ValueError(f"{role} is missing {len(missing)} mask files; rerun SAM 2 first")

        frames_dir = self.ensure_frames(labels[sample_id], start_frame, end_frame)
        first_frame = cv2.imread(str(frames_dir / "000000.jpg"), cv2.IMREAD_COLOR)
        if first_frame is None:
            raise RuntimeError("could not read cached event frames")
        height, width = first_frame.shape[:2]
        opacity = max(0.05, min(0.9, float(request.get("opacity", 0.45))))
        output_path = sample_dir / "review_overlay.mp4"
        temporary_path = sample_dir / "review_overlay.tmp.mp4"
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pixel_format", "bgr24",
            "-video_size", f"{width}x{height}", "-framerate", str(annotation["fps"]),
            "-i", "pipe:0", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(temporary_path),
        ]
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
        colors = {"tackler": np.asarray([72, 54, 255]), "ball_carrier": np.asarray([255, 141, 35])}
        try:
            for local_index, source_frame in enumerate(expected_frames):
                frame = cv2.imread(str(frames_dir / f"{local_index:06d}.jpg"), cv2.IMREAD_COLOR)
                if frame is None:
                    raise RuntimeError(f"could not read event frame {source_frame}")
                for role, color in colors.items():
                    mask = cv2.imread(
                        str(sample_dir / "masks" / role / f"{source_frame:06d}.png"),
                        cv2.IMREAD_GRAYSCALE,
                    )
                    selected = mask > 0
                    frame[selected] = (
                        frame[selected].astype(np.float32) * (1.0 - opacity) + color * opacity
                    ).astype(np.uint8)
                process.stdin.write(frame.tobytes())
            process.stdin.close()
            return_code = process.wait()
        except Exception:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.terminate()
            process.wait()
            raise
        if return_code != 0:
            raise RuntimeError("ffmpeg failed to encode the review video")
        temporary_path.replace(output_path)
        relative_path = output_path.resolve().relative_to(ROOT)
        self.send_json(
            {
                "ok": True,
                "frame_count": len(expected_frames),
                "url": f"/media?path={quote(str(relative_path))}",
                "path": display_path(output_path),
            }
        )

    def serve_mask(self, parsed) -> None:
        query = parse_qs(parsed.query)
        sample_id = query.get("id", [""])[0]
        role = query.get("role", [""])[0]
        frame = int(query.get("frame", ["0"])[0])
        if sample_id not in self.labels_by_id() or role not in ROLE_IDS:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        path = self.output_dir / sample_id / "masks" / role / f"{frame:06d}.png"
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_media(self, parsed) -> None:
        raw_path = unquote(parse_qs(parsed.query).get("path", [""])[0])
        try:
            path = repo_path(raw_path)
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        size = path.stat().st_size
        start, end, status = 0, size - 1, HTTPStatus.OK
        range_header = self.headers.get("Range")
        if range_header:
            try:
                start_text, end_text = range_header.split("=", 1)[1].split("-", 1)
                start = int(start_text) if start_text else 0
                end = min(int(end_text) if end_text else size - 1, size - 1)
                status = HTTPStatus.PARTIAL_CONTENT
            except ValueError:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as file:
            file.seek(start)
            remaining = length
            while remaining:
                chunk = file.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
                remaining -= len(chunk)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sam2-config", default=DEFAULT_SAM2_CONFIG)
    parser.add_argument("--sam2-checkpoint", type=Path, default=DEFAULT_SAM2_CHECKPOINT)
    parser.add_argument("--device", choices=["auto", "cuda", "mps", "cpu"], default=DEFAULT_DEVICE)
    args = parser.parse_args()

    def resolve(path: Path) -> Path:
        return path.resolve() if path.is_absolute() else (ROOT / path).resolve()

    AnnotatorHandler.labels_path = resolve(args.labels)
    AnnotatorHandler.output_dir = resolve(args.output_dir)
    AnnotatorHandler.cache_dir = AnnotatorHandler.output_dir / ".frame_cache"
    AnnotatorHandler.sam2_config = args.sam2_config
    AnnotatorHandler.sam2_checkpoint = resolve(args.sam2_checkpoint)
    AnnotatorHandler.device = args.device

    server = ThreadingHTTPServer((args.host, args.port), AnnotatorHandler)
    print(f"Individual tackle annotator: http://{args.host}:{args.port}")
    print(f"Labels: {AnnotatorHandler.labels_path}")
    print(f"Output: {AnnotatorHandler.output_dir}")
    print(f"SAM 2 device: {args.device}")
    print(f"SAM 2 checkpoint: {AnnotatorHandler.sam2_checkpoint or 'not configured'}")
    server.serve_forever()


if __name__ == "__main__":
    main()
