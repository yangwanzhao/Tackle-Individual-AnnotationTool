# Tackle Annotator

Local browser app for marking a tackle event window, selecting the tackler and
ball carrier, and tracking both players with Meta SAM 2. Includes positive/negative
mask corrections and review video export.

## Install

Use Python 3.10+ (3.11 recommended), Git, and FFmpeg with the libx264 encoder.
On macOS, FFmpeg can be installed with `brew install ffmpeg`; on Ubuntu use
`sudo apt install ffmpeg`. On Windows, use WSL2 for the SAM 2 backend.
Meta officially targets Linux; macOS MPS support depends on your PyTorch build.

From the cloned repository root:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Install a matching PyTorch/torchvision build for your machine using
[PyTorch's installer](https://pytorch.org/get-started/locally/).
SAM 2 requires PyTorch >= 2.5.1 and torchvision >= 0.20.1.
Then install the app dependencies (the optional CUDA extension is disabled):

```bash
SAM2_BUILD_CUDA=0 python -m pip install -r requirements.txt
curl -L --fail https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_base_plus.pt -o sam2.1_hiera_base_plus.pt
```

SAM 2 is pinned to the source revision used by the original project; its code and
weights remain subject to their upstream terms. See
[Meta's installation guide](https://github.com/facebookresearch/sam2/blob/main/INSTALL.md).
No model weights or third-party source trees are bundled here.

## Prepare your videos

Put your own videos in a separate folder, then run:

```bash
python scripts/prepare_videos.py /path/to/my-videos
```

This converts MP4/MOV/WMV/AVI/MKV files to constant 30 fps H.264 MP4s without audio
and generates the manifest expected by the app. Source files are untouched.
`--fps 25` selects a different frame rate. The script refuses to replace an
existing dataset. Use only videos you are authorized to process.

Advanced users can create `data/processed/individual_tackles/labels/all.jsonl`
manually, one JSON object per line:

```json
{"id":"individual_tackle_001","clip_path":"data/processed/individual_tackles/clips/individual_tackle_001.mp4","normalized_media":{"frame_rate":"30/1"}}
```

IDs should contain only letters, digits, `_` and `-`. Video paths are relative
to the repository root and must stay inside it. Use constant-frame-rate videos
and specify their true frame rate. An optional `scores` object is displayed as
reference information; it is not needed for tracking.

## Run

```bash
python tools/individual_tackle_annotator/server.py
```

Open **http://127.0.0.1:8766**. Keep the terminal running while using the app.
The model loads only when tracking is first requested. `--device auto` (default)
selects CUDA, then Apple MPS, then CPU. Override with `--device cpu` if needed;
CPU inference can be slow. The UI can open without loading the model.

This is a local, single-user Python service. GitHub hosts the source; GitHub Pages
cannot run the Python/SAM 2 backend. It has no authentication or multi-user job
isolation, so keep the default loopback address. Remote access should use a
trusted SSH tunnel, for example `ssh -L 8766:127.0.0.1:8766 user@gpu-host`.

## Workflow

1. Pause at the central conflict frame, set seconds on either side, and select
   **Set conflict frame**.
2. Pick a clear frame inside that window and draw both role boxes on that same
   frame. Choose a frame before the players overlap when possible.
3. Select **Run SAM 2 tracking** to propagate masks in both directions.
4. Inspect the red tackler and blue carrier masks. Add positive/negative clicks
   on problem frames and rerun tracking.
5. Optionally select **Generate review video**.

Arrow keys move one frame; Space toggles playback. **Go to box frame** returns
to the initialization frame. Prompts are saved to disk when tracking completes;
unsaved edits in the browser are lost on refresh. Rerunning replaces saved masks.

## Outputs

Each sample is saved under
`data/processed/individual_tackles/tracking_annotations/<id>/`:

- `annotation.json`: conflict frame, inclusive window bounds, initialization
  frame and two `bbox_xyxy` boxes in original pixel coordinates, correction
  clicks, source path, fps, status, and mask paths. Frames are zero-based.
- `masks/tackler/*.png` and `masks/ball_carrier/*.png`: full-resolution binary
  masks, background 0 / player 255. Filenames use original video frame numbers.
- `review_overlay.mp4`: optional event-window video with both masks overlaid.

The `.frame_cache` folder can be regenerated. Back up your output folders yourself;
all data and outputs are intentionally ignored by Git. The app saves initialization
boxes, not per-frame bounding boxes. Pose/keypoint extraction, scoring labels,
and downstream feature computation are outside this standalone package.

## Troubleshooting

- Connection refused: start the server and leave its terminal running.
- Empty list: prepare a dataset and refresh the browser.
- Missing checkpoint: download the `.pt` file into the repository root.
- `ffmpeg` missing: install it and ensure it is on PATH.
- MPS operator failure: restart with `--device cpu`.
- Port already in use: run with `--port 8767` and open that port.

## Publication contents

Only the web app, video preparation script, dependency manifest and documentation
are distributed. Original research videos, human ratings, masks, checkpoints,
experiments, reports, local environments and credentials are excluded.

## License

The original app code and documentation are available under the [MIT License](LICENSE).
Copyright (c) 2026 wanzhao yang.
Third-party dependencies and model weights, including SAM 2, remain subject to
their respective licenses and terms.
