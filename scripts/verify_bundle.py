#!/usr/bin/env python3
"""Verify a generated Blob Emoji distribution bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path, PurePosixPath

from bundle_artifacts import (
    ATLAS_COLUMNS,
    ATLAS_SIZE,
    CELL_SIZE,
    CELLS_PER_ATLAS,
    atlas_file_count,
    safe_relative_path,
)

try:
    from PIL import Image
except ImportError as error:  # pragma: no cover
    raise SystemExit("Bundle image dependencies are missing; install requirements-bundle.txt") from error


HEX_SEQUENCE = re.compile(r"[0-9A-F]+(?:-[0-9A-F]+)*")
BUNDLE_ID = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+-[a-f0-9]{8}$")
REPRESENTATIVE_SEQUENCES = {
    "1F600",  # simple
    "2764-FE0F",  # variation selector
    "1F469-200D-1F4BB",  # ZWJ
    "1F44D-1F3FD",  # skin tone
    "0023-FE0F-20E3",  # keycap
    "1F1FA-1F1F8",  # region flag
}


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read valid JSON from {path}") from error


def verify_checksums(root: Path) -> None:
    checksum_path = root / "checksums.sha256"
    entries: dict[str, str] = {}
    previous = ""
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise ValueError(f"invalid checksum line: {line!r}")
        digest, relative = match.groups()
        safe_relative_path(relative)
        if relative <= previous or relative in entries:
            raise ValueError("checksum paths are not strictly sorted and unique")
        previous = relative
        entries[relative] = digest

    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != checksum_path
    }
    symlinks = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_symlink())
    if symlinks:
        raise ValueError(f"bundle contains symbolic links: {symlinks}")
    if actual != set(entries):
        missing = sorted(set(entries) - actual)
        unexpected = sorted(actual - set(entries))
        raise ValueError(f"checksum file set mismatch; missing={missing}, unexpected={unexpected}")
    for relative, expected in entries.items():
        actual_digest = hashlib.sha256((root / Path(*PurePosixPath(relative).parts)).read_bytes()).hexdigest()
        if actual_digest != expected:
            raise ValueError(f"checksum mismatch: {relative}")


def asset_path(root: Path, sequence: str) -> Path:
    key = "-".join(point for point in sequence.split("-") if point != "FE0F").lower()
    return root / "svg" / f"{key}.svg"


def cell_has_alpha(image: Image.Image, slot: int) -> bool:
    local_slot = slot % CELLS_PER_ATLAS
    row, column = divmod(local_slot, ATLAS_COLUMNS)
    alpha = image.getchannel("A").crop(
        (column * CELL_SIZE, row * CELL_SIZE, (column + 1) * CELL_SIZE, (row + 1) * CELL_SIZE)
    )
    return alpha.getbbox() is not None


def verify_bundle(root: Path) -> None:
    required = {
        "README.md", "manifest.json", "assets.json", "picker.json", "clover-picker.json",
        "text-manifest.json", "checksums.sha256",
    }
    absent = sorted(name for name in required if not (root / name).is_file())
    if absent:
        raise ValueError(f"required bundle files are missing: {absent}")

    manifest = load_json(root / "manifest.json")
    assets = load_json(root / "assets.json")
    picker = load_json(root / "picker.json")
    clover = load_json(root / "clover-picker.json")
    text_manifest = load_json(root / "text-manifest.json")
    if not all(isinstance(value, dict) for value in (manifest, assets, picker, clover, text_manifest)):
        raise ValueError("bundle contracts must be JSON objects")
    if manifest.get("schema_version") != 1 or picker.get("schema_version") != 1 or clover.get("schema_version") != 1:
        raise ValueError("unsupported or undocumented picker/manifest schema")
    bundle_id = manifest.get("bundle_id")
    source = manifest.get("source")
    if not isinstance(bundle_id, str) or not BUNDLE_ID.fullmatch(bundle_id):
        raise ValueError(f"invalid bundle ID: {bundle_id!r}")
    if not isinstance(source, dict) or not isinstance(source.get("commit"), str):
        raise ValueError("manifest source commit is missing")
    expected_bundle_id = f"{manifest.get('emojibase_version')}-{source['commit'][:8]}"
    if bundle_id != expected_bundle_id:
        raise ValueError(
            f"bundle ID must derive from Emojibase version and source commit: expected {expected_bundle_id!r}"
        )
    manifest_paths = {
        "picker": "clover-picker.json",
        "source_picker": "picker.json",
        "text_manifest": "text-manifest.json",
        "checksums": "checksums.sha256",
    }
    for field, expected_path in manifest_paths.items():
        value = manifest.get(field)
        if value != expected_path:
            raise ValueError(f"unexpected manifest path for {field}: {value!r}")
        safe_relative_path(value)
    if picker.get("record_fields") != ["sequence_index", "name_string_index", "group_index", "subgroup_index", "keyword_string_indexes", "skin_variants"]:
        raise ValueError("unexpected source picker field declaration")
    if clover.get("record_fields") != ["sequence", "label_string_index", "group_index", "keyword_string_indexes", "shortcodes"]:
        raise ValueError("unexpected Clover picker field declaration")

    groups = clover.get("groups")
    strings = clover.get("strings")
    rows = clover.get("emoji")
    if not isinstance(groups, list) or not isinstance(strings, list) or not isinstance(rows, list):
        raise ValueError("invalid Clover picker arrays")
    seen_sequences: set[str] = set()
    for slot, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != 5:
            raise ValueError(f"invalid Clover picker row at slot {slot}")
        sequence, label_index, group_index, keyword_indexes, shortcodes = row
        if not isinstance(sequence, str) or not HEX_SEQUENCE.fullmatch(sequence) or sequence in seen_sequences:
            raise ValueError(f"invalid or duplicate sequence at slot {slot}")
        seen_sequences.add(sequence)
        if not isinstance(label_index, int) or not 0 <= label_index < len(strings):
            raise ValueError(f"label index out of bounds at slot {slot}")
        if not isinstance(group_index, int) or not 0 <= group_index < len(groups):
            raise ValueError(f"group index out of bounds at slot {slot}")
        if not isinstance(keyword_indexes, list) or any(not isinstance(i, int) or not 0 <= i < len(strings) for i in keyword_indexes):
            raise ValueError(f"keyword index out of bounds at slot {slot}")
        if not isinstance(shortcodes, list) or any(not isinstance(code, str) or not code for code in shortcodes):
            raise ValueError(f"invalid shortcode list at slot {slot}")

    expected_groups = [[group["key"], group["message"], group["order"]] for group in picker.get("groups", [])]
    if groups != expected_groups or strings != picker.get("strings"):
        raise ValueError("Clover picker is not a projection of the source picker")
    source_sequences = picker.get("sequences", [])
    source_rows = picker.get("emoji", [])
    projected_sequences = [source_sequences[row[0]] for row in source_rows]
    if [row[0] for row in rows] != projected_sequences:
        raise ValueError("Clover picker row order differs from source picker/atlas slot order")

    text_sequences = text_manifest.get("sequences")
    missing = assets.get("missing")
    if text_manifest.get("missing") != missing or not isinstance(text_sequences, dict) or not isinstance(missing, list):
        raise ValueError("text manifest missing report differs from assets manifest")
    for row in rows:
        if text_sequences.get(row[0]) != strings[row[1]]:
            raise ValueError(f"text label differs from picker label: {row[0]}")
    absent_representatives = sorted(REPRESENTATIVE_SEQUENCES - set(text_sequences))
    if absent_representatives:
        raise ValueError(f"representative Unicode sequence coverage is missing: {absent_representatives}")
    for sequence in REPRESENTATIVE_SEQUENCES:
        if sequence not in set(missing) and not asset_path(root, sequence).is_file():
            raise ValueError(f"representative sequence does not resolve to artwork: {sequence}")

    atlas = manifest.get("atlas", {})
    if not isinstance(atlas, dict):
        raise ValueError("invalid atlas manifest")
    atlas_files = atlas.get("files")
    if atlas.get("size") != ATLAS_SIZE or atlas.get("cell_size") != CELL_SIZE:
        raise ValueError("unexpected atlas geometry")
    if not isinstance(atlas_files, list) or len(atlas_files) != atlas_file_count(len(rows)):
        raise ValueError("atlas count does not match picker slot capacity")
    opened: list[Image.Image] = []
    try:
        for relative in atlas_files:
            safe_relative_path(relative)
            image = Image.open(root / Path(*PurePosixPath(relative).parts))
            image.load()
            if image.size != (ATLAS_SIZE, ATLAS_SIZE) or "A" not in image.getbands():
                raise ValueError(f"atlas lacks declared dimensions or alpha channel: {relative}")
            opened.append(image.convert("RGBA"))
        missing_set = set(missing)
        boundary_slots = {0, 1023, 1024, len(rows) - 1} & set(range(len(rows)))
        for slot, row in enumerate(rows):
            sequence = row[0]
            has_svg = asset_path(root, sequence).is_file()
            if has_svg == (sequence in missing_set):
                raise ValueError(f"asset/missing declaration disagrees for {sequence}")
            populated = cell_has_alpha(opened[slot // CELLS_PER_ATLAS], slot)
            if populated != has_svg:
                label = "boundary slot" if slot in boundary_slots else "slot"
                raise ValueError(f"{label} {slot} transparency disagrees with artwork for {sequence}")
    finally:
        for image in opened:
            image.close()

    licenses = root / "LICENSES"
    if not licenses.is_dir() or len(list(licenses.iterdir())) < 6:
        raise ValueError("required licenses and attribution are missing")
    verify_checksums(root)


def verify_archive(path: Path) -> None:
    with tarfile.open(path, "r:gz") as archive:
        names = []
        for member in archive.getmembers():
            normalized_name = member.name[:-1] if member.name.endswith("/") else member.name
            pure = safe_relative_path(normalized_name)
            if pure.parts[0] != "blob-emoji" or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise ValueError(f"unexpected archive member: {member.name!r}")
            names.append(member.name)
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("archive entries are not sorted and unique")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    verify_bundle(args.bundle.resolve())
    if args.archive:
        verify_archive(args.archive.resolve())
    print(f"Verified Blob Emoji bundle: {args.bundle}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
