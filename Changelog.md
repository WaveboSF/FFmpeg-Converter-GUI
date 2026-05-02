# 📝 CHANGELOG - FFmpeg Converter GUI

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

### 🔧 CHANGED

- 🔧 **Remux-Dialog bleibt offen** nach "Start Remux" — beschleunigt
  Multi-File-Workflows (kein erneutes Öffnen pro Datei)
- 🔧 **nvidia-smi-Parsing gefixt:** GPU-Namen mit "(UUID"-Suffix werden
  jetzt korrekt erkannt
- 🔧 **Encoder-Combo auto-sized** auf den längsten GPU-Namen, damit
  lange Bezeichner nicht abgeschnitten werden

---

## Version 2.5 (2025) — FFmpeg 8.0 Modernisierung

### 🎉 NEW FEATURES

#### 🎬 Remux MKV → MP4 (Eigenständiger Dialog)
**Major Feature:** Container-only Konversion ohne Re-Encoding

**Components:**
- ✅ Eigenständiger `RemuxDialog` mit Workflow-Diagramm
- ✅ Echte Widget-Hierarchie statt HTML-Label (anklickbare Source/Output-Container)
- ✅ Live-Diagramm passt sich automatisch an geladene Streams an
- ✅ Source und Output direkt im Dialog wählbar (nicht mehr nur Hauptfenster)
- ✅ Stream-Selection per Checkbox (Audio + Subtitles)
- ✅ "All/None"-Helper für Bulk-Selection
- ✅ Apple-Toggle (`hvc1`-Tag) für QuickTime/Apple-Kompatibilität
- ✅ Eingebettete Doku per Toggle einblendbar
- ✅ Erhält Dolby Vision Metadata 100%

**Use-Cases:**
- MKV → MP4 ohne Qualitätsverlust
- Apple-TV/iOS-Kompatibilität (hvc1-Tag)
- DV-Profile-Erhaltung

#### 🎵 Externe Audio/Subtitle-Files (Multi-Input)
**Im Remux-Dialog:** Audio- oder Subtitle-Spuren aus separaten Dateien einbinden

- ✅ "+ Add external audio…" Button
- ✅ "+ Add external subtitle…" Button
- ✅ Multi-Input ffmpeg-Command (mehrere `-i` Inputs)
- ✅ Synchroner ffprobe-Call für externe Files
- ✅ Sync-Warnung bei Dauerunterschied zwischen Source und externer Spur
- ✅ Pro-File Enable/Disable per Checkbox
- ✅ Externe Subtitles werden als `mov_text` gemuxt

#### 📊 Codec-aware Recommended Bitrate
**Major Feature:** Smart Defaults statt pauschaler 50%-Empfehlung

**Hintergrund:**
H.265-Quellen haben weniger Headroom als H.264. Die alte 50%-Pauschale führte
bei H.265 → AV1 zu Blockartefakten. Die neue Matrix berücksichtigt
Source-Codec, Target-Codec UND Auflösung.

**Bitrate-Multiplier-Matrix:**

| Source → Target | H.264 | H.265 | AV1  |
|-----------------|-------|-------|------|
| **H.264**       | 0.50  | 0.35  | 0.30 |
| **H.265**       | 1.00  | 0.70  | 0.60 |
| **AV1**         | 2.00  | 1.20  | 0.80 |

**Quality-Floor (minimum Bitrate):**

| Resolution | AV1   | H.265 | H.264  |
|------------|-------|-------|--------|
| **1080p**  | 2.0M  | 2.5M  | 4.0M   |
| **4K**     | 6.0M  | 8.0M  | 12.0M  |

Quality-Floor wird angewendet, außer wenn Source-Bitrate selbst schon drunter
liegt (dann wird Source nicht überschritten).

**Slider-Verhalten:**
- ✅ Slider-Maximum auf Source-Bitrate gecappt (Re-Encoding über Source-Bitrate macht keinen Sinn)
- ✅ Label zeigt jetzt Prozent-Wert: `5.9M (47%)`
- ✅ Manueller User-Override wird respektiert (kein Auto-Override mehr nach Codec-Wechsel)

#### 🎚️ Strict VBV Checkbox (Variante C)
**Bitrate-Compliance optional umschaltbar**

- ✅ **AUS (default):** `maxrate = 2× target`, kein bufsize → weicher Cap, Quality-First
- ✅ **AN:** `maxrate = target`, `bufsize = 2× maxrate` → harter VBV-konformer Cap

#### 🖥️ Multi-GPU Support (Modern FFmpeg 8.0)
**Breaking-Change in der ffmpeg-Syntax**

- ✅ NVENC GPU-Selection via `-init_hw_device cuda:N` (global, vor `-i`)
- ❌ Altes codec-lokales `-gpu N` entfernt (deprecated, in Multi-GPU-Setups unzuverlässig)
- ✅ Mehrere GPUs werden automatisch erkannt und im Dropdown angeboten

#### 📦 Container-Format-Matrix
**UI filtert Codec-Dropdowns je nach Container**

| Container | Video-Codecs                               | Audio-Codecs                                  |
|-----------|--------------------------------------------|-----------------------------------------------|
| **MKV**   | AV1, H.265, H.264 (NVENC + SW), VP9        | Copy, AAC, AC-3, E-AC-3, MP3, Opus, FLAC      |
| **MP4**   | AV1, H.265, H.264 (NVENC + SW)             | Copy, AAC, AC-3, E-AC-3, MP3, Opus, FLAC      |
| **MOV**   | H.265, H.264 (NVENC + SW)                  | Copy, AAC, AC-3, MP3                          |
| **WebM**  | AV1 (NVENC + SW), VP9                      | Opus, Vorbis                                  |

WebM ist absichtlich strikt nach Standard.

#### 🎵 Volle Audio-Codec-Auswahl
**Nicht mehr nur Copy/Opus/AAC**

- ✅ Neue Codecs: AC-3, E-AC-3, MP3, FLAC, Vorbis
- ✅ FFmpeg-Encoder-Mapping: AAC→aac, AC-3→ac3, E-AC-3→eac3, MP3→libmp3lame, Opus→libopus, FLAC→flac, Vorbis→libvorbis

#### 💾 Portable Config
**USB-Stick-friendly**

- ✅ Settings-Datei liegt jetzt **neben dem Programm** (nicht mehr in AppData)
- ✅ One-Shot-Migration aus alter AppData-Location bei erstem v2.5-Start
- ✅ Persistiertes letztes Browse-Verzeichnis für File-Dialogs
- ✅ Smart Browse-Dir Fallback: letzter Pfad → Drive-Root → System-Root

#### 🆔 Source-Codec Detection
**Codec-Normalisierung für Bitrate-Logik**

- ✅ ffprobe ermittelt jetzt auch den Video-Codec der Source
- ✅ Normalisierung auf `h264`/`h265`/`av1`/`unknown`
- ✅ VP9 wird als `h265` behandelt (ähnliche Effizienz)
- ✅ `VideoInfo.video_codec` Feld neu in Dataclass

### 🔧 CHANGED

#### FFmpeg 8.0 Syntax-Migration
- `-gpu N` (codec-lokal) → `-init_hw_device cuda:N` (global)
- `-vsync cfr` → `-fps_mode cfr` (vsync deprecated)

#### UI-Layout
- Fensterbreite explizit auf 1280px (Atemraum für Settings-Zeile)
- Subtitles aus dem Grid raus, sitzt jetzt rechts in der Codec-Zeile
- Codec-Zeile neu strukturiert: Video Codec | Container | 4K | Mediathek
- Settings-Group bekommt VBoxLayout als äußeren Container
- Slider-Label initial ohne Prozent (kein Source-Reference-Punkt)
- Bitrate-Label umbenannt (Default ist jetzt eine Empfehlung, kein Pauschalwert)

#### Console-Logging
- `build_ffmpeg_command` bekommt `log`-Parameter (default `False`)
- Preview-Updates produzieren keinen Console-Spam mehr
- Status-Meldungen kommen genau einmal pro Encoding-Run (in `process_single_file`)

#### Bitrate-Reset bei File-Wechsel
- Bei jedem neuen File-Load wird das User-Override-Flag zurückgesetzt
- Neue Datei → Recommended-Bitrate für deren Codec/Auflösung
- Keine "klebende" alte Bitrate mehr über Files hinweg

### 📦 NEW DIALOGS / WIDGETS
- `RemuxDialog` (eigenständiges Fenster)
- `ClickableLabel` (QLabel mit clicked-Signal)
- `ClickableFrame` (QFrame mit clicked-Signal)

---

## Version 2.0 (2025) — Audio-Track Defaults & Cleanup

### 🔧 CHANGED
- ✅ Default-Audio-Track-Selection: ALLE Tracks (vorher nur erster Track)
  → Verhalten wie Original-File, kein versehentliches Verlieren von Tracks
- ✅ Settings persistiert in AppData (vor v2.5-Migration zu portable)

### 🐛 BUG FIXES
- ✅ Diverse Stabilitäts-Fixes seit v1.5.4

---

## Version 1.5.4 (2025) — Stabilitäts-Release

### 🔧 CRITICAL FIXES

#### Robuste Shutdown-Sequenz
- ✅ `force=True` Parameter überspringt Graceful-Phase und killt sofort
- ✅ psutil-basierter Children-Kill als Fallback wenn taskkill nicht durchkam
- ✅ Zombie-Prevention deutlich verbessert

#### Slider-Override-Handling
- ✅ Tracking ob User die Bitrate manuell verändert hat
- ✅ `actionTriggered` feuert NUR bei User-Aktionen (Maus/Tastatur), nicht bei programmatischen `setValue()`-Calls
- ✅ Recommended-Bitrate überschreibt manuelle Eingaben nicht mehr

#### UI-Refresh nach File-Wechsel
- ✅ Audio-Track-Checkboxen und Subtitle-Combo werden korrekt geleert
- ✅ Keine Geister-Tracks der vorherigen Datei mehr sichtbar

#### Batch-Modus
- ✅ Counter für übersprungene Files (korrekte Toast-Meldung)
- ✅ Im rekursiven Batch-Modus IMMER neben das Input-File schreiben
- ✅ Skipped Files werden in `Message.log` protokolliert

---

## Version 1.5.3 (2025) — Settings & Mediathek-Constraints

### 🎉 NEW FEATURES

#### Settings-Trennung
- ✅ Save lädt/speichert NUR Encoder-Settings, KEINE Pfade
- ✅ Saubere Trennung von Defaults und Session-Daten

#### Mediathek-Bitrate-Constraint
- ✅ Mediathek-Safe Checkbox automatisch deaktiviert wenn Bitrate > 8.0 Mbps
- ✅ Tooltip erklärt warum (Mediathek-Player limitieren bei höheren Bitraten)

---

## Version 1.5.2 (2025) — Mediathek-Safe Mode

### 🎉 NEW FEATURES

#### 📺 Mediathek-Safe Mode
**Major Feature:** Fix für Black-Screen-Probleme bei ARD/ZDF-Mediathek-Downloads

**Input Options (vor `-i`):**
- ✅ `-fflags +genpts` — Regeneriert Timestamps
- ✅ `-avoid_negative_ts make_zero` — Behebt negative Timestamps

**Output Options:**
- ✅ `-vsync cfr` (später `-fps_mode cfr`) — Erzwingt konstante Frame-Rate
- ✅ `-pix_fmt yuv420p` — Force 8-bit (kein 10-bit Output)

**NVENC-spezifische Compatibility-Fixes:**
- ✅ `-g 48` — Keyframe alle 48 Frames (~2 Sek bei 24fps)
- ✅ `-strict_gop 1` — Strict GOP-Struktur

**Codec-Profile/Level:**
- ✅ H.264 NVENC: `profile=high`, `level=4.1`
- ✅ HEVC NVENC: `profile=main`, `level=4.1`
- ✅ AV1 NVENC: nur GOP (kein Profile/Level nötig)

#### Force AV1 Compatibility Mode
**Sub-Feature:** Experimentelle Color-Space-Fixes für AV1

- ✅ `color_primaries=bt709`, `color_trc=bt709`
- ✅ `colorspace=bt709`, `color_range=tv`
- ✅ Sichtbar nur wenn AV1 NVENC + Mediathek-Safe aktiv

#### Pixel Format Auto-Detection
- ✅ Source-Pixel-Format wird gespeichert (z.B. `yuv420p`, `yuv420p10le`)
- ✅ `VideoInfo.pix_fmt` Feld neu in Dataclass

---

## Version 1.5.1 (2025) — Toast-System

### 🎉 NEW FEATURES

#### 🎉 Toast-Notification
- ✅ Visueller Erfolgs-Toast nach Batch-Completion
- ✅ Countdown-Auto-Dismiss
- ✅ Manuell schließbar

#### UI während Encoding
- ✅ Save/About/Pause/Cancel-Buttons bleiben aktiv (nicht mehr disabled)
- ✅ User kann während Encoding Settings einsehen / About öffnen

---

## Version 1.5 (2025) — Robust Cancel & UI-Polish

### 🎉 NEW FEATURES

#### Robust Cancel System
- ✅ Sofortiger Batch-Stop bei Cancel
- ✅ Graceful Shutdown mit Eskalation (terminate → kill)
- ✅ Keine Zombie-Prozesse mehr

#### UI Polish
- ✅ "Default"-Button (rechts neben About) für Reset-to-Defaults
- ✅ Run-Button mit dezentem Grün (flach, kein 3D-Effekt)

---

## Version 1.2 Enhanced (2025-10-31)

### 🎉 NEW FEATURES

#### 📐 Resolution Scaling
**Major Feature:** Flexible Video-Skalierung mit High-Quality-Algorithmen

**Components:**
- ✅ Preset-Dropdown: Original, 4K, 2K, 1080p, 720p, Custom
- ✅ Custom Width/Height Input-Felder
- ✅ "Keep Aspect Ratio" Checkbox mit Auto-Berechnung
- ✅ Scaling-Algorithmus Auswahl: Lanczos, Bicubic, Bilinear, Fast Bilinear
- ✅ Visual Feedback: Input-Resolution wird angezeigt
- ✅ Auto-Detection der Input-Resolution (async)
- ✅ Settings Persistence (Speichern/Laden)

**Use-Cases:**
- 4K → 1080p Archivierung (80-90% Platzeinsparung mit AV1)
- 1080p → 720p für Mobile-Devices
- Custom Aspect Ratios für spezielle Anwendungen
- Batch-Downscaling ganzer Collections

**Technical Implementation:**
- VideoInfo Dataclass erweitert um `width`, `height`, `resolution_str`
- VideoInfoWorker: Neue Methode `_get_resolution_safe()`
- FFmpegGUI: Neue Methode `_build_scaling_filter()`
- Command-Generation erweitert um `-vf scale=W:H:flags=algo`
- UI-Callbacks: `on_resolution_preset_changed()`, `on_resolution_manual_change()`, `on_keep_aspect_ratio_changed()`

**Documentation:**
- `RESOLUTION_SCALING_DOCUMENTATION.md` (32 Seiten)
- `RESOLUTION_SCALING_QUICK_START.md` (Quick-Reference)

---

## Version 1.1 Enhanced (2025-10-30)

### 🎉 NEW FEATURES

#### 🎬 Dolby Atmos Auto-Protection
**Major Feature:** Automatischer Schutz von Dolby Atmos Audio-Streams

**Components:**
- ✅ Automatische Erkennung von TrueHD + Atmos
- ✅ Automatische Erkennung von E-AC-3 JOC (Atmos)
- ✅ Rotes "🎬 DOLBY ATMOS" Label mit auffälligem Styling
- ✅ UI Auto-Lock: Audio-Codec auf "Copy" gezwungen
- ✅ Alle Audio-Tracks automatisch aktiviert + gesperrt
- ✅ Informative Tooltips für User-Guidance
- ✅ Batch-Mode Support: Master-Config propagiert Atmos-Status
- ✅ Console-Feedback mit 🎬 Symbolen

**Technical Implementation:**
- VideoInfo Dataclass erweitert um `has_atmos`, `atmos_stream_indices`
- VideoInfoWorker: Neue Methode `_detect_atmos()`
- FFmpegGUI: Neue Methoden `_activate_atmos_protection()`, `_clear_atmos_state()`
- Command-Generation: Erzwingt `-c:a copy` bei Atmos-Detection
- UI-Components: Styled QLabel für Atmos-Indikator

**Detection Logic:**
- TrueHD: `codec_name == 'truehd' AND 'atmos' in (profile OR tags)`
- E-AC-3: `codec_name == 'eac3' AND ('atmos' OR 'joc') in (profile OR tags)`

**Documentation:**
- `Dolby_Atmos_Feature_Documentation.md` (42 Seiten)
- `Atmos_Quick_Start.md` (User-friendly Guide)

---

### 🔧 CRITICAL FIXES (from v1.0)

#### Thread-Safety Improvements
- ✅ QMutex für Worker-Zugriff implementiert
- ✅ Neue Methode: `_is_worker_running()` (thread-safe check)
- ✅ QMutexLocker in allen kritischen Sections:
  - `run_ffmpeg()`
  - `toggle_pause()`
  - `cancel_encoding()`
  - `process_single_file()`
  - `closeEvent()`
- ✅ Race Conditions behoben

#### Async Video Info Loading
- ✅ Neue Klasse: `VideoInfoWorker` (QRunnable-basiert)
- ✅ Neue Dataclass: `VideoInfo` (Type-Safe)
- ✅ QThreadPool Integration (max 4 parallele Threads)
- ✅ Timeout-Protection (10 Sekunden pro ffprobe-Call)
- ✅ Keine UI-Blockierung mehr bei großen Dateien
- ✅ Callback-System: `_on_video_info_loaded()`

**Affected Functions:**
- `get_video_duration()` → Jetzt in VideoInfoWorker
- `get_video_fps()` → Jetzt in VideoInfoWorker
- `get_stream_info()` → Jetzt in VideoInfoWorker
- `on_input_change()` → Nutzt jetzt async loading

#### Enhanced Error Recovery
- ✅ FFmpegWorker: Graceful Shutdown mit Escalation (terminate → kill)
- ✅ Zombie-Prozess-Prevention mit `_ensure_process_terminated()`
- ✅ Try-Finally für garantiertes Cleanup
- ✅ Timeout-Handling für `Process.wait()`
- ✅ Better Exception Handling in `run()` Methode

**New Methods in FFmpegWorker:**
- `_force_kill()` - Erzwingt Prozess-Terminierung
- `_ensure_process_terminated()` - Cleanup-Garantie

---

## Version 1.0 (Original - 2024)

### ✨ INITIAL FEATURES

#### Core Functionality
- ✅ FFmpeg GUI Wrapper für Video-Encoding
- ✅ GPU-Encoding Support (NVENC: AV1, H.265, H.264)
- ✅ CPU-Encoding Fallback (libx265, libx264)
- ✅ Audio-Codec Auswahl: Copy, Opus, AAC
- ✅ Multi-Track Audio Support
- ✅ Subtitle Support (Copy oder Burn-in)
- ✅ Bitrate-Slider (0.1 - 20 Mbps)
- ✅ Command Preview
- ✅ Progress-Tracking mit ETA
- ✅ Pause/Resume Funktion
- ✅ Settings Persistence (JSON)

#### Advanced Settings
- ✅ NVENC Preset (p7)
- ✅ NVENC Tune (uhq)
- ✅ Multipass Encoding
- ✅ RC Lookahead

#### Batch Processing
- ✅ Batch Folder Input
- ✅ Output Folder Configuration
- ✅ Master-Config System
- ✅ Audio-Compatibility Check
- ✅ Automatic File Processing

#### UI Components
- ✅ File Path Input (Batch Folder, Input, Output)
- ✅ Video Codec Selection
- ✅ Audio Codec Selection
- ✅ Audio Track Selection (Checkboxes)
- ✅ Subtitle Selection
- ✅ Progress Bar mit Percentage
- ✅ Speed Indicator (fps, multiplier)
- ✅ Remaining Time Estimate
- ✅ Predicted File Size

---

## 📊 FEATURE COMPARISON

| Feature                     | v1.0 | v1.1 | v1.2 | v1.5.x | v2.0 | v2.5 | v2.6 |
|-----------------------------|------|------|------|--------|------|------|------|
| **Basic Encoding**          | ✅   | ✅   | ✅   | ✅     | ✅   | ✅   | ✅   |
| **GPU Support**             | ✅   | ✅   | ✅   | ✅     | ✅   | ✅   | ✅   |
| **Multi-GPU Support**       | ❌   | ❌   | ❌   | ⚠️     | ⚠️   | ✅   | ✅   |
| **Batch Mode**              | ✅   | ✅   | ✅   | ✅     | ✅   | ✅   | ✅   |
| **Pause/Resume**            | ✅   | ✅   | ✅   | ✅     | ✅   | ✅   | ✅   |
| **Thread-Safety**           | ❌   | ✅   | ✅   | ✅     | ✅   | ✅   | ✅   |
| **Async Video Info**        | ❌   | ✅   | ✅   | ✅     | ✅   | ✅   | ✅   |
| **Error Recovery**          | ⚠️   | ✅   | ✅   | ✅✅   | ✅✅ | ✅✅ | ✅✅ |
| **Dolby Atmos Protection**  | ❌   | ✅   | ✅   | ✅     | ✅   | ✅   | ✅   |
| **Resolution Scaling**      | ❌   | ❌   | ✅   | ✅     | ✅   | ✅   | ✅   |
| **Mediathek-Safe Mode**     | ❌   | ❌   | ❌   | ✅     | ✅   | ✅   | ✅   |
| **Toast Notifications**     | ❌   | ❌   | ❌   | ✅     | ✅   | ✅   | ✅   |
| **Codec-aware Bitrate**     | ❌   | ❌   | ❌   | ❌     | ❌   | ✅   | ✅   |
| **Strict VBV Toggle**       | ❌   | ❌   | ❌   | ❌     | ❌   | ✅   | ✅   |
| **Container Matrix**        | ❌   | ❌   | ❌   | ❌     | ❌   | ✅   | ✅   |
| **Full Audio-Codec List**   | ❌   | ❌   | ❌   | ❌     | ❌   | ✅   | ✅   |
| **Remux MKV→MP4**           | ❌   | ❌   | ❌   | ❌     | ❌   | ✅   | ✅   |
| **External Audio/Sub**      | ❌   | ❌   | ❌   | ❌     | ❌   | ✅   | ✅   |
| **Portable Config**         | ❌   | ❌   | ❌   | ❌     | ❌   | ✅   | ✅   |
| **FFmpeg 8.0 Syntax**       | ❌   | ❌   | ❌   | ❌     | ❌   | ✅   | ✅   |
| **PySide6 (LGPL)**          | ❌   | ❌   | ❌   | ❌     | ❌   | ❌   | ✅   |
| **Dark Mode**               | ❌   | ❌   | ❌   | ❌     | ❌   | ❌   | ✅   |

---

## 🐛 BUG FIXES

### v2.5
- Console-Spam bei Preview-Updates eliminiert (`log`-Parameter)
- Slider-Max wurde nicht zurückgesetzt bei File-Unload
- Externe Files wurden im Audio-Count nicht mitgezählt

### v2.0
- Default-Audio-Track-Verhalten korrigiert (alle Tracks statt nur erster)

### v1.5.4
- Zombie-Prozesse bei Cancel/Crash (psutil-Fallback ergänzt)
- Slider-Override-Tracking (User-Eingaben wurden überschrieben)
- UI-Refresh nach File-Wechsel (Geister-Tracks)
- Batch-Output-Pfad im rekursiven Modus
- Skipped-File-Counter in Toast-Meldung

### v1.5.3
- Save/Load lädt jetzt nur Encoder-Settings (Pfade getrennt)
- Mediathek-Safe Constraint bei Bitrate > 8 Mbps

### v1.5.2
- Pixel-Format wurde nicht aus Source übernommen
- AV1 NVENC Color-Space-Probleme bei Mediathek-Files

### v1.5.1
- UI-Lock während Encoding zu strikt (Save/About blockiert)

### v1.5
- Cancel-Befehl beendete Batch nicht zuverlässig
- Run-Button-Styling visuell zu auffällig

### v1.2
- None (new release)

### v1.1
- ✅ Fixed: UI freezing bei großen Dateien (async loading)
- ✅ Fixed: Race Conditions im Worker-Management (QMutex)
- ✅ Fixed: Zombie-Prozesse bei FFmpeg-Crashes (enhanced cleanup)
- ✅ Fixed: Timeout-Issues bei ffprobe (10s limit)
- ✅ Fixed: Memory-Leaks in VideoInfo-Loading (proper cleanup)

### v1.0
- Initial Release

---

## 📈 PERFORMANCE IMPROVEMENTS

### v2.5
- Codec-aware Bitrate verhindert "zu kleine Bitrate"-Re-Encodes
  → bei H.265-Sources spart das einen kompletten Re-Encode-Durchgang
- Console-Logging reduziert (kein Spam bei Settings-Toggles)
- Preview-Update läuft schweigend, Encoding-Start loggt einmalig

### v1.5.x
- Cancel-Latenz drastisch reduziert (Eskalation terminate→kill)
- psutil-basierter Children-Kill verhindert hängende ffmpeg-Prozesse

### v1.2
- Resolution-Detection Overhead: ~50ms (acceptable)
- Scaling-Filter: 15-20% Encoding-Overhead (Lanczos)
- Memory: Minimal increase (~0.1 MB)

### v1.1
- Video Info Loading: ~100ms slower BUT async (no UI blocking)
- Thread-Pool: Better CPU utilization (4 parallel threads)
- Worker Management: Faster startup/shutdown (Mutex-optimized)

---

## 🔄 BREAKING CHANGES

### v2.5
- **FFmpeg 8.0+ erforderlich** (vorher 6.x/7.x reichten)
- `-gpu N` Syntax komplett entfernt (zu `-init_hw_device cuda:N` migriert)
- `-vsync cfr` zu `-fps_mode cfr` migriert
- Config-Datei-Location: AppData → portable (Migration läuft automatisch beim ersten Start)

### v2.0
- Default-Audio-Track-Selektion (alle statt einer) — bestehende Settings bleiben erhalten

### v1.1
- **None** - 100% Backward Compatible
- Settings-File Format unchanged
- Old config files work without migration

---

## 🗑️ DEPRECATED / REMOVED

### v2.5
- **Removed:** Codec-lokales `-gpu N` Argument (nicht mehr von FFmpeg 8.0 unterstützt)
- **Removed:** `-vsync cfr` (durch `-fps_mode cfr` ersetzt)
- **Deprecated:** AppData-Config-Location (automatische Migration zu portable)

### v1.2
- None

### v1.1
- Legacy blocking `get_video_duration()` calls (replaced by async)
- Legacy blocking `get_video_fps()` calls (replaced by async)
- Legacy blocking `get_stream_info()` calls (replaced by async)

**Note:** Legacy functions still exist for compatibility but are no longer used internally.

---

## 📦 DEPENDENCIES

### As of v2.5
```
PyQt6 >= 6.0   (replaced by PySide6 in v2.6)
psutil >= 5.0
ffmpeg >= 8.0  (external binary)  ← UPGRADE REQUIRED
ffprobe >= 8.0 (external binary)
```

### Current (v2.6)
```
PySide6 >= 6.0   (LGPL — replaces PyQt6)
psutil >= 5.0
ffmpeg >= 8.0  (external binary)
ffprobe >= 8.0 (external binary)
```

### Build-Time (für .exe)
```
nuitka
ordered-set
zstandard
```

### Changes from v2.0
- FFmpeg 8.0+ erforderlich (vorher 6.x/7.x)

### Changes from v1.x
- Keine neuen Python-Dependencies
- Build-System auf Nuitka umgestellt (Standalone + Onefile)

---

## 🔧 MIGRATION GUIDE

### From v2.0 to v2.5
**Migration:** Drop-in Replacement, aber FFmpeg 8.0+ benötigt!

1. **FFmpeg upgraden** auf 8.0+ (zwingend!)
2. Replace executable/script
3. Beim ersten Start: Config wird automatisch von AppData neben das Programm migriert
4. Settings bleiben erhalten

**Neue Settings (Auto-populated):**
- `last_browse_directory`
- `strict_vbv` (Boolean)
- `container_format` (mkv/mp4/mov/webm)
- `force_av1_compat` (Boolean)
- `gpu_index` (für Multi-GPU)

**Achtung bei Multi-GPU-Setups:**
- Alte v2.0-Configs mit `-gpu N` werden ignoriert
- GPU-Auswahl muss neu vorgenommen werden

### From v1.1/v1.2 to v2.5
**Migration:** Über Zwischenschritt empfohlen, aber Drop-in ist möglich

1. Backup alter Settings (optional, werden migriert)
2. FFmpeg auf 8.0+ upgraden
3. v2.5 starten — alle Settings werden automatisch portiert

---

## 📚 DOCUMENTATION

### v2.5 Documentation
- `Changelog.md` (this file, updated)
- `Dolby_Atmos_Feature_Documentation.md` (Atmos-Details)
- `Atmos_Quick_Start.md` (User-friendly Atmos-Guide)

### v1.2 Documentation (historical)
- `RESOLUTION_SCALING_DOCUMENTATION.md` (32 pages)
- `RESOLUTION_SCALING_QUICK_START.md`

### v1.1 Documentation (historical)
- `DOLBY_ATMOS_FEATURE_DOCUMENTATION.md`
- `ATMOS_QUICK_START.md`

### v1.0 Documentation
- Basic README only

---

## 🎯 ROADMAP

### Considering for v2.6
- [ ] HDR10/HDR10+ Metadata Preservation
- [ ] DTS-HD/DTS:X Audio Detection (analog Atmos-Protection)
- [ ] Resume-From-Checkpoint (bei Crash)
- [ ] Advanced Filters (Deinterlace, Denoise)
- [ ] Two-Pass Encoding Support
- [ ] Quality-Based Encoding (CRF Mode für SVT-AV1/x265/x264)

### Considering for v3.0
- [ ] UI Redesign (Modern Dark Theme)
- [ ] Preset Management System (User-defined Profiles)
- [ ] Chapter Markers Support
- [ ] AMD AMF / Intel QuickSync Support
- [ ] Plugin-System für Custom Filters
- [ ] CLI-Modus (für Automation/Scripting)

### Done in v2.5
- [x] ~~Multi-GPU Support~~ ✅ Implemented
- [x] ~~Codec-aware Bitrate-Defaults~~ ✅ Implemented
- [x] ~~Stream-Copy Mode (Pure Remux)~~ ✅ Implemented as RemuxDialog
- [x] ~~Container-Format-Selection~~ ✅ Implemented
- [x] ~~Full Audio-Codec-Support~~ ✅ Implemented

---

## 🙏 ACKNOWLEDGEMENTS

### v2.5
- FFmpeg 8.0 Release-Notes für Migration-Hinweise
- NVIDIA NVENC Multi-GPU Documentation
- SVT-AV1, x264, x265 Maintainer
- User-Feedback zu H.265→AV1 Bitrate-Problem (Trigger für Codec-aware Logic)
- Claude (Anthropic) für AI-Coding-Support bei Architektur, Refactoring und Bug-Fixes

### v1.5.x
- Community-Feedback zu Mediathek-Black-Screen-Issues
- ARD/ZDF-Mediathek-Stream-Analyse durch User-Reports

### v1.2
- Thanks to FFmpeg team for scaling filter documentation
- Community feedback on 4K→1080p workflows

### v1.1
- Thanks to Dolby for Atmos specifications
- Community feedback on Atmos-handling issues

### v1.0
- FFmpeg team for the amazing tool
- NVIDIA for NVENC documentation
- PyQt team for the GUI framework
- Community for testing and feedback

---

## 📞 SUPPORT

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

**Current Version:** 2.6
**Release Date:** 2026
**Status:** ✅ Production Ready
**Author:** Silvestar Friedrich
**FFmpeg Requirement:** 8.0+
