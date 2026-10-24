"""Colours and the application style."""
from PySide6.QtGui import QColor, QPalette

BG = "#14161b"
PANEL = "#1d2028"
PANEL_HI = "#2a2e39"
BORDER = "#3a3f4d"
TEXT = "#e8eaf0"
MUTED = "#9aa0ad"
ACCENT = "#4ea1ff"
ACCENT2 = "#ff8a3d"   # downbeat
GOLD = "#f5c242"
TIGHT = "#3ccf6e"
OK = "#f2c037"
OFF = "#ff5a5a"
MISS = "#8a8f9c"
GRADE = {"tight": TIGHT, "ok": OK, "off": OFF, "miss": MISS, "stray": MISS}

STYLE = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-size: 15px; }}
QFrame#panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel {{ background: transparent; }}
QPushButton {{ background: {PANEL_HI}; border: 1px solid {BORDER}; border-radius: 8px;
              padding: 9px 18px; font-weight: 600; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #5d6270; border-color: #2c303a; }}
QPushButton#primary {{ background: {ACCENT}; color: #0b1220; border: none; }}
QPushButton#primary:disabled {{ background: #2f4766; color: #6c7a90; }}
QPushButton#danger {{ background: #5a2328; border-color: {OFF}; }}
QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {{ background: {PANEL_HI}; border: 1px solid {BORDER};
              border-radius: 6px; padding: 6px 8px; }}
QSpinBox#bpm {{ font-size: 26px; font-weight: 700; min-width: 110px; }}
QProgressBar {{ background: {PANEL_HI}; border: 1px solid {BORDER}; border-radius: 4px; height: 12px; }}
QProgressBar::chunk {{ background: {TIGHT}; border-radius: 3px; }}
QStatusBar {{ background: {PANEL}; color: {MUTED}; }}
QCheckBox::indicator {{ width: 18px; height: 18px; }}
"""


def palette() -> QPalette:
    p = QPalette()
    for role, c in [(QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, PANEL_HI),
                    (QPalette.Text, TEXT), (QPalette.Button, PANEL_HI), (QPalette.ButtonText, TEXT),
                    (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#0b1220"),
                    (QPalette.ToolTipBase, PANEL), (QPalette.ToolTipText, TEXT)]:
        p.setColor(role, QColor(c))
    return p
