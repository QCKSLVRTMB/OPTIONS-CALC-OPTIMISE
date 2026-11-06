"""
check_functions.py — проверка целостности проекта.

Сканирует все .py проекта (config.py, app.py, core/, ui/, tabs/)
и проверяет:

    AST-режим (по умолчанию, быстро):
        MISSING       — имена в Load-контексте, но не определённые
                        ни в этом файле, ни через import, ни builtin.
        DUPLICATES    — функции с одним именем >1 раз в одном файле.
        UNUSED        — функции без использования. Колбэки Streamlit
                        (on_change=..., on_click=...) сюда попадают ложно —
                        передаются как объект, а не вызываются.

    --imports (медленно, но ловит ImportError):
        Реально импортирует каждый модуль проекта через importlib.
        Файлы с st.set_page_config на верхнем уровне (app.py)
        пропускаются — их проверяйте через `streamlit run`.

Использование:
    python check_functions.py                # AST-check
    python check_functions.py --imports      # AST + imports
    python check_functions.py app.py         # один файл
    python check_functions.py /path/to/      # конкретная папка

Exit code:
    0 — всё ок
    1 — есть MISSING / DUPLICATES / ImportError
    2 — фатальная ошибка (файл не найден, синтаксис)
"""
from __future__ import annotations

import argparse
import ast
import collections
import importlib
import sys
from pathlib import Path


# ==================================================================
# Настройки
# ==================================================================
IGNORE_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "env",
    ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
    "node_modules", ".streamlit",
}
IGNORE_FILES = {"check_functions.py", "setup.py"}

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
    has_star = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    has_star = True
                    continue
                imported.add(alias.asname or alias.name)

    params: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in (*node.args.args, *node.args.kwonlyargs,
                        *getattr(node.args, "posonlyargs", [])):
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

    # has_set_page_config на верхнем уровне — такие файлы пропускаем
    # в режиме --imports (нужен Streamlit runtime).
    top_calls_spc = False
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            f = node.value.func
            if isinstance(f, ast.Attribute) and f.attr == "set_page_config":
                top_calls_spc = True
                break
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            f = node.value.func
            if (isinstance(f, ast.Attribute) and f.attr == "set_page_config"):
                top_calls_spc = True
                break
        # проверяем также внутри if __name__ == "__main__":
        if isinstance(node, ast.If):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    if (isinstance(f, ast.Attribute)
                            and f.attr == "set_page_config"):
                        top_calls_spc = True
                        break

    return {
        "defined": defined,
        "imported": imported,
        "used": used,
        "called": called,
        "assigned": assigned,
        "params": params,
        "class_methods": class_methods,
        "defs_count": counts,
        "has_star": has_star,
        "has_set_page_config": top_calls_spc,
    }


# ==================================================================
# Поиск файлов
# ==================================================================
def find_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(
        p for p in root.rglob("*.py")
        if not any(part in IGNORE_DIRS for part in p.parts)
        and p.name not in IGNORE_FILES
    )


# ==================================================================
# AST-анализ
# ==================================================================
def analyze_ast(root: Path) -> dict:
    files = find_files(root)
    parsed: dict[Path, dict] = {}
    for f in files:
        try:
            parsed[f] = parse_file(f)
        except SyntaxError as e:
            return {"error": f"{f}:{e.lineno}: {e.msg}", "file": f}

    project_defined = set()
    for info in parsed.values():
        project_defined |= info["defined"]

    missing_by_file: dict[Path, set[str]] = {}
    dups_by_file: dict[Path, dict[str, int]] = {}
    for f, info in parsed.items():
        if info["has_star"]:
            continue
        miss = (info["used"] - info["defined"] - info["imported"]
                - info["assigned"] - info["params"] - BUILTINS)
        if miss:
            missing_by_file[f] = miss
        d = {n: c for n, c in info["defs_count"].items() if c > 1}
        if d:
            dups_by_file[f] = d

    all_used, all_methods = set(), set()
    for info in parsed.values():
        all_used |= info["used"]
        all_methods |= info["class_methods"]
    unused = sorted(project_defined - all_used - all_methods)

    return {
        "files": files,
        "parsed": parsed,
        "missing": missing_by_file,
        "dups": dups_by_file,
        "unused": unused,
        "defined": project_defined,
    }


# ==================================================================
# Импорт-проверка
# ==================================================================
def module_name_from_path(file_path: Path, project_root: Path) -> str | None:
    try:
        rel = file_path.relative_to(project_root)
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return None
    return ".".join(parts)


def analyze_imports(root: Path, parsed: dict[Path, dict]) -> dict:
    """Пробует импортировать каждый модуль. Пропускает app.py / файлы
    с st.set_page_config на верхнем уровне."""
    project_root = root if root.is_dir() else root.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    import_errors: dict[Path, str] = {}
    skipped: list[Path] = []

    for f, info in parsed.items():
        if info.get("has_set_page_config"):
            skipped.append(f)
            continue

        module_name = module_name_from_path(f, project_root)
        if module_name is None:
            skipped.append(f)
            continue

        try:
            importlib.import_module(module_name)
        except Exception as e:
            import_errors[f] = f"{type(e).__name__}: {e}"

    return {"import_errors": import_errors, "skipped": skipped}


# ==================================================================
# Отчёт
# ==================================================================
def print_report_ast(result: dict) -> bool:
    print(f"Файлов просканировано: {len(result['files'])}")
    print(f"Функций/классов в проекте: {len(result['defined'])}")
    print()

    print("MISSING (used, но не определено/импортировано/builtin):")
    if not result["missing"]:
        print("   OK")
    else:
        for f, names in sorted(result["missing"].items()):
            print(f"   {f}:")
            for n in sorted(names):
                print(f"      {n}")
    print()

    print("DUPLICATES (более одного def в одном файле):")
    if not result["dups"]:
        print("   OK")
    else:
        for f, d in sorted(result["dups"].items()):
            print(f"   {f}:")
            for n, cnt in sorted(d.items()):
                print(f"      {n} — {cnt} раз")
    print()

    print("UNUSED (определено, но нигде не использовано):")
    if not result["unused"]:
        print("   OK")
    else:
        for n in result["unused"]:
            print(f"   {n}")
    print()

    return not (result["missing"] or result["dups"])


def print_report_imports(result: dict) -> bool:
    print("IMPORTS (реальный importlib):")
    errs = result["import_errors"]
    if not errs:
        print("   OK")
    else:
        for f, msg in sorted(errs.items()):
            print(f"   {f}: {msg}")
    if result["skipped"]:
        print("   Пропущено (нужен streamlit runtime):")
        for f in result["skipped"]:
            print(f"      {f}")
    print()
    return not errs


# ==================================================================
# Точка входа
# ==================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", nargs="?", default=None,
                    help="Файл или папка (по умолчанию — корень проекта)")
    ap.add_argument("--imports", action="store_true",
                    help="Дополнительно пробовать import каждого модуля")
    args = ap.parse_args()

    root = Path(args.path) if args.path else Path(__file__).resolve().parent
    if not root.exists():
        print(f"Путь не найден: {root}")
        return 2

    print(f"Корень проекта: {root.resolve()}")
    print()

    result = analyze_ast(root)
    if "error" in result:
        print(f"ОШИБКА: {result['error']}")
        return 2

    ast_ok = print_report_ast(result)
    imports_ok = True
    if args.imports:
        imp = analyze_imports(root, result["parsed"])
        imports_ok = print_report_imports(imp)

    ok = ast_ok and imports_ok
    print("ИТОГ: " + ("✅ проблем не найдено" if ok else "❌ есть проблемы"))
    if ok and result["unused"]:
        print(f"      {len(result['unused'])} функций ни разу не использованы.")
        print("      (Streamlit-колбэки сюда попадают ложно — это нормально.)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
