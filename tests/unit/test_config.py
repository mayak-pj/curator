import json

import pytest

from tifjpg.config import ConfigError, Settings, from_dict, load


def test_defaults_match_decision_d12():
    settings = Settings()
    assert settings.tone_method == "linear"
    assert settings.jpeg_quality == 100
    assert settings.conversion_options().tone.method == "linear"
    assert settings.folder_rules == "default_v1"


def test_missing_file_gives_defaults(tmp_path):
    assert load(str(tmp_path / "нет.json")) == Settings()


def test_save_and_load_roundtrip(tmp_path):
    path = str(tmp_path / "config.json")
    settings = Settings(jpeg_quality=95, tone_method="percentile", tone_exclude_extremes=True,
                        log_level="DEBUG", network_retry_delays_s=(1, 2))
    settings.save(path)

    assert load(path) == settings
    stored = json.loads(open(path, encoding="utf-8").read())
    assert stored["tone"] == {"method": "percentile", "p_low": 0.5, "p_high": 99.5, "exclude_extremes": True}


def test_broken_file_is_reported(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{не json", encoding="utf-8")
    with pytest.raises(ConfigError) as error:
        load(str(path))
    assert "повреждён" in str(error.value)


@pytest.mark.parametrize("data, expected", [
    ({"jpeg_quality": 0}, "jpeg_quality"),
    ({"tone": {"method": "гамма"}}, "tone.method"),
    ({"folder_rules": "нет такого"}, "folder_rules"),
    ({"log_level": "TRACE"}, "log_level"),
    ({"lock_stale_minutes": 0}, "lock_stale_minutes"),
    ({"network_retry_delays_s": [-1]}, "паузы"),
    ({"неизвестно": 1}, "неизвестные настройки"),
])
def test_invalid_values_are_explained(data, expected):
    with pytest.raises(ConfigError) as error:
        from_dict(data)
    assert expected in str(error.value)


def test_settings_are_immutable():
    with pytest.raises(Exception):
        Settings().jpeg_quality = 50
