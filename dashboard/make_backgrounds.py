"""Draw the Tableau background images and layout guide for the six dashboard pages (route-line theme)."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
FONTS = HERE / "fonts"

W, H = 1400, 1000
MARGIN, GAP = 40, 16
CONTENT_TOP, CONTENT_BOTTOM = 120, 946

BG = "#061F3F"
PANEL = "#0B2C55"
PANEL_BORDER = "#173F6E"
INNER = "#082447"
CITY = "#0A5FA8"
CITY_BORDER = "#2C7AC4"
SKY = "#4FA8F0"
WHITE = "#FFFFFF"
MUTED = "#9DB7D5"
FAINT = "#5F7FA6"
TEAL = "#2BB3A3"
AMBER = "#F2B53A"
CORAL = "#E8604C"

TITLE = "ETS BUS RELIABILITY"
SUBTITLE = "Edmonton Transit Service buses  ·  Nov 20 – Dec 1, 2026  ·  live GTFS feed"
FOOTER_LEFT = "Independent analysis of ETS open data  ·  Not affiliated with the City of Edmonton or Edmonton Transit Service"
FOOTER_RIGHT = "github.com/ap001AP/ets-reliability"

PAGES = ["Overview", "Routes", "When", "Where", "Stories", "About"]
NAV_X0, NAV_X1, NAV_Y = 650, 1318, 50


@dataclass
class Panel:
    name: str
    x: int
    y: int
    w: int
    h: int
    subtitle: str = ""
    kind: str = "chart"
    accent: str | None = None
    notes: str = ""

    @property
    def inner(self) -> tuple[int, int, int, int]:
        if self.kind == "kpi":
            return (self.x + 16, self.y + 40, self.w - 32, self.h - 74)
        if self.kind == "strip" and self.h <= 64:
            return (self.x + 120, self.y + 10, self.w - 136, self.h - 20)
        if self.kind in ("highlight", "strip"):
            return (self.x + 16, self.y + 44, self.w - 32, self.h - 56)
        return (self.x + 16, self.y + 48, self.w - 32, self.h - 64)


@dataclass
class Page:
    name: str
    panels: list[Panel] = field(default_factory=list)


def columns(widths: list[int], x0: int = MARGIN) -> list[int]:
    xs, x = [], x0
    for w in widths:
        xs.append(x)
        x += w + GAP
    return xs


def split(total: int, parts: int) -> list[int]:
    base = (total - GAP * (parts - 1)) // parts
    sizes = [base] * parts
    sizes[-1] += total - GAP * (parts - 1) - base * parts
    return sizes


CONTENT_W = W - 2 * MARGIN
TWO_THIRDS = (CONTENT_W - GAP) * 2 // 3
ONE_THIRD = CONTENT_W - GAP - TWO_THIRDS
HALF = split(CONTENT_W, 2)


def build_pages() -> list[Page]:
    pages = []

    kpi_w = split(CONTENT_W, 5)
    kx = columns(kpi_w)
    kpis = [
        ("ON TIME", "1 min early to 5 min late", TEAL),
        ("ETS STANDARD", "0 to +3 min · 90% target", SKY),
        ("BUNCHED", "frequent service", AMBER),
        ("EXCESS WAIT", "per rider, seconds", AMBER),
        ("MISSING TRIPS", "never ran, not cancelled", CORAL),
    ]
    overview = Page("Overview", [
        Panel(n, kx[i], 120, kpi_w[i], 120, s, "kpi", a, "Big number (BAN)") for i, (n, s, a) in enumerate(kpis)
    ])
    overview.panels += [
        Panel("ON-TIME BY DAY", MARGIN, 256, TWO_THIRDS, 330, "share of departures", notes="network_daily.csv"),
        Panel("ABOUT THIS DATA", MARGIN + TWO_THIRDS + GAP, 256, ONE_THIRD, 330, "scope", notes="Text: scope and dates"),
        Panel("LEAST RELIABLE ROUTES", MARGIN, 602, HALF[0], 344, "on-time %", notes="routes.csv"),
        Panel("MOST BUNCHED ROUTES", MARGIN + HALF[0] + GAP, 602, HALF[1], 344, "frequent service", notes="routes.csv"),
    ]
    pages.append(overview)

    right_w = 544
    left_w = CONTENT_W - GAP - right_w
    pages.append(Page("Routes", [
        Panel("FILTERS", MARGIN, 120, CONTENT_W, 64, kind="strip", notes="Route kind, day type"),
        Panel("ROUTE RANKING", MARGIN, 200, left_w, 746, "sorted by on-time %", notes="routes.csv table"),
        Panel("ON TIME VS BUNCHING", MARGIN + left_w + GAP, 200, right_w, 420, "each dot is a route",
              notes="routes.csv scatter"),
        Panel("SELECTED ROUTE", MARGIN + left_w + GAP, 636, right_w, 310, kind="highlight",
              notes="Route detail on selection"),
    ]))

    pages.append(Page("When", [
        Panel("FILTERS", MARGIN, 120, CONTENT_W, 64, kind="strip", notes="Route, route kind"),
        Panel("ON-TIME BY HOUR AND DAY", MARGIN, 200, CONTENT_W, 440, "share of departures on time",
              notes="route_hour.csv heatmap"),
        Panel("EARLY AND LATE BY HOUR", MARGIN, 656, TWO_THIRDS, 290, "share of departures",
              notes="route_hour.csv"),
        Panel("WEEKDAY VS WEEKEND", MARGIN + TWO_THIRDS + GAP, 656, ONE_THIRD, 290, notes="network_daily.csv"),
    ]))

    pages.append(Page("Where", [
        Panel("DELAY MAP", MARGIN, 120, TWO_THIRDS, 826, "average departure delay by stop",
              notes="stop_route.csv points + route_shapes.csv lines"),
        Panel("ROUTE AND DIRECTION", MARGIN + TWO_THIRDS + GAP, 120, ONE_THIRD, 150, kind="strip",
              notes="Route and direction filters"),
        Panel("DELAY ALONG THE ROUTE", MARGIN + TWO_THIRDS + GAP, 286, ONE_THIRD, 440, "stop by stop",
              notes="stop_route.csv by stop_order"),
        Panel("LEGEND AND NOTES", MARGIN + TWO_THIRDS + GAP, 742, ONE_THIRD, 204, notes="Colour legend, text"),
    ]))

    pages.append(Page("Stories", [
        Panel("ROUTE 56 · CENTURY PARK", MARGIN, 120, ONE_THIRD, 405, kind="highlight", notes="Story text"),
        Panel("DEPARTURES FROM CENTURY PARK", MARGIN + ONE_THIRD + GAP, 120, TWO_THIRDS, 405,
              "gap to the bus ahead", notes="headways.csv, route 056"),
        Panel("ROUTE 127 · A DELAY THAT CARRIED", MARGIN, 541, ONE_THIRD, 405, kind="highlight", notes="Story text"),
        Panel("DELAY BY TRIP", MARGIN + ONE_THIRD + GAP, 541, TWO_THIRDS, 405, "Fri Nov 20 afternoon",
              notes="trip-level delays, route 127"),
    ]))

    pages.append(Page("About", [
        Panel("HOW IT'S MEASURED", MARGIN, 120, TWO_THIRDS, 300, "from GPS ping to stop departure", kind="method"),
        Panel("VALIDATION", MARGIN + TWO_THIRDS + GAP, 120, ONE_THIRD, 300, "against ETS's own records",
              notes="Text or BANs"),
        Panel("FEED UPTIME AND OUTAGES", MARGIN, 436, HALF[0], 510, notes="Text or table from data_quality.md"),
        Panel("ISSUES FOUND AND FIXED", MARGIN + HALF[0] + GAP, 436, HALF[1], 510, notes="Text"),
    ]))
    return pages


class Canvas:
    def __init__(self, scale: int):
        self.s = scale
        self.img = Image.new("RGB", (W * scale, H * scale), BG)
        self.d = ImageDraw.Draw(self.img)
        self.fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    def font(self, name: str, size: float) -> ImageFont.FreeTypeFont:
        key = (name, round(size * self.s))
        if key not in self.fonts:
            self.fonts[key] = ImageFont.truetype(str(FONTS / name), key[1])
        return self.fonts[key]

    def _p(self, *v: float) -> list[float]:
        return [x * self.s for x in v]

    def rect(self, x, y, w, h, fill, outline=None, radius=10, width=1):
        self.d.rounded_rectangle(self._p(x, y, x + w, y + h), radius=radius * self.s, fill=fill,
                                 outline=outline, width=max(1, round(width * self.s)))

    def bar(self, x, y, w, h, fill):
        self.d.rectangle(self._p(x, y, x + w, y + h), fill=fill)

    def line(self, pts, fill, width):
        self.d.line([c * self.s for p in pts for c in p], fill=fill, width=round(width * self.s), joint="curve")

    def circle(self, cx, cy, r, fill, outline=None, width=1):
        self.d.ellipse(self._p(cx - r, cy - r, cx + r, cy + r), fill=fill, outline=outline,
                       width=round(width * self.s))

    def text(self, x, y, s, font, size, fill, anchor="la", spacing=0.0):
        f = self.font(font, size)
        if not spacing:
            self.d.text((x * self.s, y * self.s), s, font=f, fill=fill, anchor=anchor)
            return
        total = sum(f.getlength(ch) for ch in s) + spacing * self.s * (len(s) - 1)
        cx = x * self.s - (total if anchor[0] == "r" else total / 2 if anchor[0] == "m" else 0)
        _, top, _, _ = f.getbbox(s, anchor="ls")
        baseline = {"t": y * self.s - top, "a": y * self.s - top, "m": y * self.s - top / 2}.get(anchor[1], y * self.s)
        for ch in s:
            self.d.text((cx, baseline), ch, font=f, fill=fill, anchor="ls")
            cx += f.getlength(ch) + spacing * self.s

    def width(self, s, font, size) -> float:
        return self.font(font, size).getlength(s) / self.s


def nav_stops() -> list[float]:
    step = (NAV_X1 - NAV_X0) / (len(PAGES) - 1)
    return [NAV_X0 + i * step for i in range(len(PAGES))]


def draw_header(c: Canvas, current: str) -> None:
    c.text(MARGIN, 26, TITLE, "RobotoCondensed-Bold.ttf", 36, WHITE, spacing=1.2)
    c.text(MARGIN, 74, SUBTITLE, "Roboto-Regular.ttf", 14, MUTED)
    xs = nav_stops()
    c.line([(NAV_X0 - 34, NAV_Y), (NAV_X1 + 34, NAV_Y)], SKY, 4)
    for end in (NAV_X0 - 34, NAV_X1 + 34):
        c.bar(end - 2, NAV_Y - 8, 4, 16, SKY)
    for x, name in zip(xs, PAGES):
        if name == current:
            c.circle(x, NAV_Y, 13, SKY)
            c.circle(x, NAV_Y, 7, WHITE)
            c.text(x, NAV_Y + 22, name.upper(), "RobotoCondensed-Bold.ttf", 14, WHITE, anchor="mt", spacing=0.8)
        else:
            c.circle(x, NAV_Y, 10, BG, outline=SKY, width=3)
            c.text(x, NAV_Y + 22, name.upper(), "RobotoCondensed-SemiBold.ttf", 13, MUTED, anchor="mt", spacing=0.8)


def draw_footer(c: Canvas) -> None:
    c.bar(MARGIN, 958, CONTENT_W, 1, PANEL_BORDER)
    c.text(MARGIN, 976, FOOTER_LEFT, "Roboto-Regular.ttf", 12, FAINT, anchor="lm")
    c.text(W - MARGIN, 976, FOOTER_RIGHT, "Roboto-Medium.ttf", 12, MUTED, anchor="rm")


def draw_panel(c: Canvas, p: Panel) -> None:
    if p.kind == "highlight":
        c.rect(p.x, p.y, p.w, p.h, CITY, outline=CITY_BORDER)
        c.text(p.x + 18, p.y + 18, p.name, "RobotoCondensed-Bold.ttf", 15, WHITE, spacing=1.0)
        return
    c.rect(p.x, p.y, p.w, p.h, PANEL, outline=PANEL_BORDER)
    if p.kind == "kpi":
        c.d.rectangle(c._p(p.x + 1, p.y + 1, p.x + p.w - 1, p.y + 4), fill=p.accent)
        c.text(p.x + 16, p.y + 18, p.name, "RobotoCondensed-Bold.ttf", 14, WHITE, spacing=1.0)
        c.text(p.x + 16, p.y + p.h - 16, p.subtitle, "Roboto-Regular.ttf", 12, MUTED, anchor="lb")
        return
    if p.kind == "strip":
        c.text(p.x + 18, p.y + (22 if p.h <= 64 else 18), p.name, "RobotoCondensed-Bold.ttf", 14, MUTED,
               spacing=1.0)
        return
    c.text(p.x + 18, p.y + 17, p.name, "RobotoCondensed-Bold.ttf", 16, WHITE, spacing=1.0)
    if p.subtitle:
        c.text(p.x + p.w - 18, p.y + 19, p.subtitle, "Roboto-Regular.ttf", 13, MUTED, anchor="ra")
    if p.kind == "method":
        draw_method(c, p)
        return
    ix, iy, iw, ih = p.inner
    c.rect(ix, iy, iw, ih, INNER, radius=6)


def draw_method(c: Canvas, p: Panel) -> None:
    ix, iy, iw, ih = p.inner
    c.rect(ix, iy, iw, ih, INNER, radius=6)
    step_w = iw / 3
    labels = [
        ("1.  GPS PING", "Every bus reports its position", "every 30 seconds."),
        ("2.  MATCH TO ROUTE", "Each ping is placed on the trip's", "route line, in order."),
        ("3.  TIME THE STOP", "Departure = when the bus passes", "30 m beyond the stop."),
    ]
    art_y = iy + 70
    for i, (title, l1, l2) in enumerate(labels):
        x0 = ix + i * step_w
        if i:
            c.bar(x0, iy + 20, 1, ih - 40, PANEL_BORDER)
        cx = x0 + step_w / 2
        if i == 0:
            for dx, dy in ((-60, 12), (-25, -10), (12, 8), (48, -6)):
                c.circle(cx + dx, art_y + dy, 6, AMBER)
        elif i == 1:
            c.line([(cx - 80, art_y + 22), (cx - 20, art_y - 4), (cx + 30, art_y + 6), (cx + 80, art_y - 18)], SKY, 4)
            for dx, dy, tx, ty in ((-60, 18, -58, 13), (-10, -14, -12, -2), (50, 20, 48, -2)):
                c.line([(cx + dx, art_y + dy), (cx + tx, art_y + ty)], FAINT, 1.5)
                c.circle(cx + dx, art_y + dy, 5, AMBER)
            c.circle(cx - 58, art_y + 13, 4, WHITE)
            c.circle(cx - 12, art_y - 2, 4, WHITE)
            c.circle(cx + 48, art_y - 2, 4, WHITE)
        else:
            c.line([(cx - 85, art_y + 8), (cx + 85, art_y + 8)], SKY, 4)
            c.bar(cx - 30, art_y - 22, 4, 30, WHITE)
            c.rect(cx - 40, art_y - 32, 24, 14, WHITE, radius=3)
            c.bar(cx + 8, art_y - 10, 2, 36, TEAL)
            c.line([(cx - 26, art_y + 26), (cx + 8, art_y + 26)], TEAL, 2)
            c.text(cx - 9, art_y + 32, "30 m", "Roboto-Medium.ttf", 11, TEAL, anchor="mt")
            c.circle(cx + 8, art_y + 8, 6, TEAL)
        c.text(cx, iy + 132, title, "RobotoCondensed-Bold.ttf", 14, WHITE, anchor="mt", spacing=0.8)
        c.text(cx, iy + 160, l1, "Roboto-Regular.ttf", 12, MUTED, anchor="mt")
        c.text(cx, iy + 178, l2, "Roboto-Regular.ttf", 12, MUTED, anchor="mt")


def render(page: Page, scale: int) -> Image.Image:
    c = Canvas(scale)
    draw_header(c, page.name)
    for p in page.panels:
        draw_panel(c, p)
    draw_footer(c)
    return c.img


def nav_buttons() -> list[dict]:
    xs = nav_stops()
    half = (xs[1] - xs[0]) / 2
    return [{"page": n, "x": round(x - half + 4), "y": NAV_Y - 18, "w": round(2 * half - 8), "h": 58}
            for x, n in zip(xs, PAGES)]


def layout_guide(pages: list[Page]) -> str:
    lines = [
        "# Dashboard layout guide",
        "",
        f"Dashboard size: **{W} × {H}** (Tableau: Size → Fixed size → Custom). Background images are drawn at 2× "
        f"({W * 2} × {H * 2}) for sharp text; add each as an Image object at x 0, y 0, w {W}, h {H} with "
        "**Fit image** on, then float every sheet on top at the positions below and set sheet backgrounds to None "
        "(transparent).",
        "",
        "Colours: background `" + BG + "`, panel `" + PANEL + "`, chart area `" + INNER + "`, highlight `" + CITY +
        "`, text `" + WHITE + "` / `" + MUTED + "`, on time `" + TEAL + "`, early `" + AMBER + "`, late `" + CORAL +
        "`, accent `" + SKY + "`.",
        "",
        "## Navigation buttons (same on every page)",
        "",
        "Float a Navigation button over each stop, set it to go to that page, and make it invisible "
        "(Button style: Image with a fully transparent PNG, or Text with an empty title, no border and no fill).",
        "",
        "| page | x | y | w | h |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| {b['page']} | {b['x']} | {b['y']} | {b['w']} | {b['h']} |" for b in nav_buttons()]
    for page in pages:
        lines += ["", f"## {page.name}  (`bg_{page.name.lower()}.png`)", "",
                  "| panel | place sheet at x | y | w | h | suggested content |", "|---|---|---|---|---|---|"]
        for p in page.panels:
            ix, iy, iw, ih = p.inner
            lines.append(f"| {p.name} | {ix} | {iy} | {iw} | {ih} | {p.notes} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Draw dashboard background images")
    parser.add_argument("--out", type=Path, default=HERE / "backgrounds")
    parser.add_argument("--scale", type=int, default=2)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    pages = build_pages()
    for page in pages:
        img = render(page, args.scale)
        path = args.out / f"bg_{page.name.lower()}.png"
        img.save(path, optimize=True)
        print("wrote", path)
    (HERE / "LAYOUT.md").write_text(layout_guide(pages))
    (HERE / "layout.json").write_text(json.dumps({
        "size": [W, H],
        "nav": nav_buttons(),
        "pages": {pg.name: [{"panel": p.name, "inner": p.inner, "outer": [p.x, p.y, p.w, p.h]} for p in pg.panels]
                  for pg in pages},
    }, indent=2))
    print("wrote", HERE / "LAYOUT.md")


if __name__ == "__main__":
    main()
