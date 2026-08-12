#!/usr/bin/env python3
"""Build compact picker metadata and deterministic Blob Emoji SVG assets."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from bundle_artifacts import (
    ATLAS_SIZE,
    CELL_SIZE,
    WEBP_ALPHA_QUALITY,
    WEBP_METHOD,
    WEBP_QUALITY,
    WEBP_VERSION,
    build_atlases,
    write_checksums,
)

EMOJI_VERSION = "17.0"
EMOJI_TEST_URL = "https://www.unicode.org/Public/17.0.0/emoji/emoji-test.txt"
EMOJIBASE_VERSION = "17.0.0"
EMOJIBASE_BASE_URL = f"https://cdn.jsdelivr.net/npm/emojibase-data@{EMOJIBASE_VERSION}"
EMOJIBASE_URLS = {
    "data": f"{EMOJIBASE_BASE_URL}/en/data.json",
    "messages": f"{EMOJIBASE_BASE_URL}/en/messages.json",
    "emojibase_shortcodes": f"{EMOJIBASE_BASE_URL}/en/shortcodes/emojibase.json",
    "joypixels_shortcodes": f"{EMOJIBASE_BASE_URL}/en/shortcodes/joypixels.json",
    "license": f"{EMOJIBASE_BASE_URL}/LICENSE",
}
UNSAFE_TAGS = {"script", "foreignobject", "iframe", "object", "embed", "audio", "video", "image"}
UNSAFE_STYLE = re.compile(r"(?:@import|expression\s*\()", re.IGNORECASE)
URL_START = re.compile(r"url\s*\(", re.IGNORECASE)
URL_REFERENCE = re.compile(r"url\s*\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
SAFE_FRAGMENT = re.compile(r"#[A-Za-z_][A-Za-z0-9_.:-]*")
PAINT_ELEMENTS = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text"}


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "blob-emoji-bundler/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def sequence_key(codepoints: list[str]) -> str:
    return "-".join(f"{int(point, 16):04X}" for point in codepoints)


def parse_emoji_test(payload: bytes) -> dict[str, dict[str, str]]:
    sequences: dict[str, dict[str, str]] = {}
    group = ""
    subgroup = ""
    for raw_line in payload.decode("utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("# group:"):
            group = line.split(":", 1)[1].strip()
            continue
        if line.startswith("# subgroup:"):
            subgroup = line.split(":", 1)[1].strip()
            continue
        if not line or line.startswith("#") or ";" not in line or "#" not in line:
            continue
        left, comment = line.split("#", 1)
        codepoints, qualification = left.split(";", 1)
        if qualification.strip() != "fully-qualified":
            continue
        # The final field is the Unicode short name; retain it as diagnostic data.
        fields = comment.strip().split(maxsplit=2)
        if not group or not subgroup:
            raise ValueError("emoji-test.txt sequence appeared before group metadata")
        sequences[sequence_key(codepoints.split())] = {
            "emoji_test_name": fields[2] if len(fields) == 3 else "",
            "group": group,
            "subgroup": subgroup,
        }
    if not sequences:
        raise ValueError("emoji-test.txt contained no fully-qualified sequences")
    return sequences


def asset_key(path: Path) -> str | None:
    # Upstream uses a leading `u` only for the first code point, e.g.
    # emoji_u1f3c3_1f3fb_200d_2640.svg.  It also conventionally omits FE0F.
    match = re.fullmatch(r"emoji_(u?[0-9a-f]+(?:_u?[0-9a-f]+)*)\.svg", path.name, re.IGNORECASE)
    if not match:
        return None
    return sequence_key([part.removeprefix("u") for part in match.group(1).split("_")])


def resolve_svg_alias(source: Path, asset_directory: Path) -> Path:
    """Resolve upstream filename aliases without allowing directory traversal."""
    root = asset_directory.resolve()
    current = source.resolve()
    visited = set()
    while True:
        if current in visited:
            raise ValueError(f"cyclic SVG alias starting at {source}")
        visited.add(current)
        raw = current.read_bytes()
        if raw.lstrip().startswith(b"<"):
            return current
        try:
            alias = raw.decode("ascii").strip()
        except UnicodeDecodeError as error:
            raise ValueError(f"invalid non-XML SVG asset: {current}") from error
        if not re.fullmatch(r"emoji_u[0-9a-f]+(?:_[0-9a-f]+)*\.svg", alias, re.IGNORECASE):
            raise ValueError(f"invalid SVG alias in {current}: {alias!r}")
        target = (current.parent / alias).resolve()
        if target.parent != root or not target.is_file():
            raise ValueError(f"SVG alias escapes its asset directory or is missing: {current} -> {alias}")
        current = target


def without_emoji_vs(sequence: str) -> str:
    """Match upstream's FE0F-elided filename convention without changing the key."""
    return "-".join(point for point in sequence.split("-") if point != "FE0F")


def normalized_expected_keys(expected: dict[str, object]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key in expected:
        compact = without_emoji_vs(key)
        if compact in normalized and normalized[compact] != key:
            raise ValueError(f"Emoji sequences collide after FE0F removal: {normalized[compact]} and {key}")
        normalized[compact] = key
    return normalized


def resolve_expected_key(hexcode: str, normalized: dict[str, str]) -> str | None:
    return normalized.get(without_emoji_vs(hexcode.upper()))


def parse_shortcode_dataset(payload: bytes) -> dict[str, list[str]]:
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("Emojibase shortcode dataset must be an object")
    result: dict[str, list[str]] = {}
    for hexcode, value in parsed.items():
        aliases = value if isinstance(value, list) else [value]
        if not aliases or not all(isinstance(alias, str) and alias for alias in aliases):
            raise ValueError(f"Invalid Emojibase shortcodes for {hexcode}")
        result[hexcode.upper()] = aliases
    return result


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def fragment_references(value: str) -> set[str]:
    references = set()
    for match in URL_REFERENCE.finditer(value):
        target = match.group(2).strip()
        if SAFE_FRAGMENT.fullmatch(target):
            references.add(target[1:])
    return references


def has_unsafe_url(value: str) -> bool:
    matches = list(URL_REFERENCE.finditer(value))
    if len(matches) != len(URL_START.findall(value)):
        return True
    return any(not SAFE_FRAGMENT.fullmatch(match.group(2).strip()) for match in matches)


def sanitize_inline_style(value: str) -> str:
    declarations = []
    for declaration in value.split(";"):
        declaration = declaration.strip()
        if not declaration or ":" not in declaration:
            continue
        name, css_value = declaration.split(":", 1)
        name = name.strip()
        css_value = css_value.strip()
        if not re.fullmatch(r"--?[A-Za-z][A-Za-z0-9-]*|[A-Za-z][A-Za-z0-9-]*", name):
            continue
        if UNSAFE_STYLE.search(css_value) or has_unsafe_url(css_value):
            continue
        declarations.append(f"{name}:{css_value}")
    return ";".join(declarations)


def tree_fragment_references(root: ET.Element) -> set[str]:
    references = set()
    for element in root.iter():
        for attribute, value in element.attrib.items():
            references.update(fragment_references(value))
            if local_name(attribute) == "href" and SAFE_FRAGMENT.fullmatch(value.strip()):
                references.add(value.strip()[1:])
        if local_name(element.tag) == "style" and element.text:
            references.update(fragment_references(element.text))
    return references


def sanitize_svg(source: Path, destination: Path) -> None:
    raw = source.read_bytes()
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError(f"unsafe XML declaration in {source}")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ValueError(f"invalid SVG XML in {source}: {error}") from error
    if local_name(root.tag) != "svg":
        raise ValueError(f"not an SVG root: {source}")

    gradient_ids = {
        element.attrib["id"]
        for element in root.iter()
        if local_name(element.tag) in {"lineargradient", "radialgradient"} and "id" in element.attrib
    }
    source_references = tree_fragment_references(root)
    source_used_gradients = source_references & gradient_ids
    painted_elements = []
    for element in root.iter():
        if local_name(element.tag) not in PAINT_ELEMENTS:
            continue
        references = set()
        for attribute, value in element.attrib.items():
            if local_name(attribute) in {"fill", "stroke", "style"}:
                references.update(fragment_references(value))
        if references & gradient_ids:
            painted_elements.append((element, references & gradient_ids))

    def clean(parent: ET.Element) -> None:
        for child in list(parent):
            if local_name(child.tag) in UNSAFE_TAGS:
                parent.remove(child)
                continue
            clean(child)
        for attribute, value in list(parent.attrib.items()):
            name = local_name(attribute)
            if name.startswith("on") or (name in {"href", "src"} and not SAFE_FRAGMENT.fullmatch(value.strip())):
                del parent.attrib[attribute]
            elif name == "style":
                sanitized = sanitize_inline_style(value)
                if sanitized:
                    parent.attrib[attribute] = sanitized
                else:
                    del parent.attrib[attribute]
            elif URL_START.search(value) and (UNSAFE_STYLE.search(value) or has_unsafe_url(value)):
                del parent.attrib[attribute]
        if local_name(parent.tag) == "style" and parent.text:
            if UNSAFE_STYLE.search(parent.text) or has_unsafe_url(parent.text):
                parent.text = ""

    clean(root)
    ids = {element.attrib["id"] for element in root.iter() if "id" in element.attrib}
    sanitized_references = tree_fragment_references(root)
    unresolved = sanitized_references - ids
    if unresolved:
        raise ValueError(f"sanitized SVG has unresolved same-document references in {source}: {sorted(unresolved)}")
    orphaned_gradients = source_used_gradients - sanitized_references
    if orphaned_gradients:
        raise ValueError(f"sanitization orphaned referenced gradients in {source}: {sorted(orphaned_gradients)}")
    for element, expected_references in painted_elements:
        actual_references = set()
        for attribute, value in element.attrib.items():
            if local_name(attribute) in {"fill", "stroke", "style"}:
                actual_references.update(fragment_references(value))
        missing_paint = expected_references - actual_references
        if missing_paint:
            raise ValueError(f"sanitization removed gradient paint from {local_name(element.tag)} in {source}: {sorted(missing_paint)}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)


def copy_notices(repo: Path, output: Path, emojibase_license: bytes) -> None:
    notices = {
        "svg/LICENSE": "LICENSES/upstream-svg-LICENSE",
        "third_party/color_emoji/LICENSE": "LICENSES/third_party-color_emoji-LICENSE",
        "third_party/region-flags/LICENSE": "LICENSES/third_party-region-flags-LICENSE",
        "AUTHORS": "LICENSES/AUTHORS",
        "CONTRIBUTORS": "LICENSES/CONTRIBUTORS",
    }
    for source, target in notices.items():
        path = repo / source
        if not path.is_file():
            raise FileNotFoundError(f"required upstream notice missing: {source}")
        destination = output / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    (output / "LICENSES" / "emojibase-LICENSE").write_bytes(emojibase_license)


def write_compact_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("dist/blob-emoji"))
    parser.add_argument("--emoji-test", type=Path, help="Use a checked-in/downloaded emoji-test.txt")
    parser.add_argument("--emojibase-data", type=Path, help="Use a downloaded Emojibase en/data.json")
    parser.add_argument("--emojibase-messages", type=Path, help="Use a downloaded Emojibase en/messages.json")
    parser.add_argument("--emojibase-shortcodes", type=Path, help="Use downloaded Emojibase shortcode preset")
    parser.add_argument("--emojibase-joypixels-shortcodes", type=Path, help="Use downloaded Emojibase JoyPixels shortcode preset")
    parser.add_argument("--emojibase-license", type=Path, help="Use a downloaded Emojibase LICENSE")
    parser.add_argument("--fail-on-missing", action="store_true", help="Return nonzero if any Emoji 17 sequence lacks an SVG")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    try:
        output.relative_to(repo)
    except ValueError:
        parser.error("--output must be inside this repository")
    if output == repo or output == repo / ".git":
        parser.error("--output must name a bundle directory, not the repository")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    emoji_test = args.emoji_test.read_bytes() if args.emoji_test else fetch(EMOJI_TEST_URL)
    emojibase_payload = args.emojibase_data.read_bytes() if args.emojibase_data else fetch(EMOJIBASE_URLS["data"])
    messages_payload = args.emojibase_messages.read_bytes() if args.emojibase_messages else fetch(EMOJIBASE_URLS["messages"])
    emojibase_shortcodes_payload = args.emojibase_shortcodes.read_bytes() if args.emojibase_shortcodes else fetch(EMOJIBASE_URLS["emojibase_shortcodes"])
    joypixels_shortcodes_payload = args.emojibase_joypixels_shortcodes.read_bytes() if args.emojibase_joypixels_shortcodes else fetch(EMOJIBASE_URLS["joypixels_shortcodes"])
    emojibase_license = args.emojibase_license.read_bytes() if args.emojibase_license else fetch(EMOJIBASE_URLS["license"])
    expected = parse_emoji_test(emoji_test)
    normalized_expected = normalized_expected_keys(expected)
    sequences = list(expected)
    sequence_indexes = {sequence: index for index, sequence in enumerate(sequences)}
    emojibase_data = json.loads(emojibase_payload)
    messages = json.loads(messages_payload)
    emojibase_shortcodes = parse_shortcode_dataset(emojibase_shortcodes_payload)
    joypixels_shortcodes = parse_shortcode_dataset(joypixels_shortcodes_payload)
    if not isinstance(emojibase_data, list) or not isinstance(messages, dict):
        raise ValueError("Invalid Emojibase data or messages dataset")

    assets: dict[str, str] = {}
    asset_directories = (
        repo / "svg",
        repo / "third_party" / "region-flags" / "waved-svg",
    )
    for asset_directory in asset_directories:
        for source in sorted(asset_directory.glob("*.svg")):
            key = asset_key(source)
            if not key:
                continue
            filename = f"{key.lower()}.svg"
            if key in assets:
                raise ValueError(f"duplicate SVG sequence {key}: {assets[key]} and {source}")
            sanitize_svg(resolve_svg_alias(source, asset_directory), output / "svg" / filename)
            assets[key] = str(source.relative_to(repo))
    copy_notices(repo, output, emojibase_license)

    groups = messages.get("groups")
    subgroups = messages.get("subgroups")
    skin_tones = messages.get("skinTones")
    if not isinstance(groups, list) or not isinstance(subgroups, list) or not isinstance(skin_tones, list):
        raise ValueError("Emojibase messages are missing groups, subgroups, or skin tones")
    picker_records: list[list[object]] = []
    shortcodes: dict[str, int] = {}
    missing: list[str] = []
    picker_coverage: set[str] = set()
    text_labels: dict[str, str] = {}

    strings: list[str] = []
    string_indexes: dict[str, int] = {}

    def string_index(value: str) -> int:
        if value not in string_indexes:
            string_indexes[value] = len(strings)
            strings.append(value)
        return string_indexes[value]

    ordered_records = sorted(emojibase_data, key=lambda record: record.get("order", 1_000_000))
    for record in ordered_records:
        key = resolve_expected_key(str(record.get("hexcode", "")), normalized_expected)
        if not key:
            continue
        group = record.get("group")
        subgroup = record.get("subgroup")
        label = record.get("label")
        tags = record.get("tags", [])
        if not isinstance(group, int) or not isinstance(subgroup, int) or not isinstance(label, str) or not isinstance(tags, list):
            raise ValueError(f"Incomplete Emojibase picker metadata for {key}")
        if key in picker_coverage:
            raise ValueError(f"Duplicate Emojibase picker sequence: {key}")
        picker_coverage.add(key)
        text_labels[key] = label
        variant_records: list[list[object]] = []
        for variant in record.get("skins", []):
            variant_key = resolve_expected_key(str(variant.get("hexcode", "")), normalized_expected)
            if not variant_key:
                continue
            if variant_key in picker_coverage:
                raise ValueError(f"Duplicate Emojibase skin sequence: {variant_key}")
            picker_coverage.add(variant_key)
            text_labels[variant_key] = label
            variant_records.append([sequence_indexes[variant_key], variant.get("tone")])
        picker_records.append([
            sequence_indexes[key],
            string_index(label),
            group,
            subgroup,
            [string_index(tag) for tag in tags if isinstance(tag, str)],
            variant_records,
        ])

    uncovered_picker = set(expected) - picker_coverage
    if uncovered_picker:
        sample = ", ".join(sorted(uncovered_picker)[:10])
        raise ValueError(f"Emojibase {EMOJIBASE_VERSION} does not cover {len(uncovered_picker)} fully-qualified Emoji {EMOJI_VERSION} sequences: {sample}")

    # Emojibase aliases are canonical. JoyPixels aliases fill compatibility gaps without
    # changing the meaning of a canonical shortcode if a preset conflicts.
    for dataset in (emojibase_shortcodes, joypixels_shortcodes):
        for hexcode, aliases in dataset.items():
            key = resolve_expected_key(hexcode, normalized_expected)
            if not key:
                continue
            for alias in aliases:
                shortcodes.setdefault(alias, sequence_indexes[key])

    for key in expected:
        if key not in assets and without_emoji_vs(key) not in assets:
            missing.append(key)

    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    picker = {
        "schema_version": 1,
        "unicode_emoji_version": EMOJI_VERSION,
        "emojibase_version": EMOJIBASE_VERSION,
        "shortcode_presets": ["emojibase", "joypixels"],
        "record_fields": ["sequence_index", "name_string_index", "group_index", "subgroup_index", "keyword_string_indexes", "skin_variants"],
        "skin_variant_fields": ["sequence_index", "tone"],
        "groups": groups,
        "subgroups": subgroups,
        "skin_tones": skin_tones,
        "sequences": sequences,
        "strings": strings,
        "emoji": picker_records,
        "shortcodes": dict(sorted(shortcodes.items())),
    }
    assets_manifest = {
        "schema_version": 1,
        "unicode_emoji_version": EMOJI_VERSION,
        "source": {
            "repository": "https://github.com/0230am/blob-emoji",
            "upstream_repository": "https://github.com/tpnonthealps/blob-emoji",
            "commit": commit,
        },
        "path_template": "svg/{asset_key}.svg",
        "asset_key": "lowercase hexadecimal code points joined by '-', with FE0F removed",
        "missing": missing,
    }
    shortcodes_by_sequence: dict[int, list[str]] = {}
    for alias, sequence_index in shortcodes.items():
        shortcodes_by_sequence.setdefault(sequence_index, []).append(alias)
    clover_rows = [
        [
            sequences[record[0]],
            record[1],
            record[2],
            record[4],
            sorted(shortcodes_by_sequence.get(record[0], [])),
        ]
        for record in picker_records
    ]
    clover_picker = {
        "schema_version": 1,
        "unicode_emoji_version": EMOJI_VERSION,
        "emojibase_version": EMOJIBASE_VERSION,
        "record_fields": ["sequence", "label_string_index", "group_index", "keyword_string_indexes", "shortcodes"],
        "group_fields": ["key", "label", "order"],
        "groups": [[group["key"], group["message"], group["order"]] for group in groups],
        "strings": strings,
        "emoji": clover_rows,
    }
    text_manifest = {
        "schema_version": 1,
        "unicode_emoji_version": EMOJI_VERSION,
        "missing": missing,
        "sequences": {sequence: text_labels[sequence] for sequence in sequences},
    }
    write_compact_json(output / "picker.json", picker)
    write_compact_json(output / "assets.json", assets_manifest)
    write_compact_json(output / "clover-picker.json", clover_picker)
    write_compact_json(output / "text-manifest.json", text_manifest)
    atlas_files = build_atlases(output, [row[0] for row in clover_rows], set(missing))
    manifest = {
        "schema_version": 1,
        "bundle_id": f"bundle-{commit}",
        "unicode_emoji_version": EMOJI_VERSION,
        "emojibase_version": EMOJIBASE_VERSION,
        "source": {
            "repository": "https://github.com/0230am/blob-emoji",
            "upstream_repository": "https://github.com/tpnonthealps/blob-emoji",
            "commit": commit,
        },
        "picker": "clover-picker.json",
        "source_picker": "picker.json",
        "text_manifest": "text-manifest.json",
        "atlas": {
            "size": ATLAS_SIZE,
            "cell_size": CELL_SIZE,
            "files": atlas_files,
            "rasterizer": {"name": "resvg_py", "version": "0.3.4", "fit": "contain"},
            "encoder": {
                "name": "Pillow WebP",
                "version": "11.3.0",
                "libwebp_version": WEBP_VERSION,
                "quality": WEBP_QUALITY,
                "alpha_quality": WEBP_ALPHA_QUALITY,
                "method": WEBP_METHOD,
                "exact": True,
            },
        },
        "svg": {"path_template": "svg/{asset_key}.svg", "assets_manifest": "assets.json"},
        "checksums": "checksums.sha256",
    }
    write_compact_json(output / "manifest.json", manifest)
    (output / "README.md").write_text(
        "# Blob Emoji static bundle\n\n"
        f"This self-contained bundle is identified by `bundle-{commit}` and is derived from Unicode Emoji {EMOJI_VERSION} and pinned Emojibase {EMOJIBASE_VERSION}. "
        "`manifest.json` is the provider-neutral entry point and declares the runtime picker, source picker, text-recognition manifest, atlases, SVG lookup, and checksums. "
        "`clover-picker.json` schema 1 contains compact groups and rows whose fields are declared by `group_fields` and `record_fields`; each row ordinal is its 64-pixel atlas slot. "
        "`picker.json` schema 1 remains the source contract and contains ordered Unicode categories, pooled CLDR names/keywords, skin-tone variants, and Emojibase/JoyPixels shortcode lookup. "
        "Each compact base-emoji row follows `record_fields`; sequence integers index `sequences`, while name and keyword integers index `strings`. Shortcode values also index `sequences`. "
        "`text-manifest.json` maps every fully-qualified sequence to its accessible label and repeats the explicit missing-artwork list. `assets.json` documents deterministic image lookup. "
        "Convert a sequence to lowercase hexadecimal code points joined by `-`, remove `fe0f`, then use `svg/<asset-key>.svg`. "
        "Country flags use the preserved upstream waved region-flag SVGs; render those assets instead of the regional-indicator text. "
        "SVGs were XML-sanitized during this build. `checksums.sha256` lists every distributed file except itself. See `LICENSES/` for preserved upstream and Emojibase notices.\n",
        encoding="utf-8",
    )
    write_checksums(output)
    print(f"Validated {len(expected)} fully-qualified Emoji {EMOJI_VERSION} sequences: {len(expected) - len(missing)} available, {len(missing)} missing.")
    if missing:
        print(f"Missing-sequence report: {output / 'assets.json'}", file=sys.stderr)
    return 1 if args.fail_on_missing and missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
