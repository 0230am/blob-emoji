#!/usr/bin/env python3
"""Create and verify the asset-only npm package from a verified bundle."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath

PACKAGE_NAME = "@0230am/blob-emoji"
REGISTRY = "https://registry.npmjs.org/"
PUBLIC_BASE_ROOT = "https://static.0230.am/clover/emoji/v1"
PACKAGE_ENTRIES = (
    "README.md",
    "manifest.json",
    "assets.json",
    "picker.json",
    "clover-picker.json",
    "text-manifest.json",
    "checksums.sha256",
    "atlas",
    "svg",
    "LICENSES",
)
REQUIRED_ASSET_ENTRIES = {
    "manifest.json",
    "assets.json",
    "clover-picker.json",
    "text-manifest.json",
    "atlas",
    "svg",
    "LICENSES",
}
BUNDLE_ID = re.compile(r"^(?P<base>[0-9]+\.[0-9]+\.[0-9]+)-(?P<revision>[a-f0-9]{8})$")
SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def verify_source_bundle(bundle: Path) -> None:
    # Keep metadata/version helpers usable without loading the pinned image stack.
    from verify_bundle import verify_bundle

    verify_bundle(bundle)


def package_version(bundle_id: str) -> str:
    match = BUNDLE_ID.fullmatch(bundle_id)
    if not match:
        raise ValueError(f"cannot derive npm version from bundle ID: {bundle_id!r}")
    return f"{match.group('base')}-{match.group('revision')}.0"


def bundle_checksums(bundle: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (bundle / "checksums.sha256").read_text(encoding="ascii").splitlines():
        digest, relative = line.split("  ", 1)
        result[relative] = digest
    return result


def expected_payload_files(bundle: Path) -> set[str]:
    files: set[str] = set()
    for entry in PACKAGE_ENTRIES:
        source = bundle / entry
        if source.is_file():
            files.add(entry)
        elif source.is_dir():
            files.update(path.relative_to(bundle).as_posix() for path in source.rglob("*") if path.is_file())
        else:
            raise ValueError(f"required package source is missing: {entry}")
    actual_bundle_files = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file()
    }
    if files != actual_bundle_files:
        raise ValueError(
            "package entry list does not exactly cover the verified bundle; "
            f"missing={sorted(actual_bundle_files - files)}, unexpected={sorted(files - actual_bundle_files)}"
        )
    return files


def metadata(bundle: Path) -> dict[str, object]:
    manifest = load_json(bundle / "manifest.json")
    bundle_id = manifest.get("bundle_id")
    if not isinstance(bundle_id, str):
        raise ValueError("bundle manifest has no bundle_id")
    version = package_version(bundle_id)
    return {
        "name": PACKAGE_NAME,
        "version": version,
        "description": "Asset-only Blob Emoji bundle for Clover",
        "private": False,
        "license": "Apache-2.0",
        "repository": {
            "type": "git",
            "url": "git+https://github.com/0230am/blob-emoji.git",
        },
        "homepage": "https://github.com/0230am/blob-emoji",
        "bundleId": bundle_id,
        "publicBaseUrl": f"{PUBLIC_BASE_ROOT}/{bundle_id}/",
        "files": list(PACKAGE_ENTRIES),
        "publishConfig": {
            "access": "public",
            "registry": REGISTRY,
        },
    }


def create_package(bundle: Path, package_root: Path) -> dict[str, object]:
    verify_source_bundle(bundle)
    absent = sorted(entry for entry in REQUIRED_ASSET_ENTRIES if not (bundle / entry).exists())
    if absent:
        raise ValueError(f"required asset package entries are missing: {absent}")
    expected_payload_files(bundle)
    try:
        package_root.relative_to(bundle)
    except ValueError:
        pass
    else:
        raise ValueError("package output must be outside the verified bundle")
    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True)
    for entry in PACKAGE_ENTRIES:
        source = bundle / entry
        destination = package_root / entry
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
    package_metadata = metadata(bundle)
    (package_root / "package.json").write_text(
        json.dumps(package_metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    verify_package(bundle, package_root)
    return package_metadata


def verify_package(bundle: Path, package_root: Path) -> dict[str, object]:
    verify_source_bundle(bundle)
    expected_files = expected_payload_files(bundle)
    actual_files = {
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*")
        if path.is_file()
    }
    expected_package_files = expected_files | {"package.json"}
    if actual_files != expected_package_files:
        raise ValueError(
            "npm package file set mismatch; "
            f"missing={sorted(expected_package_files - actual_files)}, "
            f"unexpected={sorted(actual_files - expected_package_files)}"
        )
    symlinks = sorted(path.relative_to(package_root).as_posix() for path in package_root.rglob("*") if path.is_symlink())
    if symlinks:
        raise ValueError(f"npm package contains symbolic links: {symlinks}")

    checksums = bundle_checksums(bundle)
    for relative in sorted(expected_files):
        if relative == "checksums.sha256":
            if (package_root / relative).read_bytes() != (bundle / relative).read_bytes():
                raise ValueError("packaged checksum index differs from verified bundle")
            continue
        expected = checksums.get(relative)
        if expected is None:
            raise ValueError(f"packaged bundle file is absent from checksums.sha256: {relative}")
        digest = hashlib.sha256((package_root / Path(*PurePosixPath(relative).parts)).read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError(f"packaged file differs from verified bundle: {relative}")

    package_metadata = load_json(package_root / "package.json")
    if package_metadata != metadata(bundle):
        raise ValueError("package.json does not match metadata derived from the bundle")
    if not SEMVER.fullmatch(str(package_metadata.get("version", ""))):
        raise ValueError("package version is not SemVer-compatible")
    forbidden = {"main", "module", "browser", "bin", "exports", "scripts", "dependencies", "optionalDependencies", "peerDependencies"}
    present = sorted(forbidden & package_metadata.keys())
    if present:
        raise ValueError(f"asset-only package has executable entry points, scripts, or dependencies: {present}")
    return package_metadata


def git_epoch(repo: Path) -> int:
    return int(subprocess.check_output(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=repo, text=True).strip())


def create_tarball(package_root: Path, archive: Path, epoch: int) -> None:
    files = sorted((path for path in package_root.rglob("*") if path.is_file()), key=lambda path: path.relative_to(package_root).as_posix())
    directories = {Path("package")}
    for path in files:
        for parent in path.relative_to(package_root).parents:
            if parent != Path("."):
                directories.add(Path("package") / parent)
    entries: list[tuple[str, Path | None]] = [(path.as_posix() + "/", None) for path in directories]
    entries.extend(((Path("package") / path.relative_to(package_root)).as_posix(), path) for path in files)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for name, path in sorted(entries):
                    info = tarfile.TarInfo(name)
                    info.mtime = epoch
                    info.uid = info.gid = 0
                    info.uname = info.gname = "root"
                    if path is None:
                        info.type = tarfile.DIRTYPE
                        info.mode = 0o755
                        tar.addfile(info)
                    else:
                        data = path.read_bytes()
                        info.size = len(data)
                        info.mode = 0o644
                        tar.addfile(info, io.BytesIO(data))


def verify_tarball(bundle: Path, package_root: Path, archive: Path) -> None:
    expected = {
        f"package/{path.relative_to(package_root).as_posix()}"
        for path in package_root.rglob("*")
        if path.is_file()
    }
    actual: set[str] = set()
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if [member.name for member in members] != sorted(member.name for member in members):
            raise ValueError("npm tarball entries are not sorted")
        for member in members:
            if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise ValueError(f"unexpected npm tarball member: {member.name}")
            if member.isfile():
                actual.add(member.name)
                source = package_root / Path(*PurePosixPath(member.name).parts[1:])
                extracted = tar.extractfile(member)
                if extracted is None or extracted.read() != source.read_bytes():
                    raise ValueError(f"npm tarball content mismatch: {member.name}")
    if actual != expected:
        raise ValueError(f"npm tarball file set mismatch; missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}")
    verify_package(bundle, package_root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("package_root", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    package_root = args.package_root.resolve()
    archive = args.archive.resolve()
    repo = Path(__file__).resolve().parents[1]
    if not args.verify_only:
        try:
            package_root.relative_to(repo)
        except ValueError:
            parser.error("package_root must be inside this repository when creating a package")
        if package_root in {repo, repo / ".git"}:
            parser.error("package_root must name a package directory, not the repository")
    try:
        archive.relative_to(package_root)
    except ValueError:
        pass
    else:
        parser.error("archive must be outside package_root")
    if args.verify_only:
        package_metadata = verify_package(bundle, package_root)
        verify_tarball(bundle, package_root, archive)
    else:
        package_metadata = create_package(bundle, package_root)
        create_tarball(package_root, archive, git_epoch(repo))
        verify_tarball(bundle, package_root, archive)
    print(
        f"Verified npm package {package_metadata['name']}@{package_metadata['version']} "
        f"for {package_metadata['publishConfig']['registry']}: {archive}\n"
        f"bundleId={package_metadata['bundleId']}\n"
        f"publicBaseUrl={package_metadata['publicBaseUrl']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, tarfile.TarError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
