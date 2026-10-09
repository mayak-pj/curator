"""Точка входа.

    python -m curator               окно программы
    python -m curator scan ROOT     консольные команды (см. app/cli.py)
"""

import sys

if len(sys.argv) > 1:
    from curator.app.cli import main

    sys.exit(main())

from curator.gui.main_window import run

sys.exit(run())
