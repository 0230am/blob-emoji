# Static release bundles

This repository tracks [tpnonthealps/blob-emoji](https://github.com/tpnonthealps/blob-emoji). The upstream SVG licence and third-party notices are preserved in the repository and copied unchanged into each release bundle.

Consumers should download and pin a `bundle-<commit>` GitHub Release archive and unpack it into their own static-assets directory. They must not use this repository as a Git submodule or fetch a mutable branch at runtime.

`picker.json` is the compact text-input and picker index. It is derived at build time from pinned `emojibase-data` 17.0.0, which supplies Emoji 17 / Unicode 17 / CLDR 48 labels, search tags, picker order, groups, subgroups, and nested skin-tone variants. Repeated strings and Unicode sequences are pooled and referenced by integer indexes. The maintained Emojibase shortcode preset is canonical, with the JoyPixels preset added as a fallback; shortcode values index the shared `sequences` array.

SVG filenames are deterministic Unicode asset keys, such as `svg/1f44d.svg`: lowercase hexadecimal code points joined by `-`, with `FE0F` removed. Country-flag assets come from the preserved upstream `third_party/region-flags/waved-svg` set, so consumers should render the resolved SVG for a flag sequence instead of displaying its regional-indicator characters as native text. `assets.json` documents the lookup rule and explicitly lists any fully-qualified Unicode Emoji 17 sequence without artwork. No sequence is silently discarded.

The build independently validates Emojibase and SVG coverage against Unicode's fixed Emoji 17 test data, sanitizes every output SVG, and copies upstream licence/credit files and the Emojibase MIT notice to `LICENSES/`. Safe same-document paint references such as `url(#gradient)` are retained; external URLs are rejected, and the build fails if sanitization orphans a used gradient or removes its paint from a drawable element.

## Bundle contracts

`manifest.json` schema 1 is the provider-neutral bundle entry point. Its `bundle_id` is `bundle-<full-git-commit>` and its paths are relative to the extraction root. It identifies the runtime and source picker contracts, text-recognition manifest, atlas geometry and encoder settings, SVG lookup, and checksum list.

`picker.json` remains source picker schema 1. Its pooled arrays and row fields are declared in the file. `clover-picker.json` schema 1 is a smaller browser projection with these declared row fields:

```text
[sequence, label_string_index, group_index, keyword_string_indexes, shortcodes]
```

Its compact group rows are `[key, label, order]`. `sequence` is the uppercase, hyphen-delimited fully-qualified Unicode sequence. Labels and keywords index the shared `strings` pool; shortcodes are direct strings. The row ordinal is the atlas slot, with no coordinate map shipped.

`text-manifest.json` schema 1 maps every fully-qualified Emoji 17 sequence to an accessible CLDR label and carries the same explicit `missing` artwork report as `assets.json`.

Atlases use 2048 × 2048 WebP images with 64 × 64 cells, 32 columns, and 1,024 slots per file. Slots are row-major. Missing artwork leaves a transparent slot, so later ordinals never move. Releases rasterize with the self-contained resvg_py 0.3.4 native wheel using contain behavior and encode through Pillow 11.3.0 with its bundled libwebp 1.5.0, quality 90, alpha quality 100, method 6, and exact transparent RGB preservation. The builder rejects an encoder-version mismatch. `requirements-bundle.txt` pins the Python/native distribution dependencies; GitHub builds use Python 3.12 on Ubuntu 24.04.

`checksums.sha256` contains strictly sorted SHA-256 entries for every file except itself. Verification rejects missing, extra, unsafe, or mismatched paths and validates picker indexes, projection order, text labels, atlas geometry/alpha/slot occupancy, and required notices.

## Build, verify, and package

```text
python -m pip install -r requirements-bundle.txt
python scripts/build_bundle.py --output dist/blob-emoji
python scripts/verify_bundle.py dist/blob-emoji
python scripts/package_bundle.py dist/blob-emoji blob-emoji-<commit>.tar.gz
python scripts/verify_bundle.py dist/blob-emoji --archive blob-emoji-<commit>.tar.gz
```

The equivalent convenience targets are `make bundle` and `make verify-bundle`. Release archives contain one top-level `blob-emoji/` directory, use sorted entries and normalized Git-commit timestamps, ownership, and permissions, and are published as `blob-emoji-<full-commit>.tar.gz` under `bundle-<full-commit>` with a sibling `.sha256` file.
