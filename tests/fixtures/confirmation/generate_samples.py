"""Generate a duplicate target from owned geometric replay fixtures."""
from pathlib import Path
import runpy


def main():
    root = Path(__file__).resolve().parent
    helpers = runpy.run_path(str(root.parent / "replay_template" / "generate_samples.py"))
    # Route the existing stdlib PNG writer to this fixture directory.
    helpers["write_png"].__globals__["BASE"] = root
    canvas = bytearray((24, 28, 36)) * (1280 * 720)
    patch = helpers["badge"]()
    helpers["paste"](canvas, patch, 220, 200)
    helpers["paste"](canvas, patch, 380, 300)
    helpers["write_png"]("multiple_targets.png", 1280, 720, canvas)


if __name__ == "__main__":
    main()
