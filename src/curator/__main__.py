"""Точка входа.

    python -m curator                     стартовое окно версии 2 (platform/app.py)
    python -m curator --snapshot ПАПКА    снимки окна и отчёт без участия человека
    python -m curator --selftest accept|rollback ДЕРЕВО ПАПКА   окно обработки без человека
    python -m curator scan ROOT           консольные команды (см. app/cli.py)

Окно версии 1 (tkinter, gui/) из исходников: from curator.gui.main_window import run.
"""

import sys

if len(sys.argv) > 1 and sys.argv[1] not in ("--snapshot", "--selftest"):
    from curator.app.cli import main

    sys.exit(main())

from curator.platform.app import run

sys.exit(run())
