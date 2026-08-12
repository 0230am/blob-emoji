#!/usr/bin/env python3
"""Create a reproducible Blob Emoji release archive and sibling checksum."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import subprocess
import tarfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    archive = args.archive.resolve()
    if not bundle.is_dir():
        parser.error("bundle must be an existing directory")
    try:
        archive.relative_to(bundle)
    except ValueError:
        pass
    else:
        parser.error("archive must be outside the bundle directory")
    repo = Path(__file__).resolve().parents[1]
    epoch = int(subprocess.check_output(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=repo, text=True).strip())
    files = sorted((path for path in bundle.rglob("*") if path.is_file()), key=lambda path: path.relative_to(bundle).as_posix())

    archive.parent.mkdir(parents=True, exist_ok=True)
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
                directories = {Path("blob-emoji")}
                for path in files:
                    relative = path.relative_to(bundle)
                    for parent in relative.parents:
                        if parent != Path("."):
                            directories.add(Path("blob-emoji") / parent)
                entries = [(directory.as_posix() + "/", None) for directory in directories]
                entries.extend(((Path("blob-emoji") / path.relative_to(bundle)).as_posix(), path) for path in files)
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
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_name(archive.name + ".sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii", newline="\n")
    print(f"Wrote {archive} ({archive.stat().st_size} bytes, sha256 {digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
