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
UNSAFE_STYLE = re.compile(r"(?:@import|url\s*\(|expression\s*\()", re.IGNORECASE)


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


def sanitize_svg(source: Path, destination: Path) -> None:
    raw = source.read_bytes()
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError(f"unsafe XML declaration in {source}")
    root = ET.fromstring(raw)
    if local_name(root.tag) != "svg":
        raise ValueError(f"not an SVG root: {source}")

    def clean(parent: ET.Element) -> None:
        for child in list(parent):
            if local_name(child.tag) in UNSAFE_TAGS:
                parent.remove(child)
                continue
            clean(child)
        for attribute, value in list(parent.attrib.items()):
            name = local_name(attribute)
            if name.startswith("on") or (name in {"href", "src"} and not value.strip().startswith("#")):
                del parent.attrib[attribute]
            elif name == "style" and UNSAFE_STYLE.search(value):
                del parent.attrib[attribute]
        if local_name(parent.tag) == "style" and parent.text and UNSAFE_STYLE.search(parent.text):
            parent.text = ""

    clean(root)
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
    for source in sorted((repo / "svg").glob("*.svg")):
        key = asset_key(source)
        if key:
            filename = f"{key.lower()}.svg"
            if filename in assets.values():
                raise ValueError(f"duplicate normalized SVG filename: {filename}")
            sanitize_svg(source, output / "svg" / filename)
            assets[key] = filename
    copy_notices(repo, output, emojibase_license)

    groups = messages.get("groups")
    subgroups = messages.get("subgroups")
    skin_tones = messages.get("skinTones")
    if not isinstance(groups, list) or not isinstance(subgroups, list) or not isinstance(skin_tones, list):
        raise ValueError("Emojibase messages are missing groups, subgroups, or skin tones")
    picker_records: list[list[object]] = []
    shortcodes: dict[str, str] = {}
    missing: list[str] = []
    picker_coverage: set[str] = set()

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
        variant_records: list[list[object]] = []
        for variant in record.get("skins", []):
            variant_key = resolve_expected_key(str(variant.get("hexcode", "")), normalized_expected)
            if not variant_key:
                continue
            if variant_key in picker_coverage:
                raise ValueError(f"Duplicate Emojibase skin sequence: {variant_key}")
            picker_coverage.add(variant_key)
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
            "repository": "https://github.com/tpnonthealps/blob-emoji",
            "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        },
        "path_template": "svg/{asset_key}.svg",
        "asset_key": "lowercase hexadecimal code points joined by '-', with FE0F removed",
        "missing": missing,
    }
    write_compact_json(output / "picker.json", picker)
    write_compact_json(output / "assets.json", assets_manifest)
    (output / "README.md").write_text(
        "# Blob Emoji static bundle\n\n"
        f"`picker.json` is derived from pinned Emojibase {EMOJIBASE_VERSION} and contains ordered Unicode categories, pooled CLDR names/keywords, skin-tone variants, and Emojibase/JoyPixels shortcode lookup. "
        "Each compact base-emoji row follows `record_fields`; sequence integers index `sequences`, while name and keyword integers index `strings`. Shortcode values also index `sequences`. "
        "`assets.json` documents deterministic image lookup and explicitly lists missing Emoji 17 sequences. "
        "Convert a sequence to lowercase hexadecimal code points joined by `-`, remove `fe0f`, then use `svg/<asset-key>.svg`. "
        "SVGs were XML-sanitized during this build. See `LICENSES/` for preserved upstream and Emojibase notices.\n",
        encoding="utf-8",
    )
    print(f"Validated {len(expected)} fully-qualified Emoji {EMOJI_VERSION} sequences: {len(expected) - len(missing)} available, {len(missing)} missing.")
    if missing:
        print(f"Missing-sequence report: {output / 'assets.json'}", file=sys.stderr)
    return 1 if args.fail_on_missing and missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
