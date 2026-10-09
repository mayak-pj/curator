"""Детерминированный порядок файлов (ARCHITECTURE.md, раздел 7).

Снимки делались последовательно, поэтому порядок задаётся именем файла и
не зависит от порядка выдачи файловой системы. Числа сравниваются как
числа: «снимок 2» идёт перед «снимок 10».
"""

import re

from curator.domain.naming import normalize

_CHUNKS = re.compile(r"(\d+)")


def natural_key(name):
    parts = _CHUNKS.split(normalize(name))
    key = []
    for index, part in enumerate(parts):
        if index % 2:
            key.append((0, int(part), ""))
        elif part:
            key.append((1, 0, part))
    # Точное имя в конце — чтобы порядок был строгим при совпадении ключей.
    key.append((2, 0, name))
    return key


def natural_sorted(names):
    return sorted(names, key=natural_key)
