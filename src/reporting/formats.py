"""Форматы отчёта: текст для человека, JSON целиком, CSV главной таблицы.

Файл пишется только после того, как весь текст собран и закодирован:
если данные не кодируются, прежний отчёт на диске остаётся целым.
"""

import csv
import io
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from src.exceptions import InvalidOperationError

FILE_ENCODING = "utf-8"
# BOM и точка с запятой: так русский Excel открывает CSV сразу по колонкам.
CSV_ENCODING = "utf-8-sig"
CSV_DELIMITER = ";"
# Ячейка, начинающаяся с этих знаков, в Excel выполняется как формула.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
FORMULA_ESCAPE = "'"
TEXT_RULE_WIDTH = 72
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def plain(value):
    """Значение ячейки: деньги и время строкой, пусто вместо None."""
    if value is None:
        return ""

    if isinstance(value, datetime):
        return value.strftime(TIME_FORMAT)

    if isinstance(value, (list, tuple)):
        return ", ".join(plain(item) for item in value)

    return str(value)


def render_text(report):
    rule = "=" * TEXT_RULE_WIDTH
    lines = [rule, report.title, f"Сформирован: {plain(report.generated_at)}", rule]
    width = max((len(key) for key in report.summary), default=0)

    for key, value in report.summary.items():
        lines.append(f"{key:<{width}}  {plain(value)}")

    for name, rows in report.tables.items():
        lines.extend(["", f"--- {name} ({len(rows)}) ---"])
        lines.extend(_render_table(rows))

    return "\n".join(lines) + "\n"


def write_text(report, path):
    return _write(path, render_text(report), FILE_ENCODING)


def write_json(report, path):
    text = json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=_jsonable)
    return _write(path, text + "\n", FILE_ENCODING)


def write_csv(report, path):
    """Одна плоская таблица: главная таблица отчёта."""
    rows = report.tables[report.main_table]
    columns = list(rows[0]) if rows else list(report.main_columns)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=CSV_DELIMITER)
    writer.writerow(columns)
    writer.writerows([_csv_cell(row.get(column)) for column in columns] for row in rows)
    return _write(path, buffer.getvalue(), CSV_ENCODING)


def _render_table(rows):
    if not rows:
        return ["(нет данных)"]

    columns = list(rows[0])
    cells = [[plain(row.get(column)) for column in columns] for row in rows]
    widths = [
        max(len(column), *(len(line[index]) for line in cells))
        for index, column in enumerate(columns)
    ]
    header = "  ".join(f"{column:<{width}}" for column, width in zip(columns, widths))
    body = [
        "  ".join(f"{cell:<{width}}" for cell, width in zip(line, widths)).rstrip()
        for line in cells
    ]
    return [header.rstrip(), *body]


def _csv_cell(value):
    """Текст, похожий на формулу, экранируется апострофом; числа не трогаем."""
    text = plain(value)

    if text.startswith(FORMULA_PREFIXES) and not _is_number(text):
        return FORMULA_ESCAPE + text

    return text


def _is_number(text):
    try:
        return Decimal(text).is_finite()
    except InvalidOperation:
        return False


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")

    return str(value)


def _write(path, text, encoding):
    try:
        data = text.encode(encoding)
    except UnicodeEncodeError as error:
        raise InvalidOperationError(
            "Report contains text that cannot be saved"
        ) from error

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
