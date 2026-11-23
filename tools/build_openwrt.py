"""Build ARMv7 soft-float userspace in a pinned external ImmortalWrt checkout.

Run with Linux Python in WSL. Produces rootfs archives only; no router access.
Downloads use the normal environment unless --proxy is supplied.
"""
import argparse
import os
from pathlib import Path
import subprocess

from prepare_openwrt import configure_checkout
from common.paths import DEFAULT_OPENWRT, build_environment, require_linux


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("openwrt_dir", type=Path, nargs="?", default=DEFAULT_OPENWRT)
    parser.add_argument("--jobs", type=int, default=min(os.cpu_count() or 1, 8))
    parser.add_argument("--proxy", help="HTTP proxy URL, applied to this build only")
    parser.add_argument("--mirror", help="Preferred ImmortalWrt source mirror URL")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    checkout = args.openwrt_dir.resolve()
    require_linux()
    if not (checkout / "include/target.mk").is_file():
        parser.error("ImmortalWrt sources missing; run tools/prepare_build.py first")
    logs = checkout.parent / "logs"
    logs.mkdir(exist_ok=True)
    env = build_environment()
    for name, script in (("REVISION", "getver.sh"),
                         ("SOURCE_DATE_EPOCH", "get_source_date_epoch.sh")):
        env[name] = subprocess.check_output(
            [str(checkout / "scripts" / script)], cwd=checkout, env=env, text=True,
        ).strip()
    if args.proxy:
        for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            env[name] = args.proxy
    if args.mirror:
        env["DOWNLOAD_MIRROR"] = args.mirror

    def stage(name, *make_args):
        log_path = logs / f"{name}.log"
        print(f"{name}: {log_path}", flush=True)
        with log_path.open("w") as log:
            result = subprocess.run(
                ["make", *make_args], cwd=checkout, env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            )
        if result.returncode:
            print("\n".join(log_path.read_text(errors="replace").splitlines()[-35:]))
            raise SystemExit(f"{name} failed ({result.returncode}); see {log_path}")

    def value(name, subdir=None):
        expression = f"$({name})"
        if subdir:
            expression = f"$(foreach curdir,{subdir},{expression})"
        return subprocess.check_output(
            ["make", "-s", "OPENWRT_BUILD=1", "--eval",
             f"rax-build-value:;@printf '%s\\n' '{expression}'", "rax-build-value"],
            cwd=checkout, env=env,
            stdin=subprocess.DEVNULL, text=True,
        ).strip()

    configure_checkout(checkout, env)
    stage("tools", f"-j{args.jobs}", "tools/install", "V=s")
    stage("toolchain", f"-j{args.jobs}", "toolchain/install", "V=s")
    # Upstream adds kernel/linux unconditionally. Override only this package
    # list on make's command line, without editing the upstream Makefiles.
    packages = sorted(set(value("package-y").split()) - {"kernel/linux"})
    modules = sorted(set(value("package-m").split()) - {"kernel/linux"})
    overrides = ["package-y=" + " ".join(packages), "package-m=" + " ".join(modules)]
    # fstools also defines blockd. Its unselected autofs kernel dependency is
    # included in the directory-wide build graph; retain all userspace deps.
    fstools_deps = value("package/system/fstools/compile", "package").split()
    overrides.append("package/system/fstools/compile=" + " ".join(
        dep for dep in fstools_deps if dep != "package/kernel/linux/compile"
    ))
    # PPP's directory also defines unselected PPPoA/PPTP packages. Their
    # kernel, ATM and resolveip dependencies are not needed for PPPoE.
    ppp_deps = value("package/network/services/ppp/compile", "package").split()
    overrides.append("package/network/services/ppp/compile=" + " ".join(
        dep for dep in ppp_deps if dep not in {
            "package/kernel/linux/compile", "package/network/utils/linux-atm/compile",
            "package/network/utils/resolveip/compile",
        }
    ))
    # These directories also contain unselected nft and extra kmod variants.
    for directory in ("network/utils/iptables", "network/config/firewall"):
        target = f"package/{directory}/compile"
        dependencies = value(target, "package").split()
        overrides.append(target + "=" + " ".join(
            dep for dep in dependencies if dep != "package/kernel/linux/compile"
        ))
    stage("packages", f"-j{args.jobs}", "package/compile", "V=s", *overrides)
    stage("rootfs", "package/install", "V=s", *overrides)
    stage("images", "-C", "target/linux/rax3000h/image", f"TOPDIR={checkout}",
          "install", "V=s")
    output = Path(value("BIN_DIR"))
    archives = sorted(output.glob("*-rootfs.tar.gz")) + sorted(output.glob("*-rootfs.squashfs"))
    if len(archives) < 2:
        raise SystemExit(f"Missing rootfs output in {output}")
    for path in archives:
        print(f"Built: {path} ({path.stat().st_size / 1024 / 1024:.2f} MiB)")


if __name__ == "__main__":
    main()
