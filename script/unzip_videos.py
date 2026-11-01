import argparse
import hashlib
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEMBER = re.compile(r"^[A-Za-z0-9_-]{11}/seg\d+\.mp4$")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--zips", type=Path)
    ap.add_argument("--skip-checksum", action="store_true")
    ap.add_argument("--delete-zips", action="store_true")
    args = ap.parse_args()
    root = args.root.resolve()
    zdir = (args.zips or root / "videos_zip").resolve()
    dest = root / "videos"

    zips = sorted(zdir.glob("*.zip"))
    if not zips:
        sys.exit(f"no zip files in {zdir}")
    sums = {}
    if (zdir / "SHA256SUMS").exists():
        for line in (zdir / "SHA256SUMS").read_text().splitlines():
            h, name = line.split(maxsplit=1)
            sums[name] = h

    bad = []
    for zpath in zips:
        if not args.skip_checksum:
            if zpath.name not in sums:
                print(f"{zpath.name}: no checksum listed, not verified")
            elif sha256(zpath) != sums[zpath.name]:
                print(f"{zpath.name}: checksum mismatch, download it again")
                bad.append(zpath.name)
                continue
        with zipfile.ZipFile(zpath) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                if not MEMBER.match(info.filename):
                    sys.exit(f"{zpath.name}: unexpected entry {info.filename}")
                target = dest / info.filename
                if target.exists() and target.stat().st_size == info.file_size:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(".mp4.part")
                with z.open(info) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                tmp.replace(target)
        print(f"{zpath.name}: ok")
        if args.delete_zips:
            zpath.unlink()

    if args.delete_zips and not bad and not list(zdir.glob("*.zip")):
        (zdir / "SHA256SUMS").unlink(missing_ok=True)
        if not any(zdir.iterdir()):
            zdir.rmdir()

    expected = {(p.parent.name, p.stem) for p in (root / "bbox").glob("*/*.csv")}
    present = {(p.parent.name, p.stem) for p in dest.glob("*/*.mp4")}
    missing = sorted(expected - present)
    print(f"{len(present & expected)} of {len(expected)} segments have a video")
    for v, s in missing:
        print(f"missing videos/{v}/{s}.mp4")
    if bad or missing:
        sys.exit(1)


if __name__ == "__main__":
    main()
