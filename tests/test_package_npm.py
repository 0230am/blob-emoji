from __future__ import annotations

import json
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import package_npm


class PackageMetadataTest(unittest.TestCase):
    def test_version_is_immutable_semver_projection(self) -> None:
        version = package_npm.package_version("17.0.0-528935cd")
        self.assertEqual(version, "17.0.0-528935cd.0")
        self.assertIsNotNone(package_npm.SEMVER.fullmatch(version))

    def test_asset_only_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            (bundle / "manifest.json").write_text(
                json.dumps({"bundle_id": "17.0.0-528935cd"}),
                encoding="utf-8",
            )
            value = package_npm.metadata(bundle)

        self.assertEqual(value["name"], "@0230am/blob-emoji")
        self.assertEqual(value["version"], "17.0.0-528935cd.0")
        self.assertIs(value["private"], False)
        self.assertEqual(value["bundleId"], "17.0.0-528935cd")
        self.assertEqual(
            value["publicBaseUrl"],
            "https://static.0230.am/clover/emoji/v1/17.0.0-528935cd/",
        )
        self.assertEqual(value["publishConfig"]["registry"], "https://registry.npmjs.org/")
        forbidden = {
            "main", "module", "browser", "bin", "exports", "scripts",
            "dependencies", "optionalDependencies", "peerDependencies",
        }
        self.assertFalse(forbidden & value.keys())

    def test_package_projection_and_tarball_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            package_root = root / "package"
            archive = root / "blob-emoji.tgz"
            payloads = {
                "README.md": b"bundle readme\n",
                "manifest.json": json.dumps({"bundle_id": "17.0.0-528935cd"}).encode(),
                "assets.json": b"{}\n",
                "picker.json": b"{}\n",
                "clover-picker.json": b"{}\n",
                "text-manifest.json": b"{}\n",
                "atlas/atlas-0.webp": b"atlas",
                "svg/1f600.svg": b"<svg/>",
                "LICENSES/upstream-svg-LICENSE": b"license",
            }
            for relative, payload in payloads.items():
                path = bundle / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            checksum_payload = "".join(
                f"{sha256(payload).hexdigest()}  {relative}\n"
                for relative, payload in sorted(payloads.items())
            ).encode("ascii")
            (bundle / "checksums.sha256").write_bytes(
                checksum_payload,
            )
            payloads["checksums.sha256"] = checksum_payload

            with patch.object(package_npm, "verify_source_bundle"):
                metadata = package_npm.create_package(bundle, package_root)
                package_npm.create_tarball(package_root, archive, epoch=1_700_000_000)
                package_npm.verify_tarball(bundle, package_root, archive)

            self.assertEqual(metadata["version"], "17.0.0-528935cd.0")
            actual = {
                path.relative_to(package_root).as_posix()
                for path in package_root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual, set(payloads) | {"package.json"})

    def test_verify_only_accepts_installs_outside_repository(self) -> None:
        script = Path(package_npm.__file__).resolve()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            installed = root / "external-node-modules" / "@0230am" / "blob-emoji"
            archive = root / "blob-emoji.tgz"
            bundle.mkdir()
            installed.mkdir(parents=True)
            archive.write_bytes(b"not opened in this test")
            with (
                patch.object(
                    package_npm,
                    "verify_package",
                    return_value={
                        "name": "@0230am/blob-emoji",
                        "version": "17.0.0-528935cd.0",
                        "bundleId": "17.0.0-528935cd",
                        "publicBaseUrl": "https://static.0230.am/clover/emoji/v1/17.0.0-528935cd/",
                        "publishConfig": {"registry": "https://registry.npmjs.org/"},
                    },
                ),
                patch.object(package_npm, "verify_tarball"),
                patch.object(sys, "argv", [str(script), "--verify-only", str(bundle), str(installed), str(archive)]),
            ):
                self.assertEqual(package_npm.main(), 0)


if __name__ == "__main__":
    unittest.main()
