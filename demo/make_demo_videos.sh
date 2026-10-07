#!/usr/bin/env bash
# Re-encode the demo segments on KTHBox and fetch them into demo/data/videos/.
#
# The segments keep every frame at 25 fps, so bbox frame_idx stays valid; only the
# resolution drops to 720p to keep the files small. The source resolution is saved
# next to each video so the viewer can scale the boxes.
#
# Usage: demo/make_demo_videos.sh      (re-run to resume after a dropped connection)
set -euo pipefail

HOST=${HOST:-KTHBox}
SRC=${SRC:-/media/haotianq/hdd/haotianq/dataset/AVCC/AVCC+/videos}
TMP=${TMP:-/tmp/avcc_demo_videos}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/data/videos"
SEGMENTS=(sL3jHOX826s/seg04 M9SWIUeAecA/seg20)

for s in "${SEGMENTS[@]}"; do
  echo ">> encoding $s on $HOST"
  ssh "$HOST" bash -s -- "$SRC" "$TMP" "$s" <<'REMOTE'
set -euo pipefail
src="$1/$3.mp4"; dst="$2/$3.mp4"; meta="$2/$3.json"
mkdir -p "$(dirname "$dst")"
if [ ! -s "$dst" ]; then
  ffmpeg -nostdin -loglevel error -y -i "$src" \
    -vf "scale=-2:720" -c:v libx264 -preset slow -crf 30 -pix_fmt yuv420p \
    -c:a aac -b:a 64k -ac 1 -movflags +faststart "$dst.part.mp4"
  mv "$dst.part.mp4" "$dst"
fi
probe() { ffprobe -v error -select_streams v:0 -count_packets \
  -show_entries stream=width,height,nb_read_packets -of csv=p=0 "$1"; }
IFS=, read -r sw sh sn <<<"$(probe "$src")"
IFS=, read -r ow oh on <<<"$(probe "$dst")"
if [ "$sn" != "$on" ]; then echo "frame count changed: $sn -> $on" >&2; exit 1; fi
printf '{"src_width": %s, "src_height": %s, "width": %s, "height": %s, "frames": %s}\n' \
  "$sw" "$sh" "$ow" "$oh" "$on" > "$meta"
echo "   frames $on  ${sw}x${sh} -> ${ow}x${oh}  $(du -h "$dst" | cut -f1)"
REMOTE
done

echo ">> downloading"
mkdir -p "$OUT"
start=$(date +%s)
rsync -a --partial --info=progress2 "$HOST:$TMP/" "$OUT/"
echo ">> done in $(( $(date +%s) - start )) s"
ls -lh "$OUT"/*/*
