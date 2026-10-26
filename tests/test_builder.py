import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location("builder", Path(__file__).parents[1] / "container/builder.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def elf(abi_class=2, machine=183):
    header = bytearray(20)
    header[:4] = b"\x7fELF"
    header[4:6] = bytes([abi_class, 1])
    header[18:20] = machine.to_bytes(2, "little")
    return header


class ArtifactTests(unittest.TestCase):
    def test_release_metadata(self):
        info = builder.parse_badging("package: name='net.mullvad.mullvadvpn' versionCode='26129000' versionName='2026.12-dev-9a848c'\n")
        self.assertEqual(info["versionName"], "2026.12-dev-9a848c")
        self.assertFalse(info["debuggable"])

    def test_debug_build_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "debuggable"):
            builder.parse_badging("package: name='net.mullvad.mullvadvpn' versionCode='1' versionName='1.0'\napplication-debuggable\n")

    def test_wrong_package_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "package"):
            builder.parse_badging("package: name='com.other.app' versionCode='1' versionName='1.0'")

    def test_unsafe_version_filename_is_rejected(self):
        with self.assertRaises(ValueError):
            builder.parse_badging("package: name='net.mullvad.mullvadvpn' versionCode='1' versionName='../../escape'")

    def check_apk(self, members, arch="arm64"):
        with tempfile.TemporaryDirectory() as folder:
            apk = Path(folder) / "test.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                for name, content in members.items():
                    archive.writestr(name, content)
            return builder.verify_native(apk, arch)

    def test_pixel_native_library(self):
        self.assertEqual(self.check_apk({"lib/arm64-v8a/libmullvad_jni.so": elf()}), ["arm64-v8a"])

    def test_mislabeled_elf_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "mismatch"):
            self.check_apk({"lib/arm64-v8a/libmullvad_jni.so": elf(2, 62)})

    def test_missing_native_library_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "architectures"):
            self.check_apk({"lib/arm64-v8a/unrelated.so": elf()})

    def test_all_architectures_required(self):
        members = {f"lib/{abi}/libmullvad_jni.so": elf(*machine)
                   for abi, machine in builder.ARCHITECTURES["all"].items()}
        self.assertEqual(len(self.check_apk(members, "all")), 4)

    def test_bad_elf_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid ELF"):
            self.check_apk({"lib/arm64-v8a/libmullvad_jni.so": b"bad"})

    def test_checksum(self):
        with tempfile.NamedTemporaryFile() as file:
            file.write(b"abc")
            file.flush()
            self.assertEqual(builder.sha256(file.name), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_signing_rejects_changed_apk(self):
        with tempfile.TemporaryDirectory() as folder:
            apk = Path(folder) / "release-unsigned.apk"
            apk.write_bytes(b"changed apk")
            (apk.parent / "build-info.json").write_text(json.dumps({"apk": apk.name, "apkSha256": "0" * 64}))
            with self.assertRaisesRegex(ValueError, "does not match"):
                builder.sign_release(apk, "/unused/keystore", "release")

    def test_signing_requires_password(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            apk = Path(folder) / "release-unsigned.apk"
            apk.write_bytes(b"apk")
            (apk.parent / "build-info.json").write_text(json.dumps({"apk": apk.name, "apkSha256": builder.sha256(apk)}))
            with self.assertRaisesRegex(ValueError, "PASSWORD"):
                builder.sign_release(apk, "/unused/keystore", "release")

    def test_certificate_output_formats(self):
        digest = "ab" * 32
        for prefix in ("Signer #1", "V3.0 Signer:", "V3.1 Signer:"):
            self.assertEqual(builder.certificate_fingerprint(prefix + " certificate SHA-256 digest: " + digest), digest)

    def test_multiple_certificates_rejected(self):
        with self.assertRaises(ValueError):
            builder.certificate_fingerprint("Signer #1 certificate SHA-256 digest: " + "ab" * 32 +
                                            "\nSigner #2 certificate SHA-256 digest: " + "cd" * 32)


class SourceTests(unittest.TestCase):
    def test_credentials_in_url_rejected(self):
        with self.assertRaises(ValueError):
            builder.validate_source("https://secret@example.com/repo", "main")

    def test_option_injection_rejected(self):
        with self.assertRaises(ValueError):
            builder.validate_source("https://github.com/example/repo.git", "--upload-pack=evil")

    def test_supported_refs(self):
        for ref in ("HEAD", "android-pause-vpn", "android/2026.12", "9a848cd421241d4826ad6183f7a37ab592ba2c85"):
            builder.validate_source("https://github.com/zerox80/mullvadvpn-app.git", ref)

    def test_reused_checkout_fetches_new_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "original"
            cached = Path(folder) / "cached"
            subprocess.run(["git", "init", "-b", "main", str(original)], check=True, capture_output=True)
            for key, value in (("user.name", "Build Test"), ("user.email", "build@example.invalid")):
                subprocess.run(["git", "-C", str(original), "config", key, value], check=True)
            file = original / "file.txt"
            def commit(text):
                file.write_text(text)
                subprocess.run(["git", "-C", str(original), "add", "file.txt"], check=True)
                subprocess.run(["git", "-C", str(original), "commit", "-m", text], check=True, capture_output=True)
                return subprocess.check_output(["git", "-C", str(original), "rev-parse", "HEAD"], text=True).strip()
            first = commit("first")
            self.assertEqual(builder.prepare_source(str(original), "main", False, cached), first)
            second = commit("second")
            self.assertEqual(builder.prepare_source(str(original), "main", False, cached), second)
            self.assertEqual((cached / "file.txt").read_text(), "second")
            (cached / "file.txt").write_text("local edit")
            with self.assertRaisesRegex(ValueError, "tracked changes"):
                builder.prepare_source(str(original), "main", False, cached)

    def test_missing_offline_source_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "Offline"):
                builder.prepare_source("https://github.com/example/repo.git", "main", True, Path(folder) / "repo")


if __name__ == "__main__":
    unittest.main()
