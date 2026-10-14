"""Validation and crash-safe configuration storage for MultiLive Desktop."""
from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


VALID_MODES = ("extreme", "balanced", "stable")
SUPPORTED_NETWORK = {"http", "https", "rtsp", "rtmp", "rtmps", "udp", "tcp", "srt"}


def config_directory() -> Path:
    if os.environ.get("MULTILIVE_CONFIG_DIR"):
        return Path(os.environ["MULTILIVE_CONFIG_DIR"]).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "MultiLiveDesktop"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "multilive-desktop"


def validate_address(raw: str) -> str:
    if any(ord(c) < 32 for c in raw):
        raise ValueError("播放地址包含控制字符")
    raw = raw.strip().strip('"')
    if not raw:
        raise ValueError("播放地址不能为空或包含控制字符")
    if raw.startswith("lavfi:"):
        # Synthetic input for automated validation; never accept shell commands.
        if not re.fullmatch(r"lavfi:(testsrc2?|color)(=[A-Za-z0-9_=.,:/ -]{1,150})?", raw):
            raise ValueError("无效的测试源")
        return raw
    if re.match(r"^[A-Za-z]:[\\/]", raw) or raw.startswith("\\\\") or Path(raw).is_file():
        return raw
    parsed = urlsplit(raw)
    if parsed.scheme.lower() in SUPPORTED_NETWORK and (parsed.netloc or parsed.scheme in {"udp", "tcp"}):
        return raw
    raise ValueError("输入 http(s) / rtsp / rtmp / srt / udp 等视频地址，或本地文件路径")


def safe_display_url(url: str) -> str:
    """Strip credentials, query tokens and fragments from UI/log text."""
    try:
        p = urlsplit(url)
        if not p.scheme or not p.netloc:
            return Path(url).name
        hostname = p.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = "[" + hostname + "]"
        netloc = hostname + (f":{p.port}" if p.port else "")
        return urlunsplit((p.scheme, netloc, p.path, "", ""))
    except ValueError:
        return "[已隐藏地址]"


@dataclass
class Source:
    id: str
    name: str
    url: str

    @classmethod
    def create(cls, name: str, url: str) -> "Source":
        url = validate_address(url)
        name = name.strip()[:60] or safe_display_url(url)[:48] or "直播源"
        return cls(id=uuid.uuid4().hex[:12], name=name, url=url)

    @classmethod
    def from_dict(cls, val: dict[str, Any]) -> "Source":
        if not isinstance(val, dict):
            raise ValueError("播放源应为 JSON 对象")
        item = cls.create(str(val.get("name", "")), str(val.get("url", "")))
        item.id = str(val.get("id", ""))[:36] or item.id
        return item


@dataclass
class Settings:
    mode: str = "extreme"
    layout: int = 4
    render_fps: int = 24
    reconnect: bool = True
    hwaccel: bool = False
    rtsp_tcp: bool = True
    pause_hidden: bool = True
    audio: bool = False
    volume: int = 70
    capture_dir: str = ""
    assignments: list[str] = field(default_factory=lambda: ["" for _ in range(9)])
    sources: list[Source] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Settings":
        cfg = cls()
        cfg.mode = raw.get("mode", "extreme") if raw.get("mode") in VALID_MODES else "extreme"
        cfg.layout = raw.get("layout", 4) if raw.get("layout") in (1, 2, 4, 9) else 4
        try:
            cfg.render_fps = max(8, min(60, int(raw.get("render_fps", 24))))
            cfg.volume = max(0, min(100, int(raw.get("volume", 70))))
        except (TypeError, ValueError):
            pass
        for flag in ("reconnect", "hwaccel", "rtsp_tcp", "audio", "pause_hidden"):
            if isinstance(raw.get(flag), bool):
                setattr(cfg, flag, raw[flag])
        cfg.capture_dir = str(raw.get("capture_dir", ""))[:1024]
        assignments = raw.get("assignments", [])
        if isinstance(assignments, list):
            cfg.assignments = [str(x)[:36] for x in assignments[:9]]
            cfg.assignments.extend([""] * (9 - len(cfg.assignments)))
        seen = set()
        for item in raw.get("sources", []):
            try:
                parsed = Source.from_dict(item)
            except (ValueError, TypeError, AttributeError):
                continue
            if parsed.id not in seen and len(cfg.sources) < 300:
                cfg.sources.append(parsed)
                seen.add(parsed.id)
        return cfg

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_settings(path: Path | None = None) -> Settings:
    path = path or config_directory() / "settings.json"
    try:
        val = json.loads(path.read_text(encoding="utf-8"))
        return Settings.from_dict(val) if isinstance(val, dict) else Settings()
    except (OSError, ValueError, UnicodeError):
        return Settings()


def save_settings(cfg: Settings, path: Path | None = None) -> None:
    path = path or config_directory() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fout:
            json.dump(cfg.to_dict(), fout, ensure_ascii=False, indent=2)
            fout.flush()
            os.fsync(fout.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save_source_list(sources: list[Source], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(x) for x in sources], ensure_ascii=False, indent=2), encoding="utf-8")


def load_source_list(path: Path) -> list[Source]:
    val = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(val, list):
        raise ValueError("播放列表必须为 JSON 数组")
    result = []
    for row in val[:300]:
        result.append(Source.from_dict(row))
    return result
