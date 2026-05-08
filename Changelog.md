# 📝 CHANGELOG - FFmpeg Converter GUI

---

## Version 3.0.2 (2026-05-08) — Subtitle handling & batch-timing hotfix

Focused bugfix release. Addresses a crash that hit certain MKV files during batch encoding: FFmpeg failed with `[sost#0:3/ssa] Subtitle encoding currently only possible from text to text or bitmap to bitmap` because the Matroska muxer's default subtitle encoder is text-based, and FFmpeg fell back to it whenever `-c:s copy` could not pass through. The root cause beneath that was a timing race in `process_next` — `setText()` triggered the async ffprobe and the synchronous pipeline ran before the probe finished, so `build_ffmpeg_command` saw stale stream data from the previous file. `process_next` is now split into pre-probe and post-probe halves, with the continuation `_process_next_after_probe` running only after `_on_video_info_loaded` has refreshed state. The subtitle build path additionally re-validates the combo selection against the current source, uses optional `-map 0:N?`, guards burn-in against picture-based subs, marks bitmap subs visually in the dropdown, and auto-retries once without subtitle in batch mode (skip is logged in `Message.log`). `build_nuitka.bat` and `build_nuitka_onefile.bat` bumped to `--file-version=3.0.2.0` / `--product-version=3.0.2.0`.

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
