# Static release bundles

This repository tracks [tpnonthealps/blob-emoji](https://github.com/tpnonthealps/blob-emoji). The upstream SVG licence and third-party notices are preserved in the repository and copied unchanged into each release bundle.

Consumers should download and pin a `bundle-<commit>` GitHub Release archive and unpack it into their own static-assets directory. They must not use this repository as a Git submodule or fetch a mutable branch at runtime.

`picker.json` is the compact text-input and picker index. It is derived at build time from pinned `emojibase-data` 17.0.0, which supplies Emoji 17 / Unicode 17 / CLDR 48 labels, search tags, picker order, groups, subgroups, and nested skin-tone variants. Repeated strings and Unicode sequences are pooled and referenced by integer indexes. The maintained Emojibase shortcode preset is canonical, with the JoyPixels preset added as a fallback; shortcode values index the shared `sequences` array.

SVG filenames are deterministic Unicode asset keys, such as `svg/1f44d.svg`: lowercase hexadecimal code points joined by `-`, with `FE0F` removed. Country-flag assets come from the preserved upstream `third_party/region-flags/waved-svg` set, so consumers should render the resolved SVG for a flag sequence instead of displaying its regional-indicator characters as native text. `assets.json` documents the lookup rule and explicitly lists any fully-qualified Unicode Emoji 17 sequence without artwork. No sequence is silently discarded.

The build independently validates Emojibase and SVG coverage against Unicode's fixed Emoji 17 test data, sanitizes every output SVG, and copies upstream licence/credit files and the Emojibase MIT notice to `LICENSES/`. Safe same-document paint references such as `url(#gradient)` are retained; external URLs are rejected, and the build fails if sanitization orphans a used gradient or removes its paint from a drawable element.
