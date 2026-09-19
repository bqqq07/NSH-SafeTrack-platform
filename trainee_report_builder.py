"""
NSH SafeTrack — Trainee Weekly Report Builder
Generates a complete English PPTX from structured DB data. No template required.
"""
import io
from lxml import etree
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.oxml.ns import qn

def _c(r, g, b): return RGBColor(r, g, b)

C_BLUE   = _c(0x1e,0x40,0xaf)
C_DBLUE  = _c(0x17,0x32,0x7f)
C_GREEN  = _c(0x16,0xa3,0x4a)
C_AMBER  = _c(0xd9,0x77,0x06)
C_RED    = _c(0xdc,0x26,0x26)
C_PURPLE = _c(0x7c,0x3a,0xed)
C_TEAL   = _c(0x0d,0x94,0x88)
C_DARK   = _c(0x1e,0x29,0x3b)
C_GRAY   = _c(0x64,0x74,0x8b)
C_MID    = _c(0x94,0xa3,0xb8)
C_WHITE  = _c(0xff,0xff,0xff)
C_LIGHT  = _c(0xf8,0xfa,0xfc)

BG_PASS  = _c(0xbb,0xf7,0xd0)
BG_PROG  = _c(0xfe,0xf0,0x8a)
BG_NONE  = _c(0xf1,0xf5,0xf9)
BG_HIGH  = _c(0xfe,0xe2,0xe2)
BG_MED   = _c(0xff,0xf7,0xcc)
BG_LOW   = _c(0xdc,0xfa,0xce)
BG_POS   = _c(0xdb,0xea,0xfe)
BG_HDR   = _c(0x1e,0x40,0xaf)
BG_ALT   = _c(0xf8,0xfa,0xfc)

MODULE_SHORT = [
    "Foundation", "Hot Work", "Work at Height",
    "Confined Spaces", "Lifting & Equip.", "Electrical & Rad.", "Governance",
]

SW = Inches(13.33)
SH = Inches(7.5)
M  = Inches(0.45)


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _blank(prs):
    layout = next((l for l in prs.slide_layouts if l.name.lower() == "blank"),
                  prs.slide_layouts[6])
    slide = prs.slides.add_slide(layout)
    for ph in list(slide.placeholders):
        ph.element.getparent().remove(ph.element)
    return slide


def _bg(slide, color=None):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color or C_WHITE


def _tb(slide, l, t, w, h, text, size=11, bold=False, color=None,
        align=PP_ALIGN.LEFT, italic=False):
    box = slide.shapes.add_textbox(l, t, w, h)
    tf  = box.text_frame
    tf.word_wrap = True
    p   = tf.paragraphs[0]
    p.alignment = align
    r   = p.add_run()
    r.text           = str(text)
    r.font.size      = Pt(size)
    r.font.bold      = bold
    r.font.italic    = italic
    r.font.color.rgb = color or C_DARK
    r.font.name      = "Calibri"
    return box


def _rect(slide, l, t, w, h, fill, border=None, bw=0.75):
    sh = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, l, t, w, h)
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    if border:
        sh.line.color.rgb = border
        sh.line.width = Pt(bw)
    else:
        sh.line.fill.background()
    return sh


def _bar(slide, l, t, w, h, pct, fg, bg=None):
    _rect(slide, l, t, w, h, bg or _c(0xe2,0xe8,0xf0))
    if pct > 0:
        _rect(slide, l, t, max(int(w * pct / 100), int(h * 0.5)), h, fg)


def _cell_bg(cell, rgb):
    tc   = cell._tc
    tcPr = tc.find(qn("a:tcPr"))
    if tcPr is None:
        tcPr = etree.SubElement(tc, qn("a:tcPr"))
        tc.insert(0, tcPr)
    for sf in tcPr.findall(qn("a:solidFill")):
        tcPr.remove(sf)
    sf  = etree.SubElement(tcPr, qn("a:solidFill"))
    clr = etree.SubElement(sf,   qn("a:srgbClr"))
    clr.set("val", "%02X%02X%02X" % (rgb[0], rgb[1], rgb[2]))


def _cs(cell, text, size=9, bold=False, color=None, align=PP_ALIGN.CENTER,
        bg=None, italic=False):
    if bg:
        _cell_bg(cell, bg)
    cell.margin_left   = Pt(4)
    cell.margin_right  = Pt(4)
    cell.margin_top    = Pt(3)
    cell.margin_bottom = Pt(3)
    tf = cell.text_frame
    tf.word_wrap = True
    p  = tf.paragraphs[0]
    p.alignment = align
    for run in p.runs:
        run.text = ""
    r = p.runs[0] if p.runs else p.add_run()
    r.text           = str(text)
    r.font.size      = Pt(size)
    r.font.bold      = bold
    r.font.italic    = italic
    r.font.color.rgb = color or C_DARK
    r.font.name      = "Calibri"


def _header(slide, title, sub=None):
    _rect(slide, 0, 0, SW, Inches(0.06), C_BLUE)
    _tb(slide, M, Inches(0.1), SW - 2*M, Inches(0.5),
        title, size=20, bold=True, color=C_DARK)
    if sub:
        _tb(slide, M, Inches(0.58), SW - 2*M, Inches(0.26),
            sub, size=10, color=C_GRAY)
        return Inches(0.92)
    return Inches(0.7)


def _status_bg(status):
    return {"Passed": BG_PASS, "In Progress": BG_PROG}.get(status, BG_NONE)


def _ptw_bg(val):
    if val == "5/5": return BG_PASS
    if val == "—":   return BG_NONE
    try:
        n, _ = val.split("/")
        return BG_PROG if int(n) > 0 else BG_NONE
    except Exception:
        return BG_NONE


def _tfont(n_rows):
    if n_rows <= 16: return 9
    if n_rows <= 22: return 8
    return 7


# ── Slide 1 — Cover ──────────────────────────────────────────────────────────

def _s_cover(prs, data):
    slide = _blank(prs)
    _bg(slide, C_BLUE)
    _rect(slide, 0, 0, SW, Inches(0.5), C_DBLUE)
    _tb(slide, M, Inches(0.1), SW - 2*M, Inches(0.32),
        "NSH SAFETRACK", size=12, bold=True,
        color=_c(0xba,0xcf,0xf8), align=PP_ALIGN.CENTER)

    _tb(slide, M, Inches(1.8), SW - 2*M, Inches(1.2),
        "Trainee Weekly Report",
        size=44, bold=True, color=C_WHITE, align=PP_ALIGN.CENTER)

    _rect(slide, SW/2 - Inches(3), Inches(3.3), Inches(6), Inches(0.025),
          _c(0x3b,0x5f,0xcd))

    ws = data["week_start"].strftime("%d %b")
    we = data["week_end"].strftime("%d %b %Y")
    _tb(slide, M, Inches(3.45), SW - 2*M, Inches(0.5),
        f"Week:  {ws}  –  {we}",
        size=22, color=_c(0xba,0xcf,0xf8), align=PP_ALIGN.CENTER)

    n = len(data["trainees"])
    _tb(slide, M, Inches(4.1), SW - 2*M, Inches(0.4),
        f"{n} Trainee{'s' if n != 1 else ''} Selected",
        size=15, color=C_WHITE, align=PP_ALIGN.CENTER)

    company = data.get("company_name", "")
    if company:
        _tb(slide, M, Inches(4.65), SW - 2*M, Inches(0.35),
            company, size=13, color=_c(0xba,0xcf,0xf8), align=PP_ALIGN.CENTER)

    _rect(slide, 0, SH - Inches(0.4), SW, Inches(0.4), C_DBLUE)
    gen = data["generated_at"].strftime("%d %b %Y,  %H:%M")
    _tb(slide, M, SH - Inches(0.38), SW - 2*M, Inches(0.35),
        f"Generated:  {gen}",
        size=10, color=_c(0x93,0xb0,0xe5), align=PP_ALIGN.CENTER)


# ── Slide 2 — KPI Dashboard ──────────────────────────────────────────────────

def _s_kpi(prs, data):
    slide = _blank(prs)
    _bg(slide, C_LIGHT)
    ws = data["week_start"].strftime("%d %b")
    we = data["week_end"].strftime("%d %b %Y")
    cy = _header(slide, "Weekly Performance Dashboard",
                 f"Week:  {ws} – {we}")

    hse   = data["hse"]
    ptw   = data["ptw_summary"]
    delta = data.get("delta", {})

    TW = Inches(5.85)
    TH = Inches(2.5)
    GX = Inches(0.38)
    GY = Inches(0.28)

    tiles = [
        dict(label="Modules Passed This Week",
             value=delta.get("modules_passed", 0),
             sub=None, delta=None,
             color=C_BLUE, bg=_c(0xef,0xf6,0xff)),
        dict(label="PTW Stages Approved",
             value=ptw.get("approved", 0),
             sub=f"{ptw.get('submitted', 0)} submitted this week",
             delta=delta.get("ptw_stages"),
             color=C_GREEN, bg=_c(0xf0,0xfd,0xf4)),
        dict(label="Total Observations",
             value=hse.get("total_obs", 0),
             sub=f"High-risk: {hse.get('high', 0)}",
             delta=delta.get("obs"),
             color=C_RED, bg=_c(0xff,0xf1,0xf2)),
        dict(label="TBT Sessions",
             value=hse.get("tbt_sessions", 0),
             sub=f"{hse.get('tbt_attend', 0)} attendees",
             delta=delta.get("tbt"),
             color=C_TEAL, bg=_c(0xf0,0xfd,0xfa)),
    ]

    positions = [
        (M,         cy + GY),
        (M+TW+GX,   cy + GY),
        (M,         cy + GY + TH + Inches(0.25)),
        (M+TW+GX,   cy + GY + TH + Inches(0.25)),
    ]

    for tile, (tx, ty) in zip(tiles, positions):
        card = _rect(slide, tx, ty, TW, TH, tile["bg"], tile["color"])
        card.line.width = Pt(1.5)
        _rect(slide, tx, ty, Inches(0.1), TH, tile["color"])
        _tb(slide, tx+Inches(0.22), ty+Inches(0.15),
            TW-Inches(0.35), Inches(0.32),
            tile["label"], size=11, color=C_GRAY)
        _tb(slide, tx+Inches(0.22), ty+Inches(0.46),
            TW-Inches(0.35), Inches(1.1),
            str(tile["value"]), size=54, bold=True, color=tile["color"])
        d   = tile.get("delta")
        sub = tile.get("sub")
        if d is not None:
            sign  = "▲" if d > 0 else ("▼" if d < 0 else "—")
            dc    = C_GREEN if d > 0 else (C_RED if d < 0 else C_GRAY)
            dtxt  = f"{sign} {abs(d)} vs prev week" if d != 0 else "= same as prev week"
            _tb(slide, tx+Inches(0.22), ty+Inches(1.9),
                TW-Inches(0.35), Inches(0.32), dtxt, size=10, color=dc, bold=(d != 0))
            if sub:
                _tb(slide, tx+Inches(0.22), ty+Inches(2.2),
                    TW-Inches(0.35), Inches(0.28), sub, size=9, color=C_GRAY)
        elif sub:
            _tb(slide, tx+Inches(0.22), ty+Inches(1.9),
                TW-Inches(0.35), Inches(0.4), sub, size=11, color=C_GRAY)


# ── Slide 3 — E-Learning Module Overview ─────────────────────────────────────

def _s_lms_overview(prs, data):
    slide = _blank(prs)
    _bg(slide)
    cy = _header(slide, "E-Learning Progress — Module Overview",
                 "Completion status across all 7 PTW training modules")

    trainees = data["trainees"]
    n = len(trainees)
    if not n:
        _tb(slide, M, cy+Inches(0.5), SW-2*M, Inches(1),
            "No trainees selected.", size=14, color=C_GRAY, align=PP_ALIGN.CENTER)
        return

    ROW_H  = Inches(0.72)
    BAR_X  = M + Inches(3.65)
    BAR_W  = Inches(8.0)
    BAR_H  = Inches(0.22)
    LBL_W  = Inches(3.5)

    for mi, name in enumerate(MODULE_SHORT):
        y = cy + Inches(0.12) + ROW_H * mi
        passed  = sum(1 for t in trainees
                      if mi < len(t["modules"]) and t["modules"][mi]["status"] == "Passed")
        in_prog = sum(1 for t in trainees
                      if mi < len(t["modules"]) and t["modules"][mi]["status"] == "In Progress")
        not_s   = n - passed - in_prog
        pct_p   = int(passed  / n * 100) if n else 0
        pct_ip  = int(in_prog / n * 100) if n else 0

        _tb(slide, M, y + Inches(0.06), LBL_W, Inches(0.32),
            f"M{mi+1}  {name}", size=11, bold=True, color=C_DARK)

        bar_y = y + Inches(0.4)
        _rect(slide, BAR_X, bar_y, BAR_W, BAR_H, _c(0xe2,0xe8,0xf0))
        if passed > 0:
            _rect(slide, BAR_X, bar_y, int(BAR_W * passed / n), BAR_H, C_GREEN)
        if in_prog > 0:
            ip_x = BAR_X + int(BAR_W * passed / n)
            _rect(slide, ip_x, bar_y, int(BAR_W * in_prog / n), BAR_H, C_AMBER)

        parts = []
        if passed:  parts.append(f"{passed} Passed ({pct_p}%)")
        if in_prog: parts.append(f"{in_prog} In Progress ({pct_ip}%)")
        if not_s:   parts.append(f"{not_s} Not Started")
        _tb(slide, M, y + Inches(0.37), LBL_W, Inches(0.26),
            "  ·  ".join(parts), size=8, color=C_GRAY)

    # Legend
    lx = M
    ly = SH - Inches(0.38)
    for col, lbl in [(C_GREEN,"Passed"),(C_AMBER,"In Progress"),(_c(0xe2,0xe8,0xf0),"Not Started")]:
        _rect(slide, lx, ly+Inches(0.07), Inches(0.18), Inches(0.18), col)
        _tb(slide, lx+Inches(0.24), ly, Inches(1.4), Inches(0.32), lbl, size=9, color=C_GRAY)
        lx += Inches(1.7)


# ── Slide 4 — E-Learning Per-Trainee Table ───────────────────────────────────

def _s_lms_table(prs, data):
    slide = _blank(prs)
    _bg(slide, C_LIGHT)
    cy = _header(slide, "E-Learning Progress — Per Trainee",
                 "Module completion status per trainee (sorted by progress)")

    trainees = sorted(data["trainees"],
        key=lambda t: (-sum(1 for m in t["modules"] if m["status"] == "Passed"),
                       -sum(1 for m in t["modules"] if m["status"] == "In Progress")))

    n_rows = len(trainees) + 1
    fs     = _tfont(n_rows)
    COL_W  = [Inches(2.5)] + [Inches(0.98)]*7 + [Inches(1.73)]
    tbl_w  = sum(COL_W)
    tbl_l  = (SW - tbl_w) / 2
    tbl_t  = cy + Inches(0.08)
    tbl_h  = SH - tbl_t - Inches(0.15)

    tf  = slide.shapes.add_table(n_rows, 9, tbl_l, tbl_t, tbl_w, tbl_h)
    tbl = tf.table
    for ci, cw in enumerate(COL_W): tbl.columns[ci].width = cw

    for ci, h in enumerate(["Trainee Name"] + [f"M{i+1}" for i in range(7)] + ["Status"]):
        _cs(tbl.cell(0, ci), h, size=fs, bold=True, color=C_WHITE, bg=BG_HDR,
            align=PP_ALIGN.CENTER if ci > 0 else PP_ALIGN.LEFT)

    for ri, t in enumerate(trainees, 1):
        alt = BG_ALT if ri % 2 == 0 else C_WHITE
        _cs(tbl.cell(ri, 0), t["name"], size=fs, bold=True, color=C_DARK,
            align=PP_ALIGN.LEFT, bg=alt)
        for mi in range(7):
            status  = t["modules"][mi]["status"] if mi < len(t["modules"]) else "—"
            display = {"Passed": "✓", "In Progress": "●", "—": "—"}.get(status, status)
            new_w   = mi in t.get("new_passes", [])
            _cs(tbl.cell(ri, 1+mi), ("★" if new_w else "") + display,
                size=fs, bold=(status == "Passed"),
                color=C_DARK, bg=_status_bg(status), align=PP_ALIGN.CENTER)
        passed_count = sum(1 for m in t["modules"] if m["status"] == "Passed")
        ip = next((f"M{i+1}" for i,m in enumerate(t["modules"])
                   if m["status"] == "In Progress"), None)
        if passed_count == 7: summary, sc = "All Passed ✓", C_GREEN
        elif ip:              summary, sc = f"{passed_count}/7  ·  {ip} active", C_AMBER
        elif passed_count:    summary, sc = f"{passed_count}/7 passed", C_DARK
        else:                 summary, sc = "Not started", C_GRAY
        _cs(tbl.cell(ri, 8), summary, size=fs, color=sc, bg=alt,
            bold=(passed_count == 7), align=PP_ALIGN.LEFT)

    _tb(slide, M, SH-Inches(0.28), SW-2*M, Inches(0.25),
        "★ = newly passed this week", size=8, color=C_GRAY)


# ── Slide 5 — Weekly Delta ────────────────────────────────────────────────────

def _s_delta(prs, data):
    slide = _blank(prs)
    _bg(slide)
    ws = data["week_start"].strftime("%d %b")
    we = data["week_end"].strftime("%d %b %Y")
    cy = _header(slide, f"This Week's Progress Changes  —  {ws} – {we}",
                 "New module completions, PTW stage gains, and observations recorded this week")

    trainees = sorted(data["trainees"],
        key=lambda t: -(len(t.get("new_passes", [])) + t.get("ptw_stages_this_week", 0)))

    n_rows = len(trainees) + 1
    fs     = _tfont(n_rows)
    COL_W  = [Inches(2.3), Inches(3.1), Inches(1.9), Inches(1.45), Inches(4.13)]
    tbl_w  = sum(COL_W)
    tbl_l  = (SW - tbl_w) / 2
    tbl_t  = cy + Inches(0.08)
    tbl_h  = SH - tbl_t - Inches(0.15)

    tf  = slide.shapes.add_table(n_rows, 5, tbl_l, tbl_t, tbl_w, tbl_h)
    tbl = tf.table
    for ci, cw in enumerate(COL_W): tbl.columns[ci].width = cw

    for ci, h in enumerate(["Trainee", "New Passes This Week",
                             "PTW Stages ↑", "Obs Count", "Overall Progress"]):
        _cs(tbl.cell(0, ci), h, size=fs, bold=True, color=C_WHITE, bg=BG_HDR,
            align=PP_ALIGN.LEFT if ci in (0, 1, 4) else PP_ALIGN.CENTER)

    for ri, t in enumerate(trainees, 1):
        alt      = BG_ALT if ri % 2 == 0 else C_WHITE
        new_p    = t.get("new_passes", [])
        ptw_w    = t.get("ptw_stages_this_week", 0)
        obs      = t.get("obs_count", 0)

        _cs(tbl.cell(ri,0), t["name"], size=fs, bold=True,
            color=C_DARK, align=PP_ALIGN.LEFT, bg=alt)

        if new_p:
            _cs(tbl.cell(ri,1), "  ".join(f"M{i+1} ✓" for i in new_p),
                size=fs, bold=True, color=C_GREEN,
                bg=BG_PASS, align=PP_ALIGN.LEFT)
        else:
            _cs(tbl.cell(ri,1), "—", size=fs, color=C_MID, bg=alt, align=PP_ALIGN.CENTER)

        _cs(tbl.cell(ri,2), f"+{ptw_w}" if ptw_w else "—",
            size=fs, bold=(ptw_w > 0),
            color=C_BLUE if ptw_w else C_MID,
            bg=BG_POS if ptw_w else alt, align=PP_ALIGN.CENTER)

        _cs(tbl.cell(ri,3), str(obs) if obs else "—",
            size=fs, color=C_RED if obs else C_MID,
            bg=alt, align=PP_ALIGN.CENTER)

        passed = sum(1 for m in t["modules"] if m["status"] == "Passed")
        total  = t.get("ptw_overall", "0/35")
        _cs(tbl.cell(ri,4), f"{passed}/7 modules  ·  {total} PTW stages",
            size=fs, color=C_DARK, bg=alt, align=PP_ALIGN.LEFT)


# ── Slide 6 — PTW Summary ────────────────────────────────────────────────────

def _s_ptw_summary(prs, data):
    slide = _blank(prs)
    _bg(slide, C_LIGHT)
    cy = _header(slide, "PTW Field Training — Weekly Summary",
                 "Permit-to-Work field training  (5 stages × 7 modules = 35 stages per trainee)")

    ptw      = data["ptw_summary"]
    trainees = data["trainees"]

    TW, TH = Inches(2.85), Inches(1.5)
    gap    = Inches(0.2)

    for i, (label, val, color, tile_bg) in enumerate([
        ("Submitted", ptw.get("submitted",0), C_BLUE,  _c(0xef,0xf6,0xff)),
        ("Approved",  ptw.get("approved",0),  C_GREEN, _c(0xf0,0xfd,0xf4)),
        ("Rejected",  ptw.get("rejected",0),  C_RED,   _c(0xff,0xf1,0xf2)),
        ("Pending",   ptw.get("pending",0),   C_AMBER, _c(0xff,0xfd,0xf0)),
    ]):
        tx, ty = M + (TW+gap)*i, cy + Inches(0.15)
        card = _rect(slide, tx, ty, TW, TH, tile_bg, color)
        card.line.width = Pt(1)
        _rect(slide, tx, ty, Inches(0.08), TH, color)
        _tb(slide, tx+Inches(0.18), ty+Inches(0.1),  TW-Inches(0.25), Inches(0.3),
            label, size=10, color=C_GRAY)
        _tb(slide, tx+Inches(0.18), ty+Inches(0.38), TW-Inches(0.25), Inches(0.8),
            str(val), size=38, bold=True, color=color)

    # Cumulative bar
    total_app = sum(
        int(t.get("ptw_overall","0/35").split("/")[0]) for t in trainees
    )
    total_tgt = len(trainees) * 35
    bar_y = cy + TH + Inches(0.5)

    _tb(slide, M, bar_y, SW-2*M, Inches(0.28),
        f"Cumulative Field Stages  —  {total_app} of {total_tgt} total stages approved",
        size=11, bold=True, color=C_DARK)

    pct = int(total_app / total_tgt * 100) if total_tgt else 0
    _bar(slide, M, bar_y+Inches(0.32), SW-2*M, Inches(0.28), pct, C_GREEN)
    _tb(slide, M, bar_y+Inches(0.65), SW-2*M, Inches(0.26),
        f"{total_app} approved  ·  {total_tgt - total_app} remaining  ({pct}%)",
        size=9, color=C_GRAY)

    # Per-trainee mini-bars
    active = [t for t in trainees
              if int(t.get("ptw_overall","0/35").split("/")[0]) > 0]
    if active:
        by = bar_y + Inches(1.05)
        _tb(slide, M, by, SW-2*M, Inches(0.28),
            "Per-Trainee Field Stage Progress", size=11, bold=True, color=C_DARK)
        by += Inches(0.32)
        row_h = Inches(0.38)
        bx    = M + Inches(2.8)
        bw    = SW - 2*M - Inches(3.2)

        for t in active[:12]:
            done = int(t.get("ptw_overall","0/35").split("/")[0])
            pc   = int(done / 35 * 100)
            _tb(slide, M, by, Inches(2.7), row_h, t["name"], size=9, color=C_DARK)
            _bar(slide, bx, by+Inches(0.09), bw, Inches(0.2), pc, C_GREEN)
            _tb(slide, bx+bw+Inches(0.08), by, Inches(0.6), row_h,
                t.get("ptw_overall",""), size=9, bold=True,
                color=C_GREEN if pc >= 50 else C_AMBER)
            by += row_h


# ── Slide 7 — PTW Per-Trainee Table ──────────────────────────────────────────

def _s_ptw_table(prs, data):
    slide = _blank(prs)
    _bg(slide, C_LIGHT)
    cy = _header(slide, "PTW Field Training — Per-Trainee Stage Progress",
                 "Stages completed per module (format: X/5).  Green = complete,  Yellow = in progress")

    trainees = sorted(data["trainees"],
        key=lambda t: -int(t.get("ptw_overall","0/35").split("/")[0]))

    n_rows = len(trainees) + 1
    fs     = _tfont(n_rows)
    COL_W  = [Inches(2.3)] + [Inches(0.9)]*7 + [Inches(0.88), Inches(1.15)]
    tbl_w  = sum(COL_W)
    tbl_l  = (SW - tbl_w) / 2
    tbl_t  = cy + Inches(0.08)
    tbl_h  = SH - tbl_t - Inches(0.15)

    tf  = slide.shapes.add_table(n_rows, 10, tbl_l, tbl_t, tbl_w, tbl_h)
    tbl = tf.table
    for ci, cw in enumerate(COL_W): tbl.columns[ci].width = cw

    for ci, h in enumerate(["Trainee"] + [f"M{i+1}" for i in range(7)] + ["Total","Status"]):
        _cs(tbl.cell(0,ci), h, size=fs, bold=True, color=C_WHITE, bg=BG_HDR,
            align=PP_ALIGN.CENTER)

    for ri, t in enumerate(trainees, 1):
        alt = BG_ALT if ri % 2 == 0 else C_WHITE
        _cs(tbl.cell(ri,0), t["name"], size=fs, bold=True,
            color=C_DARK, align=PP_ALIGN.LEFT, bg=alt)

        pm = t.get("ptw_per_module", ["—"]*7)
        for mi in range(7):
            val = pm[mi] if mi < len(pm) else "—"
            _cs(tbl.cell(ri,1+mi), val, size=fs, bg=_ptw_bg(val), align=PP_ALIGN.CENTER)

        overall = t.get("ptw_overall","0/35")
        try:    done = int(overall.split("/")[0])
        except: done = 0
        pc = int(done / 35 * 100)
        _cs(tbl.cell(ri,8), overall, size=fs, bold=True,
            color=C_GREEN if pc >= 80 else (C_AMBER if pc >= 30 else C_GRAY),
            bg=alt, align=PP_ALIGN.CENTER)

        stages_w = t.get("ptw_stages_this_week", 0)
        if stages_w > 0: status, sbg = "Active",   BG_PASS
        elif done > 0:   status, sbg = "Enrolled", BG_PROG
        else:            status, sbg = "Pending",  BG_NONE
        _cs(tbl.cell(ri,9), status, size=fs, color=C_DARK, bg=sbg, align=PP_ALIGN.CENTER)


# ── Slide 8 — Observations Overview ─────────────────────────────────────────

def _s_obs_overview(prs, data):
    slide = _blank(prs)
    _bg(slide)
    ws = data["week_start"].strftime("%d %b")
    we = data["week_end"].strftime("%d %b %Y")
    cy = _header(slide, "Observations Overview",
                 f"All HSE observations recorded  {ws} – {we}")

    hse = data["hse"]

    TW, TH = Inches(2.28), Inches(1.38)
    gap    = Inches(0.22)

    for i, (label, val, color, tile_bg) in enumerate([
        ("Total",     hse.get("total_obs",0), C_BLUE,   _c(0xef,0xf6,0xff)),
        ("High Risk", hse.get("high",0),       C_RED,    BG_HIGH),
        ("Medium",    hse.get("medium",0),     C_AMBER,  BG_MED),
        ("Low Risk",  hse.get("low",0),        C_GREEN,  BG_LOW),
        ("Positive",  hse.get("positive",0),   C_PURPLE, BG_POS),
    ]):
        tx, ty = M + (TW+gap)*i, cy + Inches(0.15)
        card = _rect(slide, tx, ty, TW, TH, tile_bg, color)
        card.line.width = Pt(1)
        _rect(slide, tx, ty, Inches(0.07), TH, color)
        _tb(slide, tx+Inches(0.14), ty+Inches(0.08), TW-Inches(0.2), Inches(0.28),
            label, size=10, color=C_GRAY)
        _tb(slide, tx+Inches(0.14), ty+Inches(0.34), TW-Inches(0.2), Inches(0.75),
            str(val), size=34, bold=True, color=color)

    # Per-officer table
    div_y = cy + TH + Inches(0.55)
    _tb(slide, M, div_y, SW-2*M, Inches(0.28),
        "Observations per Officer This Week", size=12, bold=True, color=C_DARK)

    officer_obs = sorted(
        [{"name": t["name"], "total": t.get("obs_count",0),
          "high": t.get("obs_high",0)}
         for t in data["trainees"] if t.get("obs_count",0) > 0],
        key=lambda x: -x["total"]
    )

    if officer_obs:
        COL_W = [Inches(3.5), Inches(1.9), Inches(1.9), Inches(5.58)]
        tbl_w = sum(COL_W)
        tbl_l = (SW - tbl_w) / 2
        tbl_t = div_y + Inches(0.33)
        tbl_h = SH - tbl_t - Inches(0.15)
        n_rows= len(officer_obs) + 1
        fs    = _tfont(n_rows)

        tf  = slide.shapes.add_table(n_rows, 4, tbl_l, tbl_t, tbl_w, tbl_h)
        tbl = tf.table
        for ci, cw in enumerate(COL_W): tbl.columns[ci].width = cw

        for ci, h in enumerate(["Officer","Total Obs","High-Risk","Risk Breakdown"]):
            _cs(tbl.cell(0,ci), h, size=fs, bold=True, color=C_WHITE, bg=BG_HDR,
                align=PP_ALIGN.LEFT if ci in (0,3) else PP_ALIGN.CENTER)

        for ri, o in enumerate(officer_obs, 1):
            alt  = BG_ALT if ri%2==0 else C_WHITE
            tot  = o["total"]
            hi   = o["high"]
            low  = tot - hi
            _cs(tbl.cell(ri,0), o["name"], size=fs, bold=True, color=C_DARK,
                align=PP_ALIGN.LEFT, bg=alt)
            _cs(tbl.cell(ri,1), str(tot), size=fs, bold=True, color=C_BLUE,
                align=PP_ALIGN.CENTER, bg=alt)
            _cs(tbl.cell(ri,2), str(hi) if hi else "—", size=fs,
                color=C_RED if hi else C_MID,
                bg=BG_HIGH if hi else alt, align=PP_ALIGN.CENTER)
            parts = []
            if hi:  parts.append(f"{int(hi/tot*100)}% High")
            if low: parts.append(f"{int(low/tot*100)}% Low-Med")
            _cs(tbl.cell(ri,3), "  ·  ".join(parts) or "—", size=fs,
                color=C_GRAY, align=PP_ALIGN.LEFT, bg=alt)
    else:
        _tb(slide, M, div_y+Inches(0.4), SW-2*M, Inches(0.5),
            "No officer observations recorded this week.",
            size=11, color=C_GRAY, align=PP_ALIGN.CENTER)


# ── Slide 9 — Daily Observations Breakdown ───────────────────────────────────

def _s_obs_daily(prs, data):
    slide = _blank(prs)
    _bg(slide, C_LIGHT)
    cy = _header(slide, "Observations — Daily Breakdown",
                 "Summary of all observations recorded each day of the week")

    days = data.get("obs_by_day", [])
    if not days:
        _tb(slide, M, cy+Inches(0.5), SW-2*M, Inches(1),
            "No observations recorded this week.", size=14,
            color=C_GRAY, align=PP_ALIGN.CENTER)
        return

    n_rows = len(days) + 2
    COL_W  = [Inches(1.75), Inches(0.95), Inches(0.82), Inches(0.82), Inches(0.82),
              Inches(0.95), Inches(0.82), Inches(1.05), Inches(4.5)]
    tbl_w  = sum(COL_W)
    tbl_l  = (SW - tbl_w) / 2
    tbl_t  = cy + Inches(0.08)
    tbl_h  = SH - tbl_t - Inches(0.15)
    fs     = _tfont(n_rows)

    tf  = slide.shapes.add_table(n_rows, 9, tbl_l, tbl_t, tbl_w, tbl_h)
    tbl = tf.table
    for ci, cw in enumerate(COL_W): tbl.columns[ci].width = cw

    for ci, h in enumerate(["Day","Total","High","Medium","Low","Positive","TBT","Attend.","Key Observations"]):
        _cs(tbl.cell(0,ci), h, size=fs, bold=True, color=C_WHITE, bg=BG_HDR,
            align=PP_ALIGN.LEFT if ci in (0,8) else PP_ALIGN.CENTER)

    totals = {k:0 for k in ["total","high","medium","low","positive","tbt","tbt_attend"]}

    for ri, day in enumerate(days, 1):
        alt = BG_ALT if ri%2==0 else C_WHITE
        v   = [day.get("total",0), day.get("high",0), day.get("medium",0),
               day.get("low",0),   day.get("positive",0),
               day.get("tbt",0),   day.get("tbt_attend",0)]
        for k, val in zip(["total","high","medium","low","positive","tbt","tbt_attend"], v):
            totals[k] += val

        _cs(tbl.cell(ri,0), day.get("date_label",""), size=fs, bold=True,
            color=C_DARK, align=PP_ALIGN.LEFT, bg=alt)
        bgs = [alt, BG_HIGH if v[1] else alt, BG_MED if v[2] else alt,
               BG_LOW if v[3] else alt, BG_POS if v[4] else alt, alt, alt]
        for ci2, (val, bg) in enumerate(zip(v, bgs), 1):
            _cs(tbl.cell(ri,ci2), str(val) if val else "—", size=fs,
                color=C_DARK if val else C_MID, bg=bg, align=PP_ALIGN.CENTER)
        key = "  ·  ".join(day.get("key_obs",[])[:2])
        _cs(tbl.cell(ri,8), key[:65] if key else "—", size=max(fs-1,7),
            color=C_GRAY, align=PP_ALIGN.LEFT, bg=alt)

    # Totals row
    lr = len(days) + 1
    _cs(tbl.cell(lr,0), "WEEKLY TOTAL", size=fs, bold=True,
        color=C_WHITE, bg=C_BLUE, align=PP_ALIGN.LEFT)
    for ci2, v in enumerate([totals["total"], totals["high"], totals["medium"],
                              totals["low"], totals["positive"],
                              totals["tbt"], totals["tbt_attend"]], 1):
        _cs(tbl.cell(lr,ci2), str(v), size=fs, bold=True,
            color=C_WHITE, bg=C_BLUE, align=PP_ALIGN.CENTER)
    _cs(tbl.cell(lr,8), "", bg=C_BLUE)


# ── Slide 10 — TBT Sessions ──────────────────────────────────────────────────

def _s_tbt(prs, data):
    slide = _blank(prs)
    _bg(slide)
    hse = data["hse"]
    cy  = _header(slide, "Toolbox Talk (TBT) Sessions",
                  f"Week total:  {hse.get('tbt_sessions',0)} sessions  ·  {hse.get('tbt_attend',0)} attendees")

    sessions = data.get("tbt_sessions", [])
    if not sessions:
        _tb(slide, M, cy+Inches(0.5), SW-2*M, Inches(1),
            "No TBT sessions recorded this week.", size=14,
            color=C_GRAY, align=PP_ALIGN.CENTER)
        return

    n_rows = len(sessions) + 1
    fs     = _tfont(n_rows)
    COL_W  = [Inches(0.45), Inches(4.1), Inches(2.75), Inches(2.75), Inches(1.93)]
    tbl_w  = sum(COL_W)
    tbl_l  = (SW - tbl_w) / 2
    tbl_t  = cy + Inches(0.08)
    tbl_h  = SH - tbl_t - Inches(0.15)

    tf  = slide.shapes.add_table(n_rows, 5, tbl_l, tbl_t, tbl_w, tbl_h)
    tbl = tf.table
    for ci, cw in enumerate(COL_W): tbl.columns[ci].width = cw

    hdr_bg = _c(0x0d,0x94,0x88)
    for ci, h in enumerate(["#","Topic","Officer","Location","Attendance"]):
        _cs(tbl.cell(0,ci), h, size=fs, bold=True, color=C_WHITE, bg=hdr_bg,
            align=PP_ALIGN.LEFT if ci == 1 else PP_ALIGN.CENTER)

    for ri, s in enumerate(sessions, 1):
        alt = BG_ALT if ri%2==0 else C_WHITE
        _cs(tbl.cell(ri,0), str(ri), size=fs, color=C_GRAY,
            align=PP_ALIGN.CENTER, bg=alt)
        _cs(tbl.cell(ri,1), s.get("topic","—"), size=fs, color=C_DARK,
            align=PP_ALIGN.LEFT, bg=alt)
        _cs(tbl.cell(ri,2), s.get("officer","—"), size=fs, color=C_DARK,
            align=PP_ALIGN.LEFT, bg=alt)
        _cs(tbl.cell(ri,3), s.get("location","—"), size=fs, color=C_GRAY,
            align=PP_ALIGN.LEFT, bg=alt)
        att = s.get("attend", 0)
        _cs(tbl.cell(ri,4), str(att) if att else "—", size=fs, bold=(att > 0),
            color=C_TEAL if att else C_MID, align=PP_ALIGN.CENTER, bg=alt)


# ── Slide 11 — Week Summary ───────────────────────────────────────────────────

def _s_summary(prs, data):
    slide = _blank(prs)
    _bg(slide, C_BLUE)
    ws = data["week_start"].strftime("%d %b")
    we = data["week_end"].strftime("%d %b %Y")

    _rect(slide, 0, 0, SW, Inches(1.1), C_DBLUE)
    _tb(slide, M, Inches(0.15), SW-2*M, Inches(0.44),
        "Week Summary", size=22, bold=True, color=C_WHITE)
    _tb(slide, M, Inches(0.6), SW-2*M, Inches(0.35),
        f"{ws} – {we}  ·  {len(data['trainees'])} Trainees",
        size=13, color=_c(0xba,0xcf,0xf8))

    hse   = data["hse"]
    ptw   = data["ptw_summary"]
    delta = data.get("delta", {})

    bullets = []
    dp = delta.get("modules_passed", 0)
    if dp:
        bullets.append((C_PURPLE, f"{dp} module completion{'s' if dp!=1 else ''} recorded this week"))

    app = ptw.get("approved", 0)
    if app:
        bullets.append((C_GREEN, f"{app} PTW field stage{'s' if app!=1 else ''} approved"))

    rej = ptw.get("rejected", 0)
    if rej:
        bullets.append((C_AMBER, f"{rej} PTW submission{'s' if rej!=1 else ''} rejected — follow-up required"))

    tot = hse.get("total_obs", 0)
    hi  = hse.get("high", 0)
    if tot:
        suffix = f"  —  {hi} high-risk" if hi else ""
        bullets.append((C_RED if hi else C_BLUE,
                        f"{tot} HSE observation{'s' if tot!=1 else ''} recorded{suffix}"))

    tbt = hse.get("tbt_sessions", 0)
    att = hse.get("tbt_attend", 0)
    if tbt:
        bullets.append((C_TEAL,
                        f"{tbt} TBT session{'s' if tbt!=1 else ''} conducted  ·  {att} attendees"))

    jso = hse.get("jso_closures", 0)
    if jso:
        bullets.append((C_MID, f"{jso} JSO closure{'s' if jso!=1 else ''} recorded"))

    if not bullets:
        bullets.append((C_MID, "No significant activity recorded this week."))

    by = Inches(1.28)
    for color, text in bullets:
        _rect(slide, M, by+Inches(0.2), Inches(0.08), Inches(0.08), color)
        _tb(slide, M+Inches(0.22), by, SW-2*M-Inches(0.3), Inches(0.62),
            text, size=15, color=C_WHITE)
        by += Inches(0.65)

    _rect(slide, 0, SH-Inches(0.38), SW, Inches(0.38), C_DBLUE)
    gen = data["generated_at"].strftime("%d %b %Y,  %H:%M")
    _tb(slide, M, SH-Inches(0.36), SW-2*M, Inches(0.33),
        f"NSH SafeTrack  ·  Generated: {gen}",
        size=9, color=_c(0x93,0xb0,0xe5), align=PP_ALIGN.CENTER)


# ── Slide 2 — Evaluation Framework ──────────────────────────────────────────

def _s_intro(prs, data):
    slide = _blank(prs)
    _bg(slide, C_LIGHT)

    _rect(slide, 0, 0, SW, Inches(1.05), C_BLUE)
    _tb(slide, M, Inches(0.12), SW-2*M, Inches(0.52),
        "Evaluation Framework", size=26, bold=True, color=C_WHITE)
    _tb(slide, M, Inches(0.64), SW-2*M, Inches(0.3),
        "Trainees are assessed across three integrated methods throughout the programme",
        size=11, color=_c(0xba,0xcf,0xf8))

    methods = [
        {
            "icon": "01",
            "title": "E-Learning  (LMS)",
            "color": C_BLUE,
            "tile_bg": _c(0xef,0xf6,0xff),
            "accent": _c(0xbe,0xd7,0xfb),
            "lines": [
                "7 theory modules delivered via the online learning platform.",
                "Each module ends with a graded quiz; a passing score marks the module complete.",
                "Modules: Foundation  ·  Hot Work  ·  Work at Height  ·  Confined Spaces",
                "         Lifting & Equipment  ·  Electrical & Radiation  ·  Governance",
            ],
            "metric": "7 Modules",
        },
        {
            "icon": "02",
            "title": "PTW Field Training",
            "color": C_GREEN,
            "tile_bg": _c(0xf0,0xfd,0xf4),
            "accent": _c(0xbb,0xf7,0xd0),
            "lines": [
                "Hands-on Permit-to-Work training assessed in the field by a supervisor.",
                "5 practical stages per module × 7 modules = 35 stages per trainee.",
                "Each stage is submitted by the trainee and approved / rejected by the supervisor.",
                "Progress is tracked in real time via the SafeTrack web platform.",
            ],
            "metric": "35 Stages",
        },
        {
            "icon": "03",
            "title": "HSE Observations",
            "color": C_TEAL,
            "tile_bg": _c(0xf0,0xfd,0xfa),
            "accent": _c(0x99,0xf6,0xe4),
            "lines": [
                "Daily safety observations submitted via the SafeTrack mobile application.",
                "Classified by risk level: High / Medium / Low / Positive.",
                "Includes Toolbox Talks (TBT) conducted by safety officers on site.",
                "JSO closures and officer activity are also captured and reported.",
            ],
            "metric": "Daily Field",
        },
    ]

    card_w = Inches(3.9)
    gap    = Inches(0.265)
    card_h = Inches(5.75)
    start_x = (SW - (card_w * 3 + gap * 2)) / 2
    top_y  = Inches(1.18)

    for i, m in enumerate(methods):
        cx = start_x + i * (card_w + gap)
        _rect(slide, cx, top_y, card_w, card_h, m["tile_bg"],
              border=m["accent"], bw=1)
        _rect(slide, cx, top_y, Inches(0.1), card_h, m["color"])

        icon_x = cx + Inches(0.2)
        _rect(slide, icon_x, top_y+Inches(0.18), Inches(0.5), Inches(0.5), m["color"])
        _tb(slide, icon_x, top_y+Inches(0.2), Inches(0.5), Inches(0.46),
            m["icon"], size=14, bold=True, color=C_WHITE, align=PP_ALIGN.CENTER)

        _tb(slide, cx+Inches(0.2), top_y+Inches(0.8), card_w-Inches(0.3), Inches(0.4),
            m["title"], size=14, bold=True, color=m["color"])

        _rect(slide, cx+Inches(0.2), top_y+Inches(1.25), card_w-Inches(0.4), Inches(0.025),
              m["accent"])

        for li, line in enumerate(m["lines"]):
            ly = top_y + Inches(1.38) + li * Inches(0.72)
            _rect(slide, cx+Inches(0.28), ly+Inches(0.18),
                  Inches(0.07), Inches(0.07), m["color"])
            _tb(slide, cx+Inches(0.45), ly, card_w-Inches(0.6), Inches(0.68),
                line, size=9, color=C_DARK)

        _rect(slide, cx+Inches(0.2), top_y+card_h-Inches(0.55),
              card_w-Inches(0.4), Inches(0.38), m["color"])
        _tb(slide, cx+Inches(0.2), top_y+card_h-Inches(0.52),
            card_w-Inches(0.4), Inches(0.35),
            m["metric"], size=13, bold=True, color=C_WHITE, align=PP_ALIGN.CENTER)


# ── Slide 3 — Report Contents ────────────────────────────────────────────────

def _s_contents(prs, data):
    slide = _blank(prs)
    _bg(slide, C_LIGHT)

    _rect(slide, 0, 0, SW, Inches(0.9), C_BLUE)
    _tb(slide, M, Inches(0.1), SW - 2*M, Inches(0.48),
        "What's in This Report", size=22, bold=True, color=C_WHITE)
    _tb(slide, M, Inches(0.58), SW - 2*M, Inches(0.28),
        "A guide to each section — what it shows and why it matters",
        size=10, color=_c(0xba, 0xcf, 0xf8))

    sections = [
        (C_PURPLE, "KPI Dashboard",
         "Four headline numbers for the week: module completions, PTW stages approved, "
         "total observations, and TBT sessions — with change vs. prior week."),
        (C_BLUE,   "E-Learning Module Overview",
         "Bar chart showing how many trainees passed each of the 7 theory modules on the "
         "LMS platform. Quickly identifies which modules still need attention."),
        (C_BLUE,   "Per-Trainee E-Learning Table",
         "Row-by-row breakdown of every trainee's status across all 7 modules "
         "(Passed / In Progress / Not Started). Stars (★) mark modules passed this week."),
        (C_TEAL,   "Weekly Progress Delta",
         "Side-by-side view of what changed this week: new module passes, "
         "PTW stages completed, observations submitted, and TBT sessions held."),
        (C_GREEN,  "PTW Field Training — Summary",
         "Permit-to-Work field training overview: how many stages were submitted, "
         "approved, rejected, or pending, plus a cumulative progress bar per trainee."),
        (C_GREEN,  "Per-Trainee PTW Stages",
         "Detailed grid showing each trainee's progress through 5 stages × 7 modules "
         "(35 total field stages). Color-coded: green = complete, yellow = in progress."),
        (C_TEAL,   "HSE Observations — Overview",
         "Total safety observations for the week broken down by risk level "
         "(High / Medium / Low / Positive) and per safety officer."),
        (C_TEAL,   "Observations — Daily Breakdown",
         "Day-by-day table of observations, TBT sessions, and attendance counts, "
         "with key observation highlights for each day of the week."),
        (C_AMBER,  "Toolbox Talk (TBT) Sessions",
         "List of all safety briefings conducted: topic, officer, location, and number "
         "of attendees. Confirms HSE engagement on site."),
    ]

    # Two-column layout
    col_w   = (SW - 3 * M) / 2
    col_gap = M
    row_h   = Inches(0.72)
    top_y   = Inches(1.0)

    for i, (color, title, desc) in enumerate(sections):
        col  = i % 2
        row  = i // 2
        cx   = M + col * (col_w + col_gap)
        cy   = top_y + row * row_h

        _rect(slide, cx, cy + Inches(0.1), Inches(0.06), Inches(0.46), color)
        _tb(slide, cx + Inches(0.14), cy + Inches(0.08),
            col_w - Inches(0.2), Inches(0.26),
            title, size=10, bold=True, color=color)
        _tb(slide, cx + Inches(0.14), cy + Inches(0.32),
            col_w - Inches(0.2), Inches(0.36),
            desc, size=8, color=C_GRAY)

        if i < len(sections) - 2:
            _rect(slide, cx, cy + row_h - Inches(0.02),
                  col_w, Inches(0.01), _c(0xe2, 0xe8, 0xf0))

    # Last row (index 8) sits in col 0 — add Week Summary note on col 1
    last_row = (len(sections) - 1) // 2
    cx2 = M + (col_w + col_gap)
    cy2 = top_y + last_row * row_h
    _rect(slide, cx2, cy2 + Inches(0.1), Inches(0.06), Inches(0.46), C_DARK)
    _tb(slide, cx2 + Inches(0.14), cy2 + Inches(0.08),
        col_w - Inches(0.2), Inches(0.26),
        "Week Summary", size=10, bold=True, color=C_DARK)
    _tb(slide, cx2 + Inches(0.14), cy2 + Inches(0.32),
        col_w - Inches(0.2), Inches(0.36),
        "Closing slide with the week's key highlights in bullet form — "
        "suitable for a one-page executive summary.", size=8, color=C_GRAY)


# ── Main entry point ──────────────────────────────────────────────────────────

def build_report(data: dict) -> bytes:
    """
    Build a complete 13-slide PPTX from structured data dict.

    Required keys: week_start, week_end, company_name, generated_at,
    trainees, hse, ptw_summary, obs_by_day, tbt_sessions, delta
    """
    prs = Presentation()
    prs.slide_width  = SW
    prs.slide_height = SH

    _s_cover(prs, data)
    _s_intro(prs, data)
    _s_contents(prs, data)
    _s_kpi(prs, data)
    _s_lms_overview(prs, data)
    _s_lms_table(prs, data)
    _s_delta(prs, data)
    _s_ptw_summary(prs, data)
    _s_ptw_table(prs, data)
    _s_obs_overview(prs, data)
    _s_obs_daily(prs, data)
    _s_tbt(prs, data)
    _s_summary(prs, data)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
