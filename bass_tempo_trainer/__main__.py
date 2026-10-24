"""python -m bass_tempo_trainer [--simulate]"""
import argparse
import sys


def main() -> None:
    ap = argparse.ArgumentParser(prog="bass_tempo_trainer")
    ap.add_argument("--simulate", action="store_true",
                    help="no audio devices: a simulated player plays along")
    args = ap.parse_args()

    from PySide6.QtWidgets import QApplication
    from .ui import theme
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Bass Tempo Trainer")
    app.setStyle("Fusion")
    app.setPalette(theme.palette())
    app.setStyleSheet(theme.STYLE)
    w = MainWindow(simulate=args.simulate)
    w.resize(1500, 1000)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
