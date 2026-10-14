import json
from pathlib import Path

import pytest

from multilive.model import (Settings, Source, load_settings, load_source_list,
                             safe_display_url, save_settings, save_source_list,
                             validate_address)


def test_sources_validate_and_mask():
    assert validate_address('rtsp://192.168.1.3/live') == 'rtsp://192.168.1.3/live'
    assert validate_address('lavfi:testsrc=size=640x360:rate=30')
    for bad in ('', 'just some words', 'javascript:alert(1)', 'http://bad\r\n'):
        with pytest.raises(ValueError):
            validate_address(bad)
    assert 'secret' not in safe_display_url('rtsp://user:secret@host/live?token=secret#secret')
    assert safe_display_url('rtsp://user:secret@host/live?token=secret') == 'rtsp://host/live'


def test_round_trip_settings_and_sources(tmp_path: Path):
    one = Source.create('Test 1', 'https://example.org/play.m3u8?token=private')
    s = Settings(layout=9, sources=[one], assignments=[one.id] + [''] * 8)
    target = tmp_path / 'settings.json'
    save_settings(s, target)
    restored = load_settings(target)
    assert restored.layout == 9
    assert restored.sources[0].url == one.url
    assert restored.assignments[0] == one.id
    plist = tmp_path / 'list.json'
    save_source_list(s.sources, plist)
    assert load_source_list(plist)[0].name == 'Test 1'


def test_recover_corrupted_settings(tmp_path: Path):
    target = tmp_path / 'bad.json'
    target.write_text('{ broken', encoding='utf-8')
    assert load_settings(target).layout == 4
    target.write_text(json.dumps({'render_fps': -5, 'volume': 10000, 'layout': 5,
                                 'sources': [{'name': 'invalid', 'url': 'huh'}]}))
    result = load_settings(target)
    assert result.render_fps == 8
    assert result.volume == 100
    assert not result.sources
