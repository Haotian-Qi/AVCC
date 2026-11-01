import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_utterance_rttm import read_words, segment_lines  # noqa: E402

PAGE_DIR = Path(__file__).resolve().parent / "annotate"
PAGES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/bbox.js": ("bbox.js", "text/javascript; charset=utf-8"),
}
NAME = re.compile(r"^[A-Za-z0-9_+-]+$")
SUFFIX = {"word": ".txt", "utterance": ".txt", "rttm": ".rttm", "bbox": ".csv", "videos": ".mp4"}
WRITABLE = ("word", "bbox", "utterance", "rttm")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
MEDIA_CHUNK = 8 << 20
ROOT = None
write_lock = threading.Lock()


def natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def data_path(layer, video, seg):
    if layer not in SUFFIX or not NAME.match(video) or not NAME.match(seg):
        raise ValueError("bad video or segment name")
    return ROOT / layer / video / f"{seg}{SUFFIX[layer]}"


def writable_path(layer, video, seg):
    path = data_path(layer, video, seg)
    allowed = (ROOT / layer).resolve()
    if layer not in WRITABLE or not path.resolve().is_relative_to(allowed):
        raise PermissionError(f"refusing to write {path}")
    return path


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_lines(path):
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def event_rows():
    rows = []
    for line in read_lines(ROOT / "test_events.txt"):
        p = line.split("\t")
        if len(p) >= 7:
            rows.append(p)
    return rows


def listing():
    lines = read_lines(ROOT / "splits.txt")
    head = lines[0].split("\t") if lines else []
    if "videoID" not in head or "split" not in head:
        raise ValueError("splits.txt needs a header with videoID and split")
    vi, si = head.index("videoID"), head.index("split")
    split_of = {}
    for line in lines[1:]:
        p = line.split("\t")
        if len(p) > max(vi, si):
            split_of[p[vi]] = p[si]
    n_events = {}
    for p in event_rows():
        n_events[(p[0], p[1])] = n_events.get((p[0], p[1]), 0) + 1
    out = {"train": [], "val": [], "test": []}
    for video in sorted(split_of):
        if not NAME.match(video):
            continue
        segs = set()
        for layer in ("word", "bbox", "videos"):
            d = ROOT / layer / video
            if d.is_dir():
                segs.update(f.stem for f in d.iterdir() if f.suffix == SUFFIX[layer] and NAME.match(f.stem))
        if segs:
            out.setdefault(split_of[video], []).append({
                "video": video,
                "segs": [{"seg": s, "events": n_events.get((video, s), 0)} for s in sorted(segs, key=natural)],
            })
    return out


def events(video, seg):
    out = []
    for p in event_rows():
        if p[0] == video and p[1] == seg:
            out.append({"prev": p[2], "start": float(p[3]), "dur": float(p[4]), "next": p[5], "type": p[6]})
    return sorted(out, key=lambda e: e["start"])


def save_words(data):
    video, seg = data["video"], data["seg"]
    paths = {layer: writable_path(layer, video, seg) for layer in ("word", "utterance", "rttm")}
    rows, skipped = [], 0
    for i, w in enumerate(data["words"], 1):
        speaker, start, dur = str(w[0]).strip(), float(w[1]), float(w[2])
        text = " ".join(str(w[3]).split())
        if not text:
            skipped += 1
            continue
        if not NAME.match(speaker):
            raise ValueError(f"word {i} '{text}': bad speaker '{speaker}'")
        if not (math.isfinite(start) and math.isfinite(dur)) or start < 0 or round(dur, 3) <= 0:
            raise ValueError(f"word {i} '{text}' at {start:.3f} s: start must be >= 0 and end after start")
        rows.append((speaker, start, dur, text))
    rows.sort(key=lambda r: r[1])
    body = "".join(f"{video}\t{seg}\t{spk}\t{start:.3f}\t{dur:.3f}\t{text}\n" for spk, start, dur, text in rows)
    with write_lock:
        write_atomic(paths["word"], body.encode("utf-8"))
        ulines, rlines = segment_lines(read_words(paths["word"]), video, seg)
        write_atomic(paths["utterance"], "".join(ulines).encode("utf-8"))
        write_atomic(paths["rttm"], "".join(rlines).encode("utf-8"))
    return {"ok": True, "skipped": skipped, "written": [
        {"path": f"word/{video}/{seg}.txt", "rows": len(rows)},
        {"path": f"utterance/{video}/{seg}.txt", "rows": len(ulines)},
        {"path": f"rttm/{video}/{seg}.rttm", "rows": len(rlines)},
    ]}


def save_bbox(data):
    video, seg, ops = data["video"], data["seg"], data["ops"]
    path = writable_path("bbox", video, seg)
    with write_lock:
        header, rows, nl = "frame_idx,speaker_id,x1,y1,x2,y2", [], "\n"
        if path.exists():
            raw = path.read_bytes().decode("utf-8")
            if "\r\n" in raw:
                nl = "\r\n"
            lines = [line for line in raw.replace("\r\n", "\n").split("\n") if line.strip()]
            if lines and lines[0].startswith("frame_idx"):
                header = lines.pop(0)
            rows = [line.split(",") for line in lines]
        where = {(int(r[0]), r[1]): i for i, r in enumerate(rows)}
        for o in ops:
            f, s = int(o["f"]), str(o["s"])
            if f < 0 or not NAME.match(s):
                raise ValueError(f"bad frame or id: {f} {s}")
            if o["op"] == "del":
                i = where.pop((f, s), None)
                if i is not None:
                    rows[i] = None
            elif o["op"] == "set":
                x1, y1, x2, y2 = (int(round(float(o[k]))) for k in ("x1", "y1", "x2", "y2"))
                if x2 <= x1 or y2 <= y1:
                    raise ValueError(f"frame {f} id {s}: empty box")
                row = [str(f), s, str(x1), str(y1), str(x2), str(y2)]
                if (f, s) in where:
                    rows[where[(f, s)]] = row
                else:
                    where[(f, s)] = len(rows)
                    rows.append(row)
            else:
                raise ValueError(f"unknown op {o['op']}")
        rows = sorted((r for r in rows if r is not None), key=lambda r: int(r[0]))
        body = nl.join([header] + [",".join(r) for r in rows]) + nl
        write_atomic(path, body.encode("utf-8"))
    return {"ok": True, "rows": len(rows), "applied": len(ops), "path": f"bbox/{video}/{seg}.csv"}


def wav_header(n, sr):
    return (b"RIFF" + (36 + n).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
            + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + sr.to_bytes(4, "little")
            + (sr * 2).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
            + b"data" + n.to_bytes(4, "little"))


class Handler(BaseHTTPRequestHandler):
    # The players need keep-alive: these mp4s keep their index at the end of the file,
    # so a player makes several range requests before it can show a frame.
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionError, TimeoutError):
            self.close_connection = True

    def send_body(self, body, ctype, code=200, headers=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.send_body(json.dumps(obj).encode(), "application/json", code)

    def local_host(self):
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):
            host = host[:host.find("]") + 1]
        else:
            host = host.rsplit(":", 1)[0]
        return host in LOCAL_HOSTS

    def do_GET(self):
        if not self.local_host():
            return self.send_json({"error": "forbidden host"}, 403)
        u = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = unquote(u.path)
        try:
            if path in PAGES:
                name, ctype = PAGES[path]
                return self.send_body((PAGE_DIR / name).read_bytes(), ctype)
            if path == "/api/list":
                return self.send_json(listing())
            if path == "/api/events":
                return self.send_json(events(q["video"], q["seg"]))
            if path == "/api/audio":
                return self.audio(q["video"], q["seg"], float(q.get("from", 0)), float(q.get("dur", 300)),
                                  int(q.get("sr", 16000)))
            layer, _, rest = path.strip("/").partition("/")
            video, _, name = rest.partition("/")
            if layer in SUFFIX and "/" not in name and name.endswith(SUFFIX[layer]):
                p = data_path(layer, video, name[:-len(SUFFIX[layer])])
                if layer == "videos":
                    return self.stream(p)
                if not p.is_file():
                    return self.send_json({"error": "not found"}, 404)
                return self.send_body(p.read_bytes(), "text/plain; charset=utf-8")
            return self.send_json({"error": "not found"}, 404)
        except KeyError as e:
            return self.send_json({"error": f"missing parameter {e}"}, 400)
        except ValueError as e:
            return self.send_json({"error": str(e)}, 400)
        except Exception as e:
            return self.send_json({"error": str(e)}, 500)

    do_HEAD = do_GET

    def do_POST(self):
        if not self.local_host():
            return self.send_json({"error": "forbidden host"}, 403)
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send_json({"error": "expected application/json"}, 415)
        path = urlsplit(self.path).path
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if path == "/api/save/words":
                return self.send_json(save_words(data))
            if path == "/api/save/bbox":
                return self.send_json(save_bbox(data))
            return self.send_json({"error": "not found"}, 404)
        except PermissionError as e:
            return self.send_json({"error": str(e)}, 403)
        except KeyError as e:
            return self.send_json({"error": f"not saved: missing {e}"}, 400)
        except (IndexError, TypeError, ValueError) as e:
            return self.send_json({"error": f"not saved: {e}"}, 400)
        except Exception as e:
            return self.send_json({"error": f"not saved: {e}"}, 500)

    def audio(self, video, seg, start, dur, sr):
        mp4 = data_path("videos", video, seg)
        if not mp4.is_file():
            return self.send_json({"error": "no video file"}, 404)
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return self.send_json({"error": "ffmpeg not found"}, 500)
        sr = max(8000, min(sr, 48000))
        start = max(0.0, start)
        dur = max(1.0, min(dur, 900.0))
        # -ss before -i seeks by keyframe instead of decoding from the start. aresample=async
        # places samples by their timestamps, as the player does, so the audio stays in sync.
        kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        proc = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(mp4),
                               "-vn", "-af", "aresample=async=1000", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                              stdin=subprocess.DEVNULL, capture_output=True, **kw)
        pcm = proc.stdout
        if not pcm:
            err = proc.stderr.decode("utf-8", "replace").strip()[:200]
            return self.send_json({"error": err or "no audio decoded"}, 404)
        self.send_body(wav_header(len(pcm), sr) + pcm, "audio/wav")

    def stream(self, path):
        if not path.is_file():
            return self.send_json({"error": "not found"}, 404)
        st = path.stat()
        size = st.st_size
        etag = f'"{size:x}-{st.st_mtime_ns:x}"'
        cache = [("ETag", etag), ("Cache-Control", "private, no-cache"), ("Accept-Ranges", "bytes")]
        if etag in [t.strip() for t in (self.headers.get("If-None-Match") or "").split(",")]:
            self.send_response(304)
            for k, v in cache:
                self.send_header(k, v)
            self.end_headers()
            return
        rng = self.headers.get("Range")
        if rng and self.headers.get("If-Range", etag).strip() != etag:
            rng = None
        start, end, code = 0, size - 1, 200
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].strip().partition("-")
            try:
                if not a:
                    start = max(0, size - int(b))
                else:
                    start = int(a)
                    # An open-ended range gets a capped slice; the player asks for more as it plays.
                    end = int(b) if b else start + MEDIA_CHUNK - 1
            except ValueError:
                return self.send_json({"error": "bad range"}, 400)
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", "video/mp4")
        for k, v in cache:
            self.send_header(k, v)
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as fh:
            fh.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = fh.read(min(1 << 20, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except ConnectionError:
                    self.close_connection = True
                    return
                left -= len(chunk)


def main():
    global ROOT
    ap = argparse.ArgumentParser(description="Annotation tool for AVCC: correct words, speakers and face boxes.")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1],
                    help="the AVCC folder with word/, bbox/, videos/ (default: the parent of script/)")
    ap.add_argument("--port", type=int, default=8781)
    args = ap.parse_args()
    ROOT = args.root.resolve()
    if not (ROOT / "word").is_dir():
        raise SystemExit(f"no word/ folder in {ROOT}")
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as e:
        raise SystemExit(f"cannot use port {args.port}: {e.strerror}")
    print(f"Open http://localhost:{args.port}  (data: {ROOT}, Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()


if __name__ == "__main__":
    main()
