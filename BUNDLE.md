# Static release bundles

This repository tracks [tpnonthealps/blob-emoji](https://github.com/tpnonthealps/blob-emoji). The upstream SVG licence and third-party notices are preserved in the repository and copied unchanged into each release bundle.

Consumers should download and pin a `bundle-<commit>` GitHub Release archive and unpack it into their own static-assets directory. They must not use this repository as a Git submodule or fetch a mutable branch at runtime.

`picker.json` is the compact text-input and picker index. It is derived at build time from pinned `emojibase-data` 17.0.0, which supplies Emoji 17 / Unicode 17 / CLDR 48 labels, search tags, picker order, groups, subgroups, and nested skin-tone variants. Repeated strings and Unicode sequences are pooled and referenced by integer indexes. The maintained Emojibase shortcode preset is canonical, with the JoyPixels preset added as a fallback; shortcode values index the shared `sequences` array.

SVG filenames are deterministic Unicode asset keys, such as `svg/1f44d.svg`: lowercase hexadecimal code points joined by `-`, with `FE0F` removed. `assets.json` documents that rule and lists every fully-qualified Unicode Emoji 17 sequence for which the upstream has no artwork. Consumers should fall back to native rendering for those entries. No sequence is silently discarded.

The build independently validates Emojibase and SVG coverage against Unicode's fixed Emoji 17 test data, sanitizes every output SVG, and copies upstream licence/credit files and the Emojibase MIT notice to `LICENSES/`.
