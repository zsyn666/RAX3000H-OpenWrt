"""Install the project's userspace target into an external ImmortalWrt checkout.

Run with Linux Python in WSL. Generated copies may be discarded; the source
of the adaptation remains beside this script. Does not download or build.
"""
import argparse
import json
import shutil
import re
import subprocess
from pathlib import Path

from common.paths import DEFAULT_OPENWRT, build_environment, require_linux


def source_lock():
    path = Path(__file__).resolve().parent / "openwrt/sources.lock.json"
    return json.loads(path.read_text())


def patch_paths(section):
    source = Path(__file__).resolve().parent / "openwrt"
    return sorted((source / "patches" / section).glob("*.patch"))


def require_revision(directory, expected):
    actual = subprocess.check_output(
        ["git", "-C", str(directory), "rev-parse", "HEAD"], text=True,
    ).strip()
    if actual != expected:
        raise ValueError(f"Source version mismatch: {directory}: expected {expected}, got {actual}")


def verify_feeds(checkout, allow_missing=False, lock=None):
    for name, entry in (lock or source_lock())["feeds"].items():
        directory = checkout / "feeds" / name
        if allow_missing and not directory.exists():
            continue
        require_revision(directory, entry["commit"])


def verify_sources(checkout, lock=None):
    lock = lock or source_lock()
    require_revision(checkout, lock["openwrt"]["commit"])
    feeds = checkout / "feeds.conf"
    if not feeds.exists():
        feeds = checkout / "feeds.conf.default"
    entries = [line.split() for line in feeds.read_text().splitlines()
               if line.strip() and not line.lstrip().startswith("#")]
    for name, entry in lock["feeds"].items():
        expected = ["src-git", name, entry["url"] + "^" + entry["commit"]]
        if [line for line in entries if len(line) > 1 and line[1] == name] != [expected]:
            raise ValueError(f"{feeds}: expected pinned feed: {' '.join(expected)}")
    recipe = (checkout / "package/network/utils/iwinfo/Makefile").read_text()
    for key, expected in (("PKG_SOURCE_VERSION", lock["iwinfo"]["commit"]),
                          ("IWINFO_ABI_VERSION", lock["iwinfo"]["abi"])):
        match = re.search(rf"^{key}:=(\S+)\s*$", recipe, re.MULTILINE)
        if not match or match[1] != expected:
            raise ValueError(f"iwinfo {key} must be {expected}")
    verify_feeds(checkout, allow_missing=True, lock=lock)


def install_target(checkout, lock=None):
    lock = lock or source_lock()
    source = Path(__file__).resolve().parent / "openwrt"
    checkout = checkout.resolve()
    if not (checkout / "include/target.mk").is_file():
        raise ValueError("openwrt_dir must be an ImmortalWrt source checkout")
    # Reject other source versions before writing generated files or patches.
    verify_sources(checkout, lock)
    for patch in patch_paths('openwrt'):
        applied = subprocess.run(['git', 'apply', '--reverse', '--check', str(patch)],
                                 cwd=checkout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if applied.returncode:
            subprocess.run(['git', 'apply', '--check', str(patch)], cwd=checkout, check=True)
            subprocess.run(['git', 'apply', str(patch)], cwd=checkout, check=True)
    shutil.copytree(source / "target", checkout / "target/linux/rax3000h", dirs_exist_ok=True)
    shutil.copytree(source / "wifi-scan", checkout / "package/rax-wifi-scan", dirs_exist_ok=True)
    for package, directory in (("firewall", "network/config/firewall"),):
        shutil.copytree(source / 'patches' / package,
                        checkout / 'package' / directory / 'patches', dirs_exist_ok=True)
    # Keep the vendor iwinfo backend in this project, with a reproducible
    # generated source patch in the disposable upstream checkout.
    backend = (source / "iwinfo_rax3000h.c").read_text()
    patch = ('--- a/Makefile\n+++ b/Makefile\n'
             '@@ -6,7 +6,7 @@ IWINFO_LDFLAGS     = -luci -lubox -lubus\n'
             ' \n'
             ' IWINFO_LIB         = libiwinfo.so\n'
             ' IWINFO_LIB_LDFLAGS = $(LDFLAGS) -shared -Wl,-soname -Wl,$(IWINFO_LIB).$(IWINFO_SOVERSION)\n'
             '-IWINFO_LIB_OBJ     = iwinfo_utils.o iwinfo_lib.o\n'
             '+IWINFO_LIB_OBJ     = iwinfo_utils.o iwinfo_lib.o iwinfo_rax3000h.o\n'
             ' \n'
             ' IWINFO_LUA         = iwinfo.so\n'
             ' IWINFO_LUA_LDFLAGS = $(LDFLAGS) -shared -L. -liwinfo -llua\n'
             '--- a/iwinfo_lib.c\n+++ b/iwinfo_lib.c\n'
             '@@ -396,7 +396,10 @@ const struct iwinfo_iso3166_label IWINFO_ISO3166_NAMES[] = {\n'
             ' \t{ 0,               "" }\n'
             ' };\n'
             ' \n'
             '+extern const struct iwinfo_ops rax3000h_ops;\n+\n'
             ' static const struct iwinfo_ops *backends[] = {\n'
             '+\t&rax3000h_ops,\n'
             ' #ifdef USE_NL80211\n'
             ' \t&nl80211_ops,\n'
             ' #endif\n'
             '--- /dev/null\n+++ b/iwinfo_rax3000h.c\n'
             f'@@ -0,0 +1,{len(backend.splitlines())} @@\n' +
             ''.join('+' + line + '\n' for line in backend.splitlines()))
    header = (source / 'wifi-scan/src/rax_scan.h').read_text()
    patch += ('--- /dev/null\n+++ b/rax_scan.h\n'
              f'@@ -0,0 +1,{len(header.splitlines())} @@\n' +
              ''.join('+' + line + '\n' for line in header.splitlines()))
    patchdir = checkout / 'package/network/utils/iwinfo/patches'
    patchdir.mkdir(exist_ok=True)
    (patchdir / '900-rax3000h.patch').write_text(patch)
    install_theme(checkout, lock)
    merge_config(checkout)
    print(f"Target installed: {checkout / 'target/linux/rax3000h'}")


def install_theme(checkout, lock):
    source = checkout.parent / '7dollar'
    if not source.is_dir():
        raise ValueError(f"Missing 7Dollar checkout: {source}; run tools/prepare_build.py first")
    require_revision(source, lock['theme']['commit'])
    destination = checkout / 'package' / lock['theme']['package']
    if destination.is_symlink():
        destination.unlink()
    elif destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(
        '.git', '.github', 'dist', 'docs'))


def merge_config(checkout):
    source = Path(__file__).resolve().parent / "openwrt"
    config = checkout / ".config"
    fragment = (source / "rootfs.config").read_text().splitlines()
    keys = {m.group(1) for line in fragment
            if (m := re.match(r"(?:# )?(CONFIG_[A-Za-z0-9_]+)(?:=| is not set)", line))}
    previous = config.read_text().splitlines() if config.exists() else []
    previous = [line for line in previous
                if not ((m := re.match(r"(?:# )?(CONFIG_[A-Za-z0-9_]+)(?:=| is not set)", line))
                        and m.group(1) in keys)]
    config.write_text("\n".join(previous + fragment) + "\n")


def configure_checkout(checkout, env=None):
    """Apply project sources and reindex local feeds; never fetch repositories."""
    checkout = checkout.resolve()
    env = env or build_environment()
    lock = source_lock()
    logs = checkout.parent / "logs"
    logs.mkdir(exist_ok=True)
    install_target(checkout, lock)
    verify_feeds(checkout, lock=lock)
    for action, packages in (("update", list(lock["feeds"])), ("install", [
            "cgi-io", "luasrcdiet", "luci-base", "luci-mod-status", "luci-mod-system", "luci-mod-network",
            "luci-theme-bootstrap", "luci-i18n-base-zh-cn", "luci-proto-ppp",
            "luci-app-firewall", "luci-i18n-firewall-zh-cn", "luci-proto-ipv6"])):
        log_path = logs / f"feeds-{action}.log"
        with log_path.open("w") as log:
            subprocess.run(["./scripts/feeds", action, *(["-i"] if action == "update" else []), *packages], cwd=checkout,
                           env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    source = Path(__file__).resolve().parent / 'openwrt'
    luci = checkout / 'feeds/luci'
    for patch in patch_paths('luci'):
        # Applying an explicit source patch is repeatable; unexpected source
        # changes fail here rather than silently producing a broken form.
        applied = subprocess.run(['git', 'apply', '--reverse', '--check', str(patch)],
                                 cwd=luci, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if applied.returncode:
            subprocess.run(['git', 'apply', '--check', str(patch)], cwd=luci, check=True)
            subprocess.run(['git', 'apply', str(patch)], cwd=luci, check=True)
    shutil.copyfile(source / 'luci/rax3000h.js',
                    luci / 'modules/luci-mod-network/htdocs/luci-static/resources/tools/rax3000h.js')
    # Feed indexing can resolve .config before LuCI definitions are installed.
    # Reapply the fragment after installation so a fresh checkout retains them.
    merge_config(checkout)
    subprocess.run(["make", "defconfig"], cwd=checkout, env=env, check=True)
    validate_config(checkout)

def validate_config(checkout):
    config = (checkout / ".config").read_text().splitlines()
    required = {
        "CONFIG_TARGET_rax3000h=y", "CONFIG_SOFT_FLOAT=y", "CONFIG_USE_APK=y",
        'CONFIG_CPU_TYPE="cortex-a9"', "CONFIG_TARGET_ROOTFS_TARGZ=y",
        "CONFIG_TARGET_ROOTFS_SQUASHFS=y",
    }
    source = Path(__file__).resolve().parent / "openwrt"
    required.update(line for line in (source / 'rootfs.config').read_text().splitlines()
                    if line.startswith('CONFIG_PACKAGE_') and line.endswith('=y'))
    missing = required - set(config)
    if missing:
        raise SystemExit("Missing rootfs configuration: " + ", ".join(sorted(missing)))
    if any(line.startswith("CONFIG_PACKAGE_kmod-") and line.endswith(("=y", "=m"))
           for line in config):
        raise SystemExit("Kernel modules cannot be built for the retained vendor kernel")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("openwrt_dir", type=Path, nargs="?", default=DEFAULT_OPENWRT)
    args = parser.parse_args()
    require_linux()
    configure_checkout(args.openwrt_dir)


if __name__ == "__main__":
    main()
