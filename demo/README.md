# AVCC demo

A browser viewer for two segments from the AVCC test set, one with two speakers and one with three.
It plays the video with the face boxes drawn on top, shows the words as they are spoken, and lays
out each speaker's utterances and the hold/shift turn events on a timeline.

The timeline zooms from the whole segment down to one second: use the slider, the −/+ buttons,
the mouse wheel or the <kbd>+</kbd>/<kbd>−</kbd> keys, and drag to pan. At 15 s or less each word is
drawn inside its utterance, and hovering any word, utterance or event shows its text and timing.

**Open it:** https://haotian-qi.github.io/AVCC/demo/

| Segment | Speakers | Length | Turn events |
|---|---|---|---|
| `sL3jHOX826s/seg04` | 2 | 3:16 | 15 hold, 17 shift |
| `M9SWIUeAecA/seg20` | 3 | 1:41 | 7 hold, 7 shift |

## Files

The annotation files use the same layout and format as the full release:

```
data/
  manifest.json                 segments shown in the viewer
  word/<video>/<seg>.txt        one row per word
  utterance/<video>/<seg>.txt   words merged per speaker across pauses <= 0.2 s
  rttm/<video>/<seg>.rttm       one RTTM row per utterance
  bbox/<video>/<seg>.csv        frame_idx,speaker_id,x1,y1,x2,y2 (25 fps, source pixels)
  turn_events.txt               hold/shift events for both segments
  videos/<video>/<seg>.mp4      720p preview video, every frame kept
  videos/<video>/<seg>.json     source and preview resolution, frame count
```

Text rows are tab-separated: `video  segment  speaker  start  duration  text`, with times in
seconds from the start of the segment. Turn events use the same columns, with the next speaker
in place of `text` and the event type (`hold` or `shift`) as a seventh column.

## Rebuilding the videos

`make_demo_videos.sh` re-encodes the two segments on the lab server at 720p and downloads them
into `data/videos/`. Frame count and frame rate are unchanged, so `bbox` frame indices still
line up; the viewer scales the boxes from the source resolution.

## Running it locally

Serve this folder with any web server that supports range requests (needed for seeking in the
video), for example `npx http-server demo`, then open the printed address.
