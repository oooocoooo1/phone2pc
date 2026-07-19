from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "pc_server" / "icon.ico"
RES = ROOT / "android_app" / "android" / "app" / "src" / "main" / "res"

LEGACY_SIZES = {
    "mdpi": 48,
    "hdpi": 72,
    "xhdpi": 96,
    "xxhdpi": 144,
    "xxxhdpi": 192,
}


def main():
    source = Image.open(SOURCE).convert("RGBA")
    for density, legacy_size in LEGACY_SIZES.items():
        directory = RES / f"mipmap-{density}"
        directory.mkdir(parents=True, exist_ok=True)

        legacy = source.resize((legacy_size, legacy_size), Image.Resampling.LANCZOS)
        legacy.save(directory / "ic_launcher.png", optimize=True)

        # Adaptive icon foreground canvases are 108 dp. Keep the complete PC
        # icon inside the 66 dp safe zone so circular launcher masks don't crop
        # the phone/monitor glyph.
        canvas_size = round(legacy_size * 2.25)
        visible_size = round(canvas_size * 0.76)
        foreground = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        inset = (canvas_size - visible_size) // 2
        foreground.alpha_composite(
            source.resize((visible_size, visible_size), Image.Resampling.LANCZOS),
            (inset, inset),
        )
        foreground.save(directory / "ic_launcher_foreground.png", optimize=True)


if __name__ == "__main__":
    main()
