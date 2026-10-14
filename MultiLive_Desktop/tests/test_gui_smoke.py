"""Optional smoke test for systems with CustomTkinter installed."""
import importlib.util
import os
import sys

import pytest


@pytest.mark.skipif(importlib.util.find_spec('customtkinter') is None,
                    reason='GUI extra dependency not installed in test container')
@pytest.mark.skipif(sys.platform.startswith('linux') and not os.environ.get('DISPLAY'),
                    reason='Tk display server unavailable')
def test_customtkinter_smoke(tmp_path, monkeypatch):
    monkeypatch.setenv('MULTILIVE_CONFIG_DIR', str(tmp_path))
    from multilive.gui import MultiLiveApp, VideoPlayer
    from multilive.model import Source
    app = MultiLiveApp()
    try:
        app.update()
        # Empty-source hint must live outside the scrollable frame so that
        # dragging the scrollbar cannot translate it.
        assert app.empty_hint.master is not app.listbox
        assert app.empty_hint.winfo_ismapped()
        assert not app.listbox.winfo_ismapped()
        # Notice text sits right of the switch group, not stacked below it.
        assert app.notice.winfo_manager() == 'pack'
        assert app.notice.master is not app.main

        source = Source.create('smoke', 'lavfi:testsrc2=size=320x240:rate=15')
        app.settings.sources.append(source)
        app._rebuild_sources()
        app.update()
        assert not app.empty_hint.winfo_ismapped()

        # Double-click path plays the source in the single player window.
        app.play_source(source)
        app.update()
        assert app.player.source is source
        assert app.player.worker is not None

        app.player.toggle_play()
        app.update()
        assert app.player.paused
        assert app.player.overlay_play.cget('text').startswith('▶')
        app.player.toggle_play()
        app.update()
        assert not app.player.paused
        assert app.player.overlay_play.cget('text').startswith('‖')

        # Resize adaptivity: no upscale cap, video tracks the canvas in both
        # letterbox and fill modes (regression for the frozen-size bug).
        assert VideoPlayer._target_size(960, 540, 1920, 1080, 'fit') == (1920, 1080)
        assert VideoPlayer._target_size(960, 540, 800, 600, 'fit') == (800, 450)
        w, h = VideoPlayer._target_size(960, 540, 800, 600, 'fill')
        assert h == 600 and w > 800

        # All four view modes are distinct and toggle/restore cleanly.
        for mode in ('theater', 'full', 'video_fs', 'video_fs_win'):
            app.set_view_mode(mode)
            app.update()
            assert app._view_mode == mode
            if mode in ('theater', 'video_fs', 'video_fs_win'):
                assert not app.left.winfo_ismapped()
            else:
                assert app.left.winfo_ismapped()
            assert bool(app.attributes('-fullscreen')) == (mode in ('full', 'video_fs'))
            if mode in ('video_fs', 'video_fs_win'):
                assert app.player.render_mode == 'fill'
                assert app.player.overlay.winfo_ismapped()
                assert not app.player.controls.winfo_ismapped()
            else:
                assert app.player.render_mode == 'fit'
                assert not app.player.overlay.winfo_ismapped()
                assert app.player.controls.winfo_ismapped()

        app.exit_view()
        app.update()
        assert app._view_mode == 'normal'
        assert app.left.winfo_ismapped()
        assert app.player.controls.winfo_ismapped()
        assert not app.player.overlay.winfo_ismapped()
        assert not bool(app.attributes('-fullscreen'))
    finally:
        app.on_close()
