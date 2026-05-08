# 📝 CHANGELOG - FFmpeg Converter GUI

> **ℹ️ Maintenance notice:** Only the **v3.0 line** is actively maintained going
> forward. Earlier versions (v2.x, v1.x) are kept here for historical reference
> only and will not receive bug fixes or feature updates. Please upgrade to the
> latest v3.0.x release.

---

## Version 3.0.2 (2026-05-08) — Subtitle handling & batch-timing hotfix

Focused bugfix release. Addresses a crash that hit certain MKV files during batch encoding: FFmpeg failed with `[sost#0:3/ssa] Subtitle encoding currently only possible from text to text or bitmap to bitmap` because the Matroska muxer's default subtitle encoder is text-based, and FFmpeg fell back to it whenever `-c:s copy` could not pass through. The root cause beneath that was a timing race in `process_next` — `setText()` triggered the async ffprobe and the synchronous pipeline ran before the probe finished, so `build_ffmpeg_command` saw stale stream data from the previous file. `process_next` is now split into pre-probe and post-probe halves, with the continuation `_process_next_after_probe` running only after `_on_video_info_loaded` has refreshed state. The subtitle build path additionally re-validates the combo selection against the current source, uses optional `-map 0:N?`, guards burn-in against picture-based subs, marks bitmap subs visually in the dropdown, and auto-retries once without subtitle in batch mode (skip is logged in `Message.log`). `build_nuitka.bat` and `build_nuitka_onefile.bat` bumped to `--file-version=3.0.2.0` / `--product-version=3.0.2.0`.

---

## Version 2.6 (2026) — PySide6 migration & dark mode

> *Historical entry — no longer maintained.*

### 🎉 NEW FEATURES

#### 🌙 Dark mode
**Live switching between light and dark themes** — no restart required.

**Components:**
- ✅ Icon toggle button (☾ / ☀) in the Advanced Settings, to the left of the "Default" button
- ✅ Choice is persisted in `Converter_settings.json` (key `theme`)
- ✅ Dark mode uses Fusion style + an explicit `QPalette` — robust on Windows
  (the native `windowsvista` style partially ignores palette changes at runtime)
- ✅ Light mode reverts to the native Windows style so the UI looks
  "native" there rather than a Fusion reskin
- ✅ Theme is read from settings at app start, before the
  MainWindow is built (no flicker)

#### 🔀 Framework migration: PyQt6 → PySide6
Identical behaviour, but LGPL instead of GPL — better suited for commercial
distribution.

---

## 📞 SUPPORT

### Reporting issues
- Check this CHANGELOG for known issues
- Review the documentation for your version
- Test with the latest v3.0.x release first — older versions are no longer maintained
- Provide: OS, FFmpeg version (`ffmpeg -version`), input file details (MediaInfo)

### Feature requests
- Check the ROADMAP first
- Describe the use case clearly
- Provide an example workflow

---

## 📄 LICENSE

**Freeware** — free for personal and commercial use

**Dependencies:**
- FFmpeg: GPL/LGPL (see ffmpeg.org)
- PySide6: LGPL v3 (Qt for Python)
- Python: PSF License
- psutil: BSD-3-Clause
- Nuitka: Apache 2.0 (build-time only)

---

**Current Version:** 3.0.2
**Release Date:** 2026-05-08
**Status:** ✅ Production Ready (v3.0 line — sole maintained branch)
**Author:** Silvestar Friedrich
**FFmpeg Requirement:** 8.0+
