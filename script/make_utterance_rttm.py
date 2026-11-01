import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAUSE = 0.1
EPS = 1e-9


def read_words(path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        p = line.split("\t")
        start = float(p[3])
        rows.append([p[0], p[1], p[2], start, start + float(p[4]), "\t".join(p[5:]).strip()])
    return rows


def merge(words, pause=PAUSE):
    by_speaker = {}
    for w in words:
        by_speaker.setdefault(w[2], []).append(w)
    out = []
    for items in by_speaker.values():
        items.sort(key=lambda w: (w[3], w[4]))
        cur = None
        for w in items:
            if cur and w[3] - cur[4] <= pause + EPS:
                cur[4] = max(cur[4], w[4])
                cur[5] += " " + w[5]
            else:
                cur = list(w)
                out.append(cur)
    return sorted(out, key=lambda u: (u[3], u[4], u[2]))


def segment_lines(words, video, seg, pause=PAUSE):
    ulines, rlines = [], []
    for v, s, spk, a, b, text in merge(words, pause):
        start = round(a, 3)
        dur = round(round(b, 3) - start, 3)
        ulines.append(f"{v}\t{s}\t{spk}\t{start:.3f}\t{dur:.3f}\t{text}\n")
        rlines.append(f"SPEAKER {video}+{seg} 0 {start:.3f} {dur:.3f} <NA> <NA> {spk} <NA> <NA>\n")
    return ulines, rlines


def main():
    pause = float(sys.argv[1]) if len(sys.argv) > 1 else PAUSE
    for name in ("utterance", "rttm"):
        shutil.rmtree(ROOT / name, ignore_errors=True)
        (ROOT / name).mkdir()

    n_seg = n_utt = 0
    for wfile in sorted((ROOT / "word").glob("*/*.txt")):
        video, seg = wfile.parent.name, wfile.stem
        ulines, rlines = segment_lines(read_words(wfile), video, seg, pause)
        (ROOT / "utterance" / video).mkdir(exist_ok=True)
        (ROOT / "rttm" / video).mkdir(exist_ok=True)
        (ROOT / "utterance" / video / f"{seg}.txt").write_text("".join(ulines), encoding="utf-8", newline="\n")
        (ROOT / "rttm" / video / f"{seg}.rttm").write_text("".join(rlines), encoding="utf-8", newline="\n")
        n_seg += 1
        n_utt += len(ulines)
    print(f"pause={pause} segments={n_seg} utterances={n_utt}")


if __name__ == "__main__":
    main()
