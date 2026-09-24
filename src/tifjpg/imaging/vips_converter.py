"""TIFF -> JPEG через libvips с потоковым чтением (ARCHITECTURE.md, раздел 8).

Изображение целиком в память не загружается:
  проход 1 (если нужен тон-маппингу) — гистограмма по всему снимку;
  проход 2 — LUT 16->8 бит и запись baseline JPEG полосами.
"""

import errno
import os
import time
from array import array

from tifjpg.domain.errors import ConversionCancelled, FileProcessingError, UnsupportedImageError
from tifjpg.imaging.base import ConversionResult, ImageConverter
from tifjpg.imaging.tiff_tags import read_tiff_header
from tifjpg.imaging.tone import build_lut, window_from_histogram
from tifjpg.imaging.vips_runtime import DEFAULT_CONCURRENCY, at_least, load_pyvips

JPEG_MAX_DIMENSION = 65500
STAGE_HISTOGRAM = "histogram"
STAGE_ENCODE = "encode"

_FORMAT_MAX = {"uchar": 255, "ushort": 65535}
_SUPPORTED_BANDS = (1, 3)
_HISTOGRAM_SHARE = 0.4  # доля прогресса на проход гистограммы


class VipsTiffJpegConverter(ImageConverter):
    name = "vips-tiff-jpeg"
    extensions = (".tif", ".tiff")

    def __init__(self, concurrency=DEFAULT_CONCURRENCY):
        self._concurrency = concurrency

    def convert(self, source, destination, options, progress=None, is_cancelled=None):
        vips = load_pyvips(self._concurrency)
        started = time.monotonic()
        if os.path.lexists(destination):
            raise FileExistsError(errno.EEXIST, "файл назначения уже существует", destination)

        header = read_tiff_header(source)
        warnings = []
        if header.page_count > 1:
            warnings.append(
                "многостраничный TIFF ({} стр.): конвертируется первая страница".format(header.page_count)
            )
        if header.tag(274, 1) != 1:
            warnings.append("тег Orientation = {} не применяется".format(header.tag(274)))

        tracker = _ProgressTracker(progress, is_cancelled)
        destination_created = False
        try:
            image = self._open(vips, source)
            _check_supported(source, image)
            source_format = image.format
            window = (0, _FORMAT_MAX[source_format])
            tone = "none (8-bit source)"
            encode_start = 0.0
            output = image

            if source_format == "ushort":
                tone = options.tone.describe()
                if options.tone.needs_histogram:
                    tracker.watch(image, STAGE_HISTOGRAM, 0.0, _HISTOGRAM_SHARE)
                    counts = read_histogram(image)
                    tracker.raise_if_cancelled(source)
                    window = window_from_histogram(counts, options.tone)
                    encode_start = _HISTOGRAM_SHARE
                    # Последовательный доступ не перематывается: второй проход — новое чтение.
                    image = self._open(vips, source)
                lut = vips.Image.new_from_memory(build_lut(*window), 65536, 1, 1, "uchar")
                output = image.maplut(lut)

            # Без явной интерпретации jpegsave пересчитал бы grey16/rgb16 ещё раз.
            output = output.copy(interpretation="b-w" if output.bands == 1 else "srgb")
            tracker.watch(output, STAGE_ENCODE, encode_start, 1.0 - encode_start)
            destination_created = True
            output.jpegsave(destination, Q=options.quality, optimize_coding=False, interlace=False)
        except vips.Error as exc:
            if destination_created:
                _remove_quietly(destination)
            if tracker.cancelled:
                raise ConversionCancelled(source) from None
            raise FileProcessingError(source, "ошибка libvips: {}".format(_short_error(exc))) from exc
        except BaseException:
            if destination_created:
                _remove_quietly(destination)
            raise

        tracker.report(STAGE_ENCODE, 1.0)
        return ConversionResult(
            source=source,
            destination=destination,
            converter=self.name,
            width=image.width,
            height=image.height,
            bands=image.bands,
            source_format=source_format,
            photometric=header.photometric,
            compression=header.compression,
            tone=tone,
            window=window,
            quality=options.quality,
            output_bytes=os.path.getsize(destination),
            seconds=round(time.monotonic() - started, 3),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _open(vips, source):
        kwargs = {"access": "sequential"}
        if at_least(vips, 8, 12):
            kwargs["fail_on"] = "error"
        else:
            kwargs["fail"] = True
        return vips.Image.tiffload(source, **kwargs)


def read_histogram(image):
    """Гистограмма значений (все каналы суммарно) — один последовательный проход."""
    hist = image.hist_find()
    if hist.format != "uint":
        hist = hist.cast("uint")
    data = array("I")
    if data.itemsize != 4:
        data = array("L")
    data.frombytes(hist.write_to_memory())
    bands = hist.bands
    if bands == 1:
        return data
    return [sum(data[index * bands:(index + 1) * bands]) for index in range(hist.width)]


def _check_supported(source, image):
    if image.format not in _FORMAT_MAX:
        raise UnsupportedImageError(source, "формат пикселей {} не поддерживается".format(image.format))
    if image.bands not in _SUPPORTED_BANDS:
        raise UnsupportedImageError(
            source, "{} канал(ов) не поддерживается (нужно 1 или 3, без альфа-канала)".format(image.bands)
        )
    if image.width > JPEG_MAX_DIMENSION or image.height > JPEG_MAX_DIMENSION:
        raise UnsupportedImageError(
            source, "размер {}x{} превышает предел JPEG {}".format(image.width, image.height, JPEG_MAX_DIMENSION)
        )


class _ProgressTracker:
    """Прогресс и отмена через сигнал libvips "eval".

    Исключения внутри обратного вызова cffi не пробрасываются, поэтому
    отмена выставляет kill у изображения, а решение принимается после
    ошибки libvips.
    """

    def __init__(self, callback, is_cancelled):
        self._callback = callback
        self._is_cancelled = is_cancelled
        self._last = -1.0
        self.cancelled = False

    def watch(self, image, stage, start, span):
        if self._callback is None and self._is_cancelled is None:
            return

        def on_eval(target, status):
            if self._is_cancelled is not None and self._is_cancelled():
                self.cancelled = True
                target.set_kill(True)
                return
            self.report(stage, start + span * status.percent / 100.0)

        image.set_progress(True)
        image.signal_connect("eval", on_eval)

    def report(self, stage, fraction):
        if self._callback is None:
            return
        fraction = min(max(fraction, 0.0), 1.0)
        if fraction - self._last >= 0.01 or (fraction >= 1.0 > self._last):
            self._last = fraction
            self._callback(stage, fraction)

    def raise_if_cancelled(self, source):
        if self.cancelled or (self._is_cancelled is not None and self._is_cancelled()):
            self.cancelled = True
            raise ConversionCancelled(source)


def _remove_quietly(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _short_error(exc):
    text = str(exc).strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(lines[:3]) if lines else exc.__class__.__name__
