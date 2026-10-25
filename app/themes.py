"""Foreground-only themes. Background and opacity belong to the terminal."""

NAMES = ("minimal", "midnight", "ember")
PALETTES = {
    "minimal": {
        "cyan": "38;5;110", "muted": "2", "white": "",
        "live": "38;5;174", "dev": "38;5;109", "sbx": "38;5;180", "other": "38;5;146",
    },
    "midnight": {
        "cyan": "38;2;103;232;249", "muted": "38;2;120;145;170",
        "live": "38;2;251;113;133", "dev": "38;2;110;231;183",
        "sbx": "38;2;253;211;118", "other": "38;2;167;139;250", "white": "1",
    },
    "ember": {
        # Historical "cyan" means the UI accent, not a literal colour.
        "cyan": "38;2;255;82;104", "muted": "38;2;186;154;164",
        "live": "38;2;255;115;130", "dev": "38;2;155;195;175",
        "sbx": "38;2;232;190;131", "other": "38;2;206;174;195",
        "white": "1;38;2;255;240;220",
    },
}


def compact(theme: str) -> bool:
    return theme != "midnight"


def pointer(colored: bool) -> str:
    # A solid rail + arrow is much easier to locate than a lone small triangle.
    return "▌▸" if colored else ">>"


def fzf_colors(theme: str, colored: bool) -> str:
    # -1 emits the terminal's default background, including for the selected row.
    # Gutter and preview must be explicit; inheriting fzf's scheme can paint them.
    base = "bg:-1,bg+:-1,preview-bg:-1,preview-fg:-1,gutter:-1,fg:-1:regular,fg+:-1:regular:bold:underline"
    if not colored:
        return "bw," + base + ",pointer:-1:regular:bold,hl+:-1:regular:bold:underline"
    accent, border, focus = {
        "minimal": ("#89b4c4", "#6b7b87", "#ffffff"),
        "midnight": ("#67e8f9", "#536b86", "#f5fbff"),
        "ember": ("#ff5268", "#a45c6c", "#fff0dc"),
    }[theme]
    foreground = ",fg:#cdbfc2:regular" if theme == "ember" else ""
    return (base + foreground + f",fg+:{focus}:regular:bold:underline,hl:{accent},hl+:{focus}:regular:bold:underline,pointer:{accent}:bold,marker:{accent},prompt:{accent}"
            + f",header:{border},info:{border},border:{border},label:{accent},spinner:{accent}")