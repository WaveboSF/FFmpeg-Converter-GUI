# Remux Mode (MKV → MP4)

**Version:** v3.0 · **Status:** Stable

## What it does

Selecting **`Remux only (Copy → MP4)`** in the *Video Codec* dropdown rewraps an
`.mkv` into an `.mp4` **without re-encoding** — same video bytes, same audio
bytes, new container. A 50 GB UHD file finishes in under a minute on NVMe.
Crucially, the **Dolby Vision RPU layer is preserved**, which is why this mode
exists: many TVs (LG OLED, certain Samsungs, Apple TV) only render DV from MP4,
not MKV — even though the underlying HEVC bitstream is identical.

## How to use it

1. Load a source via the **Input** field.
2. Pick `Remux only (Copy → MP4)` in the *Video Codec* dropdown. All
   irrelevant controls (bitrate slider, audio codec, subtitles, 4K→1080p) gray
   out automatically.
3. Optionally uncheck audio tracks you don't want.
4. Press **Run**. Output lands next to the source as `.mp4`.

## What gets transferred

| Stream    | Behavior                                                          |
|-----------|-------------------------------------------------------------------|
| Video     | Copied 1:1 — bitstream, DV RPU, HDR10/10+ metadata, timestamps    |
| Audio     | Copied 1:1 per checked track (codec must fit MP4)                 |
| Subtitles | **Not muxed.** MP4 subtitle support is too fragile to do reliably |

For subtitles, either keep an `.srt` side-car next to the `.mp4` (most TVs
auto-pick it up), or switch to a real codec mode which handles subtitle
conversion properly.

## Audio codec compatibility

| Codec               | Fits in MP4? | Notes                                  |
|---------------------|--------------|----------------------------------------|
| AAC, AC-3, E-AC-3, MP3 | ✅        | Standard MP4 audio                     |
| FLAC, Opus          | ⚠️           | Spec-allowed; older hardware may choke |
| **TrueHD**          | ❌           | Common in UHD-BluRay rips — not supported |
| DTS-HD MA           | ❌           | Not supported in MP4                   |

If the source has a TrueHD track, the GUI shows a warning before launch.
Deselect TrueHD tracks and keep only AC-3 / E-AC-3, or fall back to a
re-encode mode if TrueHD is the only audio.

## The FFmpeg command

```
ffmpeg -y -progress pipe:1 -loglevel error
       -i "source.mkv"
       -map 0:v:0 -map 0:1 -map 0:2
       -c:v copy -c:a copy -strict -2
       "source.mp4"
```

`-strict -2` is defensive — it explicitly allows E-AC-3 in MP4. No `-c:s` is
emitted because no subtitle streams are mapped.

## When NOT to remux

- **Reducing file size** — remux preserves bitrate exactly. Re-encode instead.
- **Changing resolution** — remux can't downscale. Use H.265/AV1 mode.
- **Source has TrueHD-only audio or MPEG-2 video** — won't fit in MP4.
- **Your player already handles DV in MKV** — modern Apple TV, recent firmware
  on many smart TVs do. Test playback first.

## Troubleshooting

- **Output a few hundred KB different in size:** normal — container framing
  overhead differs between MKV and MP4. The streams are byte-identical.
- **MP4 plays but not as DV:** likely a DV profile mismatch (profile 7
  dual-layer is the most fragile; profiles 5 and 8.1 are widely supported),
  or the source was HDR10 only — verify with MediaInfo.
- **"codec not currently supported in container":** TrueHD or MPEG-2 in the
  source. Deselect the offending track or re-encode.
- **Progress finishes in 5 seconds:** expected. The file is on disk — check
  next to the input.
