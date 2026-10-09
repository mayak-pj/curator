#!/usr/bin/env python3
"""Проверка PE-файлов (exe/dll/pyd) на совместимость с Windows 7 x64.

    python scripts/check_pe_imports.py dist/Curator [--report report.txt]

Эвристика, а не гарантия (окончательная проверка — запуск на Win7):
  * каждая импортируемая DLL должна лежать в сборке или быть системной DLL Windows 7;
  * наборы api-ms-win-core-* новее l1-1-0 на Windows 7 отсутствуют;
  * известные функции, появившиеся в Windows 8+.
Код возврата 1, если найдены проблемы.
"""

import argparse
import os
import re
import sys

import pefile

# Системные DLL, присутствующие в Windows 7 SP1 x64 (без дополнительных компонентов).
WIN7_SYSTEM_DLLS = {
    "advapi32.dll", "bcrypt.dll", "bcryptprimitives.dll", "cfgmgr32.dll", "comctl32.dll", "comdlg32.dll", "crypt32.dll",
    "cryptbase.dll", "d2d1.dll", "d3d9.dll", "d3d10.dll", "d3d10_1.dll", "d3d11.dll", "dbghelp.dll",
    "dnsapi.dll", "dwmapi.dll", "dwrite.dll", "dxgi.dll", "gdi32.dll", "gdiplus.dll", "imm32.dll",
    "iphlpapi.dll", "kernel32.dll", "kernelbase.dll", "mpr.dll", "msimg32.dll", "msvcrt.dll",
    "mswsock.dll", "ncrypt.dll", "netapi32.dll", "normaliz.dll", "ntdll.dll", "ole32.dll",
    "oleaut32.dll", "opengl32.dll", "powrprof.dll", "psapi.dll", "rpcrt4.dll", "secur32.dll",
    "setupapi.dll", "shell32.dll", "shlwapi.dll", "urlmon.dll", "user32.dll", "userenv.dll",
    "usp10.dll", "uxtheme.dll", "version.dll", "winhttp.dll", "wininet.dll", "winmm.dll",
    "winspool.drv", "wintrust.dll", "wldap32.dll", "ws2_32.dll", "wsock32.dll", "wtsapi32.dll",
    # UCRT: на целевом ПК есть ucrtbase.dll (KB2999226).
    "ucrtbase.dll",
}
API_SET_WIN7 = re.compile(r"^api-ms-win-core-[a-z0-9-]+-l1-1-0\.dll$")
API_SET_CRT = re.compile(r"^api-ms-win-crt-[a-z0-9-]+-l1-1-0\.dll$")

# Функции Windows 8+ (неполный список наиболее частых в современных тулчейнах).
POST_WIN7_FUNCTIONS = {
    "GetSystemTimePreciseAsFileTime": "Windows 8",
    "CreateFile2": "Windows 8",
    "CopyFile2": "Windows 8",
    "GetCurrentThreadStackLimits": "Windows 8",
    "PrefetchVirtualMemory": "Windows 8",
    "SetThreadInformation": "Windows 8",
    "GetThreadInformation": "Windows 8",
    "SetProcessMitigationPolicy": "Windows 8",
    "GetProcessMitigationPolicy": "Windows 8",
    "WaitOnAddress": "Windows 8",
    "WakeByAddressSingle": "Windows 8",
    "WakeByAddressAll": "Windows 8",
    "GetOverlappedResultEx": "Windows 8",
    "CreateFileMappingFromApp": "Windows 8",
    "MapViewOfFileFromApp": "Windows 8",
    "ResolveDelayLoadedAPI": "Windows 8",
    "GetPackageFamilyName": "Windows 8",
    "GetCurrentPackageId": "Windows 8",
    "DiscardVirtualMemory": "Windows 8.1",
    "OfferVirtualMemory": "Windows 8.1",
    "GetDpiForMonitor": "Windows 8.1",
    "SetProcessDpiAwareness": "Windows 8.1",
    "SetThreadDescription": "Windows 10",
    "GetThreadDescription": "Windows 10",
    "GetSystemCpuSetInformation": "Windows 10",
    "SetThreadSelectedCpuSets": "Windows 10",
    "VirtualAlloc2": "Windows 10",
    "MapViewOfFile3": "Windows 10",
    "ProcessPrng": "Windows 10",
    "GetDpiForWindow": "Windows 10",
    "SetProcessDpiAwarenessContext": "Windows 10",
    "GetTempPath2W": "Windows 11",
}

PE_EXTENSIONS = (".exe", ".dll", ".pyd")


def iter_pe_files(root):
    if os.path.isfile(root):
        yield root
        return
    for directory, _, files in os.walk(root):
        for name in sorted(files):
            if name.lower().endswith(PE_EXTENSIONS):
                yield os.path.join(directory, name)


def read_imports(path):
    pe = pefile.PE(path, fast_load=True)
    pe.parse_data_directories(directories=[
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
    ])
    imports = {}  # dll -> (имена функций, только отложенный импорт)
    for attribute, delayed in (("DIRECTORY_ENTRY_IMPORT", False), ("DIRECTORY_ENTRY_DELAY_IMPORT", True)):
        for entry in getattr(pe, attribute, []):
            dll = entry.dll.decode("ascii", "replace").lower()
            names, was_delayed = imports.get(dll, (set(), True))
            for symbol in entry.imports:
                if symbol.name:
                    names.add(symbol.name.decode("ascii", "replace"))
            imports[dll] = (names, was_delayed and delayed)
    info = {
        "machine": pefile.MACHINE_TYPE.get(pe.FILE_HEADER.Machine, hex(pe.FILE_HEADER.Machine)),
        "subsystem_version": "{}.{}".format(
            pe.OPTIONAL_HEADER.MajorSubsystemVersion, pe.OPTIONAL_HEADER.MinorSubsystemVersion),
        "os_version": "{}.{}".format(
            pe.OPTIONAL_HEADER.MajorOperatingSystemVersion, pe.OPTIONAL_HEADER.MinorOperatingSystemVersion),
    }
    pe.close()
    return imports, info


def check(root):
    files = list(iter_pe_files(root))
    bundled = {os.path.basename(path).lower() for path in files}
    problems = []
    warnings = []
    lines = []
    for path in files:
        relative = os.path.relpath(path, root) if os.path.isdir(root) else os.path.basename(path)
        try:
            imports, info = read_imports(path)
        except pefile.PEFormatError as exc:
            problems.append("{}: не PE-файл ({})".format(relative, exc))
            continue
        lines.append("{}  [{}, subsystem {}, os {}]".format(
            relative, info["machine"], info["subsystem_version"], info["os_version"]))
        if info["machine"] != "IMAGE_FILE_MACHINE_AMD64":
            problems.append("{}: архитектура {}, ожидалась x64".format(relative, info["machine"]))
        major = int(info["subsystem_version"].split(".")[0])
        minor = int(info["subsystem_version"].split(".")[1])
        if (major, minor) > (6, 1):
            problems.append("{}: MajorSubsystemVersion {} > 6.1 — загрузчик Win7 откажется запускать".format(
                relative, info["subsystem_version"]))
        for dll in sorted(imports):
            functions, delayed = imports[dll]
            # Отложенный импорт падает только при вызове функции — предупреждение, а не ошибка.
            sink = warnings if delayed else problems
            if dll in bundled:
                origin = "в сборке"
            elif dll in WIN7_SYSTEM_DLLS or API_SET_CRT.match(dll):
                origin = "система Win7"
            elif API_SET_WIN7.match(dll):
                origin = "api-set Win7"
            elif dll.startswith("api-ms-win-"):
                origin = "НЕТ В WIN7 (api-set новее)"
                sink.append("{}: импортирует {} — такого набора API нет в Windows 7".format(relative, dll))
            else:
                origin = "НЕ НАЙДЕНА"
                sink.append("{}: импортирует {} — нет ни в сборке, ни в списке системных DLL Win7".format(
                    relative, dll))
            lines.append("    {:<45} {}{}".format(dll, origin, " (delay-load)" if delayed else ""))
            for function in sorted(functions):
                if function in POST_WIN7_FUNCTIONS:
                    sink.append("{}: {}!{} появилась в {}".format(
                        relative, dll, function, POST_WIN7_FUNCTIONS[function]))
    return files, lines, problems, warnings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root")
    parser.add_argument("--report")
    parser.add_argument("--verbose", action="store_true", help="печатать импорты каждого файла")
    args = parser.parse_args(argv)

    files, lines, problems, warnings = check(args.root)
    output = []
    if args.verbose or args.report:
        output.extend(lines)
        output.append("")
    output.append("Проверено PE-файлов: {}".format(len(files)))
    if warnings:
        output.append("ПРЕДУПРЕЖДЕНИЯ, отложенный импорт ({}):".format(len(warnings)))
        output.extend("  - " + warning for warning in warnings)
    if problems:
        output.append("ПРОБЛЕМЫ ({}):".format(len(problems)))
        output.extend("  - " + problem for problem in problems)
    else:
        output.append("Проблем не найдено.")
    text = "\n".join(output)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as stream:
            stream.write(text + "\n")
    print(text)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
