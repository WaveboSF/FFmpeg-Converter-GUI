#!/usr/bin/env python3
"""
FFmpeg Converter GUI v2.6
===================================================
A graphical user interface for FFmpeg with hardware and software encoding,
thoughtful batch processing and smart defaults.

Requires FFmpeg 8.0 or newer.

Highlights:
- Hardware Encoding: NVENC AV1, H.265, H.264 with multi-GPU support
  (modern -init_hw_device cuda:N)
- Codec-aware Recommended Bitrate: Smart defaults per source/target codec
  combination (H.265→AV1 ≈ 60%, H.264→AV1 ≈ 30%, etc.); quality floors per
  resolution; slider capped at source bitrate
- Strict VBV: Optional hard bitrate cap; off = Quality-First soft cap
- Remux MKV → MP4: Container-only conversion preserving Dolby Vision
  metadata (separate dialog with workflow diagram and stream selection;
  dialog stays open between remuxes for fast multi-file workflows)
- Recursive Batch: Folder trees; skipped files logged in Message.log
- Dolby Atmos Auto-Protection: Automatic detection & safe handling on
  re-encoding
- Mediathek-Safe Mode: Fix for black-screen issues on Mediathek downloads
- 4K → 1080p: One-click resolution scaling with automatic aspect ratio
- Portable Config: Settings file lives next to the program (USB-stick-friendly)
- Smart Browse Directory: Remembers last folder; falls back to drive root,
  then system root if the path no longer exists
- Drag & Drop: Files into Input field, folders into Batch field
- Robust Shutdown: ffmpeg processes are reliably terminated on close
- Dark Mode: Toggle Light/Dark theme live; choice persists in settings

v2.6 changes: PySide6 (LGPL) replaces PyQt6; Dark Mode toggle (☾/☀) with
Fusion style + explicit QPalette for reliable live switching on Windows;
Remux dialog stays open between remuxes; nvidia-smi parsing handles UUID
suffix; Encoder combo auto-sizes to longest GPU name.

Uses FFmpeg 8.0 syntax (`-init_hw_device cuda:N`, `-fps_mode cfr`).

Acknowledgements:
FFmpeg, PySide6, psutil, Python, NVIDIA NVENC, SVT-AV1, x264, x265,
and Claude (Anthropic) for AI-coding support on architecture,
refactoring and bug fixes.

(c) S. Friedrich 2025-2026
Freeware - released under the MIT License.
"""

import sys
import os
import subprocess
import traceback
import json
import time
import shutil
from datetime import timedelta
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
import psutil

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QPushButton,
    QLabel,
    QLineEdit,
    QSlider,
    QFileDialog,
    QComboBox,
    QMessageBox,
    QDialog,
    QDialogButtonBox,
    QProgressBar,
    QCheckBox,
    QSpacerItem,
    QSizePolicy,

    QTextBrowser,
    QFrame,
    QScrollArea,

    QStyleFactory
)
from PySide6.QtGui import QPixmap, QIcon, QPalette, QColor
from PySide6.QtCore import (
    Qt, QThread, Signal, QTimer, QStandardPaths,
    QRunnable, QObject, QMutex, QMutexLocker, QThreadPool
)

# ============================================================================
# Exception Hook
# ============================================================================

def excepthook(exc_type, exc_value, exc_tb):
    traceback.print_exception(exc_type, exc_value, exc_tb)

sys.excepthook = excepthook

# ============================================================================
# Helper Functions
# ============================================================================

def seconds_to_hms(seconds):
    return str(timedelta(seconds=int(seconds)))

def get_gpu_info():
    """Parses `nvidia-smi -L` output.

    Format pro Zeile: 'GPU N: <name> (UUID: GPU-xxxxxxxx-...)'

    FIX: Frueher wurde line.split(': ') verwendet - das splittet aber auch
    am ': ' innerhalb von '(UUID: GPU-...)', wodurch der Name als
    'NVIDIA GeForce RTX 5090 (UUID' im Dropdown landete. Jetzt:
    partition() splittet nur am ersten ': ', und das ' (UUID:'-Suffix
    wird per find() sauber abgeschnitten.

    Robust gegen MIG-Sublines auf Datacenter-Karten (eingerueckte Zeilen,
    die nicht mit 'GPU' beginnen) und gegen kuenftige nvidia-smi-Varianten
    ohne UUID-Suffix.
    """
    gpus = []
    try:
        out = subprocess.check_output(['nvidia-smi', '-L'], universal_newlines=True)
        for line in out.strip().splitlines():
            # Split nur am ERSTEN ': ' -> 'GPU N' | '<name> (UUID: ...)'
            head, sep, tail = line.partition(': ')
            if not sep or not head.startswith('GPU') or not tail:
                # Leerzeilen, MIG-Sublines, oder unbekanntes Format -> skip
                continue
            head_parts = head.split()
            if len(head_parts) < 2:
                continue
            gpu_index = int(head_parts[1])
            # Alles vor ' (UUID:' ist der Name; Fallback auf den ganzen Tail
            # falls eine kuenftige Version kein UUID-Suffix mehr liefert.
            name_end = tail.find(' (UUID:')
            gpu_name = tail[:name_end].strip() if name_end != -1 else tail.strip()
            if gpu_name:
                gpus.append({'index': gpu_index, 'name': gpu_name})
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError, IndexError):
        return []
    return gpus

# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class VideoInfo:
    """Container für Video-Metadaten"""
    duration: float = 0.0
    fps: float = 0.0
    width: int = 0
    height: int = 0
    audio_streams: List[Dict] = None
    subtitle_streams: List[Dict] = None
    has_atmos: bool = False
    atmos_stream_indices: List[int] = None
    pix_fmt: str = 'yuv420p'  # Pixel format (z.B. yuv420p, yuv420p10le)

    # Normalisiert: 'h264', 'h265', 'av1', 'unknown'. (VP9 wird als 'h265'
    # behandelt - aehnliche Effizienz, separate Spalte waere Overkill.)
    video_codec: str = 'unknown'
    error: Optional[str] = None
    
    def __post_init__(self):
        if self.audio_streams is None:
            self.audio_streams = []
        if self.subtitle_streams is None:
            self.subtitle_streams = []
        if self.atmos_stream_indices is None:
            self.atmos_stream_indices = []
    
    @property
    def is_valid(self) -> bool:
        """Prüft, ob Video-Info erfolgreich geladen wurde"""
        return self.error is None and self.duration > 0
    
    @property
    def resolution_str(self) -> str:
        """Formatierte Resolution-String"""
        if self.width > 0 and self.height > 0:
            return f"{self.width}x{self.height}"
        return "Unknown"

# ============================================================================
# Signal Classes
# ============================================================================

class VideoInfoSignals(QObject):
    """Signal-Container für VideoInfoWorker"""
    finished = Signal(object)  # VideoInfo object

# ============================================================================
# Worker Classes
# ============================================================================

class VideoInfoWorker(QRunnable):
    """
    Nicht-blockierender Worker für ffprobe-Abfragen.
    Läuft in QThreadPool - keine UI-Blockierung.
    """
    
    def __init__(self, filepath: str):
        super().__init__()
        self.filepath = filepath
        self.signals = VideoInfoSignals()
        self._timeout_seconds = 10
        
    def run(self):
        """Hauptlogik - läuft im Thread-Pool"""
        try:
            if not os.path.isfile(self.filepath):
                info = VideoInfo(error=f"File not found: {self.filepath}")
                self.signals.finished.emit(info)
                return
            
            # Parallele Datensammlung
            info = VideoInfo()
            
            # 1. Duration
            info.duration = self._get_duration_safe()
            
            # 2. FPS (nur wenn Duration valide)
            if info.duration > 0:
                info.fps = self._get_fps_safe()
            
            # 3. Resolution
            width, height = self._get_resolution_safe()
            info.width = width
            info.height = height
            
            # 4. Stream Info + Pixel Format + Video Codec (v2.5)
            audio, subs, pix_fmt, video_codec = self._get_streams_safe()
            info.audio_streams = audio
            info.subtitle_streams = subs
            info.pix_fmt = pix_fmt
            info.video_codec = video_codec
            
            # 5. Atmos Detection
            if audio:
                has_atmos, atmos_indices = self._detect_atmos(audio)
                info.has_atmos = has_atmos
                info.atmos_stream_indices = atmos_indices
            
            # Emit result
            self.signals.finished.emit(info)
            
        except Exception as e:
            error_info = VideoInfo(error=f"Unexpected error: {str(e)}")
            self.signals.finished.emit(error_info)
    
    def _get_duration_safe(self) -> float:
        """Sichere Duration-Extraktion mit Timeout"""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", self.filepath],
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=self._timeout_seconds,
                check=True
            )
            return float(result.stdout.strip())
        except subprocess.TimeoutExpired:
            print(f"⚠️ Timeout getting duration for {self.filepath}")
            return 0.0
        except (ValueError, subprocess.CalledProcessError) as e:
            print(f"⚠️ Error getting duration: {e}")
            return 0.0
    
    def _get_fps_safe(self) -> float:
        """Sichere FPS-Extraktion mit Timeout"""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=avg_frame_rate",
                 "-of", "default=noprint_wrappers=1:nokey=1", self.filepath],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=self._timeout_seconds,
                check=True
            )
            fps_str = result.stdout.strip()
            if '/' in fps_str:
                num, den = map(int, fps_str.split('/'))
                return float(num / den) if den != 0 else 0.0
            return float(fps_str)
        except subprocess.TimeoutExpired:
            print(f"⚠️ Timeout getting FPS for {self.filepath}")
            return 0.0
        except (ValueError, subprocess.CalledProcessError) as e:
            print(f"⚠️ Error getting FPS: {e}")
            return 0.0
    
    def _get_resolution_safe(self) -> Tuple[int, int]:
        """Sichere Resolution-Extraktion mit Timeout"""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height",
                 "-of", "csv=p=0", self.filepath],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=self._timeout_seconds,
                check=True
            )
            output = result.stdout.strip()
            if ',' in output:
                width_str, height_str = output.split(',')
                width = int(width_str)
                height = int(height_str)
                return width, height
            return 0, 0
        except subprocess.TimeoutExpired:
            print(f"⚠️ Timeout getting resolution for {self.filepath}")
            return 0, 0
        except (ValueError, subprocess.CalledProcessError) as e:
            print(f"⚠️ Error getting resolution: {e}")
            return 0, 0
    
    def _get_streams_safe(self) -> Tuple[List[Dict], List[Dict], str, str]:
        """
        Sichere Stream-Info-Extraktion mit Timeout.
        Returns: (audio_streams, subtitle_streams, pixel_format, video_codec)
        video_codec als 4. Returnwert fuer Codec-aware Bitrate.
        """
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_streams", "-of", "json", self.filepath],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=self._timeout_seconds,
                check=True
            )
            data = json.loads(result.stdout)
            
            audio_streams = []
            subtitle_streams = []
            pixel_format = 'yuv420p'  # Default
            video_codec = 'unknown'
            video_stream_found = False  # Flag um nur ersten Video-Stream zu nehmen
            
            for stream in data.get('streams', []):
                codec_type = stream.get('codec_type')
                if codec_type == 'audio':
                    audio_streams.append(stream)
                elif codec_type == 'subtitle':
                    subtitle_streams.append(stream)
                elif codec_type == 'video' and not video_stream_found:
                    # Extrahiere pix_fmt vom ersten Video-Stream
                    pixel_format = stream.get('pix_fmt', 'yuv420p')

                    # ffprobe liefert: 'h264', 'hevc', 'av1', 'vp9', 'mpeg2video', ...
                    raw_codec = stream.get('codec_name', '').lower()
                    if raw_codec == 'h264':
                        video_codec = 'h264'
                    elif raw_codec == 'hevc':
                        video_codec = 'h265'
                    elif raw_codec == 'av1':
                        video_codec = 'av1'
                    elif raw_codec == 'vp9':
                        # VP9 hat aehnliche Effizienz wie H.265 -> gleiche Multipler
                        video_codec = 'h265'
                    else:
                        video_codec = 'unknown'
                    video_stream_found = True
            
            return audio_streams, subtitle_streams, pixel_format, video_codec
            
        except subprocess.TimeoutExpired:
            print(f"⚠️ Timeout getting streams for {self.filepath}")
            return [], [], 'yuv420p', 'unknown'
        except (json.JSONDecodeError, subprocess.CalledProcessError) as e:
            print(f"⚠️ Error getting streams: {e}")
            return [], [], 'yuv420p', 'unknown'
    
    def _detect_atmos(self, audio_streams: List[Dict]) -> Tuple[bool, List[int]]:
        """
        Erkennt Dolby Atmos in Audio-Streams.
        Returns: (has_atmos, list_of_atmos_stream_indices)
        """
        atmos_indices = []
        
        for stream in audio_streams:
            codec_name = stream.get('codec_name', '').lower()
            profile = stream.get('profile', '').lower()
            
            # Atmos-Indicators:
            # 1. TrueHD + "atmos" im Profile
            # 2. EAC3 + "atmos" im Profile oder in Tags
            is_atmos = False
            
            if codec_name == 'truehd':
                # TrueHD mit Atmos hat oft profile "TrueHD + Atmos"
                if 'atmos' in profile:
                    is_atmos = True
                # Alternativ: Checke Tags
                tags = stream.get('tags', {})
                for key, value in tags.items():
                    if 'atmos' in str(value).lower():
                        is_atmos = True
                        break
            
            elif codec_name == 'eac3':
                # EAC3 mit Atmos (E-AC-3 JOC)
                if 'atmos' in profile or 'joc' in profile:
                    is_atmos = True
                # Check tags
                tags = stream.get('tags', {})
                for key, value in tags.items():
                    if 'atmos' in str(value).lower():
                        is_atmos = True
                        break
            
            if is_atmos:
                stream_index = stream.get('index', -1)
                if stream_index >= 0:
                    atmos_indices.append(stream_index)
                    print(f"🎬 Dolby Atmos detected in stream #{stream_index} ({codec_name})")
        
        return len(atmos_indices) > 0, atmos_indices


class FFmpegWorker(QThread):
    """
    Enhanced FFmpeg Worker mit robustem Error Handling und Process Cleanup.
    """
    finished_signal = Signal(int, str)
    progress_signal = Signal(float, float, int)

    def __init__(self, cmd, total_duration):
        super().__init__()
        self.cmd = cmd
        self.total_duration = total_duration
        self.process = None
        self._is_paused = False
        self._stop_requested = False
        self._cleanup_timeout = 5

    def run(self):
        """Hauptlogik mit verbessertem Error Handling"""
        output = []
        return_code = -1
        
        try:
            startupinfo = None
            creationflags = 0
            
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 6
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

            self.process = subprocess.Popen(
                self.cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT,
                universal_newlines=True, 
                bufsize=1, 
                startupinfo=startupinfo,
                creationflags=creationflags
            )

            current_time = 0.0
            total_size_bytes = 0

            for line in iter(self.process.stdout.readline, ''):
                if self._stop_requested:
                    break
                    
                line = line.strip()
                output.append(line)

                if line.startswith("out_time_ms="):
                    try:
                        ms = int(line.split("=")[1])
                        current_time = ms / 1_000_000.0
                    except (ValueError, IndexError):
                        continue
                elif line.startswith("out_time="):
                    try:
                        current_time = float(line.split("=")[1])
                    except (ValueError, IndexError):
                        continue
                elif line.startswith("total_size="):
                    try:
                        total_size_bytes = int(line.split("=")[1])
                    except (ValueError, IndexError):
                        pass
                
                if self.total_duration > 0 and current_time > 0:
                    progress = (current_time / self.total_duration) * 100.0
                    self.progress_signal.emit(progress, current_time, total_size_bytes)

                if line == "progress=end":
                    break
            
            try:
                self.process.wait(timeout=self._cleanup_timeout)
                return_code = self.process.returncode
            except subprocess.TimeoutExpired:
                print("⚠️ FFmpeg process did not terminate gracefully")
                self._force_kill()
                return_code = -1
            
            full_output = "\n".join(output)
            self.finished_signal.emit(return_code, full_output)

        except Exception as e:
            error_msg = f"Worker exception: {str(e)}\n{traceback.format_exc()}"
            print(f"⚠️ {error_msg}")
            self.finished_signal.emit(-1, error_msg)
        
        finally:
            self._ensure_process_terminated()

    def pause(self):
        """Pausiert den FFmpeg-Prozess"""
        if self.process and not self._is_paused:
            try:
                proc = psutil.Process(self.process.pid)
                proc.suspend()
                self._is_paused = True
                print(f"✓ Process {self.process.pid} paused")
            except psutil.NoSuchProcess:
                print(f"⚠️ Process {self.process.pid} no longer exists")
            except Exception as e:
                print(f"⚠️ Error pausing process: {e}")

    def resume(self):
        """Setzt den pausierten FFmpeg-Prozess fort"""
        if self.process and self._is_paused:
            try:
                proc = psutil.Process(self.process.pid)
                proc.resume()
                self._is_paused = False
                print(f"✓ Process {self.process.pid} resumed")
            except psutil.NoSuchProcess:
                print(f"⚠️ Process {self.process.pid} no longer exists")
            except Exception as e:
                print(f"⚠️ Error resuming process: {e}")

    def stop(self, force: bool = False):
        """
        Stoppt den FFmpeg-Prozess mit graceful shutdown.
        Verwendet eskalierendes Kill-Verfahren.
        
        force=True überspringt graceful-Phase und killt sofort
        den Prozessbaum. Wird beim closeEvent verwendet, damit ffmpeg
        nicht weiterläuft wenn das GUI geschlossen wird.
        """
        self._stop_requested = True
        
        if not self.process or self.process.poll() is not None:
            return
        
        # Force-Modus: Direkt _force_kill (mit psutil-Fallback)
        if force:
            self._force_kill()
            return
        
        try:
            pid = self.process.pid
            
            if os.name == 'nt':
                subprocess.run(
                    ["taskkill", "/PID", str(pid)],
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE,
                    timeout=2,
                    check=False
                )
            else:
                self.process.terminate()
            
            try:
                self.process.wait(timeout=2)
                print(f"✓ Process {pid} terminated gracefully")
                return
            except subprocess.TimeoutExpired:
                pass
            
            self._force_kill()
            
        except Exception as e:
            print(f"⚠️ Error in stop(): {e}")
            self._force_kill()

    def _force_kill(self):
        """
        Erzwingt Prozess-Terminierung.
        Jetzt mit psutil-basiertem Children-Kill als Fallback.
        Wichtig wegen CREATE_NEW_PROCESS_GROUP: der Prozessbaum wird sonst
        nicht zuverlässig mitgenommen wenn taskkill versagt.
        """
        if not self.process:
            return
            
        try:
            pid = self.process.pid
            
            if os.name == 'nt':
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=2,
                    check=False
                )
            else:
                self.process.kill()
            
            try:
                self.process.wait(timeout=1)
                print(f"✓ Process {pid} force killed")
                return
            except subprocess.TimeoutExpired:
                pass
            

            # Killt explizit auch alle Children (z.B. ffmpeg-eigene Helper-Prozesse).
            try:
                proc = psutil.Process(pid)
                children = proc.children(recursive=True)
                
                # Erst Kinder killen, dann Parent (von unten nach oben)
                for child in children:
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                    except Exception as e:
                        print(f"⚠️ Error killing child {child.pid}: {e}")
                
                try:
                    proc.kill()
                except psutil.NoSuchProcess:
                    pass
                
                # Final wait
                gone, alive = psutil.wait_procs([proc] + children, timeout=2)
                if alive:
                    print(f"⚠️ {len(alive)} process(es) still alive after psutil kill")
                else:
                    print(f"✓ Process tree {pid} killed via psutil fallback")
            except psutil.NoSuchProcess:
                # Already dead - good
                pass
            except Exception as e:
                print(f"⚠️ psutil fallback error: {e}")
            
        except Exception as e:
            print(f"⚠️ Error force killing process: {e}")

    def _ensure_process_terminated(self):
        """Stellt sicher, dass kein Zombie-Prozess übrig bleibt"""
        if not self.process:
            return
        
        try:
            if self.process.poll() is None:
                self._force_kill()
        except Exception as e:
            print(f"⚠️ Error in cleanup: {e}")

# ============================================================================
# Main GUI Class
# ============================================================================

# ============================================================================
# RemuxDialog
# ============================================================================
#
# Standalone-Dialog fuer den Remux-Workflow (Container-Wechsel MKV->MP4 ohne
# Re-Encoding). Komplett getrennt vom normalen Encoding-Workflow.
#
# Aufbau (kompaktes Layout):
#   - Header (eine Zeile)
#   - Workflow-Diagramm (immer sichtbar)
#   - Source-Path (eine Zeile)
#   - Streams-Sektion: Audio + Subtitles, je mit "Select All / None" Buttons,
#     halbautomatischer Vorauswahl pro Sprache
#   - Output-Path (eine Zeile)
#   - Apple-Compatibility-Checkbox (HEVC tag hvc1)
#   - Doku-Toggle (initial collapsed)
#   - "Start Remux" / "Cancel" Buttons
#
# Wiring zum Hauptfenster:
#   - Liest die ffprobe-Daten aus parent (audio_streams, subtitle_streams,
#     input_video_codec)
#   - Beim Start: ruft parent.process_single_file(input, output, cmd_override=cmd)
#     mit dem fertigen Remux-Befehl - der Worker-Code wird gemeinsam genutzt.
# ============================================================================

class ClickableLabel(QLabel):
    """
    QLabel mit clicked-Signal.
    """
    clicked = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # WA_Hover aktiviert :hover-Pseudo-Class in QSS auch ohne MouseTracking
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ClickableFrame(QFrame):
    """
    QFrame mit clicked-Signal.

    Genutzt als Container fuer die Source/Output-Boxen im Workflow-Diagramm,
    damit auch der "Rand-Bereich" um die Stream-Items klickbar ist (nicht nur
    die Items selbst).
    """
    clicked = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class RemuxDialog(QDialog):
    """
    Eigenstaendiger Dialog fuer Remux-Workflow mit Audio+Sub-Auswahl,
    halbautomatischer Vorauswahl und Apple-Compatibility-Option.
    """

    # Codec-Klassifizierung fuer MP4-Compatibility & Halbauto-Vorauswahl
    AUDIO_MP4_COMPATIBLE = {'aac', 'ac3', 'eac3', 'mp3', 'opus', 'flac'}
    AUDIO_MP4_INCOMPATIBLE = {'truehd', 'dts', 'dts-hd', 'dtshd'}

    # Subtitle-Codecs die zu mov_text konvertiert werden koennen (text-basiert)
    SUB_CONVERTIBLE = {'subrip', 'srt', 'ass', 'ssa', 'mov_text'}
    # Bild-basierte Sub-Codecs: koennen nicht in MP4 (kein OCR ohne Re-Encode)
    SUB_PICTURE_BASED = {'hdmv_pgs_subtitle', 'pgs', 'dvd_subtitle', 'dvb_subtitle'}

    def __init__(self, parent):
        super().__init__(parent)
        self.parent_gui = parent
        self.setWindowTitle("Remux MKV → MP4")
        # Mit der ScrollArea darf der Dialog deutlich kompakter sein:
        # bei wenigen Tracks zeigt er alles ohne Scrollbar, bei vielen Tracks
        # erscheint die Scrollbar und die Buttons bleiben unten sichtbar.
        # Frueher: Mindesthoehe 520 / Default 600.
        self.setMinimumSize(820, 480)
        self.resize(880, 600)

        self.audio_track_checkboxes = []  # List of (QCheckBox, stream_dict)
        self.subtitle_track_checkboxes = []  # List of (QCheckBox, stream_dict)

        # Jeder Eintrag: {'path': str, 'codec': str, 'duration': float,
        #                 'language': str, 'enabled': bool}
        self.external_audio_files = []
        self.external_subtitle_files = []

        self._build_ui()
        self._populate_from_parent()

        # File-geladen vs. nicht-geladen an)
        self._update_workflow_diagram()

    # ------------------------------------------------------------------
    # UI-Aufbau
    # ------------------------------------------------------------------
    def _build_ui(self):
        # Root-Layout des Dialogs:
        #   [fix]    Header
        #   [fix]    Workflow-Diagramm
        #   [scroll] Source / Streams / Output / Apple-Compat / Docs
        #   [fix]    Buttons (Cancel + Start Remux)
        #
        # Hintergrund: Bei Files mit vielen Audio-Tracks und Subtitles
        # wuchs der Dialog ueber die Bildschirmgroesse hinaus, sodass
        # die Action-Buttons nicht mehr sichtbar waren. Loesung: nur die
        # scrollbare Mitte darf wachsen; Header (zur Orientierung) und
        # Buttons (zum Bedienen) bleiben immer im sichtbaren Bereich.
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        # 1. Header (kompakt, eine Zeile) - FIX OBEN
        header = QLabel(
            "<h3 style='margin: 0 0 2px 0;'>Remux to MP4</h3>"
            "<span style='color: #555;'>Container conversion only — no re-encoding. "
            "Preserves Dolby Vision, HDR, and exact source quality.</span>"
        )
        header.setWordWrap(True)
        root.addWidget(header)

        # 2. Workflow-Diagramm (immer sichtbar - Anker fuer den User) - FIX OBEN

        # Source-Container, Pfeil und Output-Container sind separate Widgets,
        # einzelne Stream-Eintraege sind klickbare Labels mit Hover-State.
        self.diagram_container = QFrame()
        self.diagram_container.setStyleSheet(
            "QFrame#diagramContainer { background: #f8f9fa; border: 1px solid #ddd; "
            "border-radius: 4px; }"
        )
        self.diagram_container.setObjectName("diagramContainer")
        self.diagram_layout = QHBoxLayout(self.diagram_container)
        self.diagram_layout.setContentsMargins(10, 10, 10, 10)
        self.diagram_layout.setSpacing(12)
        root.addWidget(self.diagram_container)
        # Loading-Flag fuer Diagramm-State
        self._loading_source = False

        # 3. Scrollbarer Mittelteil. Das Inner-Layout heisst weiter `layout`,
        # damit die ganze nachfolgende UI-Konstruktion unveraendert bleibt.
        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        # Kein zusaetzlicher Frame - der Border wuerde sonst doppelt zum
        # Dialog-Border wirken und das Layout optisch unruhig machen.
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Nur vertikales Scrollen erlauben - horizontaler Scrollbalken ist
        # bei diesem Dialog nie sinnvoll (alle Widgets resizen mit).
        self.content_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.content_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)
        layout.setSpacing(8)
        # Innen-Margins auf 0 - aussen kuemmert sich `root` um Padding,
        # innen koennten doppelte Margins zu unsymmetrischer Optik fuehren.
        layout.setContentsMargins(0, 0, 0, 0)

        # 3. Source File (eine Zeile mit Browse-Button)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("<b>Source:</b>"))
        self.source_path_label = QLabel("<i>(no file loaded — click the diagram or Browse to select)</i>")
        self.source_path_label.setStyleSheet("color: #555;")
        self.source_path_label.setWordWrap(True)
        self.source_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        src_row.addWidget(self.source_path_label, 1)
        self.src_browse_btn = QPushButton("Browse…")
        self.src_browse_btn.setMaximumWidth(90)
        self.src_browse_btn.clicked.connect(self._browse_source)
        src_row.addWidget(self.src_browse_btn)
        layout.addLayout(src_row)

        # 4. Streams (kompakter GroupBox)
        self.streams_box = QGroupBox("Streams")
        streams_layout = QVBoxLayout(self.streams_box)
        streams_layout.setContentsMargins(10, 10, 10, 10)
        streams_layout.setSpacing(6)

        # 4a. Video-Zeile
        self.video_label = QLabel()
        self.video_label.setTextFormat(Qt.TextFormat.RichText)
        self.video_label.setWordWrap(True)
        streams_layout.addWidget(self.video_label)

        # 4b. Audio Section: Header mit Select-All/None Buttons + Track-Liste
        audio_header_row = QHBoxLayout()
        audio_header_row.addWidget(QLabel("<b>Audio Tracks:</b>"))
        audio_header_row.addStretch()
        self.audio_all_btn = QPushButton("All")
        self.audio_all_btn.setMaximumWidth(50)
        self.audio_all_btn.clicked.connect(lambda: self._toggle_all_checkboxes(self.audio_track_checkboxes, True))
        self.audio_none_btn = QPushButton("None")
        self.audio_none_btn.setMaximumWidth(60)
        self.audio_none_btn.clicked.connect(lambda: self._toggle_all_checkboxes(self.audio_track_checkboxes, False))
        audio_header_row.addWidget(self.audio_all_btn)
        audio_header_row.addWidget(self.audio_none_btn)
        streams_layout.addLayout(audio_header_row)

        self.audio_tracks_widget = QWidget()
        self.audio_tracks_layout = QVBoxLayout(self.audio_tracks_widget)
        self.audio_tracks_layout.setContentsMargins(16, 0, 0, 0)
        self.audio_tracks_layout.setSpacing(2)
        streams_layout.addWidget(self.audio_tracks_widget)


        self.audio_external_widget = QWidget()
        self.audio_external_layout = QVBoxLayout(self.audio_external_widget)
        self.audio_external_layout.setContentsMargins(16, 0, 0, 0)
        self.audio_external_layout.setSpacing(2)
        streams_layout.addWidget(self.audio_external_widget)


        audio_add_row = QHBoxLayout()
        audio_add_row.setContentsMargins(16, 0, 0, 0)
        self.audio_add_external_btn = QPushButton("+ Add external audio file…")
        self.audio_add_external_btn.setStyleSheet(
            "QPushButton { text-align: left; padding: 3px 8px; "
            "background: transparent; border: 1px dashed #888; color: #2962ff; }"
            "QPushButton:hover { background: #f0f4ff; border: 1px dashed #4a90e2; }"
        )
        self.audio_add_external_btn.setToolTip(
            "Add an audio track from a separate file (e.g. a German dub track\n"
            "to combine with an English-only video). The output MP4 will contain\n"
            "all selected internal and external audio tracks."
        )
        self.audio_add_external_btn.clicked.connect(self._add_external_audio)
        audio_add_row.addWidget(self.audio_add_external_btn)
        audio_add_row.addStretch()
        streams_layout.addLayout(audio_add_row)

        # 4c. Subtitle Section
        sub_header_row = QHBoxLayout()
        sub_header_row.addWidget(QLabel("<b>Subtitles:</b>"))
        sub_header_row.addStretch()
        self.sub_all_btn = QPushButton("All")
        self.sub_all_btn.setMaximumWidth(50)
        self.sub_all_btn.clicked.connect(lambda: self._toggle_all_checkboxes(self.subtitle_track_checkboxes, True))
        self.sub_none_btn = QPushButton("None")
        self.sub_none_btn.setMaximumWidth(60)
        self.sub_none_btn.clicked.connect(lambda: self._toggle_all_checkboxes(self.subtitle_track_checkboxes, False))
        sub_header_row.addWidget(self.sub_all_btn)
        sub_header_row.addWidget(self.sub_none_btn)
        streams_layout.addLayout(sub_header_row)

        self.subtitle_tracks_widget = QWidget()
        self.subtitle_tracks_layout = QVBoxLayout(self.subtitle_tracks_widget)
        self.subtitle_tracks_layout.setContentsMargins(16, 0, 0, 0)
        self.subtitle_tracks_layout.setSpacing(2)
        streams_layout.addWidget(self.subtitle_tracks_widget)


        self.sub_external_widget = QWidget()
        self.sub_external_layout = QVBoxLayout(self.sub_external_widget)
        self.sub_external_layout.setContentsMargins(16, 0, 0, 0)
        self.sub_external_layout.setSpacing(2)
        streams_layout.addWidget(self.sub_external_widget)


        sub_add_row = QHBoxLayout()
        sub_add_row.setContentsMargins(16, 0, 0, 0)
        self.sub_add_external_btn = QPushButton("+ Add external subtitle file…")
        self.sub_add_external_btn.setStyleSheet(
            "QPushButton { text-align: left; padding: 3px 8px; "
            "background: transparent; border: 1px dashed #888; color: #2962ff; }"
            "QPushButton:hover { background: #f0f4ff; border: 1px dashed #4a90e2; }"
        )
        self.sub_add_external_btn.setToolTip(
            "Add a subtitle track from a separate file (e.g. an .srt file).\n"
            "Text-based subtitles (SRT, ASS) will be converted to mov_text.\n"
            "Picture-based subtitles (PGS, SUP) cannot be muxed and will be rejected."
        )
        self.sub_add_external_btn.clicked.connect(self._add_external_subtitle)
        sub_add_row.addWidget(self.sub_add_external_btn)
        sub_add_row.addStretch()
        streams_layout.addLayout(sub_add_row)

        # TrueHD-Warnung (initial leer; wird in _populate gesetzt)
        self.truehd_warning_label = QLabel()
        self.truehd_warning_label.setTextFormat(Qt.TextFormat.RichText)
        self.truehd_warning_label.setWordWrap(True)
        self.truehd_warning_label.setStyleSheet(
            "background: #fff5e6; border: 1px solid #d49544; padding: 6px; "
            "border-radius: 3px; margin-top: 4px;"
        )
        self.truehd_warning_label.hide()
        streams_layout.addWidget(self.truehd_warning_label)

        layout.addWidget(self.streams_box)

        # 5. Output File + Apple-Compat in einer Zeile
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("<b>Output:</b>"))
        self.output_line = QLineEdit()
        self.output_line.setPlaceholderText("Output path (.mp4)")
        out_row.addWidget(self.output_line, 1)
        self.out_browse_btn = QPushButton("Browse…")
        self.out_browse_btn.setMaximumWidth(90)
        self.out_browse_btn.clicked.connect(self._browse_output)
        out_row.addWidget(self.out_browse_btn)
        layout.addLayout(out_row)

        self.output_line.textChanged.connect(self._update_workflow_diagram)

        # 6. Apple-Compat-Checkbox
        apple_row = QHBoxLayout()
        self.apple_compat_checkbox = QCheckBox("Apple compatibility (HEVC tag: hvc1)")
        self.apple_compat_checkbox.setChecked(True)  # Default an: schadet nicht
        self.apple_compat_checkbox.setToolTip(
            "Apple compatibility for HEVC video streams\n"
            "\n"
            "Sets the HEVC tag to 'hvc1' instead of the default 'hev1' — required\n"
            "for Quicktime, MacOS Finder thumbnails, iOS, and Apple TV playback.\n"
            "Without this flag, Apple devices may show: no audio, no thumbnail,\n"
            "or refuse the file entirely.\n"
            "\n"
            "Has no effect on non-HEVC streams (H.264, AV1) — the flag is\n"
            "harmless on those, FFmpeg simply ignores it.\n"
            "\n"
            "Recommended: leave ON. Only relevant cost is a slightly different\n"
            "container metadata byte; the video bitstream is unchanged."
        )
        apple_row.addWidget(self.apple_compat_checkbox)
        apple_row.addStretch()
        layout.addLayout(apple_row)

        self.apple_compat_checkbox.stateChanged.connect(self._update_workflow_diagram)

        # 7. Doku-Toggle (initial collapsed -> Höhe 0)
        docs_header_row = QHBoxLayout()
        docs_header_row.addWidget(QLabel("<b>Documentation</b>"))
        docs_header_row.addStretch()
        self._docs_expanded = False
        self.docs_toggle_btn = QPushButton("▶ Show details")
        self.docs_toggle_btn.setFlat(True)
        self.docs_toggle_btn.setStyleSheet("text-align: left; color: #2962ff;")
        self.docs_toggle_btn.clicked.connect(self._toggle_docs)
        docs_header_row.addWidget(self.docs_toggle_btn)
        layout.addLayout(docs_header_row)

        self.docs_browser = QTextBrowser()
        self.docs_browser.setHtml(self._documentation_html())
        self.docs_browser.setOpenExternalLinks(True)
        self.docs_browser.setStyleSheet("QTextBrowser { background: #f8f8f8; }")
        self.docs_browser.setMaximumHeight(0)  # Initial collapsed
        self.docs_browser.hide()
        layout.addWidget(self.docs_browser)

        layout.addStretch()  # Push content to top wenn weniger Inhalt als Scrollarea-Hoehe

        # Inner-Widget der Scrollarea zuweisen, dann Scrollarea ins Root-Layout
        # mit Stretch=1 - frisst den verfuegbaren Platz zwischen Diagramm und Buttons.
        self.content_scroll.setWidget(scroll_content)
        root.addWidget(self.content_scroll, 1)

        # 8. Action-Buttons - FIX UNTEN, immer sichtbar (auch bei vielen Tracks)
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        self.start_btn = QPushButton("Start Remux")
        # Farbgebung passt zum Run-Button im Hauptmenue: Material Blue 50/100,
        # je 10% pro Kanal abgedunkelt fuer einen kraeftigeren Look. So sind
        # beide Action-Buttons der App visuell konsistent.
        self.start_btn.setStyleSheet(
            "QPushButton { background: #CCDAE4; color: #1B3A57; "
            "padding: 8px 24px; font-weight: bold; }"
            "QPushButton:hover { background: #A8C8E2; }"
            "QPushButton:disabled { background: #DDD; color: #888; }"
        )
        self.start_btn.clicked.connect(self._on_start_clicked)
        button_layout.addWidget(self.start_btn)
        root.addLayout(button_layout)

    # ------------------------------------------------------------------
    # Datenfluss
    # ------------------------------------------------------------------
    def _populate_from_parent(self):
        """Liest den aktuellen Input-Status aus dem Hauptfenster.

        Externe Files (external_audio_files / external_subtitle_files)
        bleiben beim Source-Wechsel erhalten - sind ja unabhaengige Files. Nur
        die UI fuer sie wird re-rendert (Sync-Warnung kann sich aendern wenn
        neue Source eine andere Duration hat).
        """
        p = self.parent_gui
        input_path = p.input_line.text().strip()

        if not input_path or not os.path.isfile(input_path):

            self.source_path_label.setText(
                "<i>(no file loaded — click the diagram or Browse to select)</i>"
            )
            self.source_path_label.setStyleSheet("color: #555;")
            self.video_label.setText("<i>—</i>")
            self.start_btn.setEnabled(False)
            self.output_line.clear()
            # Stream-Layouts leeren und Hint-Labels einfuegen
            while (item := self.audio_tracks_layout.takeAt(0)) is not None:
                w = item.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
            self.audio_track_checkboxes = []
            self.audio_tracks_layout.addWidget(QLabel("<i>(no source loaded)</i>"))
            while (item := self.subtitle_tracks_layout.takeAt(0)) is not None:
                w = item.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
            self.subtitle_track_checkboxes = []
            self.subtitle_tracks_layout.addWidget(QLabel("<i>(no source loaded)</i>"))
            self.audio_all_btn.setEnabled(False)
            self.audio_none_btn.setEnabled(False)
            self.sub_all_btn.setEnabled(False)
            self.sub_none_btn.setEnabled(False)
            self.truehd_warning_label.hide()
            return

        # File da -> Start-Button aktivieren
        self.start_btn.setEnabled(True)

        # Quelldatei
        self.source_path_label.setText(f"<code>{input_path}</code>")
        self.source_path_label.setStyleSheet("color: #222;")

        # Output-Pfad vorschlagen: gleicher Ordner, gleicher Name, .mp4-Endung
        base, _ = os.path.splitext(input_path)
        self.output_line.setText(base + ".mp4")

        # Video-Stream
        codec_disp = p.input_video_codec.upper() if p.input_video_codec != 'unknown' else 'Unknown'
        res_disp = f"{p.input_width}×{p.input_height}" if p.input_width > 0 else "?"
        bitrate_disp = f"{p.input_source_bitrate:.2f} Mbps" if p.input_source_bitrate > 0 else "? Mbps"
        self.video_label.setText(
            f"<b>Video:</b> {codec_disp}, {res_disp}, {bitrate_disp} "
            f"<span style='color: #888;'>— copied 1:1 (DV/HDR preserved)</span>"
        )

        # Audio + Subtitles als Checkboxen
        self._populate_audio_checkboxes(p.audio_streams, p.atmos_stream_indices)
        self._populate_subtitle_checkboxes(p.subtitle_streams)

        # TrueHD-Warnung wenn vorhanden
        has_truehd = any(s.get('codec_name', '').lower() == 'truehd' for s in p.audio_streams)
        if has_truehd:
            self.truehd_warning_label.setText(
                "<b>⚠ TrueHD audio detected.</b> "
                "MP4 cannot contain TrueHD streams. The pre-selection has unchecked them automatically; "
                "if you check one anyway, FFmpeg will abort. "
                "WEB-DL sources usually ship E-AC-3 (Atmos) tracks which fit into MP4 fine — "
                "TrueHD is mainly a UHD-BluRay-rip thing."
            )
            self.truehd_warning_label.show()


        # Video-Duration reflektieren (kann sich nach Source-Wechsel geaendert haben)
        self._refresh_external_audio_ui()
        self._refresh_external_sub_ui()

    def _populate_audio_checkboxes(self, audio_streams, atmos_indices):
        """
        Audio-Track-Checkboxen mit halbautomatischer Vorauswahl:
        - Pro Sprache wird der ERSTE MP4-kompatible Track angehakt
        - MP4-inkompatible Tracks (TrueHD, DTS) sind unchecked und visuell markiert
        - Zusaetzliche kompatible Tracks derselben Sprache sind unchecked
          (User kann manuell mehrere wahlen)
        """
        # Reset
        while (item := self.audio_tracks_layout.takeAt(0)) is not None:
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.audio_track_checkboxes = []

        if not audio_streams:
            self.audio_tracks_layout.addWidget(QLabel("<i>(no audio streams in source)</i>"))
            self.audio_all_btn.setEnabled(False)
            self.audio_none_btn.setEnabled(False)
            return

        self.audio_all_btn.setEnabled(True)
        self.audio_none_btn.setEnabled(True)

        # Halbauto-Vorauswahl: pro Sprache erster MP4-kompatibler Track angehakt
        languages_with_compatible_picked = set()

        for stream in audio_streams:
            tags = stream.get('tags', {})
            lang = tags.get('language', 'und').upper()
            title = tags.get('title', '').strip() or f"Track {stream.get('index', '?')}"
            codec = stream.get('codec_name', 'unknown').lower()
            stream_index = stream.get('index', -1)

            is_atmos = stream_index in atmos_indices
            is_compatible = codec in self.AUDIO_MP4_COMPATIBLE
            is_incompatible = codec in self.AUDIO_MP4_INCOMPATIBLE

            # Label-Aufbau
            label_html = f"#{stream_index} <b>{lang}</b> · {title} <span style='color: #666;'>[{codec}]</span>"
            if is_atmos:
                label_html += " 🎬"
            if is_incompatible:
                label_html += ' <span style="color: #c0392b;"><b>⚠ not MP4-compatible</b></span>'
            elif is_compatible:
                label_html += ' <span style="color: #2e7d32;">✓ MP4-OK</span>'

            cb = QCheckBox()
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(cb)
            row_label = QLabel(label_html)
            row_label.setTextFormat(Qt.TextFormat.RichText)
            row.addWidget(row_label, 1)

            container = QWidget()
            container.setLayout(row)
            self.audio_tracks_layout.addWidget(container)

            # Halbautomatische Vorauswahl
            should_check = False
            if is_compatible and lang not in languages_with_compatible_picked:
                should_check = True
                languages_with_compatible_picked.add(lang)
            cb.setChecked(should_check)

            cb.stateChanged.connect(self._update_workflow_diagram)

            self.audio_track_checkboxes.append((cb, stream))

    def _populate_subtitle_checkboxes(self, subtitle_streams):
        """
        Subtitle-Track-Checkboxen mit halbautomatischer Vorauswahl:
        - Text-Subs (SRT, ASS, SSA): konvertierbar zu mov_text -> erster pro Sprache angehakt
        - Bild-Subs (PGS, DVB): nicht muxbar in MP4 -> unchecked + visuell markiert
        """
        while (item := self.subtitle_tracks_layout.takeAt(0)) is not None:
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.subtitle_track_checkboxes = []

        if not subtitle_streams:
            self.subtitle_tracks_layout.addWidget(QLabel("<i>(no subtitle streams in source)</i>"))
            self.sub_all_btn.setEnabled(False)
            self.sub_none_btn.setEnabled(False)
            return

        self.sub_all_btn.setEnabled(True)
        self.sub_none_btn.setEnabled(True)

        languages_with_text_picked = set()

        for stream in subtitle_streams:
            tags = stream.get('tags', {})
            lang = tags.get('language', 'und').upper()
            title = tags.get('title', '').strip() or f"Sub {stream.get('index', '?')}"
            codec = stream.get('codec_name', 'unknown').lower()
            stream_index = stream.get('index', -1)

            is_text = codec in self.SUB_CONVERTIBLE
            is_picture = codec in self.SUB_PICTURE_BASED

            label_html = f"#{stream_index} <b>{lang}</b> · {title} <span style='color: #666;'>[{codec}]</span>"
            if is_text:
                label_html += ' <span style="color: #2e7d32;">✓ convertible (mov_text)</span>'
            elif is_picture:
                label_html += ' <span style="color: #c0392b;">⚠ picture-based — cannot mux</span>'
            else:
                label_html += ' <span style="color: #b8860b;">(uncertain)</span>'

            cb = QCheckBox()
            cb.setEnabled(not is_picture)  # Picture-Subs grau und unchecked
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(cb)
            row_label = QLabel(label_html)
            row_label.setTextFormat(Qt.TextFormat.RichText)
            row.addWidget(row_label, 1)

            container = QWidget()
            container.setLayout(row)
            self.subtitle_tracks_layout.addWidget(container)

            # Halbauto: erste konvertierbare Sub pro Sprache
            should_check = False
            if is_text and lang not in languages_with_text_picked:
                should_check = True
                languages_with_text_picked.add(lang)
            cb.setChecked(should_check)

            cb.stateChanged.connect(self._update_workflow_diagram)

            self.subtitle_track_checkboxes.append((cb, stream))

    # ------------------------------------------------------------------
    # Aktionen
    # ------------------------------------------------------------------
    def _toggle_all_checkboxes(self, checkbox_list, state: bool):
        """
        All/None-Helper. Greift nur auf enabled Checkboxes
        (PGS-Subs sind disabled, die wuerden sonst trotz "All" aus bleiben - korrekt so).
        """
        for cb, _ in checkbox_list:
            if cb.isEnabled():
                cb.setChecked(state)

    def _toggle_docs(self):
        self._docs_expanded = not self._docs_expanded
        if self._docs_expanded:
            self.docs_browser.setMaximumHeight(16777215)
            self.docs_browser.setMinimumHeight(220)
            self.docs_browser.show()
            self.docs_toggle_btn.setText("▼ Hide details")
        else:
            self.docs_browser.hide()
            self.docs_browser.setMaximumHeight(0)
            self.docs_browser.setMinimumHeight(0)
            self.docs_toggle_btn.setText("▶ Show details")

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------

    @staticmethod
    def _format_duration_diff(seconds: float) -> str:
        """Formatiert eine Dauer-Differenz wie '0:35' oder '2:13'."""
        seconds = abs(seconds)
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}:{s:02d}"

    def _probe_external_file(self, path: str) -> dict:
        """
        Synchroner ffprobe-Call fuer externe Audio/Sub-Files.

        Returns dict mit codec, duration, language, type ('audio'/'subtitle').
        Bei Fehler: dict mit 'error'-Key.
        """
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json",
                 "-show_streams", "-show_format", path],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                return {"error": f"ffprobe failed: {result.stderr.strip()[:200]}"}
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if not streams:
                return {"error": "No streams found in file"}

            # Erster Audio- oder Subtitle-Stream
            for stream in streams:
                stype = stream.get("codec_type", "").lower()
                if stype in ("audio", "subtitle"):
                    tags = stream.get("tags", {})
                    duration = float(stream.get("duration", 0)
                                     or data.get("format", {}).get("duration", 0)
                                     or 0)
                    return {
                        "type": stype,
                        "codec": stream.get("codec_name", "unknown").lower(),
                        "duration": duration,
                        "language": tags.get("language", "und").upper(),
                        "title": tags.get("title", "").strip(),
                    }
            return {"error": "No audio or subtitle stream in file"}

        except subprocess.TimeoutExpired:
            return {"error": "ffprobe timeout (file may be very large or remote)"}
        except json.JSONDecodeError as e:
            return {"error": f"ffprobe output unparseable: {e}"}
        except Exception as e:
            return {"error": f"Unexpected error: {e}"}

    def _add_external_audio(self):
        """User-Klick auf '+ Add external audio…'."""
        init_dir = self.parent_gui._resolve_initial_browse_dir(
            self.parent_gui.input_line.text()
        )
        filename, _ = QFileDialog.getOpenFileName(
            self, "Add External Audio File", init_dir,
            "Audio Files (*.eac3 *.ac3 *.aac *.m4a *.mp3 *.opus *.flac);;All Files (*.*)"
        )
        if not filename:
            return

        # Probe
        info = self._probe_external_file(filename)
        if "error" in info:
            QMessageBox.warning(
                self, "Cannot Read File",
                f"Failed to probe audio file:\n\n{filename}\n\n{info['error']}"
            )
            return

        if info["type"] != "audio":
            QMessageBox.warning(
                self, "Not an Audio File",
                f"The selected file does not contain an audio stream "
                f"(detected: {info['type']})."
            )
            return

        # Codec-Compat-Check
        codec = info["codec"]
        if codec in self.AUDIO_MP4_INCOMPATIBLE:
            reply = QMessageBox.warning(
                self, "Incompatible Codec",
                f"<b>⚠ {codec.upper()} cannot be muxed into MP4.</b><br><br>"
                "MP4 supports AAC, AC-3, E-AC-3, MP3, Opus, FLAC.<br>"
                "Adding this file would cause FFmpeg to abort.<br><br>"
                "Add anyway? (only useful if you plan to remove it before starting)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # State + UI aktualisieren
        entry = {
            "path": filename,
            "codec": codec,
            "duration": info["duration"],
            "language": info["language"],
            "title": info["title"],
            "enabled": True,
        }
        self.external_audio_files.append(entry)
        self._refresh_external_audio_ui()
        self._update_workflow_diagram()
        self.parent_gui._update_last_browse_dir(filename)

    def _add_external_subtitle(self):
        """User-Klick auf '+ Add external subtitle…'."""
        init_dir = self.parent_gui._resolve_initial_browse_dir(
            self.parent_gui.input_line.text()
        )
        filename, _ = QFileDialog.getOpenFileName(
            self, "Add External Subtitle File", init_dir,
            "Subtitle Files (*.srt *.ass *.ssa *.vtt);;All Files (*.*)"
        )
        if not filename:
            return

        info = self._probe_external_file(filename)
        if "error" in info:
            QMessageBox.warning(
                self, "Cannot Read File",
                f"Failed to probe subtitle file:\n\n{filename}\n\n{info['error']}"
            )
            return

        if info["type"] != "subtitle":
            QMessageBox.warning(
                self, "Not a Subtitle File",
                f"The selected file does not contain a subtitle stream "
                f"(detected: {info['type']})."
            )
            return

        codec = info["codec"]
        if codec in self.SUB_PICTURE_BASED:
            QMessageBox.warning(
                self, "Picture-Based Subtitle",
                f"<b>{codec.upper()}</b> is a picture-based subtitle format and "
                "cannot be muxed into MP4 (no OCR available).<br><br>"
                "Use a text-based subtitle file (SRT, ASS, SSA) instead."
            )
            return

        entry = {
            "path": filename,
            "codec": codec,
            "duration": info["duration"],
            "language": info["language"],
            "title": info["title"],
            "enabled": True,
        }
        self.external_subtitle_files.append(entry)
        self._refresh_external_sub_ui()
        self._update_workflow_diagram()
        self.parent_gui._update_last_browse_dir(filename)

    def _remove_external_file(self, entry: dict, kind: str):
        """Entfernt einen externen File-Eintrag."""
        if kind == "audio" and entry in self.external_audio_files:
            self.external_audio_files.remove(entry)
            self._refresh_external_audio_ui()
        elif kind == "subtitle" and entry in self.external_subtitle_files:
            self.external_subtitle_files.remove(entry)
            self._refresh_external_sub_ui()
        self._update_workflow_diagram()

    def _make_external_file_row(self, entry: dict, kind: str) -> QWidget:
        """
        Baut die UI-Zeile fuer einen externen File-Eintrag.
        Zeigt: Checkbox + Label (filename + codec + sync-warning) + Remove-Button.
        """
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        cb = QCheckBox()
        cb.setChecked(entry["enabled"])
        cb.stateChanged.connect(lambda state, e=entry: self._on_external_toggled(e, state))
        row.addWidget(cb)

        # Label: filename [codec] ✓ MP4-OK (sync-warning?)
        codec = entry["codec"]
        basename = os.path.basename(entry["path"])
        # Mittel-elidieren wenn zu lang
        if len(basename) > 50:
            basename = basename[:24] + "…" + basename[-24:]

        label_parts = [
            f"<b>ext:</b> {basename}",
            f"<span style='color:#666;'>[{codec}]</span>",
        ]
        if kind == "audio":
            if codec in self.AUDIO_MP4_COMPATIBLE:
                label_parts.append('<span style="color:#2e7d32;">✓ MP4-OK</span>')
            elif codec in self.AUDIO_MP4_INCOMPATIBLE:
                label_parts.append('<span style="color:#c0392b;"><b>⚠ not MP4-compatible</b></span>')
        elif kind == "subtitle":
            if codec in self.SUB_CONVERTIBLE:
                label_parts.append('<span style="color:#2e7d32;">✓ → mov_text</span>')

        # Sync-Warnung (nur bei Audio relevant - Subs haben keine harte Sync-Bedingung)
        if kind == "audio" and self.parent_gui.total_duration > 0 and entry["duration"] > 0:
            diff = entry["duration"] - self.parent_gui.total_duration
            if abs(diff) > 1.0:
                direction = "longer than" if diff > 0 else "shorter than"
                label_parts.append(
                    f'<span style="color:#b8860b;">'
                    f'({self._format_duration_diff(diff)} {direction} video)</span>'
                )

        label = QLabel(" ".join(label_parts))
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setToolTip(entry["path"])  # voller Pfad als Tooltip
        row.addWidget(label, 1)

        remove_btn = QPushButton("✗")
        remove_btn.setMaximumWidth(28)
        remove_btn.setToolTip("Remove this external file")
        remove_btn.setStyleSheet(
            "QPushButton { color: #c0392b; font-weight: bold; padding: 2px; }"
            "QPushButton:hover { background: #fff0f0; }"
        )
        remove_btn.clicked.connect(lambda: self._remove_external_file(entry, kind))
        row.addWidget(remove_btn)

        container = QWidget()
        container.setLayout(row)
        return container

    def _on_external_toggled(self, entry: dict, state: int):
        """Externe Checkbox getoggled."""
        entry["enabled"] = bool(state)
        self._update_workflow_diagram()

    def _refresh_external_audio_ui(self):
        """External-Audio-Liste neu aufbauen."""
        while (item := self.audio_external_layout.takeAt(0)) is not None:
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for entry in self.external_audio_files:
            self.audio_external_layout.addWidget(
                self._make_external_file_row(entry, "audio")
            )

    def _refresh_external_sub_ui(self):
        """External-Subtitle-Liste neu aufbauen."""
        while (item := self.sub_external_layout.takeAt(0)) is not None:
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for entry in self.external_subtitle_files:
            self.sub_external_layout.addWidget(
                self._make_external_file_row(entry, "subtitle")
            )

    def _browse_source(self):
        """
        Source-Datei via File-Picker waehlen.

        Wird aufgerufen vom:
          - "Browse…" Button neben Source-Pfad
          - Click auf das Workflow-Diagramm (ClickableLabel)
        """
        # Initial-Verzeichnis: parent's smart-fallback-resolver
        init_dir = self.parent_gui._resolve_initial_browse_dir(
            self.parent_gui.input_line.text()
        )
        filename, _ = QFileDialog.getOpenFileName(
            self, "Select Source Video", init_dir,
            "Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.ts);;All Files (*.*)"
        )
        if not filename:
            return
        self._set_source_file(filename)

    def _set_source_file(self, filepath: str):
        """
        Setzt die Source und triggert async ffprobe.

        Workflow:
          1. parent's input_line wird aktualisiert (mit blockSignals damit
             kein doppeltes Probing entsteht)
          2. last_browse_dir wird im parent gemerkt (fuer naechsten Browse)
          3. UI zeigt Loading-State
          4. parent._load_video_info_async wird mit unserem Callback gestartet -
             parent's eigener Callback (_on_video_info_loaded) feuert auch
             und aktualisiert parent-State (audio_streams, total_duration etc.)
          5. Wenn parent fertig ist, ruft unser Callback _populate_from_parent()
             auf, was die ganze Dialog-UI inkl. Diagramm aktualisiert
        """
        p = self.parent_gui

        # parent.input_line setzen ohne textChanged-Re-Trigger
        p.input_line.blockSignals(True)
        p.input_line.setText(filepath)
        p.input_line.blockSignals(False)

        # Smart-Browse-Verzeichnis im parent merken
        p._update_last_browse_dir(filepath)

        # Loading-UI im Dialog
        self.source_path_label.setText(
            f"<i>Loading {os.path.basename(filepath)}…</i>"
        )
        self.source_path_label.setStyleSheet("color: #555;")
        self.start_btn.setEnabled(False)
        self._show_streams_loading()

        # parent's async load mit unserem Callback
        p._load_video_info_async(filepath, done_callback=self._on_source_loaded)

    def _on_source_loaded(self, info):
        """
        Wird gefeuert nachdem parent's ffprobe abgeschlossen ist.

        Parent's eigener Callback (_on_video_info_loaded) ist VOR uns gefeuert
        und hat parent-State (audio_streams, subtitle_streams, total_duration,
        input_video_codec etc.) bereits aktualisiert. Wir muessen also nur
        noch unsere Dialog-UI aus dem aktuellen parent-State neu populieren.
        """
        self._loading_source = False
        if info.error:
            QMessageBox.warning(
                self, "File Error",
                f"Failed to read source file:\n\n{info.error}"
            )
            self._populate_from_parent()
            self._update_workflow_diagram()
            return

        if not info.is_valid:
            QMessageBox.warning(
                self, "Invalid Video File",
                "The selected file does not appear to be a valid video "
                "(no readable duration, fps, or streams)."
            )
            self._populate_from_parent()
            self._update_workflow_diagram()
            return

        # Repopulate aus dem nun aktuellen parent-State
        self._populate_from_parent()
        self._update_workflow_diagram()

    def _show_streams_loading(self):
        """Streams-Bereich auf 'Loading…' setzen waehrend ffprobe laeuft."""
        self._loading_source = True
        # Audio
        while (item := self.audio_tracks_layout.takeAt(0)) is not None:
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.audio_track_checkboxes = []
        self.audio_tracks_layout.addWidget(QLabel("<i>Loading audio streams…</i>"))
        self.audio_all_btn.setEnabled(False)
        self.audio_none_btn.setEnabled(False)

        # Subtitles
        while (item := self.subtitle_tracks_layout.takeAt(0)) is not None:
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.subtitle_track_checkboxes = []
        self.subtitle_tracks_layout.addWidget(QLabel("<i>Loading subtitle streams…</i>"))
        self.sub_all_btn.setEnabled(False)
        self.sub_none_btn.setEnabled(False)

        # Video Label
        self.video_label.setText("<i>Loading video info…</i>")

        # Hide TrueHD warning (will be re-set if needed after probe)
        self.truehd_warning_label.hide()

        # Diagramm zeigt "Loading..."-Hinweis
        self._update_workflow_diagram()

    def _browse_output(self):
        current = self.output_line.text() or os.path.expanduser("~")
        init_dir = os.path.dirname(current) if current else os.path.expanduser("~")
        filename, _ = QFileDialog.getSaveFileName(
            self, "Select Output File", init_dir, "MP4 Files (*.mp4)"
        )
        if filename:
            if not filename.lower().endswith('.mp4'):
                filename += '.mp4'
            self.output_line.setText(filename)

    def _on_start_clicked(self):
        """Sammelt Settings, baut Remux-Befehl, triggert process_single_file im parent."""
        p = self.parent_gui
        input_path = p.input_line.text().strip()
        output_path = self.output_line.text().strip()

        if not input_path or not os.path.isfile(input_path):
            QMessageBox.warning(self, "No Input", "No valid input file loaded.")
            return
        if not output_path:
            QMessageBox.warning(self, "No Output", "Please specify an output path.")
            return

        selected_audio = [s for cb, s in self.audio_track_checkboxes if cb.isChecked()]
        selected_subs = [s for cb, s in self.subtitle_track_checkboxes if cb.isChecked()]

        enabled_ext_audio = [e for e in self.external_audio_files if e.get("enabled")]
        enabled_ext_subs = [e for e in self.external_subtitle_files if e.get("enabled")]
        total_audio_count = len(selected_audio) + len(enabled_ext_audio)

        if (self.audio_track_checkboxes or self.external_audio_files) and total_audio_count == 0:
            reply = QMessageBox.question(
                self, "No Audio Tracks Selected",
                "You haven't selected any audio tracks (internal or external).\n"
                "The output will be silent.\n\nContinue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # TrueHD warnen - intern UND extern
        truehd_internal = [s for s in selected_audio if s.get('codec_name', '').lower() == 'truehd']
        truehd_external = [e for e in enabled_ext_audio if e.get('codec', '').lower() == 'truehd']
        truehd_total = len(truehd_internal) + len(truehd_external)
        if truehd_total > 0:
            reply = QMessageBox.warning(
                self, "TrueHD Track Selected",
                f"<b>⚠ {truehd_total} TrueHD track(s) selected for MP4 output.</b><br><br>"
                "MP4 cannot contain TrueHD. FFmpeg will likely abort with "
                "<i>\"codec not currently supported in container\"</i>.<br><br>Continue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # Existierendes Output abfragen
        if os.path.exists(output_path):
            reply = QMessageBox.question(
                self, "Output File Exists",
                f"The output file already exists:\n\n{output_path}\n\nOverwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        cmd = self._build_remux_command(input_path, output_path, selected_audio, selected_subs)

        # CHANGED: Dialog bleibt offen waehrend des Remux. Frueher hier:
        #   self.accept()   <- schloss den Dialog sofort
        # Stattdessen: Buttons sperren, an worker.finished_signal haengen,
        # nach Abschluss Buttons wieder freigeben damit der User direkt das
        # naechste File remuxen kann ohne den Dialog neu oeffnen zu muessen.
        self._set_dialog_busy(True)

        # An finished-Signal des MainWindow-Workers haengen. process_single_file
        # erstellt den Worker frisch, daher muessen wir uns NACH dem Aufruf an
        # parent.worker.finished_signal haengen.
        p.process_single_file(input_path, output_path, cmd_override=cmd)
        if p.worker is not None:
            p.worker.finished_signal.connect(self._on_remux_finished)

    def _set_dialog_busy(self, busy: bool):
        """
        NEW: Sperrt/entsperrt die Dialog-Controls waehrend ein Remux laeuft.
        Wird sowohl beim Start als auch beim Finish-Callback aufgerufen.
        """
        self.start_btn.setEnabled(not busy)
        self.start_btn.setText("Remuxing…" if busy else "Start Remux")
        # Cancel-Button beschriftung passt sich an, Funktion bleibt: schliesst Dialog.
        # Im Busy-State macht "Cancel" den Dialog zu, Encoding laeuft im MainWindow
        # weiter - dort kann es ueber den dortigen Cancel-Button gestoppt werden.
        self.cancel_btn.setText("Close")
        # Inputs/Streams sperren, damit User waehrend des Runs nicht das Source-File wechselt.
        for attr in ('src_browse_btn', 'out_browse_btn',
                     'audio_add_external_btn', 'sub_add_external_btn',
                     'audio_all_btn', 'audio_none_btn',
                     'sub_all_btn', 'sub_none_btn',
                     'streams_box'):
            w = getattr(self, attr, None)
            if w is not None:
                w.setEnabled(not busy)

    def _on_remux_finished(self, exit_code: int, output_path: str):
        """
        NEW: Wird gefeuert wenn der Remux-Worker fertig ist (Erfolg ODER Fehler).
        Reaktiviert die Dialog-UI damit der User direkt das naechste File
        anwerfen kann.
        """
        self._set_dialog_busy(False)

    def _build_remux_command(self, input_path: str, output_path: str,
                             selected_audio_streams, selected_subtitle_streams) -> List[str]:
        """
        Baut den Remux-Befehl - Multi-Input-fähig.

        Logik:
          - `-c copy` als globaler Default (kopiert Video + Audio 1:1)
          - Subtitles: explizit `-c:s mov_text` fuer text-basierte Subs
            (konvertiert SRT/ASS in das MP4-native mov_text-Format)
          - `-tag:v hvc1` falls Apple-Compat aktiv UND Video ist HEVC
          - `-strict -2` defensiv fuer eac3-in-MP4
          - Stream-Maps explizit pro ausgewaehltem Track
          - Multiple `-i` inputs fuer externe Audio/Sub-Files
            * Input 0: das eigentliche Source-File (Video-Quelle)
            * Input 1..K: externe Audio-Files
            * Input K+1..N: externe Subtitle-Files
            * Mappings: 0:v:0, 0:N (interne Audio/Sub),
                        K:a:0 (externe Audio), L:s:0 (externe Sub)
        """
        cmd = ["ffmpeg", "-y", "-progress", "pipe:1", "-nostats", "-loglevel", "error"]

        # Input 0: Source-File
        cmd.extend(["-i", input_path])

        # Externe Audio-Files: Input 1..K (nur enabled)
        enabled_ext_audio = [e for e in self.external_audio_files if e.get("enabled")]
        ext_audio_input_indices = []
        for entry in enabled_ext_audio:
            input_idx = len(ext_audio_input_indices) + 1
            cmd.extend(["-i", entry["path"]])
            ext_audio_input_indices.append(input_idx)

        # Externe Subtitle-Files: Input K+1..N (nur enabled)
        enabled_ext_subs = [e for e in self.external_subtitle_files if e.get("enabled")]
        ext_sub_input_indices = []
        for entry in enabled_ext_subs:
            input_idx = 1 + len(enabled_ext_audio) + len(ext_sub_input_indices)
            cmd.extend(["-i", entry["path"]])
            ext_sub_input_indices.append(input_idx)

        # ---- Stream-Mapping ----

        # Video: erster Stream aus Input 0
        cmd.extend(["-map", "0:v:0"])

        # Interne Audio
        for stream in selected_audio_streams:
            idx = stream.get('index', -1)
            if idx >= 0:
                cmd.extend(["-map", f"0:{idx}"])

        # Externe Audio (jedes File hat einen Audio-Stream auf 0:a:0 seines Input)
        for input_idx in ext_audio_input_indices:
            cmd.extend(["-map", f"{input_idx}:a:0"])

        # Interne Subtitle
        for stream in selected_subtitle_streams:
            idx = stream.get('index', -1)
            if idx >= 0:
                cmd.extend(["-map", f"0:{idx}"])

        # Externe Subtitle
        for input_idx in ext_sub_input_indices:
            cmd.extend(["-map", f"{input_idx}:s:0"])

        # ---- Codec-Strategie ----
        cmd.extend(["-c", "copy"])
        # mov_text Konvertierung greift sowohl fuer interne als auch externe Subs
        if selected_subtitle_streams or enabled_ext_subs:
            cmd.extend(["-c:s", "mov_text"])

        # Apple Compatibility: HEVC tag von "hev1" auf "hvc1" aendern
        # (nur sinnvoll wenn Video tatsaechlich HEVC ist)
        if self.apple_compat_checkbox.isChecked():
            video_codec = self.parent_gui.input_video_codec
            if video_codec == 'h265':
                cmd.extend(["-tag:v", "hvc1"])

        # eac3-in-MP4 explizit erlauben (defensiv)
        cmd.extend(["-strict", "-2"])

        cmd.append(output_path)
        return cmd

    # ------------------------------------------------------------------
    # Inhalt: Workflow-Diagramm (echte Widget-Hierarchie) + Doku
    # ------------------------------------------------------------------
    def _update_workflow_diagram(self):
        """
        Re-rendert das Workflow-Diagramm als echte Widget-Hierarchie.

        Bei jedem Toggle (Audio/Sub-Checkbox, Apple-Compat, Output-Pfad) wird
        das Diagramm-Layout komplett geleert und neu aufgebaut. Bei 4-8 Streams
        sind das ein paar Dutzend Widgets - performant genug.

        Drei Modi:
          - Loading: zentrierter "Loading…"-Hinweis (waehrend ffprobe laeuft)
          - File geladen: Live-Diagramm mit echten Streams
          - Kein File: Generisches Beispiel
        """
        if not hasattr(self, 'diagram_layout'):
            return

        # Layout leeren.
        # WICHTIG: setParent(None) zwingt das Widget sofort aus dem Visual-Tree.
        # deleteLater() allein loescht asynchron - dadurch konnten alte und
        # neue Widgets im Diagramm parallel sichtbar sein und sich beim
        # File-Wechsel ueberlappen (Source-Box + Output-Box uebereinander).
        while (item := self.diagram_layout.takeAt(0)) is not None:
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        # Loading-State
        if self._loading_source:
            loading = QLabel("<i>Loading streams from source file…</i>")
            loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
            loading.setStyleSheet("color: #555; padding: 30px; background: transparent; border: none;")
            self.diagram_layout.addWidget(loading)
            return

        # Live oder Generic
        p = self.parent_gui
        input_path = p.input_line.text().strip()
        if input_path and os.path.isfile(input_path):
            self._build_live_diagram_widgets(input_path)
        else:
            self._build_generic_diagram_widgets()

    @staticmethod
    def _shorten_filename(name: str, max_len: int = 38) -> str:
        """Mittel-elidiert lange Filenamen fuer die Container-Header."""
        if len(name) <= max_len:
            return name
        keep = max_len - 1
        left = keep // 2
        right = keep - left
        return name[:left] + "…" + name[-right:]

    # ---- Stream-Item-Builder ------------------------------------------------

    # Hintergrundfarben pro Stream-Typ (Pastell-Codierung)
    _STREAM_BG_COLORS = {
        'video': '#d8e4ff',
        'audio': '#ffe8d6',
        'sub':   '#fff3d6',
    }

    def _make_stream_item(self, label_text: str, stream_type: str,
                          included: bool, click_action) -> QLabel:
        """
        Baut einen einzelnen Stream-Eintrag im Diagramm.

        Klickbar (oeffnet Source/Output-Picker), zeigt Hover-Effekt.
        Nicht-eingeschlossene Streams werden ausgegraut/durchgestrichen.
        """
        bg = self._STREAM_BG_COLORS.get(stream_type, '#eeeeee')
        item = ClickableLabel(label_text)
        if included:
            item.setStyleSheet(
                f"ClickableLabel {{ background: {bg}; border: 1px solid #aaa; "
                f"padding: 3px 8px; font-family: 'Consolas', 'Courier New', monospace; "
                f"font-size: 9pt; color: #222; }}"
                "ClickableLabel:hover { background: #b8c8ff; border: 1px solid #4a90e2; }"
            )
        else:
            item.setStyleSheet(
                "ClickableLabel { background: #f0f0f0; border: 1px solid #ccc; "
                "padding: 3px 8px; font-family: 'Consolas', 'Courier New', monospace; "
                "font-size: 9pt; color: #999; text-decoration: line-through; }"
                "ClickableLabel:hover { background: #e0e0e0; border: 1px solid #999; }"
            )
        item.clicked.connect(click_action)
        return item

    def _make_container(self, title_text: str, bg_color: str,
                        click_action) -> tuple:
        """
        Baut den Source/Output-Container als ClickableFrame.

        Returns (frame, inner_layout). Caller fuegt dann die Stream-Items
        ueber inner_layout.addWidget(...) ein.

        Der Container selbst ist auch klickbar - so trifft der User auch
        wenn er zwischen den Items klickt.
        """
        frame = ClickableFrame()
        frame.setObjectName("diagramBox")
        frame.setStyleSheet(
            f"ClickableFrame#diagramBox {{ background: {bg_color}; "
            f"border: 2px solid #777; border-radius: 4px; }}"
            "ClickableFrame#diagramBox:hover { border: 2px solid #4a90e2; }"
        )
        frame.clicked.connect(click_action)

        inner = QVBoxLayout(frame)
        inner.setContentsMargins(8, 6, 8, 6)
        inner.setSpacing(3)

        # Header: Filename
        title = QLabel(f"<b>{title_text}</b>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "background: transparent; border: none; padding: 0; color: #222; font-size: 9pt;"
        )
        inner.addWidget(title)

        return frame, inner

    def _make_arrow_widget(self) -> QWidget:
        """Mittiger Pfeil-Block zwischen Source und Output."""
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        v = QVBoxLayout(wrapper)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        v.addStretch()
        arrow = QLabel("→")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow.setStyleSheet(
            "font-size: 28pt; color: #4CAF50; background: transparent; border: none;"
        )
        v.addWidget(arrow)
        cap = QLabel("copy<br>(no re-encode)")
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setStyleSheet(
            "font-size: 9pt; color: #555; background: transparent; border: none;"
        )
        v.addWidget(cap)
        v.addStretch()
        return wrapper

    # ---- Live-Diagramm (echte Streams) -------------------------------------

    def _build_live_diagram_widgets(self, input_path: str):
        """Diagramm mit echten Streams aus dem geladenen File.
        Externe Files erscheinen mit 'ext:' Prefix im Source und Output."""
        p = self.parent_gui
        src_name = self._shorten_filename(os.path.basename(input_path))
        out_path = self.output_line.text().strip()
        out_name = self._shorten_filename(os.path.basename(out_path)) if out_path else "(output).mp4"

        # ---- Source-Container ----
        src_frame, src_inner = self._make_container(
            src_name, "#f0f4ff", self._browse_source
        )
        src_frame.setToolTip("Click to choose a different source file")

        # Video (immer drin, kein toggle)
        codec_disp = p.input_video_codec.upper() if p.input_video_codec != 'unknown' else '?'
        src_inner.addWidget(self._make_stream_item(
            f"Video ({codec_disp})", 'video', True, self._browse_source
        ))

        # Interne Audio
        for cb, stream in self.audio_track_checkboxes:
            idx = stream.get('index', '?')
            tags = stream.get('tags', {})
            lang = tags.get('language', 'und').upper()
            codec = stream.get('codec_name', '?').lower()
            src_inner.addWidget(self._make_stream_item(
                f"#{idx} {lang} ({codec})", 'audio', cb.isChecked(), self._browse_source
            ))


        for entry in self.external_audio_files:
            short_name = os.path.basename(entry["path"])
            if len(short_name) > 22:
                short_name = short_name[:10] + "…" + short_name[-10:]
            label = f"ext: {short_name} ({entry['codec']})"
            # Click auf externes Item: gleicher Container-Handler (Source wechseln)
            src_inner.addWidget(self._make_stream_item(
                label, 'audio', entry.get('enabled', True), self._browse_source
            ))

        # Interne Subtitles
        for cb, stream in self.subtitle_track_checkboxes:
            idx = stream.get('index', '?')
            tags = stream.get('tags', {})
            lang = tags.get('language', 'und').upper()
            codec = stream.get('codec_name', '?').lower()
            src_inner.addWidget(self._make_stream_item(
                f"#{idx} {lang} sub ({codec})", 'sub', cb.isChecked(), self._browse_source
            ))


        for entry in self.external_subtitle_files:
            short_name = os.path.basename(entry["path"])
            if len(short_name) > 22:
                short_name = short_name[:10] + "…" + short_name[-10:]
            label = f"ext: {short_name} ({entry['codec']})"
            src_inner.addWidget(self._make_stream_item(
                label, 'sub', entry.get('enabled', True), self._browse_source
            ))

        # ---- Output-Container ----
        out_frame, out_inner = self._make_container(
            out_name, "#f0fff4", self._browse_output
        )
        out_frame.setToolTip("Click to choose a different output path")

        # Video (mit ggf. Apple-Tag)
        apple_tag = ""
        if (self.apple_compat_checkbox.isChecked()
                and p.input_video_codec == 'h265'):
            apple_tag = " + hvc1"
        out_inner.addWidget(self._make_stream_item(
            f"Video ({codec_disp}){apple_tag}", 'video', True, self._browse_output
        ))

        # Interne Audio (nur ausgewaehlte)
        n_audio = 0
        for cb, stream in self.audio_track_checkboxes:
            if not cb.isChecked():
                continue
            n_audio += 1
            idx = stream.get('index', '?')
            tags = stream.get('tags', {})
            lang = tags.get('language', 'und').upper()
            codec = stream.get('codec_name', '?').lower()
            out_inner.addWidget(self._make_stream_item(
                f"#{idx} {lang} ({codec})", 'audio', True, self._browse_output
            ))


        for entry in self.external_audio_files:
            if not entry.get('enabled', True):
                continue
            n_audio += 1
            short_name = os.path.basename(entry["path"])
            if len(short_name) > 22:
                short_name = short_name[:10] + "…" + short_name[-10:]
            label = f"ext: {short_name} ({entry['codec']})"
            out_inner.addWidget(self._make_stream_item(
                label, 'audio', True, self._browse_output
            ))

        # Interne Subtitles (nur ausgewaehlte, jetzt mov_text)
        for cb, stream in self.subtitle_track_checkboxes:
            if not cb.isChecked():
                continue
            idx = stream.get('index', '?')
            tags = stream.get('tags', {})
            lang = tags.get('language', 'und').upper()
            out_inner.addWidget(self._make_stream_item(
                f"#{idx} {lang} sub (mov_text)", 'sub', True, self._browse_output
            ))


        for entry in self.external_subtitle_files:
            if not entry.get('enabled', True):
                continue
            short_name = os.path.basename(entry["path"])
            if len(short_name) > 22:
                short_name = short_name[:10] + "…" + short_name[-10:]
            label = f"ext: {short_name} (mov_text)"
            out_inner.addWidget(self._make_stream_item(
                label, 'sub', True, self._browse_output
            ))

        # Wenn kein Audio im Output: roter Hinweis
        if n_audio == 0:
            no_audio = QLabel("⚠ no audio selected")
            no_audio.setStyleSheet(
                "background: #fff5f5; border: 1px dashed #c0392b; "
                "color: #c0392b; padding: 3px 8px; font-size: 8pt; "
                "font-family: sans-serif;"
            )
            out_inner.addWidget(no_audio)

        # Layout zusammenbauen
        self.diagram_layout.addWidget(src_frame, 0, Qt.AlignmentFlag.AlignTop)
        self.diagram_layout.addWidget(self._make_arrow_widget(), 0, Qt.AlignmentFlag.AlignVCenter)
        self.diagram_layout.addWidget(out_frame, 0, Qt.AlignmentFlag.AlignTop)
        self.diagram_layout.addStretch(1)

    # ---- Generic-Diagramm (kein File) --------------------------------------

    def _build_generic_diagram_widgets(self):
        """Beispiel-Diagramm wenn kein File geladen ist."""
        # Source-Container
        src_frame, src_inner = self._make_container(
            "source.mkv", "#f0f4ff", self._browse_source
        )
        src_frame.setToolTip("Click to choose a source file")

        for label, stype in [
            ("Video (HEVC + DV RPU)", 'video'),
            ("Audio Stream_A (eac3)", 'audio'),
            ("Audio Stream_B (ac3)", 'audio'),
            ("Subtitle Stream_C (srt)", 'sub'),
        ]:
            src_inner.addWidget(self._make_stream_item(
                label, stype, True, self._browse_source
            ))

        # Output-Container
        out_frame, out_inner = self._make_container(
            "source.mp4", "#f0fff4", self._browse_source
        )
        out_frame.setToolTip("Click to choose a source file")

        for label, stype in [
            ("Video (HEVC + DV RPU) + hvc1", 'video'),
            ("Audio Stream_A (eac3)", 'audio'),
            ("Audio Stream_B (ac3)", 'audio'),
            ("Subtitle Stream_C (mov_text)", 'sub'),
        ]:
            out_inner.addWidget(self._make_stream_item(
                label, stype, True, self._browse_source
            ))

        self.diagram_layout.addWidget(src_frame, 0, Qt.AlignmentFlag.AlignTop)
        self.diagram_layout.addWidget(self._make_arrow_widget(), 0, Qt.AlignmentFlag.AlignVCenter)
        self.diagram_layout.addWidget(out_frame, 0, Qt.AlignmentFlag.AlignTop)
        self.diagram_layout.addStretch(1)


    def _documentation_html(self) -> str:
        """Kompakte Doku im Dialog. Vollversion ist in Remux_Documentation.md."""
        return """
<style>
  body { font-family: sans-serif; font-size: 10pt; color: #222; }
  h3 { margin-top: 12px; margin-bottom: 4px; }
  ul { margin-top: 4px; }
  table { border-collapse: collapse; }
  td, th { padding: 4px 8px; border: 1px solid #bbb; }
  th { background: #eee; }
  code { background: #eee; padding: 1px 4px; font-family: Consolas, monospace; }
</style>

<h3>Why remux?</h3>
<p>Many TVs and hardware players (LG OLED, certain Samsungs, Apple TV) only render
Dolby Vision metadata when the file is in an MP4 container. The exact same HEVC
video stream — same RPU layer carrying the DV metadata — plays as plain HDR10 in
MKV but lights up as Dolby Vision in MP4.</p>

<h3>What gets transferred</h3>
<ul>
  <li><b>Video</b>: copied 1:1 — bitstream, DV RPU, HDR metadata, timestamps. Identical bytes.</li>
  <li><b>Audio</b>: copied 1:1 for each track you check. Codec must be MP4-compatible (see table).</li>
  <li><b>Subtitles</b>: text-based subs (SRT, ASS, SSA) are converted to <code>mov_text</code>
      (MP4's native subtitle format). Picture-based subs (PGS, DVB) cannot be muxed
      and are disabled in the list.</li>
</ul>

<h3>Audio codec compatibility</h3>
<table>
  <tr><th>Codec</th><th>MP4?</th><th>Notes</th></tr>
  <tr><td>AAC, AC-3, E-AC-3, MP3</td><td>✅</td><td>Standard MP4 audio</td></tr>
  <tr><td>FLAC, Opus</td><td>✅</td><td>Spec-supported; old players may struggle</td></tr>
  <tr><td><b>TrueHD</b></td><td>❌</td><td><b>Not supported</b> — UHD-BluRay rip</td></tr>
  <tr><td>DTS-HD MA</td><td>❌</td><td>Not supported in MP4</td></tr>
</table>

<h3>Subtitle handling</h3>
<table>
  <tr><th>Codec</th><th>MP4?</th><th>Notes</th></tr>
  <tr><td>SRT (subrip), ASS, SSA</td><td>✅</td><td>Auto-converted to <code>mov_text</code></td></tr>
  <tr><td>PGS / SUP (BluRay graphical)</td><td>❌</td><td>Picture-based; cannot mux to MP4</td></tr>
  <tr><td>DVB / DVD subtitles</td><td>❌</td><td>Picture-based; cannot mux to MP4</td></tr>
</table>

<h3>Apple compatibility</h3>
<p>The "Apple compatibility" checkbox sets the HEVC tag to <code>hvc1</code> instead of
the default <code>hev1</code>. Required for Quicktime, MacOS Finder thumbnails, iOS, and
Apple TV playback. Without this, Apple devices may show no audio, no thumbnail, or
refuse the file. Has no effect on H.264/AV1.</p>

<h3>The FFmpeg command</h3>
<p>Typical case (HEVC + 1 audio + 1 SRT subtitle, Apple compat on):</p>
<pre style="background: #f0f0f0; padding: 6px; border: 1px solid #ddd;">
ffmpeg -y -i "source.mkv"
       -map 0:v:0 -map 0:1 -map 0:2
       -c copy -c:s mov_text -tag:v hvc1 -strict -2
       "source.mp4"
</pre>

<h3>Pre-selection logic</h3>
<p>Tracks are pre-selected per language:</p>
<ul>
  <li><b>Audio</b>: first MP4-compatible track per language is checked. Multiple
      tracks of the same language stay unchecked; check them manually if needed.</li>
  <li><b>Subtitles</b>: first text-based subtitle per language is checked. Picture-based
      subs are disabled and cannot be checked.</li>
</ul>

<h3>When NOT to use remux</h3>
<ul>
  <li>You want to <b>reduce file size</b> — remux doesn't change bitrate. Re-encode instead.</li>
  <li>You want to <b>change resolution</b> — remux can't downscale. Use 4K → 1080p.</li>
  <li>The source has <b>only TrueHD audio</b> or PGS subs you need — re-encoding is required.</li>
  <li>Your player <b>already plays MKV-DV correctly</b> — no need to remux.</li>
</ul>
"""



# ============================================================================
class FFmpegGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FFmpeg Converter GUI 2.6")

        # damit "Mediathek-Safe" nicht abgeschnitten wird und Container in der gleichen
        # Zeile rein passt.

        # Audio-Tracks sitzen jetzt im Settings-Grid (Spalte 2, ueberspannt
        # beide Zeilen) statt in einer eigenen Zeile darunter. Damit kommt das
        # Hauptfenster mit kompakten 620 px Hoehe aus - das Layout wirkt beim
        # Einlesen von Source-Daten ruhiger als groessere Default-Werte
        # (kein "Aufblaeh-Sprung" beim Erstauftritt der Stream-Infos).
        # Mindesthoehe = Default: das Fenster darf nicht unter die Default-
        # Hoehe schrumpfen, weil sonst Settings-Zone und Audio-Tracks-GroupBox
        # gequetscht werden.
        self.resize(1280, 620)
        self.setMinimumWidth(1280)
        self.setMinimumHeight(620)

        self.script_dir = os.path.dirname(os.path.realpath(__file__))
        self.logo_path = os.path.join(self.script_dir, "resources", "icons", "logo.png")
        if os.path.exists(self.logo_path):
            self.setWindowIcon(QIcon(self.logo_path))
        
        # Thread management
        self.worker = None
        self.worker_mutex = QMutex()
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(4)
        
        # State variables
        self.total_duration = 0.0
        self.fps = 0.0
        self.paused = False
        self.audio_streams = []
        self.subtitle_streams = []
        self.input_video_fps = 0.0
        self.gpu_info_list = []
        
        # Input video properties
        self.input_width = 0
        self.input_height = 0
        self.input_pix_fmt = 'yuv420p'  # Pixel format vom Input-Video

        self.input_video_codec = 'unknown'    # h264 / h265 / av1 / unknown
        self.input_source_bitrate = 0.0       # Mbps (0.0 = unbekannt / kein Video)
        
        # Dolby Atmos protection
        self.has_atmos_detected = False
        self.atmos_stream_indices = []


        self.user_cancelled = False
        self.pending_batch_timers = []
        

        # Verhindert dass Auto-Bitrate (50%) bewusste User-Eingaben überschreibt
        self._user_modified_bitrate = False
        

        self.batch_toast = None

        self.batch_mode = False
        self.batch_files = []
        self.batch_index = 0
        self.batch_master_config = None
        self.batch_paused_for_reconfig = False

        self.batch_skipped_count = 0

        self.predicted_file_size_mb = 0
        self.prediction_start_time_seconds = 30
        self.prediction_update_interval_seconds = 60
        self.last_prediction_update_time = 0.0


        # (vorher: %LocalAppData%\FFmpegAV1Converter\). Tool + Config wandern
        # zusammen - USB-Stick-tauglich, mehrere Installationen koennen
        # unabhaengige Configs haben.
        #
        # sys.argv[0] funktioniert konsistent fuer:
        #   - Python-Skript            (.py-Datei)
        #   - Nuitka standalone        (.exe im Programm-Ordner)
        #   - Nuitka --onefile         (Original-.exe, NICHT der temp-Pfad)
        program_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        self.settings_path = os.path.join(program_dir, 'Converter_settings.json')
        print(f"📋 Settings file: {self.settings_path}")


        # Greift nur einmal beim ersten v2.5-Start mit existierender v2.0-Config.
        self._migrate_legacy_settings_if_needed()


        # Wird in load_settings aus der Config gelesen (None wenn kein Settings-File
        # vorhanden -> erster Start -> _resolve_initial_browse_dir liefert System-Root).
        # Wird in save_settings persistiert. Wird bei jedem erfolgreichen Browse
        # via _update_last_browse_dir aktualisiert.
        self.last_browse_dir = None

        # NEW: Theme (light/dark) - tatsaechlicher Apply geschieht in __main__
        # via apply_theme(); hier nur das Attribut anlegen, damit setup_ui()
        # den Toggle-Button korrekt beschriften kann. Wird in load_settings()
        # auf den persistierten Wert gesetzt.
        self.current_theme = THEME_LIGHT

        self.setup_ui()
        self.load_settings()

        # Enable Drag & Drop
        self.setAcceptDrops(True)

    def _migrate_legacy_settings_if_needed(self) -> None:
        """
        Einmalige Migration der Config aus dem alten AppData-Ort
        (%LocalAppData%\\FFmpegAV1Converter\\Converter_settings.json) in den
        neuen portable Ort neben dem Programm.

        Greift nur, wenn:
          - Neue Config existiert NICHT (sonst wuerde Migration ueberschreiben)
          - Alte Config existiert (v2.0-User mit gespeicherten Settings)

        Beim naechsten save_settings landet die Config dann im neuen Ort.
        Die alte Datei in AppData bleibt liegen - kann der User loeschen
        wenn gewuenscht.
        """
        if os.path.exists(self.settings_path):
            return  # Neue Config schon da, nichts zu tun

        legacy_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.ConfigLocation)
        legacy_path = os.path.join(legacy_dir, "FFmpegAV1Converter", "Converter_settings.json")

        if not os.path.exists(legacy_path):
            return  # Kein Legacy-File - normaler erster Start

        try:
            shutil.copy2(legacy_path, self.settings_path)
            print(f"📦 Migrated settings from {legacy_path}")
            print(f"   to new portable location next to the program.")
            print(f"   Legacy file left in place - delete manually if desired.")
        except Exception as e:
            # Kein Drama - User landet einfach mit Defaults und kann neu speichern
            print(f"⚠️  Could not migrate legacy settings ({e}). Using defaults.")

    # ========================================================================

    # ========================================================================
    #
    # Multiplier-Matrix: source_codec → target_codec
    #
    # Begruendung der Werte (Industrie-Faustregeln aus Codec-Comparisons):
    #   - H.265 ist ~50% effizienter als H.264 bei gleicher Qualitaet
    #   - AV1 ist ~30% effizienter als H.265
    #   - => H.264 → AV1 spart am meisten, AV1 → H.264 braucht *mehr* Bitrate
    #
    # User-Beobachtung als Trigger fuer diese Logik: 50% Pauschale fuehrt
    # bei H.265-Source zu Blockartefakten - H.265 hat weniger Headroom als H.264.
    BITRATE_MULTIPLIERS = {
        # source → target
        ('h264', 'h264'): 0.50,
        ('h264', 'h265'): 0.35,
        ('h264', 'av1'):  0.30,
        ('h265', 'h264'): 1.00,  # selten sinnvoll, aber sauber definiert
        ('h265', 'h265'): 0.70,
        ('h265', 'av1'):  0.60,
        ('av1',  'h264'): 2.00,  # AV1 → alte Codecs brauchen *mehr*
        ('av1',  'h265'): 1.20,
        ('av1',  'av1'):  0.80,
    }
    UNKNOWN_SOURCE_MULTIPLIER = 0.50  # Fallback wie v2.0-Verhalten

    # Quality-Floor: minimale Bitrate je Resolution+Target-Codec, unter der
    # Blockartefakte sehr wahrscheinlich werden. Wird angewendet, *ausser*
    # wenn die Source-Bitrate selbst schon drunter liegt (dann nicht ueber
    # Source gehen!).
    QUALITY_FLOOR_MBPS = {
        # (resolution_class, target_codec) -> floor in Mbps
        ('1080p', 'av1'):  2.0,
        ('1080p', 'h265'): 2.5,
        ('1080p', 'h264'): 4.0,
        ('4k',    'av1'):  6.0,
        ('4k',    'h265'): 8.0,
        ('4k',    'h264'): 12.0,
    }

    # ========================================================================

    # ========================================================================
    #
    # Welche Codecs darf welcher Container? Liste der Codec-Data-Werte
    # (currentData()) die per Container erlaubt sind. Bei Container-Wechsel
    # filtern wir das Video-Codec-Dropdown entsprechend.
    #
    # Quellen: FFmpeg 8.0 muxer-Capabilities + praktische Player-Compat.
    # WebM ist absichtlich strikt (VP9/AV1 + Opus/Vorbis, nichts anderes -
    # so legt's der Standard fest).
    # ========================================================================

    CONTAINER_VIDEO_CODECS = {
        # Reihenfolge bestimmt UI-Reihenfolge im Dropdown
        'mkv':  ['av1_nvenc', 'hevc_nvenc', 'h264_nvenc',
                 'libsvt-av1', 'libx265', 'libx264', 'libvpx-vp9'],
        'mp4':  ['av1_nvenc', 'hevc_nvenc', 'h264_nvenc',
                 'libsvt-av1', 'libx265', 'libx264'],
        'mov':  ['hevc_nvenc', 'h264_nvenc', 'libx265', 'libx264'],
        'webm': ['av1_nvenc', 'libsvt-av1', 'libvpx-vp9'],
    }

    # Display-Labels fuer das Video-Codec-Dropdown (was der User sieht)
    VIDEO_CODEC_LABELS = {
        'av1_nvenc':  'AV1 (av1_nvenc)',
        'hevc_nvenc': 'H.265 (hevc_nvenc)',
        'h264_nvenc': 'H.264 (h264_nvenc)',
        'libsvt-av1': 'AV1 (libsvt-av1)',
        'libx265':    'H.265 (libx265)',
        'libx264':    'H.264 (libx264)',
        'libvpx-vp9': 'VP9 (libvpx-vp9)',
    }

    # Welche Audio-Codecs erlaubt jeder Container? Display-Labels.
    CONTAINER_AUDIO_CODECS = {
        'mkv':  ['Copy', 'AAC', 'AC-3', 'E-AC-3', 'MP3', 'Opus', 'FLAC', 'Vorbis'],
        'mp4':  ['Copy', 'AAC', 'AC-3', 'E-AC-3', 'MP3', 'Opus', 'FLAC'],
        'mov':  ['Copy', 'AAC', 'AC-3', 'MP3'],
        'webm': ['Opus', 'Vorbis'],
    }

    # Display-Label -> FFmpeg-Encoder-Name (fuer -c:a). 'Copy' bleibt special.
    AUDIO_CODEC_FFMPEG_MAP = {
        'AAC':    'aac',
        'AC-3':   'ac3',
        'E-AC-3': 'eac3',
        'MP3':    'libmp3lame',
        'Opus':   'libopus',
        'FLAC':   'flac',
        'Vorbis': 'libvorbis',
    }

    # Container-Display-Label
    CONTAINER_LABELS = {
        'mkv':  'MKV',
        'mp4':  'MP4',
        'mov':  'MOV',
        'webm': 'WebM',
    }

    def _get_target_codec_family(self) -> str:
        """
        Liefert die Codec-Familie ('h264', 'h265', 'av1') des aktuell
        gewaehlten Target-Codecs - egal ob NVENC oder Software-Encoder.
        """
        codec = self.video_codec_combo.currentData() or ''
        if 'av1' in codec:
            return 'av1'
        if 'hevc' in codec or 'x265' in codec or '265' in codec:
            return 'h265'
        if '264' in codec:
            return 'h264'
        return 'h264'  # safe default

    def _get_resolution_class(self) -> str:
        """
        '4k' fuer >=2160 Hoehe oder >=3840 Breite, sonst '1080p'.
        Wird fuer die Quality-Floor-Tabelle benutzt.
        """
        if self.input_height >= 2160 or self.input_width >= 3840:
            return '4k'
        return '1080p'

    def _calculate_recommended_bitrate(self) -> float:
        """
        Berechnet die empfohlene Target-Bitrate (Mbps) aus:
          - Source-Codec (self.input_video_codec)
          - Source-Bitrate (self.input_source_bitrate)
          - Target-Codec (aus video_codec_combo)
          - Source-Resolution (fuer Quality-Floor)

        Returns 0.0 wenn keine Source-Info verfuegbar (-> Caller faellt
        auf 50%-Pauschale zurueck).
        """
        if self.input_source_bitrate <= 0:
            return 0.0

        target = self._get_target_codec_family()
        source = self.input_video_codec  # h264/h265/av1/unknown

        # Multiplier waehlen
        if source == 'unknown':
            multiplier = self.UNKNOWN_SOURCE_MULTIPLIER
        else:
            multiplier = self.BITRATE_MULTIPLIERS.get(
                (source, target), self.UNKNOWN_SOURCE_MULTIPLIER
            )

        recommended = self.input_source_bitrate * multiplier

        # Quality-Floor anwenden, aber nicht ueber Source-Bitrate gehen
        floor = self.QUALITY_FLOOR_MBPS.get(
            (self._get_resolution_class(), target), 0.0
        )
        if recommended < floor:
            recommended = min(floor, self.input_source_bitrate)

        return recommended

    def _format_bitrate_label(self, mbps: float) -> str:
        """
        Formatiert das Slider-Wert-Label.
        Mit Source-Bitrate: '5.9M (47%)'
        Ohne Source-Bitrate: '5.9M'
        """
        base = f"{mbps:.1f}M"
        if self.input_source_bitrate > 0:
            pct = round(mbps / self.input_source_bitrate * 100)
            return f"{base} ({pct}%)"
        return base

    def _reset_source_bitrate_state(self):
        """
        Setzt Source-Codec und Source-Bitrate zurueck (kein Video geladen).
        Slider-Max wird auf den Default 200 (= 20.0 Mbps) zurueckgesetzt.
        Slider-Wert-Label wird ohne Prozent-Anzeige aktualisiert.
        """
        self.input_video_codec = 'unknown'
        self.input_source_bitrate = 0.0
        self.bitrate_slider.setMaximum(200)
        # Label ohne Prozent-Anzeige (kein Source-Reference-Punkt)
        self.bitrate_value_label.setText(
            self._format_bitrate_label(self.bitrate_slider.value() * 0.1)
        )

    def _on_codec_changed_recompute_bitrate(self):
        """
        Codec-Combo-Wechsel-Handler.

        Re-berechnet die Recommended Bitrate, wenn der User den Target-Codec
        wechselt (z.B. AV1 -> H.265). Voraussetzung: ein Video ist geladen
        UND der User hat den Slider noch nicht manuell bewegt.

        Sobald der User den Slider angefasst hat (_user_modified_bitrate),
        wird die Auto-Logik unterdrueckt - manuelle Eingaben sind King.
        """
        # Kein Video geladen oder User hat manuell gesetzt -> nichts tun
        if self.input_source_bitrate <= 0:
            return
        if self._user_modified_bitrate:
            return

        target_bitrate = self._calculate_recommended_bitrate()
        if target_bitrate <= 0:
            return

        # Clamp auf Slider-Range (0.1 Mbps bis Source-Bitrate)
        target_bitrate = max(0.1, min(self.input_source_bitrate, target_bitrate))
        slider_value = int(target_bitrate * 10)
        self.bitrate_slider.setValue(slider_value)

        pct = round(target_bitrate / self.input_source_bitrate * 100)
        src_disp = self.input_video_codec.upper() if self.input_video_codec != 'unknown' else 'unknown'
        tgt_disp = self._get_target_codec_family().upper()
        print(f"🎯 Recommended bitrate recalculated ({src_disp}→{tgt_disp}): "
              f"{target_bitrate:.2f} Mbps ({pct}%)")

    def _is_worker_running(self) -> bool:
        """Thread-safe check ob Worker läuft"""
        with QMutexLocker(self.worker_mutex):
            return self.worker is not None and self.worker.isRunning()

    def _load_video_info_async(self, filepath: str, done_callback=None):
        """Lädt Video-Informationen asynchron.

        optionaler done_callback wird zusaetzlich an das Worker-
        Signal gehaengt. Genutzt vom RemuxDialog, der nach Abschluss des
        Probes seine eigene UI aktualisieren muss.
        """
        if not filepath or not os.path.isfile(filepath):
            return

        # (Option A): Bei jedem neuen File-Load wird das
        # _user_modified_bitrate-Flag zurueckgesetzt, damit jedes File seine
        # eigene Recommended Bitrate als Startpunkt bekommt. Verhindert das
        # alte Verhalten, dass ein einmal manuell gesetzter Slider-Wert
        # ueber alle nachfolgenden Files hinweg "klebt" - was mit dem v2.5
        # Slider-Cap auf Source-Bitrate zu stillschweigendem Capping fuehrte.
        #
        # bleibt erhalten: das Flag wird HIER (vor ffprobe-Start)
        # zurueckgesetzt, NICHT in _on_video_info_loaded. Schiebt der User
        # den Slider waehrend ffprobe noch laeuft, setzt actionTriggered das
        # Flag wieder auf True - und das ffprobe-Result respektiert das.
        #
        # Im Batch-Modus aendert dieser Reset nichts: dort greift Auto-Set
        # ohnehin nur beim ersten File (siehe should_auto_set-Bedingung).
        self._user_modified_bitrate = False

        self.actual_bitrate_value_label.setText("...")

        worker = VideoInfoWorker(filepath)
        worker.signals.finished.connect(self._on_video_info_loaded)

        if done_callback is not None:
            worker.signals.finished.connect(done_callback)

        self.thread_pool.start(worker)

    def _on_video_info_loaded(self, info: VideoInfo):
        """Callback wenn Video-Info geladen wurde"""
        if info.error:
            print(f"⚠️ Error loading video info: {info.error}")
            self.actual_bitrate_value_label.setText("N/A")
            self._reset_source_bitrate_state()
            self._clear_atmos_state()
            return
        
        if not info.is_valid:
            self.actual_bitrate_value_label.setText("N/A")
            self._reset_source_bitrate_state()
            self._clear_atmos_state()
            return
        
        self.total_duration = info.duration
        self.input_video_fps = info.fps
        self.audio_streams = info.audio_streams
        self.subtitle_streams = info.subtitle_streams
        
        # Resolution speichern
        self.input_width = info.width
        self.input_height = info.height
        

        self.input_pix_fmt = info.pix_fmt
        
        # Problematische Pixel-Formate die zu schwarzem Bildschirm führen können
        PROBLEMATIC_PIX_FMTS = [
            'yuv420p10le',   # 10-bit
            'yuv420p12le',   # 12-bit
            'yuv422p',       # 4:2:2
            'yuv422p10le',   # 4:2:2 10-bit
            'yuv444p',       # 4:4:4
            'yuv444p10le',   # 4:4:4 10-bit
        ]
        
        # Auto-Enable Mediathek-Safe bei problematischen Formaten
        if self.input_pix_fmt in PROBLEMATIC_PIX_FMTS:
            self.mediathek_safe_checkbox.setChecked(True)
            print(f"⚠️ Problematic pixel format detected: {self.input_pix_fmt} - Auto-enabled Mediathek-Safe mode")
        
        # Dolby Atmos Detection & UI Lock
        self.has_atmos_detected = info.has_atmos
        self.atmos_stream_indices = info.atmos_stream_indices
        
        if self.has_atmos_detected:
            self._activate_atmos_protection()
        else:
            self._clear_atmos_state()
        
        filepath = self.input_line.text().strip()
        if os.path.isfile(filepath) and info.duration > 0:
            file_size_bits = os.path.getsize(filepath) * 8
            bitrate_val = file_size_bits / (info.duration * 1_000_000)
            self.actual_bitrate_value_label.setText(f"{bitrate_val:.2f}")


            self.input_video_codec = info.video_codec
            self.input_source_bitrate = bitrate_val


            # (hoeher als Source ist Speicherverschwendung ohne Quality-Gewinn).
            # Aufrunden auf naechste 0.1, mindestens 2 (= 0.2 Mbps).
            #

            # der Slider auf Range=0 und Qt rendert das Handle nicht. Mit Floor=2
            # bleibt mindestens ein Schritt Spielraum, damit der Slider sichtbar
            # ist. Der Cap bleibt bei sehr niedrigen Bitraten dicht an der Source.
            slider_max = max(2, int(bitrate_val * 10 + 0.5))
            self.bitrate_slider.setMaximum(slider_max)


            # Faellt auf 50% Pauschale zurueck wenn Source-Codec 'unknown'.
            # Im Batch-Modus nur beim ersten File, im Single-Modus bei jedem File

            should_auto_set = (
                (not self.batch_mode or self.batch_index == 0)
                and not self._user_modified_bitrate
            )

            if should_auto_set:
                target_bitrate = self._calculate_recommended_bitrate()
                if target_bitrate <= 0:  # Fallback (sollte nicht eintreten, da bitrate_val > 0)
                    target_bitrate = bitrate_val * 0.5
                # Clamp auf Slider-Range (0.1 Mbps bis Source-Bitrate)
                target_bitrate = max(0.1, min(bitrate_val, target_bitrate))
                slider_value = int(target_bitrate * 10)
                self.bitrate_slider.setValue(slider_value)
                pct = round(target_bitrate / bitrate_val * 100)
                src_codec_disp = self.input_video_codec.upper() if self.input_video_codec != 'unknown' else 'unknown'
                tgt_codec_disp = self._get_target_codec_family().upper()
                print(f"🎯 Recommended bitrate ({src_codec_disp}→{tgt_codec_disp}): "
                      f"{bitrate_val:.2f} → {target_bitrate:.2f} Mbps ({pct}%)")
            elif self._user_modified_bitrate:
                current_mbps = self.bitrate_slider.value() * 0.1
                print(f"ℹ️ User-defined bitrate respected: {current_mbps:.1f} Mbps (Auto-Bitrate suppressed)")


            self.bitrate_value_label.setText(
                self._format_bitrate_label(self.bitrate_slider.value() * 0.1)
            )
        else:

            # nicht (mehr) existiert oder duration==0 ist (kann durch Timing-
            # Effekte zwischen async ffprobe-Worker und UI-Thread im Batch
            # passieren), den Slider auf einen sauberen Default zuruecksetzen
            # statt im Stale-Zustand der vorherigen Datei zu belassen.
            # Ohne diesen Reset blieb der Slider z.B. bei Min==Max==1 stehen
            # ("0.1M (1%)" und kein Handle sichtbar) und konnte vom User nicht
            # mehr feinjustiert werden.
            self.actual_bitrate_value_label.setText("N/A")
            self._reset_source_bitrate_state()
        
        self.update_subtitle_combo()
        self.refresh_audio_track_checkboxes()
        self.update_output_filename()
        self.update_command_preview()
    
    def _activate_atmos_protection(self):
        """Aktiviert Dolby Atmos Schutz-Modus"""
        print(f"🎬 Dolby Atmos Protection ACTIVE - Streams: {self.atmos_stream_indices}")
        
        # Zeige rotes ATMOS Label
        self.atmos_label.setText("DOLBY ATMOS")
        self.atmos_label.setVisible(True)
        self.atmos_label.setToolTip(
            "Dolby Atmos detected!\n\n"
            "Audio codec has been locked to 'Copy' mode to preserve\n"
            "the Atmos metadata and ensure 100% compatibility.\n\n"
            "All audio tracks will be copied without re-encoding."
        )
        
        # Erzwinge Audio Codec auf "Copy"
        self.audio_codec_combo.setCurrentIndex(0)  # Copy
        self.audio_codec_combo.setEnabled(False)
        self.audio_codec_combo.setToolTip("Locked to 'Copy' due to Dolby Atmos detection")
        
        # Disable Audio Bitrate (nicht relevant bei Copy)
        self.audio_bitrate_line.setEnabled(False)
        
        # Alle Audio-Tracks werden automatisch aktiviert
        # (wird in refresh_audio_track_checkboxes() behandelt)
    
    def _clear_atmos_state(self):
        """Deaktiviert Atmos-Schutz und entsperrt UI"""
        self.has_atmos_detected = False
        self.atmos_stream_indices = []
        
        # Verstecke ATMOS Label
        self.atmos_label.setVisible(False)
        self.atmos_label.setToolTip("")
        
        # Re-enable Audio Codec Auswahl
        if self.run_btn.isEnabled():  # Nur wenn nicht gerade encoding läuft
            self.audio_codec_combo.setEnabled(True)
            self.audio_codec_combo.setToolTip("")
            self.toggle_audio_bitrate_field()
    
    def _clear_video_info_cache(self):
        """Löscht alle Video-Info-Caches (für Drag & Drop)"""
        self.total_duration = 0.0
        self.fps = 0.0
        self.input_width = 0
        self.input_height = 0
        self.input_pix_fmt = 'yuv420p'
        self.audio_streams = []
        self.subtitle_streams = []
        self.input_video_fps = 0.0
        
        # UI zurücksetzen
        self.actual_bitrate_value_label.setText("N/A")
        self._reset_source_bitrate_state()
        

        # sonst bleiben Checkboxen und Subtitle-Combo der vorherigen Datei sichtbar.
        self.refresh_audio_track_checkboxes()
        self.update_subtitle_combo()
        
        # Atmos State clearen
        self._clear_atmos_state()

    def _measure_standard_button_height(self) -> int:
        """
        Liefert die natuerliche Hoehe eines Standard-QPushButton in
        der aktuellen Style/Font/DPI-Umgebung.
        
        Wird verwendet, um die Zeilenhoehe der File-Paths-Zone (Browse-Buttons +
        QLineEdits) auf das gleiche Mass wie About/Default/Remux zu bringen.
        Diese drei haben keine fixe Hoehe, uebernehmen also die sizeHint() aus
        Style und Font - die wir hier an einem Prototyp messen, ohne eine
        Magic-Number zu verwenden. Dadurch passt sich die File-Paths-Zone
        automatisch an System-DPI und Theme-Style an.
        
        Hintergrund zur Wahl von setFixedHeight statt setMinimumHeight beim
        Aufrufer: bei knapp bemessenen Layouts kann Qt einzelne Widgets unter
        ihre minimumHeight stauchen. setFixedHeight verhindert das.
        """
        proto = QPushButton("About")
        height = proto.sizeHint().height()
        proto.deleteLater()
        return height

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)


        # auf About/Default/Remux. Details siehe _measure_standard_button_height.
        _row_h = self._measure_standard_button_height()

        file_paths_group = QGroupBox("File Paths")
        file_paths_layout = QVBoxLayout(file_paths_group)

        # Default-Spacing (oft 6 px) liess die Zeilen optisch aneinanderkleben,
        # besonders weil die LineEdits ihre kompakte natuerliche Hoehe haben.
        # 10 px statt 8: mit 8 wurde der untere GroupBox-Rahmen leicht abgeschnitten,
        # weil die GroupBox-Hoehe knapp bemessen war.
        file_paths_layout.setSpacing(10)
        folder_layout = QHBoxLayout()
        folder_layout.addWidget(QLabel("Batch Folder:"))
        self.folder_line = QLineEdit()
        self.folder_line.setPlaceholderText("Optional batch folder")
        self.folder_line.setFixedHeight(_row_h)
        self.folder_line.textChanged.connect(self._on_batch_folder_changed)
        browse_folder_btn = QPushButton("Browse...")
        browse_folder_btn.setFixedHeight(_row_h)
        browse_folder_btn.clicked.connect(self.browse_folder)
        folder_layout.addWidget(self.folder_line)
        folder_layout.addWidget(browse_folder_btn)
        file_paths_layout.addLayout(folder_layout)
        output_folder_layout = QHBoxLayout()
        output_folder_layout.addWidget(QLabel("Output Folder:"))
        self.output_folder_line = QLineEdit()
        self.output_folder_line.setPlaceholderText("Optional output folder")
        self.output_folder_line.setFixedHeight(_row_h)
        self.output_folder_line.textChanged.connect(self.update_output_filename)
        self.output_folder_line.textChanged.connect(self._on_batch_folder_changed)
        browse_output_folder_btn = QPushButton("Browse...")
        browse_output_folder_btn.setFixedHeight(_row_h)
        browse_output_folder_btn.clicked.connect(self.browse_output_folder)
        output_folder_layout.addWidget(self.output_folder_line)
        output_folder_layout.addWidget(browse_output_folder_btn)
        file_paths_layout.addLayout(output_folder_layout)
        in_layout = QHBoxLayout()
        in_layout.addWidget(QLabel("Input File:"))
        self.input_line = QLineEdit()
        self.input_line.setFixedHeight(_row_h)
        browse_in_btn = QPushButton("Browse...")
        browse_in_btn.setFixedHeight(_row_h)
        browse_in_btn.clicked.connect(self.browse_input)
        self.input_line.textChanged.connect(lambda text: self.on_input_change(text))
        in_layout.addWidget(self.input_line)
        in_layout.addWidget(browse_in_btn)
        file_paths_layout.addLayout(in_layout)
        out_layout = QHBoxLayout()
        out_layout.addWidget(QLabel("Output File:"))
        self.output_line = QLineEdit()
        self.output_line.setFixedHeight(_row_h)
        browse_out_btn = QPushButton("Browse...")
        browse_out_btn.setFixedHeight(_row_h)
        browse_out_btn.clicked.connect(self.browse_output)
        self.output_line.textChanged.connect(self.update_command_preview)
        out_layout.addWidget(self.output_line)
        out_layout.addWidget(browse_out_btn)
        file_paths_layout.addLayout(out_layout)
        main_layout.addWidget(file_paths_group)

        settings_group = QGroupBox("Settings")

        # Innen: Grid (oben) + Codec-Zeile (unten, volle Breite ohne Spalten-Beschraenkung).
        settings_outer_layout = QVBoxLayout(settings_group)
        settings_outer_layout.setContentsMargins(9, 9, 9, 9)
        settings_outer_layout.setSpacing(8)

        settings_grid_widget = QWidget()
        settings_layout = QGridLayout(settings_grid_widget)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.addLayout(self._create_actual_bitrate_layout(), 0, 0)
        settings_layout.addLayout(self._create_bitrate_slider_layout(), 1, 0)
        settings_layout.addLayout(self._create_audio_codec_layout(), 0, 1)
        settings_layout.addLayout(self._create_audio_bitrate_layout(), 1, 1)

        # Audio Tracks: GroupBox in Spalte 2 (rechts neben Audio Codec/Bitrate),
        # ueberspannt beide Zeilen. Tracks werden vertikal in einer ScrollArea
        # gestapelt; bei mehr als 2 Tracks erscheint der Scrollbar. Damit ist
        # die GroupBox-Hoehe konstant (= Hoehe von 2 Grid-Zeilen) und das
        # Settings-Layout zerbricht nicht bei vielen Audio-Spuren.
        audio_tracks_group = QGroupBox("Audio Tracks")
        # Mindesthoehe explizit auf 2 Track-Zeilen einstellen. Ohne diese
        # Vorgabe nimmt die GroupBox sich nur die Hoehe der beiden Grid-Zeilen
        # daneben (Audio Codec + Audio Bitrate kbps), was knapp 1 Checkbox-
        # Zeile ergab - der zweite Track verschwand hinter dem ScrollBar-Cap.
        # 70 px = GroupBox-Header (~16) + Padding (~6) + 2 * Checkbox (~22) + Reserve.
        audio_tracks_group.setMinimumHeight(70)
        _atg_layout = QVBoxLayout(audio_tracks_group)
        _atg_layout.setContentsMargins(1, 1, 1, 1)
        _atg_layout.setSpacing(0)

        self.audio_tracks_scroll = QScrollArea()
        self.audio_tracks_scroll.setWidgetResizable(True)
        self.audio_tracks_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.audio_tracks_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        _scroll_inner = QWidget()
        self.audio_tracks_layout = QVBoxLayout(_scroll_inner)
        self.audio_tracks_layout.setContentsMargins(0, 0, 0, 0)
        self.audio_tracks_layout.setSpacing(2)
        self.audio_track_checkboxes = []
        self.audio_tracks_layout.addStretch()
        self.audio_tracks_scroll.setWidget(_scroll_inner)

        _atg_layout.addWidget(self.audio_tracks_scroll)
        # GroupBox spannt Spalte 2, beide Grid-Zeilen
        settings_layout.addWidget(audio_tracks_group, 0, 2, 2, 1)

        # Spaltenstretch: Slider darf wachsen (2x), Audio-Codec-Spalte schmal
        # halten (1x), Audio-Tracks-Spalte (2x) damit Track-Labels Platz haben.
        settings_layout.setColumnStretch(0, 2)
        settings_layout.setColumnStretch(1, 1)
        settings_layout.setColumnStretch(2, 2)
        settings_outer_layout.addWidget(settings_grid_widget)


        # liegt jetzt ALS EIGENE ZEILE unter dem Grid, mit voller Settings-GroupBox-Breite.
        # Damit ist sie unabhaengig von der 2:1:2-Spaltenaufteilung und hat ~1240px Platz
        # statt nur 491px. Subtitles bleibt im Grid in Zeile 2 col 2 unangetastet.
        settings_outer_layout.addLayout(self._create_video_codec_and_resolution_layout())

        main_layout.addWidget(settings_group)
        
        advanced_group = QGroupBox("Advanced Settings")
        advanced_main_layout = QHBoxLayout(advanced_group)
        settings_v_layout = QVBoxLayout()
        settings_v_layout.addLayout(self._create_gpu_selection_layout())
        checkbox_grid_layout = QGridLayout()
        checkbox_grid_layout.addLayout(self._create_preset_layout(), 0, 0)
        checkbox_grid_layout.addLayout(self._create_tune_layout(), 1, 0)
        checkbox_grid_layout.addLayout(self._create_multipass_layout(), 0, 1)
        checkbox_grid_layout.addLayout(self._create_lookahead_layout(), 1, 1)
        settings_v_layout.addLayout(checkbox_grid_layout)
        settings_v_layout.addStretch(1)
        advanced_main_layout.addLayout(settings_v_layout, 1)
        buttons_v_layout = QVBoxLayout()
        about_layout = QHBoxLayout()
        
        about_layout.addStretch()


        # Quadratischer Button; Breite DPI-aware aus FontMetrics statt 32 px hart.
        self.theme_btn = QPushButton()
        self.theme_btn.setCheckable(False)
        self.theme_btn.setToolTip("Toggle Light / Dark mode")
        _btn_w = self.fontMetrics().horizontalAdvance("MM") + 16  # ~2 'M' + Padding
        self.theme_btn.setFixedWidth(_btn_w)
        self.theme_btn.clicked.connect(self._toggle_theme)
        about_layout.addWidget(self.theme_btn)
        # Initial-Beschriftung (Icon) wird nach load_settings() in _refresh_theme_btn gesetzt
        self._refresh_theme_btn()


        self.default_btn = QPushButton("Default")
        self.default_btn.setToolTip("Reset all settings to defaults")
        self.default_btn.clicked.connect(self.reset_to_defaults)
        about_layout.addWidget(self.default_btn)


        # Streams, Doku. Komplett separater Workflow zum normalen Encoding.
        self.remux_btn = QPushButton("Remux")
        self.remux_btn.setToolTip(
            "MKV → MP4 container conversion (no re-encoding).\n"
            "Preserves Dolby Vision metadata.\n"
            "Opens a dedicated dialog with stream selection and workflow diagram."
        )
        self.remux_btn.clicked.connect(self.show_remux_dialog)
        about_layout.addWidget(self.remux_btn)

        self.about_btn = QPushButton("About")
        self.about_btn.clicked.connect(self.show_about_dialog)
        about_layout.addWidget(self.about_btn)

        buttons_v_layout.addLayout(about_layout)
        buttons_v_layout.addStretch(1)
        main_controls_layout = QHBoxLayout()
        self.store_btn = QPushButton("Save Settings")
        self.run_btn = QPushButton("Run")
        

        # Theme-aware und Zustands-aware ist (idle vs. running). Das verhindert
        # den 2-Klick-Bug beim Theme-Wechsel: der Run-Button hat ein eigenes
        # Stylesheet mit fixen Hex-Farben, das vom QPalette-Wechsel nicht
        # angefasst wird - dadurch passten Stylesheet und Theme-Farben nach
        # einem Toggle nicht zueinander, erst beim zweiten Toggle wurde es
        # konsistent. Jetzt wird das Stylesheet bei jedem Theme-Wechsel
        # zusammen mit der Palette aktualisiert.
        self._run_btn_state = "idle"
        self._apply_run_button_style()
        
        self.pause_btn = QPushButton("Pause")
        self.cancel_btn = QPushButton("Cancel")
        self.pause_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.store_btn.clicked.connect(self.save_settings)
        self.run_btn.clicked.connect(self.run_ffmpeg)
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.cancel_btn.clicked.connect(self.cancel_encoding)
        main_controls_layout.addWidget(self.store_btn)
        main_controls_layout.addWidget(self.run_btn)
        main_controls_layout.addWidget(self.pause_btn)
        main_controls_layout.addWidget(self.cancel_btn)
        buttons_v_layout.addLayout(main_controls_layout)
        advanced_main_layout.addLayout(buttons_v_layout, 1)
        main_layout.addWidget(advanced_group)
        
        cmd_group = QGroupBox("Command Preview")
        cmd_layout = QVBoxLayout(cmd_group)
        self.cmd_preview_label = QLabel()
        self.cmd_preview_label.setWordWrap(True)
        self.cmd_preview_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # Label oben ausrichten und vertikal wachsen lassen, damit das Inhaltsfeld
        # sichtbar mit der GroupBox waechst (statt nur die Hoehe einer Textzeile
        # zu nehmen und den Rest als leeren Platz darunter zu lassen).
        self.cmd_preview_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.cmd_preview_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        cmd_layout.addWidget(self.cmd_preview_label)
        # Stretch=1: cmd_group nimmt den verbleibenden vertikalen Platz im
        # main_layout, statt dass der durch ein nachgelagertes addStretch()
        # verschwendet wird.
        main_layout.addWidget(cmd_group, 1)
        
        progress_info_layout = QVBoxLayout()
        self.current_file_label = QLabel("")
        progress_info_layout.addWidget(self.current_file_label)
        progress_display_layout = QHBoxLayout()
        self.progress_label = QLabel("Progress: 00:00 / 00:00")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        self.remaining_label = QLabel("Remaining: --:--:--")
        self.speed_label = QLabel("Speed: 0.00x | 0.00 fps")
        self.predicted_size_label = QLabel("Predicted Size: N/A")
        progress_display_layout.addWidget(self.progress_label)
        progress_display_layout.addWidget(self.progress_bar, 1)
        progress_display_layout.addWidget(self.remaining_label)
        progress_display_layout.addWidget(self.speed_label)
        progress_display_layout.addWidget(self.predicted_size_label)
        progress_info_layout.addLayout(progress_display_layout)
        main_layout.addLayout(progress_info_layout)

        self.toggle_audio_bitrate_field()
        self.update_command_preview()

    def _create_actual_bitrate_layout(self):
        actual_layout = QHBoxLayout()
        actual_layout.addWidget(QLabel("Actual Bitrate (Mbit/s):"))
        self.actual_bitrate_value_label = QLabel("0.00")
        actual_layout.addWidget(self.actual_bitrate_value_label)
        actual_layout.addStretch()
        return actual_layout

    def _create_bitrate_slider_layout(self):
        bitrate_layout = QHBoxLayout()

        # Codec-aware Empfehlung (siehe BITRATE_MULTIPLIERS), nicht mehr stumpf 50%.
        bitrate_label = QLabel("Recommended Bitrate (Mbit/s):")
        bitrate_label.setToolTip(
            "Recommended Bitrate\n"
            "\n"
            "Auto-set based on source codec, target codec and source bitrate.\n"
            "Examples:\n"
            "  H.264 → AV1   ≈ 30% of source\n"
            "  H.265 → AV1   ≈ 60% of source\n"
            "  H.265 → H.265 ≈ 70% of source\n"
            "  AV1   → AV1   ≈ 80% of source\n"
            "\n"
            "Quality floors per resolution prevent excessively low bitrates.\n"
            "Slider maximum is capped at the source bitrate (higher would\n"
            "waste storage without quality gain).\n"
            "\n"
            "Manual changes are preserved - if you move the slider, the\n"
            "automatic recommendation will not overwrite it for this file."
        )
        bitrate_layout.addWidget(bitrate_label)
        self.bitrate_slider = QSlider(Qt.Orientation.Horizontal)
        self.bitrate_slider.setMinimum(1)

        # dynamisch auf die Source-Bitrate gesetzt (siehe _on_video_info_loaded).
        self.bitrate_slider.setMaximum(200)
        self.bitrate_slider.setValue(40)
        self.bitrate_slider.valueChanged.connect(self.on_bitrate_slider_change)

        # NICHT bei programmatischem setValue(). Damit erkennen wir bewusste Eingaben.
        self.bitrate_slider.actionTriggered.connect(self._on_bitrate_user_action)

        self.bitrate_value_label = QLabel(self._format_bitrate_label(self.bitrate_slider.value() * 0.1))
        bitrate_layout.addWidget(self.bitrate_slider)
        bitrate_layout.addWidget(self.bitrate_value_label)
        return bitrate_layout

    # ------------------------------------------------------------------
    # DPI-aware Combo Configuration
    # ------------------------------------------------------------------

    @staticmethod
    def _configure_combo_dpi_aware(
        combo,
        visible_chars: int,
        popup_extra_chars: int = 6,
    ) -> None:
        """
        Konfiguriert eine QComboBox so, dass sie unter Windows-Skalierungen
        (100 %, 125 %, 150 %, 200 %) sauber bleibt.

        Hintergrund:
        Frueher wurde fuer viele Combos setFixedWidth(<pixel>) gesetzt. Bei
        High-DPI skaliert Qt zwar Schriften und Abstaende mit, der Pixelwert
        bleibt aber starr - dadurch wird der Inhalt gequetscht und Nachbar-
        widgets (z. B. die Burn-in-Checkbox neben Subtitles) wandern in den
        Combo-Bereich hinein.

        Loesung:
        - SizeAdjustPolicy.AdjustToContents: Combo misst sich an ihren Items
          aus und skaliert mit der Schriftgroesse mit.
        - setMinimumContentsLength(visible_chars): Legt eine Mindestbreite
          *in Zeichen* fest. Skaliert automatisch DPI-aware ueber die
          Font-Metriken.
        - SizePolicy Fixed/Fixed: Der Layout-Manager darf die Combo nicht
          horizontal stretchen, sodass Nachbarwidgets ihren Platz behalten.
        - setView()-Trick fuer das Popup: Damit lange Eintraege im geoeffneten
          Dropdown nicht abgeschnitten werden, geben wir der Item-View
          mehr Platz als die geschlossene Combo - wieder DPI-aware via
          QFontMetrics.

        Args:
            combo: Die zu konfigurierende QComboBox.
            visible_chars: Mindest-Zeichenbreite der geschlossenen Combo.
            popup_extra_chars: Zusaetzliche Zeichen, die das geoeffnete
                Dropdown-Popup gegenueber der geschlossenen Combo bekommt
                (fuer lange Eintraege wie Untertitel-Stream-Beschriftungen).
        """
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        combo.setMinimumContentsLength(visible_chars)
        combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        # Popup-Breite DPI-aware ueber QFontMetrics berechnen.
        # Qt 6: combo.view() liefert die QAbstractItemView des Dropdowns.
        # Wir nehmen die durchschnittliche Zeichenbreite ('x' als Referenz)
        # mal (visible_chars + popup_extra_chars) plus etwas Padding fuer
        # Scrollbar/Rahmen.
        try:
            fm = combo.fontMetrics()
            char_width = fm.horizontalAdvance("x")
            popup_width = char_width * (visible_chars + popup_extra_chars) + 40
            view = combo.view()
            if view is not None:
                view.setMinimumWidth(popup_width)
        except Exception:
            # Falls irgendein Style die Methoden nicht unterstuetzt: still
            # fallen lassen - die geschlossene Combo bleibt trotzdem korrekt.
            pass

    def _create_audio_codec_layout(self):
        layout = QHBoxLayout()
        layout.addSpacing(20)
        layout.addWidget(QLabel("Audio Codec:"))
        self.audio_codec_combo = QComboBox()
        # DPI-aware statt setFixedWidth(110). Laengster Eintrag z.B.
        # "E-AC-3 (eac3)" = 13 Zeichen; 14 Zeichen Mindestbreite passt.
        self._configure_combo_dpi_aware(
            self.audio_codec_combo, visible_chars=14, popup_extra_chars=4
        )

        # Bei Container-Wechsel filtert _refresh_audio_codec_combo automatisch.
        self.audio_codec_combo.addItems(self.CONTAINER_AUDIO_CODECS['mkv'])
        self.audio_codec_combo.currentIndexChanged.connect(self.toggle_audio_bitrate_field)

        self.audio_codec_combo.currentIndexChanged.connect(self.update_command_preview)
        layout.addWidget(self.audio_codec_combo)
        
        # Dolby Atmos Indikator
        self.atmos_label = QLabel("")
        self.atmos_label.setStyleSheet("""
            QLabel {
                color: #FF0000;
                font-weight: bold;
                font-size: 9pt;
                background-color: #2B2B2B;
                padding: 2px 6px;
                border-radius: 3px;
                border: 1px solid #FF0000;
            }
        """)
        self.atmos_label.setVisible(False)
        layout.addWidget(self.atmos_label)
        
        layout.addStretch()
        return layout

    def _create_audio_bitrate_layout(self):
        layout = QHBoxLayout()
        layout.addSpacing(20)
        layout.addWidget(QLabel("Bitrate (kbps):"))
        self.audio_bitrate_line = QLineEdit("128")
        self.audio_bitrate_line.setFixedWidth(50)
        self.audio_bitrate_line.textChanged.connect(self.update_command_preview)
        layout.addWidget(self.audio_bitrate_line)
        layout.addStretch()
        return layout
    
    def _create_video_codec_and_resolution_layout(self):
        """
        Eine sauber aufgebaute Zeile mit:
            Video Codec | Container | 4K→1080p | Mediathek-Safe | (Force AV1)

        - Codec zuerst (was wird codiert)
        - Container danach (wie wird's verpackt)
        - Output-Modifier rechts (Resolution + Mediathek-Compat)
        - 22px Trenner zwischen funktionalen Gruppen
        - Mindestbreiten an den Combos damit Labels voll lesbar bleiben
        """
        combined_layout = QHBoxLayout()

        # ---- Video Codec ----
        combined_layout.addWidget(QLabel("Video Codec:"))
        self.video_codec_combo = QComboBox()
        # DPI-aware statt setFixedWidth(130). Laengster Eintrag
        # "H.264 (h264_nvenc)" = 18 Zeichen; 18 Zeichen Mindestbreite reicht.
        self._configure_combo_dpi_aware(
            self.video_codec_combo, visible_chars=18, popup_extra_chars=4
        )
        self.video_codec_combo.currentIndexChanged.connect(self.update_output_filename)
        self.video_codec_combo.currentIndexChanged.connect(self.update_advanced_options_state)
        self.video_codec_combo.currentIndexChanged.connect(self._update_force_av1_visibility)

        self.video_codec_combo.currentIndexChanged.connect(self._on_codec_changed_recompute_bitrate)
        combined_layout.addWidget(self.video_codec_combo)

        # Trenner zur naechsten Gruppe
        combined_layout.addSpacing(22)

        # ---- Container ----
        combined_layout.addWidget(QLabel("Container:"))
        self.container_combo = QComboBox()
        # DPI-aware statt setFixedWidth(70). Eintraege "MKV"/"MP4"/"MOV"/
        # "WebM" sind max. 4 Zeichen; 5 Zeichen Mindestbreite passt mit Pfeil.
        self._configure_combo_dpi_aware(
            self.container_combo, visible_chars=5, popup_extra_chars=2
        )
        self.container_combo.setToolTip(
            "Output container format.\n"
            "\n"
            "MKV:  All codecs supported (default for max flexibility)\n"
            "MP4:  AV1/H.265/H.264 + AAC/AC-3/E-AC-3/MP3/Opus/FLAC\n"
            "MOV:  H.265/H.264 + AAC/AC-3/MP3 (Apple-friendly)\n"
            "WebM: VP9/AV1 + Opus/Vorbis (Web standard, codec-restricted)\n"
            "\n"
            "Changing the container will filter the codec lists. If the\n"
            "currently selected codec isn't supported, you'll be asked to\n"
            "auto-switch to a compatible one."
        )
        for key in ('mkv', 'mp4', 'mov', 'webm'):
            self.container_combo.addItem(self.CONTAINER_LABELS[key], key)
        self.container_combo.currentIndexChanged.connect(self._on_container_changed)
        combined_layout.addWidget(self.container_combo)

        # Trenner zur naechsten Gruppe
        combined_layout.addSpacing(22)

        # ---- 4K -> 1080p Checkbox ----
        # Label als Attribut speichern fuer dynamische Farb-Aenderung
        self.downscale_label = QLabel("4K→1080p:")
        combined_layout.addWidget(self.downscale_label)

        self.downscale_to_1080p_checkbox = QCheckBox()
        self.downscale_to_1080p_checkbox.setToolTip(
            "Automatically downscale to 1920x1080 while preserving aspect ratio.\n"
            "Uses Lanczos algorithm for best quality."
        )
        self.downscale_to_1080p_checkbox.stateChanged.connect(self.update_command_preview)
        self.downscale_to_1080p_checkbox.stateChanged.connect(self._on_downscale_checkbox_changed)
        combined_layout.addWidget(self.downscale_to_1080p_checkbox)

        # Trenner zur naechsten Gruppe
        combined_layout.addSpacing(22)

        # ---- Mediathek-Safe Checkbox ----
        mediathek_label = QLabel("Mediathek-Safe:")
        combined_layout.addWidget(mediathek_label)

        self.mediathek_safe_checkbox = QCheckBox()
        self.mediathek_safe_checkbox.setToolTip(
            "COMPREHENSIVE MEDIATHEK FIX - Resolves black screen issues:\n"
            "• Converts 10-bit/12-bit to 8-bit (yuv420p)\n"
            "• Forces constant frame rate (fps_mode cfr)\n"
            "• Fixes negative timestamps (avoid_negative_ts)\n"
            "• Regenerates timestamps (fflags +genpts)\n"
            "• NVENC: Profile/Level + strict GOP (max compatibility)\n"
            "\n"
            "Auto-enabled when problematic pixel format detected.\n"
            "Safe to use - tested with ARD/ZDF Mediathek content."
        )
        self.mediathek_safe_checkbox.stateChanged.connect(self.update_command_preview)
        self.mediathek_safe_checkbox.stateChanged.connect(self._update_force_av1_visibility)
        combined_layout.addWidget(self.mediathek_safe_checkbox)

        # ---- Force AV1 (conditional) ----
        # Erscheint nur wenn AV1 + Mediathek-Safe aktiv sind. Spacer ist auch
        # konditional sichtbar, sonst haengt 22px Loch nach Mediathek-Safe.
        # Spacer-Breite DPI-aware ueber FontMetrics statt 22 px hart.
        self.force_av1_spacer = QWidget()
        _spacer_w = self.fontMetrics().horizontalAdvance("xx")  # ~2 'x' Breiten
        self.force_av1_spacer.setFixedWidth(_spacer_w)
        self.force_av1_spacer.setVisible(False)
        combined_layout.addWidget(self.force_av1_spacer)

        self.force_av1_label = QLabel("Force AV1:")
        self.force_av1_label.setVisible(False)
        combined_layout.addWidget(self.force_av1_label)
        
        self.force_av1_checkbox = QCheckBox()
        self.force_av1_checkbox.setToolTip(
            "⚠️ EXPERIMENTAL - Force AV1 with compatibility fixes:\n"
            "• Adds color space metadata (bt709)\n"
            "• Sets color range and transfer characteristics\n"
            "• May still cause black screen issues\n"
            "\n"
            "Use at your own risk - H.265_NVENC recommended instead!"
        )
        self.force_av1_checkbox.setVisible(False)
        self.force_av1_checkbox.stateChanged.connect(self.update_command_preview)
        combined_layout.addWidget(self.force_av1_checkbox)
        
        # Input Resolution Info - nicht mehr angezeigt, aber behalten für Kompatibilität
        self.input_resolution_label = QLabel("")
        self.input_resolution_label.setVisible(False)  # Verstecken
        combined_layout.addWidget(self.input_resolution_label)


        # der Settings-Zeile - frueher waren sie in der Grid-Zelle (2,2), jetzt
        # rechts an der Codec-Zeile angehaengt fuer eine ruhigere Optik.
        combined_layout.addStretch()

        # ---- Subtitles + Burn-in ----
        combined_layout.addWidget(QLabel("Subtitles:"))
        self.subtitle_combo = QComboBox()
        # DPI-aware statt setFixedWidth(180). Mindestbreite 20 Zeichen
        # (passt typische Eintraege wie "Track 1 (eng, srt)"); langer Stream-
        # Name wird im Combo elidiert, im Dropdown bekommt das Popup mehr
        # Platz (popup_extra_chars=14) damit volle Stream-Beschriftungen
        # lesbar bleiben. Burn-in steht damit zuverlaessig direkt rechts
        # daneben - kein Ueberlappen mehr bei 125%/150% Skalierung.
        self._configure_combo_dpi_aware(
            self.subtitle_combo, visible_chars=20, popup_extra_chars=14
        )
        self.subtitle_combo.currentIndexChanged.connect(self.update_command_preview)
        combined_layout.addWidget(self.subtitle_combo)

        self.burn_in_checkbox = QCheckBox("Burn-in")
        self.burn_in_checkbox.stateChanged.connect(self.update_command_preview)
        combined_layout.addWidget(self.burn_in_checkbox)

        return combined_layout
    
    def _on_downscale_checkbox_changed(self, state):
        """Ändert Label-Farbe basierend auf Checkbox-Status"""
        if state == Qt.CheckState.Checked.value:
            self.downscale_label.setStyleSheet("color: #FF0000; font-weight: bold;")  # Rot & Fett
        else:
            # FIX: leerer Style => Label nimmt die Palette-Farbe (schwarz im Light-Mode,
            # hell im Dark-Mode). Frueher hartcodiert auf #000000 - im Dark-Mode unsichtbar.
            self.downscale_label.setStyleSheet("")
    
    def _update_force_av1_visibility(self):
        """
        Zeigt/versteckt Force AV1 Checkbox basierend auf:
        - AV1_NVENC ausgewählt UND
        - Mediathek-Safe aktiviert
        """
        video_codec = self.video_codec_combo.currentData()
        is_av1 = video_codec == 'av1_nvenc'
        mediathek_safe_active = self.mediathek_safe_checkbox.isChecked()
        
        should_show = is_av1 and mediathek_safe_active


        if hasattr(self, 'force_av1_spacer'):
            self.force_av1_spacer.setVisible(should_show)
        self.force_av1_label.setVisible(should_show)
        self.force_av1_checkbox.setVisible(should_show)
        
        # Wenn ausgeblendet, deaktivieren
        if not should_show:
            self.force_av1_checkbox.setChecked(False)
    
    def _update_mediathek_availability(self) -> None:
        """
        Deaktiviert Mediathek-Safe Checkbox wenn Bitrate > 8.0 Mbit.
        Mediathek-Safe ist nur für niedrige Bitraten sinnvoll (max 8 Mbit).
        """
        current_bitrate_mbits = self.bitrate_slider.value() * 0.1
        
        if current_bitrate_mbits > 8.0:
            # Bitrate zu hoch → Mediathek-Safe deaktivieren
            if self.mediathek_safe_checkbox.isChecked():
                self.mediathek_safe_checkbox.setChecked(False)
                print(f"⚠️ Mediathek-Safe auto-disabled (bitrate {current_bitrate_mbits:.1f} Mbit > 8.0 Mbit)")
            
            self.mediathek_safe_checkbox.setEnabled(False)
            self.mediathek_safe_checkbox.setToolTip(
                "⚠️ DISABLED - Bitrate too high for Mediathek-Safe mode\n"
                "Mediathek-Safe is designed for low bitrate encoding (≤ 8.0 Mbit/s).\n"
                "Reduce bitrate to enable this option."
            )
        else:
            # Bitrate OK → Mediathek-Safe verfügbar
            self.mediathek_safe_checkbox.setEnabled(True)
            self.mediathek_safe_checkbox.setToolTip(
                "COMPREHENSIVE MEDIATHEK FIX - Resolves black screen issues:\n"
                "• Converts 10-bit/12-bit to 8-bit (yuv420p)\n"
                "• Forces constant frame rate (fps_mode cfr)\n"
                "• Fixes negative timestamps (avoid_negative_ts)\n"
                "• Regenerates timestamps (fflags +genpts)\n"
                "• NVENC: Profile/Level + strict GOP (max compatibility)\n"
                "\n"
                "Auto-enabled when problematic pixel format detected.\n"
                "Safe to use - tested with ARD/ZDF Mediathek content."
            )

    def _create_video_codec_layout(self):
        """DEPRECATED - Use _create_video_codec_and_resolution_layout() instead"""
        layout = QHBoxLayout()
        layout.addWidget(QLabel("Video Codec:"))
        self.video_codec_combo = QComboBox()
        self.video_codec_combo.currentIndexChanged.connect(self.update_output_filename)
        self.video_codec_combo.currentIndexChanged.connect(self.update_advanced_options_state)
        layout.addWidget(self.video_codec_combo)
        layout.addStretch()
        return layout
    
    def _create_resolution_layout(self):
        """DEPRECATED - Use _create_video_codec_and_resolution_layout() instead"""
        layout = QHBoxLayout()
        
        # Label mit mehr Platz
        layout.addWidget(QLabel("Resolution:"))
        
        # Preset Dropdown - NUR Original + 1080p
        self.resolution_preset_combo = QComboBox()
        self.resolution_preset_combo.addItem("Original (no scaling)", "original")
        self.resolution_preset_combo.addItem("1920x1080 (Full HD)", "1080p")
        self.resolution_preset_combo.addItem("Custom...", "custom")
        self.resolution_preset_combo.setMinimumWidth(180)  # Mehr Platz für lesbare Darstellung
        self.resolution_preset_combo.currentIndexChanged.connect(self.on_resolution_preset_changed)
        layout.addWidget(self.resolution_preset_combo)
        
        # Width Field
        layout.addWidget(QLabel("W:"))
        self.resolution_width_line = QLineEdit()
        self.resolution_width_line.setFixedWidth(70)  # 60 → 70 für mehr Platz
        self.resolution_width_line.setPlaceholderText("1920")
        self.resolution_width_line.textChanged.connect(self.on_resolution_manual_change)
        layout.addWidget(self.resolution_width_line)
        
        # Height Field
        layout.addWidget(QLabel("H:"))
        self.resolution_height_line = QLineEdit()
        self.resolution_height_line.setFixedWidth(70)  # 60 → 70 für mehr Platz
        self.resolution_height_line.setPlaceholderText("1080")
        self.resolution_height_line.textChanged.connect(self.on_resolution_manual_change)
        layout.addWidget(self.resolution_height_line)
        
        # Keep Aspect Ratio Checkbox - VOLLSTÄNDIGER TEXT
        self.keep_aspect_ratio_checkbox = QCheckBox("Keep Aspect Ratio")
        self.keep_aspect_ratio_checkbox.setChecked(True)
        self.keep_aspect_ratio_checkbox.setToolTip("Height will be calculated automatically to maintain aspect ratio")
        self.keep_aspect_ratio_checkbox.stateChanged.connect(self.on_keep_aspect_ratio_changed)
        layout.addWidget(self.keep_aspect_ratio_checkbox)
        
        # Input Resolution Info
        self.input_resolution_label = QLabel("")
        self.input_resolution_label.setStyleSheet("color: #888888; font-size: 9pt;")
        layout.addWidget(self.input_resolution_label)
        
        # Scaling Algorithm - Fest auf Lanczos (bestes Quality/Speed Verhältnis)
        # Kein Dropdown mehr - wird intern immer "lanczos" verwendet
        self.scaling_algo_combo = QComboBox()
        self.scaling_algo_combo.addItem("Lanczos (Best Quality)", "lanczos")
        self.scaling_algo_combo.setCurrentIndex(0)
        self.scaling_algo_combo.setEnabled(False)  # Read-only
        self.scaling_algo_combo.setVisible(False)  # Komplett ausblenden - nicht mehr nötig
        self.scaling_algo_combo.currentIndexChanged.connect(self.update_command_preview)
        
        layout.addStretch()
        
        # Initially disable custom fields
        self.resolution_width_line.setEnabled(False)
        self.resolution_height_line.setEnabled(False)
        
        return layout

    def _create_gpu_selection_layout(self):
        gpu_layout = QHBoxLayout()
        gpu_layout.addWidget(QLabel("Encoder:"))
        self.gpu_combo = QComboBox()
        self.gpu_info_list = get_gpu_info()
        if self.gpu_info_list:
            for gpu in self.gpu_info_list:
                self.gpu_combo.addItem(gpu['name'], userData=gpu['index'])
        self.gpu_combo.addItem("CPU", userData=None)

        # FIX: Combo automatisch an laengsten Eintrag anpassen, damit Namen
        # wie 'NVIDIA GeForce RTX 5090' nicht vom Dropdown-Pfeil abgeschnitten
        # werden.
        # Auf gemeinsamen DPI-aware Helper umgestellt.
        # "NVIDIA GeForce RTX 5090" = 23 Zeichen; 24 als Mindestbreite.
        self._configure_combo_dpi_aware(
            self.gpu_combo, visible_chars=24, popup_extra_chars=4
        )

        self.gpu_combo.currentIndexChanged.connect(self.update_video_codec_options)
        self.gpu_combo.currentIndexChanged.connect(self.update_command_preview)
        gpu_layout.addWidget(self.gpu_combo)
        gpu_layout.addStretch()
        return gpu_layout

    def _create_subtitle_layout(self):
        """
        Subtitles + Burn-in sitzen jetzt direkt in der Codec-Zeile
        (siehe _create_video_codec_and_resolution_layout). Diese Methode wird
        nicht mehr aufgerufen und bleibt nur als Fallback erhalten falls sie
        woanders extern referenziert wird.
        """
        subtitle_layout = QHBoxLayout()
        subtitle_layout.addWidget(QLabel("Subtitles:"))
        self.subtitle_combo = QComboBox()

        # erlaubt aber Wachstum wenn Platz da ist (lange Sub-Stream-Labels)
        self.subtitle_combo.setMinimumWidth(220)
        self.subtitle_combo.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed
        )
        self.subtitle_combo.currentIndexChanged.connect(self.update_command_preview)
        self.burn_in_checkbox = QCheckBox("Burn-in")
        self.burn_in_checkbox.stateChanged.connect(self.update_command_preview)
        subtitle_layout.addWidget(self.subtitle_combo, 1)
        subtitle_layout.addWidget(self.burn_in_checkbox)
        return subtitle_layout

    def _create_preset_layout(self):
        preset_layout = QHBoxLayout()
        self.preset_checkbox = QCheckBox("High Quality preset (p7)")
        self.preset_checkbox.setChecked(True)
        self.preset_checkbox.stateChanged.connect(self.update_command_preview)
        preset_layout.addWidget(self.preset_checkbox)
        preset_layout.addStretch()
        return preset_layout

    def _create_tune_layout(self):
        tune_layout = QHBoxLayout()
        self.tune_checkbox = QCheckBox("Ultra High Quality tune (uhq)")
        self.tune_checkbox.setChecked(True)
        self.tune_checkbox.stateChanged.connect(self.update_command_preview)
        tune_layout.addWidget(self.tune_checkbox)
        tune_layout.addStretch()
        return tune_layout

    def _create_multipass_layout(self):
        multipass_layout = QHBoxLayout()
        self.multipass_checkbox = QCheckBox("Multipass Fullres")
        self.multipass_checkbox.stateChanged.connect(self.update_command_preview)
        multipass_layout.addWidget(self.multipass_checkbox)


        # Rechts neben Multipass Fullres. Springt nie automatisch.
        self.strict_vbv_checkbox = QCheckBox("Strict VBV (bitrate compliance)")
        self.strict_vbv_checkbox.setChecked(False)
        self.strict_vbv_checkbox.setToolTip(
            "Strict VBV (bitrate compliance)\n"
            "\n"
            "When ENABLED, the maximum bitrate is enforced strictly via VBV buffer:\n"
            "  maxrate = target bitrate (1x)\n"
            "  bufsize = 2 x target bitrate\n"
            "\n"
            "WARNING: If the bitrate slider is set too low for the source\n"
            "complexity, this will visibly REDUCE QUALITY in difficult scenes\n"
            "(motion, grain, fine detail). The encoder is forced to drop\n"
            "quality to stay within the cap, instead of briefly spending more\n"
            "bits in hard sections.\n"
            "\n"
            "When DISABLED (default):\n"
            "  maxrate = 2 x target bitrate (soft cap, Quality-First)\n"
            "  no bufsize set\n"
            "\n"
            "Use Strict VBV for: streaming targets, broadcast specs,\n"
            "true bitrate ceilings (e.g. Mediathek compliance).\n"
            "Leave OFF for: best-quality archival encodes where the slider\n"
            "is a guideline, not a hard limit."
        )
        self.strict_vbv_checkbox.stateChanged.connect(self.update_command_preview)
        multipass_layout.addWidget(self.strict_vbv_checkbox)

        multipass_layout.addStretch()
        return multipass_layout

    def _create_lookahead_layout(self):
        lookahead_layout = QHBoxLayout()
        lookahead_label = QLabel("Lookahead (frames):")
        self.rc_lookahead_checkbox = QCheckBox()
        self.rc_lookahead_checkbox.setChecked(True)
        self.rc_lookahead_checkbox.stateChanged.connect(self.update_advanced_options_state)
        self.rc_lookahead_line = QLineEdit("32")
        self.rc_lookahead_line.setFixedWidth(40)
        self.rc_lookahead_line.textChanged.connect(self.update_command_preview)
        lookahead_layout.addWidget(lookahead_label)
        lookahead_layout.addWidget(self.rc_lookahead_checkbox)
        lookahead_layout.addWidget(self.rc_lookahead_line)
        lookahead_layout.addStretch()
        return lookahead_layout

    def update_output_filename(self, *args):
        input_path = self.input_line.text()
        if not input_path or not os.path.isfile(input_path):
            return

        base_name_only = os.path.splitext(os.path.basename(input_path))[0]


        # Container kommt aus dem container_combo, nicht hardcoded ".mkv".
        selected_codec = self.video_codec_combo.currentData()
        codec_marker_map = {
            "av1_nvenc": ".AV1",
            "hevc_nvenc": ".H265",
            "h264_nvenc": ".H264",
            "libsvt-av1": ".AV1-CPU",
            "libx265": ".H265-CPU",
            "libx264": ".H264-CPU",
            "libvpx-vp9": ".VP9",
        }
        codec_marker = codec_marker_map.get(selected_codec, "")
        container = self.container_combo.currentData() if hasattr(self, 'container_combo') else 'mkv'
        output_suffix = f"{codec_marker}.{container}"

        new_output_filename = f"{base_name_only}{output_suffix}"


        # Sonst würden Files aus verschiedenen Subfolders im selben Output-Folder
        # landen und potenziell namentlich kollidieren.
        if self.batch_mode:
            input_dir = os.path.dirname(input_path)
            final_output_path = os.path.join(input_dir, new_output_filename)
        else:
            output_folder = self.output_folder_line.text().strip()
            if output_folder:
                final_output_path = os.path.join(output_folder, new_output_filename)
            else:
                input_dir = os.path.dirname(input_path)
                final_output_path = os.path.join(input_dir, new_output_filename)
        
        self.output_line.setText(final_output_path)

    def update_advanced_options_state(self):
        is_gpu = self.gpu_combo.currentData() is not None
        is_av1_nvenc = self.video_codec_combo.currentData() == 'av1_nvenc'

        self.preset_checkbox.setEnabled(is_gpu and is_av1_nvenc)
        self.tune_checkbox.setEnabled(is_gpu and is_av1_nvenc)
        self.multipass_checkbox.setEnabled(is_gpu and is_av1_nvenc)
        self.rc_lookahead_checkbox.setEnabled(is_gpu and is_av1_nvenc)
        self.rc_lookahead_line.setEnabled(is_gpu and is_av1_nvenc and self.rc_lookahead_checkbox.isChecked())

        self.update_command_preview()

    def update_video_codec_options(self):
        """
        Filtert das Video-Codec-Dropdown basierend auf
        Container-Wahl UND GPU-Verfuegbarkeit.

        - Wenn GPU verfuegbar: NVENC-Encoder werden bevorzugt sortiert
        - Wenn keine GPU: nur Software-Encoder, AV1-CPU disabled (zu langsam)
        - Container-Filter: nur Codecs die der gewaehlte Container muxen kann
        """
        is_gpu = self.gpu_combo.currentData() is not None

        # Container-Filter
        container = self.container_combo.currentData() if hasattr(self, 'container_combo') else 'mkv'
        allowed_codecs = self.CONTAINER_VIDEO_CODECS.get(container, self.CONTAINER_VIDEO_CODECS['mkv'])

        # Erlaubte Codecs nach GPU/CPU partitionieren
        gpu_codecs = ['av1_nvenc', 'hevc_nvenc', 'h264_nvenc']
        if is_gpu:
            # GPU first, dann Software
            ordered = [c for c in allowed_codecs if c in gpu_codecs]
            ordered += [c for c in allowed_codecs if c not in gpu_codecs]
        else:
            # Nur Software-Encoder anbieten
            ordered = [c for c in allowed_codecs if c not in gpu_codecs]

        self.video_codec_combo.blockSignals(True)
        self.video_codec_combo.clear()

        for codec_data in ordered:
            label = self.VIDEO_CODEC_LABELS.get(codec_data, codec_data)
            self.video_codec_combo.addItem(label, codec_data)

        # AV1-CPU im CPU-only-Pfad disablen (zu langsam fuer praktische Use Cases)
        if not is_gpu:
            model = self.video_codec_combo.model()
            for i in range(self.video_codec_combo.count()):
                if self.video_codec_combo.itemData(i) == 'libsvt-av1':
                    item = model.item(i)
                    if item:
                        item.setEnabled(False)
                    break
            # Default auf libx265 wenn vorhanden, sonst index 0 (libx264 oder VP9)
            for i in range(self.video_codec_combo.count()):
                if self.video_codec_combo.itemData(i) == 'libx265':
                    self.video_codec_combo.setCurrentIndex(i)
                    break
            else:
                if self.video_codec_combo.count() > 0:
                    self.video_codec_combo.setCurrentIndex(0)
        else:
            if self.video_codec_combo.count() > 0:
                self.video_codec_combo.setCurrentIndex(0)

        self.video_codec_combo.blockSignals(False)
        self.update_advanced_options_state()
        self.update_output_filename()

    def _on_container_changed(self):
        """
        Container-Wechsel-Handler.

        Prueft ob aktueller Video- und Audio-Codec mit dem neuen Container
        kompatibel sind. Falls nicht: Warning-Dialog mit Auto-Switch-Option.
        Bei Cancel: revert auf alten Container.
        """
        new_container = self.container_combo.currentData()
        if not new_container:
            return

        # Aktuelle Codec-Auswahl
        current_video = self.video_codec_combo.currentData()
        current_audio = self.audio_codec_combo.currentText() if hasattr(self, 'audio_codec_combo') else None

        allowed_video = self.CONTAINER_VIDEO_CODECS.get(new_container, [])
        allowed_audio = self.CONTAINER_AUDIO_CODECS.get(new_container, [])

        video_incompatible = current_video and current_video not in allowed_video
        audio_incompatible = current_audio and current_audio not in allowed_audio

        if video_incompatible or audio_incompatible:
            # Was ist nicht kompatibel?
            issues = []
            if video_incompatible:
                vlabel = self.VIDEO_CODEC_LABELS.get(current_video, current_video)
                # Suggested replacement: erster verfuegbarer Codec im neuen Container
                # (filtered by GPU availability)
                is_gpu = self.gpu_combo.currentData() is not None
                gpu_codecs = ['av1_nvenc', 'hevc_nvenc', 'h264_nvenc']
                if is_gpu:
                    suggested_v = next((c for c in allowed_video if c in gpu_codecs), allowed_video[0])
                else:
                    suggested_v = next((c for c in allowed_video if c not in gpu_codecs), allowed_video[0])
                suggested_v_label = self.VIDEO_CODEC_LABELS.get(suggested_v, suggested_v)
                issues.append(
                    f"<b>Video:</b> <code>{vlabel}</code> is not supported in "
                    f"<b>{new_container.upper()}</b><br>"
                    f"&nbsp;&nbsp;&nbsp;Suggested replacement: <b>{suggested_v_label}</b>"
                )
            if audio_incompatible:
                suggested_a = allowed_audio[0]
                issues.append(
                    f"<b>Audio:</b> <code>{current_audio}</code> is not supported in "
                    f"<b>{new_container.upper()}</b><br>"
                    f"&nbsp;&nbsp;&nbsp;Suggested replacement: <b>{suggested_a}</b>"
                )

            issue_html = "<br><br>".join(issues)

            reply = QMessageBox.warning(
                self,
                "Codec Incompatible With Container",
                f"<b>⚠ The current codec selection is not compatible with "
                f"the {new_container.upper()} container.</b><br><br>"
                f"{issue_html}<br><br>"
                "Choose an option:<br>"
                "• <b>Yes</b> — auto-switch to compatible codecs and proceed<br>"
                "• <b>No</b> — revert to the previous container",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )

            if reply != QMessageBox.StandardButton.Yes:
                # Revert: blockSignals, vorherigen Container wieder setzen
                self.container_combo.blockSignals(True)
                # Vorheriger Container ist aus den noch aktuellen Codec-Listen ableitbar:
                # finde einen Container in dem current_video und current_audio beide passen
                prev_container = 'mkv'  # safe fallback
                for cont_key, cont_video_list in self.CONTAINER_VIDEO_CODECS.items():
                    if (current_video in cont_video_list
                            and current_audio in self.CONTAINER_AUDIO_CODECS.get(cont_key, [])):
                        prev_container = cont_key
                        break
                for i in range(self.container_combo.count()):
                    if self.container_combo.itemData(i) == prev_container:
                        self.container_combo.setCurrentIndex(i)
                        break
                self.container_combo.blockSignals(False)
                return

        # Hier: Auto-Switch oder von Anfang an kompatibel.
        # Codec-Dropdowns neu befuellen (filtert nach Container).
        self.update_video_codec_options()
        self._refresh_audio_codec_combo()
        self.update_output_filename()
        self.update_command_preview()

    def _refresh_audio_codec_combo(self):
        """
        Befuellt das Audio-Codec-Dropdown basierend auf Container.

        Wenn der aktuell gewaehlte Audio-Codec nicht mehr verfuegbar ist,
        wird auf den ersten Eintrag der neuen Liste umgeschaltet.
        """
        if not hasattr(self, 'audio_codec_combo'):
            return
        container = self.container_combo.currentData() if hasattr(self, 'container_combo') else 'mkv'
        allowed = self.CONTAINER_AUDIO_CODECS.get(container, ['Copy'])

        previous = self.audio_codec_combo.currentText()
        self.audio_codec_combo.blockSignals(True)
        self.audio_codec_combo.clear()
        self.audio_codec_combo.addItems(allowed)

        # Vorherige Wahl wiederherstellen wenn moeglich, sonst erster Eintrag
        if previous in allowed:
            idx = allowed.index(previous)
            self.audio_codec_combo.setCurrentIndex(idx)
        else:
            self.audio_codec_combo.setCurrentIndex(0)
        self.audio_codec_combo.blockSignals(False)
        self.toggle_audio_bitrate_field()

    def on_input_change(self, filepath):
        """Modified: uses async loading"""
        self._load_video_info_async(filepath)
        self.update_command_preview()
    
    def on_resolution_preset_changed(self, index):
        """DEPRECATED - Not used anymore with simple checkbox approach"""
        pass
    
    def on_resolution_manual_change(self):
        """DEPRECATED - Not used anymore with simple checkbox approach"""
        pass
    
    def on_keep_aspect_ratio_changed(self, state):
        """DEPRECATED - Not used anymore with simple checkbox approach"""
        pass


    # ========================================================================

    # ========================================================================
    #
    # Use case: Many TVs and hardware players (LG OLED, some Samsungs, Apple TV)
    # only render Dolby Vision metadata when the video is in an MP4 container.
    # The actual codec and DV RPU layer stay identical - we just change the box.
    # See Remux_Documentation.md for the full workflow rationale.
    #
    # The remux UI lives entirely in the standalone RemuxDialog class (above).
    # The dialog builds its own ffmpeg command and triggers process_single_file
    # via cmd_override - the worker code is shared, but no encoding state from
    # the main window leaks into the remux command.

    def build_ffmpeg_command(self, input_path, output_path, log=False):
        """
        'log'-Parameter (default False) kontrolliert ob Status-
        Meldungen auf die Console gedruckt werden. update_command_preview
        ruft mit log=False (Preview-Update schweigt), process_single_file
        mit log=True (genau einmal pro Encoding-Run).

        Frueher: jeder Preview-Update produzierte einen weiteren Satz
        '📺 Mediathek-Safe: ...'-Zeilen, was bei jedem Settings-Toggle
        die Console zugespammt hat - und beim eigentlichen Start kamen
        die Zeilen nochmal doppelt durch.
        """
        if not input_path or not output_path:
            return None

        video_codec = self.video_codec_combo.currentData()
        if video_codec is None:
            return None

        is_gpu = self.gpu_combo.currentData() is not None
        is_nvenc = is_gpu and video_codec in ['h264_nvenc', 'hevc_nvenc', 'av1_nvenc']
        bitrate = f"{self.bitrate_slider.value()*0.1:.1f}M"

        # Base command with global options
        cmd = ["ffmpeg", "-y", "-progress", "pipe:1", "-nostats", "-loglevel", "error"]


        # Alt (entfernt): codec-lokales `-gpu N` (deprecated, in Multi-GPU-Setups
        # nicht mehr zuverlaessig wirksam)
        # Neu: globales `-init_hw_device cuda:N` VOR -i
        if is_nvenc:
            gpu_index_str = str(self.gpu_combo.currentData())
            cmd.extend(["-init_hw_device", f"cuda:{gpu_index_str}"])


        if self.mediathek_safe_checkbox.isChecked():
            cmd.extend(["-fflags", "+genpts"])              # Regeneriert Timestamps
            cmd.extend(["-avoid_negative_ts", "make_zero"]) # Behebt negative Timestamps
            if log:
                print("📺 Mediathek-Safe: Input options activated (genpts + avoid_negative_ts)")
        
        # Input file
        cmd.extend(["-i", input_path])
        
        maps, codecs, video_filters = [], [], []

        maps.extend(["-map", "0:v:0"])

        if is_gpu:

            # via `-init_hw_device cuda:N` vor -i (siehe oben). Modern, FFmpeg 8.0+.
            codecs.extend(["-c:v", video_codec])
            if video_codec == 'av1_nvenc':
                if self.preset_checkbox.isChecked(): codecs.extend(["-preset", "p7"])
                if self.tune_checkbox.isChecked(): codecs.extend(["-tune", "uhq"])
                if self.multipass_checkbox.isChecked(): codecs.extend(["-multipass", "fullres"])
                if self.rc_lookahead_checkbox.isChecked() and self.rc_lookahead_line.text():
                    codecs.extend(["-rc-lookahead", self.rc_lookahead_line.text()])
        else:
            codecs.extend(["-c:v", video_codec])
            if video_codec in ["libx265", "libx264"]:
                codecs.extend(["-preset", "medium"])
        

        # Checkbox AUS (default): maxrate = 2 x target, kein bufsize (weicher Cap, Quality-First)
        # Checkbox AN: maxrate = target, bufsize = 2 x maxrate (harter Cap, VBV-Compliance)
        target_mbps = self.bitrate_slider.value() * 0.1
        if self.strict_vbv_checkbox.isChecked():
            maxrate_mbps = target_mbps
            bufsize_mbps = target_mbps * 2
            codecs.extend([
                "-b:v", bitrate,
                "-maxrate", f"{maxrate_mbps:.1f}M",
                "-bufsize", f"{bufsize_mbps:.1f}M",
            ])
        else:
            maxrate_mbps = target_mbps * 2
            codecs.extend([
                "-b:v", bitrate,
                "-maxrate", f"{maxrate_mbps:.1f}M",
            ])
        

        if self.mediathek_safe_checkbox.isChecked():

            codecs.extend(["-fps_mode", "cfr"])         # Erzwingt konstante Frame-Rate
            codecs.extend(["-pix_fmt", "yuv420p"])     # Force 8-bit
            
            # NVENC-spezifische Compatibility-Fixes
            if is_nvenc:
                codecs.extend(["-g", "48"])            # Keyframe alle 48 Frames (~2 Sek bei 24fps)
                codecs.extend(["-strict_gop", "1"])    # Strict GOP structure
                
                # Codec-spezifische Profile/Level für maximale Kompatibilität
                if video_codec == 'h264_nvenc':
                    codecs.extend(["-profile:v", "high"])
                    codecs.extend(["-level", "4.1"])
                    if log:
                        print("📺 Mediathek-Safe: H.264 NVENC (profile=high, level=4.1, strict_gop)")
                elif video_codec == 'hevc_nvenc':
                    codecs.extend(["-profile:v", "main"])
                    codecs.extend(["-level", "4.1"])
                    if log:
                        print("📺 Mediathek-Safe: HEVC NVENC (profile=main, level=4.1, strict_gop)")
                elif video_codec == 'av1_nvenc':
                    # AV1 braucht kein profile/level, nur GOP

                    if self.force_av1_checkbox.isChecked():
                        # Experimentelle Color Space Fixes für AV1
                        codecs.extend(["-color_primaries", "bt709"])
                        codecs.extend(["-color_trc", "bt709"])
                        codecs.extend(["-colorspace", "bt709"])
                        codecs.extend(["-color_range", "tv"])
                        if log:
                            print("📺 Mediathek-Safe: AV1 NVENC FORCE MODE (strict_gop, g=48, bt709 color space)")
                    else:
                        if log:
                            print("📺 Mediathek-Safe: AV1 NVENC (strict_gop, g=48)")
            else:
                if log:
                    print("📺 Mediathek-Safe: Output options (fps_mode cfr + yuv420p)")
        
        # Resolution Scaling
        scaling_filter = self._build_scaling_filter()
        if scaling_filter:
            video_filters.append(scaling_filter)

        selected_audio_streams_to_map = []
        for cb, stream_data in self.audio_track_checkboxes:
            if cb.isChecked(): selected_audio_streams_to_map.append(stream_data)

        if not selected_audio_streams_to_map and self.audio_streams:
            selected_audio_streams_to_map.append(self.audio_streams[0])

        for stream in selected_audio_streams_to_map:
            maps.extend(["-map", f"0:{stream['index']}"])

        # Dolby Atmos Protection: Erzwinge Copy für alle Audio-Streams
        if self.has_atmos_detected:
            codecs.extend(["-c:a", "copy"])
            print("🎬 Dolby Atmos detected: Using -c:a copy for all audio streams")
        else:

            # AUDIO_CODEC_FFMPEG_MAP enthaelt das Encoder-Mapping; 'Copy' ist special.
            audio_codec = self.audio_codec_combo.currentText()
            if audio_codec == "Copy":
                codecs.extend(["-c:a", "copy"])
            else:
                ffmpeg_codec = self.AUDIO_CODEC_FFMPEG_MAP.get(audio_codec, "aac")
                bitrate_val = self.audio_bitrate_line.text().strip() or "128"
                codecs.extend(["-c:a", ffmpeg_codec, "-b:a", f"{bitrate_val}k"])

        selected_subtitle_stream = self.subtitle_combo.itemData(self.subtitle_combo.currentIndex())
        if selected_subtitle_stream is not None:
            if self.burn_in_checkbox.isChecked():
                escaped_path = input_path.replace('\\', '/').replace(':', '\\:')
                video_filters.append(f"subtitles='{escaped_path}':si={selected_subtitle_stream['index']}")
            else:
                maps.extend(["-map", f"0:{selected_subtitle_stream['index']}"])
                codecs.extend(["-c:s", "copy"])

        cmd.extend(maps)
        if video_filters: cmd.extend(["-vf", ",".join(video_filters)])
        cmd.extend(codecs)
        cmd.append(output_path)
        
        return cmd
    
    def _build_scaling_filter(self) -> str:
        """Baut den Scaling-Filter für ffmpeg - ULTRA-EINFACH via Checkbox"""
        # Wenn Checkbox nicht aktiviert, keine Skalierung
        if not self.downscale_to_1080p_checkbox.isChecked():
            return ""
        
        # Wenn kein Input-Video geladen, keine Skalierung
        if self.input_width <= 0 or self.input_height <= 0:
            return ""
        
        # Nur downscalen wenn Input größer als 1080p
        if self.input_height <= 1080:
            print(f"ℹ️ Input already 1080p or smaller ({self.input_width}x{self.input_height}), skipping downscale")
            return ""
        
        # Berechne 1080p Width mit Aspect Ratio Preservation
        target_height = 1080
        aspect_ratio = self.input_width / self.input_height
        target_width = int(target_height * aspect_ratio)
        
        # Stelle sicher dass Width gerade ist (ffmpeg requirement)
        if target_width % 2 != 0:
            target_width += 1
        
        print(f"📐 Downscaling: {self.input_width}x{self.input_height} → {target_width}x{target_height} (Lanczos)")
        
        # Verwende Lanczos für beste Qualität
        return f"scale={target_width}:{target_height}:flags=lanczos"

    def refresh_audio_track_checkboxes(self):
        while (item := self.audio_tracks_layout.takeAt(0)) is not None:
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif item.spacerItem():
                self.audio_tracks_layout.removeItem(item)

        self.audio_track_checkboxes = []

        if not self.audio_streams:
            self.audio_tracks_layout.addWidget(QLabel("No audio tracks found."))
            self.audio_tracks_layout.addStretch()
            return

        for i, stream in enumerate(self.audio_streams):
            tags = stream.get('tags', {})
            lang = tags.get('language', 'und')
            title = tags.get('title', f"Track {stream.get('index', i)}")
            codec = stream.get('codec_name', 'unknown')
            
            # Check if this stream is Atmos
            stream_index = stream.get('index', -1)
            is_atmos_stream = stream_index in self.atmos_stream_indices
            
            checkbox_label = f"#{stream.get('index', i)} ({lang.upper()}: {title}) [{codec}]"
            if is_atmos_stream:
                checkbox_label += " 🎬"  # Atmos-Marker
            
            cb = QCheckBox(checkbox_label)
            
            # Bei Atmos: ALLE Tracks aktiviert + disabled
            if self.has_atmos_detected:
                cb.setChecked(True)
                cb.setEnabled(False)
            else:

                # (vorher: nur erster Track aktiv, was Audio-Spuren stillschweigend wegwarf)
                cb.setChecked(True)
            
            cb.stateChanged.connect(self.update_command_preview)
            self.audio_tracks_layout.addWidget(cb)
            self.audio_track_checkboxes.append((cb, stream))
        self.audio_tracks_layout.addStretch()

    def update_subtitle_combo(self):
        self.subtitle_combo.clear()
        self.subtitle_combo.addItem("None", None)
        for i, stream in enumerate(self.subtitle_streams):
            tags = stream.get('tags', {})
            lang = tags.get('language', 'und').lower()
            title = tags.get('title', f"Track {stream.get('index', i)}")
            codec = stream.get('codec_name', 'unknown')
            item_text = f"Stream {stream.get('index', i)} ({lang.upper()}: {title}) [{codec}]"
            self.subtitle_combo.addItem(item_text, stream)

    def _resolve_initial_browse_dir(self, current_text: str = "") -> str:
        """
        Liefert das Initial-Verzeichnis fuer File/Folder-Dialoge.

        Reihenfolge (erste passende Option gewinnt):
          1. Verzeichnis aus current_text (z.B. aktueller Inhalt von input_line),
             falls gesetzt UND existiert
          2. self.last_browse_dir, falls gesetzt UND existiert
          3. Drive-Root von self.last_browse_dir (z.B. "U:\\"), falls Drive
             noch existiert (Verzeichnis selbst aber nicht mehr - z.B. nach
             Umbenennen oder Loeschen des letzten Ordners)
          4. System-Root - bei erstem Start (kein Settings-File) ODER wenn
             auch das Drive verschwunden ist (z.B. USB-Stick gezogen).
             Auf Windows = Root des aktuellen Drives (typ. C:\\),
             auf Linux/Mac = "/".
        """
        # 1. Aktueller Pfad aus dem Line Edit
        if current_text:
            current_dir = os.path.dirname(current_text)
            if current_dir and os.path.isdir(current_dir):
                return current_dir

        # 2. Persistiertes letztes Verzeichnis
        if self.last_browse_dir and os.path.isdir(self.last_browse_dir):
            return self.last_browse_dir

        # 3. Drive-Root vom letzten Verzeichnis (falls Drive noch lebt)
        if self.last_browse_dir:
            drive, _ = os.path.splitdrive(self.last_browse_dir)
            if drive:
                drive_root = drive + os.sep
                if os.path.isdir(drive_root):
                    return drive_root

        # 4. System-Root als Fallback
        return os.path.abspath(os.sep)

    def _update_last_browse_dir(self, path: str) -> None:
        """
        Merkt sich das Verzeichnis nach erfolgreichem Browse.
        Wird beim naechsten save_settings persistiert.
        """
        if not path:
            return
        d = path if os.path.isdir(path) else os.path.dirname(path)
        if d and os.path.isdir(d):
            self.last_browse_dir = d

    def browse_input(self):

        init_dir = self._resolve_initial_browse_dir(self.input_line.text())
        filename, _ = QFileDialog.getOpenFileName(self, "Select Input Video", init_dir, "Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.ts)")
        if filename:
            self.input_line.setText(filename)
            self._update_last_browse_dir(filename)

    def browse_output(self):

        init_dir = self._resolve_initial_browse_dir(self.output_line.text())
        filename, _ = QFileDialog.getSaveFileName(self, "Select Output Video", init_dir, "MKV Files (*.mkv);;MP4 Files (*.mp4)")
        if filename:
            self.output_line.setText(filename)
            self._update_last_browse_dir(filename)

    def browse_folder(self):

        init_dir = self._resolve_initial_browse_dir(self.folder_line.text())
        folder = QFileDialog.getExistingDirectory(self, "Select Folder", init_dir)
        if folder:
            self.folder_line.setText(folder)
            self._update_last_browse_dir(folder)

    def browse_output_folder(self):

        init_dir = self._resolve_initial_browse_dir(self.output_folder_line.text())
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", init_dir)
        if folder:
            self.output_folder_line.setText(folder)
            self._update_last_browse_dir(folder)

    def set_inputs_enabled(self, enabled: bool):
        """
        Während Encoding bleiben Save/About/Pause/Cancel aktiv.
        Freezes UI für robuste Encoding-Session.
        """
        widgets_to_toggle = [
            self.folder_line, self.output_folder_line, self.input_line, self.output_line,
            self.bitrate_slider, self.video_codec_combo, self.gpu_combo,
            self.subtitle_combo, self.burn_in_checkbox,
            self.default_btn,
            self.downscale_to_1080p_checkbox,
            self.mediathek_safe_checkbox,
            self.force_av1_checkbox,

            # danach wieder freigeben (anders als multipass/preset/tune, die nur bei AV1-NVENC enabled sind)
            self.strict_vbv_checkbox
        ]
        
        # Audio Codec und Bitrate: NUR wenn KEIN Atmos aktiv
        if not self.has_atmos_detected:
            widgets_to_toggle.extend([self.audio_codec_combo, self.audio_bitrate_line])
        
        for w in self.findChildren(QPushButton):
            if w.text() == "Browse...":
                widgets_to_toggle.append(w)
                
        for widget in widgets_to_toggle:
            widget.setEnabled(enabled)
        
        # Audio Track Checkboxes: NUR wenn KEIN Atmos aktiv
        if not self.has_atmos_detected:
            for cb, _ in self.audio_track_checkboxes:
                cb.setEnabled(enabled)
        
        if enabled:
            self.update_advanced_options_state()
            if not self.has_atmos_detected:
                self.toggle_audio_bitrate_field()
        else:
            self.preset_checkbox.setEnabled(False)
            self.tune_checkbox.setEnabled(False)
            self.multipass_checkbox.setEnabled(False)
            self.rc_lookahead_checkbox.setEnabled(False)
            self.rc_lookahead_line.setEnabled(False)

    def update_progress(self, percent, current_seconds, current_size_bytes):
        self.progress_bar.setValue(int(percent * 10))
        self.progress_bar.setFormat(f"{percent:.1f}%")
        current_time_str = seconds_to_hms(current_seconds)
        total_time_str = seconds_to_hms(self.total_duration)
        self.progress_label.setText(f"Progress: {current_time_str} / {total_time_str}")
        
        elapsed = time.time() - self.start_time
        speed_x = (current_seconds / elapsed) if elapsed > 0 else 0.0
        
        speed_fps = 0.0
        if self.input_video_fps > 0 and current_seconds > 0:
            current_frames = current_seconds * self.input_video_fps
            speed_fps = (current_frames / elapsed) if elapsed > 0 else 0.0

        if speed_x > 0 and self.total_duration > 0:
            rem_seconds = max(0, (self.total_duration - current_seconds) / speed_x)
            self.remaining_label.setText(f"Remaining: {seconds_to_hms(rem_seconds)}")
        else:
            self.remaining_label.setText("Remaining: --:--:--")

        self.speed_label.setText(f"Speed: {speed_x:.2f}x | {speed_fps:.2f} fps")

        if current_seconds >= self.prediction_start_time_seconds:
            if current_size_bytes > 0 and self.total_duration > 0 and current_seconds > 0:
                if time.time() - self.last_prediction_update_time >= self.prediction_update_interval_seconds or self.predicted_file_size_mb == 0:
                    predicted_size_bytes = (current_size_bytes / current_seconds) * self.total_duration
                    self.predicted_file_size_mb = int(predicted_size_bytes / (1024 * 1024))
                    self.predicted_size_label.setText(f"Predicted Size: ~{self.predicted_file_size_mb} MB")
                    self.last_prediction_update_time = time.time()
        else:
            self.predicted_size_label.setText("Predicted Size: N/A")
    
    def update_command_preview(self, *args):
        input_path = self.input_line.text().strip()
        output_path = self.output_line.text().strip()
        
        if not input_path or not output_path:
            self.cmd_preview_label.setText("")
            return
        
        cmd_list = self.build_ffmpeg_command(input_path, output_path)
        if not cmd_list:
            self.cmd_preview_label.setText("(Invalid settings for command preview)")
            return
        
        def quote_if_needed(part):
            if (' ' in part or '(' in part or ')' in part) and any(c in part for c in './\\'):
                return f'"{part}"'
            return part

        cmd_list_quoted = [quote_if_needed(part) for part in cmd_list]
        self.cmd_preview_label.setText(" ".join(cmd_list_quoted))

    def load_settings(self):
        """
        Lädt NUR Encoder-Settings, KEINE Pfade.
        Pfad-Felder bleiben leer beim Start.
        """
        try:
            with open(self.settings_path, 'r') as f:
                settings = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            settings = {}


        # oder kaputtem JSON -> _resolve_initial_browse_dir liefert dann System-Root)
        stored_dir = settings.get('last_browse_dir', '')
        self.last_browse_dir = stored_dir if stored_dir else None

        # NEW: Theme aus Settings. Die Palette ist beim App-Start in __main__
        # bereits angewendet worden (via _read_theme_from_settings()), daher hier
        # nur den State synchronisieren und den Toggle-Button beschriften.
        stored_theme = settings.get('theme', THEME_LIGHT)
        if stored_theme not in (THEME_LIGHT, THEME_DARK):
            stored_theme = THEME_LIGHT
        self.current_theme = stored_theme
        self._refresh_theme_btn()

        # werden. setup_ui() lief vorher mit dem Default THEME_LIGHT und hat den
        # hellen Idle-Stil angewendet. Wenn das gespeicherte Theme tatsaechlich
        # DARK ist, passt der Stil nicht mehr zur Palette - im Bild war es
        # weiss-auf-weiss bzw. blau-auf-blau und unlesbar. Der Fix ruft den
        # zentralen Stil-Setter mit dem nun korrekt geladenen Theme auf.
        self._apply_run_button_style()


        # Codec-Dropdowns korrekt gefiltert werden. Default beim ersten Start: MKV.
        container_data = settings.get('container', 'mkv')
        if container_data not in self.CONTAINER_VIDEO_CODECS:
            container_data = 'mkv'
        for i in range(self.container_combo.count()):
            if self.container_combo.itemData(i) == container_data:
                self.container_combo.blockSignals(True)
                self.container_combo.setCurrentIndex(i)
                self.container_combo.blockSignals(False)
                break

        # GPU & Video Codec
        gpu_index = settings.get('gpu_index', 0 if self.gpu_info_list else self.gpu_combo.count() - 1)
        if gpu_index < self.gpu_combo.count():
            self.gpu_combo.setCurrentIndex(gpu_index)

        self.update_video_codec_options()
        self._refresh_audio_codec_combo()

        if 'video_codec_index' in settings:
            video_codec_index = settings['video_codec_index']
            if video_codec_index < self.video_codec_combo.count():
                item = self.video_codec_combo.model().item(video_codec_index)
                if item and item.isEnabled():
                    self.video_codec_combo.setCurrentIndex(video_codec_index)
        
        # Bitrate & Advanced Options
        self.bitrate_slider.setValue(settings.get('bitrate', 40))
        self.preset_checkbox.setChecked(settings.get('preset_enabled', True))
        self.tune_checkbox.setChecked(settings.get('tune_enabled', True))
        self.multipass_checkbox.setChecked(settings.get('multipass_enabled', False))

        self.strict_vbv_checkbox.setChecked(settings.get('strict_vbv_enabled', False))
        self.rc_lookahead_checkbox.setChecked(settings.get('rc_lookahead_enabled', True))
        self.rc_lookahead_line.setText(settings.get('rc_lookahead', '32'))
        
        # Resolution & Compatibility Settings
        self.downscale_to_1080p_checkbox.setChecked(settings.get('downscale_to_1080p', False))
        self.mediathek_safe_checkbox.setChecked(settings.get('mediathek_safe', False))
        self.force_av1_checkbox.setChecked(settings.get('force_av1', False))
        
        # Audio Settings
        audio_codec_index = settings.get('audio_codec_index', 0)
        if audio_codec_index < self.audio_codec_combo.count():
            self.audio_codec_combo.setCurrentIndex(audio_codec_index)

        self.audio_bitrate_line.setText(settings.get('audio_bitrate', '128'))
        
        # Update UI state
        self._update_mediathek_availability()
        self.update_advanced_options_state()
        self.update_command_preview()

    def save_settings(self):
        """
        Speichert NUR Encoder-Settings, KEINE Pfade.
        User-Workflow: Settings sind unabhängig von aktuellen Dateien.
        """
        settings = {
            # Encoder Settings
            'gpu_index': self.gpu_combo.currentIndex(),

            'container': self.container_combo.currentData() or 'mkv',
            'video_codec_index': self.video_codec_combo.currentIndex(),
            'bitrate': self.bitrate_slider.value(),
            
            # Advanced Options
            'preset_enabled': self.preset_checkbox.isChecked(),
            'tune_enabled': self.tune_checkbox.isChecked(),
            'multipass_enabled': self.multipass_checkbox.isChecked(),

            'strict_vbv_enabled': self.strict_vbv_checkbox.isChecked(),
            'rc_lookahead_enabled': self.rc_lookahead_checkbox.isChecked(),
            'rc_lookahead': self.rc_lookahead_line.text(),
            
            # Resolution & Compatibility
            'downscale_to_1080p': self.downscale_to_1080p_checkbox.isChecked(),
            'mediathek_safe': self.mediathek_safe_checkbox.isChecked(),
            'force_av1': self.force_av1_checkbox.isChecked(),
            
            # Audio Settings
            'audio_codec_index': self.audio_codec_combo.currentIndex(),
            'audio_bitrate': self.audio_bitrate_line.text(),


            # naechsten Programmstart). Leerstring wenn noch nichts gebrowst wurde.
            'last_browse_dir': self.last_browse_dir or '',

            # NEW: Theme-Wahl (light/dark)
            'theme': self.current_theme,
        }
        try:
            with open(self.settings_path, 'w') as f:
                json.dump(settings, f, indent=4)
            
            QMessageBox.information(self, "Settings Saved", "Settings have been saved successfully!")

        except Exception as e:
            QMessageBox.warning(self, "Settings Save Error", f"Failed to save settings: {e}")

    # ------------------------------------------------------------------
    # Theme Handling (NEW)
    # ------------------------------------------------------------------

    def _toggle_theme(self):
        """
        Wechselt zwischen Light- und Dark-Mode. Live - kein Restart noetig.
        Wird sofort persistiert, damit die Wahl beim naechsten Start
        ohne Aufruf von "Save Settings" erhalten bleibt.
        """
        new_theme = THEME_DARK if self.current_theme == THEME_LIGHT else THEME_LIGHT
        self.current_theme = new_theme
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, new_theme)
        self._refresh_theme_btn()

        # Sonst passen Stylesheet und Palette nach Theme-Wechsel nicht zueinander
        # und der Button braucht 2 Klicks bis er korrekt aussieht.
        self._apply_run_button_style()
        # Light-Persist ohne Dialog: nur Theme-Key updaten (nicht das volle
        # save_settings() aufrufen, das wuerde "Settings saved"-Box poppen).
        self._persist_theme_only(new_theme)

    def _apply_run_button_style(self):
        """
        Setzt das Run-Button-Stylesheet abhaengig von Theme und
        Encoding-Zustand (idle vs. running).
        
        Hintergrund: Der Run-Button hat eigene Hex-Farben fuer visuelle
        Hervorhebung (hellgruen idle, leuchtgruen running). Diese Farben
        muessen passend zum Theme gewaehlt werden, sonst entsteht im Dark-
        Mode ein hellgruener Block, der nicht zum sonstigen Look passt.
        Wird bei jedem Theme-Wechsel und Zustands-Wechsel neu aufgerufen.
        """
        if not hasattr(self, 'run_btn'):
            return
        is_dark = (getattr(self, 'current_theme', THEME_LIGHT) == THEME_DARK)
        state = getattr(self, '_run_btn_state', 'idle')
        
        if state == "running":
            # Running-Look ist in beiden Themes gleich: leuchtgruen mit
            # dunkelgruener fettsetzter Schrift - gut lesbar auf Hell und Dunkel.
            self.run_btn.setStyleSheet("""
                QPushButton {
                    background-color: #5FCF5F;
                    color: #1B5E20;
                    font-weight: bold;
                }
                QPushButton:disabled {
                    background-color: #5FCF5F;
                    color: #2E7D2E;
                }
            """)
        elif is_dark:
            # Idle-Look Dark: dezent dunkelblau, konsistent zum Light-Blau,
            # damit der Run-Button sich vom Window abhebt aber nicht aufdringlich
            # wirkt. Heller Blauton fuer Schrift, fuer guten Kontrast auf dem
            # dunklen Hintergrund. Farben sind 10% dunkler als der Material-
            # Blue-Default (Material 50/100 als Ausgangsbasis * 0.9 pro Kanal).
            self.run_btn.setStyleSheet("""
                QPushButton {
                    background-color: #17293E;
                    color: #A8C8E2;
                }
                QPushButton:hover {
                    background-color: #203953;
                }
                QPushButton:disabled {
                    background-color: #2A2A2A;
                    color: #6B6B6B;
                }
            """)
        else:
            # Idle-Look Light: dezent blass-blau, hebt den Run-Button visuell
            # hervor ohne aufdringlich zu wirken. Material Blue 50/100 als
            # Ausgangspunkt, 10% dunkler pro Kanal fuer einen kraeftigeren Look.
            self.run_btn.setStyleSheet("""
                QPushButton {
                    background-color: #CCDAE4;
                }
                QPushButton:hover {
                    background-color: #A8C8E2;
                }
                QPushButton:disabled {
                    background-color: #F5F5F5;
                }
            """)


        # rendert Qt das Stylesheet manchmal nicht direkt neu, besonders wenn
        # der Stylesheet-Text identisch zum vorherigen Aufruf ist. Ohne diesen
        # Trick brauchte der Run-Button beim Theme-Wechsel mehrere Klicks bis
        # zur korrekten Darstellung. unpolish + polish zwingt Qt, den Style
        # komplett neu zu berechnen.
        self.run_btn.style().unpolish(self.run_btn)
        self.run_btn.style().polish(self.run_btn)
        self.run_btn.update()

    def _refresh_theme_btn(self):
        """Icon des Theme-Buttons an aktuelles Theme anpassen.
        
        Im Dark-Mode: Sonne (☀) - klick wechselt zu Light.
        Im Light-Mode: Mond/Sichel (☾) - klick wechselt zu Dark.
        Nur das Icon, kein Text - der Button ist quadratisch und kompakt.
        """
        if not hasattr(self, 'theme_btn'):
            return
        if self.current_theme == THEME_DARK:
            self.theme_btn.setText("☀")
        else:
            self.theme_btn.setText("☾")

    def _persist_theme_only(self, theme: str):
        """
        Schreibt NUR den 'theme'-Key in die Settings-Datei, ohne die anderen
        Werte anzufassen oder Dialoge zu zeigen. Robust gegen kaputte/
        fehlende Dateien (faellt auf Default-Dict zurueck).
        """
        try:
            try:
                with open(self.settings_path, 'r') as f:
                    settings = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                settings = {}
            settings['theme'] = theme
            with open(self.settings_path, 'w') as f:
                json.dump(settings, f, indent=4)
        except OSError as e:
            print(f"⚠️ Could not persist theme: {e}")

    def closeEvent(self, event):
        """
        Robuste Shutdown-Sequenz.
        
        Wichtig: ffmpeg läuft als CREATE_NEW_PROCESS_GROUP, wird also NICHT
        automatisch mit dem GUI-Prozess gekillt. Wenn der closeEvent nicht
        sauber durchläuft, bleiben verwaiste ffmpeg-Prozesse zurück.
        
        Sequenz:
        1. Wenn Encoding läuft: User um Bestätigung bitten
        2. user_cancelled-Flag setzen (verhindert spätere process_next-Triggers)
        3. Pending Timer ZUERST stoppen (sonst startet noch ein neuer Worker)
        4. Aktiven Worker mit force=True stoppen (taskkill /F /T + psutil-Fallback)
        5. Toast/ThreadPool aufräumen
        """
        # Schritt 1: Bestätigung wenn Encoding läuft
        if self._is_worker_running():
            reply = QMessageBox.question(
                self,
                'Encoding läuft',
                "Es läuft gerade ein Encoding-Vorgang.\n\n"
                "Beim Schließen wird der laufende ffmpeg-Prozess beendet "
                "und der Batch (falls aktiv) abgebrochen.\n\n"
                "Wirklich beenden?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        
        # Schritt 2: Cancel-Flag setzen — verhindert dass irgendein noch nicht
        # gefeuerter Timer einen neuen Worker triggert
        self.user_cancelled = True
        
        # Schritt 3: Pending Batch Timer ZUERST stoppen (vor dem Worker-Stop!)
        # Sonst könnte zwischen worker.stop() und super().closeEvent() noch
        # ein Timer feuern und einen neuen ffmpeg-Prozess starten.
        for timer in self.pending_batch_timers:
            try:
                if timer.isActive():
                    timer.stop()
                timer.deleteLater()
            except RuntimeError:
                pass  # Timer schon zerstört
        self.pending_batch_timers.clear()
        
        # Schritt 4: Worker-Referenz unter Mutex sichern, dann ohne Mutex stoppen
        worker_to_stop = None
        with QMutexLocker(self.worker_mutex):
            if self.worker and self.worker.isRunning():
                worker_to_stop = self.worker
        
        if worker_to_stop is not None:
            # force=True: Direkt taskkill /F /T + psutil-Fallback
            # (kein graceful-Versuch beim Shutdown - wir wollen schnell raus)
            worker_to_stop.stop(force=True)
            
            # Auf Thread-Ende warten - gibt subprocess.wait() Zeit
            if not worker_to_stop.wait(3000):
                print("⚠️ Worker thread did not finish in 3s after force stop")
                # Letzte Chance: nochmal kill versuchen
                try:
                    worker_to_stop._ensure_process_terminated()
                except Exception as e:
                    print(f"⚠️ Final cleanup error: {e}")
        
        # Schritt 5: Toast und ThreadPool aufräumen
        self.cleanup_toast()
        self.thread_pool.waitForDone(2000)
        
        super().closeEvent(event)

    def toggle_pause(self):
        """Modified: thread-safe pause/resume"""
        if not self._is_worker_running():
            return
        
        with QMutexLocker(self.worker_mutex):
            if self.paused:
                self.worker.resume()
                self.pause_btn.setText("Pause")
                self.paused = False
            else:
                self.worker.pause()
                self.pause_btn.setText("Resume")
                self.paused = True

    def cancel_encoding(self):
        """Robust cancel with immediate batch stop"""
        if not self._is_worker_running():
            return
        
        reply = QMessageBox.question(
            self, 'Confirm Cancel', 
            "Are you sure you want to cancel the current encoding?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:

            self.user_cancelled = True
            


            for timer in self.pending_batch_timers:
                if timer.isActive():
                    timer.stop()
                timer.deleteLater()
            self.pending_batch_timers.clear()
            
            # Stop current worker
            with QMutexLocker(self.worker_mutex):
                if self.worker:
                    self.worker.stop()
            
            print("🛑 User cancelled - batch processing stopped")

    def on_bitrate_slider_change(self, value):

        self.bitrate_value_label.setText(self._format_bitrate_label(value * 0.1))
        self._update_mediathek_availability()
        self.update_command_preview()
    
    def _on_bitrate_user_action(self, action):
        """
        Wird NUR bei expliziten User-Aktionen am Slider gefeuert
        (Klick, Tastatur, Mausrad). NICHT bei programmatischem setValue().
        Dadurch erkennen wir bewusste User-Eingaben und unterdrücken
        in solchen Fällen die Auto-Bitrate (50%-Regel).
        """
        self._user_modified_bitrate = True

    def toggle_audio_bitrate_field(self):
        if self.run_btn.isEnabled(): 
            is_copy = self.audio_codec_combo.currentText() == "Copy"
            self.audio_bitrate_line.setEnabled(not is_copy)
        self.update_command_preview()

    def _check_av1_mediathek_compatibility(self) -> bool:
        """
        Prüft AV1 + Mediathek-Safe Konflikt.
        Bietet automatischen Wechsel zu HEVC_NVENC an oder Force AV1 Option.
        Returns: True wenn fortgefahren werden kann, False wenn abgebrochen
        """
        # Prüfe ob AV1_NVENC + Mediathek-Safe aktiv
        if not self.mediathek_safe_checkbox.isChecked():
            return True  # Kein Mediathek-Safe → Kein Problem
        
        video_codec = self.video_codec_combo.currentData()
        if video_codec != 'av1_nvenc':
            return True  # Nicht AV1 → Kein Problem
        
        # Wenn Force AV1 bereits aktiviert → Durchlassen (User weiß was er tut)
        if self.force_av1_checkbox.isChecked():
            print("⚠️ AV1 with Mediathek-Safe - Force mode active (experimental)")
            return True
        
        # Konflikt erkannt! Zeige Warnung mit Auto-Switch Angebot
        reply = QMessageBox.warning(
            self,
            "AV1 + Mediathek-Safe Compatibility Issue",
            "<b>⚠️ AV1 encoding with Mediathek-Safe may cause black screens!</b><br><br>"
            "AV1_NVENC has known compatibility issues with Mediathek videos.<br>"
            "H.264 and HEVC work reliably with Mediathek-Safe mode.<br><br>"
            "<b>Recommendation:</b> Switch to HEVC_NVENC (H.265) for reliable results.<br><br>"
            "Choose an option:<br>"
            "• <b>Yes</b> - Switch to HEVC_NVENC (recommended)<br>"
            "• <b>No</b> - Force AV1 with experimental fixes (at your own risk)<br>"
            "• <b>Cancel</b> - Abort encoding",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            # Auto-Switch zu HEVC_NVENC
            for i in range(self.video_codec_combo.count()):
                if self.video_codec_combo.itemData(i) == 'hevc_nvenc':
                    self.video_codec_combo.setCurrentIndex(i)
                    print("🔄 Auto-switched from AV1_NVENC to HEVC_NVENC for Mediathek compatibility")
                    QMessageBox.information(
                        self,
                        "Codec Switched",
                        "Video codec changed to HEVC_NVENC (H.265).<br>"
                        "You can now proceed with encoding."
                    )
                    return True
            
            # Fallback wenn HEVC nicht verfügbar (sollte nicht passieren)
            QMessageBox.warning(
                self,
                "HEVC Not Available",
                "HEVC_NVENC is not available. Please select a different codec manually."
            )
            return False
        
        elif reply == QMessageBox.StandardButton.No:
            # User will mit AV1 weitermachen → Aktiviere Force AV1 automatisch
            self.force_av1_checkbox.setChecked(True)
            print("⚠️ Force AV1 mode activated - using experimental compatibility fixes")
            return True
        
        else:  # Cancel
            return False

    def run_ffmpeg(self):
        """Modified: thread-safe worker check"""
        if self._is_worker_running():
            QMessageBox.warning(self, "Encoding Running", 
                              "An encoding process is already running.")
            return
        

        if not self._check_av1_mediathek_compatibility():
            return  # User cancelled or switched codec
        
        if self.batch_mode:
            self.capture_master_config()
            self.set_inputs_enabled(False)
            self.process_next()
            return
        
        input_path = self.input_line.text().strip()
        output_path = self.output_line.text().strip()
        
        if not input_path or not output_path:
            QMessageBox.warning(self, "Missing Input", "Please select both input and output files.")
            return

        if not os.path.exists(output_path):
            self.process_single_file(input_path, output_path)
            return

        reply = QMessageBox.question(self, 'Output File Exists', 
                                        f'The output file "{os.path.basename(output_path)}" already exists. Do you want to overwrite it?',
                                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
                                        QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.process_single_file(input_path, output_path)

    def _ensure_output_path_exists(self, file_path: str) -> bool:
        if not file_path:
            return False
        
        directory = os.path.dirname(file_path)
        
        if not directory:
            return True

        if not os.path.isdir(directory):
            try:
                os.makedirs(directory, exist_ok=True)
                print(f"Created output directory: {directory}")
                return True
            except Exception as e:
                QMessageBox.critical(self, "Output Path Error",
                                     f"Could not create the output directory:\n\n"
                                     f"{directory}\n\n"
                                     f"Please check permissions and the path.\n\nError: {e}")
                self.reset_ui_after_run()
                return False
        return True

    def process_single_file(self, input_path, output_path, is_batch=False, cmd_override=None):
        """
        - UI-Freeze für robuste Encoding-Session
        - Run-Button bekommt dunkelgrünes Signal während Encoding

        cmd_override - wird vom Remux-Dialog genutzt um seinen
        eigenen Befehl zu uebergeben, anstatt build_ffmpeg_command (welches
        den Hauptfenster-State liest) aufzurufen. Saubere Trennung.
        """
        if not is_batch:

            self.set_inputs_enabled(False)
        
        if not output_path or not any(output_path.lower().endswith(ext) for ext in ['.mkv', '.mp4']):
            error_msg = f"Invalid or missing output file name:\n\n{output_path}\n\nPlease ensure the output file has a valid extension (.mkv or .mp4)."
            if is_batch:
                QMessageBox.critical(self, "Batch Error", error_msg + "\n\nBatch process canceled.")
                self.reset_ui_after_run()
            else:
                QMessageBox.warning(self, "Invalid Output File", error_msg)
                self.set_inputs_enabled(True)  # Re-enable bei Fehler
            return

        if not self._ensure_output_path_exists(output_path):
            if not is_batch:
                self.set_inputs_enabled(True)  # Re-enable bei Fehler
            return

        if self.total_duration <= 0:
            error_msg = f"Could not determine video duration for {os.path.basename(input_path)}. This file might be corrupted or not a valid video."
            if is_batch:
                self.ffmpeg_finished(-1, error_msg)
            else:
                QMessageBox.warning(self, "Duration Error", error_msg)
                self.set_inputs_enabled(True)  # Re-enable bei Fehler
            return


        # direkt - sonst Standard-Build aus dem aktuellen Hauptfenster-State.
        if cmd_override is not None:
            cmd = cmd_override
        else:
            cmd = self.build_ffmpeg_command(input_path, output_path, log=True)
        if not cmd:
            error_msg = f"Could not build FFmpeg command for {os.path.basename(input_path)}. Check your settings."
            if is_batch:
                self.ffmpeg_finished(-1, error_msg)
            else:
                QMessageBox.critical(self, "Error", error_msg)
                self.set_inputs_enabled(True)  # Re-enable bei Fehler
            return
        
        if is_batch:
            self.current_file_label.setText(f"Processing: {os.path.basename(input_path)} ({self.batch_index + 1}/{len(self.batch_files)})")
        else:
            self.current_file_label.setText(f"Processing: {os.path.basename(input_path)}")



        self._run_btn_state = "running"
        self._apply_run_button_style()
        
        self.run_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.start_time = time.time()
        self.last_prediction_update_time = time.time()
        self.predicted_file_size_mb = 0
        self.predicted_size_label.setText("Predicted Size: N/A")
        self.progress_bar.setValue(0)
        self.progress_label.setText("Progress: 00:00 / " + seconds_to_hms(self.total_duration))
        self.remaining_label.setText("Remaining: --:--:--") 
        self.speed_label.setText("Speed: 0.00x | 0.00 fps") 

        with QMutexLocker(self.worker_mutex):

            if self.worker is not None:
                try:
                    self.worker.deleteLater()
                except RuntimeError:
                    pass  # Worker schon gelöscht
            
            self.worker = FFmpegWorker(cmd, self.total_duration)
            self.worker.progress_signal.connect(self.update_progress)
            self.worker.finished_signal.connect(self.ffmpeg_finished)
            self.worker.start()

    def process_next(self):
        if self.batch_index >= len(self.batch_files):
            QMessageBox.information(self, "Batch Complete", "All files have been processed.")
            self.reset_ui_after_run()
            return
        
        input_path = self.batch_files[self.batch_index]
        self.input_line.setText(input_path)
        
        audio_is_compatible = self.is_audio_compatible()
        
        if not audio_is_compatible:
            self.apply_master_config()
            self.audio_codec_combo.setCurrentIndex(0)
            for cb, _ in self.audio_track_checkboxes:
                cb.setChecked(True)
        else:
            self.apply_master_config()
        
        output_path = self.output_line.text()
        

        # Wenn die Output-Datei bereits existiert, wird sie übersprungen statt
        # überschrieben. Das verhindert versehentliche Doppel-Encodings, z.B.
        # nach einem Programm-Neustart mit demselben Batch-Folder.
        if output_path and os.path.exists(output_path):
            self._log_batch_skip(input_path, output_path, "Output file already exists")
            print(f"⏭️  Skipping {os.path.basename(input_path)} (output already exists)")
            
            self.batch_index += 1
            self.batch_skipped_count += 1
            
            # Nächste Datei nach kurzer Pause - via tracked timer (kein Memory Leak)
            if self.batch_index < len(self.batch_files) and not self.user_cancelled:
                timer = QTimer()
                timer.setSingleShot(True)
                
                def fire_and_cleanup(t=timer):
                    if t in self.pending_batch_timers:
                        self.pending_batch_timers.remove(t)
                    t.deleteLater()
                    self.process_next()
                
                timer.timeout.connect(fire_and_cleanup)
                self.pending_batch_timers.append(timer)
                timer.start(100)  # kurz, kein Encoding hat stattgefunden
            else:
                # Letzte Datei wurde geskippt → Batch sauber beenden
                self._finalize_batch_after_skips()
            return
        
        self.process_single_file(input_path, output_path, is_batch=True)
    
    def _log_batch_skip(self, input_path: str, output_path: str, reason: str) -> None:
        """
        Schreibt einen Skip-Vorfall in eine Message.log
        im Verzeichnis der übersprungenen Eingabedatei.
        
        Beispielzeile:
            [2026-04-30 14:32:11] SKIPPED
                Input:  S01E01.mkv
                Output: S01E01.AV1.mkv
                Reason: Output file already exists
        """
        log_dir = os.path.dirname(input_path)
        log_path = os.path.join(log_dir, "Message.log")
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        
        msg = (
            f"[{timestamp}] SKIPPED\n"
            f"    Input:  {os.path.basename(input_path)}\n"
            f"    Output: {os.path.basename(output_path)}\n"
            f"    Reason: {reason}\n\n"
        )
        
        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(msg)
        except OSError as e:
            # Log-Schreiben darf das Encoding nie blockieren
            print(f"⚠️ Could not write skip log to {log_path}: {e}")
    
    def _finalize_batch_after_skips(self) -> None:
        """
        Wird aufgerufen wenn der Batch durch reine Skips endet
        (also kein FFmpeg-Worker mehr läuft, der ffmpeg_finished triggern würde).
        Zeigt die Toast-Meldung und resettet die UI.
        """
        # Anzahl der tatsächlich verarbeiteten + geskippten Files
        total_files = len(self.batch_files)
        self.show_batch_complete_toast(total_files)
        self.reset_ui_after_run()

    def ffmpeg_finished(self, return_code: int, output: str):

        # Vorher: returncode in [-15,-9,1] - aber RC=1 ist generischer FFmpeg-Fehler,
        # NICHT zwingend ein Cancel. Echte Fehler wurden so als "canceled" gemeldet.
        is_cancelled = self.user_cancelled
        
        if return_code == 0:
            self.progress_bar.setValue(1000) 
            self.progress_bar.setFormat("100.0%")

        if self.batch_mode:
            current_file = os.path.basename(self.batch_files[self.batch_index])
            
            if return_code != 0 and not is_cancelled:
                QMessageBox.critical(self, "Batch Error", f"Error processing {current_file} (Return Code: {return_code}):\n\n{output}")
            
            self.batch_index += 1
            

            if self.batch_index < len(self.batch_files) and not self.user_cancelled and not self.batch_paused_for_reconfig:

                timer = QTimer()
                timer.setSingleShot(True)
                
                def fire_and_cleanup(t=timer):
                    if t in self.pending_batch_timers:
                        self.pending_batch_timers.remove(t)
                    t.deleteLater()
                    self.process_next()
                
                timer.timeout.connect(fire_and_cleanup)
                self.pending_batch_timers.append(timer)
                timer.start(500)
            elif not self.batch_paused_for_reconfig:
                if self.user_cancelled:
                    QMessageBox.information(self, "Batch Canceled", "The batch process has been canceled.")
                else:
                    # Batch erfolgreich - Zeige robuste Toast-Notification
                    total_files = len(self.batch_files)
                    self.show_batch_complete_toast(total_files)
                
                self.reset_ui_after_run()
            return
        
        if return_code == 0:
            QMessageBox.information(self, "Success", "Encoding completed successfully!")
        elif is_cancelled:
            QMessageBox.information(self, "Encoding Canceled", "Encoding process has been canceled.")
        else:
            QMessageBox.critical(self, "Encoding Failed", f"FFmpeg failed with return code {return_code}.\n\nOutput:\n{output}")
        
        self.reset_ui_after_run()

    def reset_ui_after_run(self):
        """
        - Smart reset - clears input/output/atmos, keeps codec settings
        - Reset Run-Button zu normalem Grünton
        """
        # Batch-Variablen zurücksetzen
        self.batch_mode = False
        self.batch_index = 0
        self.batch_files = []
        self.batch_master_config = None
        self.batch_paused_for_reconfig = False
        self.batch_skipped_count = 0
        

        self.user_cancelled = False
        self.pending_batch_timers.clear()
        

        self.input_line.clear()
        self.output_line.clear()
        self.folder_line.clear()
        self.output_folder_line.clear()
        

        self._clear_atmos_state()
        


        self._run_btn_state = "idle"
        self._apply_run_button_style()
        
        # UI-Buttons
        self.set_inputs_enabled(True)
        self.run_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.pause_btn.setText("Pause")
        self.paused = False
        
        # Progress-Labels
        self.progress_label.setText("Progress: 00:00 / 00:00")
        self.remaining_label.setText("Remaining: --:--:--")
        self.speed_label.setText("Speed: 0.00x | 0.00 fps")
        self.predicted_size_label.setText("Predicted Size: N/A")
        self.current_file_label.setText("")
        
        # Progress Bar Reset (verzögert für visuelle Feedback)
        QTimer.singleShot(1500, lambda: self.progress_bar.setValue(0))
        QTimer.singleShot(1500, lambda: self.progress_bar.setFormat("%p%"))
        
        # Worker cleanup
        with QMutexLocker(self.worker_mutex):
            self.worker = None

    def _on_batch_folder_changed(self, *args):
        """
        Erfasst jetzt REKURSIV alle Video-Dateien im Batch-Folder
        inklusive Unterordner via os.walk().
        """
        if self._is_worker_running():
            return
        
        folder = self.folder_line.text().strip()

        if folder and os.path.isdir(folder):
            if self.batch_mode and self.batch_files and os.path.commonpath([self.batch_files[0], folder]) == folder:
                # Schon dieselbe Folder-Wurzel - kein Re-Listing nötig
                if os.path.dirname(self.batch_files[0]).startswith(folder):
                    return

            video_extensions = ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.ts', '.m2ts', '.m4v']
            

            # Erfasst Video-Dateien in allen Unterordnern.
            files = []
            for root, dirs, filenames in os.walk(folder):
                for filename in filenames:
                    if os.path.splitext(filename)[1].lower() in video_extensions:
                        files.append(os.path.join(root, filename))
            files.sort()
            
            if not files:
                self.current_file_label.setText("No video files found in batch folder (recursive scan).")
                return

            self.batch_mode = True
            self.batch_files = files
            self.batch_index = 0
            self.batch_master_config = None
            self.batch_paused_for_reconfig = False
            self.batch_skipped_count = 0

            self.input_line.setText(self.batch_files[0])
            
            # User-Info über Anzahl + Verteilung
            unique_dirs = len({os.path.dirname(f) for f in files})
            print(f"📂 Batch mode (recursive): {len(files)} files across {unique_dirs} folder(s)")
            
            self.current_file_label.setText(
                f"Batch mode: {len(files)} files in {unique_dirs} folder(s). "
                f"Click 'Run' to start. Output goes next to each input file."
            )
        else:
            if self.batch_mode:
                self.reset_ui_after_run()
                self.current_file_label.setText("")

    def capture_master_config(self):
        selected_indices = [i for i, (cb, _) in enumerate(self.audio_track_checkboxes) if cb.isChecked()]
        audio_codecs = [stream.get('codec_name', 'unknown') for stream in self.audio_streams]
        
        self.batch_master_config = {
            'audio_stream_count': len(self.audio_streams),
            'audio_stream_codecs': audio_codecs,
            'selected_audio_indices': selected_indices,
            'audio_codec_index': self.audio_codec_combo.currentIndex(),
            'audio_bitrate': self.audio_bitrate_line.text(),
            'gpu_index': self.gpu_combo.currentIndex(),
            'video_codec_index': self.video_codec_combo.currentIndex(),
            'video_codec_text': self.video_codec_combo.currentText(),
            'bitrate': self.bitrate_slider.value(),
            'preset_enabled': self.preset_checkbox.isChecked(),
            'tune_enabled': self.tune_checkbox.isChecked(),
            'multipass_enabled': self.multipass_checkbox.isChecked(),

            'strict_vbv_enabled': self.strict_vbv_checkbox.isChecked(),
            'rc_lookahead_enabled': self.rc_lookahead_checkbox.isChecked(),
            'rc_lookahead': self.rc_lookahead_line.text(),
            'has_atmos': self.has_atmos_detected  # NEU: Atmos-Status für Batch
        }
        
        # Info für User wenn Atmos im Batch
        if self.has_atmos_detected:
            print("🎬 Batch Mode with Dolby Atmos: All files will use audio copy")

    def is_audio_compatible(self) -> bool:
        if not self.batch_master_config:
            return True
            
        current_codecs = [stream.get('codec_name', 'unknown') for stream in self.audio_streams]
        
        if len(current_codecs) != self.batch_master_config['audio_stream_count']:
            return False
        if current_codecs != self.batch_master_config['audio_stream_codecs']:
            return False
            
        return True

    def apply_master_config(self):
        if not self.batch_master_config:
            return
        
        self.gpu_combo.setCurrentIndex(self.batch_master_config['gpu_index'])
        self.update_video_codec_options()
        self.video_codec_combo.setCurrentIndex(self.batch_master_config['video_codec_index'])

        # _on_video_info_loaded hat slider_max bereits an die Source-Bitrate
        # der AKTUELLEN Datei angepasst (Cap auf Source - hoeher waere Speicher-
        # verschwendung). Wenn der Master-Wert (von einer groesseren Datei
        # stammend) ueber dem aktuellen Max liegt, clampen wir ihn auf das Max
        # statt das Max hochzuziehen - sonst koennte der User eine Bitrate
        # einstellen, die ueber der Source liegt (was sinnlos ist).
        master_bitrate = min(
            self.batch_master_config['bitrate'],
            self.bitrate_slider.maximum()
        )
        self.bitrate_slider.setValue(master_bitrate)
        self.preset_checkbox.setChecked(self.batch_master_config['preset_enabled'])
        self.tune_checkbox.setChecked(self.batch_master_config['tune_enabled'])
        self.multipass_checkbox.setChecked(self.batch_master_config['multipass_enabled'])

        self.strict_vbv_checkbox.setChecked(self.batch_master_config.get('strict_vbv_enabled', False))
        self.rc_lookahead_checkbox.setChecked(self.batch_master_config['rc_lookahead_enabled'])
        self.rc_lookahead_line.setText(self.batch_master_config['rc_lookahead'])
        
        # Atmos-Batch-Mode: Erzwinge Copy für ALLE Dateien im Batch
        if self.batch_master_config.get('has_atmos', False):
            print("🎬 Applying Atmos-safe settings for batch file")
            self.audio_codec_combo.setCurrentIndex(0)  # Copy
            # Alle Tracks werden in refresh_audio_track_checkboxes() aktiviert
        else:
            self.audio_codec_combo.setCurrentIndex(self.batch_master_config['audio_codec_index'])
            self.audio_bitrate_line.setText(self.batch_master_config['audio_bitrate'])
            
            for i, (cb, _) in enumerate(self.audio_track_checkboxes):
                cb.setChecked(i in self.batch_master_config['selected_audio_indices'])

    def show_batch_complete_toast(self, total_files: int) -> None:
        """
        Zeigt eine selbstschließende Toast-Notification mit Fallback-Button.
        Robuste Implementation die IMMER schließbar ist.
        
        Args:
            total_files: Anzahl der Dateien im Batch insgesamt
        """

        # (verhindert Timer-Leck bei sehr schnellen aufeinanderfolgenden Batches)
        self.cleanup_toast()
        
        # Toast-Dialog erstellen (Instanzvariable verhindert Garbage Collection)
        self.batch_toast = QMessageBox(self)
        self.batch_toast.setWindowTitle("Batch Complete")
        

        skipped = self.batch_skipped_count
        encoded = total_files - skipped
        
        if skipped == 0:
            text = (
                f"✅ All {total_files} file{'s' if total_files != 1 else ''} "
                f"encoded successfully!"
            )
        elif encoded == 0:
            text = (
                f"⏭️ All {total_files} file{'s' if total_files != 1 else ''} skipped\n"
                f"(outputs already existed — see Message.log files)"
            )
        else:
            text = (
                f"✅ Batch complete:\n"
                f"   • {encoded} encoded\n"
                f"   • {skipped} skipped (see Message.log files)"
            )
        
        self.batch_toast.setText(text)
        self.batch_toast.setIcon(QMessageBox.Icon.Information)
        
        # OK-Button mit Countdown (User kann jederzeit klicken)
        ok_button = self.batch_toast.addButton("OK (3)", QMessageBox.ButtonRole.AcceptRole)
        self.batch_toast.setDefaultButton(ok_button)
        
        # Countdown-Variablen
        self.toast_countdown = 3
        
        def update_countdown():
            """Aktualisiert den Countdown im Button-Text"""
            if not hasattr(self, 'batch_toast') or not self.batch_toast:
                return  # Toast wurde bereits geschlossen
                
            self.toast_countdown -= 1
            if self.toast_countdown > 0:
                ok_button.setText(f"OK ({self.toast_countdown})")
            else:
                # Zeit abgelaufen - Dialog automatisch schließen
                if self.batch_toast and self.batch_toast.isVisible():
                    self.batch_toast.accept()
        
        # Timer für Countdown (jede Sekunde)
        self.toast_timer = QTimer()
        self.toast_timer.timeout.connect(update_countdown)
        self.toast_timer.start(1000)  # 1 Sekunde Intervall
        
        # Cleanup bei Dialog-Schließung
        self.batch_toast.finished.connect(self.cleanup_toast)
        
        # Dialog anzeigen (NON-BLOCKING!)
        self.batch_toast.show()

    def cleanup_toast(self) -> None:
        """Räumt Timer und Referenzen sauber auf"""
        # Timer stoppen und löschen
        if hasattr(self, 'toast_timer') and self.toast_timer:
            self.toast_timer.stop()
            self.toast_timer.deleteLater()
            self.toast_timer = None
        
        # Toast-Referenz löschen
        if hasattr(self, 'batch_toast'):
            self.batch_toast = None
        
        self.toast_countdown = 0

    def show_remux_dialog(self):
        """
        Oeffnet den Remux-Dialog.

        Voraussetzungen:
          - Kein Worker laeuft (sonst Verwirrung mit zwei parallelen Encoding-Sessions)
          - Optionales Input-File: wenn keins geladen, zeigt der Dialog einen Hinweis
            und der Start-Button ist disabled

        Der Dialog ist modal - das Hauptfenster ist gesperrt waehrend der Dialog
        offen ist. Beim Klick auf "Start Remux" startet das Encoding im Hintergrund;
        der Dialog bleibt offen, sodass der User direkt das naechste File remuxen
        kann ohne den Dialog neu oeffnen zu muessen. Nur Klick auf "Close" (= Cancel)
        schliesst den Dialog.
        """
        if self._is_worker_running():
            QMessageBox.warning(
                self, "Encoding Running",
                "An encoding process is already running. "
                "Please wait for it to finish or cancel it before starting a remux."
            )
            return
        dlg = RemuxDialog(self)
        dlg.exec()

    def show_about_dialog(self):
        """
        Komplett-Neuaufbau als QDialog (vorher QMessageBox).

        QMessageBox ist fuer einfache Yes/No/OK-Prompts gemacht; sein internes
        HBox-Layout (Icon | Text | Buttons) plus ein globales Stylesheet auf
        QLabel hat zu unkontrollierbaren Layout-Kollisionen gefuehrt
        (Leerraeume links, abgeschnittener Text rechts).

        QDialog gibt volle Kontrolle: festes Hauptlayout, klar definierte
        Logo-Spalte, klar definierte Content-Spalte mit Word-Wrap, sauber
        positionierter OK-Button. Kein globales Stylesheet noetig.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("About FFmpeg Converter GUI")

        # Dialog-Groesse: 820x680 gibt genug Platz, damit lange Highlight-
        # Zeilen (z.B. "Hardware Encoding ... -init_hw_device cuda:N",
        # "Strict VBV ... bufsize = 2x maxrate", "Mediathek-Safe ... black-
        # screen issues on Mediathek downloads") nicht rechts abgeschnitten
        # werden, und der Footer (Copyright + Lizenz) komplett sichtbar bleibt.
        dlg.resize(820, 680)
        dlg.setMinimumSize(740, 560)

        # Hauptlayout: Content links, Logo rechts oben.
        main_layout = QHBoxLayout(dlg)
        main_layout.setContentsMargins(24, 24, 24, 16)
        main_layout.setSpacing(20)

        # --- Content-Spalte (links) ---
        content_layout = QVBoxLayout()
        content_layout.setSpacing(0)
        content_layout.setContentsMargins(0, 0, 0, 0)

        # HTML-Aufbau: einfach, robust, ohne Tabellen/Listen.
        # Jeder Eintrag ist ein <p> mit Bold-Title + em-dash + Description.
        def item(icon_title, description):
            return (
                f"<p style='margin: 3px 0;'>"
                f"<b>{icon_title}</b> &mdash; {description}"
                f"</p>"
            )

        highlights = (
            item("🚀 Hardware Encoding",
                 "NVENC AV1, H.265, H.264 with multi-GPU support "
                 "(modern <code>-init_hw_device cuda:N</code>)") +
            item("🎯 Codec-aware Recommended Bitrate",
                 "Smart defaults based on source codec (e.g. H.265→AV1 ≈ 60%, "
                 "H.264→AV1 ≈ 30%); slider capped at source bitrate") +
            item("🔒 Strict VBV",
                 "Optional hard bitrate cap; off = Quality-First soft cap") +
            item("📦 Remux MKV → MP4",
                 "Container-only conversion preserving Dolby Vision &amp; HDR metadata; "
                 "interactive workflow diagram, per-track audio/subtitle selection, "
                 "external audio/subtitle file support (multi-input), "
                 "Apple-compatible HEVC tagging (hvc1)") +
            item("📂 Recursive Batch",
                 "Folder trees; skipped files logged in Message.log") +
            item("🎵 Dolby Atmos Auto-Protection",
                 "Automatic detection &amp; safe handling on re-encoding") +
            item("📺 Mediathek-Safe Mode",
                 "Fix for black-screen issues on Mediathek downloads") +
            item("✨ 4K → 1080p",
                 "One-click resolution scaling with automatic aspect ratio") +
            item("💾 Portable Config",
                 "Settings live next to the program — USB-stick-friendly") +
            item("📥 Drag &amp; Drop",
                 "Files into Input field, folders into Batch field") +
            item("🛡 Robust Shutdown",
                 "ffmpeg processes are reliably terminated on close")
        )

        acks = (
            item("FFmpeg",              "video &amp; audio conversion") +
            item("PySide6",             "GUI framework") +
            item("psutil",              "process management") +
            item("Python",              "programming language") +
            item("NVIDIA NVENC",        "hardware encoder") +
            item("SVT-AV1, x264, x265", "software encoders") +
            item("Claude (Anthropic)",  "AI Coding Assistant for architecture, "
                                        "refactoring &amp; bug fixes")
        )

        html = (
            "<h3 style='margin: 0 0 4px 0;'>FFmpeg Converter GUI v2.6</h3>"
            "<p style='margin: 0 0 6px 0;'>A graphical user interface for "
            "FFmpeg with hardware and software encoding, thoughtful batch "
            "processing and smart defaults.</p>"
            # Keine explizite color: Text erbt die QPalette-Farbe und ist
            # somit in beiden Themes (Light/Dark) automatisch lesbar.
            "<p style='margin: 0 0 14px 0;'>"
            "<i>Requires FFmpeg 8.0 or newer.</i></p>"

            "<p style='margin: 12px 0 4px 0;'><b>Highlights</b></p>"
            f"{highlights}"

            # Mehr Luft vor Acknowledgements (28px statt 14px)
            "<p style='margin: 28px 0 4px 0;'><b>Acknowledgements</b></p>"
            f"{acks}"

            "<p style='margin: 16px 0 0 0;'>"
            f"(c) S. Friedrich 2025-2026 &middot; Version 2.6 &middot; "
            f"{time.strftime('%Y-%m')}"
            f"</p>"
            "<p style='margin: 4px 0 0 0;'>"
            f"<b>Freeware</b> &middot; Released under the <b>MIT License</b> "
            f"&middot; See LICENSE file for details"
            f"</p>"
        )

        text_label = QLabel(html)
        text_label.setTextFormat(Qt.TextFormat.RichText)
        text_label.setWordWrap(True)
        text_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        text_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # KEIN setStyleSheet hier - haette globale min/max-width Effekte gehabt.

        content_layout.addWidget(text_label)
        # Kein Stretch zwischen Label und OK-Button: der Stretch hat den
        # vertikalen Restplatz "gefressen" und konnte den Footer (Copyright +
        # Lizenz-Hinweis) abschneiden, wenn das Label sich an die Dialog-
        # Hoehe orientierte. Ohne Stretch nimmt das Label seine echte
        # sizeHint(), der OK-Button sitzt darunter.

        # --- Button-Bar unten in der Content-Spalte ---
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        button_box.accepted.connect(dlg.accept)
        content_layout.addWidget(button_box)

        # Content zuerst (nimmt Restbreite via stretch=1), Logo dann rechts oben.
        main_layout.addLayout(content_layout, 1)

        # --- Logo-Spalte (rechts oben) ---
        if os.path.exists(self.logo_path):
            logo_label = QLabel()
            pixmap = QPixmap(self.logo_path)
            logo_label.setPixmap(pixmap.scaled(
                96, 96,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            ))
            logo_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
            logo_label.setFixedWidth(110)  # Spalte fixiert -> kein Drift
            main_layout.addWidget(logo_label)

        dlg.exec()
    
    def reset_to_defaults(self):
        """
        Setzt alle Encoder-Einstellungen auf Standardwerte zurück.
        Löscht NICHT Input/Output-Pfade.
        """
        reply = QMessageBox.question(
            self, 
            'Reset to Defaults', 
            "Reset all settings to defaults?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:

            # die volle Auswahl haben bevor wir die Defaults setzen
            self.container_combo.blockSignals(True)
            for i in range(self.container_combo.count()):
                if self.container_combo.itemData(i) == 'mkv':
                    self.container_combo.setCurrentIndex(i)
                    break
            self.container_combo.blockSignals(False)

            # GPU: Erste Option (normalerweise CPU oder erste GPU)
            self.gpu_combo.setCurrentIndex(0)
            self.update_video_codec_options()
            self._refresh_audio_codec_combo()

            # Video Codec: Erste verfügbare Option nach GPU-Update
            self.video_codec_combo.setCurrentIndex(0)
            
            # Bitrate: 4.0 Mbps (Slider-Wert 40)
            self.bitrate_slider.setValue(40)

            self._user_modified_bitrate = False
            
            # Audio Codec: Copy
            self.audio_codec_combo.setCurrentIndex(0)
            self.audio_bitrate_line.setText("128")
            
            # Resolution Scaling: Aus
            self.downscale_to_1080p_checkbox.setChecked(False)
            

            self.mediathek_safe_checkbox.setChecked(False)
            

            self.force_av1_checkbox.setChecked(False)
            
            # Advanced Options
            self.preset_checkbox.setChecked(True)
            self.tune_checkbox.setChecked(True)
            self.multipass_checkbox.setChecked(False)

            self.strict_vbv_checkbox.setChecked(False)
            self.rc_lookahead_checkbox.setChecked(True)
            self.rc_lookahead_line.setText("32")
            
            # Audio Tracks: Alle aktivieren (falls vorhanden)
            for cb, _ in self.audio_track_checkboxes:
                cb.setChecked(True)
            
            # Command Preview aktualisieren
            self.update_command_preview()
            
            print("✅ Settings reset to defaults")
    
    def dragEnterEvent(self, event):
        """Handler für Drag Enter - akzeptiert Files und Folders"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()
    
    def dropEvent(self, event):
        """Handler für Drop - setzt Input File oder Batch Folder"""
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        
        urls = event.mimeData().urls()
        if not urls:
            event.ignore()
            return
        
        # Nur das erste Element verarbeiten
        first_url = urls[0]
        path = first_url.toLocalFile()
        
        if not path:
            event.ignore()
            return
        
        # Unterscheide: File vs Folder
        if os.path.isfile(path):
            # Einzelne Datei → Input File
            video_extensions = ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.ts', '.m2ts', '.m4v']
            ext = os.path.splitext(path)[1].lower()
            
            if ext in video_extensions:
                # WICHTIG: Cache sofort löschen, bevor async load startet
                self._clear_video_info_cache()
                
                self.input_line.setText(path)

                self._update_last_browse_dir(path)
                print(f"📁 Dropped video file: {os.path.basename(path)}")
                event.acceptProposedAction()
            else:
                QMessageBox.warning(
                    self, 
                    "Invalid File Type", 
                    f"Unsupported file extension: {ext}\n\nSupported: {', '.join(video_extensions)}"
                )
                event.ignore()
        
        elif os.path.isdir(path):
            # Ordner → Batch Folder
            # WICHTIG: Cache löschen für sauberen Batch-Start
            self._clear_video_info_cache()
            
            self.folder_line.setText(path)

            self._update_last_browse_dir(path)
            print(f"📁 Dropped batch folder: {os.path.basename(path)}")
            event.acceptProposedAction()
        
        else:
            event.ignore()

# ============================================================================
# Theme System (Light / Dark / System)
# ============================================================================
#
# Wir nutzen Qt6's nativen colorScheme-Mechanismus statt eigener Stylesheets.
# Auf Windows 10/11 zieht Qt damit automatisch die Personalisierungsfarben
# des Users - hell oder dunkel, je nachdem wie Windows konfiguriert ist.
#
# - THEME_LIGHT: Qt rendert mit dem hellen System-Farbschema
# - THEME_DARK:  Qt rendert mit dem dunklen System-Farbschema
# - THEME_SYSTEM (Default): Qt folgt der aktuellen Windows-Einstellung
#
# Das ist robust, konsistent und braucht KEINE eigenen QSS-Layer.

THEME_LIGHT = "light"
THEME_DARK = "dark"
THEME_SYSTEM = "system"


def _build_dark_palette() -> QPalette:
    """
    Baut eine explizite Dark-Mode-Palette.
    
    Hintergrund: Unter Windows wechselt Qt's setColorScheme() mit dem nativen
    Windows-Style die Palette zur Laufzeit nicht zuverlaessig. Mit dem
    plattformneutralen "Fusion"-Style + expliziter QPalette funktioniert der
    Live-Wechsel sauber. Farben angelehnt an den klassischen Qt-Dark-Mode.
    """
    p = QPalette()
    # Fenster / Dialog-Hintergrund
    p.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
    p.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
    # Eingabefelder, Listen, Tabellen
    p.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
    # Tooltips
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(53, 53, 53))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(220, 220, 220))
    # Allgemeiner Text
    p.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
    # Buttons
    p.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))
    # Hervorhebungen / Selektion
    p.setColor(QPalette.ColorRole.BrightText, QColor(255, 60, 60))
    p.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    p.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
    # Disabled-Zustaende (sonst werden disabled-Texte unleserlich)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
               QColor(127, 127, 127))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
               QColor(127, 127, 127))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText,
               QColor(127, 127, 127))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText,
               QColor(127, 127, 127))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight,
               QColor(80, 80, 80))
    return p


def _build_light_palette() -> QPalette:
    """
    Baut eine explizite Light-Mode-Palette.
    
    Wird mit dem Fusion-Style kombiniert. Sieht aus wie ein klassischer
    Windows-Hellmodus: weisse Eingabefelder, hellgraues Fenster, klare
    Trennung. Der Sinn von "selbst-malen" statt nativem Windows-Style:
    Wir koennen zwischen Light und Dark zur Laufzeit zuverlaessig
    umschalten, ohne dass Style-Internas haengen bleiben.
    """
    p = QPalette()
    # Fenster / Dialog-Hintergrund (klassisches Windows-Hellgrau)
    p.setColor(QPalette.ColorRole.Window, QColor(240, 240, 240))
    p.setColor(QPalette.ColorRole.WindowText, QColor(0, 0, 0))
    # Eingabefelder, Listen, Tabellen - reinweiss, klare Trennung
    p.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(245, 245, 245))
    # Tooltips
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 220))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
    # Allgemeiner Text
    p.setColor(QPalette.ColorRole.Text, QColor(0, 0, 0))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(120, 120, 120))
    # Buttons
    p.setColor(QPalette.ColorRole.Button, QColor(240, 240, 240))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(0, 0, 0))
    # Hervorhebungen / Selektion (Windows-Blau)
    p.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    p.setColor(QPalette.ColorRole.Link, QColor(0, 102, 204))
    p.setColor(QPalette.ColorRole.Highlight, QColor(0, 120, 215))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    # Disabled-Zustaende
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
               QColor(160, 160, 160))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
               QColor(160, 160, 160))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText,
               QColor(160, 160, 160))
    return p


def apply_theme(app: QApplication, theme: str) -> None:
    """
    Wendet das gewuenschte Farbschema global an.

    v2.6: Beide Modi nutzen den "Fusion"-Style mit expliziter QPalette.
    
    Warum nicht der native windowsvista-Style fuer Light?
    - windowsvista wendet zur Laufzeit gesetzte Paletten nicht zuverlaessig an
    - Wenn man von Dark (Fusion) zurueck nach Light (windowsvista) wechselt,
      bleiben Style-Internas haengen - die App sieht dann "haesslich" aus
    - Beide Modi mit Fusion = konsistent, verlaesslich, sauberer Live-Wechsel
    
    Fusion mit korrekter Light-Palette sieht nahezu identisch zum nativen
    Windows-Hellmodus aus: weisse Felder, hellgrauer Fenster-Hintergrund,
    Windows-Blau fuer Selektion. Schriftart bleibt Segoe UI (Windows-Default).
    """
    hints = app.styleHints()
    
    # Beide Modi nutzen Fusion - das ist der entscheidende Trick fuer
    # zuverlaessigen Live-Wechsel
    app.setStyle(QStyleFactory.create("Fusion"))
    
    if theme == THEME_DARK:
        app.setPalette(_build_dark_palette())
        hints.setColorScheme(Qt.ColorScheme.Dark)
    else:
        app.setPalette(_build_light_palette())
        hints.setColorScheme(Qt.ColorScheme.Light)



# ============================================================================
# Application Entry Point
# ============================================================================

def _read_theme_from_settings() -> str:
    """
    Liest das Theme einmalig aus den Settings BEVOR FFmpegGUI instanziiert
    wird. Damit kann die Palette schon gesetzt sein, bevor das MainWindow
    aufgebaut wird - sonst flackert es beim Start kurz hell auf, weil die
    Widgets erstmal mit Default-Palette gebaut werden.

    Identische Pfad-Logik wie in FFmpegGUI._setup_settings_path / load_settings,
    aber ohne den vollen Pfad-Migrations-Code (der laeuft eh gleich danach
    via load_settings).
    """
    if getattr(sys, 'frozen', False):
        program_dir = os.path.dirname(sys.executable)
    else:
        program_dir = os.path.dirname(os.path.abspath(__file__))
    settings_path = os.path.join(program_dir, 'Converter_settings.json')
    try:
        with open(settings_path, 'r') as f:
            settings = json.load(f)
        theme = settings.get('theme', THEME_LIGHT)
        if theme not in (THEME_LIGHT, THEME_DARK):
            theme = THEME_LIGHT
        return theme
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return THEME_LIGHT


if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_theme(app, _read_theme_from_settings())
    win = FFmpegGUI()
    win.show()
    sys.exit(app.exec())
