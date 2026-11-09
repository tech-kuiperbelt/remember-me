"""Build the macOS app icon from the existing Windows brand ICO.

Source of truth: packaging/windows-beta/remember-me.ico (also used by the
Windows tray and Setup). This script does not redesign the brand mark; it
extracts the embedded sizes and adds the retina representations required by
macOS. Run it only when the source icon changes:

    python3 packaging/macos/make_icns.py
"""
import struct
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'packaging/windows-beta/remember-me.ico'
TARGET = ROOT / 'packaging/macos/RememberMe.icns'

# Canonical PNG chunks produced by iconutil for a macOS iconset. ic1* are the
# @2x slots; icp4/icp5 and ic07-ic10 are the standard 1x slots.
CHUNKS = (
    ('icp4', 16),
    ('ic11', 32),
    ('icp5', 32),
    ('ic12', 64),
    ('ic07', 128),
    ('ic13', 256),
    ('ic08', 256),
    ('ic14', 512),
    ('ic09', 512),
    ('ic10', 1024),
)


def png_bytes(image):
    from io import BytesIO
    output = BytesIO()
    image.save(output, format='PNG', optimize=True)
    return output.getvalue()


def pick(ico, size):
    available = sorted(ico.sizes())
    exact = [item for item in available if item == (size, size)]
    if exact:
        return ico.getimage(exact[0])
    smaller = [item for item in available if item[0] <= size]
    source = smaller[-1] if smaller else available[0]
    return ico.getimage(source).resize((size, size), Image.Resampling.LANCZOS)


def main():
    image = Image.open(SOURCE)
    ico = image.ico
    if ico is None:
        raise ValueError(f'Not an ICO file: {SOURCE}')
    payload = []
    for kind, size in CHUNKS:
        data = png_bytes(pick(ico, size))
        payload.append(kind.encode('ascii') + struct.pack('>I', len(data) + 8) + data)
    body = b''.join(payload)
    TARGET.write_bytes(b'icns' + struct.pack('>I', len(body) + 8) + body)
    print(f'Wrote {TARGET} ({TARGET.stat().st_size} bytes, {len(CHUNKS)} chunks)')


if __name__ == '__main__':
    main()
