"""Collect the two built images and a Windows/Linux Python flasher ZIP.

This creates local release assets only; it never uploads or publishes them.
Extract the flasher ZIP beside immortalwrt-rootfs.ubi and installer.cpio.gz, install
requirements.txt, then run: python flash_openwrt.py
"""
import argparse
from pathlib import Path
import shutil
import zipfile

from common.paths import PROJECT, DIST

FLASH_FILES = (
    'tools/flash_openwrt.py', 'tools/serve_tftp.py',
    'tools/common/__init__.py', 'tools/common/boot.py', 'tools/common/network.py',
    'tools/common/ports.py', 'tools/common/ssh.py', 'tools/firmware/ubi.py',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', type=Path, default=DIST / 'firmware/immortalwrt-rootfs.ubi')
    parser.add_argument('--installer', type=Path, default=DIST / 'installer/installer.cpio.gz')
    parser.add_argument('--output', type=Path, default=DIST / 'release')
    args = parser.parse_args()
    for path in (args.firmware, args.installer):
        if not path.is_file():
            parser.error(f'Build artifact missing: {path}')
    args.output.mkdir(parents=True, exist_ok=True)
    for path, name in ((args.firmware, 'immortalwrt-rootfs.ubi'), (args.installer, 'installer.cpio.gz')):
        target = args.output / name
        if path.resolve() != target.resolve():
            shutil.copyfile(path, target)
    requirements = PROJECT / 'requirements.txt'
    if not requirements.exists():
        requirements = PROJECT / 'publish/requirements.txt'
    license_root = PROJECT if (PROJECT / 'LICENSE').is_file() else PROJECT / 'publish'
    bundle = args.output / 'rax3000h-flasher.zip'
    with zipfile.ZipFile(bundle, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FLASH_FILES:
            archive.write(PROJECT / name, name.removeprefix('tools/'))
        archive.write(requirements, 'requirements.txt')
        for name in ('LICENSE', 'LICENSES/MIT.txt'):
            archive.write(license_root / name, name)
    print(f'RELEASE_DIR={args.output.resolve()}\nFLASHER={bundle.resolve()}')


if __name__ == '__main__':
    main()
