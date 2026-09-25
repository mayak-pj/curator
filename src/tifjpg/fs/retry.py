"""Повторы при сбоях сети (ARCHITECTURE.md, 9.2).

Снимки лежат на сетевом диске, поэтому обрыв связи — рабочая ситуация, а
не исключение. Временные ошибки повторяются с паузами, постоянные
(нет прав, нет места) пробрасываются сразу.
"""

import errno
import time

from tifjpg.domain.errors import ConversionCancelled, TransientNetworkError

DEFAULT_DELAYS = (5, 15, 30, 60, 120)

# Коды Windows, означающие пропавшую сеть или занятый ресурс.
TRANSIENT_WINDOWS_ERRORS = {
    51,    # ERROR_REM_NOT_LIST — сеть недоступна
    53,    # ERROR_BAD_NETPATH
    54,    # ERROR_NETWORK_BUSY
    55,    # ERROR_DEV_NOT_EXIST
    58,    # ERROR_BAD_REM_ADAP
    59,    # ERROR_UNEXP_NET_ERR
    64,    # ERROR_NETNAME_DELETED
    65,    # ERROR_NETWORK_ACCESS_DENIED (временная потеря сессии)
    67,    # ERROR_BAD_NET_NAME
    71,    # ERROR_REQ_NOT_ACCEP
    121,   # ERROR_SEM_TIMEOUT
    170,   # ERROR_BUSY
    1231,  # ERROR_NETWORK_UNREACHABLE
    1236,  # ERROR_CONNECTION_ABORTED
}

TRANSIENT_ERRNOS = {
    errno.EAGAIN, errno.EBUSY, errno.ECONNABORTED, errno.ECONNRESET, errno.EHOSTDOWN,
    errno.EHOSTUNREACH, errno.EINTR, errno.EIO, errno.ENETDOWN, errno.ENETRESET,
    errno.ENETUNREACH, errno.ENOTCONN, errno.EPIPE, errno.ESTALE, errno.ETIMEDOUT,
}


def is_transient(exc):
    if isinstance(exc, TransientNetworkError):
        return True
    if not isinstance(exc, OSError):
        return False
    if getattr(exc, "winerror", None) in TRANSIENT_WINDOWS_ERRORS:
        return True
    return exc.errno in TRANSIENT_ERRNOS


def with_retry(operation, description="операция", delays=DEFAULT_DELAYS, sleep=time.sleep,
               on_retry=None, is_cancelled=None):
    """Выполнить operation(), повторяя её при временных сбоях.

    Каждая попытка должна быть самодостаточной: состояние перечитывается с
    диска, незавершённые временные файлы удаляются вызывающим кодом.
    """
    attempts = len(delays) + 1
    for attempt in range(1, attempts + 1):
        if is_cancelled is not None and is_cancelled():
            raise ConversionCancelled(description)
        try:
            return operation()
        except OSError as exc:
            if not is_transient(exc) or attempt == attempts:
                raise
            delay = delays[attempt - 1]
            if on_retry is not None:
                on_retry(description, attempt, delay, exc)
            sleep(delay)
    raise AssertionError("недостижимо")
