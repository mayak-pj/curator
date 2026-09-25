"""Мост между фоновым потоком и окном (ARCHITECTURE.md, раздел 18).

tkinter можно трогать только из главного потока, а обработка идёт в
фоновом. Поэтому события складываются в очередь, а окно разбирает её по
таймеру. Планировщик передаётся снаружи (root.after), чтобы мост можно
было проверить тестами без окна.
"""

import queue
import threading
import traceback

TASK_DONE = "task_done"
TASK_FAILED = "task_failed"


class Worker:
    def __init__(self, scheduler, handler, interval_ms=100):
        self._scheduler = scheduler          # обычно root.after
        self._handler = handler              # вызывается в главном потоке
        self._interval = interval_ms
        self._events = queue.Queue()
        self._thread = None

    @property
    def busy(self):
        return self._thread is not None and self._thread.is_alive()

    def post(self, event):
        """Положить событие в очередь. Вызывается из любого потока."""
        self._events.put(event)

    def submit(self, tag, function, *args, **kwargs):
        """Запустить работу в фоне. Пока предыдущая не закончилась — отказ."""
        if self.busy:
            return False
        self._thread = threading.Thread(
            target=self._run, args=(tag, function, args, kwargs), daemon=True)
        self._thread.start()
        return True

    def pump(self):
        """Разобрать накопленные события в главном потоке."""
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            self._handler(event)

    def start_polling(self):
        self.pump()
        self._scheduler(self._interval, self.start_polling)

    def _run(self, tag, function, args, kwargs):
        try:
            result = function(*args, **kwargs)
        except BaseException as exc:  # в фоновом потоке некому ловить исключение
            self.post({"kind": TASK_FAILED, "tag": tag, "error": exc,
                       "traceback": traceback.format_exc()})
        else:
            self.post({"kind": TASK_DONE, "tag": tag, "result": result})
