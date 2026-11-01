import argparse
import json
import posixpath
import re
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
PAGE = HERE / "viewer"
CHUNK = 1 << 16
TEXT = "text/plain; charset=utf-8"
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".txt": TEXT,
    ".rttm": TEXT,
    ".md": TEXT,
    ".csv": "text/csv; charset=utf-8",
    ".mp4": "video/mp4",
}
SPLIT_ORDER = {"train": 0, "val": 1, "test": 2}


def natural(name):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name)]


def mp4_boxes(f, start, end):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        size, kind = struct.unpack(">I4s", f.read(8))
        head = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            head = 16
        elif size == 0:
            size = end - pos
        if size < head:
            return
        yield kind, pos + head, pos + size
        pos += size


def mp4_duration(path):
    try:
        with path.open("rb") as f:
            for kind, a, b in mp4_boxes(f, 0, path.stat().st_size):
                if kind != b"moov":
                    continue
                for kind, a, b in mp4_boxes(f, a, b):
                    if kind == b"mvhd":
                        f.seek(a)
                        v1 = f.read(1) == b"\x01"
                        f.seek(a + (20 if v1 else 12))
                        scale, dur = struct.unpack(">IQ" if v1 else ">II", f.read(12 if v1 else 8))
                        return round(dur / scale, 3)
    except (OSError, struct.error, ZeroDivisionError):
        pass
    return None


def listing(root):
    split_of = {}
    splits_file = root / "splits.txt"
    if splits_file.exists():
        for line in splits_file.read_text(encoding="utf-8").splitlines()[1:]:
            p = line.split("\t")
            if len(p) >= 2:
                split_of[p[0]] = p[1].strip()

    segs = {}
    for wfile in (root / "word").glob("*/*.txt"):
        segs.setdefault(wfile.parent.name, []).append(wfile.stem)

    splits = {}
    for video in sorted(segs):
        items = []
        for seg in sorted(segs[video], key=natural):
            items.append({"id": seg, "duration": mp4_duration(root / "videos" / video / f"{seg}.mp4")})
        splits.setdefault(split_of.get(video, "none"), []).append({"id": video, "segments": items})
    names = sorted(splits, key=lambda s: (SPLIT_ORDER.get(s, 3), s))
    return [{"name": s, "videos": splits[s]} for s in names]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    root = None

    def do_GET(self):
        self.respond(True)

    def do_HEAD(self):
        self.respond(False)

    def log_request(self, code="-", size="-"):
        pass

    def respond(self, body):
        path = unquote(urlsplit(self.path).path)
        if path == "/list.json":
            self.send_json(listing(self.root), body)
            return
        if path == "/":
            path = "/viewer/index.html"
        if path.startswith("/viewer/"):
            base, rel = PAGE, path[len("/viewer/"):]
        elif path.startswith("/data/"):
            base, rel = self.root, path[len("/data/"):]
        else:
            self.send_error(404)
            return
        rel = posixpath.normpath("/" + rel).lstrip("/")
        file = base / rel
        if not rel or not file.is_file():
            self.send_error(404)
            return
        try:
            self.send_file(file, body)
        except ConnectionError:
            self.close_connection = True

    def send_json(self, obj, body):
        data = json.dumps(obj, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if body:
            self.wfile.write(data)

    def send_file(self, file, body):
        size = file.stat().st_size
        start, end = 0, size - 1
        status = 200
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        if m and (m[1] or m[2]):
            if m[1]:
                start = int(m[1])
                if m[2]:
                    end = min(int(m[2]), size - 1)
            else:
                start = max(0, size - int(m[2]))
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206

        self.send_response(status)
        self.send_header("Content-Type", TYPES.get(file.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not body:
            return
        with file.open("rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                data = f.read(min(CHUNK, left))
                if not data:
                    break
                self.wfile.write(data)
                left -= len(data)


def main():
    parser = argparse.ArgumentParser(description="Browse the AVCC annotations and videos in a web browser.")
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--root", type=Path, default=HERE.parent, help="dataset folder (default: %(default)s)")
    args = parser.parse_args()

    Handler.root = args.root.resolve()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"{Handler.root}\nhttp://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
