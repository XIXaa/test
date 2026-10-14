"""Thread-isolated FFmpeg stream decoding with bounded, latest-frame-only buffers.

No Tk operation is made from any worker. Network source and decoder are always
separate from the GUI event loop. FFmpeg/FFplay binaries are resolved via PATH.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .model import safe_display_url


_IS_WIN = os.name == "nt"
_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if _IS_WIN else 0


@dataclass(frozen=True)
class Profile:
    label: str
    probe_size: str
    analyze_us: str
    stall_s: float
    retry_s: float


PROFILES = {
    "extreme": Profile("极限追帧", "32768", "100000", 8.0, 0.7),
    "balanced": Profile("低延迟均衡", "131072", "500000", 12.0, 1.2),
    "stable": Profile("稳定优先", "1048576", "1500000", 20.0, 2.0),
}


@dataclass(frozen=True)
class Frame:
    number: int
    data: bytes
    width: int
    height: int
    received_at: float


@dataclass(frozen=True)
class State:
    text: str = "未启动"
    decoded: int = 0
    displayed: int = 0
    reconnects: int = 0
    fps: float = 0.0
    last_frame_at: float = 0.0
    last_error: str = ""


def binary_path(name: str) -> str:
    exe = name + (".exe" if _IS_WIN else "")
    user_config = os.environ.get("MULTILIVE_FFMPEG_DIR", "")
    if user_config:
        path = Path(user_config) / exe
        if path.is_file():
            return str(path)
    return shutil.which(exe) or exe


def _run_options(profile: Profile, url: str, rtsp_tcp: bool, hwaccel: bool) -> list[str]:
    options = ["-hide_banner", "-loglevel", "error", "-nostdin"]
    remote = "://" in url
    if remote:
        options += ["-fflags", "nobuffer", "-flags", "low_delay",
                    "-probesize", profile.probe_size, "-analyzeduration", profile.analyze_us]
        if url.lower().startswith("rtsp:"):
            options += ["-rtsp_transport", "tcp" if rtsp_tcp else "udp"]
        if url.lower().startswith(("http:", "https:")):
            options += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "2"]
    elif not url.startswith("lavfi:"):
        options.append("-re")
    if hwaccel:
        options += ["-hwaccel", "auto"]
    if url.startswith("lavfi:"):
        options += ["-re", "-f", "lavfi", "-i", url[6:]]
    else:
        options += ["-i", url]
    return options


def decode_command(url: str, width: int, height: int, mode: str, *, rtsp_tcp: bool = True,
                   hwaccel: bool = False) -> list[str]:
    p = PROFILES.get(mode, PROFILES["extreme"])
    filt = (f"scale={width}:{height}:force_original_aspect_ratio=decrease:"
            f"flags=fast_bilinear,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x08111e,")
    filt += "format=rgb24"
    return [binary_path("ffmpeg"), *_run_options(p, url, rtsp_tcp, hwaccel),
            "-map", "0:v:0", "-an", "-sn", "-dn", "-filter_threads", "1",
            "-vf", filt, "-pix_fmt", "rgb24", "-fps_mode", "passthrough",
            "-f", "rawvideo", "pipe:1"]


class StreamWorker:
    """One bounded-frame worker per tile, nonblocking from GUI's perspective."""

    def __init__(self, url: str, width: int, height: int, mode: str = "extreme", *,
                 reconnect: bool = True, rtsp_tcp: bool = True, hwaccel: bool = False):
        self.url = url
        self.width = width
        self.height = height
        self.mode = mode
        self.reconnect = reconnect
        self.rtsp_tcp = rtsp_tcp
        self.hwaccel = hwaccel
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._restart = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._watcher: threading.Thread | None = None
        self._latest: Frame | None = None
        self._status = "准备连接"
        self._error = ""
        self._decoded = 0
        self._displayed = 0
        self._reconnects = 0
        self._last_at = 0.0
        self._started_at = 0.0
        self._fps_samples: deque[float] = deque(maxlen=80)
        self._errors: deque[str] = deque(maxlen=6)

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._restart.clear()
        self._thread = threading.Thread(target=self._loop, name="stream-decode", daemon=True)
        self._watcher = threading.Thread(target=self._watchdog_loop, name="stream-watchdog", daemon=True)
        self._thread.start()
        self._watcher.start()

    def restart_live(self) -> None:
        if self.is_running:
            self._restart.set()
            self._terminate_process()

    def stop(self) -> None:
        self._stop.set()
        self._restart.set()
        self._terminate_process()
        with self._lock:
            self._status = "已停止"

    def get_latest(self, last_number: int = 0) -> Frame | None:
        with self._lock:
            return self._latest if self._latest and self._latest.number != last_number else None

    def mark_displayed(self) -> None:
        with self._lock:
            self._displayed += 1

    def status(self) -> State:
        now = time.monotonic()
        with self._lock:
            while self._fps_samples and now - self._fps_samples[0] > 2.0:
                self._fps_samples.popleft()
            fps = (len(self._fps_samples) - 1) / (self._fps_samples[-1] - self._fps_samples[0]) \
                if len(self._fps_samples) > 1 and self._fps_samples[-1] > self._fps_samples[0] else 0.0
            return State(self._status, self._decoded, self._displayed,
                         self._reconnects, fps, self._last_at, self._error)

    def _update_status(self, value: str, error: str = "") -> None:
        with self._lock:
            self._status = value
            if error:
                self._error = error[:280]

    def _terminate_process(self) -> None:
        with self._lock:
            proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.kill()  # never block Tk while waiting for a stalled network decoder
            except (OSError, ProcessLookupError):
                pass

    def _watchdog_loop(self) -> None:
        while not self._stop.wait(1.0):
            with self._lock:
                started, last, proc = self._started_at, self._last_at, self._proc
            if proc is None or proc.poll() is not None or not started:
                continue
            if time.monotonic() - max(started, last) > PROFILES.get(self.mode, PROFILES["extreme"]).stall_s:
                self._update_status("超时重连")
                self._restart.set()
                self._terminate_process()

    def _drain_errors(self, proc: subprocess.Popen) -> None:
        if not proc.stderr:
            return
        for line in proc.stderr:
            val = line.decode("utf-8", "replace").strip()
            if val:
                # ffmpeg stderr may contain query tokens, usernames, credentials.
                scrubbed = val.replace(self.url, safe_display_url(self.url))
                with self._lock:
                    self._errors.append(scrubbed[:280])

    def _loop(self) -> None:
        attempt = 0
        frame_length = self.width * self.height * 3
        while not self._stop.is_set():
            if attempt:
                with self._lock:
                    self._reconnects += 1
            self._restart.clear()
            self._update_status("连接中" if attempt == 0 else "重新连接中")
            cmd = decode_command(self.url, self.width, self.height, self.mode,
                                 rtsp_tcp=self.rtsp_tcp, hwaccel=self.hwaccel)
            proc: subprocess.Popen | None = None
            frames_this_round = 0
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        bufsize=0, creationflags=_CREATION_FLAGS)
                with self._lock:
                    self._proc = proc
                    self._started_at = time.monotonic()
                    self._last_at = 0
                threading.Thread(target=self._drain_errors, args=(proc,), daemon=True).start()
                assert proc.stdout is not None
                while not self._stop.is_set() and not self._restart.is_set():
                    data = bytearray(frame_length)
                    view = memoryview(data)
                    remaining = frame_length
                    while remaining and not self._stop.is_set() and not self._restart.is_set():
                        got = proc.stdout.readinto(view[frame_length - remaining:])
                        if not got:
                            break
                        remaining -= got
                    if remaining:
                        break
                    now = time.monotonic()
                    with self._lock:
                        self._decoded += 1
                        self._last_at = now
                        self._fps_samples.append(now)
                        self._latest = Frame(self._decoded, bytes(data), self.width, self.height, now)
                        self._status = "播放中"
                    frames_this_round += 1
            except (FileNotFoundError, OSError, ValueError) as exc:
                self._update_status("解码失败", str(exc))
            finally:
                if proc:
                    self._terminate_process()
                    try:
                        proc.wait(timeout=2.0)
                    except (subprocess.TimeoutExpired, OSError):
                        pass
                    for f in (proc.stdout, proc.stderr):
                        try:
                            if f: f.close()
                        except OSError:
                            pass
                with self._lock:
                    if self._proc is proc:
                        self._proc = None
                    if self._errors:
                        self._error = self._errors[-1]
                        self._errors.clear()
            if self._stop.is_set():
                return
            if self.url.startswith("lavfi:") or (not "://" in self.url and not self._restart.is_set()):
                self._update_status("播放结束" if frames_this_round else "无法打开文件")
                return
            if not self.reconnect and not self._restart.is_set():
                self._update_status("连接断开")
                return
            self._update_status("追帧重连中" if self._restart.is_set() else "信号中断，准备重连")
            # Reset backoff after a successful interval; cap otherwise.
            attempt = 1 if frames_this_round > 20 else min(attempt + 1, 6)
            delay = min(12.0, PROFILES.get(self.mode, PROFILES["extreme"]).retry_s * (1.65 ** (attempt-1)))
            if self._restart.is_set():
                delay = 0.08
            self._stop.wait(delay)


class AudioPlayer:
    """Optional audio-only FFplay; separate audio connection can have sync offset."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self.url = ""

    def play(self, url: str, volume: int = 70, rtsp_tcp: bool = True) -> None:
        self.stop()
        cmd = [binary_path("ffplay"), "-hide_banner", "-loglevel", "quiet", "-nodisp",
               "-vn", "-autoexit", "-fflags", "nobuffer", "-flags", "low_delay",
               "-volume", str(max(0, min(100, volume)))]
        if url.lower().startswith("rtsp:"):
            cmd += ["-rtsp_transport", "tcp" if rtsp_tcp else "udp"]
        cmd += ["-i", url]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=_CREATION_FLAGS)
        self.url = url

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.kill()
            except OSError:
                pass
        self._proc = None
        self.url = ""

    def is_playing(self) -> bool:
        return self._proc is not None and self._proc.poll() is None


class Recorder:
    """Record the original compressed bitstream without costly re-encoding."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.path: Path | None = None
        self.url = ""

    def start(self, url: str, target: Path, rtsp_tcp: bool = True) -> None:
        if self.active:
            raise RuntimeError("已有录制任务正在运行")
        target.parent.mkdir(parents=True, exist_ok=True)
        cmd = [binary_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y"]
        if url.lower().startswith("rtsp:"):
            cmd += ["-rtsp_transport", "tcp" if rtsp_tcp else "udp"]
        cmd += ["-i", url, "-map", "0:v?", "-map", "0:a?", "-c", "copy", "-f", "matroska", str(target)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=_CREATION_FLAGS)
        self.path = target
        self.url = url

    @property
    def active(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        proc = self.proc
        self.proc = None
        self.url = ""
        if proc is None:
            return

        def finalize() -> None:
            if proc.poll() is None:
                try:
                    if proc.stdin:
                        proc.stdin.write(b"q\n")
                        proc.stdin.flush()
                    proc.wait(timeout=4)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        proc.kill()
                    except OSError:
                        pass
            try:
                if proc.stdin: proc.stdin.close()
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass

        threading.Thread(target=finalize, daemon=True, name="record-finalize").start()
