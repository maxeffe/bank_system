"""PNG-диаграммы отчётов.

Рисуем через Figure без pyplot: нет глобального списка фигур, который надо
закрывать, и нет окон. Стиль включается через rc_context только на время
рисования и не меняет настройки matplotlib для остального кода.

Цвета - проверенная палитра из навыка dataviz (светлая тема): категориальные
слоты идут в фиксированном порядке, уровни риска - статусные цвета с подписью.
"""

import math
from datetime import timedelta
from pathlib import Path

import matplotlib
from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SERIES_COLORS = (
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#4a3aa7",
    "#e34948",
)
STATUS_COLORS = {"low": "#0ca30c", "medium": "#fab219", "high": "#d03b3b"}
EMPTY_TEXT = "Нет данных"

FIGURE_SIZE = (8, 4.5)
DPI = 150
BODY_FONT_SIZE = 10
TITLE_FONT_SIZE = 12
BAR_FILL = 0.6
BAR_MAX_PX = 24
LINE_WIDTH = 1.5
MARKER_SIZE = 7
GAP_WIDTH = 2
GRID_WIDTH = 0.6
LABEL_OFFSET = 6
AXES_CENTER = 0.5

PIE_START_ANGLE = 90
MIN_PIE_LABEL_PERCENT = 5
PERCENT = 100
MIN_INNER_LABEL_SHARE = 0.08
VALUE_LABEL_MARGIN = 0.15
NEGATIVE_LABEL_MARGIN = 0.3
MIN_VALUE_SPAN = 1
END_LABEL_MIN_GAP = 0.06
SINGLE_MOMENT_SPAN = timedelta(hours=1)
LEGEND_RIGHT_ANCHOR = (1, 0.5)
LEGEND_BELOW_ANCHOR = (0.5, -0.15)
LEGEND_COLUMNS = 2

# Подписи дат только цифрами: без английских названий месяцев.
DATE_FORMATS = ["%Y", "%m.%Y", "%d.%m", "%H:%M", "%H:%M", "%H:%M:%S"]
DATE_OFFSET_FORMATS = ["", "%Y", "%m.%Y", "%d.%m.%Y", "%d.%m.%Y", "%d.%m.%Y %H:%M"]

STYLE = {
    "font.family": ["Segoe UI", "DejaVu Sans"],
    "font.size": BODY_FONT_SIZE,
    # Имена клиентов - не формулы: "$...$" рисуется как есть.
    "text.parse_math": False,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "axes.edgecolor": BASELINE,
    "axes.titlecolor": INK,
    "axes.titlesize": TITLE_FONT_SIZE,
    "axes.titlelocation": "left",
    "axes.labelcolor": SECONDARY,
    "xtick.color": BASELINE,
    "ytick.color": BASELINE,
    "xtick.labelcolor": SECONDARY,
    "ytick.labelcolor": SECONDARY,
    "grid.color": GRID,
    "grid.linewidth": GRID_WIDTH,
    "grid.linestyle": "-",
    "legend.frameon": False,
    "legend.labelcolor": SECONDARY,
}


def money(value):
    """1 234 567.89 - пробел между тысячами, как принято в рублях."""
    return f"{float(value):,.2f}".replace(",", " ")


def save_pie(path, title, labels, values):
    """Доли целого. Отрицательную долю нарисовать нельзя - она названа в заголовке."""
    negative = [(label, value) for label, value in zip(labels, values) if value < 0]

    if negative:
        listed = ", ".join(f"{label} {money(value)}" for label, value in negative)
        title = f"{title} (без отрицательных: {listed})"

    with matplotlib.rc_context(STYLE):
        figure, axes = _figure(title)
        slices = [
            (str(label), value) for label, value in zip(labels, values) if value > 0
        ]
        _draw_pie(axes, slices, SERIES_COLORS[: len(slices)])
        return _save(figure, path)


def save_risk_levels(path, title, counts):
    """Уровни риска - статусные цвета, поэтому у каждой доли подпись в легенде."""
    with matplotlib.rc_context(STYLE):
        figure, axes = _figure(title)
        slices = [(level, count) for level, count in counts.items() if count > 0]
        _draw_pie(axes, slices, [STATUS_COLORS[level] for level, _ in slices])
        return _save(figure, path)


def save_bars(path, title, labels, values):
    """Одна серия горизонтальных столбцов: один цвет, значение на конце столбца."""
    with matplotlib.rc_context(STYLE):
        figure, axes = _figure(title)

        if not values:
            _mark_empty(axes)
            return _save(figure, path)

        numbers = [float(value) for value in values]
        _set_category_axis(axes, labels)
        bars = axes.barh(
            range(len(numbers)),
            numbers,
            height=_bar_height(figure, len(numbers)),
            color=SERIES_COLORS[0],
        )
        axes.bar_label(
            bars,
            labels=[_number(value) for value in values],
            padding=LABEL_OFFSET,
            color=SECONDARY,
        )

        if min(numbers) < 0:
            axes.axvline(0, color=BASELINE, linewidth=GRID_WIDTH)

        _style_value_axis(axes, "x", numbers)
        return _save(figure, path)


def save_stacked_bars(path, title, categories, series):
    """Части целого по категориям: зазор цвета фона между сегментами, легенда."""
    with matplotlib.rc_context(STYLE):
        figure, axes = _figure(title)

        if not categories:
            _mark_empty(axes)
            return _save(figure, path)

        totals = [sum(parts) for parts in zip(*series.values())]
        largest = max(totals) or 1
        left = [0] * len(categories)
        _set_category_axis(axes, categories)

        for index, (name, values) in enumerate(series.items()):
            color = SERIES_COLORS[index]
            bars = axes.barh(
                range(len(categories)),
                values,
                left=left,
                height=_bar_height(figure, len(categories)),
                color=color,
                label=name,
                edgecolor=SURFACE,
                linewidth=GAP_WIDTH,
            )
            inner = [
                str(value) if value / largest >= MIN_INNER_LABEL_SHARE else ""
                for value in values
            ]
            axes.bar_label(bars, labels=inner, label_type="center", color=INK)
            left = [start + value for start, value in zip(left, values)]

        _style_value_axis(axes, "x", totals)
        _legend_below(axes, len(series))
        return _save(figure, path)


def save_balance_lines(path, title, series):
    """Ступенчатые линии: баланс держится до следующей операции. Одна ось Y."""
    drawn = {label: points for label, points in series.items() if points}
    shown = dict(list(drawn.items())[: len(SERIES_COLORS)])

    if len(shown) < len(drawn):
        title = f"{title} (первые {len(shown)} счетов из {len(drawn)})"

    with matplotlib.rc_context(STYLE):
        figure, axes = _figure(title)

        if not shown:
            _mark_empty(axes)
            return _save(figure, path)

        start = min(points[0][0] for points in shown.values())
        end = max(points[-1][0] for points in shown.values())
        ends = []

        for index, (label, points) in enumerate(shown.items()):
            color = SERIES_COLORS[index]
            ends.append(_draw_balance_line(axes, label, points, end, color))

        _set_time_axis(axes, start, end)
        values = [float(value) for points in shown.values() for _, value in points]
        _label_line_ends(axes, ends, values)
        _style_value_axis(axes, "y", values)
        _legend_below(axes, len(shown))
        return _save(figure, path)


def _draw_balance_line(axes, label, points, end, color):
    times = [moment for moment, _ in points] + [end]
    values = [float(value) for _, value in points]
    values.append(values[-1])
    axes.step(
        times, values, where="post", color=color, linewidth=LINE_WIDTH, label=label
    )
    axes.plot(
        times[-1],
        values[-1],
        marker="o",
        markersize=MARKER_SIZE,
        color=color,
        markeredgecolor=SURFACE,
        markeredgewidth=GAP_WIDTH,
    )
    return times[-1], values[-1]


def _label_line_ends(axes, ends, values):
    """Подпись конца линии только у одиночных концов.

    Если концы линий близко, подпись одной из них встанет рядом с маркером
    другой и прочитается неверно: такие концы остаются на легенду и ось.
    """
    gap = (max(values) - min(values)) * END_LABEL_MIN_GAP
    ordered = sorted(ends, key=lambda end: end[1])

    for index, (moment, value) in enumerate(ordered):
        neighbours = ordered[max(index - 1, 0) : index] + ordered[index + 1 : index + 2]

        if any(abs(value - other) < gap for _, other in neighbours):
            continue

        axes.annotate(
            money(value),
            (moment, value),
            xytext=(LABEL_OFFSET, 0),
            textcoords="offset points",
            va="center",
            color=SECONDARY,
        )


def _draw_pie(axes, slices, colors):
    """Проценты на крупных долях, у мелких - только в легенде: иначе слипаются."""
    if not slices:
        _mark_empty(axes)
        return

    total = sum(float(value) for _, value in slices)
    shares = [float(value) / total * PERCENT for _, value in slices]
    wedges, _ = axes.pie(
        [float(value) for _, value in slices],
        labels=[
            f"{share:.1f}%" if share >= MIN_PIE_LABEL_PERCENT else ""
            for share in shares
        ],
        colors=colors,
        startangle=PIE_START_ANGLE,
        counterclock=False,
        wedgeprops={"edgecolor": SURFACE, "linewidth": GAP_WIDTH},
        textprops={"color": INK},
    )
    axes.legend(
        wedges,
        [
            f"{label}: {_number(value)} ({share:.1f}%)"
            for (label, value), share in zip(slices, shares)
        ],
        loc="center left",
        bbox_to_anchor=LEGEND_RIGHT_ANCHOR,
    )
    axes.set_aspect("equal")


def _figure(title):
    figure = Figure(figsize=FIGURE_SIZE, dpi=DPI)
    axes = figure.add_subplot()
    axes.set_title(title)
    return figure, axes


def _save(figure, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, bbox_inches="tight")
    return path


def _mark_empty(axes):
    axes.text(
        AXES_CENTER, AXES_CENTER, EMPTY_TEXT, ha="center", va="center", color=MUTED
    )
    axes.set_axis_off()


def _set_category_axis(axes, labels):
    """Каждой категории - полоса ровно в одну единицу, первая сверху."""
    axes.set_yticks(range(len(labels)), labels=[str(label) for label in labels])
    axes.set_ylim(len(labels) - AXES_CENTER, -AXES_CENTER)


def _set_time_axis(axes, start, end):
    locator = AutoDateLocator()
    axes.xaxis.set_major_locator(locator)
    axes.xaxis.set_major_formatter(
        ConciseDateFormatter(
            locator, formats=DATE_FORMATS, offset_formats=DATE_OFFSET_FORMATS
        )
    )

    if start == end:
        axes.set_xlim(start - SINGLE_MOMENT_SPAN, end + SINGLE_MOMENT_SPAN)


def _legend_below(axes, count):
    axes.legend(
        loc="upper center",
        bbox_to_anchor=LEGEND_BELOW_ANCHOR,
        ncols=min(count, LEGEND_COLUMNS),
    )


def _style_value_axis(axes, value_axis, values):
    """Сетка по оси значений, целые деления, место под подписи значений."""
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)

    axis = axes.xaxis if value_axis == "x" else axes.yaxis
    axis.set_major_locator(MaxNLocator(integer=True))
    axis.set_major_formatter(FuncFormatter(_whole))
    axes.grid(axis=value_axis)
    axes.set_axisbelow(True)
    axes.tick_params(length=0)

    if value_axis == "x":
        _fit_bars(axes, values)
    elif max(values) - min(values) < MIN_VALUE_SPAN:
        axes.set_ylim(
            math.floor(min(values)) - MIN_VALUE_SPAN,
            math.ceil(max(values)) + MIN_VALUE_SPAN,
        )


def _fit_bars(axes, values):
    """Столбцы растут от нуля: ноль входит в диапазон, справа место под подпись."""
    low, high = min(0, min(values)), max(0, max(values))

    if high - low < MIN_VALUE_SPAN:
        axes.set_xlim(math.floor(low), max(MIN_VALUE_SPAN, math.ceil(high)))
    else:
        axes.margins(x=NEGATIVE_LABEL_MARGIN if low < 0 else VALUE_LABEL_MARGIN)


def _whole(value, _position=None):
    """Подпись деления оси: целое число, пробел между тысячами, без «-0»."""
    text = f"{float(value):,.0f}".replace(",", " ")
    return "0" if text == "-0" else text


def _bar_height(figure, count):
    """Столбец не толще 24 px: полоса считается от реальной высоты области графика."""
    params = figure.subplotpars
    plot_px = figure.get_figheight() * figure.dpi * (params.top - params.bottom)
    return min(BAR_FILL, BAR_MAX_PX / (plot_px / count))


def _number(value):
    return str(value) if isinstance(value, int) else money(value)
