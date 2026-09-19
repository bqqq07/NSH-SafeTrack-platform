"""
Trainee Weekly Report Generator
Parses: progress CSV, PTW Weekly PDF, HSE Weekly PDF, daily observation PDFs
Produces: updated PPTX (binary)
"""

import io, csv, re, os, copy
from datetime import datetime
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE

# Module names (M1–M7) — update here if names change
MODULE_NAMES = [
    "Foundation — PTW System & Documents",
    "Hot Work",
    "Work at Height & Grating",
    "Confined Spaces & Excavations",
    "Lifting & Heavy Equipment",
    "Electrical Isolation & Radiography",
    "Governance & Permit Sections",
]


# ─────────────────────────────────────────────────────────────────────
# CSV PARSING  (progress_YYYYMMDD.csv)
# ─────────────────────────────────────────────────────────────────────

def parse_progress_csv(content: bytes) -> dict:
    """Return list of real trainee dicts with module progress."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(text.splitlines())
    rows = list(reader)
    if not rows:
        return {"trainees": []}

    STATUS_MAP = {"اجتاز": "Passed", "جارٍ": "In Progress", "لم يبدأ": "—", "": "—"}

    trainees = []
    for row in rows[1:]:          # skip header
        if len(row) < 4:
            continue
        name = row[0].strip()
        if not name or name.lower().startswith("test") or name == "الاسم":
            continue

        modules = []
        for i in range(7):
            score_idx  = 4 + i * 2
            status_idx = 5 + i * 2
            score  = row[score_idx].strip()  if score_idx  < len(row) else ""
            status = row[status_idx].strip() if status_idx < len(row) else ""
            modules.append({
                "score":  score,
                "status": STATUS_MAP.get(status, "—"),
            })
        trainees.append({"name": name, "modules": modules})

    return {"trainees": trainees}


# ─────────────────────────────────────────────────────────────────────
# PTW PDF PARSING  (PTW Weekly Report — …pdf)
# ─────────────────────────────────────────────────────────────────────

def parse_ptw_pdf(content: bytes) -> dict:
    """Return PTW field-training stats and per-trainee rows."""
    try:
        import pdfplumber
    except ImportError:
        return _ptw_empty()

    result = {
        "week_label": "",
        "active":     0,
        "submitted":  0,
        "approved":   0,
        "rejected":   0,
        "trainees":   [],
    }

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        full_text = "\n".join(p.extract_text() or "" for p in pdf.pages)

    # Week label
    m = re.search(r"Week:\s*(.+?)(?:\n|$)", full_text)
    if m:
        result["week_label"] = m.group(1).strip()

    # Header numbers  e.g.  "12 6 6 0\nActive Trainees Submissions …"
    m = re.search(r"(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\n.*Active Trainees", full_text)
    if m:
        result["active"]    = int(m.group(1))
        result["submitted"] = int(m.group(2))
        result["approved"]  = int(m.group(3))
        result["rejected"]  = int(m.group(4))

    # Per-trainee rows:  "1 Name Module N  sub  app  rej  pend  X/35 (P%)"
    pat = re.compile(
        r"^\s*(\d+)\s+(.+?)\s+(Module\s+\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d/]+)\s*\((\d+)%\)",
        re.MULTILINE
    )
    for m in pat.finditer(full_text):
        name     = m.group(2).strip()
        cur_mod  = m.group(3).strip()
        sub_w    = int(m.group(4))
        app_w    = int(m.group(5))
        rej_w    = int(m.group(6))
        pend     = int(m.group(7))
        overall  = m.group(8)          # e.g. "6/35"
        pct      = int(m.group(9))

        total_approved = int(overall.split("/")[0])
        per_module     = _derive_module_stages(cur_mod, total_approved)

        result["trainees"].append({
            "name":       name,
            "cur_module": cur_mod,
            "sub_week":   sub_w,
            "app_week":   app_w,
            "rej_week":   rej_w,
            "pending":    pend,
            "overall":    overall,
            "pct":        pct,
            "per_module": per_module,
            "active":     sub_w > 0 or app_w > 0,
        })

    return result


def _ptw_empty():
    return {"week_label": "", "active": 0, "submitted": 0, "approved": 0,
            "rejected": 0, "trainees": []}


def _derive_module_stages(cur_mod_str: str, total_approved: int) -> list:
    """Return list of 7 strings like '3/5' or '—' or '5/5'."""
    m = re.search(r"(\d+)", cur_mod_str)
    current = int(m.group(1)) if m else 1
    stages  = []
    remaining = total_approved
    for i in range(7):
        if i + 1 < current:
            stages.append("5/5")
            remaining -= 5
        elif i + 1 == current:
            stages.append(f"{max(0, remaining)}/5")
            remaining = 0
        else:
            stages.append("—")
    return stages


# ─────────────────────────────────────────────────────────────────────
# HSE PDF PARSING  (HSE Weekly Report — …pdf)
# ─────────────────────────────────────────────────────────────────────

def parse_hse_pdf(content: bytes) -> dict:
    """Return summary stats from the HSE weekly report."""
    result = {
        "week_label":   "",
        "total_obs":    0,
        "high_risk":    0,
        "jso_closures": 0,
        "tbt_sessions": 0,
        "tbt_attend":   0,
        "officers_active":  0,
        "officers_total":   0,
    }
    try:
        import pdfplumber
    except ImportError:
        return result

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        full_text = "\n".join(p.extract_text() or "" for p in pdf.pages)

    # Week label
    m = re.search(r"Week:\s*(.+?)(?:\n|$)", full_text)
    if m:
        result["week_label"] = m.group(1).strip()

    # Summary line:  "15 90 0 0 5"  then "/19" on next line
    m = re.search(r"(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\n\s*/(\d+)", full_text)
    if m:
        result["officers_active"]  = int(m.group(1))
        result["total_obs"]        = int(m.group(2))
        result["high_risk"]        = int(m.group(3))
        result["jso_closures"]     = int(m.group(4))
        result["tbt_sessions"]     = int(m.group(5))
        result["officers_total"]   = int(m.group(6))

    # TBT attendance  "TBTs (24 attend.)"
    m = re.search(r"TBTs\s*\((\d+)\s*attend", full_text, re.IGNORECASE)
    if m:
        result["tbt_attend"] = int(m.group(1))

    return result


# ─────────────────────────────────────────────────────────────────────
# DAILY OBSERVATION PDF PARSING  (observations_YYYY-MM-DD.pdf)
# ─────────────────────────────────────────────────────────────────────

def parse_obs_pdf(content: bytes) -> dict:
    """
    Parse one daily observations PDF using table extraction.
    Returns per-day stats + SGL sessions list.
    """
    result = {
        "date":         "",
        "date_label":   "",
        "total_obs":    0,
        "sgl_count":    0,
        "sgl_attend":   0,
        "high":         0,
        "medium":       0,
        "low":          0,
        "positive":     0,
        "key_obs":      [],
        "sgl_sessions": [],
    }
    try:
        import pdfplumber
    except ImportError:
        return result

    all_text   = []
    all_tables = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            all_text.append(page.extract_text() or "")
            all_tables.extend(page.extract_tables() or [])

    full_text = "\n".join(all_text)

    # ── Header  "Date: 08 Sep 2026 · Observations: 31 · SGL: 4"  ────
    m = re.search(
        r"Date:\s*(\d+\s+\w+\s+\d+)\s*[·.]\s*Observations:\s*(\d+)\s*[·.]\s*SGL:\s*(\d+)",
        full_text,
    )
    if m:
        result["date"]      = m.group(1).strip()
        result["total_obs"] = int(m.group(2))
        result["sgl_count"] = int(m.group(3))
        try:
            dt = datetime.strptime(result["date"], "%d %b %Y")
            result["date_label"] = dt.strftime("%a %d %b")
        except ValueError:
            result["date_label"] = result["date"]

    # ── Classify tables by their header row ──────────────────────────
    SGL_HEADERS  = {"conducted by", "topic", "sn"}
    OBS_HEADERS  = {"observation description", "risk", "observed by"}

    for table in all_tables:
        if not table or not table[0]:
            continue
        header = {(c or "").lower() for c in table[0]}

        if OBS_HEADERS & header:
            # Observation table: columns include Risk (index varies)
            try:
                ri = next(i for i, c in enumerate(table[0])
                          if c and c.lower() == "risk")
                di = next(i for i, c in enumerate(table[0])
                          if c and "description" in c.lower())
                ti = next((i for i, c in enumerate(table[0])
                           if c and c.lower() == "type"), None)
            except StopIteration:
                continue

            for row in table[1:]:
                if not row or len(row) <= ri:
                    continue
                risk = (row[ri] or "").strip().upper()
                if   risk == "H": result["high"]     += 1
                elif risk == "M": result["medium"]   += 1
                elif risk == "L": result["low"]      += 1

                # Positive = row whose Type contains "Positive"
                if ti is not None and ti < len(row):
                    t_val = (row[ti] or "").strip()
                    if "positive" in t_val.lower():
                        result["positive"] += 1

                # Key observations
                if di < len(row) and len(result["key_obs"]) < 4:
                    desc = (row[di] or "").replace("\n", " ").strip()
                    if desc and len(desc) > 15:
                        result["key_obs"].append(desc[:80])

        elif SGL_HEADERS & header:
            # SGL session table
            try:
                sn_i  = next(i for i, c in enumerate(table[0])
                             if c and c.lower() == "sn")
                top_i = next(i for i, c in enumerate(table[0])
                             if c and c.lower() == "topic")
                loc_i = next(i for i, c in enumerate(table[0])
                             if c and "location" in c.lower())
                off_i = next(i for i, c in enumerate(table[0])
                             if c and "conducted" in c.lower())
                att_i = next(i for i, c in enumerate(table[0])
                             if c and "att" in c.lower())
            except StopIteration:
                continue

            total_att = 0
            for row in table[1:]:
                if not row or not (row[sn_i] or "").strip():
                    continue
                topic    = (row[top_i] or "").replace("\n", " ").strip()
                location = (row[loc_i] or "").replace("\n", " ").strip()
                officer  = (row[off_i] or "").replace("\n", " ").strip()
                try:
                    att = int((row[att_i] or "0").strip())
                except ValueError:
                    att = 0
                total_att += att
                result["sgl_sessions"].append({
                    "num":      (row[sn_i] or "").strip(),
                    "topic":    topic,
                    "location": location,
                    "officer":  officer,
                    "attend":   att,
                })
            if result["sgl_attend"] == 0:
                result["sgl_attend"] = total_att

    return result


# ─────────────────────────────────────────────────────────────────────
# PDF → IMAGES  (for observation slides)
# ─────────────────────────────────────────────────────────────────────

def pdf_to_images(content: bytes, dpi: int = 150) -> list:
    """Convert every page of a PDF to a PNG bytes object."""
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError:
            return []

    doc  = fitz.open(stream=content, filetype="pdf")
    zoom = dpi / 72
    mat  = fitz.Matrix(zoom, zoom)
    imgs = []
    for page in doc:
        pix = page.get_pixmap(matrix=mat, alpha=False)
        imgs.append(pix.tobytes("png"))
    return imgs


# ─────────────────────────────────────────────────────────────────────
# PPTX HELPERS
# ─────────────────────────────────────────────────────────────────────

from lxml import etree

_A  = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R  = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

def _qn(tag):
    ns = {"a": _A}
    prefix, local = tag.split(":")
    return "{%s}%s" % (ns[prefix], local)


def _set_cell_bg(cell, r: int, g: int, b: int):
    """Set table cell solid background colour via direct XML."""
    from lxml import etree as _et
    NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
    tc   = cell._tc
    tcPr = tc.find(f"{{{NS_A}}}tcPr")
    if tcPr is None:
        tcPr = _et.Element(f"{{{NS_A}}}tcPr")
        tc.insert(0, tcPr)
    for sf in tcPr.findall(f"{{{NS_A}}}solidFill"):
        tcPr.remove(sf)
    sf = _et.SubElement(tcPr, f"{{{NS_A}}}solidFill")
    sc = _et.SubElement(sf, f"{{{NS_A}}}srgbClr")
    sc.set("val", f"{r:02X}{g:02X}{b:02X}")


# Status → background colour (R, G, B)
_STATUS_BG = {
    "Passed":      (0xBB, 0xF7, 0xD0),   # light green
    "In Progress": (0xFE, 0xF0, 0x8A),   # light yellow
    "—":           (0xF1, 0xF5, 0xF9),   # light gray
}

# PTW per-module stage value → background colour
def _ptw_stage_bg(val: str):
    if val == "—":
        return (0xF1, 0xF5, 0xF9)
    if val == "5/5":
        return (0xBB, 0xF7, 0xD0)   # fully done → green
    try:
        n, _ = val.split("/")
        return (0xFE, 0xF0, 0x8A) if int(n) > 0 else (0xF1, 0xF5, 0xF9)
    except Exception:
        return (0xF1, 0xF5, 0xF9)


def _update_cell(cell, new_text: str):
    """
    Replace a table cell's text while keeping the first run's font/style.
    Supports '\\n' as paragraph separator.
    """
    tf    = cell.text_frame
    txBody = tf._txBody

    # Capture first paragraph / first run formatting
    existing = txBody.findall(_qn("a:p"))
    saved_pPr = saved_rPr = None
    if existing:
        pPr = existing[0].find(_qn("a:pPr"))
        if pPr is not None:
            saved_pPr = copy.deepcopy(pPr)
        runs = existing[0].findall(_qn("a:r"))
        if runs:
            rPr = runs[0].find(_qn("a:rPr"))
            if rPr is not None:
                saved_rPr = copy.deepcopy(rPr)

    # Remove all paragraphs
    for p in existing:
        txBody.remove(p)

    # Re-create paragraphs
    lines = new_text.split("\n") if new_text else [""]
    for line in lines:
        p = etree.SubElement(txBody, _qn("a:p"))
        if saved_pPr is not None:
            p.append(copy.deepcopy(saved_pPr))
        r = etree.SubElement(p, _qn("a:r"))
        if saved_rPr is not None:
            r.append(copy.deepcopy(saved_rPr))
        t = etree.SubElement(r, _qn("a:t"))
        t.text = line


def _update_shape_para(shape, para_idx: int, new_text: str):
    """Update a specific paragraph in a shape's text frame (first run only)."""
    if not shape.has_text_frame:
        return
    tf = shape.text_frame
    if para_idx >= len(tf.paragraphs):
        return
    para = tf.paragraphs[para_idx]
    if para.runs:
        para.runs[0].text = new_text
        for run in para.runs[1:]:
            run.text = ""
    else:
        para.add_run().text = new_text


def _find_shape(slide, name: str):
    for s in slide.shapes:
        if s.name == name:
            return s
    return None


def _replace_slide_with_image(slide, img_bytes: bytes, prs):
    """Remove existing pictures and embed the observation image at fixed position/size."""
    from pptx.util import Inches

    # Remove existing picture shapes only (keep header text/shapes)
    to_rm = [s for s in slide.shapes if s.shape_type == 13]  # PICTURE
    for sh in to_rm:
        sh.element.getparent().remove(sh.element)

    # Fixed width/height, bottom-aligned (top = slide_height - image_height)
    img_h = Inches(4.63)
    img_stream = io.BytesIO(img_bytes)
    slide.shapes.add_picture(img_stream,
                             Inches(1.00), prs.slide_height - img_h,
                             Inches(8.22), img_h)


def _move_slide(prs, from_idx: int, to_idx: int):
    """Move slide in the slide order list."""
    sldIdLst = prs.slides._sldIdLst
    el = sldIdLst[from_idx]
    sldIdLst.remove(el)
    sldIdLst.insert(to_idx, el)


def _add_blank_image_slide(prs, img_bytes: bytes):
    """Add a new blank slide with a full-slide image at the END of presentation."""
    from pptx.util import Emu
    from pptx.dml.color import RGBColor

    # Use blank layout (index 6 in most themes)
    blank_layout = None
    for layout in prs.slide_layouts:
        if layout.name.lower() in ("blank", "title only"):
            blank_layout = layout
            break
    if blank_layout is None:
        blank_layout = prs.slide_layouts[6]

    new_slide = prs.slides.add_slide(blank_layout)

    # Remove placeholder shapes that come from layout
    for ph in list(new_slide.placeholders):
        ph.element.getparent().remove(ph.element)

    from pptx.util import Inches
    img_h = Inches(4.63)
    img_stream = io.BytesIO(img_bytes)
    new_slide.shapes.add_picture(img_stream,
                                 Inches(1.00), prs.slide_height - img_h,
                                 Inches(8.22), img_h)
    return new_slide


# ─────────────────────────────────────────────────────────────────────
# MAIN GENERATOR
# ─────────────────────────────────────────────────────────────────────

OBS_SLIDE_FIRST = 14   # 0-indexed  (slide 15 in 1-based)
OBS_SLIDE_LAST  = 36   # 0-indexed  (slide 37 in 1-based)
OBS_SLIDE_COUNT = OBS_SLIDE_LAST - OBS_SLIDE_FIRST + 1   # 23


# ─────────────────────────────────────────────────────────────────────
# MODULE STATS SLIDE BUILDER  (replaces slide 5)
# ─────────────────────────────────────────────────────────────────────

def _rebuild_module_stats_slide(slide, csv_trainees: list):
    """Replace slide 5 content with per-module completion stats bar chart."""
    from lxml import etree

    # Remove all existing content shapes from the slide
    spTree = slide.shapes._spTree
    to_remove = [
        child for child in spTree
        if etree.QName(child.tag).localname in ('sp', 'pic', 'graphicFrame', 'grpSp', 'cxnSp')
    ]
    for el in to_remove:
        spTree.remove(el)

    total = len(csv_trainees)

    # Layout constants
    LEFT        = Inches(0.55)
    TITLE_Y     = Inches(0.22)
    SUB_Y       = Inches(0.78)
    START_Y     = Inches(1.18)
    ROW_H       = Inches(0.82)
    BAR_Y_OFF   = Inches(0.40)
    BAR_H       = Inches(0.17)
    BAR_W       = Inches(8.9)
    LABEL_W     = Inches(7.0)
    STAT_LEFT   = Inches(7.3)
    STAT_W      = Inches(2.15)

    # ── Title ──────────────────────────────────────────────────────────
    tb = slide.shapes.add_textbox(LEFT, TITLE_Y, Inches(9), Inches(0.52))
    tf = tb.text_frame
    p  = tf.paragraphs[0]
    r  = p.add_run()
    r.text = "Module Completion Overview"
    r.font.size  = Pt(24)
    r.font.bold  = True
    r.font.color.rgb = RGBColor(0x1e, 0x40, 0xaf)

    # ── Subtitle ───────────────────────────────────────────────────────
    tb2 = slide.shapes.add_textbox(LEFT, SUB_Y, Inches(9), Inches(0.30))
    tf2 = tb2.text_frame
    p2  = tf2.paragraphs[0]
    r2  = p2.add_run()
    r2.text = f"{total} Trainees"
    r2.font.size  = Pt(11)
    r2.font.color.rgb = RGBColor(0x64, 0x74, 0x8b)

    # Determine how many modules have any data
    n_mods = 0
    for t in csv_trainees:
        n_mods = max(n_mods, len(t.get("modules", [])))
    n_mods = max(n_mods, 1)

    # Show only up to (and including) the last module where someone passed
    last_passed_mi = max(
        (mi for mi in range(min(n_mods, len(MODULE_NAMES)))
         if any(mi < len(t["modules"]) and t["modules"][mi]["status"] == "Passed"
                for t in csv_trainees)),
        default=0
    )

    row_idx = 0
    for mi in range(last_passed_mi + 1):
        mod_name = MODULE_NAMES[mi]
        passed  = sum(1 for t in csv_trainees
                      if mi < len(t["modules"]) and t["modules"][mi]["status"] == "Passed")
        in_prog = sum(1 for t in csv_trainees
                      if mi < len(t["modules"]) and t["modules"][mi]["status"] == "In Progress")
        pct = int(passed / total * 100) if total else 0

        y = START_Y + ROW_H * row_idx
        row_idx += 1

        # ── Module label ───────────────────────────────────────────────
        lbl = slide.shapes.add_textbox(LEFT, y, LABEL_W, Inches(0.36))
        tf  = lbl.text_frame
        p   = tf.paragraphs[0]
        r   = p.add_run()
        r.text = f"M{mi+1}  {mod_name}"
        r.font.size  = Pt(12)
        r.font.bold  = True
        r.font.color.rgb = RGBColor(0x1e, 0x29, 0x3b)

        # ── Stat label (right) ─────────────────────────────────────────
        color = RGBColor(0x15, 0x80, 0x3d) if pct >= 70 else (
                RGBColor(0xd9, 0x7f, 0x06) if pct >= 30 else RGBColor(0x9b, 0x1c, 0x1c))
        st = slide.shapes.add_textbox(STAT_LEFT, y, STAT_W, Inches(0.36))
        tf  = st.text_frame
        p   = tf.paragraphs[0]
        p.alignment = PP_ALIGN.RIGHT
        r   = p.add_run()
        r.text = f"({pct}%)  {passed}/{total}"
        r.font.size  = Pt(12)
        r.font.bold  = True
        r.font.color.rgb = color

        # ── Background bar (light gray) ────────────────────────────────
        bg = slide.shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,
            LEFT, y + BAR_Y_OFF, BAR_W, BAR_H
        )
        bg.fill.solid()
        bg.fill.fore_color.rgb = RGBColor(0xe2, 0xe8, 0xf0)
        bg.line.fill.background()

        # ── Fill bar (blue, proportional) ─────────────────────────────
        if pct > 0:
            fill_w = max(BAR_H, int(BAR_W * pct / 100))
            fb = slide.shapes.add_shape(
                MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,
                LEFT, y + BAR_Y_OFF, fill_w, BAR_H
            )
            fb.fill.solid()
            fb.fill.fore_color.rgb = RGBColor(0x1e, 0x40, 0xaf)
            fb.line.fill.background()

        # ── In-Progress bar (amber overlay) ───────────────────────────
        if in_prog > 0:
            ip_start = LEFT + int(BAR_W * passed / total)
            ip_w     = max(BAR_H, int(BAR_W * in_prog / total))
            ip = slide.shapes.add_shape(
                MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,
                ip_start, y + BAR_Y_OFF, ip_w, BAR_H
            )
            ip.fill.solid()
            ip.fill.fore_color.rgb = RGBColor(0xf5, 0xa6, 0x23)
            ip.line.fill.background()


def generate_report_from_data(template_path: str,
                              csv_data: dict,
                              hse_data: dict,
                              ptw_data: dict,
                              obs_data: list,
                              obs_images: list = None) -> bytes:
    """
    Build the PPTX from pre-parsed data dicts (no file uploads needed).

    csv_data  : {"trainees": [{"name": str, "modules": [{"score": str, "status": str}]}]}
    hse_data  : {"total_obs": int, "tbt_sessions": int, "tbt_attend": int, ...}
    ptw_data  : {"approved": int, "trainees": [...]}
    obs_data  : list of per-day dicts {"date_label": str, "total_obs": int, ...}
    obs_images: list of PNG bytes for observation image slides (optional)
    """
    if obs_images is None:
        obs_images = []
    return _build_pptx(template_path, csv_data, hse_data, ptw_data, obs_data, obs_images)


def generate_report(template_path: str,
                    csv_content: bytes,
                    hse_pdf: bytes,
                    ptw_pdf: bytes,
                    obs_pdfs: list) -> bytes:
    """
    Parse all uploaded files, update the template PPTX, return PPTX bytes.

    obs_pdfs: list of (filename, bytes) sorted by filename (= date order).
    """
    # ── 1. Parse all data files ──────────────────────────────────────
    csv_data = parse_progress_csv(csv_content)
    hse_data = parse_hse_pdf(hse_pdf)
    ptw_data = parse_ptw_pdf(ptw_pdf)

    obs_data = []
    for _fname, obs_bytes in sorted(obs_pdfs, key=lambda x: x[0]):
        obs_data.append(parse_obs_pdf(obs_bytes))

    obs_images = []
    for _fname, obs_bytes in sorted(obs_pdfs, key=lambda x: x[0]):
        obs_images.extend(pdf_to_images(obs_bytes, dpi=150))

    return _build_pptx(template_path, csv_data, hse_data, ptw_data, obs_data, obs_images)


def _build_pptx(template_path: str,
                csv_data: dict,
                hse_data: dict,
                ptw_data: dict,
                obs_data: list,
                obs_images: list) -> bytes:
    """Core PPTX builder — works on pre-parsed data dicts."""
    from pptx import Presentation

    # Aggregate obs stats
    total_obs   = sum(d["total_obs"]  for d in obs_data)
    total_sgl   = sum(d["sgl_count"]  for d in obs_data)
    total_sgl_a = sum(d["sgl_attend"] for d in obs_data)
    total_high  = sum(d["high"]       for d in obs_data)
    total_med   = sum(d["medium"]     for d in obs_data)
    total_low   = sum(d["low"]        for d in obs_data)
    total_pos   = sum(d["positive"]   for d in obs_data)

    # Use HSE PDF total if parsed (more authoritative)
    if hse_data["total_obs"]:
        total_obs = hse_data["total_obs"]

    all_sgl_sessions = []
    for d in obs_data:
        all_sgl_sessions.extend(d["sgl_sessions"])

    # TBT sessions from HSE PDF (fallback to parsed obs SGLs)
    if hse_data["tbt_sessions"]:
        total_sgl   = hse_data["tbt_sessions"]
        total_sgl_a = hse_data["tbt_attend"]

    field_stages = ptw_data.get("approved", 0)
    today_str    = datetime.now().strftime("%d %b %Y")

    # ── 2. Load template ─────────────────────────────────────────────
    prs = Presentation(template_path)

    # ── 3. Slide 1 — Cover KPIs ──────────────────────────────────────
    s1 = prs.slides[0]
    for shape in s1.shapes:
        txt = shape.text_frame.text.strip() if shape.has_text_frame else ""

        if "Total Observations" in txt:
            _update_shape_para(shape, 0, str(total_obs))

        elif "SGL Sessions" in txt and "Attendees" not in txt:
            _update_shape_para(shape, 0, str(total_sgl))

        elif "SGL Attendees" in txt:
            _update_shape_para(shape, 0, str(total_sgl_a))

        elif "Field Stages Approved" in txt:
            _update_shape_para(shape, 0, str(field_stages))

        elif "Week" in txt and "Trainees" in txt and "Generated" in txt:
            # "Week 06 – 10 Sep 2026  | 16 Trainees | Generated 10 Sep 2026"
            # Text is split across many runs; find the date runs after "Generated"
            runs = shape.text_frame.paragraphs[0].runs
            gen_idx = next((i for i, r in enumerate(runs)
                            if r.text.strip() == "Generated"), None)
            if gen_idx is not None:
                # Date parts are split like: "Generated" " " "10" " " "Sep" " " "2026"
                # Collect and blank out runs after "Generated" that look like date parts
                now_dt  = datetime.now()
                day_s   = str(now_dt.day)
                mon_s   = now_dt.strftime("%b")
                year_s  = str(now_dt.year)
                skip    = gen_idx + 1   # skip the space
                # Replace: day, space, month, space, year (runs gen_idx+2..gen_idx+6)
                date_runs_idx = [i for i in range(gen_idx + 1, min(gen_idx + 7, len(runs)))
                                 if runs[i].text.strip()]
                new_parts = [day_s, mon_s, year_s]
                for k, idx in enumerate(date_runs_idx[:3]):
                    runs[idx].text = new_parts[k] if k < len(new_parts) else ""

    # ── 4. Slide 5 — Module Completion Stats ─────────────────────────
    _rebuild_module_stats_slide(prs.slides[4], csv_data["trainees"])

    # ── 5. Slide 8 — E-Learning Table ────────────────────────────────
    s8 = prs.slides[7]
    for shape in s8.shapes:
        if shape.shape_type == 19:   # TABLE
            tbl = shape.table
            _update_elearning_table(tbl, csv_data["trainees"], ptw_data["trainees"])
            break

    # Stats on slide 8 (In Progress / Not Started counts)
    in_prog   = sum(1 for t in csv_data["trainees"]
                    if any(m["status"] == "In Progress" for m in t["modules"])
                    and not all(m["status"] in ("—", "") for m in t["modules"]))
    not_start = sum(1 for t in csv_data["trainees"]
                    if all(m["status"] in ("—", "") for m in t["modules"]))

    for shape in s8.shapes:
        if not shape.has_text_frame:
            continue
        txt = shape.text_frame.text
        if "In Progress" in txt and "Trainees" in txt:
            _update_shape_para(shape, 0, str(in_prog))
        elif "Trainees Not Started" in txt:
            _update_shape_para(shape, 0, str(not_start))

    # ── 5. Slide 9 — PTW Field Training Table ────────────────────────
    s9 = prs.slides[8]
    for shape in s9.shapes:
        if shape.shape_type == 19:
            tbl = shape.table
            _update_ptw_table(tbl, ptw_data["trainees"], ptw_data)
            break

    # ── 6. Slide 12 — Observations Daily Summary ─────────────────────
    s12 = prs.slides[11]
    for shape in s12.shapes:
        if shape.has_text_frame:
            txt = shape.text_frame.text
            if "Total\nObservations" in txt or ("Total" in txt and "Observations" in txt):
                _update_shape_para(shape, 0, str(total_obs))
            elif "Medium-Risk" in txt or "Medium" in txt:
                _update_shape_para(shape, 0, str(total_med))
            elif "Low-Risk" in txt or ("Low" in txt and "Observations" in txt):
                _update_shape_para(shape, 0, str(total_low))
            elif "Positive" in txt and "Other" in txt:
                _update_shape_para(shape, 0, str(total_pos))

        if shape.shape_type == 19:
            tbl = shape.table
            _update_obs_daily_table(tbl, obs_data, total_obs, total_high,
                                    total_med, total_low, total_pos,
                                    total_sgl, total_sgl_a)

    # Update WEEKLY TOTAL text box
    for shape in s12.shapes:
        if shape.has_text_frame and "WEEKLY TOTAL" in shape.text_frame.text:
            runs = shape.text_frame.paragraphs[0].runs
            if runs:
                runs[0].text = (f"WEEKLY TOTAL\t{total_obs}\t{total_high}\t"
                                f"{total_med}\t{total_low}\t{total_pos}\t"
                                f"{total_sgl}\t{total_sgl_a}")

    # ── 7. Slide 14 — SGL Sessions ───────────────────────────────────
    unique_officers = {s["officer"] for s in all_sgl_sessions}
    unique_topics   = {s["topic"]   for s in all_sgl_sessions}
    n_sessions      = len(all_sgl_sessions)
    att_from_sess   = sum(s["attend"] for s in all_sgl_sessions)
    # Use HSE PDF numbers as totals; session-level attendance for detail
    sgl_attend_total = total_sgl_a if total_sgl_a else att_from_sess

    s14 = prs.slides[13]
    for shape in s14.shapes:
        if not shape.has_text_frame:
            continue
        txt = shape.text_frame.text

        # Subtitle: "4 Sessions | 37 Attendees"
        # Runs are split: R0="4", R2="Sessions", R6="37", R8="Attendees"
        if "Sessions" in txt and "|" in txt and "Attendees" in txt:
            runs = shape.text_frame.paragraphs[0].runs
            # Runs are split: "4"," ","Sessions"," ","|"," ","37"," ","Attendees"
            # Find any digit run before "Sessions" label, and before "Attendees" label
            for ri, run in enumerate(runs):
                if run.text.strip() == "Sessions":
                    # Scan backwards for the nearest digit run
                    for back in range(ri - 1, max(ri - 4, -1), -1):
                        if runs[back].text.strip().isdigit():
                            runs[back].text = str(total_sgl)
                            break
                elif run.text.strip() == "Attendees":
                    for back in range(ri - 1, max(ri - 4, -1), -1):
                        if runs[back].text.strip().isdigit():
                            runs[back].text = str(sgl_attend_total)
                            break

        elif "Sessions This Week" in txt:
            _update_shape_para(shape, 0, str(total_sgl))
        elif "Total Attendees" in txt:
            _update_shape_para(shape, 0, str(sgl_attend_total))
        elif "Trainees Conducted" in txt:
            _update_shape_para(shape, 0, str(len(unique_officers)) if unique_officers else str(total_sgl))
        elif "Topics Covered" in txt:
            _update_shape_para(shape, 0, str(len(unique_topics)) if unique_topics else str(total_sgl))

    for shape in s14.shapes:
        if shape.shape_type == 19:
            tbl = shape.table
            _update_sgl_table(tbl, all_sgl_sessions)
            break

    # ── 8. Slide 39 — Week Summary ───────────────────────────────────
    s39 = prs.slides[38]
    for shape in s39.shapes:
        if not shape.has_text_frame:
            continue
        txt = shape.text_frame.text
        # Bullet: "X observations submitted …"
        if "observations submitted" in txt:
            for run in shape.text_frame.paragraphs[0].runs:
                run.text = re.sub(r"\d+(?=\s+observations)", str(total_obs), run.text)
        # Bullet: "X SGL sessions …"
        elif "SGL sessions" in txt:
            for run in shape.text_frame.paragraphs[0].runs:
                run.text = re.sub(r"\d+(?=\s+SGL sessions)", str(total_sgl), run.text)
                run.text = re.sub(r"\d+(?=\s+workers)", str(total_sgl_a), run.text)
        # Bullet: "X field stages approved"
        elif "field stages approved" in txt:
            for run in shape.text_frame.paragraphs[0].runs:
                run.text = re.sub(r"\d+(?=\s+field stages)", str(field_stages), run.text)

    # ── 9. Observation image slides (15–37 → indices 14–36) ──────────
    all_obs_images = []
    for _fname, obs_bytes in sorted(obs_pdfs, key=lambda x: x[0]):
        all_obs_images.extend(pdf_to_images(obs_bytes, dpi=150))

    _replace_observation_slides(prs, all_obs_images)

    # ── 10. Serialize ─────────────────────────────────────────────────
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────
# TABLE UPDATERS
# ─────────────────────────────────────────────────────────────────────

def _build_status_summary(trainee_data: dict) -> str:
    """Return human-readable progress string for the Status column."""
    mods = trainee_data["modules"]
    passed_count = sum(1 for m in mods if m["status"] == "Passed")
    last_in_prog = next(
        (f"M{i+1}" for i, m in enumerate(mods) if m["status"] == "In Progress"),
        None,
    )
    if passed_count == 7:
        return "All 7 modules passed"
    if passed_count > 0 and last_in_prog:
        return f"{passed_count} module{'s' if passed_count > 1 else ''} passed · {last_in_prog} In Progress"
    if passed_count > 0:
        return f"{passed_count} module{'s' if passed_count > 1 else ''} passed"
    if last_in_prog:
        return f"{last_in_prog} In Progress"
    return "Not started"


def _trainee_row_text(trainee_data, ptw_map: dict) -> str:
    """Build the tab-separated row string for the e-learning table (legacy / fallback)."""
    name = trainee_data["name"]
    mods = trainee_data["modules"]

    cols = [name]
    passed_count = 0
    last_in_prog = None

    for i, mod in enumerate(mods):
        s = mod["status"]
        cols.append(s)
        if s == "Passed":
            passed_count += 1
        elif s == "In Progress" and last_in_prog is None:
            last_in_prog = f"M{i+1}"

    # Status summary
    if passed_count == 7:
        summary = "All 7 modules passed"
    elif passed_count > 0 and last_in_prog:
        summary = f"{passed_count} module{'s' if passed_count > 1 else ''} passed · {last_in_prog} In Progress"
    elif passed_count > 0:
        summary = f"{passed_count} module{'s' if passed_count > 1 else ''} passed"
    elif last_in_prog:
        summary = f"{last_in_prog} In Progress"
    else:
        summary = "Not started"

    cols.append(summary)
    return "\t".join(cols)


def _update_elearning_table(tbl, csv_trainees: list, ptw_trainees: list):
    """Update slide 8 e-learning table — one cell per column with status colours."""
    ptw_names = {t["name"].lower() for t in ptw_trainees}

    def sort_key(t):
        mods = t["modules"]
        passed  = sum(1 for m in mods if m["status"] == "Passed")
        in_prog = any(m["status"] == "In Progress" for m in mods)
        not_start = all(m["status"] in ("—", "") for m in mods)
        return (-passed, 0 if in_prog else (1 if not_start else 0))

    filtered = [t for t in csv_trainees
                if not ptw_names or t["name"].lower() in ptw_names
                or any(t["name"].lower() in pn or pn in t["name"].lower()
                       for pn in ptw_names)]
    if not filtered:
        filtered = csv_trainees
    filtered.sort(key=sort_key)

    for row_idx in range(1, len(tbl.rows)):
        data_idx = row_idx - 1
        row      = tbl.rows[row_idx]
        n_cols   = len(row.cells)

        if data_idx < len(filtered):
            t    = filtered[data_idx]
            mods = t["modules"]

            # ── Column 0 : Trainee name ──────────────────────────────
            _update_cell(row.cells[0], t["name"])

            # ── Columns 1–7 : M1–M7 status + colour ─────────────────
            for mi in range(7):
                ci = mi + 1
                if ci >= n_cols:
                    break
                status = mods[mi]["status"] if mi < len(mods) else "—"
                if status not in ("Passed", "In Progress"):
                    status = "—"
                _update_cell(row.cells[ci], status)
                _set_cell_bg(row.cells[ci], *_STATUS_BG.get(status, _STATUS_BG["—"]))

            # ── Last column : status summary ─────────────────────────
            last_ci = n_cols - 1
            if last_ci >= 1:
                _update_cell(row.cells[last_ci], _build_status_summary(t))

        else:
            # Empty row
            for cell in row.cells:
                _update_cell(cell, "—")
                _set_cell_bg(cell, *_STATUS_BG["—"])


def _update_ptw_table(tbl, ptw_trainees: list, ptw_data: dict):
    """Update slide 9 PTW field training table — per-column with stage colours."""
    def sort_key(t):
        return -int(t["overall"].split("/")[0]) if t["overall"] else 0

    sorted_t = sorted(ptw_trainees, key=sort_key)
    footer   = (f"{ptw_data.get('approved', 0)} of 35 field stages approved this week"
                f" | Week target: 10 stages")

    for row_idx in range(1, len(tbl.rows) - 1):
        data_idx = row_idx - 1
        row      = tbl.rows[row_idx]
        n_cols   = len(row.cells)

        if data_idx < len(sorted_t):
            t      = sorted_t[data_idx]
            pm     = t["per_module"]        # list of 7 strings like "3/5" or "—"
            status = "Active" if t.get("active") else "Pending"

            # Column 0 : Name
            _update_cell(row.cells[0], t["name"])

            # Columns 1–7 : per-module stages + colour
            for mi in range(7):
                ci = mi + 1
                if ci >= n_cols:
                    break
                val = pm[mi] if mi < len(pm) else "—"
                _update_cell(row.cells[ci], val)
                _set_cell_bg(row.cells[ci], *_ptw_stage_bg(val))

            # Remaining columns: Overall / Status / Last Activity
            extras = [t["overall"], status, "—"]
            for k, val in enumerate(extras):
                ci = 8 + k
                if ci < n_cols:
                    _update_cell(row.cells[ci], val)
        else:
            for cell in row.cells:
                _update_cell(cell, "—")
                _set_cell_bg(cell, *_STATUS_BG["—"])

    # Footer row (last row)
    last_row_idx = len(tbl.rows) - 1
    if last_row_idx > 0:
        _update_cell(tbl.rows[last_row_idx].cells[0], footer)


def _update_obs_daily_table(tbl, obs_data: list,
                             total_obs, total_high, total_med,
                             total_low, total_pos, total_sgl, total_sgl_a):
    """Update slide 12 daily observations table (proper multi-column)."""
    # row 0 = header; rows 1..N = days
    for row_idx in range(1, min(len(tbl.rows), len(obs_data) + 1)):
        day = obs_data[row_idx - 1]
        cells = tbl.rows[row_idx].cells
        key = " · ".join(day["key_obs"][:3]) if day["key_obs"] else "—"
        sgl_a = str(day["sgl_attend"]) if day["sgl_count"] else "—"

        values = [
            day.get("date_label", day.get("date", "")),
            str(day["total_obs"]),
            str(day["high"])    if day["high"]    else "0",
            str(day["medium"])  if day["medium"]  else "0",
            str(day["low"])     if day["low"]      else "0",
            str(day["positive"])if day["positive"] else "0",
            str(day["sgl_count"]) if day["sgl_count"] else "0",
            sgl_a,
            key,
        ]

        for ci, val in enumerate(values):
            if ci < len(cells):
                _update_cell(cells[ci], val)


def _update_sgl_table(tbl, sessions: list):
    """Update slide 14 SGL sessions table."""
    for row_idx in range(1, len(tbl.rows)):
        si = row_idx - 1
        if si < len(sessions):
            s   = sessions[si]
            vals = [
                str(row_idx),
                s.get("topic", "—"),
                s.get("officer", "—"),
                s.get("location", "—"),
                "—",                     # Coverage (not parsed)
                str(s.get("attend", "—")),
            ]
        else:
            vals = ["—"] * 6
        cells = tbl.rows[row_idx].cells
        for ci, val in enumerate(vals):
            if ci < len(cells):
                _update_cell(cells[ci], val)


# ─────────────────────────────────────────────────────────────────────
# OBSERVATION SLIDE REPLACEMENT
# ─────────────────────────────────────────────────────────────────────

def _replace_observation_slides(prs, images: list):
    """
    Replace observation slides (originally indices 14–36) with new images.
    Handles fewer or more images than the original 23 slides.
    """
    if not images:
        return

    n_existing = OBS_SLIDE_COUNT      # 23
    n_new      = len(images)

    # ── Replace existing obs slides ───────────────────────────────────
    for i in range(min(n_existing, n_new)):
        slide = prs.slides[OBS_SLIDE_FIRST + i]
        _replace_slide_with_image(slide, images[i], prs)

    # ── If we have FEWER new images: blank out leftover slides ────────
    if n_new < n_existing:
        for i in range(n_new, n_existing):
            slide = prs.slides[OBS_SLIDE_FIRST + i]
            # Remove all pictures, leave slide blank
            to_rm = [s for s in slide.shapes if s.shape_type == 13]
            for sh in to_rm:
                sh.element.getparent().remove(sh.element)

    # ── If we have MORE new images: insert extra slides ───────────────
    if n_new > n_existing:
        insert_at = OBS_SLIDE_LAST + 1   # position after last original obs slide
        for i in range(n_existing, n_new):
            _add_blank_image_slide(prs, images[i])
            # Move newly appended slide to correct position
            last_idx = len(prs.slides) - 1
            target   = insert_at + (i - n_existing)
            _move_slide(prs, last_idx, target)
