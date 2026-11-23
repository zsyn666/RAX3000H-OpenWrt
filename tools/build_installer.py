"""Build a small public temporary initramfs using the ImmortalWrt userspace toolchain."""
import argparse
import gzip
import shutil
import tempfile
from pathlib import Path

from firmware.cpio import cpio_file
from firmware.build_common import Build, TOOLS, copy_overlay
from common.paths import DEFAULT_OPENWRT, DIST, require_linux


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('openwrt_dir', type=Path, nargs='?', default=DEFAULT_OPENWRT)
    parser.add_argument('--output', type=Path, default=DIST / 'installer')
    args = parser.parse_args()
    require_linux()
    build = Build(args.openwrt_dir, args.output)
    build.utilities()
    with tempfile.TemporaryDirectory(prefix="rax-installer-", dir=build.checkout / "build_dir") as directory:
        root = Path(directory)
        for name in ('bin', 'sbin', 'usr/sbin', 'usr/bin', 'usr/local/factory', 'etc/dropbear', 'root/.ssh', 'tmp', 'dev', 'proc', 'sys', 'plugins', 'stock'):
            (root / name).mkdir(parents=True, exist_ok=True)
        build.binary(build.root / 'bin/busybox', 'bin/busybox', root)
        for directory in ('bin', 'sbin', 'usr/bin', 'usr/sbin'):
            for path in (build.root / directory).iterdir():
                if path.is_symlink() and path.resolve().name == 'busybox':
                    target = root / directory / path.name
                    target.symlink_to('/bin/busybox')
        build.binary(build.root / 'usr/sbin/dropbear', 'usr/sbin/dropbear', root)
        build.binary(build.root / 'sbin/kmodloader', 'sbin/kmodloader', root)
        (root / 'sbin/insmod').symlink_to('kmodloader')
        (root / 'usr/sbin/dropbearkey').symlink_to('dropbear')
        build.runtime_tools(root)
        if not (root / 'bin/sh').is_symlink():
            (root / 'bin/sh').symlink_to('busybox')
        (root / 'etc/passwd').write_text('root:x:0:0:root:/root:/bin/sh\n')
        (root / 'etc/group').write_text('root:x:0:\n')
        # Temporary installer SSH allows an empty root password (dropbear -B).
        (root / 'etc/shadow').write_text('root::0:0:99999:7:::\n')
        overlay = TOOLS / 'openwrt/flash-overlay'
        for name in ('usr/sbin/rax-flash', 'usr/sbin/rax-mac', 'etc/fw_env.config'):
            target = root / name
            shutil.copyfile(overlay / name, target)
            target.chmod(0o755 if name.startswith('usr/') else 0o644)
        for name in ('vendor-files.list', 'vendor-modules.list'):
            target = root / 'usr/share/rax3000h' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(TOOLS / 'openwrt' / name, target)
        copy_overlay(TOOLS / 'firmware/installer-overlay', root)
        (root / 'i').symlink_to('/ram-init')
        (root / 'usr/bin/rax-network').write_bytes(
            (TOOLS / 'openwrt/rax-network.sh').read_bytes().replace(b'\r\n', b'\n'))
        (root / 'usr/bin/rax-network').chmod(0o755)
        archive = bytearray()
        for ino, path in enumerate(sorted(root.rglob('*')), 10000):
            mode = path.lstat().st_mode
            data = path.readlink().as_posix().encode() if path.is_symlink() else path.read_bytes() if path.is_file() else b''
            archive += cpio_file(path.relative_to(root).as_posix(), data, mode, ino)
        archive += cpio_file('TRAILER!!!', b'', 0, ino + 1)
    output = build.output / 'installer.cpio.gz'
    output.write_bytes(gzip.compress(archive, mtime=0))
    print(f'INSTALLER={output} BYTES={output.stat().st_size}')


if __name__ == '__main__':
    main()
