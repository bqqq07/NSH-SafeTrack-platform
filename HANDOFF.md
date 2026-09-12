# NSH SafeTrack — Handoff Document
**Date:** 2026-09-04  
**Project path:** `C:\Users\z\Desktop\New folder (2)\`  
**Stack:** Flask · MySQL · SQLAlchemy · Jinja2 · no Flask-Migrate (manual ALTER TABLE)

---

## What the app is

Construction site welfare & environment tracking system — Jubail, Saudi Arabia (Aramco factory).  
Standards: **GI-0002.102, CSM, GI 430.001, GI 151.006, SAEHC** (no ISO 14001).  
Language: **English only in UI**. Conversation with user stays in Arabic.

---

## Roles

| Role | Access |
|---|---|
| `welfare_officer` | Daily rounds, findings, complaints, own KPIs |
| `welfare_supervisor` | Approves officer level-up, sees all officers |
| `environment_officer` | Env checks (/env/* routes) — NOT shown in role dropdown when adding user |
| `safety_manager` / `admin` / `super_admin` | Full access |

**Critical rules (never violate):**
- Gender field → **backend only**, never in any UI page or report
- `environment_officer` role → **never appears in user-creation dropdown**
- Toilet inspection → **completely removed** (female officers on site)
- Aramco standards only — no ISO 14001

---

## The 14-level progression system (new — keep this)

Each chapter = one locked level. Officer does level-specific field work → tasks met → supervisor approves → next level unlocks.

### WlfProgress statuses
`locked` → `active` → `pending` (awaiting approval) → `done`

### Helper functions in main.py
- `_wlf_ensure_progress(uid)` — creates 14 DB rows on first visit (L1 active, rest locked)
- `_wlf_current_level(uid)` — returns first row with status "active" or "pending"
- `WLF_LEVEL_CODES = [l["code"] for l in WLF_LEVELS]` — auto list, no manual update needed

### The 14 levels

| Code | Module | Chapter | Title |
|---|---|---|---|
| L1 | M1 | 1.1 | Drinking Water Stations |
| L2 | M1 | 1.2 | Shaded Rest Areas |
| L3 | M2 | 2.1 | Accommodation Standards |
| L4 | M2 | 2.2 | Food & Nutrition |
| L5 | M2 | 2.3 | General Hygiene & Pest Control |
| L6 | M3 | 3.1 | Heat Index System |
| L7 | M3 | 3.2 | Work/Rest Schedules & Water |
| L8 | M3 | 3.3 | Midday Work Ban |
| L9 | M3 | 3.4 | Acclimatization |
| L10 | M3 | 3.5 | Heat Illness & First Aid |
| L11 | M4 | 4.1 | First Aid Kit Inspection |
| L12 | M4 | 4.2 | Emergency Numbers & Reporting |
| L13 | M5 | 5.1 | Full Daily Welfare Tour |
| L14 | M5 | 5.2 | Training & Monitoring Records |

### Each level dict keys
```python
{ "code", "phase", "module", "chapter", "title", "ref", "what", "why", "how", "right", "wrong",
  "tasks": [{"k": "rounds_submitted", "label": "...", "target": 3}] }
```

---

## What is DONE (completed and working)

| File | Status |
|---|---|
| `main.py` — WLF_LEVELS | ✅ 14 English levels defined (5 modules), replaces old 12 |
| `main.py` — WLF_ITEMS, WLF_SIMPLE_CHECKS | ✅ English |
| `main.py` — welfare_home route | ✅ passes `lvl_status` to template |
| `templates/welfare_home.html` | ✅ shows module badge, chapter, task description, status |
| `templates/welfare_path.html` | ✅ grouped by 5 modules, color-coded status, legend |
| `templates/welfare_supervisor.html` | ✅ approval header shows M+Ch+ref |
| `templates/welfare_round_field.html` | ✅ all English |
| `templates/welfare_headcount.html` | ✅ English headers |
| `templates/welfare_complaint_new.html` | ✅ English categories |
| `templates/base.html` — env_officer nav | ✅ English |
| All `templates/env_*.html` | ✅ full English rewrite (7 files) |

---

## What is BROKEN / INCOMPLETE (must fix in next session)

### 1. Two conflicting systems — biggest problem

The app currently has TWO separate welfare systems running in parallel:

**Old system (must be removed):**
- `WlfRound` model — generic 6-section mega-form (water + shelter + camp + heat + medical + docs)
- Routes: `welfare_round_new`, `welfare_round_field`, `welfare_round_desk`, `welfare_round_list`, etc.
- Templates: `welfare_round_field.html`, `welfare_round_desk.html`, `welfare_round_new.html`
- This was the original approach — one big form covering everything

**New system (keep and complete):**
- `WlfProgress` — 14 specific levels, each with its own focused task
- Each level should have its OWN simple form (not the old 6-section mega-form)
- The officer does ONE specific thing per level (e.g., L1 = water stations only)

**Action required:**
- Delete `WlfRound` model and all its routes
- Delete old round templates
- Replace with: each level gets a focused `welfare_level_work.html` form specific to that chapter's tasks
- The "tasks" counter (e.g., `rounds_submitted: 3`) tracks submissions of the level-specific form

### 2. Level descriptions need depth (field → desk → analysis)

Current `what`/`how` fields describe only the field moment. Each level should cover:
- **Field:** what to physically inspect and measure
- **Desk:** how to document, enter data, raise findings
- **Analysis:** what to compare/trend over time to prove competence

Example for L1 (Water Stations):
- Field: count stations, measure distance to furthest worker, read chlorine (0.5–3.0 ppm), photograph
- Desk: enter all readings, flag any station outside limits as a finding
- Analysis: compare chlorine readings across 5 days, identify chronically failing stations

**Action required:** Rewrite `what`, `how`, `right`, `wrong` for all 14 levels with this depth.

### 3. Nav bar too complex for new welfare officers

Current nav has too many items. Welfare officers are new to the job and get confused.

**Action required:** Reduce welfare_officer nav in `base.html` to:
- Home
- My Path
- + New Round (level work)
- Findings
- Complaints
- My KPIs

Hide everything else from welfare_officer role.

### 4. Old training templates

`training_courses.html` and `training_matrix.html` still exist with **no backend routes**.  
Training is on a separate external platform — these files are dead weight.

**Action required:** Delete both files. Remove any nav links pointing to them.

---

## Training module — future plan

- Training content lives on **separate external platform** (not yet integrated)
- When connecting: use the 14 WLF_LEVELS chapter codes (L1–L14) as course identifiers
- Do nothing until external platform is ready

---

## Key technical notes

- DB: `db.create_all()` on startup + manual `ALTER TABLE` — no Flask-Migrate
- Heat Index: Cat I (25-29) / Cat II (30-38) / Cat III (39-51) / Cat IV (>52)
- Work:rest ratios: 50:10 / 30:10 / 20:10 / Cat IV = stop work
- Midday ban: 12:00–15:00, June 15 – September 15
- SAEHC: chlorine 0.5–3.0 ppm, water ≤100 m, shade ≤100 m

---

## Priority order for next session

1. **Delete old WlfRound system** — models, routes, templates (welfare_round_*)
2. **Design level-specific work form** — one focused form per level (welfare_level_work.html)
3. **Rewrite level descriptions** — field → desk → analysis for all 14 levels in WLF_LEVELS
4. **Simplify welfare_officer nav** — 6 items only
5. **Delete dead training templates** — training_courses.html, training_matrix.html
