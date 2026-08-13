"""Generate the synthetic H&E demo slide at assets/demo_he.png.

Thin wrapper kept for backwards compatibility; the generation logic lives
in ``hescope.wsi.demo`` (shipped with the wheel) so non-editable installs can
also generate the demo slide.

Usage: python tools/make_demo_slide.py [output_path]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo layout

from hescope.wsi.demo import W, H, generate_demo_slide

OUT = Path(__file__).resolve().parent.parent / "assets" / "demo_he.png"


def main() -> Path:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    generate_demo_slide(out)
    print(f"wrote {out} ({W}x{H})")
    return out


if __name__ == "__main__":
    main()
