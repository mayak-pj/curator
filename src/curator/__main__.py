"""Точка входа.

    python -m curator                     стартовое окно версии 2 (platform/app.py)
    python -m curator --snapshot ПАПКА    снимки окна и отчёт без участия человека
    python -m curator scan ROOT           консольные команды (см. app/cli.py)

Окно версии 1 (tkinter, gui/) из исходников: from curator.gui.main_window import run.
"""

import sys

if len(sys.argv) > 1 and not sys.argv[1].startswith("--snapshot"):
    from curator.app.cli import main

    sys.exit(main())

from curator.platform.app import run

sys.exit(run())
