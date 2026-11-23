"""Shared ImmortalWrt userspace tooling. Run builders using Linux Python (WSL)."""
import os
import shutil
import subprocess
import struct
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    subprocess.run([str(x) for x in args], check=True, **kwargs)


class Build:
    def __init__(self, checkout, output):
        self.checkout = checkout.resolve()
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.target = self.checkout / 'staging_dir/target-arm_cortex-a9_musl_eabi'
        self.root = self.checkout / 'build_dir/target-arm_cortex-a9_musl_eabi/root-rax3000h'
        self.host = self.checkout / 'staging_dir/host/bin'
        compilers = list((self.checkout / 'staging_dir').glob(
            'toolchain-arm_cortex-a9_gcc-*_musl_eabi/bin/arm-openwrt-linux-muslgnueabi-gcc'))
        if len(compilers) != 1:
            raise SystemExit(f'Expected one Cortex-A9 soft-float compiler, found {len(compilers)}')
        self.cc = compilers[0]
        if not (self.root / 'bin/busybox').is_file():
            raise SystemExit('Build the base userspace with tools/build_openwrt.py first')

    def utilities(self):
        env = os.environ.copy()
        env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
        with (self.output / 'utilities-build.log').open('w') as log:
            run('make', '-j4', 'package/utils/mtd-utils/compile',
                'package/boot/uboot-tools/compile', 'CONFIG_PACKAGE_ubi-utils=m',
                'CONFIG_PACKAGE_nand-utils=m', 'CONFIG_PACKAGE_uboot-envtools=m',
                'package/system/fstools/compile=', 'V=s', cwd=self.checkout,
                env=env, stdout=log, stderr=subprocess.STDOUT)
        self.mtd = next((self.checkout / 'build_dir/target-arm_cortex-a9_musl_eabi').glob('mtd-utils-*/ipkg-install/usr/sbin'))
        self.fwenv = next((self.checkout / 'build_dir/target-arm_cortex-a9_musl_eabi').glob('u-boot-*/tools/env/fw_printenv'))

    def binary(self, source, destination, root):
        """Copy an executable and its ELF interpreter/shared-library closure."""
        destination = root / destination.lstrip('/')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o755)
        # OpenWrt sstrip removes section headers. Read ELF program headers,
        # which remain sufficient for the loader and dependency discovery.
        elf = source.read_bytes()
        if elf[:6] != b'\x7fELF\x01\x01':
            raise ValueError(f'Expected ARM ELF32 little endian: {source}')
        phoff = struct.unpack_from('<I', elf, 28)[0]
        entsize, count = struct.unpack_from('<HH', elf, 42)
        headers = [struct.unpack_from('<8I', elf, phoff+i*entsize) for i in range(count)]
        needed = []
        for kind, offset, _, _, size, *_ in headers:
            if kind == 3:
                needed.append(elf[offset:offset+size].rstrip(b'\0').decode())
            if kind != 2:
                continue
            tags = [struct.unpack_from('<II', elf, pos) for pos in range(offset, offset+size, 8)]
            stringaddr = next((v for k,v in tags if k == 5), None)
            if stringaddr is None:
                continue
            stringoff = next(off+stringaddr-va for typ,off,va,_,sz,*_ in headers if typ == 1 and va <= stringaddr < va+sz)
            for tag,value in tags:
                if tag == 1:
                    start = stringoff+value
                    needed.append(elf[start:elf.index(0,start)].decode())
        for name in needed:
            relative = name.lstrip('/') if name.startswith('/') else 'lib/' + name
            if (root / relative).exists():
                continue
            candidates = [self.root / relative, self.target / relative, self.target / 'usr/lib' / name]
            candidate = next((p for p in candidates if p.exists()), None)
            if candidate is None:
                raise FileNotFoundError(f'ELF dependency {name} for {source}')
            self.binary(candidate, relative, root)

    def runtime_tools(self, root):
        for name in ('ubiformat', 'ubiattach', 'ubidetach', 'ubiblock', 'nanddump'):
            self.binary(self.mtd / name, 'usr/sbin/' + name, root)
        self.binary(self.fwenv, 'usr/sbin/fw_printenv', root)
        (root / 'usr/sbin/fw_setenv').symlink_to('fw_printenv')
        imagecheck = self.output / 'rax-image-check'
        run(self.cc, '-Os', '-s', '-Wall', '-Wextra', TOOLS / 'firmware/image_check.c', '-o', imagecheck)
        self.binary(imagecheck, 'usr/sbin/rax-image-check', root)
        configcheck = self.output / 'rax-config-check'
        run(self.cc, '-Os', '-s', '-Wall', '-Wextra',
            '-I' + str(self.target / 'usr/include'), TOOLS / 'firmware/config_check.c',
            '-L' + str(self.target / 'usr/lib'), '-lz', '-o', configcheck)
        self.binary(configcheck, 'usr/sbin/rax-config-check', root)


def copy_overlay(source, root):
    for path in source.rglob('*'):
        if not path.is_file():
            continue
        target = root / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            target.unlink()
        # Normalize Git checkout CRLF, including when building from /mnt/c.
        target.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
        target.chmod(0o755 if target.read_bytes().startswith(b'#!') else 0o644)


def wrap_ubi(build, squashfs, output):
    config = build.output / 'ubinize.cfg'
    config.write_text(f'[rootfs]\nmode=ubi\nimage={squashfs}\nvol_id=0\nvol_type=static\nvol_name=squashfs\n')
    run(build.host / 'ubinize', '-o', output, '-p', '131072', '-m', '2048', '-O', '2048', config)
    if output.stat().st_size > 27 * 1024 * 1024:
        raise ValueError('Image leaves insufficient room for UBI overhead/bad-block reserve')
