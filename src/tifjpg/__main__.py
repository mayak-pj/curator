"""Точка входа.

    python -m tifjpg               окно программы
    python -m tifjpg scan ROOT     консольные команды (см. app/cli.py)
"""

import sys

if len(sys.argv) > 1:
    from tifjpg.app.cli import main

    sys.exit(main())

from tifjpg.gui.main_window import run

sys.exit(run())
