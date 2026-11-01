# AVCC: Audio-Visual Conversation Corpus

The **Audio-Visual Conversation Corpus (AVCC)** is a collection of unscripted, multiparty, in-the-wild conversation video. It was built to study **turn-taking in multiparty settings** using only a single camera and a single microphone.

AVCC was introduced in our paper:

> **MuVAP: Multimodal Multiparty Voice Activity Projection for Turn-taking Prediction in the Wild**
> Haotian Qi, Gabriel Skantze
> Department of Speech, Music and Hearing, KTH Royal Institute of Technology, Stockholm, Sweden
> [arXiv:2606.16731](https://arxiv.org/abs/2606.16731) · [Code (MuVAP)](https://github.com/Haotian-Qi/MuVAP)

## Download

The dataset is on Hugging Face: **https://huggingface.co/datasets/Haotian-Qi/AVCC**

Request access on that page with your name, email, institution or company and what you will use the data for. AVCC is for non-commercial use only. Once you have access:

```
hf auth login
hf download Haotian-Qi/AVCC --repo-type dataset --local-dir AVCC
python3 AVCC/script/unzip_videos.py --delete-zips
```

The dataset README on Hugging Face describes every file and format.

## About the Dataset

### Design principles

- **Continuous and unedited:** we keep only sequences without jump cuts or other edits. This preserves the timing of the conversation, including mutual silences and the behaviour just before a turn starts, which causal turn-taking models depend on.
- **Static third-person view:** a single, fixed camera shows all participants, as an outside observer would see them.
- **Spontaneous interaction:** unscripted English conversations, mostly between participants who know each other, collected from YouTube and Twitch livestreams.

Compared with controlled telephone speech such as the Fisher corpus, AVCC shows much wider variation in gaps, pauses and overlaps. This reflects how complex natural face-to-face interaction is.

### Format and annotations

| Property | Value |
|---|---|
| Size | 29 conversations, 408 segments, 31 hours; split by video into 17 train, 5 val, 7 test |
| Video | 25 fps, single static camera, 2 or 3 people on screen |
| Audio | AAC 44.1 kHz in the videos; 16 kHz mono for the models |
| Language | English |
| Transcripts | Word-level, with speaker labels and timing, checked and corrected by hand; also as utterances and RTTM |
| Face tracks | Detected with InsightFace (RetinaFace backbone), one box per speaker per frame |
| Speaker identity | Assigned with ArcFace embeddings, then manually checked and corrected |
| Turn events | 1,730 hold/shift events on the test set (865 hold, 865 shift) |
| Human study | 4,290 next-speaker predictions from 154 people on the test events |

### Demo

The [demo viewer](https://haotian-qi.github.io/AVCC/demo/) shows two segments from the test set, one with two speakers and one with three. It plays each video with the face boxes, the words as they are spoken, and a timeline of every speaker's utterances and the hold/shift turn events. The annotation files behind it are in [`demo/data/`](demo/data/) and are the same as in the full release.

## This repository

- [`demo/`](demo/): the demo viewer above.
- [`script/`](script/): the tools that also ship with the dataset: unpack the videos, rebuild utterances and RTTM from the words, extract face crops, audio and model windows the way MuVAP does, browse any segment, and correct the annotations. They expect to sit inside the downloaded dataset folder, so run them from there, for example `python3 AVCC/script/viewer.py`.

## Citation

If you use AVCC or MuVAP in your research, please cite:

```bibtex
@inproceedings{qi26_interspeech,
  title     = {{MuVAP: Multimodal Multiparty Voice Activity Projection for Turn-taking Prediction in the wild}},
  author    = {Haotian Qi and Gabriel Skantze},
  year      = {2026},
  booktitle = {{Interspeech 2026 [Long Track]}},
  pages     = {5952--5961},
  doi       = {10.21437/Interspeech.2026-1381},
  issn      = {2958-1796},
}
```

## License

The code in this repository is MIT, see [LICENSE](LICENSE). The annotations are released under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) on Hugging Face. The videos come from publicly available online content and the copyright of the original recordings stays with their creators; they are shared for non-commercial research only. If you appear in a video or own one and want it taken out, email us and we will remove it.

## Contact

Haotian Qi, KTH Royal Institute of Technology: haotianq@kth.se
