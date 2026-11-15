"""Copy shipped assets to Vercel's CDN without copying user uploads."""
from pathlib import Path
from shutil import copy2

ROOT = Path(__file__).resolve().parents[1]


def build():
    source = ROOT / "src" / "static"
    target = ROOT / "public" / "static"
    for asset in source.rglob("*"):
        relative = asset.relative_to(source)
        if "uploads" in relative.parts or asset.is_symlink() or not asset.is_file():
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy2(asset, destination)


if __name__ == "__main__":
    build()
