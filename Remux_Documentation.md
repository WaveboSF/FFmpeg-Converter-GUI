# Remux Mode (MKV → MP4) — Documentation

**Version:** v2.6
**Status:** Stable
**Use case:** Container conversion without re-encoding

---

## TL;DR

The **Remux only (Copy → MP4)** option in the *Video Codec* dropdown converts
an `.mkv` file into an `.mp4` file **without re-encoding the video or audio**.
It only swaps the container box. Operation takes seconds, video quality is
identical to the source, and **Dolby Vision metadata is preserved**.

```
┌─────────────────────────────────┐         ┌─────────────────────────────────┐
│  source.mkv                     │         │  source.mp4                     │
│  ┌───────────────────────────┐  │         │  ┌───────────────────────────┐  │
│  │ Video (HEVC + DV RPU)     │──┼────────▶│  │ Video (HEVC + DV RPU)     │  │
│  └───────────────────────────┘  │ identical│  └───────────────────────────┘  │
│  ┌───────────────────────────┐  │  bytes  │  ┌───────────────────────────┐  │
│  │ Audio Stream_A (eac3)     │──┼────────▶│  │ Audio Stream_A (eac3)     │  │
│  └───────────────────────────┘  │         │  └───────────────────────────┘  │
│  ┌───────────────────────────┐  │         │  ┌───────────────────────────┐  │
│  │ Audio Stream_B (ac3)      │──┼────────▶│  │ Audio Stream_B (ac3)      │  │
│  └───────────────────────────┘  │         │  └───────────────────────────┘  │
│  ┌───────────────────────────┐  │         │                                 │
│  │ Subtitle Stream_C (srt)   │  │  dropped│   (subtitles not muxed —        │
│  └───────────────────────────┘  │ ────╳   │    see "Subtitles" below)       │
└─────────────────────────────────┘         └─────────────────────────────────┘
       MKV container                              MP4 container
```

The arrows mean "byte-identical copy". No frames are decoded or re-encoded.
A 50 GB UHD remux completes in under a minute on a typical NVMe SSD —
it's essentially a `cp` with metadata translation.

---

## Why this exists

Many TVs and hardware players (LG OLED, certain Samsungs, Apple TV, some
streaming sticks) **only render Dolby Vision metadata when the file is
in an MP4 container**. The exact same HEVC video stream — with the same
RPU layer carrying the DV metadata — plays as plain HDR10 or even SDR
when offered in an MKV container, but lights up as Dolby Vision when
the bytes sit inside an MP4 box.

This is purely a player-side limitation; nothing about the video itself
changes. The remux just translates the container metadata so the player
recognizes the file as DV-capable.

---

## How to use it

1. Load a source file via the **Input** field (browse, drag-and-drop,
   or type a path).
2. In the **Video Codec** dropdown, select **`Remux only (Copy → MP4)`**.
3. The GUI will gray out everything that doesn't apply:
   - Bitrate slider (no encoding → no bitrate)
   - Strict VBV checkbox
   - Mediathek-Safe checkbox
   - 4K → 1080p checkbox
   - Audio Codec (forced to `Copy`)
   - Audio Bitrate
   - Subtitles dropdown (forced to `None`)
   - Burn-in checkbox
4. Optionally uncheck audio tracks you don't want in the output. By
   default, all detected tracks are included.
5. Press **Run**. The output file ends up next to the source with
   `.mp4` as the new extension (no codec marker, since the codec
   didn't change).

The command preview shows exactly what FFmpeg will execute. For a
file with two audio tracks both selected, it looks like:

```
ffmpeg -y -progress pipe:1 -nostats -loglevel error
       -i "Stream_Source.mkv"
       -map 0:v:0 -map 0:1 -map 0:2
       -c:v copy -c:a copy -strict -2
       "Stream_Source.mp4"
```

---

## What gets transferred — and what doesn't

### Video stream

The first video stream from the source is mapped 1:1. This includes:

- The compressed video data (HEVC, AV1, H.264 — whatever was there)
- HDR metadata (HDR10, HDR10+, **Dolby Vision RPU layer**)
- Pixel format and color information
- Frame timestamps

```
  source video stream                 output video stream
  ┌──────────────────┐                 ┌──────────────────┐
  │ HEVC bitstream   │  ─── copy ───▶ │ HEVC bitstream   │
  │ + DV RPU layer   │                 │ + DV RPU layer   │
  │ + HDR10 metadata │                 │ + HDR10 metadata │
  └──────────────────┘                 └──────────────────┘
```

### Audio streams

Each audio track checked in the **Audio Tracks** panel is mapped 1:1.
The audio codec must be MP4-compatible:

| Codec    | Fits in MP4? | Notes                                        |
|----------|--------------|----------------------------------------------|
| AAC      | ✅           | Native MP4 audio, always works               |
| AC-3     | ✅           | Standard MP4 audio                           |
| E-AC-3   | ✅           | Standard in WEB-DLs (often Atmos)            |
| MP3      | ✅           | Works                                        |
| **TrueHD** | ❌         | **Not supported in MP4** — see "Pitfalls"    |
| DTS-HD MA| ❌           | Not supported in MP4                         |
| FLAC     | ⚠️           | Technically supported, but player support is patchy |
| Opus     | ⚠️           | MP4 supports Opus since 2018, but old hardware players choke |

If a source has multiple audio tracks of different codecs, the user can
selectively include only the MP4-compatible ones via the track
checkboxes:

```
  ☑ Stream_A (GER) [eac3]    ← keep
  ☐ Stream_B (ENG) [truehd]  ← skip (would break the remux)
  ☑ Stream_C (ENG) [eac3]    ← keep
```

### Subtitles

**Subtitles are intentionally not transferred during remux.** MP4's
subtitle support is a minefield: SRT works, ASS/SSA needs format
conversion, and PGS (BluRay graphical subs) cannot live in MP4 at all.
Rather than attempt format-by-format triage with hidden failures, the
remux mode produces a clean MP4 with no subtitles.

If you need subtitles, two clean workarounds:

1. **Side-car file:** Extract subtitles separately with MKVToolNix and
   place an `.srt` file next to the `.mp4`. Most TVs auto-pick up
   side-car subs.
2. **Re-encode instead:** Use one of the real codec modes (H.265, AV1)
   which handle subtitle burn-in and proper sub-stream selection
   already.

---

## Pitfalls

### TrueHD audio

If the source contains a TrueHD stream and you press **Run** in remux
mode, the GUI shows a warning dialog:

```
⚠️ TrueHD audio detected — MP4 cannot contain TrueHD streams.

The selected source has at least one TrueHD audio track (typical for
UHD-BluRay rips). MP4 containers only support AC-3, E-AC-3 (eac3),
AAC, and a few other codecs — but not TrueHD.

Options:
• Yes — proceed anyway (FFmpeg will likely abort with an error)
• No  — cancel; you can switch to a real codec (H.265/AV1) and
        re-encode, which will also transcode the audio to a
        compatible format

Tip: WEB-DL sources usually ship E-AC-3 (Atmos) tracks, which DO
fit into MP4 — only physical-disc rips tend to have TrueHD.
```

**Recommendation:** if you only need DV preservation and the source has
TrueHD, deselect the TrueHD track in the Audio Tracks panel and keep
only AC-3 / E-AC-3 tracks. If the source has *only* TrueHD, you cannot
remux — re-encoding (H.265 / AV1) is the only path forward.

### Dolby Vision profile compatibility

DV exists in several profiles (4, 5, 7, 8.1, 8.4 etc.). Remuxing
preserves whatever profile is in the source — but not all players
accept all profiles in MP4. Most consumer hardware accepts profile 5
and profile 8.1 in MP4. Profile 7 (dual-layer) is the most fragile.

This GUI doesn't inspect the DV profile; if your TV refuses the output
file, the issue is profile compatibility and not the remux itself.

### Source codecs that don't fit in MP4

Beyond audio, certain video codecs also don't fit in MP4 — primarily
older ones like MPEG-2 Video. If the source uses such a codec, FFmpeg
will abort with a "codec not currently supported in container" error.
For 99% of modern source material (HEVC, AV1, H.264) this is a non-issue.

---

## What the FFmpeg command looks like

The full command emitted by the GUI for a typical case:

```
ffmpeg -y -progress pipe:1 -nostats -loglevel error
       -i "Stream_Source.mkv"
       -map 0:v:0
       -map 0:1
       -map 0:2
       -c:v copy
       -c:a copy
       -strict -2
       "Stream_Source.mp4"
```

Breakdown of the relevant flags:

| Flag                  | Purpose                                              |
|-----------------------|------------------------------------------------------|
| `-y`                  | overwrite output without prompting (GUI handles UI prompt) |
| `-progress pipe:1`    | machine-readable progress for the GUI               |
| `-loglevel error`     | hide info chatter, surface real errors              |
| `-i <input>`          | source file                                          |
| `-map 0:v:0`          | take the first video stream from input #0           |
| `-map 0:N`            | take audio stream with absolute index N (per checkbox) |
| `-c:v copy`           | **do not re-encode video** (preserves DV exactly)    |
| `-c:a copy`           | **do not re-encode audio** (preserves bitrate exactly) |
| `-strict -2`          | allow E-AC-3 in MP4 (officially supported in FFmpeg 8, defensive flag) |

No `-c:s` is emitted because no subtitle streams are mapped — see
"Subtitles" above.

---

## What the user sees in the UI

```
┌─ Settings ────────────────────────────────────────────────────────┐
│ Actual Bitrate (Mbit/s):  6.23                                    │
│ Recommended Bitrate ────────────[disabled]──────────  6.0M (96%)  │
│ Video Codec:  [Remux only (Copy → MP4)        ▼]                  │
│ 4K→1080p:  ☐ [disabled]    Mediathek-Safe:  ☐ [disabled]          │
│                                                                   │
│ Audio Codec:  [Copy ▼] [disabled]    Bitrate (kbps):  [disabled]  │
│ Audio Tracks:                                                     │
│   ☑ Stream_A (GER) [eac3]    ☑ Stream_B (ENG) [eac3]              │
│ Subtitles:  [None ▼] [disabled]      Burn-in:  ☐ [disabled]       │
└───────────────────────────────────────────────────────────────────┘
```

The disabled controls are visually grayed out but still show their
last-used values — switching back to a real codec mode restores them
exactly as they were.

---

## When NOT to use remux mode

- **You want to reduce file size.** Remux doesn't change the bitrate.
  Use H.265 or AV1 with the bitrate slider instead.
- **You want to change resolution.** Remux can't downscale. Use a real
  codec mode with the 4K → 1080p checkbox.
- **The source has incompatible streams.** TrueHD audio, PGS subtitles,
  or older video codecs (MPEG-2). Re-encode instead.
- **Your player doesn't care about the container.** Some modern devices
  (recent Apple TVs, modern smart TVs running latest firmware) handle
  DV in MKV just fine. Test playback first; if MKV plays as DV on your
  display, no remux needed.

---

## Troubleshooting

**"Output file is larger / smaller than the input."**
That's normal. MP4 and MKV have different overhead per stream. The
audio/video bitstreams are identical; only the container framing
differs by a few hundred KB.

**"My TV plays the MP4 but not as Dolby Vision."**
Three possible causes, in order of likelihood:
1. Your TV's DV implementation requires a specific profile that the
   source doesn't use (see "DV profile compatibility" above).
2. Your TV needs DV signaled via HDMI metadata that the player app
   doesn't pass through.
3. The source MKV didn't actually have DV in the first place — it was
   plain HDR10. Inspect the source with MediaInfo to confirm.

**"FFmpeg aborts with 'codec not currently supported in container'."**
The audio (likely TrueHD) or video (likely MPEG-2) codec doesn't fit
in MP4. Either deselect the offending audio track, or switch to
re-encoding mode.

**"Progress bar finishes in 5 seconds and then nothing happens."**
That's expected — remux is fast. Check that the output file exists
next to the input. The "elapsed" timer in the GUI shows the actual
duration.

---

## Why remux to MP4 specifically, not other containers?

MP4 was chosen as the target container because:

- **Hardware DV support** is concentrated in MP4 across consumer
  devices.
- **iOS / macOS native playback** prefers MP4.
- **Most streaming uploaders** require MP4.
- **MOV** (Apple) is technically very similar to MP4 but adds nothing
  for this use case.
- **MKV → MKV** would be a no-op.

If you need a different target container (e.g. `.ts` for streaming),
that would be a separate feature; the current remux mode is
specifically MP4-targeted.
