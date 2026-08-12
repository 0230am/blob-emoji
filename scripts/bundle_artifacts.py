"""Derived bundle artifacts shared by the builder and verifier."""
from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path, PurePosixPath

try:
    import resvg_py
    from PIL import Image, features
except ImportError as error:  # pragma: no cover - exercised by command-line use
    raise SystemExit(
        "Bundle image dependencies are missing; install requirements-bundle.txt"
    ) from error


ATLAS_SIZE = 2048
CELL_SIZE = 64
ATLAS_COLUMNS = ATLAS_SIZE // CELL_SIZE
CELLS_PER_ATLAS = ATLAS_COLUMNS * ATLAS_COLUMNS
WEBP_QUALITY = 90
WEBP_ALPHA_QUALITY = 100
WEBP_METHOD = 6
WEBP_VERSION = "1.5.0"

if features.version("webp") != WEBP_VERSION:  # pragma: no cover - environment guard
    raise SystemExit(
        f"Pillow WebP encoder mismatch: expected {WEBP_VERSION}, found {features.version('webp')}"
    )


def atlas_file_count(slot_count: int) -> int:
    return math.ceil(slot_count / CELLS_PER_ATLAS)


def build_atlases(output: Path, sequences: list[str], missing: set[str]) -> list[str]:
    """Rasterize SVGs into fixed-slot deterministic picker atlases."""
    atlas_directory = output / "atlas"
    atlas_directory.mkdir(parents=True, exist_ok=True)
    images = [
        Image.new("RGBA", (ATLAS_SIZE, ATLAS_SIZE), (0, 0, 0, 0))
        for _ in range(atlas_file_count(len(sequences)))
    ]
    for slot, sequence in enumerate(sequences):
        if sequence in missing:
            continue
        key = "-".join(point for point in sequence.split("-") if point != "FE0F").lower()
        svg_path = output / "svg" / f"{key}.svg"
        if not svg_path.is_file():
            raise FileNotFoundError(f"picker artwork is not declared missing: {svg_path}")
        png = bytes(resvg_py.svg_to_bytes(svg_string=svg_path.read_text(encoding="utf-8")))
        with Image.open(io.BytesIO(png)) as rendered:
            rendered_rgba = rendered.convert("RGBA")
            rendered_rgba.thumbnail((CELL_SIZE, CELL_SIZE), Image.Resampling.LANCZOS)
            cell = Image.new("RGBA", (CELL_SIZE, CELL_SIZE), (0, 0, 0, 0))
            cell.alpha_composite(
                rendered_rgba,
                ((CELL_SIZE - rendered_rgba.width) // 2, (CELL_SIZE - rendered_rgba.height) // 2),
            )
        atlas_index, local_slot = divmod(slot, CELLS_PER_ATLAS)
        row, column = divmod(local_slot, ATLAS_COLUMNS)
        images[atlas_index].alpha_composite(cell, (column * CELL_SIZE, row * CELL_SIZE))

    files: list[str] = []
    for index, image in enumerate(images):
        relative = f"atlas/atlas-{index}.webp"
        image.save(
            output / relative,
            format="WEBP",
            quality=WEBP_QUALITY,
            alpha_quality=WEBP_ALPHA_QUALITY,
            method=WEBP_METHOD,
            exact=True,
        )
        files.append(relative)
    return files


def distributed_files(output: Path) -> list[Path]:
    return sorted(
        (path for path in output.rglob("*") if path.is_file() and path.name != "checksums.sha256"),
        key=lambda path: path.relative_to(output).as_posix(),
    )


def write_checksums(output: Path) -> None:
    lines = []
    for path in distributed_files(output):
        relative = path.relative_to(output).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {relative}\n")
    (output / "checksums.sha256").write_text("".join(lines), encoding="utf-8", newline="\n")


def safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or "\\" in value
        or any(part in {"", ".", ".."} or ":" in part for part in path.parts)
    ):
        raise ValueError(f"unsafe bundle path: {value!r}")
    return path
