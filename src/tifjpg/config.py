"""Настройки приложения (ARCHITECTURE.md, раздел 16).

config.json лежит рядом с exe и правится вручную. Отсутствующий файл —
значения по умолчанию; испорченный файл — понятная ошибка при запуске, а
не молчаливый сброс настроек.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Tuple

from tifjpg.domain import folder_rules
from tifjpg.domain.errors import TifJpgError
from tifjpg.imaging.base import ConversionOptions
from tifjpg.imaging.tone import METHODS, ToneSpec


class ConfigError(TifJpgError):
    pass


@dataclass(frozen=True)
class Settings:
    jpeg_quality: int = 100
    tone_method: str = "linear"           # решение D12
    tone_p_low: float = 0.5
    tone_p_high: float = 99.5
    tone_exclude_extremes: bool = False
    folder_rules: str = folder_rules.DEFAULT_NAME
    vips_concurrency: int = 2
    network_retry_delays_s: Tuple[int, ...] = (5, 15, 30, 60, 120)
    log_level: str = "INFO"
    lock_stale_minutes: int = 10
    check_free_space: bool = True

    def __post_init__(self):
        if not 1 <= self.jpeg_quality <= 100:
            raise ConfigError("jpeg_quality должен быть от 1 до 100, получено {}".format(self.jpeg_quality))
        if self.tone_method not in METHODS:
            raise ConfigError("tone.method: ожидалось одно из {}, получено {!r}".format(
                ", ".join(METHODS), self.tone_method))
        if self.folder_rules not in folder_rules.available():
            raise ConfigError("folder_rules: неизвестный набор {!r}; известные: {}".format(
                self.folder_rules, ", ".join(folder_rules.available())))
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ConfigError("log_level: ожидалось DEBUG, INFO, WARNING или ERROR, получено {!r}".format(
                self.log_level))
        if any(delay < 0 for delay in self.network_retry_delays_s):
            raise ConfigError("network_retry_delays_s: паузы не могут быть отрицательными")
        if self.lock_stale_minutes <= 0:
            raise ConfigError("lock_stale_minutes должен быть больше нуля")

    # ------------------------------------------------------------------ вывод

    def tone(self):
        return ToneSpec(self.tone_method, p_low=self.tone_p_low, p_high=self.tone_p_high,
                        exclude_extremes=self.tone_exclude_extremes)

    def conversion_options(self):
        return ConversionOptions(tone=self.tone(), quality=self.jpeg_quality)

    def to_dict(self):
        data = asdict(self)
        return {
            "jpeg_quality": data["jpeg_quality"],
            "tone": {
                "method": data["tone_method"],
                "p_low": data["tone_p_low"],
                "p_high": data["tone_p_high"],
                "exclude_extremes": data["tone_exclude_extremes"],
            },
            "folder_rules": data["folder_rules"],
            "vips_concurrency": data["vips_concurrency"],
            "network_retry_delays_s": list(data["network_retry_delays_s"]),
            "log_level": data["log_level"],
            "lock_stale_minutes": data["lock_stale_minutes"],
            "check_free_space": data["check_free_space"],
        }

    def save(self, path):
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(self.to_dict(), stream, ensure_ascii=False, indent=2)
        return path


def from_dict(data):
    if not isinstance(data, dict):
        raise ConfigError("ожидался объект JSON с настройками")
    tone = data.get("tone", {})
    if not isinstance(tone, dict):
        raise ConfigError("раздел tone должен быть объектом")
    unknown = set(data) - {"jpeg_quality", "tone", "folder_rules", "vips_concurrency",
                           "network_retry_delays_s", "log_level", "lock_stale_minutes",
                           "check_free_space", "naming"}
    if unknown:
        raise ConfigError("неизвестные настройки: {}".format(", ".join(sorted(unknown))))
    defaults = Settings()
    try:
        return Settings(
            jpeg_quality=int(data.get("jpeg_quality", defaults.jpeg_quality)),
            tone_method=str(tone.get("method", defaults.tone_method)),
            tone_p_low=float(tone.get("p_low", defaults.tone_p_low)),
            tone_p_high=float(tone.get("p_high", defaults.tone_p_high)),
            tone_exclude_extremes=bool(tone.get("exclude_extremes", defaults.tone_exclude_extremes)),
            folder_rules=str(data.get("folder_rules", defaults.folder_rules)),
            vips_concurrency=int(data.get("vips_concurrency", defaults.vips_concurrency)),
            network_retry_delays_s=tuple(data.get("network_retry_delays_s", defaults.network_retry_delays_s)),
            log_level=str(data.get("log_level", defaults.log_level)).upper(),
            lock_stale_minutes=int(data.get("lock_stale_minutes", defaults.lock_stale_minutes)),
            check_free_space=bool(data.get("check_free_space", defaults.check_free_space)),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError("не удалось разобрать настройки: {}".format(exc)) from exc


def load(path):
    """Настройки из файла. Нет файла — значения по умолчанию."""
    if not os.path.exists(path):
        return Settings()
    try:
        with open(path, encoding="utf-8-sig") as stream:
            data = json.load(stream)
    except ValueError as exc:
        raise ConfigError("{}: файл повреждён ({})".format(path, exc)) from exc
    except OSError as exc:
        raise ConfigError("{}: не удалось прочитать ({})".format(path, exc)) from exc
    return from_dict(data)
