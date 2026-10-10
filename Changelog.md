# 📝 CHANGELOG - FFmpeg Converter GUI

---

> The repository contains only the current version (source `ffmpeg_converter_gui.py` and the Windows build `FFmpeg_Converter_GUI_v.3.04.exe`, each with a `.sha256`); older sources and builds were removed.

---

## Version 3.0.4 — Scrollable window, taskbar & tray progress

### New
- **Scrollable main window:** the complete content (including the progress row) sits in a scroll area. The window can be resized down to the top bar without clipping; a scrollbar appears when needed. The former minimum height of 620 px is gone.
- **Fixed top bar** (top right, always visible): 💾 *Save Settings* (also **Ctrl+S**), ☾/☀ theme toggle (moved here from the Advanced Settings row) and the new **Compact button** (⊟/⊞), which toggles the window height between full and about 50 %.
- **Window size and scroll position** are saved in `Converter_settings.json` (on close and on *Save Settings*) and restored at startup; *Default* resets them.
- **Windows taskbar progress:** green progress bar in the taskbar button (via `ITaskbarList3`, no extra dependency), yellow while paused, removed when finished. The window title shows the percentage during encoding.
- **Tray icon with percentage:** shown only while encoding — percentage with a progress ring (green, yellow when paused), tooltip with remaining time and batch position; clicking it restores the window.
- **Remux dialog theming:** theme-dependent accent colors (hints, warnings, errors, links) and a theme-matched *Start Remux* button, so the dialog fits the dark theme.
- **Window/taskbar icon** from `resources/icons/logo_styled.ico` (matches the EXE icon; falls back to `logo.png`) plus an explicit Windows AppUserModelID for correct taskbar grouping.

### Fixed (already in the 3.0.4 build)
- Theme switch now takes effect on the **first** click (Fusion style is installed once; color scheme first, then palette; visible windows are re-polished immediately).
- The saved theme is read at startup from the settings file next to the program (`sys.argv[0]`), so onefile builds start in the saved dark theme.
- ffprobe/FFmpeg output is decoded as UTF-8 (no `UnicodeDecodeError` with non-Latin tags or file names on Windows).
- Settings are written atomically (temp file + rename, UTF-8) and merged, so a crash cannot leave a half-written `Converter_settings.json`.
- Window title and About dialog show the correct version (were stuck on 3.0.2 / 3.0).

### Fixed in the source after the 3.0.4 build
> The included EXE is the original 3.0.4 build. The following fixes are in `ffmpeg_converter_gui.py` only and will be part of the next build.

- **Burn-in subtitles:** `si=` now receives the index among subtitle streams (it received the global stream index, so the wrong track was burned in or FFmpeg aborted). File names containing an apostrophe are escaped correctly.
- **MOV/WebM output:** `.mov` and `.webm` were missing from the allowed output extensions, so every encode to these selectable containers was rejected with *Invalid Output File*.
- **Remux dialog:**
  - External audio files now carry channel count and bitrate, so the downmix also applies to external multi-channel tracks (and the diagram shows their layout).
  - Audio-only extraction no longer maps subtitles — `.mka`/`.flac`/`.mp3` outputs failed whenever a subtitle was (pre-)selected.
  - The TrueHD warning is hidden when the source no longer contains TrueHD.
  - If the remux cannot start (e.g. invalid output extension), the dialog no longer stays locked in *Remuxing…*.
- **Batch folder:** selecting a folder on a different drive no longer raises `ValueError` (`os.path.commonpath`), and choosing a parent folder of the current batch folder now rescans it.
- Module docstring version updated to 3.0.4.

---

## Version 3.0.3 (2026-05-09) — Non-blocking batch errors

Removes the blocking `Batch Error` dialog that interrupted long-running batches whenever FFmpeg returned a non-zero exit code. The dialog stopped the batch and waited for a user click, which was catastrophic for unattended over-night runs — one broken file would freeze the queue for hours. The error path now logs to console, appends an `ERROR` block to a `Message.log` next to the failing input file (input/output/return code/last 30 lines of FFmpeg output), increments a new `batch_failed_count`, and continues with the next file. The end-of-batch toast distinguishes three buckets — `encoded`, `skipped`, `failed` — so a batch that finished with errors is visually flagged with `⚠️ Batch complete with errors` rather than the green checkmark. The pre-existing v3.0.2 subtitle auto-retry path is unchanged; this fix catches everything that retry doesn't (other FFmpeg errors, or the retry itself failing). The configuration-issue dialog in `process_single_file` (invalid output filename extension) is intentionally left as a hard stop, because it would affect every file in the batch identically.

`build_nuitka.bat` and `build_nuitka_onefile.bat` bumped to `--file-version=3.0.3.0` / `--product-version=3.0.3.0`.

---

## Version 3.0.2 (2026-05-08) — Subtitle-handling & large-file hotfix

Focused bugfix release covering two unrelated issues:

**Subtitle crash on certain MKV files.** FFmpeg failed with `[sost#0:3/ssa] Subtitle encoding currently only possible from text to text or bitmap to bitmap` because the Matroska muxer's default subtitle encoder is text-based, and FFmpeg fell back to it whenever `-c:s copy` could not pass through. The subtitle build path now re-validates the combo selection against the current source's `subtitle_streams`, uses optional `-map 0:N?` so a missing stream cannot abort the encode, guards burn-in against picture-based subs (PGS/DVB/DVD/xsub require an overlay path that libass does not provide), marks bitmap subs visually in the subtitle dropdown with `⚠ picture-based`, and auto-retries once without subtitle in batch mode if FFmpeg reports the subtitle-encoding error (skip is logged to `Message.log`). `SUB_CONVERTIBLE` and `SUB_PICTURE_BASED` are now class constants on `FFmpegGUI`, mirroring the values already used by `RemuxDialog`.

**`OverflowError` on files larger than 2 GB.** During encoding, `FFmpegWorker.progress_signal` was declared as `Signal(float, float, int)`. Qt's `int` maps to a signed 32-bit integer (max 2,147,483,647), so any `total_size_bytes` exceeding ~2.1 GB triggered `libshiboken: Overflow` warnings followed by `OverflowError` on every progress emit. The signal type is now `Signal(float, float, object)` — Python ints are passed through unchanged, the slot uses the value only in arithmetic (`predicted_file_size_mb`), and there is no upper bound.

`build_nuitka.bat` and `build_nuitka_onefile.bat` bumped to `--file-version=3.0.2.0` / `--product-version=3.0.2.0`.

---

## Version 3.0.1

No separate source, binary or release notes for 3.0.1 are included in this repository, so its changes cannot be listed reliably.

---

## Version 3.0 (2026) — Multi-vendor hardware encoding & Remux downmix

*Reconstructed from the v3.0 source (`ffmpeg_converter_gui__v30.py`) compared with v2.6.*

- **AMD AMF and Intel QSV** hardware encoders (AV1, H.265, H.264) in addition to NVIDIA NVENC. Device selection per vendor: `-init_hw_device cuda:N` (NVENC), `d3d11va=amf_dev:N` (AMF), `qsv=qsv_dev:hw_any` or a D3D11VA-derived device for multi-Intel setups (QSV).
- **Multi-vendor GPU detection:** `nvidia-smi` for NVIDIA, WMI (`wmic`, fallback PowerShell `Get-CimInstance`) for AMD/Intel on Windows, `lspci` on Linux. The encoder dropdown only lists the selected GPU vendor's hardware encoders plus software encoders.
- **GPU capability filter:** AV1 is only offered on GPUs that can encode it (RTX 40+, Radeon RX 7000/9000, Arc A/B); GPUs without a hardware encoder (GT 1030, MX450–470, RX 6400/6500) fall back to software encoders. (In-code comments label this part "v3.0.2"; it is already present in the v3.0 source.)
- NVENC-specific tunings (preset p7, tune uhq, multipass fullres, rc-lookahead) stay NVENC-only; Mediathek-Safe profile/level settings extended to AMF/QSV with the correct level format (`4.1` vs `41`). Force-AV1 is available for all AV1 encoders.
- **Remux dialog — multi-channel downmix:** 5.1/7.1 → 2.0 and 7.1 → 5.1 with explicit `pan` coefficients, *Music* (ITU-R BS.775, LFE kept) and *Movie* (cinema-balanced) modes, per-track re-encode only where needed, bitrate capped at `min(codec maximum, source bitrate)`, FLAC tip for lossy sources. The mode is persisted in the settings (`downmix_mode`).
- **ffprobe robustness:** duration falls back to the video-stream duration when the container has none (old MPEG-TS, VOB), multi-line and `N/A` answers are handled.
- **Fix:** Windows return codes above 2^31 (e.g. QSV init NTSTATUS errors) are converted to signed 32-bit before being emitted — previously `OverflowError` prevented the error dialog.

---

## Version 2.6 (2026) — PySide6 Migration & Dark Mode

### 🎉 NEW FEATURES

#### 🌙 Dark Mode
**Live-Wechsel zwischen hellem und dunklem Theme** — kein Restart nötig.

**Components:**
- ✅ Icon-Toggle-Button (☾ / ☀) in den Advanced Settings, links vom "Default"-Button
- ✅ Wahl wird in `Converter_settings.json` persistiert (Key `theme`)
- ✅ Dark-Mode nutzt Fusion-Style + explizite `QPalette` — robust unter Windows
  (der native `windowsvista`-Style ignoriert Palette-Änderungen zur Laufzeit z.T.)
- ✅ Light-Mode kehrt zum nativen Windows-Style zurück, damit das UI dort
  "nativ" aussieht und nicht nach Fusion-Reskin
- ✅ Theme wird beim App-Start direkt aus den Settings gelesen, bevor das
  MainWindow gebaut wird (kein Aufflackern)

#### 🔀 Framework-Migration: PyQt6 → PySide6
Identisches Verhalten, aber LGPL statt GPL — kompatibler für kommerzielle
Distribution.


## 🆘 Support

### Reporting Issues
- Check this CHANGELOG for known issues
- Review documentation for your version
- Test with latest version first
- Provide: OS, FFmpeg version (`ffmpeg -version`), Input file details (MediaInfo)

### Feature Requests
- Check existing GitHub issues first
- Describe use-case clearly
- Provide example workflow

---

## 📄 LICENSE

**Freeware** - released under the MIT License (see LICENSE), free for personal and commercial use

**Dependencies:**
- FFmpeg: GPL/LGPL (see ffmpeg.org)
- PySide6: LGPL v3 (Qt for Python)
- Python: PSF License
- psutil: BSD-3-Clause
- Nuitka: Apache 2.0 (Build-Time only)

---

**Current Version:** 3.0.4
**Status:** ✅ Production Ready
**Author:** WaveboSF
**FFmpeg Requirement:** 8.0+
