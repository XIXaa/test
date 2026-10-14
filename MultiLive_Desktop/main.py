"""MultiLive Desktop entry point."""
from __future__ import annotations

import sys


def main() -> int:
    try:
        import customtkinter  # noqa: F401
        from PIL import ImageTk  # noqa: F401
    except ImportError:
        print("缺少依赖，请运行: python -m pip install -r requirements.txt", file=sys.stderr)
        return 2
    from multilive.engine import binary_path
    import shutil

    if not shutil.which(binary_path("ffmpeg")) and not __import__("pathlib").Path(binary_path("ffmpeg")).is_file():
        print("警告：未找到 FFmpeg，请先安装并将 ffmpeg / ffplay 添加到 PATH。", file=sys.stderr)
    from multilive.gui import MultiLiveApp
    app = MultiLiveApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
