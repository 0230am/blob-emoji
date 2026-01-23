# Build Noto Blob Emoji

## 1. Dependencies

### Linux (Debian/Ubuntu/WSL)
```bash
sudo apt update
sudo apt install python3 pkg-config pngquant zopfli libcairo2-dev imagemagick python3-venv gcc make
```

### macOS (Homebrew)
```bash
brew install python3 pkg-config pngquant zopfli cairo imagemagick gcc
```

### Setup Python Environment
Run inside the repo folder:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 2. Customization
*   **Add PNGs:** Add new emojis to the `png/128` folder.
    *   *Note: Modifying other resolution folders does not do anything at all.*
*   **Modify SVGs:** You can only replace existing files in the `svg` folder.

## 3. Build Process

### Build CBDT/CBLC Font (Standard / PNG)
*Takes 5m - 2h depending on CPU.*
```bash
time make -j BYPASS_SEQUENCE_CHECK='True'

# Move output
mv *.ttf fonts/
```

### Build COLRv1 Font (Android 13+ / SVG)
```bash
(cd colrv1 && rm -rf build/ && time nanoemoji *.toml)

# Copy output
cp colrv1/build/NotoColorEmoji.ttf fonts/Noto-COLRv1.ttf
cp colrv1/build/NotoColorEmoji-noflags.ttf fonts/Noto-COLRv1-noflags.ttf
```

### Fix ZWJ Sequences
```bash
python colrv1_postproc.py
```

## 4. Done
Find your fonts in the `fonts/` folder.
*   **Troubleshooting:** If build fails, check logs for corrupt emojis in `png/` or `build/compressed_pngs` and remove them.