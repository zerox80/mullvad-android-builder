#!/usr/bin/env python3
"""Mullvad Android builder. Only the Python standard library is required."""

import argparse
from contextlib import ExitStack
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from urllib.parse import urlsplit
import zipfile

ARCHITECTURES = {
    "arm64": {"arm64-v8a": (2, 183)},
    "all": {"arm64-v8a": (2, 183), "armeabi-v7a": (1, 40),
            "x86": (1, 3), "x86_64": (2, 62)},
}
LOG = None


def say(message):
    print(message, flush=True)
    if LOG:
        LOG.write(str(message) + "\n")
        LOG.flush()


def run(arguments, cwd=None, capture=False):
    if capture:
        return subprocess.check_output(arguments, cwd=cwd, text=True,
                                       stderr=subprocess.STDOUT).strip()
    say("+ " + " ".join(map(str, arguments)))
    with subprocess.Popen(arguments, cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True) as process:
        for line in process.stdout:
            say(line.rstrip("\n"))
        if process.wait():
            raise subprocess.CalledProcessError(process.returncode, arguments)


def validate_source(repo, ref):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+~^{}@-]*", ref):
        raise ValueError("Invalid Git ref: use a branch, tag or commit without whitespace.")
    if repo != "/source":
        url = urlsplit(repo)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Use a public HTTPS repository URL or --source for a local checkout.")
        if any(char.isspace() for char in repo):
            raise ValueError("Repository URLs must not contain whitespace.")


def prepare_source(repo, ref, offline, destination):
    """Fetch new commits into the existing checkout instead of reusing stale refs."""
    destination = Path(destination)
    if repo == "/source":
        run(["git", "config", "--global", "--add", "safe.directory", "/source"])
        run(["git", "config", "--global", "--add", "safe.directory", "/source/.git"])
    new_checkout = not (destination / ".git").is_dir()
    if new_checkout:
        if offline and repo != "/source":
            raise ValueError("Offline build needs an existing source cache or --source.")
        run(["git", "clone", "--no-hardlinks", "--no-checkout", repo, str(destination)])
    if not new_checkout and run(["git", "status", "--porcelain", "--untracked-files=no"], destination, True):
        raise ValueError("Cached checkout has tracked changes; use a separate SOURCE_VOLUME.")
    if not offline or repo == "/source":
        run(["git", "fetch", "--tags", "--no-recurse-submodules", repo, ref], destination)
        commit = run(["git", "rev-parse", "--verify", "FETCH_HEAD^{commit}"], destination, True)
    else:
        commit = run(["git", "rev-parse", "--verify", ref + "^{commit}"], destination, True)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Source did not resolve to a full Git commit.")
    run(["git", "checkout", "--detach", commit], destination)
    return commit


def toolchains():
    return {"java": run(["java", "-version"], capture=True),
            "rust": run(["rustc", "--version"], capture=True),
            "androidSdk": os.environ.get("ANDROID_HOME"),
            "androidNdk": os.environ.get("ANDROID_NDK_HOME")}


def verify_toolchain(source):
    expected = (source / "building/android-container-image.txt").read_text().strip()
    if expected != os.environ.get("BUILDER_BASE_IMAGE"):
        raise ValueError("Source needs a different toolchain. Update base-image.txt to " + expected)
    versions = tomllib.loads((source / "android/gradle/libs.versions.toml").read_text())["versions"]
    rust = tomllib.loads((source / "rust-toolchain.toml").read_text())["toolchain"]["channel"]
    installed = run(["rustup", "toolchain", "list"], source, True)
    if not any(line.startswith(rust + "-") or line.startswith(rust + " ")
               for line in installed.splitlines()):
        raise ValueError("Required Rust toolchain is missing: " + rust)
    sdk = Path(os.environ["ANDROID_HOME"])
    paths = [sdk / "build-tools" / versions["build-tools"], sdk / "ndk" / versions["ndk"]]
    major, minor = versions["compile-sdk-major"], versions["compile-sdk-minor"]
    if not any((sdk / "platforms" / name).is_dir()
               for name in ("android-" + major, "android-" + major + "." + minor)):
        raise ValueError("Required Android platform is missing: " + major + "." + minor)
    for path in paths:
        if not path.is_dir():
            raise ValueError("Required SDK package is missing: " + str(path))
    info = toolchains()
    if not re.search(r'version "' + re.escape(versions["jvm-toolchain"]) + r'[.\"]', info["java"]):
        raise ValueError("JDK version does not match the source requirements.")
    return info


def parse_badging(text):
    package = re.search(r"^package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'", text, re.M)
    if not package:
        raise ValueError("APK package metadata could not be read.")
    if package[1] != "net.mullvad.mullvadvpn":
        raise ValueError("Unexpected APK package: " + package[1])
    if re.search(r"^application-debuggable(?:\s|$)", text, re.M):
        raise ValueError("Refusing a debuggable release APK.")
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", package[3]):
        raise ValueError("Version name cannot be used as an artifact filename.")
    return {"package": package[1], "versionCode": int(package[2]),
            "versionName": package[3], "debuggable": False}


def verify_native(apk, arch):
    expected = ARCHITECTURES[arch]
    with zipfile.ZipFile(apk) as archive:
        libraries = {name.split("/")[1]: name for name in archive.namelist()
                     if re.fullmatch(r"lib/[^/]+/libmullvad_jni\.so", name)}
        if set(libraries) != set(expected):
            raise ValueError("Unexpected core architectures: " + str(sorted(libraries)))
        for abi, name in libraries.items():
            with archive.open(name) as stream:
                header = stream.read(20)
            if len(header) < 20 or header[:4] != b"\x7fELF" or header[5] != 1:
                raise ValueError("Invalid ELF library for " + abi)
            if (header[4], int.from_bytes(header[18:20], "little")) != expected[abi]:
                raise ValueError("ELF architecture mismatch for " + abi)
    return sorted(libraries)


def sha256(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def certificate_fingerprint(text):
    # Build Tools 37 uses "V3.0 Signer:"; older versions use "Signer #1".
    fingerprints = {value.lower() for value in re.findall(
        r"^.+ certificate SHA-256 digest: ([a-fA-F0-9]{64})$", text, re.M)}
    if len(fingerprints) != 1:
        raise ValueError("Expected one verified APK signing certificate.")
    return fingerprints.pop()


def sign_release(apk, keystore, alias):
    apk = Path(apk)
    manifest = json.loads((apk.parent / "build-info.json").read_text())
    if manifest["apk"] != apk.name or manifest["apkSha256"] != sha256(apk):
        raise ValueError("Unsigned APK does not match its build-info.json.")
    if not os.environ.get("ANDROID_KEYSTORE_PASSWORD"):
        raise ValueError("ANDROID_KEYSTORE_PASSWORD is required for signing.")
    parse_badging(run(["aapt2", "dump", "badging", str(apk)], capture=True))
    verify_native(apk, manifest["architecture"])
    run(["zipalign", "-c", "-P", "16", "4", str(apk)])
    output = apk.parent / "release"
    output.mkdir(exist_ok=True)
    signed = output / apk.name.replace("-unsigned.apk", ".apk")
    run(["apksigner", "sign", "--ks", keystore, "--ks-key-alias", alias,
         "--ks-pass", "env:ANDROID_KEYSTORE_PASSWORD", "--out", str(signed), str(apk)])
    certificate = run(["apksigner", "verify", "--verbose", "--print-certs", str(signed)], capture=True)
    fingerprint = certificate_fingerprint(certificate)
    run(["zipalign", "-c", "-P", "16", "4", str(signed)])
    manifest.update({"unsignedApkSha256": manifest["apkSha256"], "apk": signed.name,
                     "apkSha256": sha256(signed), "signed": True,
                     "certificateSha256": fingerprint})
    write_json(output / "release-info.json", manifest)
    (output / "SHA256SUMS").write_text(manifest["apkSha256"] + "  " + signed.name + "\n")
    say("Signed and verified release APK: " + str(signed))
    say("Certificate SHA256: " + fingerprint)


def build(args):
    global LOG
    validate_source(args.repo, args.ref)
    source = Path("/build/repo")
    commit = prepare_source(args.repo, args.ref, args.offline, source)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = Path("/artifacts") / commit[:12] / args.arch / timestamp
    output.mkdir(parents=True)
    with (output / "build.log").open("w") as log:
        LOG = log
        say("Source commit: " + commit)
        say("Artifact directory: " + str(output))
        info = verify_toolchain(source)
        for name, value in info.items():
            say(name + ": " + str(value))
        command = ["bash", "./android/gradlew", "-p", "android", "--console", "plain",
                   "--no-daemon", "--max-workers=" + str(args.workers),
                   "-Dorg.gradle.jvmargs=-Xmx4096m -XX:MaxMetaspaceSize=1024m",
                   "-Pkotlin.compiler.execution.strategy=in-process",
                   "-Pmullvad.app.build.cargo.targets=" + ("arm64" if args.arch == "arm64" else "arm,arm64,x86,x86_64"),
                   "-Pmullvad.app.build.cargo.cleanBuild=false"]
        if args.offline:
            command.append("--offline")
        if args.command == "check":
            command.append("--dry-run")
        command.append("assembleOssProdRelease")
        run(command, source)
        manifest = {"sourceRepository": args.repo, "requestedRef": args.ref,
                    "sourceCommit": commit, "architecture": args.arch,
                    "buildType": "ossProdRelease", "buildStartedUtc": timestamp,
                    "baseImage": os.environ.get("BUILDER_BASE_IMAGE"),
                    "builderImageId": os.environ.get("BUILDER_IMAGE_ID"),
                    "builderScriptSha256": sha256(__file__), "toolchains": info,
                    "gradleCommand": command, "offline": args.offline}
        if args.command == "check":
            manifest["checkOnly"] = True
            write_json(output / "check-info.json", manifest)
            say("Readiness check passed. No APK was assembled.")
            LOG = None
            return
        unsigned = source / "android/app/build/outputs/apk/ossProd/release/app-oss-prod-release-unsigned.apk"
        badging = run(["aapt2", "dump", "badging", str(unsigned)], capture=True)
        manifest.update(parse_badging(badging))
        manifest["coreArchitectures"] = verify_native(unsigned, args.arch)
        name = "MullvadVPN-" + manifest["versionName"] + "-" + args.arch + "-release-unsigned.apk"
        apk = output / name
        run(["zipalign", "-f", "-P", "16", "4", str(unsigned), str(apk)])
        run(["zipalign", "-c", "-P", "16", "4", str(apk)])
        manifest.update({"apk": name, "apkSha256": sha256(apk), "signed": False})
        write_json(output / "build-info.json", manifest)
        (output / "SHA256SUMS").write_text(manifest["apkSha256"] + "  " + name + "\n")
        (output / "apk-badging.txt").write_text(badging + "\n")
        say("Verified unsigned release APK: " + str(apk))
        say("SHA256: " + manifest["apkSha256"])
        LOG = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "check", "doctor", "sign"])
    parser.add_argument("--repo", default="https://github.com/zerox80/mullvadvpn-app.git")
    parser.add_argument("--ref", default="android-pause-vpn")
    parser.add_argument("--arch", choices=ARCHITECTURES, default="arm64")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--workers", type=int, choices=range(1, 33), default=4)
    parser.add_argument("--apk")
    parser.add_argument("--keystore", default="/run/secrets/release.p12")
    parser.add_argument("--alias", default="release")
    args = parser.parse_args()
    if args.offline:
        os.environ["CARGO_NET_OFFLINE"] = "true"
    if args.command == "doctor":
        print(json.dumps(toolchains(), indent=2))
        return
    if args.command == "sign":
        if not args.apk:
            parser.error("sign requires --apk")
        sign_release(args.apk, args.keystore, args.alias)
        return
    Path("/cargo-target").mkdir(exist_ok=True)
    with ExitStack() as stack:
        for path in ("/build/.builder.lock", "/cargo-target/.builder.lock"):
            lock = stack.enter_context(open(path, "w"))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another build is using these caches. Wait or select other volumes.")
        build(args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print("Build failed: " + str(error), file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.output:
            print(error.output, file=sys.stderr)
        sys.exit(1)
