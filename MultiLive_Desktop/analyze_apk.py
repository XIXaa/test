"""Offline, dependency-free APK structural auditor.

Run on the *real* user-supplied APK to generate verifiable evidence for
subsequent feature matching. Does not execute untrusted APK code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import BadZipFile, ZipFile


def u16(buf: bytes, off: int) -> int:
    return struct.unpack_from('<H', buf, off)[0]


def u32(buf: bytes, off: int) -> int:
    return struct.unpack_from('<I', buf, off)[0]


def decode_length8(data: bytes, i: int) -> tuple[int, int]:
    v = data[i]
    if v & 0x80:
        return ((v & 0x7f) << 8) | data[i + 1], i + 2
    return v, i + 1


def decode_length16(data: bytes, i: int) -> tuple[int, int]:
    v = u16(data, i)
    if v & 0x8000:
        return ((v & 0x7fff) << 16) | u16(data, i + 2), i + 4
    return v, i + 2


def string_pool(data: bytes, off: int) -> list[str]:
    ctype, header, size = struct.unpack_from('<HHI', data, off)
    if ctype != 1 or size < 28 or off + size > len(data):
        return []
    count, style_count, flags, start, style_start = struct.unpack_from('<IIIII', data, off + 8)
    if count > 200_000 or count * 4 + header > size:
        return []
    result = []
    for n in range(count):
        try:
            ptr = off + start + u32(data, off + header + n * 4)
            if flags & 0x100:
                _, ptr = decode_length8(data, ptr)
                bytelength, ptr = decode_length8(data, ptr)
                text = data[ptr:ptr + bytelength].decode('utf-8', 'replace')
            else:
                units, ptr = decode_length16(data, ptr)
                text = data[ptr:ptr + units*2].decode('utf-16-le', 'replace')
            result.append(text)
        except (UnicodeError, IndexError, struct.error):
            result.append('')
    return result


def parse_android_manifest(buf: bytes) -> dict:
    """Reads standard binary AXML string pool and start-element attributes."""
    if len(buf) < 8 or u16(buf, 0) != 3:
        return {'format': 'unknown', 'elements': [], 'error': '非标准二进制 AXML'}
    limit = min(len(buf), u32(buf, 4))
    offset = u16(buf, 2)
    strings: list[str] = []
    elements = []
    while offset + 8 <= limit:
        try:
            kind, hdr, size = struct.unpack_from('<HHI', buf, offset)
            if size < 8 or offset + size > limit:
                break
            if kind == 1:
                strings = string_pool(buf, offset)
            elif kind == 0x0102 and hdr >= 16 and size >= 36:
                idx = u32(buf, offset + 20)
                tag = strings[idx] if idx < len(strings) else f'unnamed_{idx}'
                attr_start, attr_size, attr_count = struct.unpack_from('<HHH', buf, offset + 24)
                attributes = {}
                for j in range(min(attr_count, 2000)):
                    at = offset + 16 + attr_start + j*attr_size
                    if attr_size < 20 or at + 20 > offset + size:
                        break
                    ns, key, raw = struct.unpack_from('<III', buf, at)
                    data_type = buf[at + 15]
                    value = u32(buf, at + 16)
                    name = strings[key] if key < len(strings) else f'attr_{key}'
                    if raw != 0xffffffff and raw < len(strings):
                        decoded = strings[raw]
                    elif data_type == 3 and value < len(strings):
                        decoded = strings[value]
                    elif data_type == 18:
                        decoded = str(bool(value)).lower()
                    elif data_type in (16, 17):
                        decoded = str(value) if data_type == 16 else f'0x{value:x}'
                    else:
                        decoded = f'0x{value:x}'
                    attributes[name] = decoded
                elements.append({'tag': tag, 'attributes': attributes})
            offset += size
        except (ValueError, IndexError, struct.error):
            break
    return {'format': 'android-binary-xml', 'strings_count': len(strings), 'elements': elements}


def read_uleb128(buf: bytes, i: int) -> tuple[int, int]:
    value = 0
    for shift in (0, 7, 14, 21, 28):
        byte = buf[i]
        i += 1
        value |= (byte & 0x7f) << shift
        if byte < 128:
            break
    return value, i


def dex_strings(data: bytes, limit: int = 400_000) -> list[str]:
    if len(data) < 112 or not data.startswith(b'dex\n'):
        return []
    count, pointer = struct.unpack_from('<II', data, 56)
    if count > limit or pointer + count*4 > len(data):
        return []
    result = []
    for i in range(count):
        try:
            pos = u32(data, pointer + i*4)
            _, pos = read_uleb128(data, pos)
            end = data.find(b'\0', pos, min(len(data), pos + 8192))
            if end < 0:
                continue
            result.append(data[pos:end].decode('utf-8', 'replace'))
        except (ValueError, IndexError, struct.error):
            continue
    return result


def redact_possible_url(candidate: str) -> str:
    try:
        url = urlsplit(candidate)
        if url.scheme in ('http','https','rtsp','rtmp','rtmps','wss','ws') and url.hostname:
            return f'{url.scheme}://{url.hostname}/[path-redacted]'
    except ValueError:
        pass
    return '[redacted]'


def analyze(path: Path) -> dict:
    with path.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    result = {'apk_filename': path.name, 'size_bytes': path.stat().st_size,
              'sha256': digest}
    with ZipFile(path) as package:
        members = package.namelist()
        result['entries_count'] = len(members)
        result['files_by_extension'] = dict(Counter(Path(n).suffix.lower() for n in members).most_common(20))
        result['native_libraries'] = [n for n in members if n.startswith('lib/') and n.endswith('.so')]
        result['assets'] = [n for n in members if n.startswith('assets/')][:200]
        result['dex_files'] = [n for n in members if re.fullmatch(r'classes\d*\.dex', n)]
        result['signature_files'] = [n for n in members if n.startswith('META-INF/') and n.upper().endswith(('.RSA', '.DSA', '.EC'))]
        if 'AndroidManifest.xml' in members:
            result['manifest'] = parse_android_manifest(package.read('AndroidManifest.xml'))
            ele = result['manifest'].get('elements', [])
            result['permissions'] = [x.get('attributes', {}).get('name') for x in ele if x.get('tag') == 'uses-permission']
            result['components'] = [x for x in ele if x.get('tag') in ('activity','service','receiver','provider')][:200]
            result['package_metadata'] = next((x['attributes'] for x in ele if x['tag'] == 'manifest'), {})
        matches = set()
        keywords = ('frame','live','latency','sync','player','buffer','追帧','直播',
                    '延迟','加速','倍速','硬解','rtsp','rtmp','flv','hls', 'danmaku', 'bilibili')
        urls = set()
        for name in result['dex_files'][:12]:
            for s in dex_strings(package.read(name)):
                if re.match(r'(?i)^(https?|rtsp|rtmp|wss?)://', s):
                    urls.add(redact_possible_url(s))
                if len(s) <= 130 and any(term in s.lower() for term in keywords):
                    if not re.search(r'[?@=&]|\d{10,}', s):
                        matches.add(s[:130])
        result['potential_function_strings'] = sorted(matches)[:400]
        result['possible_endpoint_hosts'] = sorted(urls)[:100]
    return result


def main():
    parser = argparse.ArgumentParser(description='只读 APK 结构与字符串审计；不会执行 APK')
    parser.add_argument('apk', type=Path)
    parser.add_argument('--out', type=Path, help='输出 JSON 路径')
    args = parser.parse_args()
    if not args.apk.is_file():
        parser.error(f'文件不存在: {args.apk}')
    try:
        report = analyze(args.apk)
    except (OSError, BadZipFile, struct.error) as exc:
        parser.error(f'分析失败: {exc}')
    data = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(data, encoding='utf-8')
        print('分析报告已生成:', args.out)
    else:
        print(data)


if __name__ == '__main__':
    main()
