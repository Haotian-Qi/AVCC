"""
Detect and track faces in videos with InsightFace (buffalo_l: RetinaFace detector + ArcFace embeddings)
and write one CSV per video: frame_idx,speaker_id,x1,y1,x2,y2 (0-based frames, integer pixels).

The number of people is taken from the best of a few sampled frames (up to --max_speakers, largest
faces first). Each person gets a track anchored on their face embedding; every frame, detections are
assigned to tracks by Hungarian matching on cosine distance plus a small spatial term.

Usage:
  python face_detection.py -i video.mp4 -o out/
  python face_detection.py -i videos/ -o out/ --max_speakers 2
"""
import argparse
import os
import random

import cv2
import numpy as np
import pandas as pd
from insightface.app import FaceAnalysis
from scipy.optimize import linear_sum_assignment
from tqdm import tqdm

# -----------------------------
# Settings
# -----------------------------
DET_SCORE_THRESHOLD = 0.62

# Detections smaller than this fraction of the largest face in the frame are ignored.
MIN_REL_AREA = 0.10

# Max cosine distance (+ spatial term) allowed for a detection to be matched to a track.
# Anything worse is rejected instead of being forced onto the nearest track, so a track
# does not snap onto an unrelated face or object once the real face leaves the frame.
MAX_MATCH_COST = 0.55

# Weight of the spatial term, and the pixel distance that counts as 1.0 of it.
SPATIAL_WEIGHT = 0.2
SPATIAL_SCALE = 200.0

# How long (in frames) a track keeps its last bbox with no matching detection before
# it is considered lost. Nothing is recorded for a track while it is missing.
TRACK_MAX_MISSED = 30

# Weight of the fixed anchor embedding vs. the running (EMA) embedding when matching.
# Keeping some anchor weight prevents identity drift while still adapting to pose/lighting.
ANCHOR_BLEND = 0.5
EMA_MOMENTUM = 0.7

# Histogram correlation below this between consecutive frames is a scene cut: spatial
# cost is dropped for that frame and matching relies on embeddings only.
SCENE_CUT_HIST_CORR = 0.5


# -----------------------------
# Models
# -----------------------------
def init_models(det_size=640):
    # Only detection and recognition are used; skipping landmarks/genderage saves time.
    app = FaceAnalysis(
        name='buffalo_l',
        allowed_modules=['detection', 'recognition'],
        providers=['CUDAExecutionProvider', 'CPUExecutionProvider'],
    )
    # An explicit det_size avoids running detection at two input sizes per frame.
    app.prepare(ctx_id=0, det_size=(det_size, det_size))
    return app


def l2_normalize(x, axis=-1, epsilon=1e-10):
    return x / np.maximum(np.linalg.norm(x, axis=axis, keepdims=True), epsilon)


def cosine_distance(a, b):
    a = l2_normalize(np.asarray(a), axis=1)
    b = l2_normalize(np.asarray(b), axis=1)
    return 1 - a @ b.T


def center(box):
    return np.array([(box[0] + box[2]) / 2, (box[1] + box[3]) / 2])


# -----------------------------
# Detection
# -----------------------------
def detect(app, frame, max_faces, threshold):
    """Largest faces above threshold: (embeddings, boxes) with boxes clipped to the frame."""
    faces = [f for f in app.get(frame) if f['det_score'] >= threshold and f.embedding is not None]
    if not faces:
        return [], []
    area = lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])
    max_area = max(area(f) for f in faces)
    faces = [f for f in faces if area(f) >= MIN_REL_AREA * max_area]
    faces = sorted(faces, key=area, reverse=True)[:max_faces]

    h, w = frame.shape[:2]
    embs, boxes = [], []
    for f in faces:
        x1, y1, x2, y2 = map(int, f.bbox)
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            continue
        embs.append(f.embedding)
        boxes.append([x1, y1, x2, y2])
    return embs, boxes


def init_embeddings(app, video_path, max_speakers, threshold, nframes=50):
    """Anchor embeddings from the sampled frame with the most faces (ties: highest total score)."""
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    best = None
    for idx in sorted(random.sample(range(total), min(nframes, total))):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        faces = [f for f in app.get(frame) if f['det_score'] >= threshold]
        faces = sorted(faces, key=lambda f: f.bbox[2] - f.bbox[0], reverse=True)[:max_speakers]
        if not faces:
            continue
        key = (len(faces), sum(f['det_score'] for f in faces))
        if best is None or key > best[0]:
            best = (key, faces)
    cap.release()
    if best is None:
        raise ValueError(f"{video_path}: no face above threshold in {nframes} sampled frames")
    return [f.embedding for f in best[1]]


def is_scene_cut(prev_frame, frame, threshold=SCENE_CUT_HIST_CORR):
    if prev_frame is None:
        return False
    hists = []
    for f in (prev_frame, frame):
        gray = cv2.cvtColor(cv2.resize(f, (64, 64)), cv2.COLOR_BGR2GRAY)
        h = cv2.calcHist([gray], [0], None, [32], [0, 256])
        cv2.normalize(h, h)
        hists.append(h)
    return cv2.compareHist(hists[0], hists[1], cv2.HISTCMP_CORREL) < threshold


# -----------------------------
# Tracking
# -----------------------------
class Track:
    def __init__(self, speaker_id, anchor_emb):
        self.speaker_id = speaker_id
        self.anchor_emb = l2_normalize(anchor_emb)  # fixed, never overwritten
        self.emb = self.anchor_emb                  # running embedding, EMA-updated on each match
        self.bbox = None
        self.missed = 0                             # consecutive frames without a match

    def reference_emb(self):
        return l2_normalize(ANCHOR_BLEND * self.anchor_emb + (1 - ANCHOR_BLEND) * self.emb)

    def update(self, emb, bbox):
        self.emb = l2_normalize(EMA_MOMENTUM * self.emb + (1 - EMA_MOMENTUM) * l2_normalize(emb))
        self.bbox = bbox
        self.missed = 0

    def mark_missed(self):
        self.missed += 1
        if self.missed > TRACK_MAX_MISSED:
            self.bbox = None  # lost: stop using a stale position


def track_video(app, video_path, max_speakers, threshold):
    anchors = init_embeddings(app, video_path, max_speakers, threshold)
    tracks = [Track(i, e) for i, e in enumerate(anchors)]

    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    records = []
    prev_frame = None
    for i in tqdm(range(total), unit="frame", desc=os.path.basename(video_path)):
        ok, frame = cap.read()
        if not ok:
            break
        scene_cut = is_scene_cut(prev_frame, frame)
        prev_frame = frame

        embs, boxes = detect(app, frame, len(tracks), threshold)
        matched = set()
        if embs:
            cost = cosine_distance(embs, [t.reference_emb() for t in tracks])
            if not scene_cut:
                for r, b in enumerate(boxes):
                    for c, t in enumerate(tracks):
                        d = 1.0 if t.bbox is None else np.linalg.norm(center(b) - center(t.bbox)) / SPATIAL_SCALE
                        cost[r][c] += SPATIAL_WEIGHT * d
            for r, c in zip(*linear_sum_assignment(cost)):
                if cost[r][c] > MAX_MATCH_COST:
                    continue
                tracks[c].update(embs[r], boxes[r])
                matched.add(c)
                x1, y1, x2, y2 = boxes[r]
                records.append({"frame_idx": i, "speaker_id": tracks[c].speaker_id,
                                "x1": x1, "y1": y1, "x2": x2, "y2": y2})
        for c, t in enumerate(tracks):
            if c not in matched:
                t.mark_missed()
    cap.release()
    return records


def write_csv(records, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    df = pd.DataFrame(records, columns=["frame_idx", "speaker_id", "x1", "y1", "x2", "y2"])
    df.sort_values(["frame_idx", "speaker_id"]).to_csv(path, index=False)
    print(f"Saved {len(df)} rows to {path}")


# -----------------------------
# Run
# -----------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-i", required=True, help="A video file, or a directory of videos")
    parser.add_argument("-o", required=True, help="Output directory; writes <video name>.csv")
    parser.add_argument("--max_speakers", type=int, default=3, help="Maximum number of faces to track")
    parser.add_argument("--threshold", type=float, default=DET_SCORE_THRESHOLD, help="Detection score threshold")
    parser.add_argument("--det_size", type=int, default=640, help="Detector input size")
    parser.add_argument("--seed", type=int, default=0, help="Seed for the frames sampled at initialisation")
    args = parser.parse_args()

    exts = ('.mp4', '.mkv', '.avi', '.mov', '.webm')
    if os.path.isdir(args.i):
        videos = [os.path.join(args.i, f) for f in sorted(os.listdir(args.i)) if f.lower().endswith(exts)]
    else:
        videos = [args.i]
    if not videos:
        raise ValueError(f"no videos found in {args.i}")

    app = init_models(args.det_size)
    for path in videos:
        random.seed(args.seed)
        records = track_video(app, path, args.max_speakers, args.threshold)
        name = os.path.splitext(os.path.basename(path))[0]
        write_csv(records, os.path.join(args.o, f"{name}.csv"))
