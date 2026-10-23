"""Окно обработки проходит свои кнопки само, на настоящем сервисе и настоящих файлах (26.5).

    curator.exe --selftest accept   ДЕРЕВО ПАПКА   «Проверить» → «Начать обработку» → «Принять»
    curator.exe --selftest rollback ДЕРЕВО ПАПКА   … → «Откатить» у первой папки → «Отменить всё»

Нажимаются те же кнопки, что у человека, события идут тем же путём (фоновый поток → очередь →
таймер страницы). Подтверждения без человека считаются полученными (Context.interactive=False).
Результат на диске проверяет вызывающая сторона (scripts/e2e_tree.py check в CI).
"""

import os
import time

from curator.platform import modules as registry

REPORT_NAME = "xray_selftest.txt"
TIMEOUT_S = 600


def wait_idle(page, app, timeout=TIMEOUT_S):
    """Крутить цикл событий, пока фоновая работа не закончится и её события не разобраны."""
    deadline = time.monotonic() + timeout
    while page.busy or not page.worker._events.empty():
        if time.monotonic() > deadline:
            raise TimeoutError("окно обработки не освободилось за {} с".format(timeout))
        app.processEvents()
        time.sleep(0.02)
    page.worker.pump()
    app.processEvents()


def describe(page):
    """Что сейчас видно на странице — строками для отчёта."""
    lines = ["    строка прогресса: {}".format(page.current_label.text()),
             "    полоса: {} из {}".format(page.progress.value(), page.progress.maximum()),
             "    статус: {}".format(page.status_label.text()),
             "    кнопки: проверить={} начать={} остановить={} принять={} отменить всё={}".format(
                 *(int(button.isEnabled()) for button in (
                     page.scan_button, page.start_button, page.stop_button,
                     page.accept_button, page.rollback_all_button))),
             "    ошибок и предупреждений: {}".format(page.errors.count)]
    for folder, row in page.folders.rows.items():
        lines.append("    папка «{}»: {} · откат={}".format(row.name.text(), row.status.text(),
                                                          int(row.button.isEnabled())))
    return lines


def run(window, scenario, root, out_dir, lines):
    """Пройти сценарий; вернуть список проблем (пустой — всё как ожидалось)."""
    app = window.context.app
    problems = []
    window.open_module(registry.get("xray"))
    page = window.module_page

    def snap(name):
        for theme_name in ("light", "dark"):
            window.context.set_theme(theme_name, remember=False)
            window.refresh_theme()
            app.processEvents()
            path = os.path.join(out_dir, "xray_{}_{}.png".format(name, theme_name))
            if not window.grab().save(path, "PNG"):
                problems.append("снимок не сохранён: {}".format(path))

    def step(title, button):
        if not button.isEnabled():
            problems.append("{}: кнопка недоступна".format(title))
            return
        started = time.monotonic()
        button.click()
        wait_idle(page, app)
        lines.append("{} ({:.1f} с):".format(title, time.monotonic() - started))
        lines.extend(describe(page))

    lines.append("Готовность: {}".format("готово" if page.ready else "недоступно"))
    lines.extend("    " + line for line in page.lines)
    if not page.ready:
        problems.append("страница не готова к обработке: " + "; ".join(page.lines))
        return problems

    page.root_edit.setText(root)
    step("Проверить", page.scan_button)
    to_process = len(page.scan_result.to_process) if page.scan_result else 0
    if not to_process:
        problems.append("«Проверить» не нашла папок к обработке")
        return problems
    snap("scan")

    step("Начать обработку", page.start_button)
    summary = page.summary
    if summary is None or len(summary.succeeded) != to_process or summary.failed:
        problems.append("обработка: ожидалось успешно {}, получено {}".format(
            to_process, summary and "{} успешно, {} с ошибками".format(len(summary.succeeded), len(summary.failed))))
    if page.progress.value() != page.progress.maximum():
        problems.append("полоса прогресса не дошла до конца")
    done_rows = [row for row in page.folders.rows.values() if row.status.text() == "готово"]
    if len(done_rows) != to_process or not all(row.button.isEnabled() for row in done_rows):
        problems.append("не у всех обработанных папок статус «готово» и доступный «Откатить»")
    if not (page.accept_button.isEnabled() and page.rollback_all_button.isEnabled()):
        problems.append("после обработки недоступны «Принять» / «Отменить всё»")
    snap("done")

    if scenario == "accept":
        step("Принять", page.accept_button)
        if any(row.button.isEnabled() for row in page.folders.rows.values()) or page.accept_button.isEnabled():
            problems.append("после «Принять» откат всё ещё предлагается")
    else:
        first = done_rows[0]
        step("Откатить «{}»".format(first.name.text()), first.button)
        if first.status.text() != "откат выполнен" or first.button.isEnabled():
            problems.append("после «Откатить» у папки «{}»: {}".format(first.name.text(), first.status.text()))
        step("Отменить всё", page.rollback_all_button)
        if any(row.status.text() != "откат выполнен" for row in done_rows):
            problems.append("после «Отменить всё» не все папки откатаны")
        if page.accept_button.isEnabled() or page.rollback_all_button.isEnabled():
            problems.append("после «Отменить всё» кнопки «Принять» / «Отменить всё» доступны")
    snap("finished")
    if page.notices:
        problems.extend("сообщение окна: " + notice for notice in page.notices)
    return problems
