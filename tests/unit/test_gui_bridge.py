import threading
import time

from curator.gui.bridge import TASK_DONE, TASK_FAILED, Worker


class FakeScheduler:
    """Вместо root.after: запоминает, что и через сколько попросили вызвать."""

    def __init__(self):
        self.calls = []

    def __call__(self, delay, callback):
        self.calls.append((delay, callback))


def wait_for(condition, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def collect(worker):
    received = []
    worker._handler = received.append
    return received


def test_result_comes_back_through_the_queue():
    scheduler = FakeScheduler()
    events = []
    worker = Worker(scheduler, events.append)

    assert worker.submit("scan", lambda value: value * 2, 21)
    assert wait_for(lambda: not worker.busy)
    worker.pump()

    assert events == [{"kind": TASK_DONE, "tag": "scan", "result": 42}]


def test_exception_in_background_is_reported_not_lost():
    worker = Worker(FakeScheduler(), lambda event: None)
    events = collect(worker)

    worker.submit("start", lambda: (_ for _ in ()).throw(ValueError("сбой сети")))
    assert wait_for(lambda: not worker.busy)
    worker.pump()

    assert events[0]["kind"] == TASK_FAILED
    assert isinstance(events[0]["error"], ValueError)
    assert "сбой сети" in events[0]["traceback"]


def test_only_one_task_at_a_time():
    worker = Worker(FakeScheduler(), lambda event: None)
    release = threading.Event()

    assert worker.submit("first", release.wait, 5)
    assert not worker.submit("second", lambda: None)
    release.set()
    assert wait_for(lambda: not worker.busy)
    assert worker.submit("third", lambda: None)


def test_events_posted_from_another_thread_arrive_in_order():
    worker = Worker(FakeScheduler(), lambda event: None)
    events = collect(worker)

    def producer():
        for index in range(5):
            worker.post({"kind": "progress", "index": index})

    thread = threading.Thread(target=producer)
    thread.start()
    thread.join()
    worker.pump()

    assert [event["index"] for event in events] == [0, 1, 2, 3, 4]


def test_polling_reschedules_itself():
    scheduler = FakeScheduler()
    worker = Worker(scheduler, lambda event: None, interval_ms=50)

    worker.start_polling()

    assert scheduler.calls and scheduler.calls[0][0] == 50
    scheduler.calls[0][1]()  # имитируем срабатывание таймера
    assert len(scheduler.calls) == 2
