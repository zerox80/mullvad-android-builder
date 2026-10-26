#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo=https://github.com/zerox80/mullvadvpn-app.git
ref=android-pause-vpn
ref_set=false
source_dir=
output_dir="$root/artifacts"
arch=arm64
mode=build
offline=false
workers=4
heap_mb=4096
cache_dir=

usage() {
  cat <<'EOF'
Usage: bash build.sh [options]
  --source DIR        Build committed source from a local Git checkout (read-only).
  --repo URL          Public HTTPS source repository (default: zerox80 fork).
  --ref REF           Branch, tag or full commit (local default: HEAD).
  --arch arm64|all    Pixel release or all four Android architectures.
  --out DIR          Artifact directory (default: ./artifacts).
  --cache-dir DIR    Bind caches to a host directory (for GitHub Actions).
  --workers N        Gradle and Cargo parallelism, 1-32 (default: 4).
  --heap-mb N        Gradle heap in MiB, 1024-16384 (default: 4096).
  --check            Check toolchain and Gradle task graph; no APK build.
  --offline          Disable container networking; require cached dependencies.
  --doctor           Print installed toolchain versions; no source checkout.
  --help             Show this help.

Caches: CACHE_PREFIX, SOURCE_VOLUME, GRADLE_VOLUME, CARGO_REGISTRY_VOLUME,
        CARGO_GIT_VOLUME and CARGO_TARGET_VOLUME may override volume names.
EOF
}
need_value() { [[ $# -ge 2 && -n $2 ]] || { echo "Missing value for $1" >&2; exit 2; }; }
while (($#)); do
  case "$1" in
    --source) need_value "$@"; source_dir=$2; shift 2 ;;
    --repo) need_value "$@"; repo=$2; shift 2 ;;
    --ref) need_value "$@"; ref=$2; ref_set=true; shift 2 ;;
    --arch) need_value "$@"; arch=$2; shift 2 ;;
    --out) need_value "$@"; output_dir=$2; shift 2 ;;
    --cache-dir) need_value "$@"; cache_dir=$2; shift 2 ;;
    --workers) need_value "$@"; workers=$2; shift 2 ;;
    --heap-mb) need_value "$@"; heap_mb=$2; shift 2 ;;
    --check) mode=check; shift ;;
    --doctor) mode=doctor; shift ;;
    --offline) offline=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ $arch == arm64 || $arch == all ]] || { echo 'Architecture must be arm64 or all.' >&2; exit 2; }
prefix=${CACHE_PREFIX:-mullvad-builder-$arch}
[[ $workers =~ ^([1-9]|[12][0-9]|3[0-2])$ ]] || { echo 'Workers must be 1-32.' >&2; exit 2; }
[[ $heap_mb =~ ^[0-9]{4,5}$ && $heap_mb -ge 1024 && $heap_mb -le 16384 ]] || { echo 'Heap must be 1024-16384 MiB.' >&2; exit 2; }
command -v docker >/dev/null || { echo 'Docker is required. On Windows run Build.ps1 with WSL/Ubuntu.' >&2; exit 1; }
[[ $(docker info --format '{{.OSType}}') == linux ]] || { echo 'Use a Linux Docker engine.' >&2; exit 1; }
base=$(tr -d '\r\n' < "$root/base-image.txt")
[[ $base =~ ^ghcr\.io/mullvad/mullvadvpn-app-build-android@sha256:[a-f0-9]{64}$ ]] || {
  echo 'base-image.txt must contain a pinned Mullvad Android image digest.' >&2; exit 1;
}
image="mullvad-android-builder:local"
if $offline; then
  docker image inspect "$base" >/dev/null || { echo 'Offline mode requires the base image locally.' >&2; exit 1; }
fi
docker build --platform linux/amd64 --pull=false --network none --build-arg "BASE_IMAGE=$base" -t "$image" "$root"
mkdir -p -- "$output_dir"
output_dir=$(cd -- "$output_dir" && pwd)
[[ $output_dir != *,* ]] || { echo 'Docker bind paths must not contain commas.' >&2; exit 2; }
args=(run --rm --init --platform linux/amd64
  --hostname mullvad-android-builder --add-host mullvad-android-builder:127.0.0.1
  --mount "type=bind,source=$output_dir,target=/artifacts"
  --env "CARGO_BUILD_JOBS=$workers"
  --env "BUILDER_IMAGE_ID=$(docker image inspect --format '{{.Id}}' "$image")")
if [[ -n $cache_dir ]]; then
  mkdir -p -- "$cache_dir"
  cache_dir=$(cd -- "$cache_dir" && pwd)
  [[ $cache_dir != *,* ]] || { echo 'Docker bind paths must not contain commas.' >&2; exit 2; }
  for directory in source gradle cargo-registry cargo-git cargo-target; do mkdir -p -- "$cache_dir/$directory"; done
  args+=(--mount "type=bind,source=$cache_dir/source,target=/build"
    --mount "type=bind,source=$cache_dir/gradle,target=/root/.gradle"
    --mount "type=bind,source=$cache_dir/cargo-registry,target=/root/.cargo/registry"
    --mount "type=bind,source=$cache_dir/cargo-git,target=/root/.cargo/git"
    --mount "type=bind,source=$cache_dir/cargo-target,target=/cargo-target")
else
  args+=(--mount "type=volume,source=${SOURCE_VOLUME:-$prefix-source},target=/build"
    --mount "type=volume,source=${GRADLE_VOLUME:-$prefix-gradle},target=/root/.gradle"
    --mount "type=volume,source=${CARGO_REGISTRY_VOLUME:-$prefix-cargo-registry},target=/root/.cargo/registry"
    --mount "type=volume,source=${CARGO_GIT_VOLUME:-$prefix-cargo-git},target=/root/.cargo/git"
    --mount "type=volume,source=${CARGO_TARGET_VOLUME:-$prefix-cargo-target},target=/cargo-target")
fi
if [[ -n $source_dir ]]; then
  source_dir=$(cd -- "$source_dir" && pwd)
  [[ $source_dir != *,* ]] || { echo 'Docker bind paths must not contain commas.' >&2; exit 2; }
  args+=(--mount "type=bind,source=$source_dir,target=/source,readonly")
  repo=/source
  if ! $ref_set; then ref=HEAD; fi
fi
if $offline; then args+=(--network none --env CARGO_NET_OFFLINE=true); fi
command_args=("$mode" --repo "$repo" --ref "$ref" --arch "$arch" --workers "$workers" --heap-mb "$heap_mb")
if $offline; then command_args+=(--offline); fi
exec docker "${args[@]}" "$image" "${command_args[@]}"
