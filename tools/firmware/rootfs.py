"""Prepare ImmortalWrt 25.12 runtime files for flash and local RAM builds."""
import hashlib
import io
import tarfile
from pathlib import Path

OPENWRT_FILES = Path(__file__).resolve().parents[1] / "openwrt"


def is_kernel_module(name):
    return name.endswith((".ko", ".ko.gz", ".ko.xz", ".ko.zst"))


def prepare_rootfs(source, assets=None, flash_module=None, vendor_links=False):
    # Keep normal service start AND shutdown links. Hardware-specific GPIO,
    # LED and packet steering defaults do not apply to the retained kernel.
    excluded_services = {"gpio_switch", "led", "packet_steering"}
    additions = {
        "etc/board.json": b'{"model":{"id":"hsan,luofu","name":"RAX3000H"},"network":{}}\n',
        "etc/config/network": b"config interface 'loopback'\n\toption device 'lo'\n\toption proto 'static'\n\toption ipaddr '127.0.0.1'\n\toption netmask '255.0.0.0'\n",
        "lib/preinit/00_rax_ram": b"export INITRAMFS=1\npi_preinit_no_failsafe=y\nfs_failsafe_wait_timeout=1\n",
        "etc/rc.local": b"#!/bin/sh\necho RAX_OPENWRT_BOOT_COMPLETE >/dev/console\nexit 0\n",
    }
    output = io.BytesIO()
    asset_dirs = set()
    links = {}
    if assets or vendor_links:
        if flash_module is None and not vendor_links:
            raise ValueError("Configuration persistence requires the matching vendor flash module")
        if vendor_links:
            for module in ("hi_flash", "hi_ksysenv"):
                links[f"lib/rax3000h/{module}.ko"] = f"/plugins/openwrt/vendor/lib/hisilicon/ko/{module}.ko"
        else:
            additions["lib/rax3000h/hi_flash.ko"] = flash_module
        # New builds provide matching ARMv7 libraries and tools themselves.
        with tarfile.open(source, 'r:gz') as archive:
            native_netfilter = any(item.name.removeprefix('./') == 'sbin/fw3' for item in archive)
        if not native_netfilter:
            raise ValueError("Missing fw3; build the pinned ImmortalWrt 25.12 userspace first")
        for name in ("iptables", "iptables-save", "iptables-restore", "ip6tables", "ip6tables-save", "ip6tables-restore"):
            links["usr/sbin/" + name] = "xtables-legacy-multi"
        links["etc/rc.d/S19firewall"] = "../init.d/firewall"
        manifest = (OPENWRT_FILES / "vendor-modules.list").read_bytes()
        allowed_modules = {line.split()[1] for line in manifest.decode().splitlines()
                           if line.strip() and not line.lstrip().startswith("#")}
        selected = set(allowed_modules)
        for line in (OPENWRT_FILES / "vendor-files.list").read_text().splitlines():
            name = line.strip()
            if not name or name.startswith("#"):
                continue
            if name.endswith("/"):
                if vendor_links:
                    if not name.startswith("usr/local/factory/"):
                        links[name.rstrip("/")] = "/plugins/openwrt/vendor/" + name.rstrip("/")
                    continue
                files = [path for path in (assets / name).rglob("*") if path.is_file()]
                if not files:
                    raise FileNotFoundError(f"Missing vendor assets: {assets / name}")
                selected.update(path.relative_to(assets).as_posix() for path in files
                                if not is_kernel_module(path.name))
            else:
                selected.add(name)
        for name in sorted(selected):
            if vendor_links:
                if not name.startswith("usr/local/factory/"):
                    links[name] = "/plugins/openwrt/vendor/" + name
            else:
                additions[name] = (assets / name).read_bytes()
        # Use tracked policy and loader, not stale copies in private assets.
        additions["usr/share/rax3000h/vendor-modules.list"] = manifest
        additions["usr/bin/rax-network"] = (OPENWRT_FILES / "rax-network.sh").read_bytes().replace(b"\r\n", b"\n")
        overlay = OPENWRT_FILES / "common-overlay"
        for path in sorted(overlay.rglob("*")):
            if path.is_file():
                # These are source text files, also edited from Windows.
                # Linux cannot execute a shebang ending in CRLF.
                additions[path.relative_to(overlay).as_posix()] = path.read_bytes().replace(b"\r\n", b"\n")
        additions['usr/share/rax3000h/wireless.default'] = additions['etc/config/wireless']
        additions['usr/share/rax3000h/firewall.default'] = additions['etc/config/firewall']
        links["etc/rc.d/S18rax-device"] = "../init.d/rax-device"
        links["etc/rc.d/S97rax-led"] = "../init.d/rax-led"
    for name in additions.keys() | links.keys():
        asset_dirs.update(parent.as_posix() for parent in Path(name).parents
                          if parent != Path("."))
    with tarfile.open(source, "r:gz") as original, tarfile.open(fileobj=output, mode="w:gz") as dest:
        # Pinned LuCI versions and reproducible mtimes do not identify local
        # frontend edits. Extend LuCI's existing resource token with content.
        frontend = hashlib.sha256()
        for item in sorted(original.getmembers(), key=lambda item: item.name):
            name = item.name.removeprefix("./")
            if item.isfile() and name.startswith(("www/", "usr/share/ucode/luci/template/")):
                data = original.extractfile(item).read()
                frontend.update(name.encode() + b"\0")
                frontend.update(len(data).to_bytes(8, "big"))
                frontend.update(data)
        # Project frontend components override packaged files too. Include
        # their content so upgrades invalidate LuCI's browser resource cache.
        for name, data in sorted(additions.items()):
            if name.startswith(("www/", "usr/share/ucode/luci/template/")):
                frontend.update(name.encode() + b"\0")
                frontend.update(len(data).to_bytes(8, "big"))
                frontend.update(data)
        if vendor_links:
            for path in sorted((OPENWRT_FILES / 'flash-overlay/www').rglob('*')):
                if path.is_file():
                    frontend.update(path.relative_to(OPENWRT_FILES / 'flash-overlay').as_posix().encode())
                    frontend.update(path.read_bytes().replace(b'\r\n', b'\n'))
        resource_version = frontend.hexdigest()[:16].encode()
        # The kernel unpacks newc entries in order and requires parent
        # directories to exist before files (notably the musl interpreter).
        for name in sorted(asset_dirs):
            member = tarfile.TarInfo("./" + name)
            member.type = tarfile.DIRTYPE
            member.mode = 0o755
            dest.addfile(member)
        for member in original:
            name = member.name.removeprefix("./")
            if name in additions or name in links or name in asset_dirs:
                continue
            if is_kernel_module(name):
                continue
            if name.startswith("etc/dropbear/"):
                # Host keys are generated on the device, never built into images.
                continue
            if name.startswith("etc/uci-defaults/") and name.rsplit("/", 1)[1] not in {
                    "30_luci-theme-bootstrap", "30_luci-theme-7dollar",
                    "50_luci-mod-admin-full", "luci-i18n-base-zh-cn"}:
                continue
            if any(name.startswith(p) for p in ("lib/modules/", "etc/modules.d/", "etc/modules-boot.d/", "etc/board.d/")):
                continue
            if (name.startswith("etc/rc.d/") and
                    Path(member.linkname).name in excluded_services):
                continue
            if name == "usr/share/ucode/luci/template/header.ut":
                data = original.extractfile(member).read()
                marker = b'/luci.js?v='
                if data.count(marker) != 1:
                    raise ValueError("Unexpected LuCI header resource URL")
                data = data.replace(marker, marker + b'rax-' + resource_version + b'-', 1)
                member.size = len(data)
                dest.addfile(member, io.BytesIO(data))
                continue
            if name == "etc/rc.common" and (assets or vendor_links):
                # Small, checked source adaptation: preserve standard rc/LuCI
                # enable/disable behavior while recording explicit overrides.
                data = original.extractfile(member).read().replace(b"\r\n", b"\n")
                changes = {
                    b'\trm -f "$IPKG_INSTROOT"/etc/rc.d/K??$name\n':
                    b'\trm -f "$IPKG_INSTROOT"/etc/rc.d/K??$name\n\t[ -n "$IPKG_INSTROOT$RAX_SERVICE_RESTORE" ] || /usr/sbin/rax-service-state save "$name" 0\n',
                    b'\treturn $err\n':
                    b'\t[ "$err" != 0 ] || [ -n "$IPKG_INSTROOT$RAX_SERVICE_RESTORE" ] || /usr/sbin/rax-service-state save "$name" 1 || return $?\n\treturn $err\n',
                }
                for before, after in changes.items():
                    if data.count(before) != 1:
                        raise ValueError("Unexpected rc.common; review service-state adaptation")
                    data = data.replace(before, after)
                member.size = len(data)
                dest.addfile(member, io.BytesIO(data))
                continue
            dest.addfile(member, original.extractfile(member) if member.isfile() else None)
        for name, data in additions.items():
            member = tarfile.TarInfo("./" + name)
            executable = name.startswith(("bin/", "sbin/", "usr/bin/", "usr/sbin/", "lib/netifd/wireless/"))
            member.mode = 0o755 if name == "etc/rc.local" or executable or name.startswith("etc/init.d/") else 0o644
            if name.startswith("etc/dropbear/"):
                member.mode = 0o600
            member.size = len(data)
            dest.addfile(member, io.BytesIO(data))
        for name, target in links.items():
            member = tarfile.TarInfo("./" + name)
            member.type = tarfile.SYMTYPE
            member.linkname = target
            member.mode = 0o777
            dest.addfile(member)
    return output.getvalue()

