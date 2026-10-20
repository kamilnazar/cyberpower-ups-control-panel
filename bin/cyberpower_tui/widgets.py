"""Small curses drawing primitives and a colour theme.

Everything here paints into a curses window with plain ``addstr`` and clips to
the window bounds, so a tiny terminal degrades quietly instead of raising.
The theme falls back to monochrome when the terminal has no colour.
"""

from __future__ import annotations

import curses

# ------------------------------------------------------------- text helpers


def clip(text: str, width: int) -> str:
    """Trim to `width` cells, adding an ellipsis when something was dropped."""
    text = str(text)
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return text[:1]
    return text[: width - 1] + "…"


def pad(text: str, width: int) -> str:
    """Clip then pad with spaces to exactly `width` cells."""
    text = clip(text, width)
    return text + " " * (width - len(text))


def wrap(text: str, width: int) -> list[str]:
    """Break text into lines of at most `width` cells, on word boundaries.

    A word longer than the width is cut rather than allowed to run over.
    """
    if width <= 0:
        return []
    lines: list[str] = []
    for paragraph in str(text).split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        line = ""
        for word in words:
            candidate = word if not line else f"{line} {word}"
            if len(candidate) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            line = word if len(word) <= width else word[:width]
        lines.append(line)
    return lines


# --------------------------------------------------------------- primitives


def put(screen, row: int, col: int, text: str, attr: int = 0) -> None:
    """Draw `text` at (row, col), clipped to the window. Never raises."""
    if not text:
        return
    height, width = screen.getmaxyx()
    if row < 0 or row >= height or col < 0 or col >= width:
        return
    room = width - col
    if room <= 0:
        return
    try:
        screen.addstr(row, col, str(text)[:room], attr)
    except (curses.error, UnicodeEncodeError, ValueError):
        pass


def rule(screen, row: int, col: int, width: int, attr: int = 0) -> None:
    put(screen, row, col, "─" * max(0, width), attr)


def box(screen, top: int, left: int, height: int, width: int, attr: int = 0) -> None:
    if height < 2 or width < 2:
        return
    put(screen, top, left, "┌" + "─" * (width - 2) + "┐", attr)
    for row in range(top + 1, top + height - 1):
        put(screen, row, left, "│", attr)
        put(screen, row, left + width - 1, "│", attr)
    put(screen, top + height - 1, left, "└" + "─" * (width - 2) + "┘", attr)


def fill(screen, top: int, left: int, height: int, width: int, attr: int = 0) -> None:
    """Blank a rectangle — used to clear the area a modal sits over."""
    for row in range(top, top + height):
        put(screen, row, left, " " * max(0, width), attr)


def battery(
    screen,
    top: int,
    left: int,
    width: int,
    fraction: float,
    frame_attr: int = 0,
    fill_attr: int = 0,
) -> int:
    """Draw a three-row battery gauge. Returns the rows consumed."""
    if width < 8:
        return 0
    inner = width - 2
    frame = "─" * inner
    put(screen, top, left, "┌" + frame + "┐▏", frame_attr)
    put(screen, top + 1, left, "│", frame_attr)
    filled = max(0, min(inner, int(round(max(0.0, min(1.0, fraction)) * inner))))
    if filled:
        put(screen, top + 1, left + 1, "█" * filled, fill_attr)
    if inner - filled:
        put(screen, top + 1, left + 1 + filled, "░" * (inner - filled), frame_attr)
    put(screen, top + 1, left + width - 1, "│", frame_attr)
    put(screen, top + 2, left, "└" + frame + "┘", frame_attr)
    return 3


#: Block heights a sparkline draws from, lowest first.
SPARK_LEVELS = "▁▂▃▄▅▆▇█"


def sparkline(values, width: int) -> str:
    """A one-row bar graph of `values`, newest on the right.

    Scaled to the values it is given rather than to volts, so a flat mains
    supply still shows its own small ripple: the series' own range is the
    y-axis. An entirely flat series draws a level line instead of a floor.
    """
    values = [value for value in values if isinstance(value, (int, float))]
    if not values or width <= 0:
        return ""
    if len(values) > width:
        values = values[-width:]
    low, high = min(values), max(values)
    span = high - low
    if span <= 0:
        return SPARK_LEVELS[len(SPARK_LEVELS) // 2] * len(values)
    top = len(SPARK_LEVELS) - 1
    return "".join(SPARK_LEVELS[round((value - low) / span * top)] for value in values)


def pairs(
    screen,
    top: int,
    left: int,
    width: int,
    rows,
    theme,
    label_width: int = 18,
    gap: int = 4,
    max_columns: int = 2,
) -> int:
    """Lay (label, value) rows out in one or two columns. Returns rows used.

    Reading order is row-major, so items that belong together (Status/Runtime,
    Load/Input) stay on the same line.
    """
    rows = [row for row in rows if row]
    if not rows:
        return 0
    value_width = max(len(str(value)) for _, value in rows)
    value_width = min(value_width, max(8, width - label_width - gap))
    column_width = label_width + value_width + gap
    columns = max(1, min(max_columns, max(1, width // column_width)))
    for index, (label, value) in enumerate(rows):
        column = index % columns
        line = index // columns
        x = left + column * column_width
        put(screen, top + line, x, pad(label, label_width), theme.muted)
        put(screen, top + line, x + label_width, clip(str(value), value_width), 0)
    return (len(rows) + columns - 1) // columns


# -------------------------------------------------------------------- theme


class Theme:
    """Colour pairs with a reachable monochrome fallback."""

    def __init__(self, screen):
        self.enabled = False
        try:
            if not curses.has_colors():
                return
            curses.start_color()
            try:
                curses.use_default_colors()
                background = -1
            except curses.error:
                background = curses.COLOR_BLACK
            curses.init_pair(1, curses.COLOR_GREEN, background)
            curses.init_pair(2, curses.COLOR_YELLOW, background)
            curses.init_pair(3, curses.COLOR_RED, background)
            curses.init_pair(4, curses.COLOR_CYAN, background)
            self.enabled = True
        except curses.error:
            self.enabled = False

    def _pair(self, number: int) -> int:
        return curses.color_pair(number) if self.enabled else 0

    @property
    def good(self) -> int:
        return self._pair(1)

    @property
    def warn(self) -> int:
        return self._pair(2)

    @property
    def bad(self) -> int:
        return self._pair(3)

    @property
    def accent(self) -> int:
        return self._pair(4) | curses.A_BOLD

    @property
    def muted(self) -> int:
        return curses.A_DIM

    @property
    def selection(self) -> int:
        return curses.A_REVERSE | curses.A_BOLD

    def severity(self, level: str) -> int:
        return {"ok": self.good, "warn": self.warn, "bad": self.bad}.get(level, 0)
