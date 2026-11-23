"""Install a rootfs UBI through a temporary installer booted from U-Boot.

No stock Linux login is required. Connect serial and Ethernet, then power-cycle
the router when prompted. No arguments starts an interactive installer.
Only rootfs A is flashed; device assets and boot selection are also persisted.
Never writes the kernel or bootloader program.
"""
import argparse
import ipaddress
import re
import secrets
import socket
import struct
import sys
import time
from datetime import datetime
from pathlib import Path

import paramiko

from common.boot import Console, UBOOT, LINUX, load_image
from common.network import adapter_info, select_interface, wait_address, check_tftp_bind
from common.ports import select_port, print_devices
from common.ssh import exchange
from firmware.ubi import extract, wrap

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT = SCRIPT_DIR.parent if SCRIPT_DIR.name == 'tools' else SCRIPT_DIR


def prompt_path(label, default):
    value = input(f'{label} [{default}]: ').strip().strip('"')
    return Path(value).expanduser() if value else default


def release_file(name, directory, interactive=False):
    beside_tools = PROJECT / name
    built = PROJECT / 'dist' / directory / name
    for path in (beside_tools, built):
        if path.is_file():
            return path
    # ZIP tools may be extracted into an extra directory, or images into a
    # neighboring directory. Search one level only, using exact release names.
    candidates = set()
    for base in (PROJECT, PROJECT.parent):
        if (base / name).is_file():
            candidates.add((base / name).resolve())
        for child in base.iterdir():
            if child.is_dir() and not child.name.startswith('.') and (child / name).is_file():
                candidates.add((child / name).resolve())
    candidates = sorted(candidates)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        if not interactive:
            raise RuntimeError(f'Multiple {name} files found; specify the image path / --installer:\n'
                               + '\n'.join(str(path) for path in candidates))
        print(f'Multiple {name} files found:')
        for index, path in enumerate(candidates, 1):
            print(f'  {index}: {path}')
        answer = input(f'Select image [1-{len(candidates)}]: ').strip()
        if not answer.isdigit() or not 1 <= int(answer) <= len(candidates):
            raise RuntimeError('Invalid image selection')
        return candidates[int(answer) - 1]
    return built


def memory_bytes(output):
    result = bytearray()
    for line in output.decode('ascii', 'replace').splitlines():
        match = re.match(r'^\s*[0-9a-fA-F]{8}: ((?:[0-9a-fA-F]{2} ){1,16})', line)
        if match:
            result += bytes.fromhex(match[1])
    return result


def installer_command(console, command, timeout=10):
    # BusyBox's serial input can truncate long lines. Use short commands,
    # a slow UART cadence, and an output marker (never a prompt/echo match).
    marker = 'RAX_CMD_' + secrets.token_hex(6)
    line = command + f"; printf '\\n{marker}:%s\\n' \"$?\""
    if len(line.encode()) > 256:
        raise ValueError('Installer serial command exceeds 256 bytes')
    console.send(line, byte_delay=0.008)
    pattern = re.compile(rb'(?:^|[\r\n])' + marker.encode() + rb':([0-9]+)\r?\n')
    output = console.wait(pattern.search, timeout)
    if int(pattern.search(output)[1]):
        raise RuntimeError(f'Installer command failed: {command}\n{output.decode(errors="replace")}')
    return output


def start_installer_ssh(console, router, prefix):
    installer_command(console, f'ip addr add {router}/{prefix} dev br-lan')
    installer_command(console, 'ip link set br-lan up')
    installer_command(console, 'dropbear -B -r /tmp/installer-host-key')


def installer_ssh(host, router, index):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    if sys.platform == 'win32':
        sock.setsockopt(socket.IPPROTO_IP, 31, struct.pack('!I', index))
    sock.bind((host, 0))
    sock.connect((router, 22))
    client = paramiko.SSHClient()
    # Dropbear accepts empty accounts through SSH's "none" method. Sending
    # an empty "password" request is rejected even with -B enabled.
    transport = paramiko.Transport(sock)
    client._transport = transport
    try:
        transport.start_client(timeout=10)
        transport.auth_none('root')
    except Exception:
        client.close()
        raise
    return client


def receive_file(client, command, path, size=None):
    channel = client.get_transport().open_session(timeout=10)
    channel.exec_command(command)
    channel.shutdown_write()
    total = 0
    errors = bytearray()
    with path.open('wb') as output:
        while True:
            if channel.recv_ready():
                data = channel.recv(262144)
                output.write(data)
                total += len(data)
            if channel.recv_stderr_ready():
                errors.extend(channel.recv_stderr(65536))
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            time.sleep(.002)
    code = channel.recv_exit_status()
    channel.close()
    if code or size is not None and total != size:
        raise RuntimeError(f'Backup failed: {path.name} bytes={total}, {errors.decode(errors="replace")}')


def backup(client, directory):
    directory.mkdir(parents=True, exist_ok=True)
    mtd = exchange(client, 'cat /proc/mtd').decode()
    for number, hexsize, name in re.findall(r'mtd(\d+): ([0-9a-f]+) [0-9a-f]+ "([^"]+)"', mtd):
        path = directory / f'mtd{number}-{name}.bin'
        if path.exists():
            raise FileExistsError(path)
        print(f'BACKUP {name}', flush=True)
        receive_file(client, f'nanddump -q --omitoob --bb=padbad /dev/mtd{number}', path, int(hexsize,16))
        if name in ('rootfsa', 'rootfsb'):
            restore = directory / f'{name}-restore.ubi'
            restore.write_bytes(wrap(extract(path.read_bytes())))
    print(f'BACKUP_COMPLETE {directory}', flush=True)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path, nargs='?', help='Rootfs UBI image')
    parser.add_argument('--installer', type=Path)
    parser.add_argument('--port', help='Serial device; autodetect prefers CH340/CH341')
    parser.add_argument('--interface', help='Ethernet adapter name; auto-select or prompt if omitted')
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--clean-vendor-plugins', action='store_true')
    parser.add_argument('--yes', action='store_true', help='Accept installation without interactive confirmation')
    parser.add_argument('--list-devices', action='store_true', help='List serial and network adapters, then exit')
    parser.add_argument('--prepare-only', action='store_true', help='Boot installer and extract assets; do not write flash')
    args = parser.parse_args()
    if args.list_devices:
        print_devices()
        return
    interactive = len(sys.argv) == 1
    if interactive:
        print('RAX3000H installation — connect 3.3 V UART (GND/TX/RX, no VCC) and Ethernet to any LAN port.')
        print('Keep the router powered on. Unplug and reconnect power only when prompted.')
        args.image = prompt_path('Rootfs UBI', release_file('immortalwrt-rootfs.ubi', 'firmware', interactive=True))
    elif args.image is None:
        parser.error('image is required, or run without arguments for interactive installation')
    if args.installer is None:
        default_installer = release_file('installer.cpio.gz', 'installer', interactive=interactive)
        args.installer = prompt_path('Temporary installer', default_installer) if interactive else default_installer
    if args.clean_vendor_plugins and not args.backup:
        parser.error('--clean-vendor-plugins requires --backup for the original plugin files')
    payload = args.image.read_bytes()
    if payload[:4] != b'UBI#' or len(payload)>27*1024*1024:
        parser.error('Expected rootfs UBI image <=27 MiB')
    if not args.installer.is_file():
        parser.error(f'Installer not found: {args.installer}; supply --installer')
    args.port = select_port(args.port)
    args.interface = select_interface(args.interface)
    host, router, mask = wait_address(args.interface)
    check_tftp_bind(host)
    if interactive:
        default_backup = PROJECT / 'backup'
        answer = input(f'Backup directory [{default_backup}] (enter - to skip): ').strip()
        args.backup = None if answer == '-' else Path(answer or default_backup).expanduser()
    print(f'IMAGE={args.image}\nINSTALLER={args.installer}\nSERIAL={args.port}\nETHERNET={args.interface}')
    if args.prepare_only:
        print('Temporary installer only: no persistent asset or flash writes.')
    else:
        print('Installation writes rootfs A, saves this device\'s assets to plugins, selects A and boots it.')
        print('Kernel, bootloader program and factory calibration partition are retained.')
    if not args.yes:
        if not sys.stdin.isatty():
            parser.error('Noninteractive installation requires --yes')
        if input('Continue installation? [y/N]: ').strip().lower() not in ('y', 'yes'):
            print('Cancelled')
            return
    logs = PROJECT / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    logfile = logs / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-install.log')
    print(f'SERIAL_LOG {logfile}', flush=True)
    with logfile.open('wb', buffering=0) as log, Console(args.port, log) as console:
        print('Serial is listening. Unplug and reconnect router power now; waiting for U-Boot...', flush=True)
        while True:
            try:
                console.wait(UBOOT.search, 60, interrupt=True)
                break
            except TimeoutError:
                print('Still waiting: unplug and reconnect router power.', flush=True)
        console.command('mtd read kernela 83200000')
        header = memory_bytes(console.command('md.b 83200000 40'))
        if len(header)!=64 or header[:4] != bytes.fromhex('27051956'):
            raise RuntimeError('Cannot read the device kernel uImage header')
        size = struct.unpack_from('>I', header, 12)[0]
        if not 1048576 < size < 8388544:
            raise RuntimeError('Unexpected kernel size')
        address = wait_address(args.interface)
        load_image(console, args.installer, address, args.interface)
        host, router, mask = wait_address(args.interface)
        console.command('setenv bootflag b')  # temporary; leave normal A boot register during installer
        result = console.command(f'fdt addr {0x83200000+64+size:x}')
        if b'Unknown command' in result or b'FDT_ERR' in result:
            raise RuntimeError('Cannot select the in-RAM device tree')
        cmdline = f'cma=0 console=ttyS0,115200 initrdmem=0x85000000,0x{args.installer.stat().st_size:x} rdinit=/i'
        if args.force:
            cmdline += ' rax_force=1'
        # This vendor U-Boot does not parse a quoted multiword fdt set value
        # correctly. Patch only the existing property bytes, without resizing
        # the DT or moving the vendor metadata appended after it.
        console.command('fdt get addr rax_bootargs /chosen bootargs')
        console.command('fdt get size rax_bootcap /chosen bootargs')
        props = console.command('printenv rax_bootargs rax_bootcap')
        addr_match = re.search(rb'rax_bootargs=(?:0x)?([0-9a-fA-F]+)',props)
        cap_match = re.search(rb'rax_bootcap=(?:0x)?([0-9a-fA-F]+)',props)
        if not addr_match or not cap_match:
            raise RuntimeError('Cannot locate existing device-tree bootargs')
        bootaddr,capacity = int(addr_match[1],16),int(cap_match[1],16)
        data = cmdline.encode()+b'\0'
        if len(data)>capacity or not 0x83700000 <= bootaddr < 0x83a00000:
            raise RuntimeError('Device-tree bootargs bounds mismatch')
        console.command(f'mw.b {bootaddr:x} 0 {capacity:x}')
        data += b'\0'*(-len(data)%4)
        for offset in range(0,len(data),4):
            value = int.from_bytes(data[offset:offset+4],'little')
            console.command(f'mw.l {bootaddr+offset:x} {value:x}')
        console.send('bootfip 83200000 0')
        output = console.wait(lambda b: b'RAX_INSTALLER_LOCAL_READY' in b or b'RAX_INSTALLER_FAILED:' in b or b'Kernel panic' in b, 60)
        if b'RAX_INSTALLER_LOCAL_READY' not in output:
            raise RuntimeError('Installer boot failed; see serial log')
        console.send('')
        console.wait(LINUX.search, 5)
        start_installer_ssh(console, router, ipaddress.IPv4Network('0.0.0.0/'+mask).prefixlen)
        index = adapter_info(args.interface)['index']
        with installer_ssh(host, router, index) as client:
            force = ' --force' if args.force else ''
            print(exchange(client, f'rax-flash check-kernel a{force}').decode(), flush=True)
            if args.prepare_only:
                print('INSTALLER_PREPARED: no persistent writes', flush=True)
                return
            if args.backup:
                backup(client, args.backup)
            try:
                print(exchange(client, 'rax-stage-assets', seconds=60).decode(), flush=True)
            except RuntimeError as error:
                if not args.clean_vendor_plugins or 'PLUGIN_SPACE_INSUFFICIENT' not in str(error):
                    raise
                # Only the recognized stock plugin tree may be removed, after
                # its complete backup has been streamed to the local PC.
                plugin_backup = args.backup / 'andlink.tar.gz'
                receive_file(client, 'tar -C /plugins -czf - andlink', plugin_backup)
                if not plugin_backup.stat().st_size:
                    raise RuntimeError('Empty original plugin backup')
                print(exchange(client, 'rm -rf /plugins/andlink; sync; rax-stage-assets', seconds=60).decode(), flush=True)
            print(exchange(client, 'cat >/tmp/rootfs.ubi; rax-image-check /tmp/rootfs.ubi', payload, seconds=40).decode(), flush=True)
            # Stop reading original rootfs before detaching its UBI device.
            print(exchange(client, f'if grep -q " /stock " /proc/mounts; then umount /stock || exit; fi; rax-flash write a /tmp/rootfs.ubi{force}', seconds=90).decode(), flush=True)
            print(exchange(client, f'rax-flash select a{force}', seconds=20).decode(), flush=True)
            exchange(client, 'sync; (sleep 2; reboot -f) >/dev/null 2>&1 &')
        print('INSTALLED: rootfsa written; slot A selected; reboot requested.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (Exception,KeyboardInterrupt) as error:
        print(f'INSTALL_FAILED: {error}', file=sys.stderr)
        sys.exit(1)
