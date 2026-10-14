import struct
from zipfile import ZipFile

from analyze_apk import analyze, dex_strings, parse_android_manifest


def test_reject_non_xml_and_invalid_dex():
    assert parse_android_manifest(b'not-xml')['format'] == 'unknown'
    assert dex_strings(b'fake') == []


def test_works_on_safe_fixture(tmp_path):
    apk = tmp_path / 'fixture.apk'
    with ZipFile(apk, 'w') as z:
        z.writestr('AndroidManifest.xml', b'unknown')
        z.writestr('classes.dex', b'unknown')
        z.writestr('assets/config.txt', b'fake')
    result = analyze(apk)
    assert result['entries_count'] == 3
    assert result['dex_files'] == ['classes.dex']
    assert result['manifest']['format'] == 'unknown'
