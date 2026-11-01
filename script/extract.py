import argparse
import csv
import io
import json
import math
import subprocess
import tarfile
import wave
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "val", "test")
WINDOWS_BY_SPLIT = {"train": "train", "val": "val", "test": "events"}
FPS = 25.0
FACE_SIZE = 112
BLANK_FACE = 128
SAMPLE_RATE = 16000
SAMPLES_PER_FRAME = 640
TARGET_RMS_DBFS = -25.0
MAX_GAIN_DB = 20.0
PEAK_LIMIT = 0.99
CHUNK = 1 << 20


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT, help="AVCC folder")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--format", choices=("raw", "webdataset", "both"), default="webdataset")
    parser.add_argument("--split", action="append", choices=SPLITS, help="repeatable, default all")
    parser.add_argument("--video", action="append", help="repeatable")
    parser.add_argument("--seg", action="append", help="repeatable")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--windows",
        choices=("split", "train", "val", "events"),
        default="split",
        help="split: train windows on train, val windows on val, event windows on test",
    )
    parser.add_argument("--window", type=float, default=30.0, help="window length in seconds")
    parser.add_argument("--hop", type=float, default=20.0, help="step between train windows in seconds")
    parser.add_argument("--context", type=float, default=2.0, help="unscored history before each val window in seconds")
    parser.add_argument("--event-window", type=float, default=10.0, help="history before each test event in seconds")
    parser.add_argument("--shard-size", type=float, default=1.0, help="GB per webdataset shard")
    parser.add_argument("--color", choices=("gray", "rgb"), default="gray", help="gray is what MuVAP uses")
    parser.add_argument("--size", type=int, default=FACE_SIZE, help="face crop side in pixels")
    parser.add_argument("--image", choices=("jpg", "png"), default="jpg")
    parser.add_argument("--jpeg-quality", type=int, default=95)
    return parser.parse_args()


def read_splits(path):
    with path.open(newline="") as f:
        return {row["videoID"]: row["split"] for row in csv.DictReader(f, delimiter="\t")}


def read_events(path):
    events = defaultdict(list)
    if not path.exists():
        return events
    for line in path.read_text().splitlines():
        fields = line.split()
        if not fields:
            continue
        video, seg, prev_speaker, start, duration, next_speaker, kind = fields[:7]
        events[(video, seg)].append(
            {
                "type": kind,
                "prev_speaker": str(int(prev_speaker)),
                "next_speaker": str(int(next_speaker)),
                "pause_start": float(start),
                "pause_duration": float(duration),
            }
        )
    return events


def read_boxes(path):
    boxes = defaultdict(dict)
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            box = tuple(float(row[k]) for k in ("x1", "y1", "x2", "y2"))
            boxes[int(row["frame_idx"])][str(int(row["speaker_id"]))] = box
    return boxes


def read_rttm(path, frames, fps):
    activity = {}
    for line in path.read_text().splitlines():
        fields = line.split()
        if not fields:
            continue
        start, duration, speaker = float(fields[3]), float(fields[4]), fields[7]
        row = activity.setdefault(speaker, np.zeros(frames, np.uint8))
        first = max(0, min(round(start * fps), frames))
        last = max(0, min(round((start + duration) * fps), frames))
        row[first:last] = 1
    return activity


def speaker_order(speaker):
    return (0, int(speaker), "") if speaker.isdigit() else (1, 0, speaker)


def crop_face(frame, box, size_out=FACE_SIZE, color="gray"):
    x1, y1, x2, y2 = (int(v) for v in box)
    shape = (size_out, size_out) if color == "gray" else (size_out, size_out, 3)
    blank = np.full(shape, BLANK_FACE, np.uint8)
    if x1 >= x2 or y1 >= y2:
        return blank
    height, width = frame.shape[:2]
    size = max(x2 - x1, y2 - y1)
    left = (x1 + x2) // 2 - size // 2
    top = (y1 + y2) // 2 - size // 2
    x1c, y1c = max(0, left), max(0, top)
    x2c, y2c = min(width, left + size), min(height, top + size)
    if x1c >= x2c or y1c >= y2c:
        return blank
    face = frame[y1c:y2c, x1c:x2c]
    if color == "gray":
        face = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
    face = cv2.copyMakeBorder(
        face,
        y1c - top,
        top + size - y2c,
        x1c - left,
        left + size - x2c,
        cv2.BORDER_CONSTANT,
        value=BLANK_FACE if color == "gray" else (BLANK_FACE,) * 3,
    )
    return cv2.resize(face, (size_out, size_out))


def sinc_kernel(orig, new):
    base = min(orig, new) * 0.99
    width = math.ceil(6 * orig / base)
    idx = np.arange(-width, width + orig, dtype=np.float32)[None] / orig
    t = np.arange(0, -new, -1, dtype=np.float32)[:, None] / new + idx
    t *= base
    t = np.clip(t, -6, 6)
    window = np.cos(t * math.pi / 6 / 2) ** 2
    t *= math.pi
    with np.errstate(divide="ignore", invalid="ignore"):
        kernel = np.where(t == 0, np.float32(1), np.sin(t) / t)
    kernel *= window * (base / orig)
    return kernel.astype(np.float32), width


def resample(audio, orig, new):
    if orig == new:
        return audio
    divisor = math.gcd(orig, new)
    orig, new = orig // divisor, new // divisor
    kernel, width = sinc_kernel(orig, new)
    padded = np.pad(audio, (width, width + orig))
    blocks = np.lib.stride_tricks.sliding_window_view(padded, kernel.shape[1])[::orig]
    out = np.empty((len(blocks), new), np.float32)
    step = CHUNK // orig
    for start in range(0, len(blocks), step):
        out[start : start + step] = blocks[start : start + step] @ kernel.T
    length = int(np.ceil(np.float32(new * len(audio) / orig)))
    return out.reshape(-1)[:length]


def probe_audio(path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate,channels",
            "-of", "default=noprint_wrappers=1", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    info = dict(line.split("=", 1) for line in result.stdout.split())
    return int(info["sample_rate"]), int(info["channels"])


def load_audio(path):
    rate, channels = probe_audio(path)
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "f32le", "-c:a", "pcm_f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    samples = np.frombuffer(raw, np.float32).reshape(-1, channels)
    mono = np.empty(len(samples), np.float32)
    for start in range(0, len(samples), CHUNK):
        mono[start : start + CHUNK] = np.clip(samples[start : start + CHUNK], -1.0, 1.0).mean(axis=1)
    del raw, samples
    if not np.isfinite(mono).all():
        raise ValueError(f"{path}: audio has NaN or inf")
    return resample(mono, rate, SAMPLE_RATE)


def fit_length(audio, length):
    audio = audio[:length]
    return np.pad(audio, (0, length - len(audio)))


def to_pcm16(audio):
    return np.round(np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)


def audio_level(audio):
    if not audio.size:
        return {"audio_mean": 0.0, "audio_rms": 0.0, "audio_peak": 0.0, "audio_gain": 1.0}
    mean = np.float32(audio.mean(dtype=np.float64))
    centred = audio - mean
    rms = float(np.float32(np.sqrt(np.mean(np.square(centred, dtype=np.float64)))))
    peak = float(np.abs(centred).max())
    gain = 1.0
    if rms > 1e-8 and peak > 1e-8:
        gain = min(10 ** (TARGET_RMS_DBFS / 20) / rms, 10 ** (MAX_GAIN_DB / 20), PEAK_LIMIT / peak)
    return {"audio_mean": float(mean), "audio_rms": rms, "audio_peak": peak, "audio_gain": gain}


def grid_windows(frames, window, hop):
    if frames <= window:
        return [(0, 0, frames)]
    windows, start, covered = [], 0, 0
    while start + window <= frames:
        windows.append((start, start, start + window))
        covered = start + window
        start += hop
    if covered < frames:
        windows.append((start, start, frames))
    return windows


def tiled_windows(frames, window, context):
    span = max(1, window - context)
    if frames <= span:
        edges = [0, frames]
    else:
        edges = np.linspace(0, frames, math.ceil(frames / span) + 1).round().astype(int).tolist()
    return [(max(0, first - context), first, last) for first, last in zip(edges[:-1], edges[1:])]


def make_windows(kind, frames, speakers, events, job):
    if frames == 0:
        return []
    if kind == "events":
        windows = []
        for index, event in enumerate(events):
            last = round(event["pause_start"] * FPS)
            known = event["prev_speaker"] in speakers and event["next_speaker"] in speakers
            if last <= 0 or last > frames or not known:
                continue
            first = max(0, last - job["event_window"])
            windows.append((index, "event", first, first, last, event))
        return windows
    if kind == "train" and job["hop"] < job["window"]:
        spans = grid_windows(frames, job["window"], job["hop"])
    else:
        spans = tiled_windows(frames, job["window"], job["context"])
    return [(index, kind, *span, None) for index, span in enumerate(spans)]


def npy_bytes(array):
    buffer = io.BytesIO()
    np.save(buffer, np.ascontiguousarray(array), allow_pickle=False)
    return buffer.getvalue()


def add_member(tar, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def write_wav(path, pcm):
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(pcm.astype("<i2").tobytes())


def copy_audio_stream(video_path, path):
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(video_path), "-map", "0:a:0", "-c", "copy", str(path)],
        check=True,
    )


def write_labels(path, speakers, speaking, visible, bbox):
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["frame", "speaker", "speaking", "visible", "x1", "y1", "x2", "y2"])
        for frame in range(speaking.shape[1]):
            for row, speaker in enumerate(speakers):
                box = [f"{v:.6f}" for v in bbox[row, frame]] if visible[row, frame] else ["", "", "", ""]
                writer.writerow([frame, speaker, int(speaking[row, frame]), int(visible[row, frame]), *box])


def write_windows(path, windows, segment_level, window_levels):
    columns = [
        "window", "kind", "start_frame", "scored_from_frame", "end_frame", "start_sec", "end_sec",
        "audio_mean", "audio_rms", "audio_peak", "audio_gain",
        "event_type", "prev_speaker", "next_speaker", "pause_start", "pause_duration",
    ]
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for (index, kind, start, scored, end, event), level in zip(windows, window_levels):
            level = level or segment_level
            event = event or {}
            writer.writerow(
                [
                    index, kind, start, scored, end, f"{start / FPS:.3f}", f"{end / FPS:.3f}",
                    level["audio_mean"], level["audio_rms"], level["audio_peak"], level["audio_gain"],
                    event.get("type", ""), event.get("prev_speaker", ""), event.get("next_speaker", ""),
                    event.get("pause_start", ""), event.get("pause_duration", ""),
                ]
            )


def extract_segment(job):
    root, out = Path(job["root"]), Path(job["out"])
    split, video, seg = job["split"], job["video"], job["seg"]
    video_path = root / "videos" / video / f"{seg}.mp4"
    raw = out / "raw" / split if job["raw"] else None
    work = out / "webdataset" / ".work" if job["webdataset"] else None

    boxes = read_boxes(root / "bbox" / video / f"{seg}.csv")
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise FileNotFoundError(f"cannot open {video_path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if abs(fps - FPS) > 1e-6:
        raise ValueError(f"{video_path}: {fps} fps, expected {FPS:g}")

    activity = read_rttm(root / "rttm" / video / f"{seg}.rttm", frames, fps)
    boxed = {speaker for present in boxes.values() for speaker in present}
    speakers = sorted(boxed | set(activity), key=speaker_order)

    face_shape = (job["size"], job["size"]) if job["color"] == "gray" else (job["size"], job["size"], 3)
    faces = None
    if work:
        work.mkdir(parents=True, exist_ok=True)
        faces_path = work / f"{video}+{seg}.faces.npy"
        faces = np.lib.format.open_memmap(
            faces_path, mode="w+", dtype=np.uint8, shape=(len(speakers), frames) + face_shape
        )
        faces[:] = BLANK_FACE
    face_dir = None
    if raw:
        face_dir = raw / "faces" / video / seg
        for speaker in boxed:
            (face_dir / speaker).mkdir(parents=True, exist_ok=True)
    image_params = [cv2.IMWRITE_JPEG_QUALITY, job["jpeg_quality"]] if job["image"] == "jpg" else []

    visible = np.zeros((len(speakers), frames), bool)
    bbox = np.zeros((len(speakers), frames, 4), np.float32)
    scale = np.array([1.0 / width, 1.0 / height] * 2, np.float32)
    decoded = 0
    while decoded < frames:
        ok, frame = capture.read()
        if not ok:
            break
        present = boxes.get(decoded, {})
        for row, speaker in enumerate(speakers):
            box = present.get(speaker)
            if box is None:
                continue
            face = crop_face(frame, box, job["size"], job["color"])
            visible[row, decoded] = True
            bbox[row, decoded] = np.asarray(box, np.float32) * scale
            if faces is not None:
                faces[row, decoded] = face if job["color"] == "gray" else face[..., ::-1]
            if face_dir:
                cv2.imwrite(str(face_dir / speaker / f"{decoded:06d}.{job['image']}"), face, image_params)
        decoded += 1
    capture.release()

    speaking = np.stack([activity.get(speaker, np.zeros(frames, np.uint8)) for speaker in speakers])
    tracked = visible.any(axis=1).tolist()
    audio = fit_length(load_audio(video_path), round(frames / fps * SAMPLE_RATE))
    pcm = to_pcm16(audio)
    segment_level = audio_level(audio)

    kind = WINDOWS_BY_SPLIT[split] if job["windows"] == "split" else job["windows"]
    windows = make_windows(kind, frames, speakers, job["events"], job)
    window_levels = [
        audio_level(audio[start * SAMPLES_PER_FRAME : end * SAMPLES_PER_FRAME]) if event else None
        for _, _, start, _, end, event in windows
    ]

    info = {
        "video": video,
        "seg": seg,
        "split": split,
        "fps": fps,
        "frames": frames,
        "decoded_frames": decoded,
        "width": width,
        "height": height,
        "speakers": speakers,
        "tracked": tracked,
        "sample_rate": SAMPLE_RATE,
        "audio_samples": len(pcm),
        "face_size": job["size"],
        "face_color": job["color"],
        **segment_level,
    }

    if raw:
        for name in ("audio", "orig_audio", "labels", "windows", "info"):
            (raw / name / video).mkdir(parents=True, exist_ok=True)
        write_wav(raw / "audio" / video / f"{seg}.wav", pcm)
        copy_audio_stream(video_path, raw / "orig_audio" / video / f"{seg}.m4a")
        write_labels(raw / "labels" / video / f"{seg}.csv", speakers, speaking, visible, bbox)
        write_windows(raw / "windows" / video / f"{seg}.csv", windows, segment_level, window_levels)
        (raw / "info" / video / f"{seg}.json").write_text(json.dumps(info, indent=1) + "\n")

    segment_tar = None
    if work:
        segment_tar = work / f"{video}+{seg}.tar"
        with tarfile.open(segment_tar, "w") as tar:
            for (index, window_kind, start, scored, end, event), level in zip(windows, window_levels):
                key = f"{video}+{seg}+{index:05d}"
                meta = {
                    "video": video,
                    "seg": seg,
                    "split": split,
                    "window": index,
                    "kind": window_kind,
                    "start_frame": start,
                    "scored_from_frame": scored,
                    "end_frame": end,
                    "start_sec": start / fps,
                    "end_sec": end / fps,
                    "fps": fps,
                    "sample_rate": SAMPLE_RATE,
                    "speakers": speakers,
                    "tracked": tracked,
                    "face_size": job["size"],
                    "face_color": job["color"],
                    **(level or segment_level),
                }
                if event:
                    meta["event"] = event
                add_member(tar, f"{key}.faces.npy", npy_bytes(faces[:, start:end]))
                add_member(tar, f"{key}.audio.npy", npy_bytes(pcm[start * SAMPLES_PER_FRAME : end * SAMPLES_PER_FRAME]))
                add_member(tar, f"{key}.speaking.npy", npy_bytes(speaking[:, start:end]))
                add_member(tar, f"{key}.visible.npy", npy_bytes(visible[:, start:end].astype(np.uint8)))
                add_member(tar, f"{key}.bbox.npy", npy_bytes(bbox[:, start:end]))
                add_member(tar, f"{key}.json", json.dumps(meta).encode())
        del faces
        faces_path.unlink()

    return {
        "split": split,
        "video": video,
        "seg": seg,
        "speakers": speakers,
        "frames": frames,
        "decoded": decoded,
        "windows": len(windows),
        "tar": str(segment_tar) if segment_tar else None,
    }


class ShardWriter:
    def __init__(self, folder, split, max_bytes):
        self.folder = folder
        self.split = split
        self.max_bytes = max_bytes
        self.tar = None
        self.size = 0
        self.shards = 0
        self.samples = 0

    def add_sample(self, members):
        size = sum(len(data) + 1024 for _, data in members)
        if self.tar is None or (self.size and self.size + size > self.max_bytes):
            self.close()
            self.tar = tarfile.open(self.folder / f"{self.split}-{self.shards:06d}.tar", "w")
            self.shards += 1
            self.size = 0
        for name, data in members:
            add_member(self.tar, name, data)
        self.size += size
        self.samples += 1

    def add_tar(self, path):
        with tarfile.open(path) as source:
            members, key = [], None
            for member in source:
                member_key = member.name.split(".", 1)[0]
                if members and member_key != key:
                    self.add_sample(members)
                    members = []
                key = member_key
                members.append((member.name, source.extractfile(member).read()))
            if members:
                self.add_sample(members)
        path.unlink()

    def close(self):
        if self.tar:
            self.tar.close()
            self.tar = None


def main():
    args = parse_args()
    root = args.root.resolve()
    out = args.out.resolve()
    splits = read_splits(root / "splits.txt")
    events = read_events(root / "test_events.txt")
    wanted = args.split or list(SPLITS)
    raw = args.format in ("raw", "both")
    webdataset = args.format in ("webdataset", "both")

    jobs = []
    for split in SPLITS:
        if split not in wanted:
            continue
        for video in sorted(v for v, s in splits.items() if s == split):
            if args.video and video not in args.video:
                continue
            for rttm in sorted((root / "rttm" / video).glob("*.rttm")):
                if args.seg and rttm.stem not in args.seg:
                    continue
                jobs.append(
                    {
                        "root": str(root),
                        "out": str(out),
                        "split": split,
                        "video": video,
                        "seg": rttm.stem,
                        "events": events.get((video, rttm.stem), []),
                        "windows": args.windows,
                        "window": max(1, round(args.window * FPS)),
                        "hop": max(1, round(args.hop * FPS)),
                        "context": round(args.context * FPS),
                        "event_window": round(args.event_window * FPS),
                        "raw": raw,
                        "webdataset": webdataset,
                        "color": args.color,
                        "size": args.size,
                        "image": args.image,
                        "jpeg_quality": args.jpeg_quality,
                    }
                )
    if not jobs:
        raise SystemExit("no segment matches the filters")
    missing = [
        f"{job['video']}/{job['seg']}"
        for job in jobs
        if not (root / "videos" / job["video"] / f"{job['seg']}.mp4").exists()
        or not (root / "bbox" / job["video"] / f"{job['seg']}.csv").exists()
    ]
    if missing:
        raise SystemExit(f"{len(missing)} segments have no video or bbox file, e.g. {', '.join(missing[:5])}")

    writers = {}
    if webdataset:
        for split in sorted({job["split"] for job in jobs}):
            folder = out / "webdataset" / split
            folder.mkdir(parents=True, exist_ok=True)
            if any(folder.glob(f"{split}-*.tar")):
                raise SystemExit(f"{folder} already has shards, remove them first")
            writers[split] = ShardWriter(folder, split, round(args.shard_size * 1e9))

    pool = Pool(args.workers) if args.workers > 1 else None
    results = pool.imap(extract_segment, jobs) if pool else map(extract_segment, jobs)
    for done, result in enumerate(results, 1):
        speakers = " ".join(result["speakers"])
        print(
            f"[{done}/{len(jobs)}] {result['split']} {result['video']}/{result['seg']}: "
            f"speakers {speakers}, {result['decoded']}/{result['frames']} frames, {result['windows']} windows",
            flush=True,
        )
        if result["tar"]:
            writers[result["split"]].add_tar(Path(result["tar"]))
    if pool:
        pool.close()
        pool.join()

    for split, writer in writers.items():
        writer.close()
        print(f"{split}: {writer.samples} samples in {writer.shards} shards under {writer.folder}")
    if webdataset:
        work = out / "webdataset" / ".work"
        if work.exists() and not any(work.iterdir()):
            work.rmdir()


if __name__ == "__main__":
    main()
