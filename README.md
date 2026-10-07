# AVCC: Audio-Visual Conversation Corpus

The **Audio-Visual Conversation Corpus (AVCC)** is a collection of unscripted, multiparty, in-the-wild conversation video. It was built to study **turn-taking in multiparty settings** using only a single camera and a single microphone.

AVCC was introduced in our paper:

> **MuVAP: Multimodal Multiparty Voice Activity Projection for Turn-taking Prediction in the Wild**
> Haotian Qi, Gabriel Skantze
> Department of Speech, Music and Hearing, KTH Royal Institute of Technology, Stockholm, Sweden
> [arXiv:2606.16731](https://arxiv.org/abs/2606.16731) · [Code (MuVAP)](https://github.com/Haotian-Qi/MuVAP)

> **Note:** The dataset is not released yet. Download instructions will be posted here.

## About the Dataset

### Design principles

- **Continuous and unedited:** we keep only sequences without jump cuts or other edits. This preserves the timing of the conversation, including mutual silences and the behaviour just before a turn starts, which causal turn-taking models depend on.
- **Static third-person view:** a single, fixed camera shows all participants, as an outside observer would see them.
- **Spontaneous interaction:** unscripted English conversations, mostly between participants who know each other, collected from YouTube and Twitch livestreams.

Compared with controlled telephone speech such as the Fisher corpus, AVCC shows much wider variation in gaps, pauses and overlaps. This reflects how complex natural face-to-face interaction is.

### Format and annotations

| Property | Value |
|---|---|
| Video | 25 fps, single static camera |
| Audio | 16 kHz, mono |
| Language | English |
| Face tracks | Detected with InsightFace (RetinaFace backbone); face crops extracted from the tracked coordinates |
| Speaker identity | Assigned with ArcFace embeddings, then manually checked and corrected |

### Demo

The [demo viewer](https://haotian-qi.github.io/AVCC/demo/) shows two segments from the test set, one with two speakers and one with three. It plays each video with the face boxes, the words as they are spoken, and a timeline of every speaker's utterances and the hold/shift turn events. The annotation files behind it are in [`demo/data/`](demo/data/) and use the same format as the full release.

## Work in Progress

AVCC is under active development. We are currently working on:

- **Transcripts:** adding time-aligned speech transcriptions so the corpus can support work that combines linguistic content with audio-visual turn-taking cues.
- **Expanding the corpus:** collecting and annotating more conversations to cover a wider range of speakers, group sizes and interaction settings.

Updates will be announced in this repository.

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

See [LICENSE](LICENSE). The videos in AVCC come from publicly available online content. Copyright of the original recordings stays with their creators.

## Contact

Haotian Qi, KTH Royal Institute of Technology: haotianq@kth.se
