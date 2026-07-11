from PIL import Image


def has_transparency(path: str) -> bool:
    with Image.open(path) as img:
        # Byte transparency
        if img.info.get("transparency", None) is not None:
            return True

        # Indexed
        if img.mode == "P":
            transparent_color = img.info.get("transparency", -1)
            return any(index == transparent_color for _, index in img.getcolors())

        # No alpha
        if not img.mode.endswith("A"):
            return False

        # Alpha
        return img.getextrema()[-1][0] < 255


def transparency_amount(path: str) -> float:
    with Image.open(path).convert("RGBA") as img:
        alphas = [img.getpixel((x, y))[3] / 255.0 for x in range(img.size[0]) for y in range(img.size[1])]
        return sum(alphas) / len(alphas)


def clear_transparent_colors(img: Image.Image):
    for x in range(img.size[0]):
        for y in range(img.size[1]):
            col = img.getpixel((x, y))
            if col[3] == 0:
                img.putpixel((x, y), (0, 0, 0, 0))


def transfer_palette(source_path: str, target_path: str, size: int) -> Image.Image:
    with Image.open(target_path).convert("RGBA") as target, Image.open(source_path).convert("RGBA") as source:
        clear_transparent_colors(target)
        clear_transparent_colors(source)

        col_count = size
        quant_target = target.quantize(colors=col_count, dither=Image.Dither.NONE)
        col_count = min(col_count, len(quant_target.getcolors()))
        quant_source = source.quantize(colors=col_count, dither=Image.Dither.NONE)
        col_count = min(col_count, len(quant_source.getcolors()))

        result = target.quantize(colors=col_count, dither=Image.Dither.NONE)
        result.putpalette(quant_source.palette, rawmode="RGBA")
        return result
