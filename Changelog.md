# 📝 CHANGELOG - FFmpeg Converter GUI

---

## Version 3.0.2 (2026-05-08) — Subtitle-handling & large-file hotfix

Focused bugfix release covering two unrelated issues:

**Subtitle crash on certain MKV files.** FFmpeg failed with `[sost#0:3/ssa] Subtitle encoding currently only possible from text to text or bitmap to bitmap` because the Matroska muxer's default subtitle encoder is text-based, and FFmpeg fell back to it whenever `-c:s copy` could not pass through. The subtitle build path now re-validates the combo selection against the current source's `subtitle_streams`, uses optional `-map 0:N?` so a missing stream cannot abort the encode, guards burn-in against picture-based subs (PGS/DVB/DVD/xsub require an overlay path that libass does not provide), marks bitmap subs visually in the subtitle dropdown with `⚠ picture-based`, and auto-retries once without subtitle in batch mode if FFmpeg reports the subtitle-encoding error (skip is logged to `Message.log`). `SUB_CONVERTIBLE` and `SUB_PICTURE_BASED` are now class constants on `FFmpegGUI`, mirroring the values already used by `RemuxDialog`.

**`OverflowError` on files larger than 2 GB.** During encoding, `FFmpegWorker.progress_signal` was declared as `Signal(float, float, int)`. Qt's `int` maps to a signed 32-bit integer (max 2,147,483,647), so any `total_size_bytes` exceeding ~2.1 GB triggered `libshiboken: Overflow` warnings followed by `OverflowError` on every progress emit. The signal type is now `Signal(float, float, object)` — Python ints are passed through unchanged, the slot uses the value only in arithmetic (`predicted_file_size_mb`), and there is no upper bound.

`build_nuitka.bat` and `build_nuitka_onefile.bat` bumped to `--file-version=3.0.2.0` / `--product-version=3.0.2.0`.

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


### Reporting Issues
- Check this CHANGELOG for known issues
- Review documentation for your version
- Test with latest version first
- Provide: OS, FFmpeg version (`ffmpeg -version`), Input file details (MediaInfo)

### Feature Requests
- Check ROADMAP first
- Describe use-case clearly
- Provide example workflow

---

## 📄 LICENSE

**Freeware** - Free for personal and commercial use

**Dependencies:**
- FFmpeg: GPL/LGPL (see ffmpeg.org)
- PySide6: LGPL v3 (Qt for Python)
- Python: PSF License
- psutil: BSD-3-Clause
- Nuitka: Apache 2.0 (Build-Time only)

---

**Current Version:** 2.6 & 3.02
**Release Date:** 2026
**Status:** ✅ Production Ready
**Author:** Silvestar Friedrich
**FFmpeg Requirement:** 8.0+
