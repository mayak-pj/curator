"""Удобства окна между запусками: тема и последняя папка (gui_state.json рядом с exe).

Тот же файл и те же ключи, что у окна версии 1 (`dark`, `last_root`): не журнал, поэтому
читается и пишется best-effort — недоступный файл не мешает работе.
"""

import json


def load(path):
    try:
        with open(path, encoding="utf-8") as stream:
            state = json.load(stream)
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save(path, **changes):
    state = load(path)
    state.update(changes)
    try:
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
    except OSError:
        return False
    return True
