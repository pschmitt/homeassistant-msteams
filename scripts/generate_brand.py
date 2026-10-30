"""Generate brand assets (icon + logo) for Microsoft Teams integration."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def draw_teams_icon(size: int = 512) -> Image.Image:
    """Draw Microsoft Teams icon with rounded shapes and the 'T' badge."""
    scale = size / 512.0
    # Create image with transparent background (RGBA)
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)

    # Teams Colors
    bg_purple_dark = (70, 78, 184, 255)  # #464EB8
    bg_purple_mid = (91, 95, 199, 255)  # #5B5FC7
    bg_purple_light = (123, 131, 235, 255)  # #7B83EB
    white = (255, 255, 255, 255)

    # 1. Back person 1 (right)
    # Head circle
    draw.ellipse(
        [320 * scale, 100 * scale, 420 * scale, 200 * scale],
        fill=bg_purple_light,
    )
    # Shoulder rounded rectangle
    draw.rounded_rectangle(
        [280 * scale, 210 * scale, 460 * scale, 390 * scale],
        radius=40 * scale,
        fill=bg_purple_light,
    )

    # 2. Back person 2 (mid-back)
    draw.ellipse(
        [200 * scale, 70 * scale, 310 * scale, 180 * scale],
        fill=bg_purple_mid,
    )
    draw.rounded_rectangle(
        [150 * scale, 190 * scale, 360 * scale, 410 * scale],
        radius=45 * scale,
        fill=bg_purple_mid,
    )

    # 3. Front Badge with "T"
    badge_box = [60 * scale, 150 * scale, 290 * scale, 380 * scale]
    draw.rounded_rectangle(
        badge_box,
        radius=35 * scale,
        fill=bg_purple_dark,
    )

    # White letter "T" drawn with geometric rectangles
    # Horizontal bar
    t_top = 210 * scale
    t_bar_h = 28 * scale
    t_left = 110 * scale
    t_right = 240 * scale
    draw.rounded_rectangle(
        [t_left, t_top, t_right, t_top + t_bar_h],
        radius=6 * scale,
        fill=white,
    )
    # Vertical bar
    v_width = 32 * scale
    v_left = (t_left + t_right) / 2 - v_width / 2
    draw.rounded_rectangle(
        [v_left, t_top, v_left + v_width, 325 * scale],
        radius=6 * scale,
        fill=white,
    )

    return im


def draw_teams_logo(width: int = 512, height: int = 256) -> Image.Image:
    """Draw Microsoft Teams logo (icon on left, text on right)."""
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)

    # Draw icon at 180x180
    icon_size = 180
    icon = draw_teams_icon(icon_size)
    im.paste(icon, (30, (height - icon_size) // 2), icon)

    # Text: Microsoft Teams
    # Draw simple text using default or basic font
    text_color = (32, 33, 36, 255)
    purple_color = (70, 78, 184, 255)

    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
    except Exception:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    draw.text((230, 80), "Microsoft", fill=text_color, font=font_small)
    draw.text((230, 115), "Teams", fill=purple_color, font=font_large)

    return im


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    cc_brand = root / "custom_components" / "msteams" / "brand"
    cc_dir = root / "custom_components" / "msteams"

    cc_brand.mkdir(parents=True, exist_ok=True)

    # Icon 512x512
    icon512 = draw_teams_icon(512)
    icon512.save(root / "icon.png")
    icon512.save(cc_brand / "icon@2x.png")
    icon512.save(cc_dir / "icon.png")

    # Icon 256x256
    icon256 = icon512.resize((256, 256), Image.Resampling.LANCZOS)
    icon256.save(cc_brand / "icon.png")

    # Logo 512x256
    logo512 = draw_teams_logo(512, 256)
    logo512.save(root / "logo.png")
    logo512.save(cc_brand / "logo.png")
    logo512.save(cc_dir / "logo.png")

    # Logo 1024x512 (@2x)
    logo1024 = draw_teams_logo(1024, 512)
    logo1024.save(cc_brand / "logo@2x.png")

    print("Successfully generated Microsoft Teams branding assets.")


if __name__ == "__main__":
    main()
