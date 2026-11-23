"""Build a public, rootfs-only UBI image from the existing ImmortalWrt build.

Linux/WSL: python3 tools/build_openwrt_firmware.py OPENWRT_DIR --output DIR
No vendor binary, calibration data, device backup or credential is included.
"""
import argparse
import io
import shutil
import tarfile
import tempfile
from pathlib import Path

from firmware.rootfs import prepare_rootfs
from firmware.build_common import Build, TOOLS, copy_overlay, run, wrap_ubi
from common.paths import DEFAULT_OPENWRT, DIST, require_linux


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('openwrt_dir', type=Path, nargs='?', default=DEFAULT_OPENWRT)
    parser.add_argument('--rootfs', type=Path)
    parser.add_argument('--output', type=Path, default=DIST / 'firmware')
    args = parser.parse_args()
    require_linux()
    build = Build(args.openwrt_dir, args.output)
    build.utilities()
    source = args.rootfs or next((build.checkout / 'bin/targets/rax3000h/generic').glob('*default-rootfs.tar.gz'))
    payload = prepare_rootfs(source, vendor_links=True)
    squashfs = build.output / 'rootfs.squashfs'
    # /mnt/c without DrvFS metadata reports files as 0777 and ignores chmod.
    # Extract on the Linux build filesystem so SquashFS retains tar permissions;
    # rpcd refuses to load world-writable ucode scripts.
    with tempfile.TemporaryDirectory(prefix='rax-rootfs-', dir=build.checkout / 'build_dir') as directory:
        root = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
            archive.extractall(root, filter='fully_trusted')
        # ImmortalWrt's original init is retained, behind the minimal flash entry point.
        (root / 'sbin/init').rename(root / 'sbin/init.procd')
        shutil.copyfile(TOOLS / 'firmware/flash-init', root / 'sbin/init')
        (root / 'sbin/init').chmod(0o755)
        copy_overlay(TOOLS / 'openwrt/flash-overlay', root)
        build.runtime_tools(root)
        for name in ('plugins', 'usr/local/factory'):
            (root / name).mkdir(parents=True, exist_ok=True)
        # Presence of /overlay makes the stock /etc/chip wait for a second volume.
        if (root / 'overlay').exists():
            shutil.rmtree(root / 'overlay')
        (root / 'etc/rc.d/S96rax-boot').symlink_to('../init.d/rax-boot')
        (root / 'usr/share/rax3000h/vendor-files.list').write_bytes((TOOLS / 'openwrt/vendor-files.list').read_bytes())
        (root / 'etc/board.json').write_text('{"model":{"id":"hsan,luofu","name":"RAX3000H"},"network":{}}\n')
        run(build.host / 'mksquashfs4', root, squashfs, '-noappend', '-all-root', '-root-mode', '755', '-comp', 'xz', '-b', '131072', '-no-xattrs', '-processors', '2')
    image = build.output / 'immortalwrt-rootfs.ubi'
    wrap_ubi(build, squashfs, image)
    print(f'FIRMWARE={image} BYTES={image.stat().st_size}')


if __name__ == '__main__':
    main()
