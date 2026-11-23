"""Prepare pinned ImmortalWrt sources and downloads on Linux/WSL; no toolchain build.

Defaults to deps/openwrt. An existing checkout must match sources.lock.json.
Install host packages using your distribution's package manager first.
"""
import argparse
from pathlib import Path
import shutil
import subprocess

from common.paths import DEFAULT_OPENWRT, build_environment, require_linux
from prepare_openwrt import configure_checkout, require_revision, source_lock


def check_host_tools(env):
    tools = ('git', 'gcc', 'g++', 'make', 'patch', 'perl', 'python3', 'tar', 'gzip',
             'bzip2', 'xz', 'zstd', 'unzip', 'file', 'rsync', 'wget', 'curl', 'diff',
             'awk', 'sed', 'find', 'xargs', 'pkg-config', 'flex', 'bison')
    missing = [name for name in tools if not shutil.which(name, path=env['PATH'])]
    if missing:
        raise SystemExit('Missing host tools: ' + ', '.join(missing) + '\n'
                         'Debian/Ubuntu: sudo apt install build-essential clang flex bison g++ '
                         'gawk gcc-multilib gettext git libncurses-dev libssl-dev '
                         'python3 python3-setuptools rsync swig unzip zlib1g-dev '
                         'file wget curl patch perl xz-utils zstd bzip2 pkg-config')


def fetch_repository(directory, entry, env):
    if directory.exists() and any(directory.iterdir()):
        # Never reset a developer's checkout, including its local patches.
        require_revision(directory, entry['commit'])
        print(f'SKIP source present: {directory}', flush=True)
        return
    directory.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', str(directory)], env=env, check=True)
    subprocess.run(['git', '-C', str(directory), 'remote', 'add', 'origin', entry['url']],
                   env=env, check=True)
    subprocess.run(['git', '-C', str(directory), 'fetch', '--depth', '1', 'origin', entry['commit']],
                   env=env, check=True)
    subprocess.run(['git', '-C', str(directory), 'checkout', '--detach', entry['commit']],
                   env=env, check=True)
    require_revision(directory, entry['commit'])


def prepare_sources(checkout, env):
    lock = source_lock()
    fetch_repository(checkout, lock['openwrt'], env)
    for name, entry in lock['feeds'].items():
        fetch_repository(checkout / 'feeds' / name, entry, env)
    # 7Dollar is the only third-party package; keep it outside the upstream
    # checkout so the build tree stays disposable and its revision is locked.
    fetch_repository(checkout.parent / '7dollar', lock['theme'], env)
    feeds = checkout / 'feeds.conf'
    if not feeds.exists():
        feeds.write_text(''.join(f"src-git {name} {entry['url']}^{entry['commit']}\n"
                                 for name, entry in lock['feeds'].items()), encoding='utf-8')
    # Existing feeds.conf is validated by configure_checkout, never overwritten.
    configure_checkout(checkout, env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('openwrt_dir', type=Path, nargs='?', default=DEFAULT_OPENWRT)
    parser.add_argument('--jobs', type=int, default=8, help='Parallel source downloads')
    parser.add_argument('--skip-download', action='store_true', help='Prepare repositories/config only')
    args = parser.parse_args()
    require_linux()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    env = build_environment()
    check_host_tools(env)
    checkout = args.openwrt_dir.resolve()
    prepare_sources(checkout, env)
    if not args.skip_download:
        subprocess.run(['make', f'-j{args.jobs}', 'download', 'V=s'], cwd=checkout, env=env, check=True)
    print(f'PREPARED={checkout}\nNext: python3 tools/build_openwrt.py "{checkout}"')


if __name__ == '__main__':
    main()
