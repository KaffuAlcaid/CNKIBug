from __future__ import annotations

import tkinter as tk
from base64 import b64encode
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tkinter import font, ttk as tkttk

import ttkbootstrap as ttk
from PIL import Image, ImageDraw
from ttkbootstrap.utility import scale_size
from ttkbootstrap.widgets.scrolled import ScrolledFrame


@dataclass(frozen=True)
class Palette:
    light: bool
    canvas: str
    card: str
    card_subtle: str
    border: str
    border_strong: str
    divider: str
    fg: str
    muted: str
    disabled_fg: str
    disabled_bg: str
    primary: str
    primary_hover: str
    primary_fg: str
    primary_disabled_bg: str
    primary_disabled_fg: str
    soft_bg: str
    soft_hover: str
    soft_fg: str
    ghost_hover: str
    danger: str
    danger_soft: str
    danger_hover: str
    success: str
    success_hover: str
    success_soft: str
    warning: str
    warning_soft: str
    info: str
    info_soft: str
    purple: str
    purple_soft: str
    input_bg: str
    input_ro: str
    row_alt: str
    row_sel: str
    row_sel_fg: str
    nav_active: str


_LIGHT_PALETTE = Palette(
    light=True,
    canvas="#F3F5F8",
    card="#FFFFFF",
    card_subtle="#F8FAFC",
    border="#DFE4EC",
    border_strong="#CBD3DF",
    divider="#E7ECF2",
    fg="#1F2937",
    muted="#64748B",
    disabled_fg="#9AA5B4",
    disabled_bg="#EDF1F6",
    primary="#2563EB",
    primary_hover="#1D4ED8",
    primary_fg="#FFFFFF",
    primary_disabled_bg="#B9CDF8",
    primary_disabled_fg="#F8FAFF",
    soft_bg="#EBF0F7",
    soft_hover="#DFE7F2",
    soft_fg="#1E293B",
    ghost_hover="#E9EEF5",
    danger="#DC2626",
    danger_soft="#FEF2F2",
    danger_hover="#FEE2E2",
    success="#15803D",
    success_hover="#166534",
    success_soft="#ECFDF3",
    warning="#B45309",
    warning_soft="#FFFBEB",
    info="#1D4ED8",
    info_soft="#EFF6FF",
    purple="#6D28D9",
    purple_soft="#F5F3FF",
    input_bg="#FFFFFF",
    input_ro="#F8FAFC",
    row_alt="#FAFBFD",
    row_sel="#E8F1FD",
    row_sel_fg="#1E293B",
    nav_active="#E8F1FD",
)

_DARK_PALETTE = Palette(
    light=False,
    canvas="#181B20",
    card="#21252C",
    card_subtle="#262B33",
    border="#333944",
    border_strong="#454E5E",
    divider="#2C313B",
    fg="#E5E9F0",
    muted="#94A0B4",
    disabled_fg="#667082",
    disabled_bg="#2A2F38",
    primary="#3B82F6",
    primary_hover="#2563EB",
    primary_fg="#FFFFFF",
    primary_disabled_bg="#2B4268",
    primary_disabled_fg="#8AA7D6",
    soft_bg="#2C323D",
    soft_hover="#353D4B",
    soft_fg="#E5E9F0",
    ghost_hover="#2C323D",
    danger="#F87171",
    danger_soft="#3A2226",
    danger_hover="#4B282E",
    success="#22C55E",
    success_hover="#16A34A",
    success_soft="#1B3327",
    warning="#FBBF24",
    warning_soft="#362C1B",
    info="#60A5FA",
    info_soft="#1E2F4A",
    purple="#A78BFA",
    purple_soft="#2D2545",
    input_bg="#1B1F25",
    input_ro="#1E2229",
    row_alt="#242931",
    row_sel="#233856",
    row_sel_fg="#F1F5F9",
    nav_active="#233856",
)


def get_palette(style: ttk.Style | None = None) -> Palette:
    active = style or ttk.Style()
    return _LIGHT_PALETTE if active.theme.type == "light" else _DARK_PALETTE


def _photo_from_pil(root: tk.Misc, image: Image.Image) -> tk.PhotoImage:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return tk.PhotoImage(data=b64encode(buffer.getvalue()), master=root)


def load_brand_logo(root: tk.Misc, icon_path: Path | None, size: int = 26) -> tk.PhotoImage | None:
    candidates: list[Path] = []
    if icon_path is not None:
        logo_sibling = icon_path.with_name("logo.png")
        if logo_sibling.is_file():
            candidates.append(logo_sibling)
        if icon_path.is_file():
            candidates.append(icon_path)
    repo_logo = Path(__file__).resolve().parents[2] / "logo.png"
    if repo_logo.is_file() and repo_logo not in candidates:
        candidates.append(repo_logo)
    pixel_size = max(16, scale_size(root, size))
    for path in candidates:
        try:
            with Image.open(path) as source:
                image = source.convert("RGBA")
                image.thumbnail((pixel_size, pixel_size), Image.Resampling.NEAREST)
                return _photo_from_pil(root, image)
        except (OSError, tk.TclError):
            continue
    return None


def _draw_checkbox_image(state: str, pixel_size: int, palette: Palette) -> Image.Image:
    scale = 2
    dim = pixel_size * scale
    image = Image.new("RGBA", (dim, dim), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    pad = max(2, dim // 10)
    radius = max(3, dim // 6)
    box = (pad, pad, dim - pad - 1, dim - pad - 1)
    if state == "checked":
        draw.rounded_rectangle(box, radius=radius, fill=palette.primary)
        stroke = max(2, dim // 10)
        p1 = (int(dim * 0.27), int(dim * 0.52))
        p2 = (int(dim * 0.44), int(dim * 0.69))
        p3 = (int(dim * 0.74), int(dim * 0.34))
        draw.line([p1, p2, p3], fill=palette.primary_fg, width=stroke, joint="curve")
    elif state == "unchecked":
        stroke = max(2, dim // 12)
        outline = "#94A3B8" if palette.light else "#64748B"
        draw.rounded_rectangle(box, radius=radius, fill=palette.card, outline=outline, width=stroke)
    else:
        stroke = max(2, dim // 12)
        y = dim // 2
        draw.line([(int(dim * 0.26), y), (int(dim * 0.74), y)], fill=palette.disabled_fg, width=stroke)
    return image.resize((pixel_size, pixel_size), Image.Resampling.LANCZOS)


def get_checkbox_icons(widget: tk.Misc) -> dict[str, tk.PhotoImage]:
    root = widget.winfo_toplevel()
    palette = get_palette()
    pixel_size = max(14, scale_size(root, 16))
    cache_key = (palette.light, pixel_size)
    cached = getattr(root, "_cnkibug_checkbox_icons", None)
    if cached is not None and cached[0] == cache_key:
        return cached[1]
    icons = {
        name: _photo_from_pil(root, _draw_checkbox_image(name, pixel_size, palette))
        for name in ("checked", "unchecked", "disabled")
    }
    root._cnkibug_checkbox_icons = (cache_key, icons)
    return icons


def place_toplevel(
    window: tk.Toplevel,
    parent: tk.Misc,
    width: int,
    height: int,
    *,
    min_width: int | None = None,
    min_height: int | None = None,
    scaled: bool = True,
) -> None:
    if scaled:
        width = scale_size(parent, width)
        height = scale_size(parent, height)
        if min_width is not None:
            min_width = scale_size(parent, min_width)
        if min_height is not None:
            min_height = scale_size(parent, min_height)
    screen_w = parent.winfo_screenwidth()
    screen_h = parent.winfo_screenheight()
    width = min(width, max(320, screen_w - 72))
    height = min(height, max(240, screen_h - 84))
    parent_w = parent.winfo_width() or screen_w
    parent_h = parent.winfo_height() or screen_h
    parent_x = parent.winfo_rootx() if parent.winfo_ismapped() else 0
    parent_y = parent.winfo_rooty() if parent.winfo_ismapped() else 0
    x = max(0, min(parent_x + (parent_w - width) // 2, screen_w - width))
    y = max(0, min(parent_y + (parent_h - height) // 2, screen_h - height))
    window.geometry(f"{width}x{height}+{x}+{y}")
    if min_width is not None or min_height is not None:
        window.minsize(
            min(min_width or width, width),
            min(min_height or height, height),
        )


def configure_window_surface(window: tk.Misc, surface: str = "canvas") -> None:
    palette = get_palette()
    color = palette.card if surface == "card" else palette.canvas
    try:
        window.configure(background=color)
    except tk.TclError:
        pass


def style_treeview(tree: ttk.Treeview) -> None:
    tree.configure(style="App.Treeview")
    refresh_treeview_tags(tree)
    if not getattr(tree, "_cnkibug_tree_bound", False):
        tree._cnkibug_tree_bound = True
        tree.bind("<<ThemeChanged>>", lambda _event: refresh_treeview_tags(tree), add="+")


def refresh_treeview_tags(tree: ttk.Treeview) -> None:
    palette = get_palette()
    tree.tag_configure("even", background=palette.card)
    tree.tag_configure("odd", background=palette.row_alt)
    tree.tag_configure("success", foreground=palette.success)
    tree.tag_configure("danger", foreground=palette.danger)
    tree.tag_configure("warning", foreground=palette.warning)
    tree.tag_configure("info", foreground=palette.info)
    tree.tag_configure("muted", foreground=palette.muted)


def apply_zebra(tree: ttk.Treeview) -> None:
    for position, item_id in enumerate(tree.get_children()):
        existing = tuple(tag for tag in tree.item(item_id, "tags") if tag not in {"even", "odd"})
        tree.item(item_id, tags=(("odd" if position % 2 else "even"), *existing))


_CUSTOM_STYLES: set[str] = set()


def switch_theme(root: tk.Misc, theme_name: str) -> None:
    style = ttk.Style()
    if style.theme_use() != theme_name:
        registry = getattr(style, "_style_registry", None)
        if isinstance(registry, set):
            registry.difference_update(_CUSTOM_STYLES)
        style.theme_use(theme_name)
    apply_appearance(root)


def apply_appearance(root: tk.Misc) -> None:
    style = ttk.Style()
    default = font.nametofont("TkDefaultFont", root=root)
    fonts = getattr(root, "_cnkibug_fonts", None)
    if fonts is None:
        available = set(font.families(root=root))
        family = next((name for name in (
            "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", "WenQuanYi Micro Hei",
        ) if name in available), default.actual("family"))
        named_fonts = set(font.names(root=root))
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkTooltipFont"):
            if name in named_fonts:
                font.nametofont(name, root=root).configure(
                    family=family, size=10, weight="bold" if name == "TkHeadingFont" else "normal",
                )
        root.option_add("*TCombobox*Listbox.font", "TkTextFont")
        fonts = root._cnkibug_fonts = {}
        for role, size, weight in (
            ("Title", 15, "bold"),
            ("Heading", 12, "bold"),
            ("Section", 11, "bold"),
            ("Small", 9, "normal"),
            ("Badge", 9, "normal"),
            ("Bold", 10, "bold"),
        ):
            fonts[role] = font.Font(root=root, **default.actual())
            fonts[role].configure(size=size, weight=weight)

    p = get_palette(style)
    # Configure custom styles via tkinter.ttk.Style directly and register them in
    # ttkbootstrap's active theme so widgets keep their exact style without
    # triggering Bootstyle's automatic re-builders on theme switch.
    def cfg(name: str, **kw) -> None:
        tkttk.Style.configure(style, name, **kw)
        if "." in name and not name[0].islower():
            _CUSTOM_STYLES.add(name)
            style._register_ttkstyle(name)

    def smap(name: str, **kw) -> None:
        tkttk.Style.map(style, name, **kw)
        if "." in name and not name[0].islower():
            _CUSTOM_STYLES.add(name)
            style._register_ttkstyle(name)

    cfg(".", font=default, background=p.canvas, foreground=p.fg)

    # Window backgrounds (root + any open Toplevels)
    configure_window_surface(root, "canvas")
    for child in root.winfo_children():
        if isinstance(child, tk.Toplevel):
            configure_window_surface(child, getattr(child, "_cnkibug_surface", "canvas"))

    # Surface frames & labels
    cfg("TFrame", background=p.canvas)
    cfg("Canvas.TFrame", background=p.canvas)
    cfg("Card.TFrame", background=p.card)
    cfg("Header.TFrame", background=p.card)
    cfg("CardSubtle.TFrame", background=p.card_subtle)

    cfg("TLabel", background=p.canvas, foreground=p.fg)
    cfg("secondary.TLabel", background=p.canvas, foreground=p.muted)
    cfg("Canvas.TLabel", background=p.canvas, foreground=p.fg)
    cfg("CanvasMuted.TLabel", background=p.canvas, foreground=p.muted)
    cfg("Card.TLabel", background=p.card, foreground=p.fg)
    cfg("CardMuted.TLabel", background=p.card, foreground=p.muted)
    cfg("CardSmall.TLabel", background=p.card, foreground=p.muted, font=fonts["Small"])
    cfg("CardDanger.TLabel", background=p.card, foreground=p.danger)
    cfg("CardWarning.TLabel", background=p.card, foreground=p.warning)
    cfg("CardSuccess.TLabel", background=p.card, foreground=p.success)
    cfg("CardSubtle.TLabel", background=p.card_subtle, foreground=p.fg)
    cfg("CardSubtleMuted.TLabel", background=p.card_subtle, foreground=p.muted)
    cfg("CardSubtleSmall.TLabel", background=p.card_subtle, foreground=p.muted, font=fonts["Small"])

    for role in ("Title", "Heading", "Section", "Bold"):
        cfg(f"{role}.TLabel", font=fonts[role], background=p.canvas, foreground=p.fg)
        cfg(f"Card{role}.TLabel", font=fonts[role], background=p.card, foreground=p.fg)
        cfg(f"CardSubtle{role}.TLabel", font=fonts[role], background=p.card_subtle, foreground=p.fg)

    # Semantic soft-tinted badges
    badge_pad = scale_size(root, (7, 2))
    for suffix, bg, fg in (
        ("Neutral", p.soft_bg, p.muted),
        ("Info", p.info_soft, p.info),
        ("Purple", p.purple_soft, p.purple),
        ("Success", p.success_soft, p.success),
        ("Warning", p.warning_soft, p.warning),
        ("Danger", p.danger_soft, p.danger),
    ):
        cfg(
            f"Badge{suffix}.TLabel",
            background=bg, foreground=fg, font=fonts["Badge"], padding=badge_pad, anchor=tk.CENTER,
        )

    # Checkbuttons & Radiobuttons on Canvas / Card / CardSubtle
    for widget_type in ("TCheckbutton", "TRadiobutton"):
        cfg(widget_type, background=p.canvas, foreground=p.fg)
        smap(widget_type, foreground=[("disabled", p.disabled_fg), ("!disabled", p.fg)])
        for prefix, bg in (("Card.", p.card), ("CardSubtle.", p.card_subtle)):
            name = f"{prefix}{widget_type}"
            cfg(name, background=bg, foreground=p.fg)
            smap(
                name,
                background=[("active", bg), ("!active", bg)],
                foreground=[("disabled", p.disabled_fg), ("!disabled", p.fg)],
            )

    # Labelframes (kept clean if any dialog still uses them)
    cfg("TLabelframe", background=p.card, bordercolor=p.border, relief="solid")
    cfg("TLabelframe.Label", background=p.card, foreground=p.muted, font=fonts["Bold"])

    # Control sizing
    control_pad = (
        scale_size(root, 12),
        max(scale_size(root, 5), (scale_size(root, 32) - default.metrics("linespace") - 2) // 2),
    )
    compact_pad = (
        scale_size(root, 10),
        max(scale_size(root, 3), (scale_size(root, 28) - default.metrics("linespace") - 2) // 2),
    )
    icon_pad = (scale_size(root, 6), compact_pad[1])

    def _configure_flat_button(
        name: str,
        *,
        bg: str,
        fg: str,
        hover_bg: str,
        hover_fg: str | None = None,
        disabled_bg: str | None = None,
        disabled_fg: str | None = None,
        pad: tuple[int, int] = control_pad,
        bold: bool = False,
        border: str | None = None,
        arrow: str | None = None,
    ) -> None:
        options: dict = {
            "font": fonts["Bold"] if bold else default,
            "padding": pad,
            "background": bg,
            "foreground": fg,
            "borderwidth": 1 if border else 0,
            "relief": "flat",
            "focusthickness": 0,
            "focuscolor": p.primary,
        }
        if border:
            options["bordercolor"] = border
            options["lightcolor"] = border
            options["darkcolor"] = border
        else:
            options["bordercolor"] = bg
            options["lightcolor"] = bg
            options["darkcolor"] = bg
        if arrow is not None:
            options["arrowcolor"] = arrow
        cfg(name, **options)
        dis_bg = disabled_bg or bg
        dis_fg = disabled_fg or p.disabled_fg
        h_fg = hover_fg or fg
        maps: dict = {
            "background": [
                ("disabled", dis_bg),
                ("pressed !disabled", hover_bg),
                ("active !disabled", hover_bg),
                ("hover !disabled", hover_bg),
                ("!disabled", bg),
            ],
            "foreground": [
                ("disabled", dis_fg),
                ("pressed !disabled", h_fg),
                ("active !disabled", h_fg),
                ("hover !disabled", h_fg),
                ("!disabled", fg),
            ],
            "bordercolor": [
                ("disabled", border or dis_bg),
                ("focus !disabled", p.primary),
                ("pressed !disabled", border or hover_bg),
                ("hover !disabled", border or hover_bg),
                ("!disabled", border or bg),
            ],
            "lightcolor": [
                ("disabled", border or dis_bg),
                ("pressed !disabled", border or hover_bg),
                ("hover !disabled", border or hover_bg),
                ("!disabled", border or bg),
            ],
            "darkcolor": [
                ("disabled", border or dis_bg),
                ("pressed !disabled", border or hover_bg),
                ("hover !disabled", border or hover_bg),
                ("!disabled", border or bg),
            ],
        }
        if arrow is not None:
            maps["arrowcolor"] = [("disabled", dis_fg), ("!disabled", arrow)]
        smap(name, **maps)

    # 1. Primary buttons (custom + ttkbootstrap default primary)
    for name, pad in (
        ("Primary.TButton", control_pad),
        ("PrimaryCompact.TButton", compact_pad),
        ("primary.TButton", control_pad),
        ("TButton", control_pad),
    ):
        _configure_flat_button(
            name,
            bg=p.primary,
            fg=p.primary_fg,
            hover_bg=p.primary_hover,
            disabled_bg=p.primary_disabled_bg,
            disabled_fg=p.primary_disabled_fg,
            pad=pad,
            bold=True,
        )

    # 2. Soft / Tonal buttons
    for name, pad, is_menu in (
        ("Soft.TButton", control_pad, False),
        ("SoftCompact.TButton", compact_pad, False),
        ("Soft.TMenubutton", control_pad, True),
        ("SoftCompact.TMenubutton", compact_pad, True),
        ("secondary.TButton", control_pad, False),
        ("secondary.Outline.TButton", control_pad, False),
        ("secondary.Outline.TMenubutton", control_pad, True),
        ("TMenubutton", control_pad, True),
    ):
        _configure_flat_button(
            name,
            bg=p.soft_bg,
            fg=p.soft_fg,
            hover_bg=p.soft_hover,
            disabled_bg=p.disabled_bg,
            disabled_fg=p.disabled_fg,
            pad=pad,
            arrow=p.soft_fg if is_menu else None,
        )

    # 3. Ghost buttons (Card / CardSubtle / Canvas)
    for prefix, base_bg in (("Ghost", p.card), ("GhostSubtle", p.card_subtle), ("GhostCanvas", p.canvas)):
        _configure_flat_button(
            f"{prefix}.TButton", bg=base_bg, fg=p.fg, hover_bg=p.ghost_hover, pad=control_pad,
        )
        _configure_flat_button(
            f"{prefix}Compact.TButton", bg=base_bg, fg=p.fg, hover_bg=p.ghost_hover, pad=compact_pad,
        )
        _configure_flat_button(
            f"{prefix}Muted.TButton", bg=base_bg, fg=p.muted, hover_bg=p.ghost_hover, hover_fg=p.fg, pad=compact_pad,
        )
        _configure_flat_button(
            f"{prefix}Link.TButton", bg=base_bg, fg=p.primary, hover_bg=p.ghost_hover, hover_fg=p.primary_hover,
            pad=(scale_size(root, 6), scale_size(root, 3)),
        )
        _configure_flat_button(
            f"{prefix}.TMenubutton", bg=base_bg, fg=p.fg, hover_bg=p.ghost_hover, pad=control_pad, arrow=p.muted,
        )
        _configure_flat_button(
            f"{prefix}Compact.TMenubutton", bg=base_bg, fg=p.fg, hover_bg=p.ghost_hover, pad=compact_pad, arrow=p.muted,
        )

    _configure_flat_button(
        "secondary.Link.TButton", bg=p.card, fg=p.muted, hover_bg=p.ghost_hover, hover_fg=p.fg, pad=compact_pad,
    )

    # 4. Danger & Success buttons
    for name, pad in (
        ("DangerSoft.TButton", control_pad),
        ("DangerCompact.TButton", compact_pad),
        ("danger.Outline.TButton", control_pad),
    ):
        _configure_flat_button(
            name, bg=p.danger_soft, fg=p.danger, hover_bg=p.danger_hover,
            disabled_bg=p.disabled_bg, disabled_fg=p.disabled_fg, pad=pad,
        )
    _configure_flat_button(
        "DangerGhost.TButton",
        bg=p.card, fg=p.muted, hover_bg=p.danger_soft, hover_fg=p.danger,
        disabled_bg=p.card, disabled_fg=p.disabled_fg, pad=icon_pad,
    )
    for name, pad in (("Success.TButton", control_pad), ("SuccessCompact.TButton", compact_pad), ("success.TButton", control_pad)):
        _configure_flat_button(
            name, bg=p.success, fg="#FFFFFF", hover_bg=p.success_hover,
            disabled_bg=p.disabled_bg, disabled_fg=p.disabled_fg, pad=pad, bold=True,
        )

    # Settings navigation buttons
    _configure_flat_button(
        "Nav.TButton", bg=p.card_subtle, fg=p.fg, hover_bg=p.ghost_hover,
        pad=scale_size(root, (12, 8)),
    )
    cfg("Nav.TButton", anchor=tk.W)
    _configure_flat_button(
        "NavActive.TButton", bg=p.nav_active, fg=p.primary, hover_bg=p.nav_active,
        pad=scale_size(root, (12, 8)), bold=True,
    )
    cfg("NavActive.TButton", anchor=tk.W)

    # Inputs
    entry_pad = (
        scale_size(root, 10),
        max(scale_size(root, 4), (scale_size(root, 32) - default.metrics("linespace") - 2) // 2),
    )
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        cfg(
            name,
            padding=entry_pad,
            fieldbackground=p.input_bg,
            foreground=p.fg,
            bordercolor=p.border_strong,
            lightcolor=p.border_strong,
            darkcolor=p.border_strong,
            insertcolor=p.fg,
        )
        smap(
            name,
            fieldbackground=[("disabled", p.disabled_bg), ("readonly", p.input_ro)],
            foreground=[("disabled", p.disabled_fg), ("readonly", p.fg)],
            bordercolor=[("focus !disabled", p.primary), ("disabled", p.border)],
        )

    # Notebook tabs
    tab_pad = scale_size(root, (14, 7))
    cfg("TNotebook", background=p.card, borderwidth=0, tabmargins=(0, 0, 0, 0))
    cfg("TNotebook.Tab", font=default, padding=tab_pad, background=p.card, foreground=p.muted, borderwidth=0)
    smap(
        "TNotebook.Tab",
        padding=[("selected", tab_pad), ("!selected", tab_pad)],
        background=[("selected", p.card), ("active !selected", p.card_subtle), ("!selected", p.card)],
        foreground=[("selected", p.primary), ("active !selected", p.fg), ("!selected", p.muted)],
    )

    # Treeview (unified across all tables)
    row_height = max(default.metrics("linespace") + scale_size(root, 12), scale_size(root, 32))
    heading_pad = scale_size(root, (10, 8))
    for prefix in ("", "App.", "Results."):
        tv = f"{prefix}Treeview"
        cfg(
            tv,
            font=default,
            rowheight=row_height,
            background=p.card,
            fieldbackground=p.card,
            foreground=p.fg,
            borderwidth=0,
            relief="flat",
        )
        smap(tv, background=[("selected", p.row_sel)], foreground=[("selected", p.row_sel_fg)])
        cfg(
            f"{tv}.Heading",
            font="TkHeadingFont",
            padding=heading_pad,
            background=p.card_subtle,
            foreground=p.muted,
            borderwidth=0,
            relief="flat",
        )
        smap(
            f"{tv}.Heading",
            background=[("active", p.ghost_hover)],
            foreground=[("active", p.fg)],
        )

    cfg("TPanedwindow", background=p.canvas)


class Card(tk.Frame):
    """Flat 1px-bordered surface card that adapts cleanly to light and dark themes."""

    def __init__(self, master: tk.Misc, *, subtle: bool = False, padding: int | tuple[int, ...] = 0, **kwargs) -> None:
        self._subtle = subtle
        super().__init__(master, bd=0, highlightthickness=1, **kwargs)
        self.body = ttk.Frame(self, style="CardSubtle.TFrame" if subtle else "Card.TFrame", padding=padding)
        self.body.pack(fill=tk.BOTH, expand=True)
        self.bind("<<ThemeChanged>>", self._apply_theme, add="+")
        self._apply_theme()

    def _apply_theme(self, _event: tk.Event | None = None) -> None:
        p = get_palette()
        bg = p.card_subtle if self._subtle else p.card
        self.configure(background=bg, highlightbackground=p.border, highlightcolor=p.border)


class Divider(tk.Frame):
    """Crisp 1px horizontal or vertical hairline divider."""

    def __init__(self, master: tk.Misc, *, orient: str = tk.HORIZONTAL) -> None:
        if orient == tk.VERTICAL:
            super().__init__(master, width=1, bd=0, highlightthickness=0)
        else:
            super().__init__(master, height=1, bd=0, highlightthickness=0)
        self.bind("<<ThemeChanged>>", self._apply_theme, add="+")
        self._apply_theme()

    def _apply_theme(self, _event: tk.Event | None = None) -> None:
        self.configure(background=get_palette().divider)


class ScrollView(ScrolledFrame):
    def __init__(self, master: tk.Misc, *, surface: str = "card", **kwargs) -> None:
        self._surface = surface
        super().__init__(master, **kwargs)
        self.vscroll.configure(bootstyle="secondary")
        self.bind("<Configure>", lambda _event: self.yview(), add="+")
        self.bind("<<ThemeChanged>>", self._apply_surface, add="+")
        self._apply_surface()

    def _apply_surface(self, _event: tk.Event | None = None) -> None:
        p = get_palette()
        bg = p.card if self._surface == "card" else p.card_subtle if self._surface == "subtle" else p.canvas
        frame_style = "Card.TFrame" if self._surface == "card" else "CardSubtle.TFrame" if self._surface == "subtle" else "Canvas.TFrame"
        try:
            self.configure(style=frame_style)
            self.container.configure(style=frame_style)
            for child in self.container.winfo_children():
                if isinstance(child, tk.Canvas):
                    child.configure(background=bg, highlightthickness=0, bd=0)
        except tk.TclError:
            pass

    def yview_moveto(self, fraction: float) -> None:
        super().yview_moveto(fraction)
        first, last = self.vscroll.get()
        needed = first > 0 or last < 1
        if needed and not self.vscroll.winfo_manager():
            self.show_scrollbars()
        elif not needed and self.vscroll.winfo_manager():
            self.hide_scrollbars()


class TextView(tk.Frame):
    """Single-bordered scrollable text widget with auto-hiding scrollbar."""

    def __init__(self, master: tk.Misc, *, height: int = 7, bordered: bool = True, **kwargs) -> None:
        self._bordered = bordered
        kwargs.setdefault("font", "TkDefaultFont")
        kwargs.setdefault("wrap", tk.WORD)
        kwargs.setdefault("relief", tk.FLAT)
        kwargs.setdefault("borderwidth", 0)
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(master, bd=0, highlightthickness=1 if bordered else 0)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.text = tk.Text(self, height=height, **kwargs)
        self.text.configure(
            padx=scale_size(master, 12),
            pady=scale_size(master, 10),
            spacing1=scale_size(master, 2),
            spacing3=scale_size(master, 3),
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        self.vbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.text.yview, bootstyle="secondary")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=self._on_scroll)
        self.bind("<<ThemeChanged>>", self._apply_theme, add="+")
        self._apply_theme()

    def _on_scroll(self, first: str, last: str) -> None:
        self.vbar.set(first, last)
        try:
            needed = float(first) > 0.0 or float(last) < 1.0
        except ValueError:
            needed = True
        if needed and not self.vbar.winfo_ismapped():
            self.vbar.grid()
        elif not needed and self.vbar.winfo_ismapped():
            self.vbar.grid_remove()

    def _apply_theme(self, _event: tk.Event | None = None) -> None:
        p = get_palette()
        bg = p.input_bg if self._bordered else p.card
        self.configure(
            background=bg,
            highlightbackground=p.border,
            highlightcolor=p.primary if self._bordered else p.border,
        )
        self.text.configure(
            background=bg,
            foreground=p.fg,
            insertbackground=p.fg,
            selectbackground=p.row_sel,
            selectforeground=p.row_sel_fg,
            inactiveselectbackground=p.row_sel,
        )
