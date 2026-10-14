"""Responsive CustomTkinter front-end. Only this module touches Tk objects."""
from __future__ import annotations

import os
import sys
import time
import re
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageTk

from .engine import AudioPlayer, Recorder, StreamWorker, binary_path
from .model import (Settings, Source, load_settings, load_source_list,
                    save_settings, save_source_list, safe_display_url)


BG = "#080F1D"
PANEL = "#111C30"
PANEL2 = "#15243C"
STROKE = "#233A57"
FOREGROUND = "#E6F1FC"
SUBTEXT = "#8FA5C2"
ACCENT = "#33D6C0"
BLUE = "#4E9DFA"
DANGER = "#EC697D"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def timestamp_name(prefix: str, suffix: str) -> str:
    # Keep generated paths portable on Windows, even for arbitrary source names.
    prefix = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", prefix).strip(" .") or "capture"
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}{suffix}"


def make_button(parent, label, action, *, width=92, subtle=False, color=None):
    return ctk.CTkButton(parent, text=label, command=action, width=width, height=32,
                         corner_radius=8, font=("Microsoft YaHei UI", 12),
                         fg_color=color or (PANEL2 if subtle else BLUE),
                         hover_color=STROKE if subtle else "#337ED5", text_color=FOREGROUND)


class VideoPlayer(ctk.CTkFrame):
    """Standard single-stream player with built-in playback controls.

    Renders the latest decoded frame of one live source and provides
    play/pause, volume, windowed fullscreen and OS fullscreen controls.
    The low-latency decode pipeline (StreamWorker) is reused unchanged.
    """

    def __init__(self, master, app: "MultiLiveApp"):
        super().__init__(master, fg_color=PANEL, corner_radius=10,
                         border_width=2, border_color=STROKE)
        self.app = app
        self.source: Source | None = None
        self.worker: StreamWorker | None = None
        self.paused = False
        self.render_mode = "fit"  # "fit" letterbox | "fill" adaptive cover
        self.last_number = 0
        self.photo: ImageTk.PhotoImage | None = None
        self.render_size: tuple[int, int] | None = None
        self._volume_id: str | None = None
        self._prev_volume = 70
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self.head_frame = ctk.CTkFrame(self, fg_color="transparent", height=35)
        self.head_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 2))
        self.head_frame.columnconfigure(0, weight=1)
        self.title = ctk.CTkLabel(self.head_frame, text="▣  播放窗口 · 未连接", anchor="w",
                                  font=("Microsoft YaHei UI", 12, "bold"), text_color=FOREGROUND)
        self.title.grid(row=0, column=0, sticky="ew")
        self.badge = ctk.CTkLabel(self.head_frame, text="IDLE", font=("Consolas", 10),
                                  text_color=SUBTEXT, width=46)
        self.badge.grid(row=0, column=1)

        self.video = tk.Canvas(self, width=60, height=60, bg="#070F1A", bd=0,
                               highlightthickness=0)
        self.video.grid(row=1, column=0, padx=8, pady=3, sticky="nsew")
        self.image_item = self.video.create_image(1, 1, anchor="center", state="hidden")
        self.placeholder = self.video.create_text(1, 1, text="双击左侧播放源\n即可在此观看直播",
                                                  font=("Microsoft YaHei UI", 12),
                                                  fill=SUBTEXT, justify="center")
        self.video.bind("<Configure>", self._on_canvas_resize)

        self.controls = ctk.CTkFrame(self, fg_color="transparent", height=42)
        self.controls.grid(row=2, column=0, sticky="ew", padx=9, pady=(2, 2))
        self.play_btn = make_button(self.controls, "▶ 播放", self.toggle_play, width=80, subtle=True)
        self.play_btn.pack(side="left", padx=(3, 3))
        make_button(self.controls, "↻ 追直播", self.jump_live, width=88, subtle=True).pack(
            side="left", padx=3)
        self.mute_btn = make_button(self.controls, "♪ 音量", self.toggle_mute, width=80, subtle=True)
        self.mute_btn.pack(side="left", padx=(14, 3))
        self.volume_slider = ctk.CTkSlider(self.controls, from_=0, to=100, width=104,
                                           number_of_steps=100, height=22,
                                           command=self._on_volume_slide,
                                           progress_color=ACCENT, button_color=ACCENT,
                                           button_hover_color=BLUE)
        self.volume_slider.set(self.app.settings.volume)
        self.volume_slider.pack(side="left", padx=3)
        # Four clearly distinct view modes; packed right = rightmost first.
        make_button(self.controls, "铺满窗口", lambda: self.app.set_view_mode("video_fs_win"),
                    width=80, subtle=True).pack(side="right", padx=(3, 3))
        make_button(self.controls, "窗口全屏", lambda: self.app.set_view_mode("theater"),
                    width=80, subtle=True).pack(side="right", padx=3)
        make_button(self.controls, "铺满全屏", lambda: self.app.set_view_mode("video_fs"),
                    width=80, subtle=True).pack(side="right", padx=3)
        make_button(self.controls, "全屏", lambda: self.app.set_view_mode("full"),
                    width=60, subtle=True).pack(side="right", padx=(3, 0))

        self.stats = ctk.CTkLabel(self, text="准备就绪", anchor="w",
                                  font=("Consolas", 10), text_color=SUBTEXT)
        self.stats.grid(row=3, column=0, sticky="ew", padx=12, pady=(1, 7))

        # Floating controls shown above the video layer in 铺满 modes.
        self.overlay = ctk.CTkFrame(self.video, fg_color="#0B1526", corner_radius=10)
        make_button(self.overlay, "退出铺满", self.app.exit_view, width=92, color=BLUE).pack(
            side="left", padx=(10, 4), pady=9)
        self.overlay_play = make_button(self.overlay, "▶ 播放", self.toggle_play,
                                        width=76, subtle=True)
        self.overlay_play.pack(side="left", padx=4, pady=9)
        make_button(self.overlay, "↻ 追直播", self.jump_live, width=88, subtle=True).pack(
            side="left", padx=(4, 10), pady=9)

    @staticmethod
    def _target_size(src_w: int, src_h: int, cw: int, ch: int, mode: str) -> tuple[int, int]:
        """Display size following the current canvas; no upscale cap so the
        picture always tracks window resizes (fixes the frozen-size bug)."""
        if cw <= 2 or ch <= 2 or src_w <= 0 or src_h <= 0:
            return (2, 2)
        if mode == "fill":
            factor = max(cw / src_w, ch / src_h)
        else:
            factor = min(cw / src_w, ch / src_h)
        return (max(2, int(src_w * factor)), max(2, int(src_h * factor)))

    def enter_immersive(self) -> None:
        """Hide player chrome; the video layer fills the whole area with an
        overlay control bar floating on top of it."""
        for widget in (self.head_frame, self.controls, self.stats):
            widget.grid_remove()
        self.video.grid_configure(padx=0, pady=0)
        self.configure(border_width=0, corner_radius=0)
        self.grid_configure(padx=0, pady=0)
        self.overlay.place(relx=0.5, rely=0.95, anchor="s")

    def exit_immersive(self) -> None:
        self.head_frame.grid()
        self.controls.grid()
        self.stats.grid()
        self.video.grid_configure(padx=8, pady=3)
        self.configure(border_width=2, corner_radius=10)
        self.grid_configure(padx=14, pady=0)
        self.overlay.place_forget()

    def _sync_play_buttons(self) -> None:
        text = "▶ 播放" if (self.paused or not self.worker) else "‖ 暂停"
        self.play_btn.configure(text=text)
        self.overlay_play.configure(text=text)

    def _on_canvas_resize(self, event):
        cx, cy = max(1, event.width // 2), max(1, event.height // 2)
        self.video.coords(self.image_item, cx, cy)
        self.video.coords(self.placeholder, cx, cy)

    def set_source(self, source: Source | None) -> None:
        """Seamlessly point the player at a source; same running source is a no-op."""
        if (self.source and source and self.source.id == source.id
                and self.worker and self.worker.is_running and not self.paused):
            return
        self.release()
        self.source = source
        if not source:
            self.title.configure(text="▣  播放窗口 · 未连接")
            self.badge.configure(text="IDLE", text_color=SUBTEXT)
            self.stats.configure(text="准备就绪")
            return
        self.title.configure(text=f"▣  {source.name}")
        self.video.itemconfigure(self.placeholder, text="正在建立视频连接…", state="normal")
        self.video.itemconfigure(self.image_item, state="hidden")
        self.badge.configure(text="LINK", text_color=BLUE)
        self.paused = False
        self._spawn_worker()
        self._sync_play_buttons()

    def release(self) -> None:
        if self.worker:
            self.worker.stop()
            self.worker = None
        self.source = None
        self.paused = False
        self.photo = None
        self.last_number = 0
        self.render_size = None
        self.video.itemconfigure(self.image_item, image="", state="hidden")
        self.video.itemconfigure(self.placeholder,
                                 text="双击左侧播放源\n即可在此观看直播", state="normal")
        self._sync_play_buttons()

    def restart_stream(self) -> None:
        """Recreate the decoder so changed settings take effect."""
        src = self.source
        self.release()
        if src:
            self.set_source(src)

    def _spawn_worker(self) -> None:
        width, height = self.app.player_dimensions()
        self.worker = StreamWorker(self.source.url, width, height, self.app.settings.mode,
                                   reconnect=self.app.settings.reconnect,
                                   rtsp_tcp=self.app.settings.rtsp_tcp,
                                   hwaccel=self.app.settings.hwaccel)
        self.worker.start()

    def toggle_play(self) -> None:
        if self.paused:
            if not self.source:
                self.app.set_notice("请先双击左侧播放源")
                return
            self.paused = False
            self.badge.configure(text="LINK", text_color=BLUE)
            self._spawn_worker()
            self._sync_play_buttons()
            self.app.set_notice("继续播放：重新接入直播流")
        else:
            if not self.worker:
                self.app.set_notice("当前没有正在播放的直播流")
                return
            self.worker.stop()
            self.paused = True
            self.last_number = 0
            self._sync_play_buttons()
            self.badge.configure(text="PAUSE", text_color="#F4BE75")
            self.stats.configure(text="已暂停 · 画面停留在最后一帧")
            self.app.set_notice("已暂停：停止拉流解码，点击播放继续")

    def jump_live(self) -> None:
        if self.worker and not self.paused:
            self.worker.restart_live()
            self.app.set_notice("正在重新接入直播前沿")
        else:
            self.app.set_notice("当前没有正在播放的直播流")

    def _on_volume_slide(self, value):
        # Debounce: sliding must not restart the audio backend for every tick.
        if self._volume_id:
            try:
                self.after_cancel(self._volume_id)
            except tk.TclError:
                pass
        self._volume_id = self.after(320, self._apply_volume)

    def _apply_volume(self):
        self._volume_id = None
        audio = self.app.audio
        self.app.settings.volume = int(float(self.volume_slider.get()))
        if audio.is_playing() and audio.url:
            try:
                audio.play(audio.url, self.app.settings.volume, self.app.settings.rtsp_tcp)
            except OSError:
                pass
        self.app._save_soon()
        self.app._refresh_audio_button()

    def toggle_mute(self):
        if self.app.settings.volume > 0:
            self._prev_volume = self.app.settings.volume
            self.volume_slider.set(0)
            self.mute_btn.configure(text="♪ 已静音", fg_color=STROKE)
        else:
            self.volume_slider.set(self._prev_volume)
            self.mute_btn.configure(text="♪ 音量", fg_color=PANEL2)
        self._apply_volume()

    def draw(self) -> None:
        if self.paused:
            return
        worker = self.worker
        if not worker:
            return
        latest = worker.get_latest(self.last_number)
        if latest is not None:
            try:
                pil = Image.frombytes("RGB", (latest.width, latest.height), latest.data)
                # A canvas (rather than a naturally sized label) avoids forcing
                # the player wider than the application window. Display size is
                # recomputed from the CURRENT canvas every frame (fit letterboxes,
                # fill covers and clips), so resizes are always tracked.
                cw, ch = max(2, self.video.winfo_width()-2), max(2, self.video.winfo_height()-2)
                size = self._target_size(latest.width, latest.height, cw, ch, self.render_mode)
                if size != pil.size:
                    pil = pil.resize(size, Image.Resampling.BILINEAR)
                if self.photo is None or size != self.render_size:
                    self.photo = ImageTk.PhotoImage(pil)
                    self.video.itemconfigure(self.image_item, image=self.photo, state="normal")
                    self.video.itemconfigure(self.placeholder, state="hidden")
                    self.render_size = size
                else:
                    self.photo.paste(pil)
                self.last_number = latest.number
                worker.mark_displayed()
            except (RuntimeError, tk.TclError, ValueError) as exc:
                self.stats.configure(text=f"渲染失败：{str(exc)[:50]}")
        status = worker.status()
        ready = status.text == "播放中"
        self.badge.configure(text="LIVE" if ready else "WAIT", text_color=ACCENT if ready else "#F4BE75")
        skipped = max(0, status.decoded - status.displayed)
        age_ms = int((time.monotonic() - status.last_frame_at) * 1000) if status.last_frame_at else 0
        age_text = f"本地帧龄 {age_ms}ms" if ready else status.text
        self.stats.configure(text=f"{status.fps:4.0f} fps   丢过期帧 {skipped}   {age_text}")


class MultiLiveApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("MultiLive Desktop | 极速追帧工作台")
        self.geometry("1490x880")
        self.minsize(1080, 670)
        self.configure(fg_color=BG)
        self.settings: Settings = load_settings()
        self.audio = AudioPlayer()
        self.recorder = Recorder()
        self._closing = False
        self._tick_i = 0
        self._view_mode = "normal"  # normal | full | theater | video_fs | video_fs_win
        self._autosave_id: str | None = None
        self._build_ui()
        self._rebuild_sources()
        self._restore_last_source()
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(120, self._tick)
        self.set_notice("就绪 · 双击左侧播放源即可开始观看")

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.left = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, width=300)
        self.left.grid(row=0, column=0, sticky="nsw")
        self.left.grid_propagate(False)
        self.left.grid_columnconfigure(0, weight=1)
        self.left.grid_rowconfigure(6, weight=1)
        logo = ctk.CTkFrame(self.left, fg_color="transparent")
        logo.grid(row=0, column=0, padx=20, pady=(24, 3), sticky="ew")
        ctk.CTkLabel(logo, text="◉ MULTILIVE", font=("Consolas", 25, "bold"),
                     text_color=ACCENT).pack(anchor="w")
        ctk.CTkLabel(self.left, text="LIVE EDGE  /  低延迟监看", font=("Consolas", 11),
                     text_color=SUBTEXT).grid(row=1, column=0, padx=20, sticky="w", pady=(0, 20))

        add = ctk.CTkFrame(self.left, fg_color=PANEL2, corner_radius=11)
        add.grid(row=2, column=0, padx=15, sticky="ew")
        add.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(add, text="新建播放源", anchor="w", font=("Microsoft YaHei UI", 13, "bold"),
                     text_color=FOREGROUND).grid(row=0, column=0, padx=13, pady=(12, 7), sticky="ew")
        self.name_entry = ctk.CTkEntry(add, placeholder_text="名称（可选）", height=33,
                                       fg_color=BG, border_color=STROKE)
        self.name_entry.grid(row=1, column=0, padx=12, pady=3, sticky="ew")
        self.url_entry = ctk.CTkEntry(add, placeholder_text="RTSP / FLV / HLS / 文件路径", height=35,
                                      fg_color=BG, border_color=STROKE)
        self.url_entry.grid(row=2, column=0, padx=12, pady=4, sticky="ew")
        make_button(add, "+  添加并播放", self.add_source, color="#1B887F", width=230).grid(
            row=3, column=0, padx=12, pady=(9, 13), sticky="ew")
        self.url_entry.bind("<Return>", lambda event: self.add_source())

        listhead = ctk.CTkFrame(self.left, fg_color="transparent")
        listhead.grid(row=3, column=0, sticky="ew", padx=19, pady=(22, 8))
        listhead.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(listhead, text="我的播放源", font=("Microsoft YaHei UI", 13, "bold"),
                     text_color=FOREGROUND).grid(row=0, column=0, sticky="w")
        self.source_counter = ctk.CTkLabel(listhead, text="0 条", text_color=SUBTEXT, font=("Consolas", 11))
        self.source_counter.grid(row=0, column=1, sticky="e")

        # The empty hint lives OUTSIDE the scrollable frame on purpose: putting
        # it inside made the text translate whenever the scrollbar was dragged.
        self.empty_hint = ctk.CTkLabel(self.left, text="暂无直播源\n请先在上方添加播放地址",
                                       text_color=SUBTEXT, font=("Microsoft YaHei UI", 12),
                                       justify="center")
        self.listbox = ctk.CTkScrollableFrame(self.left, fg_color="transparent", corner_radius=0)
        self.listbox.grid(row=6, column=0, sticky="nsew", padx=(12, 5), pady=(0, 4))
        self.listbox.grid_columnconfigure(0, weight=1)

        file_row = ctk.CTkFrame(self.left, fg_color="transparent")
        file_row.grid(row=7, column=0, padx=15, pady=(5, 17), sticky="ew")
        file_row.grid_columnconfigure((0, 1), weight=1)
        make_button(file_row, "↥ 导入列表", self.import_sources, width=126, subtle=True).grid(
            row=0, column=0, padx=(0, 4), sticky="ew")
        make_button(file_row, "↧ 导出列表", self.export_sources, width=126, subtle=True).grid(
            row=0, column=1, padx=(4, 0), sticky="ew")

        main = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)
        self.main = main

        header = ctk.CTkFrame(main, fg_color="transparent")
        header.grid(row=0, column=0, padx=20, pady=(17, 11), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="实时监看中心", font=("Microsoft YaHei UI", 22, "bold"),
                     text_color=FOREGROUND).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(header, text="● DECODER READY", font=("Consolas", 10),
                     text_color=ACCENT).grid(row=0, column=1, padx=14)
        self.header = header

        self.player = VideoPlayer(main, self)
        self.player.grid(row=1, column=0, sticky="nsew", padx=14, pady=0)

        action = ctk.CTkFrame(main, fg_color=PANEL, corner_radius=12)
        action.grid(row=2, column=0, padx=18, pady=(9, 7), sticky="ew")
        make_button(action, "◎ 当前源截图  S", self.capture, width=143, subtle=True).pack(side="left", padx=(12, 6), pady=10)
        self.record_btn = make_button(action, "● 开始录制  R", self.toggle_recording, width=144, color="#9D3E50")
        self.record_btn.pack(side="left", padx=5)
        self.audio_btn = make_button(action, "♫ 音频关闭", self.toggle_audio, width=114, subtle=True)
        self.audio_btn.pack(side="left", padx=5)
        make_button(action, "↻ 追直播  L", self.chase_live, width=112, color="#1B887F").pack(
            side="left", padx=5)
        make_button(action, "打开保存目录", self.open_capture_directory, width=114, subtle=True).pack(side="right", padx=(2, 14))
        self.action_bar = action

        # Unified switch area: chase profile now lives here with the toggles.
        tuning = ctk.CTkFrame(main, fg_color="transparent")
        tuning.grid(row=3, column=0, sticky="ew", padx=24, pady=(2, 4))
        row_a = ctk.CTkFrame(tuning, fg_color="transparent")
        row_a.pack(fill="x")
        ctk.CTkLabel(row_a, text="追帧策略", text_color=SUBTEXT).pack(side="left", padx=(6, 8))
        self.profile_control = ctk.CTkOptionMenu(row_a, width=128, height=32,
                                                 values=["极限追帧", "低延迟均衡", "稳定优先"],
                                                 fg_color=PANEL2, button_color=STROKE,
                                                 button_hover_color=BLUE,
                                                 command=self._change_profile)
        self.profile_control.pack(side="left", padx=3)
        self.profile_control.set({"extreme": "极限追帧", "balanced": "低延迟均衡", "stable": "稳定优先"}[self.settings.mode])
        ctk.CTkLabel(row_a, text="UI 帧率", text_color=SUBTEXT).pack(side="left", padx=(22, 6))
        self.render_choice = ctk.CTkOptionMenu(row_a, values=["12", "20", "24", "30", "45", "60"],
                                               command=self._set_fps, width=67, height=29,
                                               fg_color=PANEL2, button_color=STROKE)
        self.render_choice.set(str(self.settings.render_fps))
        self.render_choice.pack(side="left")
        row_b = ctk.CTkFrame(tuning, fg_color="transparent")
        row_b.pack(fill="x", pady=(6, 0))
        self.auto_switch = ctk.CTkSwitch(row_b, text="异常自动重连", onvalue=True, offvalue=False,
                                         variable=ctk.BooleanVar(value=self.settings.reconnect),
                                         command=self._reconfigure, width=100,
                                         progress_color=ACCENT, text_color=SUBTEXT)
        self.auto_switch.pack(side="left", padx=6)
        self.rtsp_switch = ctk.CTkSwitch(row_b, text="RTSP TCP", onvalue=True, offvalue=False,
                                         variable=ctk.BooleanVar(value=self.settings.rtsp_tcp),
                                         command=self._reconfigure, width=90,
                                         progress_color=ACCENT, text_color=SUBTEXT)
        self.rtsp_switch.pack(side="left", padx=6)
        self.gpu_switch = ctk.CTkSwitch(row_b, text="自动硬解(实验)", onvalue=True, offvalue=False,
                                        variable=ctk.BooleanVar(value=self.settings.hwaccel),
                                        command=self._reconfigure, width=100,
                                        progress_color=ACCENT, text_color=SUBTEXT)
        self.gpu_switch.pack(side="left", padx=6)
        self.hidden_switch = ctk.CTkSwitch(row_b, text="隐藏窗口自动停止解码", onvalue=True, offvalue=False,
                                           variable=ctk.BooleanVar(value=self.settings.pause_hidden),
                                           command=self._set_pause_hidden, width=100,
                                           progress_color=ACCENT, text_color=SUBTEXT)
        self.hidden_switch.pack(side="left", padx=6)
        # Notice text sits to the RIGHT of the switch group (no vertical stacking).
        self.notice = ctk.CTkLabel(row_b, text="", text_color=SUBTEXT,
                                   font=("Microsoft YaHei UI", 11), anchor="e")
        self.notice.pack(side="right", padx=(10, 6))
        self.tuning = tuning

    def _bind_keys(self):
        self.bind("<F11>", lambda e: self.toggle_fullscreen())
        self.bind("<Escape>", lambda e: self._escape())
        self.bind("<Control-o>", lambda e: self.import_sources())
        self.bind("<Control-s>", lambda e: self.export_sources())
        self.bind("<Key-f>", lambda e: self._shortcut(e, self.toggle_fullscreen))
        self.bind("<Key-r>", lambda e: self._shortcut(e, self.toggle_recording))
        self.bind("<Key-s>", lambda e: self._shortcut(e, self.capture))
        self.bind("<Key-l>", lambda e: self._shortcut(e, self.chase_live))

    @staticmethod
    def _is_typing(widget):
        return isinstance(widget, (tk.Entry, tk.Text)) or widget.winfo_class() in ("Entry", "Text")

    def _shortcut(self, event, callback):
        if not self._is_typing(event.widget):
            callback()

    def _escape(self):
        if self._view_mode != "normal":
            self.exit_view()

    def set_notice(self, note: str):
        self.notice.configure(text=note[:180])

    def player_dimensions(self) -> tuple[int, int]:
        return (960, 540)

    def _refresh_list_visibility(self):
        if self.settings.sources:
            self.empty_hint.grid_remove()
            self.listbox.grid()
        else:
            self.listbox.grid_remove()
            self.empty_hint.grid(row=6, column=0, sticky="nsew", padx=(12, 5), pady=(0, 4))

    def _restore_last_source(self):
        source_id = self.settings.assignments[0] if self.settings.assignments else ""
        source = next((s for s in self.settings.sources if s.id == source_id), None)
        if source:
            self.player.set_source(source)
            self.set_notice(f"已恢复上次播放：{source.name}")

    def play_source(self, source: Source):
        """Double-click entry: switch the single player window to this stream."""
        self.settings.assignments[0] = source.id
        self.player.set_source(source)
        self._save_soon()
        self.set_notice(f"正在连接：{source.name} · 双击其他源可无缝切换")
        if self.audio.is_playing() and self.audio.url != source.url:
            # Switching channels should not leave stale audio playing.
            self.audio.stop()
            self._refresh_audio_button()

    def add_source(self):
        try:
            source = Source.create(self.name_entry.get(), self.url_entry.get())
        except ValueError as exc:
            messagebox.showwarning("地址格式错误", str(exc), parent=self)
            return
        self.settings.sources.append(source)
        self._rebuild_sources()
        self.play_source(source)
        self.name_entry.delete(0, "end")
        self.url_entry.delete(0, "end")
        self._save_soon()

    def remove_source(self, sid: str):
        selected = next((s for s in self.settings.sources if s.id == sid), None)
        if not selected:
            return
        if not messagebox.askyesno("移除播放源", f"要删除“{selected.name}”吗？", parent=self):
            return
        if self.settings.assignments and self.settings.assignments[0] == sid:
            self.settings.assignments[0] = ""
            self.player.set_source(None)
        self.settings.sources = [s for s in self.settings.sources if s.id != sid]
        self._rebuild_sources()
        self._save_soon()

    def _rebuild_sources(self):
        for child in self.listbox.winfo_children():
            child.destroy()
        self.source_counter.configure(text=f"{len(self.settings.sources)} 条")
        self._refresh_list_visibility()
        if not self.settings.sources:
            return
        for i, source in enumerate(self.settings.sources):
            item = ctk.CTkFrame(self.listbox, fg_color=PANEL2, corner_radius=9)
            item.grid(row=i, column=0, sticky="ew", pady=4, padx=(2, 8))
            item.grid_columnconfigure(0, weight=1)
            name_label = ctk.CTkLabel(item, text=source.name, text_color=FOREGROUND,
                                      font=("Microsoft YaHei UI", 12, "bold"), anchor="w")
            name_label.grid(row=0, column=0, padx=(12, 3), pady=(7, 0), sticky="ew")
            url_label = ctk.CTkLabel(item, text=safe_display_url(source.url)[:37], text_color=SUBTEXT,
                                     font=("Consolas", 9), anchor="w")
            url_label.grid(row=1, column=0, padx=(12, 3), pady=(0, 8), sticky="ew")
            make_button(item, "▶", lambda s=source: self.play_source(s),
                        width=30, color="#146D71").grid(row=0, column=1, rowspan=2, padx=2)
            make_button(item, "×", lambda sid=source.id: self.remove_source(sid),
                        width=29, subtle=True).grid(row=0, column=2, rowspan=2, padx=(2, 7))
            for widget in (item, name_label, url_label):
                widget.bind("<Double-Button-1>", lambda e, s=source: self.play_source(s))

    def _change_profile(self, label: str):
        mode = {"极限追帧": "extreme", "低延迟均衡": "balanced", "稳定优先": "stable"}[label]
        if mode == self.settings.mode:
            return
        self.settings.mode = mode
        self._restart_players()
        self.set_notice(f"已启用：{label} · 重新连接以使解码参数生效")

    def _reconfigure(self):
        self.settings.reconnect = bool(self.auto_switch.get())
        self.settings.rtsp_tcp = bool(self.rtsp_switch.get())
        self.settings.hwaccel = bool(self.gpu_switch.get())
        self._restart_players()

    def _set_pause_hidden(self):
        self.settings.pause_hidden = bool(self.hidden_switch.get())
        self._save_soon()
        self.set_notice("已开启：窗口最小化时暂停画面刷新以节省资源"
                        if self.settings.pause_hidden else
                        "已关闭：窗口最小化时仍持续刷新画面")

    def _restart_players(self):
        if self.player.source:
            self.player.restart_stream()
        if self.audio.is_playing():
            self.audio.stop()
            self._refresh_audio_button()
        self._save_soon()

    def _set_fps(self, val: str):
        self.settings.render_fps = max(8, min(60, int(val)))
        self._save_soon()

    def chase_live(self):
        self.player.jump_live()

    def _active_source(self) -> Source | None:
        return self.player.source

    def _media_path(self, prefix: str, suffix: str) -> Path:
        if self.settings.capture_dir:
            base = Path(self.settings.capture_dir)
        else:
            videos = Path.home() / "Videos"
            base = (videos if videos.is_dir() else Path.home()) / "MultiLive"
        base.mkdir(parents=True, exist_ok=True)
        return base / timestamp_name(prefix, suffix)

    def capture(self):
        worker = self.player.worker
        if not worker or self.player.paused:
            self.set_notice("请先播放一个直播源再截图")
            return
        frame = worker.get_latest(-1)
        if not frame:
            self.set_notice("尚未获取视频帧")
            return
        try:
            name = self.player.source.name[:12] if self.player.source else "capture"
            output = self._media_path(f"screenshot_{name}", ".png")
            Image.frombytes("RGB", (frame.width, frame.height), frame.data).save(output)
        except (OSError, ValueError) as exc:
            messagebox.showerror("截图失败", str(exc), parent=self)
        else:
            self.set_notice(f"截图已保存：{output}")

    def toggle_recording(self):
        if self.recorder.active:
            old = self.recorder.path
            self.recorder.stop()
            self.record_btn.configure(text="● 开始录制  R", fg_color="#9D3E50")
            self.set_notice(f"录制正在结束并写入文件：{old}（请等待文件句柄释放）")
            return
        source = self._active_source()
        if not source:
            self.set_notice("请先播放一个直播源再录制")
            return
        if source.url.startswith("lavfi:"):
            self.set_notice("录制功能不支持内置测试信号")
            return
        try:
            target = self._media_path(f"record_{source.name[:12]}", ".mkv")
            self.recorder.start(source.url, target, self.settings.rtsp_tcp)
            self.record_btn.configure(text="■ 停止录制  R", fg_color="#D04560")
            self.set_notice(f"正在无损封装录制：{target.name}")
        except OSError as exc:
            messagebox.showerror("录制失败", str(exc), parent=self)

    def _refresh_audio_button(self):
        self.audio_btn.configure(text="♫ 音频开启" if self.audio.is_playing() else "♫ 音频关闭",
                                 fg_color="#226E74" if self.audio.is_playing() else PANEL2)

    def toggle_audio(self):
        if self.audio.is_playing():
            self.audio.stop()
            self._refresh_audio_button()
            return
        source = self._active_source()
        if not source:
            self.set_notice("请先播放一个直播源再开启音频")
            return
        try:
            self.audio.play(source.url, self.settings.volume, self.settings.rtsp_tcp)
        except OSError as exc:
            messagebox.showerror("音频启动失败", str(exc), parent=self)
        self._refresh_audio_button()

    def import_sources(self):
        filename = filedialog.askopenfilename(title="导入播放源", filetypes=[("JSON", "*.json")], parent=self)
        if not filename:
            return
        try:
            incoming = load_source_list(Path(filename))
            existing = {s.url for s in self.settings.sources}
            for source in incoming:
                if source.url not in existing:
                    if source.id in {s.id for s in self.settings.sources}:
                        source = Source.create(source.name, source.url)
                    self.settings.sources.append(source)
                    existing.add(source.url)
            self._rebuild_sources()
            self._save_soon()
            self.set_notice(f"导入完成 · 共 {len(self.settings.sources)} 条有效播放源")
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)

    def export_sources(self):
        filename = filedialog.asksaveasfilename(title="导出播放源", defaultextension=".json",
                                                initialfile="multilive_sources.json",
                                                filetypes=[("JSON", "*.json")], parent=self)
        if not filename:
            return
        try:
            save_source_list(self.settings.sources, Path(filename))
            self.set_notice(f"已导出 {len(self.settings.sources)} 条播放源（可能包含私有访问令牌，请妥善保存）")
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def open_capture_directory(self):
        try:
            path = self._media_path(".check", ".tmp").parent
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                __import__("subprocess").Popen(["open", str(path)])
            else:
                __import__("subprocess").Popen(["xdg-open", str(path)])
        except (OSError, ValueError) as exc:
            self.set_notice(f"无法打开目录：{exc}")

    def toggle_fullscreen(self):
        """全屏 button / F11: toggle the classic OS-fullscreen app view."""
        self.set_view_mode("full")

    def set_view_mode(self, mode: str):
        """Toggle between the four distinct view modes:
        full (OS fullscreen) / theater (窗口化全屏) /
        video_fs (铺满全屏) / video_fs_win (铺满窗口)."""
        if mode not in ("full", "theater", "video_fs", "video_fs_win"):
            return
        if self._view_mode == mode:
            self.exit_view()
            return
        self._enter_view(mode)

    def exit_view(self):
        """Restore the default layout from any view mode."""
        mode = self._view_mode
        if mode == "normal":
            return
        if mode in ("full", "video_fs"):
            self.attributes("-fullscreen", False)
        if mode in ("theater", "video_fs", "video_fs_win"):
            self._set_panels_hidden(False)
        if mode in ("video_fs", "video_fs_win"):
            self.player.render_mode = "fit"
            self.player.exit_immersive()
        self._view_mode = "normal"
        self.set_notice("已恢复常规视图")

    def _enter_view(self, mode: str):
        self._exit_quiet()
        self._view_mode = mode
        if mode == "full":
            self.attributes("-fullscreen", True)
            self.set_notice("全屏 · 按 Esc 或再次点击退出")
        elif mode == "theater":
            self._set_panels_hidden(True)
            self.set_notice("窗口化全屏 · 按 Esc 或再次点击退出")
        elif mode in ("video_fs", "video_fs_win"):
            self._set_panels_hidden(True)
            self.player.render_mode = "fill"
            self.player.enter_immersive()
            if mode == "video_fs":
                self.attributes("-fullscreen", True)
            self.set_notice("铺满全屏：视频自适应铺满屏幕 · 悬浮条可控制播放"
                            if mode == "video_fs" else
                            "铺满窗口：视频自适应铺满窗口 · 悬浮条可控制播放")

    def _exit_quiet(self):
        """Undo the effects of the current view mode without touching notices."""
        mode = self._view_mode
        if mode == "normal":
            return
        if mode in ("full", "video_fs"):
            self.attributes("-fullscreen", False)
        if mode in ("theater", "video_fs", "video_fs_win"):
            self._set_panels_hidden(False)
        if mode in ("video_fs", "video_fs_win"):
            self.player.render_mode = "fit"
            self.player.exit_immersive()
        self._view_mode = "normal"

    def _set_panels_hidden(self, hidden: bool):
        for widget in (self.left, self.header, self.action_bar, self.tuning):
            if hidden:
                widget.grid_remove()
            else:
                widget.grid()

    def _save_soon(self):
        if self._autosave_id:
            try:
                self.after_cancel(self._autosave_id)
            except tk.TclError:
                pass
        self._autosave_id = self.after(500, self._save_now)

    def _save_now(self):
        self._autosave_id = None
        try:
            save_settings(self.settings)
        except OSError as exc:
            self.set_notice(f"配置保存失败：{exc}")

    def _tick(self):
        if self._closing:
            return
        try:
            # Optional power saving: skip frame consumption while minimized.
            if not self.settings.pause_hidden or self.state() != "iconic":
                self.player.draw()
            self._tick_i += 1
            if self._tick_i % max(1, self.settings.render_fps // 2) == 0:
                self._refresh_audio_button()
                if self.recorder.proc and not self.recorder.active:
                    self.recorder.proc = None
                    self.record_btn.configure(text="● 开始录制  R", fg_color="#9D3E50")
                    self.set_notice("录制进程已退出，检查保存的 MKV 文件是否完整")
        finally:
            if not self._closing:
                self.after(max(16, int(1000/self.settings.render_fps)), self._tick)

    def on_close(self):
        if self._closing:
            return
        self._closing = True
        self.player.release()
        self.audio.stop()
        self.recorder.stop()
        self._save_now()
        self.destroy()
