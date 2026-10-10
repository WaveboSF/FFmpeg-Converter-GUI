# Remux Dialog (MKV → MP4)

**Version:** v3.0.4 · **Status:** Stable

## What it does

The **Remux** button in the main window opens a dedicated dialog that rewraps
an `.mkv` into an `.mp4` **without re-encoding the video** — same video bytes,
new container. A 50 GB UHD file finishes in about a minute on NVMe.
Crucially, the **Dolby Vision RPU layer is preserved**, which is why this mode
exists: many TVs (LG OLED, certain Samsungs, Apple TV) only render DV from MP4,
not MKV — even though the underlying HEVC bitstream is identical.

The same dialog can also **extract audio only** and **downmix** multi-channel
tracks (see below). It is completely separate from the normal encoding
settings of the main window; only the FFmpeg worker is shared.

## How to use it

1. Click **Remux** in the main window (not available while an encode is
   running). If an input file is already loaded, it is used as the source.
2. Otherwise click **Browse…** next to *Source* (or anywhere on the workflow
   diagram) and pick a file. The streams are probed with ffprobe.
3. Check/uncheck audio and subtitle tracks. Optionally add external audio or
   subtitle files with **+ Add external …**.
4. Adjust the output path if needed (default: same folder and name, `.mp4`).
5. Click **Start Remux**. Progress is shown in the main window.

The dialog stays open after a remux, so you can load the next source right
away. **Cancel** (labelled **Close** after the first start) only closes the dialog — a running remux continues and can be
stopped with *Cancel* in the main window. An existing output file triggers an
overwrite prompt.

The dialog follows the main window's light/dark theme: hints, warnings, links
and the *Start Remux* button use theme-matched colors (since 3.0.4). Progress
of a running remux is also shown in the Windows taskbar button and the tray
icon, like a normal encode.

## The workflow diagram

The top of the dialog shows source and output side by side with every stream
(codec, channel layout, bitrate). It updates live: unchecked tracks are struck
through, downmixed tracks show their target layout and bitrate, and the arrow
caption tells you what will happen (`copy (no re-encode)`, `copy video +
downmix audio`, `extract audio + re-encode`, …). A red hint appears when no
audio track is selected.

## What gets transferred

| Stream    | Behavior                                                                 |
|-----------|--------------------------------------------------------------------------|
| Video     | First video stream copied 1:1 — bitstream, DV RPU, HDR10/10+ metadata    |
| Audio     | Copied 1:1 per checked track (codec must fit MP4); re-encoded only when downmixed |
| Subtitles | Text subtitles (SRT, ASS, SSA, mov_text) converted to `mov_text`. Picture-based subtitles (PGS, DVB, DVD) cannot be muxed into MP4 and are disabled in the list |

### Pre-selection

- **Audio:** the first MP4-compatible track per language is checked;
  further tracks of the same language and incompatible tracks (TrueHD, DTS)
  stay unchecked.
- **Subtitles:** the first text-based subtitle per language is checked;
  picture-based subtitles are disabled.
- **All / None** buttons toggle every enabled checkbox of a section.

### External files

- **Audio** (`.eac3 .ac3 .aac .m4a .mp3 .opus .flac`, or any file with an
  audio stream): the first audio stream is mapped. If its duration differs
  from the video by more than one second, a sync hint (e.g.
  `(0:35 longer than video)`) is shown. Incompatible codecs (TrueHD, DTS)
  ask for confirmation.
- **Subtitles** (`.srt .ass .ssa .vtt`): converted to `mov_text`.
  Picture-based subtitle files are rejected.

## Audio codec compatibility

| Codec                  | Fits in MP4? | Notes                                    |
|------------------------|--------------|------------------------------------------|
| AAC, AC-3, E-AC-3, MP3 | ✅           | Standard MP4 audio                       |
| FLAC, Opus             | ✅ / ⚠️      | Spec-allowed; older hardware may choke   |
| **TrueHD**             | ❌           | Common in UHD-BluRay rips — not supported |
| DTS / DTS-HD MA        | ❌           | Not supported in MP4                     |

If the source contains a TrueHD track, the dialog shows a warning, and
starting with a TrueHD track checked asks for confirmation. Keep only AC-3 /
E-AC-3 tracks, or fall back to a re-encode in the main window if TrueHD is the
only audio.

## Apple compatibility

**Apple compatibility (HEVC tag: hvc1)** is on by default. For HEVC sources it
adds `-tag:v hvc1` (instead of FFmpeg's default `hev1`), which QuickTime,
macOS Finder, iOS and Apple TV need. For H.264/AV1 sources no tag is set.
Without video (audio-only mode) the option is disabled.

## Audio-only extraction

Untick **Include Video** to extract audio only. Choose the format under
*Audio output format*; the output extension follows automatically:

| Format | Extension | Encoding |
|--------|-----------|----------|
| Copy (keep original codec) | `.mka` | Stream copy, no quality loss |
| FLAC (lossless) | `.flac` | 24-bit FLAC (`-sample_fmt s32 -bits_per_raw_sample 24`) |
| MP3 (lossy, universal) | `.mp3` | `libmp3lame`, FFmpeg default bitrate |
| AAC (lossy, good quality) | `.m4a` | FFmpeg `aac`, default bitrate |

Subtitle tracks are not included in audio-only output.

## Multi-channel downmix

**Downmix audio to:** is enabled as soon as a checked track (internal or
external) has more than two channels. Targets:

- **2.0 (Stereo)** — from 5.1 or 7.1
- **5.1** — only selectable when a checked track has more than 6 channels (7.1)

Only tracks whose channel count exceeds the target are re-encoded; all other
tracks stay as they are. The mode (**Music** / **Movie**) is remembered
across sessions.

**Music mode** (default) — ITU-R BS.775 weighted mix, LFE kept:

```
5.1 → 2.0:  L = FL + 0.707·FC + 0.707·SL + 0.707·LFE   (then volume −3 dB)
            R = FR + 0.707·FC + 0.707·SR + 0.707·LFE
7.1 → 2.0:  L = FL + 0.707·FC + 0.5·BL + 0.5·SL + 0.707·LFE   (then volume −3 dB)
```

**Movie mode** — cinema-balanced (centre −3 dB, surrounds −6 dB, LFE −10 dB):

```
5.1 → 2.0:  L = FL + 0.707·FC + 0.5·SL + 0.316·LFE
7.1 → 2.0:  L = FL + 0.707·FC + 0.354·BL + 0.354·SL + 0.316·LFE
```

**7.1 → 5.1** (both modes): side channels are folded into the back channels
at −3 dB. Unusual layouts (e.g. 4.0) fall back to FFmpeg's `-ac N`.

**Codec and bitrate of downmixed tracks:**

- With video, or audio-only with *Copy*: the source codec is re-encoded
  (AC-3 → AC-3, E-AC-3 → E-AC-3, AAC → AAC, MP3 → MP3, FLAC → FLAC).
- Audio-only with FLAC/MP3/AAC: the chosen format is used.
- Bitrate = `min(codec maximum, source bitrate)` — AC-3 640 kbps,
  E-AC-3 1536 kbps, AAC 512 kbps, MP3 320 kbps; FLAC has no bitrate.

When Music mode, format *Copy* and a lossy multi-channel source come
together, a tip suggests switching to FLAC to avoid a second lossy encode.

## The FFmpeg command

Typical case — HEVC source, one audio track, one SRT subtitle, Apple
compatibility on:

```
ffmpeg -y -progress pipe:1 -nostats -loglevel error
       -i "source.mkv"
       -map 0:v:0 -map 0:1 -map 0:2
       -c copy -c:s mov_text -tag:v hvc1 -strict -2
       "source.mp4"
```

External files become additional inputs (`-i ext_audio.ac3` → `-map 1:a:0`,
external subtitles after the audio inputs → `-map N:s:0`). A downmixed track
gets its own per-stream options, e.g.
`-c:a:0 ac3 -filter:a:0 "pan=stereo|…" -b:a:0 448000`.
`-strict -2` is defensive and allows E-AC-3 (and experimental encoders) in MP4.

## When NOT to remux

- **Reducing file size** — remux keeps the video bitrate. Re-encode instead.
- **Changing resolution** — remux can't downscale. Use the main window
  (4K → 1080p).
- **Source has TrueHD-only audio or MPEG-2 video** — won't fit in MP4.
- **Your player already handles DV in MKV** — modern Apple TV and recent
  firmware on many smart TVs do. Test playback first.

## Troubleshooting

- **Output a few hundred KB different in size:** normal — container framing
  overhead differs between MKV and MP4. The video stream is byte-identical.
- **MP4 plays but not as DV:** likely a DV profile mismatch (profile 7
  dual-layer is the most fragile; profiles 5 and 8.1 are widely supported),
  or the source was HDR10 only — verify with MediaInfo.
- **"codec not currently supported in container":** TrueHD, DTS or MPEG-2 in
  the selection. Deselect the offending track or re-encode.
- **Progress finishes in seconds:** expected. The file is on disk — check
  the output path.
