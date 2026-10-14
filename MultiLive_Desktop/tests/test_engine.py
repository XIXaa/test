import shutil
import time

import pytest

from multilive.engine import StreamWorker, decode_command


def test_decode_options():
    args = decode_command('rtsp://example.org/live', 320, 180, 'extreme', rtsp_tcp=True)
    assert '-rtsp_transport' in args
    assert args[args.index('-rtsp_transport') + 1] == 'tcp'
    assert args[-1] == 'pipe:1'
    assert 'nobuffer' in args
    assert '320:180' in ' '.join(args)


@pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='FFmpeg unavailable')
def test_real_ffmpeg_latest_frame_and_stop():
    worker = StreamWorker('lavfi:testsrc=size=640x360:rate=30', 160, 90)
    worker.start()
    deadline = time.monotonic() + 8
    frame = None
    try:
        while time.monotonic() < deadline:
            frame = worker.get_latest()
            if frame:
                break
            time.sleep(.05)
        assert frame is not None, worker.status()
        assert (frame.width, frame.height) == (160, 90)
        assert len(frame.data) == 160 * 90 * 3
        first_number = frame.number
        time.sleep(.3)
        later = worker.get_latest(first_number)
        assert later is not None and later.number > first_number
        assert worker.status().decoded >= later.number
        assert worker.status().displayed == 0  # UI hasn't painted any frame
    finally:
        worker.stop()
    deadline = time.monotonic() + 3
    while worker._thread and worker._thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.05)
    assert not worker.is_running
