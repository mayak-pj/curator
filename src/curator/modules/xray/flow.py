"""Анимированная сцена обработки: «как видео» (этап 15, D25, раздел 26.6).

Папка «открывается», её файлы появляются плитками; у файла, который обрабатывается, — значок текущего
шага (чтение → конвертация → запись JPEG → перенос в архив) и кольцо прогресса; рядом вырастает JPEG;
при фиксации оригинал летит в папку «ИСХ», а имя плавно меняется со старого на новое.

Только отображение уже существующих событий сервиса (file_started, progress, file_prepared,
file_completed, folder_finished): ядро не менялось. Честность: переименование и перенос идут в фазе B
короткой пачкой, поэтому пока не пришло file_completed, JPEG стоит полупрозрачным («ждёт фиксации»);
полёты оригиналов в архив выстраиваются в очередь с небольшим интервалом, чтобы пачку можно было
разглядеть, — порядок и состав соответствуют реальным событиям, задержка только на показ.
Таймер кадров работает, пока что-то движется, и останавливается, когда сцена стоит на месте.
"""

import math
import os
import time

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF, QTextOption
from PyQt5.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from curator.domain import states
from curator.modules.xray import status as status_module

# Шаги файла по порядку и их подписи
STEPS = (
    (states.STAGED, "чтение"),
    ("convert", "конвертация"),
    (states.JPEG_UPLOADED, "запись JPEG"),
    (states.SOURCE_COPIED, "перенос в архив"),
)
STEP_KEYS = tuple(key for key, _label in STEPS)

WAITING = "waiting"
RUNNING = "running"
PREPARED = "prepared"      # фаза A для файла закончена, имена ещё не окончательные
COMPLETED = "completed"

APPEAR_S = 0.35            # плитка появляется
FLY_S = 0.6                # оригинал летит в архив
FLY_GAP_S = 0.22           # минимальный интервал между полётами
DONE_GAP_S = 0.12          # интервал между сменами имён пачки фазы B
RENAME_S = 0.5             # смена имени
BUMP_S = 0.4               # «толчок» папки архива
LID_S = 0.5                # открытие/закрытие папки
FRAME_MS = 33
SCENE_RADIUS = 10
CLIP_MARGIN_TOP = 12
HOLD_S = 1.0               # после папки её итог держится на экране, прежде чем откроется следующая

TILE_W, TILE_H = 104, 100
HEADER_H, FOOTER_H, ARCHIVE_W = 44, 76, 128


def step_key(stage):
    return status_module.progress_bucket(stage)


def clamp01(value):
    return max(0.0, min(1.0, value))


def ease_out(t):
    return 1 - (1 - clamp01(t)) ** 3


def ease_in_out(t):
    t = clamp01(t)
    return t * t * (3 - 2 * t)


def ease_out_back(t):
    t = clamp01(t)
    return 1 + 2.70158 * (t - 1) ** 3 + 1.70158 * (t - 1) ** 2


class FileState:
    """Один файл папки: имена «было → стало», шаг и времена анимаций."""

    def __init__(self, planned, appear_at=0.0):
        self.index = planned.index
        self.before = os.path.basename(planned.source)
        self.jpeg = os.path.basename(planned.jpeg)
        self.archive = os.path.basename(planned.archive)
        self.archive_dir = os.path.basename(os.path.dirname(planned.archive))
        self.phase = WAITING
        self.current = None          # ключ шага, который идёт сейчас
        self.fraction = 0.0
        self.done_steps = set()
        self.jpeg_fraction = 0.0     # насколько «выросла» JPEG-копия
        self.appear_at = appear_at
        self.fly_at = None           # когда оригинал отправится в архив
        self.landed_at = None        # когда долетел
        self.done_at = None          # когда имена зафиксированы (фаза B): смена имени и галочка

    @property
    def after(self):
        return "{} + {}\\{}".format(self.jpeg, self.archive_dir, self.archive)

    def start(self):
        self.phase = RUNNING
        self.current, self.fraction, self.done_steps = None, 0.0, set()
        self.jpeg_fraction = 0.0

    def stage(self, stage, fraction):
        key = step_key(stage)
        if key not in STEP_KEYS:
            return
        if self.current is not None and key != self.current:
            self.done_steps.add(self.current)
        self.current, self.fraction = key, fraction
        if key == states.JPEG_UPLOADED:
            self.jpeg_fraction = max(self.jpeg_fraction, fraction)
        if fraction >= 1.0 and key != "convert":     # конвертация — две стадии в одной шкале
            self.done_steps.add(key)

    def prepared(self):
        self.phase = PREPARED
        self.current = None
        self.done_steps = set(STEP_KEYS)
        self.jpeg_fraction = 1.0

    def completed(self):
        self.phase = COMPLETED
        self.current = None
        self.done_steps = set(STEP_KEYS)
        self.jpeg_fraction = 1.0


class Palette:
    """Цвета сцены — токены темы."""

    FALLBACK = {"surface": "#ffffff", "surface_alt": "#eef1f5", "border": "#d5dae1", "text": "#1c2430",
                "text_muted": "#647082", "accent": "#2563eb", "accent_soft": "#e3ecfd", "on_accent": "#ffffff",
                "success": "#1a7f37", "warning": "#b26a00", "danger": "#c62828"}

    def __init__(self, theme=None):
        self._theme = theme

    def __call__(self, key, alpha=1.0):
        color = QColor(self._theme.color(key) if self._theme is not None else self.FALLBACK[key])
        color.setAlphaF(clamp01(alpha))
        return color


class Scene(QWidget):
    """Полотно сцены. Держит плитки текущей папки и рисует их по времени."""

    def __init__(self, parent=None, clock=time.monotonic):
        super().__init__(parent)
        self.setMinimumSize(360, 190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._clock = clock
        self.palette_of = Palette()
        self.tiles = {}
        self.order = []
        self.folder_name = ""
        self.enabled = True
        self.archived = 0
        self.bump_at = None
        self.lid_open_at = None
        self.lid_close_at = None
        self._fly_slot = 0.0
        self._done_slot = 0.0
        self._focus = None           # индекс файла, на котором «камера»
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------ состояние

    def open_folder(self, name, planned_files):
        now = self._clock()
        self.folder_name = name
        self.tiles, self.order = {}, []
        self.archived, self.bump_at, self.lid_close_at = 0, None, None
        self._fly_slot = 0.0
        self._done_slot = 0.0
        self._focus = None
        self.lid_open_at = now
        for position, planned in enumerate(planned_files):
            tile = FileState(planned, appear_at=now + LID_S * 0.6 + min(position, 12) * 0.05)
            self.tiles[planned.index] = tile
            self.order.append(tile)
        self._wake()

    def reset(self):
        self.tiles, self.order, self.folder_name = {}, [], ""
        self.archived, self.bump_at, self.lid_open_at, self.lid_close_at = 0, None, None, None
        self._focus = None
        self._wake()

    def touch(self, index=None, fly=False, done=False):
        if index is not None and not done:      # пачка фазы B не уводит «камеру» с обрабатываемого файла
            self._focus = index
        tile = self.tiles.get(index)
        now = self._clock()
        if fly and tile is not None and tile.fly_at is None:
            # Копия оригинала в архиве записана и проверена (шаг «перенос в архив» закончен): летит туда.
            self._fly_slot = max(now, self._fly_slot + FLY_GAP_S)
            tile.fly_at = self._fly_slot
        if done and tile is not None and tile.done_at is None:
            # Фаза B идёт пачкой: имена меняются по очереди, чтобы пачку можно было разглядеть.
            self._done_slot = max(now, self._done_slot + DONE_GAP_S)
            tile.done_at = self._done_slot
        self._wake()

    def folder_closed(self):
        self.lid_close_at = self._clock()
        self._wake()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self._wake()

    def current_tile(self):
        for tile in self.order:
            if tile.phase == RUNNING:
                return tile
        return self.tiles.get(self._focus)

    @property
    def caption(self):
        total = len(self.order)
        tile = self.current_tile()
        if not total:
            return ""
        if tile is not None and tile.phase == RUNNING:
            return "Файл {} из {}:  {}".format(tile.index, total, tile.before)
        done = sum(1 for t in self.order if t.phase == COMPLETED)
        if done == total:
            return "Папка готова: {} из {} файлов зафиксированы".format(done, total)
        if tile is not None and tile.phase == PREPARED:
            return "Файл {} из {} подготовлен, ждёт фиксации имён".format(tile.index, total)
        return "Файлов в папке: {}".format(total)

    # ---------------------------------------------------------------- время

    def _wake(self):
        if not self._timer.isActive():
            self._timer.start(FRAME_MS)
        self.update()

    def _tick(self):
        now = self._clock()
        for tile in self.order:
            if tile.fly_at is not None and tile.landed_at is None and now >= tile.fly_at + FLY_S:
                tile.landed_at = tile.fly_at + FLY_S
                self.archived += 1
                self.bump_at = tile.landed_at
        self.update()
        if not self._animating(now):
            self._timer.stop()

    def _animating(self, now):
        if any(t.phase == RUNNING for t in self.order):
            return True
        for tile in self.order:
            if now < tile.appear_at + APPEAR_S:
                return True
            if tile.fly_at is not None and (tile.landed_at is None or now < tile.landed_at + RENAME_S):
                return True
            if tile.done_at is not None and now < tile.done_at + RENAME_S:
                return True
        for moment, length in ((self.bump_at, BUMP_S), (self.lid_open_at, LID_S), (self.lid_close_at, LID_S)):
            if moment is not None and now < moment + length:
                return True
        return False

    # ------------------------------------------------------------- рисование

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        colour = self.palette_of
        width, height = self.width(), self.height()
        # Скруглённая рамка, как у списка папок (radius_md = 10): содержимое обрезается по ней.
        frame = QPainterPath()
        frame.addRoundedRect(QRectF(0.5, 0.5, width - 1, height - 1), SCENE_RADIUS, SCENE_RADIUS)
        painter.setClipPath(frame)
        painter.fillPath(frame, colour("surface"))
        try:
            self._paint_scene(painter, colour, width, height)
        finally:
            painter.setClipping(False)
            painter.setPen(QPen(colour("border"), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(frame)

    def _paint_scene(self, painter, colour, width, height):
        if not self.order or not self.enabled:
            painter.setPen(colour("text_muted"))
            painter.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap,
                             "Показ хода по файлам выключен" if not self.enabled
                             else "Здесь будет показан ход обработки: папка откроется, и каждый файл\n"
                                  "пройдёт чтение, конвертацию, запись JPEG и перенос в архив")
            return
        now = self._clock()
        self._paint_header(painter, now, width)
        grid = QRectF(12, HEADER_H + 10, width - ARCHIVE_W - 24, height - HEADER_H - 10 - FOOTER_H)
        archive = self._archive_rect(width, height)
        self._paint_archive(painter, archive, now)
        self._paint_tiles(painter, grid, archive, now)
        self._paint_footer(painter, now, width, height)

    def _paint_header(self, p, now, width):
        colour = self.palette_of
        if self.lid_close_at is not None and now >= self.lid_close_at and not self._has_unfinished():
            openness = 1 - ease_in_out((now - self.lid_close_at) / LID_S)
        else:
            openness = ease_in_out((now - self.lid_open_at) / LID_S) if self.lid_open_at else 1.0
        self._draw_folder(p, QRectF(14, 6, 38, 30), openness, colour("warning"))
        font = QFont(self.font())
        font.setPixelSize(15)
        font.setBold(True)
        p.setFont(font)
        p.setPen(colour("text"))
        p.drawText(QRectF(62, 0, width - 74, HEADER_H), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(self.folder_name, Qt.ElideMiddle, int(width - 74)))

    def _has_unfinished(self):
        return any(t.phase != COMPLETED for t in self.order)

    def _archive_rect(self, width, height):
        top = HEADER_H + (height - HEADER_H - FOOTER_H) / 2 - 46
        return QRectF(width - ARCHIVE_W + 8, top, ARCHIVE_W - 24, 92)

    def _paint_archive(self, p, rect, now):
        colour = self.palette_of
        scale = 1.0
        opened = 0.0
        if self.bump_at is not None:
            u = clamp01((now - self.bump_at) / BUMP_S)
            scale = 1 + 0.12 * math.sin(u * math.pi)
            opened = math.sin(u * math.pi)
        p.save()
        p.translate(rect.center())
        p.scale(scale, scale)
        p.translate(-rect.center())
        self._draw_folder(p, QRectF(rect.left() + 14, rect.top(), rect.width() - 28, 56), opened,
                          colour("accent"))
        p.restore()
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setBold(True)
        p.setFont(font)
        p.setPen(colour("text"))
        p.drawText(QRectF(rect.left(), rect.top() + 60, rect.width(), 16), Qt.AlignCenter, "ИСХ")
        font.setBold(False)
        font.setPixelSize(11)
        p.setFont(font)
        p.setPen(colour("text_muted"))
        p.drawText(QRectF(rect.left(), rect.top() + 76, rect.width(), 16), Qt.AlignCenter,
                   "перенесено: {}".format(self.archived))

    def _tile_rect(self, grid, position, columns, offset):
        row, column = divmod(position, columns)
        row -= offset
        return QRectF(grid.left() + column * TILE_W, grid.top() + row * TILE_H, TILE_W, TILE_H)

    def _layout(self, grid):
        columns = max(1, int(grid.width() // TILE_W))
        visible_rows = max(1, int(grid.height() // TILE_H))
        total_rows = (len(self.order) + columns - 1) // columns
        focus_row = 0
        for position, tile in enumerate(self.order):
            if tile.index == self._focus:
                focus_row = position // columns
        offset = max(0, min(focus_row - visible_rows // 2, total_rows - visible_rows))
        return columns, visible_rows, offset

    def _paint_tiles(self, p, grid, archive, now):
        columns, visible_rows, offset = self._layout(grid)
        p.save()
        # Запас сверху: значок шага и галочка стоят над иконкой и у верхнего ряда иначе обрезались бы.
        p.setClipRect(grid.adjusted(0, -CLIP_MARGIN_TOP, 0, 0))
        flights = []
        for position, tile in enumerate(self.order):
            rect = self._tile_rect(grid, position, columns, offset)
            if rect.bottom() < grid.top() or rect.top() > grid.bottom():
                continue
            self._paint_tile(p, tile, rect, now, flights, archive)
        p.restore()
        for tile, start, end in flights:        # летящие оригиналы — поверх всего
            self._paint_flight(p, tile, start, end, now)

    def _paint_tile(self, p, tile, rect, now, flights, archive):
        colour = self.palette_of
        t = (now - tile.appear_at) / APPEAR_S
        if t <= 0:
            return
        pop = ease_out_back(t)
        landed = tile.landed_at is not None and now >= tile.landed_at
        done = tile.done_at is not None and now >= tile.done_at
        p.save()
        p.translate(rect.center())
        p.scale(pop, pop)
        p.translate(-rect.center())
        card = rect.adjusted(6, 4, -6, -4)
        running = tile.phase == RUNNING
        if running:
            pulse = 0.5 + 0.5 * math.sin(now * 5)
            path = QPainterPath()
            path.addRoundedRect(card, 10, 10)
            p.fillPath(path, colour("accent_soft"))
            p.setPen(QPen(colour("accent", 0.55 + 0.45 * pulse), 2))
            p.drawPath(path)
        icon_size = 44
        icon_left = rect.center().x() - icon_size / 2
        icon_top = rect.top() + 10
        tif_rect = QRectF(icon_left, icon_top, icon_size, icon_size)
        jpeg_rect = QRectF(icon_left, icon_top, icon_size, icon_size)
        if tile.jpeg_fraction > 0 or tile.phase in (PREPARED, COMPLETED):
            # JPEG вырастает рядом с оригиналом; после переноса оригинала занимает его место
            move = ease_out((now - tile.landed_at) / RENAME_S) if landed else 0.0
            small = 0.62
            size = icon_size * (small + (1 - small) * move)
            left = icon_left + icon_size * 0.42 * (1 - move) + (icon_size - size) * 0.5 * move
            top = icon_top + icon_size * 0.34 * (1 - move) + (icon_size - size) * 0.5 * move
            jpeg_rect = QRectF(left, top, size, size)
        if not landed:
            fly_started = tile.fly_at is not None and now >= tile.fly_at
            if fly_started:
                start = tif_rect.center()
                end = QPointF(archive.center().x(), archive.top() + 28)
                flights.append((tile, start, end))
            else:
                self._draw_document(p, tif_rect, "TIF", colour("accent") if running else colour("text_muted"),
                                    colour("on_accent"))
        if tile.jpeg_fraction > 0 or tile.phase in (PREPARED, COMPLETED):
            if done:
                fade = 1.0
            elif tile.phase == RUNNING:
                fade = 0.25 + 0.75 * ease_out(tile.jpeg_fraction)
            else:
                fade = 0.55          # подготовлен, но имена ещё не зафиксированы
            p.setOpacity(fade)
            self._draw_document(p, jpeg_rect, "JPG", colour("success"), colour("on_accent"))
            p.setOpacity(1.0)
        if running and tile.current:
            self._paint_badge(p, tile, QPointF(tif_rect.right() + 2, tif_rect.top() - 2), now)
        if done:
            self._paint_check(p, QPointF(tif_rect.right() + 2, tif_rect.top() - 2), colour("success"), now - tile.done_at)
        self._paint_name(p, tile, rect, now, done)
        p.restore()

    def _paint_name(self, p, tile, rect, now, done):
        colour = self.palette_of
        font = QFont(self.font())
        font.setPixelSize(11)
        p.setFont(font)
        area = QRectF(rect.left() + 4, rect.top() + 60, rect.width() - 8, 32)
        mix = ease_in_out((now - tile.done_at) / RENAME_S) if done else 0.0
        for text, alpha in ((tile.before, 1 - mix), (tile.jpeg, mix)):
            if alpha <= 0.01:
                continue
            p.setPen(colour("success" if text == tile.jpeg else "text", alpha))
            # Имя без пробелов («Рентгенограмма.jpeg», когда файл один) переносим перед расширением,
            # а слишком длинное слово — где придётся: иначе оно обрезалось с обеих сторон.
            option = QTextOption(Qt.AlignHCenter | Qt.AlignTop)
            option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            p.drawText(area, text.replace(".", "\u200b."), option)

    def _paint_flight(self, p, tile, start, end, now):
        colour = self.palette_of
        u = ease_in_out((now - tile.fly_at) / FLY_S)
        if u >= 1:
            return
        x = start.x() + (end.x() - start.x()) * u
        y = start.y() + (end.y() - start.y()) * u - math.sin(u * math.pi) * 36
        size = 44 * (1 - 0.45 * u)
        p.setOpacity(1 - 0.35 * u)
        self._draw_document(p, QRectF(x - size / 2, y - size / 2, size, size), "TIF",
                            colour("accent"), colour("on_accent"))
        p.setOpacity(1.0)

    def _paint_footer(self, p, now, width, height):
        colour = self.palette_of
        top = height - FOOTER_H
        p.setPen(QPen(colour("border"), 1))
        p.drawLine(0, top, width, top)
        font = QFont(self.font())
        font.setPixelSize(13)
        p.setFont(font)
        p.setPen(colour("text"))
        p.drawText(QRectF(14, top + 6, width - 28, 22), Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.caption, Qt.ElideMiddle, int(width - 28)))
        tile = self.current_tile()
        pill_w = (width - 28 - 3 * 8) / 4
        font.setPixelSize(12)
        p.setFont(font)
        for position, (key, label) in enumerate(STEPS):
            rect = QRectF(14 + position * (pill_w + 8), top + 36, pill_w, 28)
            done = tile is not None and key in tile.done_steps and key != tile.current
            current = tile is not None and tile.phase == RUNNING and key == tile.current
            path = QPainterPath()
            path.addRoundedRect(rect, 14, 14)
            if current:
                p.fillPath(path, colour("accent_soft"))
                p.setPen(QPen(colour("accent"), 2))
                text = "{} {:.0f} %".format(label, tile.fraction * 100)
                p.drawPath(path)
                p.setPen(colour("accent"))
            elif done:
                p.fillPath(path, colour("success", 0.15))
                p.setPen(colour("success"))
                text = "✓ " + label
            else:
                p.setPen(QPen(colour("border"), 1))
                p.drawPath(path)
                p.setPen(colour("text_muted"))
                text = label
            p.drawText(rect, Qt.AlignCenter, text)

    # ------------------------------------------------------- значки и фигуры

    def _draw_document(self, p, rect, label, fill, text_colour):
        fold = rect.width() * 0.28
        left, top, right, bottom = rect.left() + rect.width() * 0.12, rect.top(), rect.right() - rect.width() * 0.12, rect.bottom()
        path = QPainterPath()
        path.moveTo(left, top + 3)
        path.lineTo(right - fold, top)
        path.lineTo(right, top + fold)
        path.lineTo(right, bottom - 3)
        path.lineTo(left, bottom - 3)
        path.closeSubpath()
        p.fillPath(path, fill)
        corner = QPainterPath()
        corner.moveTo(right - fold, top)
        corner.lineTo(right - fold, top + fold)
        corner.lineTo(right, top + fold)
        corner.closeSubpath()
        p.fillPath(corner, QColor(255, 255, 255, 90))
        font = QFont(self.font())
        font.setPixelSize(max(8, int(rect.width() * 0.26)))
        font.setBold(True)
        p.setFont(font)
        p.setPen(text_colour)
        p.drawText(QRectF(left, top + rect.height() * 0.36, right - left, rect.height() * 0.4), Qt.AlignCenter, label)

    def _draw_folder(self, p, rect, openness, fill):
        """Папка: задняя стенка с ушком и передняя, которая откидывается при открытии."""
        back = QColor(fill)
        back.setAlphaF(0.55)
        w, h = rect.width(), rect.height()
        tab = QPainterPath()
        tab.addRoundedRect(QRectF(rect.left(), rect.top(), w * 0.4, h * 0.3), 3, 3)
        p.fillPath(tab, back)
        body = QPainterPath()
        body.addRoundedRect(QRectF(rect.left(), rect.top() + h * 0.14, w, h * 0.86), 4, 4)
        p.fillPath(body, back)
        lift = h * 0.20 * openness
        skew = w * 0.10 * openness
        front = QPolygonF([QPointF(rect.left() + skew, rect.top() + h * 0.38 - lift),
                           QPointF(rect.right() + skew * 0.4, rect.top() + h * 0.38 - lift),
                           QPointF(rect.right(), rect.bottom()), QPointF(rect.left(), rect.bottom())])
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawPolygon(front)
        p.setBrush(Qt.NoBrush)

    def _paint_badge(self, p, tile, centre, now):
        """Значок шага над файлом: что именно сейчас делается, и кольцо прогресса."""
        colour = self.palette_of
        radius = 12
        p.setPen(Qt.NoPen)
        p.setBrush(colour("accent"))
        p.drawEllipse(centre, radius, radius)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(colour("accent"), 2))
        ring = QRectF(centre.x() - radius - 3, centre.y() - radius - 3, 2 * radius + 6, 2 * radius + 6)
        p.setPen(QPen(colour("accent", 0.25), 2))
        p.drawEllipse(ring)
        p.setPen(QPen(colour("accent"), 2))
        p.drawArc(ring, 90 * 16, -int(clamp01(tile.fraction) * 360 * 16))
        pen = QPen(colour("on_accent"), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        r = radius * 0.55
        x, y = centre.x(), centre.y()
        key = tile.current
        if key == states.STAGED:                      # лупа: читаем оригинал
            p.drawEllipse(QPointF(x - r * 0.2, y - r * 0.2), r * 0.6, r * 0.6)
            p.drawLine(QPointF(x + r * 0.25, y + r * 0.25), QPointF(x + r * 0.9, y + r * 0.9))
        elif key == "convert":                        # шестерёнка: вращающиеся зубцы
            p.drawEllipse(QPointF(x, y), r * 0.45, r * 0.45)
            for spoke in range(8):
                angle = now * 3 + spoke * math.pi / 4
                p.drawLine(QPointF(x + math.cos(angle) * r * 0.6, y + math.sin(angle) * r * 0.6),
                           QPointF(x + math.cos(angle) * r * 0.95, y + math.sin(angle) * r * 0.95))
        elif key == states.JPEG_UPLOADED:             # стрелка вверх: записываем JPEG
            p.drawLine(QPointF(x, y + r * 0.8), QPointF(x, y - r * 0.8))
            p.drawLine(QPointF(x - r * 0.6, y - r * 0.2), QPointF(x, y - r * 0.8))
            p.drawLine(QPointF(x + r * 0.6, y - r * 0.2), QPointF(x, y - r * 0.8))
        elif key == states.SOURCE_COPIED:             # стрелка вправо: переносим в архив
            p.drawLine(QPointF(x - r * 0.8, y), QPointF(x + r * 0.8, y))
            p.drawLine(QPointF(x + r * 0.2, y - r * 0.6), QPointF(x + r * 0.8, y))
            p.drawLine(QPointF(x + r * 0.2, y + r * 0.6), QPointF(x + r * 0.8, y))

    def _paint_check(self, p, centre, fill, age):
        grow = ease_out_back(age / 0.3)
        radius = 11 * grow
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawEllipse(centre, radius, radius)
        p.setBrush(Qt.NoBrush)
        pen = QPen(QColor(255, 255, 255), 2)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(centre.x() - radius * 0.45, centre.y()), QPointF(centre.x() - radius * 0.1, centre.y() + radius * 0.4))
        p.drawLine(QPointF(centre.x() - radius * 0.1, centre.y() + radius * 0.4), QPointF(centre.x() + radius * 0.5, centre.y() - radius * 0.35))


class FileFlow(QFrame):
    """Правая часть окна: анимированная сцена + выключатель. Выключенная ничего не рисует и не считает."""

    def __init__(self, enabled=True, on_toggle=None, parent=None):
        super().__init__(parent)
        self.setObjectName("flowPanel")
        self._on_toggle = on_toggle
        self._plans = {}
        self._clock = time.monotonic
        self._queue = []
        self._hold_until = 0.0
        self._drain_timer = QTimer(self)
        self._drain_timer.setSingleShot(True)
        self._drain_timer.timeout.connect(self._drain)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        header = QHBoxLayout()
        self.title = QLabel("Ход обработки")
        self.title.setProperty("role", "title")
        header.addWidget(self.title, 1)
        self.toggle = QCheckBox("Показывать ход по файлам")
        self.toggle.setChecked(enabled)
        self.toggle.toggled.connect(self._toggled)
        header.addWidget(self.toggle)
        column.addLayout(header)
        self.scene = Scene()
        self.scene.set_enabled(enabled)
        column.addWidget(self.scene, 1)

    # ------------------------------------------------------------------ API

    @property
    def enabled(self):
        return self.toggle.isChecked()

    @property
    def rows(self):
        """Плитки текущей папки по номеру файла (для проверок)."""
        return self.scene.tiles

    def refresh_theme(self, theme):
        self.scene.palette_of = Palette(theme)
        self.scene.update()

    def set_plans(self, plans):
        """Планы после «Проверить» / начала обработки: из них берутся имена «было → стало»."""
        self._plans = {plan.folder: plan for plan in plans}

    # События сервиса идут через очередь: итог закончившейся папки (галочки, закрытие) держится HOLD_S,
    # и только потом открывается следующая — иначе пачка фазы B и «закрытие» исчезали бы мгновенно.

    def folder_started(self, folder):
        self._dispatch(self._folder_started, folder)

    def file_started(self, index):
        self._dispatch(self._file_started, index)

    def stage(self, index, stage, fraction):
        self._dispatch(self._stage, index, stage, fraction)

    def file_prepared(self, index):
        self._dispatch(self._file_prepared, index)

    def file_completed(self, index):
        self._dispatch(self._file_completed, index)

    def folder_finished(self):
        self._dispatch(self._folder_finished)

    def _dispatch(self, handler, *args):
        self._queue.append((handler, args))
        self._drain()

    def _drain(self):
        while self._queue:
            wait = self._hold_until - self._clock()
            if wait > 0:
                self._drain_timer.start(int(wait * 1000) + 10)
                return
            handler, args = self._queue.pop(0)
            handler(*args)

    def _folder_started(self, folder):
        self.scene.reset()
        plan = self._plans.get(folder)
        if not self.enabled or plan is None:
            return
        name = os.path.basename(os.path.normpath(folder)) or folder
        self.scene.open_folder(name, plan.files)

    def _file_started(self, index):
        tile = self.scene.tiles.get(index)
        if tile is not None:
            tile.start()
            self.scene.touch(index)

    def _stage(self, index, stage, fraction):
        tile = self.scene.tiles.get(index)
        if tile is not None:
            tile.stage(stage, fraction)
            self.scene.touch(index, fly=stage == states.SOURCE_COPIED and fraction >= 1.0)

    def _file_prepared(self, index):
        tile = self.scene.tiles.get(index)
        if tile is not None:
            tile.prepared()
            self.scene.touch(index)

    def _file_completed(self, index):
        tile = self.scene.tiles.get(index)
        if tile is not None:
            tile.completed()
            self.scene.touch(index, done=True)

    def _folder_finished(self):
        """Папка закончена (в том числе с ошибкой): незавершённые файлы не выдаём за готовые."""
        for tile in self.scene.order:
            if tile.phase == RUNNING:
                tile.phase = WAITING
                tile.current = None
        if self.scene.order:
            self.scene.folder_closed()
            self._hold_until = self._clock() + HOLD_S

    def clear(self):
        self._queue = []
        self._hold_until = 0.0
        self.scene.reset()

    # -------------------------------------------------------------- частное

    def _toggled(self, checked):
        self.scene.set_enabled(checked)
        if not checked:
            self._queue = []
            self.scene.reset()
        if self._on_toggle is not None:
            self._on_toggle(checked)
