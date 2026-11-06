"""
check_functions.py — проверка целостности проекта.

Сканирует все .py файлы проекта (app.py, config.py, core/, ui/, tabs/)
и проверяет:

    MISSING       — имена, использованные в Load-контексте, но не определённые
                    ни в этом файле (def / class / локальная переменная /
                    параметр функции), ни через import, ни builtin.
                    Это и есть причина NameError.

    DUPLICATES    — функции с более чем одним `def` в ОДНОМ файле.
                    Python возьмёт последнее определение — редактировать
                    такой файл опасно.

    UNUSED        — функции, определённые в проекте, но не использованные
                    ни в одном файле (потенциальный dead code).
                    Колбэки Streamlit (on_change=...) попадают сюда ложно —
                    они передаются как объект, а не вызываются.

Использование:

    python check_functions.py              # авто-сканирование всего проекта
    python check_functions.py app.py       # проверка одного файла
    python check_functions.py /path/to/    # указать корень проекта

Exit code:

    0 — файл готов к запуску
    1 — есть MISSING или DUPLICATES (Streamlit упадёт на вкладке)
    2 — фатальная ошибка (файл не найден, синтаксическая ошибка)

Зависимости: только стандартная библиотека (ast, sys, collections, pathlib).
"""
from __future__ import annotations

import ast
import sys
import collections
from pathlib import Path


# ==================================================================
# Настройки
# ==================================================================
DEFAULT_ROOT = "."

IGNORE_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "env",
    ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
    "node_modules", ".streamlit",
}

IGNORE_FILES = {
    "check_functions.py",  # не проверяем сам себя
    "setup.py",
}

# Встроенные имена Python и special module attributes.
BUILTINS = {
    # базовые
    "print", "len", "range", "int", "float", "str", "bool", "dict", "list",
    "set", "tuple", "bytes", "bytearray", "frozenset", "complex",
    "min", "max", "abs", "round", "sum", "any", "all", "pow", "divmod",
    "enumerate", "zip", "sorted", "reversed", "iter", "next", "map", "filter",
    # типы / интроспекция
    "type", "isinstance", "issubclass", "getattr", "setattr", "hasattr",
    "delattr", "callable", "id", "hash", "vars", "dir", "object",
    "super", "property", "staticmethod", "classmethod",
    # I/O и утилиты
    "open", "repr", "format", "input", "eval", "exec", "compile",
    "globals", "locals", "help", "exit", "quit", "breakpoint",
    # исключения
    "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError",
    "NameError", "RuntimeError", "Exception", "BaseException",
    "StopIteration", "NotImplementedError", "ImportError", "OSError",
    "IOError", "ZeroDivisionError", "AssertionError", "UnicodeError",
    "UnicodeDecodeError", "UnicodeEncodeError", "PermissionError",
    "FileNotFoundError", "IsADirectoryError", "NotADirectoryError",
    "ConnectionError", "TimeoutError", "ArithmeticError",
    "OverflowError", "LookupError", "SystemExit", "KeyboardInterrupt",
    # константы / special
    "True", "False", "None", "NotImplemented", "Ellipsis",
    "__file__", "__name__", "__doc__", "__builtins__",
    "__spec__", "__loader__", "__package__",
}


# ==================================================================
# Разбор одного файла
# ==================================================================
def parse_file(path: Path) -> dict:
    """Возвращает словарь с информацией о файле:
        defined      — имена def / class, определённые в этом файле
        imported     — имена, импортированные в этот файл
        used         — имена в Load-контексте (для MISSING и UNUSED)
        called       — имена в Call-контексте (для отчёта)
        assigned     — имена в Store-контексте (локальные переменные)
        params       — параметры всех функций файла
        class_methods— методы классов (исключаются из UNUSED)
        defs_count   — Counter имён def (для DUPLICATES)
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef)):
            defined.add(node.name)

    used: set[str] = set()
    assigned: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                used.add(node.id)
            elif isinstance(node.ctx, ast.Store):
                assigned.add(node.id)

    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)

    imported: set[str] = set()
    has_star_import = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    has_star_import = True
                    continue
                imported.add(alias.asname or alias.name)

    params: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in node.args.args:
                params.add(arg.arg)
            for arg in node.args.kwonlyargs:
                params.add(arg.arg)
            for arg in getattr(node.args, "posonlyargs", []):
                params.add(arg.arg)
            if node.args.vararg:
                params.add(node.args.vararg.arg)
            if node.args.kwarg:
                params.add(node.args.kwarg.arg)

    class_methods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    class_methods.add(item.name)

    counts = collections.Counter(
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )

    return {
        "defined": defined,
        "imported": imported,
        "used": used,
        "called": called,
        "assigned": assigned,
        "params": params,
        "class_methods": class_methods,
        "defs_count": counts,
        "has_star_import": has_star_import,
        "source_lines": len(source.splitlines()),
    }


# ==================================================================
# Сканирование
# ==================================================================
def find_python_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    files: list[Path] = []
    for p in root.rglob("*.py"):
        if any(part in IGNORE_DIRS for part in p.parts):
            continue
        if p.name in IGNORE_FILES:
            continue
        files.append(p)
    return sorted(files)


def analyze(project_root: Path) -> dict:
    files = find_python_files(project_root)
    if not files:
        return {"error": "не найдено .py файлов"}

    parsed: dict[Path, dict] = {}
    for f in files:
        try:
            parsed[f] = parse_file(f)
        except SyntaxError as e:
            return {
                "error": f"синтаксическая ошибка в {f}: "
                         f"строка {e.lineno}: {e.msg}",
                "file": f,
            }

    project_defined: set[str] = set()
    for info in parsed.values():
        project_defined |= info["defined"]

    # MISSING per file
    missing_by_file: dict[Path, set[str]] = {}
    for f, info in parsed.items():
        # Если в файле `from x import *`, не можем проверить MISSING.
        if info["has_star_import"]:
            continue
        candidates = (
            info["used"]
            - info["defined"]
            - info["imported"]
            - info["assigned"]
            - info["params"]
            - BUILTINS
        )
        if candidates:
            missing_by_file[f] = candidates

    # DUPLICATES per file
    duplicates_by_file: dict[Path, dict[str, int]] = {}
    for f, info in parsed.items():
        dups = {name: n for name, n in info["defs_count"].items() if n > 1}
        if dups:
            duplicates_by_file[f] = dups

    # UNUSED globally
    all_used: set[str] = set()
    all_class_methods: set[str] = set()
    for info in parsed.values():
        all_used |= info["used"]
        all_class_methods |= info["class_methods"]
    unused = sorted(project_defined - all_used - all_class_methods)

    return {
        "files": files,
        "parsed": parsed,
        "missing_by_file": missing_by_file,
        "duplicates_by_file": duplicates_by_file,
        "unused": unused,
        "project_defined": project_defined,
    }


# ==================================================================
# Отчёт
# ==================================================================
def print_report(result: dict) -> bool:
    if "error" in result:
        print(f"ОШИБКА: {result['error']}")
        return True

    print(f"Файлов просканировано: {len(result['files'])}")
    print(f"Функций/классов в проекте: {len(result['project_defined'])}")
    print()

    missing_by_file = result["missing_by_file"]
    duplicates_by_file = result["duplicates_by_file"]

    print("MISSING (used, но не определено/импортировано/builtin):")
    if not missing_by_file:
        print("   OK")
    else:
        for f, names in sorted(missing_by_file.items()):
            print(f"   {f}:")
            for n in sorted(names):
                print(f"      {n}")
    print()

    print("DUPLICATES (более одного def в одном файле):")
    if not duplicates_by_file:
        print("   OK")
    else:
        for f, dups in sorted(duplicates_by_file.items()):
            print(f"   {f}:")
            for n, cnt in sorted(dups.items()):
                print(f"      {n} — {cnt} раз")
    print()

    print("UNUSED (определено, но нигде не использовано):")
    if not result["unused"]:
        print("   OK")
    else:
        for n in result["unused"]:
            print(f"   {n}")
    print()

    has_problem = bool(missing_by_file or duplicates_by_file)
    if has_problem:
        print("ИТОГ: ❌ есть проблемы, приложение упадёт на вкладке")
        print("      Исправьте MISSING/DUPLICATES до запуска Streamlit.")
    else:
        print("ИТОГ: ✅ проблем не найдено")
        if result["unused"]:
            print(f"      {len(result['unused'])} функций ни разу "
                  f"не использованы — проверьте вручную.")
            print("      (Streamlit-колбэки передаются как объекты, "
                  "а не вызываются,")
            print("       поэтому некоторые попадают сюда ложно — "
                  "это нормально.)")
    return has_problem


# ==================================================================
# Точка входа
# ==================================================================
def main() -> int:
    if len(sys.argv) > 1:
        root = Path(sys.argv[1])
    else:
        root = Path(__file__).resolve().parent

    if not root.exists():
        print(f"Путь не найден: {root}")
        return 2

    print(f"Корень проекта: {root.resolve()}")
    print()

    result = analyze(root)

    if "error" in result:
        print_report(result)
        return 2

    has_problem = print_report(result)
    return 1 if has_problem else 0


if __name__ == "__main__":
    sys.exit(main())