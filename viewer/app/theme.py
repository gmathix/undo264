"""Colour themes: one `Theme` = one QPalette + one stylesheet + the colours custom-painted widgets read.

Widgets that paint themselves (partition map, DPB table, viewport background) call
`current()` at paint time; the main window calls `apply()` when the user switches theme.
The overlay colours drawn *on the video* (MV arrows, grid, selection...) are deliberately
not themed: they must stay readable on any picture.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette


@dataclass(frozen=True)
class Theme:
    name: str
    # surfaces
    window: str        # main window / docks background
    panel: str         # cards, trees, tables
    panel_alt: str     # alternate rows, inputs, subtle fills
    viewport: str      # video canvas background
    border: str
    # text
    text: str
    text_dim: str
    # accents
    accent: str
    accent_text: str   # text drawn on top of `accent`
    accent_soft: str   # translucent accent fill (hover / checked), '#AARRGGBB' as Qt reads it
    ok: str
    error: str
    # custom painted widgets
    map_bg: str
    map_part: str
    map_part_border: str
    map_select: str
    map_intra: str
    # DPB table
    dpb_empty: str
    dpb_cell: str
    dpb_active_fg: str
    dpb_clicked_bg: str
    dpb_clicked_fg: str
    dpb_l0: str
    dpb_l1: str
    dpb_slot: str
    # macroblock kind badges
    badge_intra: str
    badge_inter: str
    badge_skip: str


SLATE = Theme(
    name="Slate",
    window="#1b1e24", panel="#232830", panel_alt="#2a303a", viewport="#14161a", border="#343b47",
    text="#e4e8ef", text_dim="#8b95a5",
    accent="#4c9aff", accent_text="#0b1220", accent_soft="#334c9aff",
    ok="#4ade80", error="#ff6b6b",
    map_bg="#14161a", map_part="#3a4250", map_part_border="#8b95a5", map_select="#ffd60a",
    map_intra="#ff7a7a",
    dpb_empty="#1b1e24", dpb_cell="#343b47", dpb_active_fg="#4ade80",
    dpb_clicked_bg="#e5b800", dpb_clicked_fg="#111111",
    dpb_l0="#b8434a", dpb_l1="#3b6fd4", dpb_slot="#8f8a3a",
    badge_intra="#c2556a", badge_inter="#3d7fd6", badge_skip="#6b7686",
)

MIDNIGHT = Theme(
    name="Midnight",
    window="#0f1020", panel="#16182d", panel_alt="#1e2140", viewport="#080913", border="#2c3060",
    text="#e6e6fa", text_dim="#8284b0",
    accent="#9d7cff", accent_text="#12082b", accent_soft="#339d7cff",
    ok="#5eead4", error="#ff7a90",
    map_bg="#080913", map_part="#2a2e5a", map_part_border="#8284b0", map_select="#ffd60a",
    map_intra="#ff8fa3",
    dpb_empty="#0f1020", dpb_cell="#2c3060", dpb_active_fg="#5eead4",
    dpb_clicked_bg="#e5b800", dpb_clicked_fg="#111111",
    dpb_l0="#b8435f", dpb_l1="#4a66e0", dpb_slot="#8c7a3a",
    badge_intra="#c0507a", badge_inter="#5a6ee0", badge_skip="#5f6490",
)

DAYLIGHT = Theme(
    name="Daylight",
    window="#eef0f4", panel="#ffffff", panel_alt="#f3f5f8", viewport="#d9dde4", border="#cdd3dd",
    text="#1d2330", text_dim="#6a7587",
    accent="#2563eb", accent_text="#ffffff", accent_soft="#262563eb",
    ok="#15803d", error="#c62828",
    map_bg="#f3f5f8", map_part="#dfe4ec", map_part_border="#6a7587", map_select="#f5b700",
    map_intra="#d03a4a",
    dpb_empty="#e4e8ef", dpb_cell="#c4cbd8", dpb_active_fg="#15803d",
    dpb_clicked_bg="#f5b700", dpb_clicked_fg="#111111",
    dpb_l0="#e07a80", dpb_l1="#6f9cf0", dpb_slot="#d8cf7a",
    badge_intra="#c2556a", badge_inter="#2f6fd0", badge_skip="#7a8596",
)

THEMES: dict[str, Theme] = {t.name: t for t in (SLATE, MIDNIGHT, DAYLIGHT)}
DEFAULT_THEME = DAYLIGHT.name

_current: Theme = SLATE


def current() -> Theme:
    return _current


def qcolor(value: str) -> QColor:
    return QColor(value)


def _palette(t: Theme) -> QPalette:
    p = QPalette()
    roles = {
        QPalette.Window: t.window, QPalette.WindowText: t.text,
        QPalette.Base: t.panel, QPalette.AlternateBase: t.panel_alt,
        QPalette.Text: t.text, QPalette.Button: t.panel_alt, QPalette.ButtonText: t.text,
        QPalette.ToolTipBase: t.panel_alt, QPalette.ToolTipText: t.text,
        QPalette.Highlight: t.accent, QPalette.HighlightedText: t.accent_text,
        QPalette.Link: t.accent, QPalette.BrightText: t.error,
        QPalette.PlaceholderText: t.text_dim,
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    dim = QColor(t.text_dim)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, dim)
    return p


def _stylesheet(t: Theme) -> str:
    return f"""
* {{ outline: none; }}
QMainWindow, QDialog {{ background: {t.window}; }}
QMainWindow::separator {{ background: {t.window}; width: 5px; height: 5px; }}
QMainWindow::separator:hover {{ background: {t.accent}; }}
QToolTip {{ background: {t.panel_alt}; color: {t.text}; border: 1px solid {t.border}; padding: 4px 6px; }}

QMenuBar {{ background: {t.window}; color: {t.text}; border-bottom: 1px solid {t.border}; padding: 2px 4px; }}
QMenuBar::item {{ padding: 4px 10px; border-radius: 5px; background: transparent; }}
QMenuBar::item:selected {{ background: {t.accent_soft}; }}
QMenu {{ background: {t.panel}; color: {t.text}; border: 1px solid {t.border}; padding: 4px; }}
QMenu::item {{ padding: 5px 24px 5px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t.accent_soft}; }}
QMenu::item:disabled {{ color: {t.text_dim}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 6px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 4px; }}

QToolBar {{ background: {t.window}; border: none; padding: 3px 6px; spacing: 4px; }}
QToolBar#Transport {{ border-top: 1px solid {t.border}; }}
QToolBar#Overlays {{ border-bottom: 1px solid {t.border}; }}
QToolBar::separator {{ background: {t.border}; width: 1px; margin: 4px 6px; }}
QToolButton {{ color: {t.text}; background: transparent; border: 1px solid transparent;
              border-radius: 6px; padding: 4px 10px; }}
QToolButton:hover {{ background: {t.accent_soft}; }}
QToolButton:pressed {{ background: {t.accent_soft}; border-color: {t.accent}; }}
QToolButton:checked {{ background: {t.accent_soft}; border-color: {t.accent}; color: {t.text}; }}
QToolButton:disabled {{ color: {t.text_dim}; }}

QDockWidget {{ color: {t.text}; }}
QDockWidget::title {{ background: {t.window}; color: {t.text_dim}; padding: 7px 10px;
                     border-bottom: 1px solid {t.border}; text-align: left; font-weight: 600; }}
QDockWidget::close-button, QDockWidget::float-button {{ background: transparent; border: none; padding: 2px; }}
QDockWidget::close-button:hover, QDockWidget::float-button:hover {{ background: {t.accent_soft}; border-radius: 4px; }}

QStatusBar {{ background: {t.window}; color: {t.text_dim}; border-top: 1px solid {t.border}; }}
QStatusBar::item {{ border: none; }}
QLabel {{ background: transparent; }}
QLabel[role="dim"] {{ color: {t.text_dim}; }}
QLabel[role="key"] {{ background: {t.panel_alt}; color: {t.text}; border: 1px solid {t.border};
                     border-bottom: 2px solid {t.border}; border-radius: 5px; padding: 2px 8px; min-width: 14px; }}
QLabel[role="section"] {{ color: {t.text_dim}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}

QPushButton {{ background: {t.panel_alt}; color: {t.text}; border: 1px solid {t.border};
              border-radius: 7px; padding: 6px 16px; }}
QPushButton:hover {{ border-color: {t.accent}; }}
QPushButton:pressed {{ background: {t.accent_soft}; }}
QPushButton:disabled {{ color: {t.text_dim}; background: {t.panel}; border-color: {t.border}; }}
QPushButton[primary="true"] {{ background: {t.accent}; color: {t.accent_text}; border: 1px solid {t.accent}; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ border-color: {t.text}; }}
QPushButton[primary="true"]:disabled {{ background: {t.panel_alt}; color: {t.text_dim}; border-color: {t.border}; }}

QFrame#card {{ background: {t.panel}; border: 1px solid {t.border}; border-radius: 10px; }}
QFrame#card QLabel {{ background: transparent; }}
QFrame#fileRow {{ background: {t.panel_alt}; border: 1px dashed {t.border}; border-radius: 8px; }}
QFrame#fileRow[filled="true"] {{ border: 1px solid {t.accent}; }}
QWidget#launchPage {{ background: {t.viewport}; }}
QLabel#launchError {{ color: {t.error}; }}

QTreeWidget, QTableWidget {{ background: {t.panel}; alternate-background-color: {t.panel_alt};
                            border: 1px solid {t.border}; border-radius: 8px; }}
QTreeWidget::item {{ padding: 2px 0; }}
QTreeWidget::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}
QHeaderView::section {{ background: {t.panel_alt}; color: {t.text_dim}; border: none;
                       border-bottom: 1px solid {t.border}; padding: 4px 6px; }}
QTableWidget {{ gridline-color: {t.window}; }}

QGroupBox {{ border: none; margin-top: 0; padding-top: 0; }}
QCheckBox {{ spacing: 6px; padding: 2px 0; }}
QCheckBox:disabled {{ color: {t.text_dim}; }}

QSlider::groove:horizontal {{ height: 4px; background: {t.border}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t.text}; width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; }}
QSlider::handle:horizontal:hover {{ background: {t.accent}; }}
QSlider::handle:horizontal:disabled {{ background: {t.text_dim}; }}
QSlider::sub-page:horizontal:disabled {{ background: {t.border}; }}

QProgressBar {{ background: {t.panel_alt}; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t.border}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {t.text_dim}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {t.border}; border-radius: 4px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {t.text_dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QGraphicsView {{ border: none; }}
"""


def apply(app, name: str) -> Theme:
    """Make `name` the active theme and restyle the whole application."""
    global _current
    _current = THEMES.get(name, SLATE)
    app.setPalette(_palette(_current))
    app.setStyleSheet(_stylesheet(_current))
    return _current
