# Build and development guide

This repository builds ImmortalWrt 25.12 userspace for the Mobile RAX3000H. The router keeps its original Linux 5.10 kernel, bootloader and device-specific drivers. The output is a rootfs-only UBI image, a temporary installer, and a Windows/Linux Python flasher bundle. LuCI defaults to the 7Dollar theme; apart from that theme, no third-party plugins are included.

All commands below run from the repository root unless stated otherwise. Building and packaging are entirely local: they do not connect to, reboot or flash a router.

## 1. Build environment

Use a Linux host or WSL 2. The current build environment is Ubuntu 22.04 x86-64 with distribution-updated Python 3.10; Ubuntu 24.04 is also a suitable setup. Run builds as a normal user. Use `sudo` only to install system packages.

| Resource | Setup |
|---|---|
| Disk | Reserve **12 GB of free space** for the current project configuration, including dependencies, downloads, compilation and output |
| Memory | 8 GB with 2–4 build jobs; 16 GB is a comfortable setup for 8 jobs |
| CPU | A multicore x86-64 host; the scripts build the ARM cross-toolchain |
| Python | Distribution-updated Python 3.10 or newer; `tarfile.TarFile.extractall` must support the `filter` argument |
| Network | Access to the locked Git repositories and upstream package source downloads |

A reference build occupies about 8.3 GiB including its download cache: approximately 6.4 GiB in `build_dir`, 711 MiB in `staging_dir`, 253 MiB in feeds and 821 MiB in cached source archives. These are measurements of the current package selection. The 12 GB allowance is for this project workspace, separate from the Linux installation itself.

The build scripts use Python's standard library. `requirements.txt` contains dependencies for the **flasher**, and is not needed to compile firmware.

### Windows / WSL

Keep the checkout and its dependencies in the Linux filesystem, for example `~/src/RAX3000H-OpenWrt`. This preserves normal permissions and symlinks and avoids slow cross-filesystem builds. Microsoft also recommends storing Linux-command-line projects in the [WSL filesystem](https://learn.microsoft.com/en-us/windows/wsl/filesystems).

Build inside WSL, then run the Windows flasher from Windows when using a Windows serial adapter. WSL does not need access to the router to build the release files.

### Install host packages

On Ubuntu:

```bash
sudo apt update
sudo apt install \
  build-essential clang flex bison g++ gawk gcc-multilib gettext git \
  libncurses-dev libssl-dev python3 python3-setuptools rsync swig unzip \
  zlib1g-dev file wget curl patch perl xz-utils zstd bzip2 pkg-config
```

Use the distribution's Python and system build tools. The wrappers set a Linux-only tool search path and a consistent locale; Windows executables and virtual-environment tools are not used as build tools.

## 2. Get the project

Clone this repository into a path without spaces. Replace `<repository-url>` with the URL of this repository:

```bash
mkdir -p ~/src
cd ~/src
git clone <repository-url> RAX3000H-OpenWrt
cd RAX3000H-OpenWrt
```

The default layout after a build is:

```text
RAX3000H-OpenWrt/
├── tools/                         # Build, packaging and flashing entry points
│   ├── common/                    # Shared host utilities
│   ├── firmware/                  # Rootfs/installer assembly and image utilities
│   └── openwrt/                   # Target, configuration, patches and runtime files
├── docs/
├── deps/
│   ├── openwrt/                   # Pinned ImmortalWrt checkout, feeds and build products
│   ├── 7dollar/                   # Pinned 7Dollar theme source
│   └── logs/                      # Feed and build stage logs
└── dist/
    ├── firmware/                  # Rootfs image and packaging intermediates
    ├── installer/                 # Temporary installer and packaging intermediates
    └── release/                   # The three distributable files
```

`deps/` and `dist/` are ignored by Git. The adaptation source belongs under `tools/`, outside the upstream checkout.

## 3. Prepare source dependencies

```bash
python3 tools/prepare_build.py
```

This step:

1. Checks that the host tools are available.
2. Fetches the exact ImmortalWrt and feed commits from `tools/openwrt/sources.lock.json` into `deps/openwrt` and `deps/openwrt/feeds`, plus the pinned 7Dollar theme into `deps/7dollar`.
3. Installs the project target, Wi-Fi scan package and 7Dollar theme, applies ImmortalWrt/LuCI adaptations, and generates the iwinfo backend patch.
4. Indexes the local feeds and installs the selected LuCI package definitions.
5. Merges `tools/openwrt/rootfs.config`, runs `make defconfig`, and checks the resulting configuration.
6. Runs `make download` to populate ImmortalWrt's source archive cache.

An existing repository at the expected commit is reused without fetching or resetting it. Its local edits are preserved. A different commit causes an explicit version mismatch; the script does not silently move a developer's checkout. Downloads reuse the upstream cache. Configuration and patch preparation still run on subsequent invocations.

This step **does not compile the toolchain or firmware**, and it does not install system packages automatically.

To prepare repositories and configuration without downloading package archives:

```bash
python3 tools/prepare_build.py --skip-download
```

The lock file currently selects ImmortalWrt `v25.12.2`, commit `4fc16f2985a358bd43bb522e43f05395fcbd6ed5`, with separately pinned packages, LuCI, iwinfo and 7Dollar revisions. Treat the lock file as the authoritative version list. Use the project preparation script instead of a generic `feeds update -a` workflow that moves feeds to newer revisions.

### Reuse an existing ImmortalWrt checkout

Each of the four preparation/build scripts accepts an optional ImmortalWrt directory as its first argument:

```bash
python3 tools/prepare_build.py /home/user/build/rax-openwrt
python3 tools/build_openwrt.py /home/user/build/rax-openwrt --jobs 8
python3 tools/build_openwrt_firmware.py /home/user/build/rax-openwrt
python3 tools/build_installer.py /home/user/build/rax-openwrt
```

Pass the same path to all four commands. The existing checkout must match the lock file; any existing `feeds.conf` must contain the pinned entries. The matching 7Dollar checkout is expected in a sibling `7dollar` directory (default `deps/7dollar`). Logs go to `logs/` beside that checkout. Output still goes to this project's `dist/` unless `--output` is supplied to the image builders.

## 4. Compile ImmortalWrt userspace

```bash
python3 tools/build_openwrt.py --jobs 8
```

Use `--jobs 2` or `--jobs 4` on a smaller machine. If omitted, the script uses at most eight jobs, limited by the host CPU count.

The wrapper first reapplies the project configuration, then runs these stages:

| Stage | What it does |
|---|---|
| `tools` | Builds host-side ImmortalWrt tools, including filesystem/image utilities |
| `toolchain` | Builds the Cortex-A9 ARMv7 soft-float GCC/musl cross-toolchain |
| `packages` | Compiles the selected userspace packages and project adaptations |
| `rootfs` | Installs the selected packages into the target root filesystem |
| `images` | Produces the base rootfs tarball and SquashFS under `bin/targets/rax3000h/generic/` |

The runtime ABI is ARMv7/Cortex-A9, musl, **soft-float**. The target's Linux 6.12 setting supplies build metadata/UAPI; it does not replace the router's Linux 5.10 kernel. The wrapper excludes the kernel build and removes irrelevant kernel dependencies from selected package build graphs. Configuration validation rejects selected `kmod-*` packages.

Use this wrapper rather than plain `make world`. Official hard-float ARM packages and official ImmortalWrt kernel modules are not substitutes for this toolchain and retained kernel. Packages use ImmortalWrt 25.12's native APK format.

Stage output is written to `deps/logs/`. A failed stage prints the final log lines and identifies the full log. Logs are overwritten when that stage runs again. For example, monitor package compilation in another terminal:

```bash
tail -f deps/logs/packages.log
```

The first build compiles the toolchain. Later builds reuse it and the existing package outputs. Rerun the same command after fixing a failed stage.

### Proxies and mirrors

Preparation and builds inherit standard proxy environment variables. The build wrapper also accepts an explicit proxy or preferred download mirror:

```bash
python3 tools/build_openwrt.py --jobs 8 --proxy http://proxy-host:port
python3 tools/build_openwrt.py --jobs 8 --mirror https://mirror-host/path
```

For the preparation/download step, set `http_proxy` and `https_proxy` in its environment. In WSL, use an address reachable from Linux; a Windows-only localhost proxy may require different addressing.

## 5. Assemble the flashable rootfs

```bash
python3 tools/build_openwrt_firmware.py
```

This reads the base `*default-rootfs.tar.gz` from the userspace build and:

- Adds the common runtime files, default configuration and flash-only management components.
- Adds links to the router's device-local vendor assets instead of copying those assets into the image.
- Installs the flash boot entry point, configuration persistence support and image/configuration checking utilities.
- Compiles the required NAND/UBI and U-Boot environment userspace tools using the same toolchain.
- Creates an XZ-compressed SquashFS and wraps it in the router's UBI format.

The result is `dist/firmware/immortalwrt-rootfs.ubi`. The image has one static volume named `squashfs`, 128 KiB physical eraseblocks, a 2048-byte minimum I/O size and VID-header offset, and a 4096-byte data offset. The packager enforces a 27 MiB image-size ceiling to leave flash overhead and bad-block reserve.

Temporary filesystem trees are created inside the Linux build `build_dir` so that SquashFS retains correct permissions. Do not assemble the root tree by manually extracting it onto a Windows filesystem.

The resulting image contains no vendor kernel, private driver blobs, calibration data, device backups or preinstalled SSH keys. At runtime, vendor assets live under `/plugins/openwrt/vendor/`; persistent configuration and credentials also live under `/plugins/openwrt/`. The image's root filesystem remains read-only, with selected writable runtime paths and configuration mounts. It is not a general writable package overlay.

## 6. Assemble the temporary installer

```bash
python3 tools/build_installer.py
```

This creates `dist/installer/installer.cpio.gz`, a small RAM filesystem containing BusyBox, temporary SSH, network setup, vendor-asset extraction/staging, and the NAND/UBI tools. Executables and their required shared libraries come from the same userspace build.

The installer is not a second persistent firmware image. During installation, the flasher loads it with the kernel read from the device. It reuses already-staged vendor assets, or extracts them from the original A-slot rootfs when installing for the first time. It then stages the assets in the plugins partition before writing the ImmortalWrt rootfs.

The installer uses temporary root SSH with no password authentication requirement. This access belongs to the RAM installer; the installed ImmortalWrt system generates and persists its own SSH host keys.

## 7. Package the release

```bash
python3 tools/package_release.py
```

This copies the two images and packages the host flasher into `dist/release/`:

| File | Purpose |
|---|---|
| `immortalwrt-rootfs.ubi` | Persistent rootfs-only firmware |
| `installer.cpio.gz` | Temporary RAM installer |
| `rax3000h-flasher.zip` | Flat Python flasher bundle with `requirements.txt` and helper modules |

Packaging does not upload files or create a GitHub release. Publish these three files as release assets; keep compilation intermediates out of the source repository.

To package images from other output directories:

```bash
python3 tools/package_release.py \
  --firmware /path/to/immortalwrt-rootfs.ubi \
  --installer /path/to/installer.cpio.gz \
  --output dist/release
```

The flasher backs up the device partitions, writes rootfs A, selects A and requests a reboot. It finishes there; it does not wait for the final operating system to boot. Follow the [README installation instructions](../README.md#刷机) for wiring, flashing and first login.

## 8. Development workflow

### Where to make changes

| Area | Project source |
|---|---|
| Required package selection | `tools/openwrt/rootfs.config` |
| Architecture and target metadata | `tools/openwrt/target/` |
| Upstream ImmortalWrt/LuCI changes | `tools/openwrt/patches/openwrt/`, `tools/openwrt/patches/luci/` |
| 7Dollar theme source | `deps/7dollar/` (pinned by `tools/openwrt/sources.lock.json`) |
| fw3 adaptation | `tools/openwrt/patches/firewall/` |
| Default UCI configuration and common runtime scripts | `tools/openwrt/common-overlay/` |
| Flash-only LuCI/RPC, persistence management and flashing | `tools/openwrt/flash-overlay/` |
| Rootfs assembly and flash boot entry | `tools/firmware/rootfs.py`, `tools/firmware/flash-init` |
| Wired networking and vendor module loading | `tools/openwrt/rax-network.sh`, `tools/openwrt/vendor-modules.list` |
| Wireless lifecycle and LuCI options | `tools/openwrt/common-overlay/lib/netifd/wireless/rax3000h.sh`, `tools/openwrt/luci/rax3000h.js` |
| iwinfo and scan implementation | `tools/openwrt/iwinfo_rax3000h.c`, `tools/openwrt/wifi-scan/` |
| Device-local asset selection | `tools/openwrt/vendor-files.list`, `tools/openwrt/vendor-modules.list` |
| Installer runtime | `tools/firmware/installer-overlay/` |
| Host flashing and release packaging | `tools/flash_openwrt.py`, `tools/common/`, `tools/package_release.py` |

Keep permanent changes in this repository. Files copied or generated in `deps/openwrt` and `deps/7dollar` are build inputs derived from these sources. Do not leave the only copy of a fix in that dependency checkout.

`prepare_openwrt.py` is the configuration/application layer used by both preparation and compilation. It can also run alone when the locked repositories, including `deps/7dollar`, already exist:

```bash
python3 tools/prepare_openwrt.py
```

It applies and indexes the local adaptation without fetching repositories, downloading package archives or compiling the toolchain.

### Choose the smallest rebuild

| Change | Commands to rerun |
|---|---|
| Common/flash overlay files used only by the installed rootfs, rootfs assembly or `flash-init` | `build_openwrt_firmware.py`, then `package_release.py` |
| Installer runtime or installer assembly | `build_installer.py`, then `package_release.py` |
| Code copied into both images: `rax-network.sh`, asset manifests, shared runtime utilities, or flash-overlay `rax-flash`, `rax-mac`, `fw_env.config` | Both image builders, then `package_release.py` |
| Package selection, upstream patches, iwinfo, scan C code, copied LuCI form module | `build_openwrt.py`, then the affected image builders and `package_release.py` |
| 7Dollar revision in `sources.lock.json` | `prepare_build.py`, `build_openwrt.py`, then the affected image builders and `package_release.py` |
| Host flasher Python or `requirements.txt` | `package_release.py` |
| Documentation or README images | No firmware rebuild |

All script names in this table are under `tools/`. These are packaging dependencies, not instructions to flash a connected device automatically.

### Package selection and defaults

Make required package changes in `rootfs.config`. The preparation step merges that fragment into `.config` and preserves unrelated settings, then runs `defconfig`. Settings explicitly present in the fragment win over local `.config` edits. Keep the package set limited to ImmortalWrt official feeds; the only third-party package is `luci-theme-7dollar`, fetched by `prepare_build.py` and installed from the pinned theme checkout by `prepare_openwrt.py`.

`make menuconfig` is useful for exploration, but promote the desired package settings into `rootfs.config` before sharing the change. Packages needed by `configure_checkout` may also need an entry in its feed-install list.

Default configuration in a new image does not automatically replace a router's existing persisted UCI files. Account for that distinction when testing changes to defaults.

### Patches and stale build products

The patch application checks whether a patch is already applied. Replacing an already-applied patch with a different patch can conflict with the previous contents in an existing checkout. Keep any dependency-local work before changing it. A separate, freshly prepared checkout is the simplest way to test a revised patch set without resetting existing work; use it instead of retaining two full builds within the 12 GB workspace budget.

ImmortalWrt also caches compiled package contents. After changing a generated iwinfo patch, clean that package before rebuilding through the wrapper:

```bash
make -C deps/openwrt package/network/utils/iwinfo/clean
python3 tools/build_openwrt.py --jobs 8
python3 tools/build_openwrt_firmware.py
python3 tools/package_release.py
```

After changing version-generation logic in `include/version.mk`, clean `base-files` so the next rootfs contains the new metadata:

```bash
make -C deps/openwrt package/base-files/clean
python3 tools/build_openwrt.py --jobs 8
```

Then rebuild the images that need the changed contents. Prefer targeted package cleaning to deleting the toolchain and recompiling everything.

### Diagnosing a failure

| Symptom | Action |
|---|---|
| Missing host tools | Install the reported system packages, then rerun preparation |
| Source revision or feed mismatch | Compare the checkout and `feeds.conf` with `sources.lock.json`; use a matching checkout |
| Patch application fails | Inspect the named patch and existing source changes; preserve local work before resolving the conflict |
| Download fails | Rerun preparation after fixing connectivity or proxy settings; completed archives remain cached |
| Compiler process is killed | Check memory pressure and rerun with fewer jobs |
| A compilation stage fails | Read its `deps/logs/<stage>.log`, fix the first relevant error, and rerun the build wrapper |
| Image utility compilation fails | Read `dist/firmware/utilities-build.log` or `dist/installer/utilities-build.log` |
| Image builder cannot find a compiler or base rootfs | Finish the userspace build first and pass the same OpenWrt directory to every builder |
| New source is present but old behavior remains | Check whether the affected package needs cleaning, whether the correct image was repackaged, and whether persisted device configuration overrides defaults |

## 9. GitHub Actions

`.github/workflows/build-immortalwrt.yml` runs the same four build/packaging steps on an Ubuntu 22.04 runner. It is triggered by a push to `main` that touches `tools/`, `docs/`, `README.md`, `requirements.txt` or the workflow itself, and can also be started manually with **Run workflow**. The workflow restores the ImmortalWrt download cache, uploads the three release files as an artifact and publishes them under the `25.12.2-rax3000h` release tag. A cold first run compiles the cross-toolchain and takes substantially longer than later runs.

## 10. Working with an agent

Use the source map and rebuild table above to scope changes. Preserve the pinned ABI and retained-kernel design unless the task explicitly changes that design. Include any new upstream edits as reproducible project patches or project-owned source files.

Keep credentials, device calibration, extracted vendor files, partition backups and build products out of commits and public release images. The public build starts from upstream sources plus this repository; no local router backup is needed to compile it.

For a source/build task, stop after producing the requested artifacts and reporting their paths. Hardware operations are a separate step: obtain the device owner's authorization before serial interaction, network changes, service reloads, reboots, flashing or slot selection. A completed build does not require a router to be connected.
