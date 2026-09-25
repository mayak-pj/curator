"""События сервиса обработки (ARCHITECTURE.md, раздел 5).

GUI ничего не знает о файлах и libvips: он получает эти события через
очередь и рисует их. Консоль подписывается на те же события.
"""

SESSION_STARTED = "session_started"
SESSION_FINISHED = "session_finished"
SESSION_ACCEPTED = "session_accepted"

SCAN_STARTED = "scan_started"
SCAN_FINISHED = "scan_finished"

FOLDER_STARTED = "folder_started"
FOLDER_FINISHED = "folder_finished"
FOLDER_SKIPPED = "folder_skipped"

FILE_STARTED = "file_started"
FILE_PREPARED = "file_prepared"
FILE_COMPLETED = "file_completed"
PROGRESS = "progress"
RETRY = "retry"

ROLLBACK_STARTED = "rollback_started"
ROLLBACK_FINISHED = "rollback_finished"

RECOVERY_FOUND = "recovery_found"
PROBLEM = "problem"


def event(kind, **data):
    data["kind"] = kind
    return data
