# Сборка

Собирает GitHub Actions: `.github/workflows/build-windows.yml`, запуск на каждый push в `main` и вручную. Результат — два артефакта: `curator-win7-x64.zip` (программа) и `curator_smoke-win7-x64.zip` (диагностика).

## Закреплённые версии

| Компонент | Версия | Почему именно она |
|---|---|---|
| Python | 3.8.10 x64 | последняя версия с официальной поддержкой Windows 7 |
| PyInstaller | 6.22.3 | поддерживает 3.8; загрузчик собирается под уровень Windows 7 |
| pyvips | 3.2.0 | работает в ABI-режиме с нашими DLL |
| cffi | 1.17.1 | последняя с готовым пакетом для 3.8 |
| libvips | **8.15.1**, сборка `vips-dev-w64-web` | см. ниже |

Версии зафиксированы в `requirements.txt` и `requirements-dev.txt`, libvips — в `scripts/fetch_libvips.py` вместе с контрольной суммой архива.

## Почему libvips 8.15.1

Современные Windows-сборки libvips собираются тулчейнами, которые Windows 7 уже не поддерживают. Проверка импортов (`scripts/check_pe_imports.py`) показала:

| Сборка | Результат |
|---|---|
| `pyvips-binary` 8.15.3 … 8.18.6 | ✗ `WaitOnAddress` (Windows 8), `ProcessPrng` (Windows 10) |
| `vips-dev-w64-web` 8.15.5 | ✗ те же функции в `librsvg-2-2.dll` (код на Rust) |
| `vips-dev-w64-web` 8.15.1 и старее | ✓ проблем не найдено |

**Обновлять libvips без повторной проверки нельзя.** Порядок обновления: поменять версию и контрольную сумму в `scripts/fetch_libvips.py`, прогнать `python scripts/check_pe_imports.py build/vips`, собрать и запустить smoke-сборку на настоящей Windows 7.

## Что делает workflow

1. Ставит Python 3.8.10 и зависимости.
2. Скачивает архив libvips, сверяет sha256, распаковывает DLL, лицензию и список версий компонентов.
3. Проверяет импорты DLL на совместимость с Windows 7.
4. Прогоняет тесты (`pytest`) на Windows.
5. Собирает `curator.exe` и `curator_smoke.exe` (onedir, без UPX, с ресурсом версии).
6. Проверяет импорты собранных папок — шаг блокирующий, при проблемах сборка падает.
7. Прогоняет собранное приложение на сгенерированном дереве папок: обработка, сверка результата по контрольным суммам, откат и сверка возврата в исходное состояние.
8. Кладёт в архив документацию, пример конфигурации и лицензии.

UCRT (`ucrtbase.dll`, `api-ms-win-*.dll`) **намеренно исключён** из сборки: PyInstaller копирует его со сборочной машины (Windows Server 2022), и на Windows 7 такие копии перекрыли бы системный UCRT. Используется тот, что стоит на целевом ПК.

## Локальная сборка на Windows

```bat
python -m pip install -r requirements-dev.txt
python scripts\fetch_libvips.py --dest build\vips
set CURATOR_VIPS_DIR=%CD%\build\vips
python -m pytest -q
python -m PyInstaller packaging\curator.spec --noconfirm --distpath dist --workpath build\pyinstaller
python scripts\check_pe_imports.py dist\curator
```

## Разработка на macOS

```bash
brew install vips uv
uv venv --python 3.8 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python -m curator scan /путь/к/папке
```

Windows-специфичное (сборка exe, длинные пути, UNC-нормализация) на macOS не проверяется — для этого есть CI и целевой ПК.

## Консольные команды

```
python -m curator                       окно программы
python -m curator scan ROOT             сухой прогон, ничего не меняет
python -m curator process ROOT          обработка (ключ --rollback-all откатывает сразу)
python -m curator convert SRC DST       один файл
python -m curator recover               незавершённые операции прошлых запусков
```

Собранный `curator.exe` принимает те же команды. Он собран как оконное приложение, поэтому вывод в консоль не печатается — смотрите логи в папке программы.
