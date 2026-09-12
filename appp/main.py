
import os
from datetime import datetime, date, timedelta
from typing import Tuple
from functools import wraps
from sqlalchemy import text  # إضافة هذا الاستيراد

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, abort, g
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, or_
# أعلى الملف مع الاستيرادات
from sqlalchemy import func

def previous_week_range(ws: date) -> Tuple[date, date]:
    prev_ws = ws - timedelta(days=7)
    prev_we = prev_ws + timedelta(days=4)
    return prev_ws, prev_we

# ===================== App Setup =====================
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-replit")

# DB path under instance/
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "app.db")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ===================== Constants =====================
WEIGHTS = {
    "targets_rows": 4,          # 4 rows × 10 = 40
    "target_per_row": 10,
    "perf_items": {             # 6 items × 10 = 60
        "punctuality": 10,
        "quality": 10,
        "productivity": 10,
        "communication": 10,
        "problemsolving": 10,
        "compliance": 10,
    },
    "perf_scale_max": 5,
    "bands": {"excellent": 90, "good": 80, "satisfactory": 70},
}

# Site Supervisor → Supervisor evaluation weights
SE_WEIGHTS = {
    "perf_items": {  # 6 equally weighted items → 100 total
        "leadership": 1,
        "communication": 1,
        "scheduling": 1,
        "compliance": 1,
        "team_support": 1,
        "reporting": 1,
    },
    "perf_scale_max": 5,
    "bands": {"excellent": 90, "good": 80, "satisfactory": 70},
}

# Week definition: Sunday → Thursday (Python weekday: Mon=0 ... Sun=6)
WEEK_START_WEEKDAY = 6  # Sunday
WEEK_END_WEEKDAY = 3    # Thursday

# ===================== Models =====================
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    supervisor_code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(120), default="")
    role = db.Column(db.String(20), default="supervisor")  # supervisor | admin | site_supervisor
    is_active = db.Column(db.Boolean, default=True)

class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)  # owner (supervisor)
    emp_number = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    department = db.Column(db.String(120), default="")
    site = db.Column(db.String(120), default="")
    is_active = db.Column(db.Boolean, default=True)

class Evaluation(db.Model):
    __table_args__ = (
        UniqueConstraint("employee_id", "week_start", "week_end", name="uq_emp_week"),
    )
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    evaluator_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    week_start = db.Column(db.Date, nullable=False)
    week_end   = db.Column(db.Date, nullable=False)

    # Targets (4 rows)
    t1_text = db.Column(db.String(255), default=""); t1_percent = db.Column(db.Float, default=0); t1_remarks = db.Column(db.String(255), default="")
    t2_text = db.Column(db.String(255), default=""); t2_percent = db.Column(db.Float, default=0); t2_remarks = db.Column(db.String(255), default="")
    t3_text = db.Column(db.String(255), default=""); t3_percent = db.Column(db.Float, default=0); t3_remarks = db.Column(db.String(255), default="")
    t4_text = db.Column(db.String(255), default=""); t4_percent = db.Column(db.Float, default=0); t4_remarks = db.Column(db.String(255), default="")

    # Performance (ratings 1..5 + comments)
    p_punctuality = db.Column(db.Integer, default=0); c_punctuality = db.Column(db.String(255), default="")
    p_quality = db.Column(db.Integer, default=0); c_quality = db.Column(db.String(255), default="")
    p_productivity = db.Column(db.Integer, default=0); c_productivity = db.Column(db.String(255), default="")
    p_communication = db.Column(db.Integer, default=0); c_communication = db.Column(db.String(255), default="")
    p_problemsolving = db.Column(db.Integer, default=0); c_problemsolving = db.Column(db.String(255), default="")
    p_compliance = db.Column(db.Integer, default=0); c_compliance = db.Column(db.String(255), default="")

    strengths = db.Column(db.Text, default="")
    improvements = db.Column(db.Text, default="")
    training_needed = db.Column(db.Text, default="")

    targets_score = db.Column(db.Float, default=0)
    perf_score    = db.Column(db.Float, default=0)
    total_score   = db.Column(db.Float, default=0)
    overall_band  = db.Column(db.String(30), default="")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# ---- Site Supervisor mappings & evaluations (NOT nested) ----
class SiteSupervisorMap(db.Model):
    __table_args__ = (
        UniqueConstraint("site_sup_id", "supervisor_id", name="uq_site_sup_pair"),
    )
    id = db.Column(db.Integer, primary_key=True)
    site_sup_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)     # evaluator (site supervisor)
    supervisor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)   # evaluated supervisor

class SupervisorEvaluation(db.Model):
    __table_args__ = (
        UniqueConstraint("supervisor_id", "week_start", "week_end", name="uq_sup_week"),
    )
    id = db.Column(db.Integer, primary_key=True)
    supervisor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)  # المُقيَّم (Supervisor)
    evaluator_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)   # المقيِّم (Site Supervisor)

    week_start = db.Column(db.Date, nullable=False)
    week_end   = db.Column(db.Date, nullable=False)

    # Targets (4 rows) — نفس الموظف
    t1_text = db.Column(db.String(255), default=""); t1_percent = db.Column(db.Float, default=0); t1_remarks = db.Column(db.String(255), default="")
    t2_text = db.Column(db.String(255), default=""); t2_percent = db.Column(db.Float, default=0); t2_remarks = db.Column(db.String(255), default="")
    t3_text = db.Column(db.String(255), default=""); t3_percent = db.Column(db.Float, default=0); t3_remarks = db.Column(db.String(255), default="")
    t4_text = db.Column(db.String(255), default=""); t4_percent = db.Column(db.Float, default=0); t4_remarks = db.Column(db.String(255), default="")

    # Performance (ratings 1..5 + comments) — نفس الموظف
    p_punctuality = db.Column(db.Integer, default=0); c_punctuality = db.Column(db.String(255), default="")
    p_quality = db.Column(db.Integer, default=0); c_quality = db.Column(db.String(255), default="")
    p_productivity = db.Column(db.Integer, default=0); c_productivity = db.Column(db.String(255), default="")
    p_communication = db.Column(db.Integer, default=0); c_communication = db.Column(db.String(255), default="")
    p_problemsolving = db.Column(db.Integer, default=0); c_problemsolving = db.Column(db.String(255), default="")
    p_compliance = db.Column(db.Integer, default=0); c_compliance = db.Column(db.String(255), default="")

    strengths = db.Column(db.Text, default="")
    improvements = db.Column(db.Text, default="")
    training_needed = db.Column(db.Text, default="")

    targets_score = db.Column(db.Float, default=0)
    perf_score    = db.Column(db.Float, default=0)
    total_score   = db.Column(db.Float, default=0)
    overall_band  = db.Column(db.String(30), default="")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===================== Utilities =====================
def ensure_db_and_admin() -> None:
    """Create DB tables and bootstrap admin if ADMIN_ID is set."""
    with app.app_context():
        db.create_all()
        # تأكد من أعمدة جدول تقييم المشرفين
        _ensure_supervisor_eval_columns()

        admin_id = os.environ.get("ADMIN_ID")
        if admin_id:
            existing = User.query.filter_by(supervisor_code=admin_id).first()
            if not existing:
                admin = User(supervisor_code=admin_id, name="Admin", role="admin", is_active=True)
                db.session.add(admin)
                db.session.commit()


def find_supervisor_from_query(q: str):
    """حاول إيجاد سوبرفايزر من نص البحث: يطابق ID بدقة أو الاسم بدقة (غير حساس لحالة الأحرف)."""
    if not q:
        return None
    # مطابقة مباشرة مع supervisor_code
    sup = User.query.filter(
        User.role == "supervisor",
        User.supervisor_code == q
    ).first()
    if sup:
        return sup
    # مطابقة دقيقة للاسم (case-insensitive)
    qlow = q.lower()
    sup = User.query.filter(
        User.role == "supervisor",
        func.lower(User.name) == qlow
    ).first()
    return sup

def _sqlite_table_columns(table_name: str) -> set[str]:
    """Return existing column names for a SQLite table."""
    with db.engine.connect() as con:
        rows = con.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
        # كل صف: (cid, name, type, notnull, dflt_value, pk)
        return {r[1] for r in rows}

def _ensure_supervisor_eval_columns():
    """Add missing columns to supervisor_evaluation to match the new model."""
    required = {
        # Targets
        "t1_text":        "t1_text VARCHAR(255) DEFAULT ''",
        "t1_percent":     "t1_percent FLOAT DEFAULT 0",
        "t1_remarks":     "t1_remarks VARCHAR(255) DEFAULT ''",
        "t2_text":        "t2_text VARCHAR(255) DEFAULT ''",
        "t2_percent":     "t2_percent FLOAT DEFAULT 0",
        "t2_remarks":     "t2_remarks VARCHAR(255) DEFAULT ''",
        "t3_text":        "t3_text VARCHAR(255) DEFAULT ''",
        "t3_percent":     "t3_percent FLOAT DEFAULT 0",
        "t3_remarks":     "t3_remarks VARCHAR(255) DEFAULT ''",
        "t4_text":        "t4_text VARCHAR(255) DEFAULT ''",
        "t4_percent":     "t4_percent FLOAT DEFAULT 0",
        "t4_remarks":     "t4_remarks VARCHAR(255) DEFAULT ''",

        # Performance 1..5 + comments
        "p_punctuality":      "p_punctuality INTEGER DEFAULT 0",
        "c_punctuality":      "c_punctuality VARCHAR(255) DEFAULT ''",
        "p_quality":          "p_quality INTEGER DEFAULT 0",
        "c_quality":          "c_quality VARCHAR(255) DEFAULT ''",
        "p_productivity":     "p_productivity INTEGER DEFAULT 0",
        "c_productivity":     "c_productivity VARCHAR(255) DEFAULT ''",
        "p_communication":    "p_communication INTEGER DEFAULT 0",
        "c_communication":    "c_communication VARCHAR(255) DEFAULT ''",
        "p_problemsolving":   "p_problemsolving INTEGER DEFAULT 0",
        "c_problemsolving":   "c_problemsolving VARCHAR(255) DEFAULT ''",
        "p_compliance":       "p_compliance INTEGER DEFAULT 0",
        "c_compliance":       "c_compliance VARCHAR(255) DEFAULT ''",

        # Notes
        "strengths":       "strengths TEXT DEFAULT ''",
        "improvements":    "improvements TEXT DEFAULT ''",
        "training_needed": "training_needed TEXT DEFAULT ''",

        # Totals
        "targets_score":   "targets_score FLOAT DEFAULT 0",
        "perf_score":      "perf_score FLOAT DEFAULT 0",
        "total_score":     "total_score FLOAT DEFAULT 0",
        "overall_band":    "overall_band VARCHAR(30) DEFAULT ''",
    }

    table = "supervisor_evaluation"
    existing = _sqlite_table_columns(table)
    missing = [ddl for col, ddl in required.items() if col not in existing]
    if not missing:
        return
    with db.engine.begin() as con:
        for ddl in missing:
            con.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))

@app.before_request
def _inject_globals():
    u = cur_user()
    g.user = u
    g.role = (u.role if u else None)
    g.get = lambda key, default=None: getattr(g, key, default)

def cur_user():
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not cur_user():
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        u = cur_user()
        if not u or u.role != "admin":
            abort(403)
        return fn(*args, **kwargs)
    return wrapper

def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()

def validate_week_sun_to_thu(start: date, end: date) -> Tuple[bool, str]:
    if start.weekday() != WEEK_START_WEEKDAY:
        return False, "Week must start on Sunday."
    if end.weekday() != WEEK_END_WEEKDAY:
        return False, "Week must end on Thursday."
    if (end - start).days != 4:
        return False, "Week must be exactly 5 days (Sun→Thu)."
    if start > end:
        return False, "Start date must be before end date."
    return True, ""

def compute_scores(ev: Evaluation) -> None:
    per = WEIGHTS["target_per_row"]
    t_score = 0.0
    for pct in [ev.t1_percent, ev.t2_percent, ev.t3_percent, ev.t4_percent]:
        v = max(0.0, min(100.0, float(pct or 0)))
        t_score += (v / 100.0) * per
    items = {
        "punctuality": ev.p_punctuality,
        "quality": ev.p_quality,
        "productivity": ev.p_productivity,
        "communication": ev.p_communication,
        "problemsolving": ev.p_problemsolving,
        "compliance": ev.p_compliance,
    }
    p_score = 0.0
    for key, rating in items.items():
        rating = int(rating or 0)
        weight = WEIGHTS["perf_items"][key]
        p_score += (rating / WEIGHTS["perf_scale_max"]) * weight

    total = round(t_score + p_score, 2)
    if total >= WEIGHTS["bands"]["excellent"]:
        band = "Excellent"
    elif total >= WEIGHTS["bands"]["good"]:
        band = "Good"
    elif total >= WEIGHTS["bands"]["satisfactory"]:
        band = "Satisfactory"
    else:
        band = "Needs Improvement"

    ev.targets_score = round(t_score, 2)
    ev.perf_score = round(p_score, 2)
    ev.total_score = total
    ev.overall_band = band

def compute_supervisor_scores(se: SupervisorEvaluation) -> None:
    items = {
        "leadership": se.p_leadership,
        "communication": se.p_communication,
        "scheduling": se.p_scheduling,
        "compliance": se.p_compliance,
        "team_support": se.p_team_support,
        "reporting": se.p_reporting,
    }
    per_item_weight = 100.0 / len(items)
    p_score = 0.0
    for _, rating in items.items():
        rating = int(rating or 0)
        p_score += (rating / SE_WEIGHTS["perf_scale_max"]) * per_item_weight

    total = round(p_score, 2)
    if total >= SE_WEIGHTS["bands"]["excellent"]:
        band = "Excellent"
    elif total >= SE_WEIGHTS["bands"]["good"]:
        band = "Good"
    elif total >= SE_WEIGHTS["bands"]["satisfactory"]:
        band = "Satisfactory"
    else:
        band = "Needs Improvement"

    se.perf_score = total
    se.total_score = total
    se.overall_band = band

def default_week_today() -> Tuple[date, date]:
    today = date.today()
    days_since_sun = (today.weekday() - WEEK_START_WEEKDAY) % 7
    start = today - timedelta(days=days_since_sun)
    end = start + timedelta(days=4)
    return start, end

# ===================== Routes =====================
@app.route("/")
def index():
    u = cur_user()
    if not u:
        return redirect(url_for("login"))
    if u.role == "admin":
        return redirect(url_for("admin_kpi"))   # ← الأدمن يروح للـ KPI
    return redirect(url_for("employees"))       # المشرف يروح لموظفيه

# اختيار المشرف والأسبوع لطباعة الحزمة
@app.route("/site/print", methods=["GET", "POST"])
@login_required
def site_print_select():
    u = cur_user()
    if not u or u.role != "site_supervisor":
        abort(403)

    # المشرفين المرتبطين بهذا الـ Site Supervisor
    links = (db.session.query(SiteSupervisorMap, User)
             .join(User, SiteSupervisorMap.supervisor_id == User.id)
             .filter(SiteSupervisorMap.site_sup_id == u.id)
             .order_by(User.supervisor_code.asc())
             .all())
    supervisors = [sup for _, sup in links]
    ws, we = default_week_today()

    if request.method == "POST":
        sup_user_id = int(request.form["sup_user_id"])
        ws = parse_date(request.form["week_start"])
        we = parse_date(request.form["week_end"])
        ok, msg = validate_week_sun_to_thu(ws, we)
        if not ok:
            flash(msg, "danger")
            return redirect(url_for("site_print_select"))
        # نستخدم GET حتى تكون قابلة لإعادة الفتح
        return redirect(url_for("site_print_bundle",
                                sup_user_id=sup_user_id,
                                week_start=ws.isoformat(),
                                week_end=we.isoformat()))
    return render_template("site_print_select.html", supervisors=supervisors, ws=ws, we=we)

@app.route("/admin/kpi", methods=["GET", "POST"])
@admin_required
def admin_kpi():
    if request.method == "POST":
        ws = parse_date(request.form["week_start"])
        we = parse_date(request.form["week_end"])
        ok, msg = validate_week_sun_to_thu(ws, we)
        if not ok:
            flash(msg, "danger")
            return redirect(url_for("admin_kpi"))
        return redirect(url_for("admin_kpi", week_start=ws.isoformat(), week_end=we.isoformat()))

    # تواريخ الأسبوع الحالي الافتراضي أو من الـquerystring
    ws, we = default_week_today()
    if request.args.get("week_start") and request.args.get("week_end"):
        ws = parse_date(request.args["week_start"])
        we = parse_date(request.args["week_end"])
    pws, pwe = previous_week_range(ws)

    # ---------- جزء السوبرفايزر (Supervisor → Employees) ----------
    supervisors = User.query.filter_by(role="supervisor", is_active=True)\
                            .order_by(User.supervisor_code.asc()).all()

    emp_totals_map = dict(
        db.session.query(Employee.user_id, func.count(Employee.id))
        .filter(Employee.is_active == True)
        .group_by(Employee.user_id).all()
    )

    emp_cnt_this = dict(
        db.session.query(Employee.user_id, func.count(Evaluation.id))
        .join(Evaluation, Evaluation.employee_id == Employee.id)
        .filter(Evaluation.week_start == ws, Evaluation.week_end == we)
        .group_by(Employee.user_id).all()
    )

    emp_cnt_prev = dict(
        db.session.query(Employee.user_id, func.count(Evaluation.id))
        .join(Evaluation, Evaluation.employee_id == Employee.id)
        .filter(Evaluation.week_start == pws, Evaluation.week_end == pwe)
        .group_by(Employee.user_id).all()
    )

    sup_rows = []
    sum_target_emp = 0
    sum_done_emp = 0
    growth_pool_prev = 0
    growth_pool_curr = 0

    for sup in supervisors:
        target = emp_totals_map.get(sup.id, 0)        # عدد موظفيه
        done_c = emp_cnt_this.get(sup.id, 0)          # قيّم كم موظف هذا الأسبوع
        done_p = emp_cnt_prev.get(sup.id, 0)          # الأسبوع الماضي
        coverage = (done_c / target * 100.0) if target else None

        sum_target_emp += target
        sum_done_emp += done_c

        # يدخل في حسبة النمو فقط إذا عنده تقييم في الأسبوعين
        if done_c > 0 and done_p > 0:
            growth_pool_prev += done_p
            growth_pool_curr += done_c

        sup_rows.append({
            "id": sup.id,
            "code": sup.supervisor_code,
            "name": sup.name,
            "target": target,
            "done_c": done_c,
            "done_p": done_p,
            "coverage": coverage,
            "delta": (done_c - done_p),
            "trend": ("up" if done_c > done_p else "down" if done_c < done_p else "flat")
        })

    # ---------- جزء مشرفي السايت (Site Supervisor → Supervisors) ----------
    site_sups = User.query.filter_by(role="site_supervisor", is_active=True)\
                          .order_by(User.supervisor_code.asc()).all()

    assigned_counts = dict(
        db.session.query(SiteSupervisorMap.site_sup_id,
                         func.count(func.distinct(SiteSupervisorMap.supervisor_id)))
        .group_by(SiteSupervisorMap.site_sup_id).all()
    )

    se_cnt_this = dict(
        db.session.query(SupervisorEvaluation.evaluator_id, func.count(SupervisorEvaluation.id))
        .filter(SupervisorEvaluation.week_start == ws, SupervisorEvaluation.week_end == we)
        .group_by(SupervisorEvaluation.evaluator_id).all()
    )

    se_cnt_prev = dict(
        db.session.query(SupervisorEvaluation.evaluator_id, func.count(SupervisorEvaluation.id))
        .filter(SupervisorEvaluation.week_start == pws, SupervisorEvaluation.week_end == pwe)
        .group_by(SupervisorEvaluation.evaluator_id).all()
    )

    site_rows = []
    sum_target_sup = 0
    sum_done_sup = 0
    for s in site_sups:
        target = assigned_counts.get(s.id, 0)      # عدد السوبرفايزر المعيّنين له
        done_c = se_cnt_this.get(s.id, 0)          # كم سوبر فايزر قيّمه هذا الأسبوع
        done_p = se_cnt_prev.get(s.id, 0)
        coverage = (done_c / target * 100.0) if target else None

        sum_target_sup += target
        sum_done_sup += done_c

        if done_c > 0 and done_p > 0:
            growth_pool_prev += done_p
            growth_pool_curr += done_c

        site_rows.append({
            "id": s.id,
            "code": s.supervisor_code,
            "name": s.name,
            "target": target,
            "done_c": done_c,
            "done_p": done_p,
            "coverage": coverage,
            "delta": (done_c - done_p),
            "trend": ("up" if done_c > done_p else "down" if done_c < done_p else "flat")
        })

    # ---------- إجماليات ----------
    total_this_week = (
        db.session.query(func.count(Evaluation.id))
        .filter(Evaluation.week_start == ws, Evaluation.week_end == we).scalar()
        +
        db.session.query(func.count(SupervisorEvaluation.id))
        .filter(SupervisorEvaluation.week_start == ws, SupervisorEvaluation.week_end == we).scalar()
    )

    total_prev_week = (
        db.session.query(func.count(Evaluation.id))
        .filter(Evaluation.week_start == pws, Evaluation.week_end == pwe).scalar()
        +
        db.session.query(func.count(SupervisorEvaluation.id))
        .filter(SupervisorEvaluation.week_start == pws, SupervisorEvaluation.week_end == pwe).scalar()
    )

    # نسبة النمو (تحسب فقط للمشاركين اللي عندهم تقييم في الأسبوعين)
    growth_pct = 0.0
    if growth_pool_prev > 0:
        growth_pct = (growth_pool_curr - growth_pool_prev) / growth_pool_prev * 100.0

    # تغطية شاملة
    overall_emp_coverage = (sum_done_emp / sum_target_emp * 100.0) if sum_target_emp else None
    overall_sup_coverage = (sum_done_sup / sum_target_sup * 100.0) if sum_target_sup else None

    return render_template(
        "admin_kpi.html",
        ws=ws, we=we, pws=pws, pwe=pwe,
        total_this_week=total_this_week, total_prev_week=total_prev_week,
        growth_pct=growth_pct,
        overall_emp_cov=overall_emp_coverage,
        overall_sup_cov=overall_sup_coverage,
        sup_rows=sup_rows, site_rows=site_rows
    )
@app.route("/admin/supervisor/<int:sup_id>/history")
@admin_required
def admin_supervisor_history(sup_id):
    sup = User.query.filter_by(id=sup_id, role="supervisor").first_or_404()
    evals = (SupervisorEvaluation.query
             .filter_by(supervisor_id=sup.id)
             .order_by(SupervisorEvaluation.week_start.desc())
             .all())
    return render_template("supervisor_history.html", sup=sup, evals=evals)

# عرض الحزمة الجاهزة للطباعة
@app.get("/site/print/bundle")
@login_required
def site_print_bundle():
    u = cur_user()
    if not u or u.role != "site_supervisor":
        abort(403)

    sup_user_id = int(request.args.get("sup_user_id", "0"))
    week_start = request.args.get("week_start")
    week_end   = request.args.get("week_end")
    if not (sup_user_id and week_start and week_end):
        return redirect(url_for("site_print_select"))

    ws = parse_date(week_start); we = parse_date(week_end)

    # تأكد أن هذا المشرف ضمن قوائم هذا الـ Site Supervisor
    link = SiteSupervisorMap.query.filter_by(site_sup_id=u.id, supervisor_id=sup_user_id).first()
    if not link:
        abort(403)

    supervisor = db.session.get(User, sup_user_id) or abort(404)

    # جميع موظفي هذا المشرف
    employees = Employee.query.filter_by(user_id=sup_user_id, is_active=True).order_by(Employee.name.asc()).all()
    emp_ids = [e.id for e in employees]

    # كل التقييمات لهؤلاء الموظفين في الأسبوع المحدد
    eval_rows = (db.session.query(Evaluation, Employee)
                 .join(Employee, Evaluation.employee_id == Employee.id)
                 .filter(Evaluation.week_start == ws,
                         Evaluation.week_end == we,
                         Employee.id.in_(emp_ids))
                 .order_by(Employee.name.asc())
                 .all())

    # جهّز قائمة مطبوعات: (ev, emp, evaluator_user)
    evals = []
    seen_emp = set()
    for ev, emp in eval_rows:
        evaluator = db.session.get(User, ev.evaluator_id)
        evals.append((ev, emp, evaluator))
        seen_emp.add(emp.id)

    # من لم يتم تقييمهم
    not_evaluated = [emp for emp in employees if emp.id not in seen_emp]

    return render_template(
        "site_print_bundle.html",
        supervisor=supervisor, ws=ws, we=we,
        evals=evals, not_evaluated=not_evaluated
    )
@app.route("/admin")
@admin_required
def admin_home():
    return redirect(url_for("admin_kpi"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        code = (request.form.get("code") or request.form.get("sup_code") or "").strip()
        if not code:
            flash("Please enter your Supervisor ID.", "danger")
            return redirect(url_for("login"))
        user = User.query.filter_by(supervisor_code=code, is_active=True).first()
        if not user:
            flash("ID not found or inactive. Contact Admin.", "danger")
            return redirect(url_for("login"))
        session["user_id"] = user.id
        flash("Signed in successfully.", "success")
        if user.role == "admin":
            return redirect(url_for("admin_kpi"))
        if user.role == "site_supervisor":
            return redirect(url_for("site_supervisors"))
        return redirect(url_for("employees"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Signed out.", "info")
    return redirect(url_for("login"))

# ----- Admin: Users -----
@app.route("/admin/users", methods=["GET", "POST"])
@admin_required
def admin_users():
    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        name = (request.form.get("name") or "").strip()
        role = request.form.get("role") or "supervisor"  # can be site_supervisor
        if not code:
            flash("Supervisor ID is required.", "danger")
        else:
            existing = User.query.filter_by(supervisor_code=code).first()
            if existing:
                flash("This ID already exists.", "warning")
            else:
                u = User(supervisor_code=code, name=name, role=role, is_active=True)
                db.session.add(u)
                db.session.commit()
                flash("User added.", "success")
        return redirect(url_for("admin_users"))
    users = User.query.order_by(User.role.desc(), User.supervisor_code.asc()).all()
    return render_template("admin_users.html", users=users)
# --- Admin: Print hub (select week) ---
@app.route("/admin/print", methods=["GET", "POST"])
@admin_required
def admin_print_select():
    ws, we = default_week_today()
    if request.method == "POST":
        ws = parse_date(request.form["week_start"])
        we = parse_date(request.form["week_end"])
        ok, msg = validate_week_sun_to_thu(ws, we)
        if not ok:
            flash(msg, "danger")
            return redirect(url_for("admin_print_select"))
        return redirect(url_for("admin_print_bundle",
                                week_start=ws.isoformat(),
                                week_end=we.isoformat()))
    return render_template("admin_print_select.html", ws=ws, we=we)


# --- Admin: Print bundle (all reports + blanks for missing) ---
@app.route("/admin/print/bundle")
@admin_required
def admin_print_bundle():
    week_start = request.args.get("week_start")
    week_end   = request.args.get("week_end")
    if not (week_start and week_end):
        return redirect(url_for("admin_print_select"))

    ws = parse_date(week_start); we = parse_date(week_end)

    # كل المشرفين الفعّالين
    supervisors = User.query.filter_by(role="supervisor", is_active=True)\
                            .order_by(User.supervisor_code.asc()).all()
    sup_by_id = {s.id: s for s in supervisors}

    # خريطة (المشرف ← أحد مشرفي السايت المربوطين به إن وجد)
    ssm_rows = SiteSupervisorMap.query.all()
    site_by_sup: dict[int, User | None] = {}
    for row in ssm_rows:
        # اختر أول مشرف سايت فقط للعرض
        if row.supervisor_id not in site_by_sup:
            site_by_sup[row.supervisor_id] = db.session.get(User, row.site_sup_id)

    # تقييمات السايت لهذا الأسبوع
    se_list = SupervisorEvaluation.query.filter_by(week_start=ws, week_end=we).all()
    se_by_sup: dict[int, SupervisorEvaluation] = {se.supervisor_id: se for se in se_list}

    # صفحات تقييم السايت الموجودة
    site_eval_pages = []
    for se in se_list:
        sup = sup_by_id.get(se.supervisor_id)
        if not sup:
            continue
        evaluator = db.session.get(User, se.evaluator_id)
        site_eval_pages.append((se, sup, evaluator))

    # صفحات السايت المفقودة (فارغة)
    site_missing_pages = []
    for sup in supervisors:
        if sup.id not in se_by_sup:
            site_eval_pages_sup = site_by_sup.get(sup.id)  # قد يكون None
            site_missing_pages.append((sup, site_eval_pages_sup))

    # الموظفون لكل مشرف
    employees = Employee.query.filter(Employee.user_id.in_([s.id for s in supervisors]),
                                      Employee.is_active == True)\
                              .order_by(Employee.name.asc()).all()
    emp_ids = [e.id for e in employees]
    emp_by_id = {e.id: e for e in employees}
    sup_id_by_emp_id = {e.id: e.user_id for e in employees}

    # كل تقييمات الموظفين لهذا الأسبوع
    ev_list = Evaluation.query.filter(Evaluation.week_start == ws,
                                      Evaluation.week_end == we,
                                      Evaluation.employee_id.in_(emp_ids)).all()
    ev_by_emp: dict[int, Evaluation] = {ev.employee_id: ev for ev in ev_list}

    # صفحات تقييم الموظفين الموجودة
    emp_eval_pages = []
    for ev in ev_list:
        emp = emp_by_id.get(ev.employee_id)
        if not emp:
            continue
        sup = sup_by_id.get(emp.user_id)
        evaluator = db.session.get(User, ev.evaluator_id)
        emp_eval_pages.append((ev, emp, sup, evaluator))

    # الموظفون غير المُقيّمين
    emp_missing_pages = []
    for emp in employees:
        if emp.id not in ev_by_emp:
            sup = sup_by_id.get(emp.user_id)
            emp_missing_pages.append((emp, sup))

    return render_template(
        "admin_print_bundle.html",
        ws=ws, we=we,
        site_eval_pages=site_eval_pages,
        site_missing_pages=site_missing_pages,
        emp_eval_pages=emp_eval_pages,
        emp_missing_pages=emp_missing_pages
    )

@app.post("/admin/users/<int:user_id>/toggle")
@admin_required
def admin_users_toggle(user_id):
    u = db.session.get(User, user_id) or abort(404)
    if u.role == "admin":
        flash("Cannot deactivate admin.", "warning")
    else:
        u.is_active = not u.is_active
        db.session.commit()
        flash("Status updated.", "success")
    return redirect(url_for("admin_users"))

@app.post("/admin/users/<int:user_id>/role")
@admin_required
def admin_users_set_role(user_id):
    u = db.session.get(User, user_id) or abort(404)
    new_role = (request.form.get("role") or "").strip()

    # لا نسمح بتعديل دور الأدمن من هنا
    if u.role == "admin":
        flash("Cannot change role of admin here.", "warning")
        return redirect(url_for("admin_users"))

    if new_role not in ["supervisor", "site_supervisor"]:
        flash("Invalid role.", "danger")
    else:
        u.role = new_role
        db.session.commit()
        flash("Role updated.", "success")

    return redirect(url_for("admin_users"))


# ----- Supervisor: Employees -----
@app.route("/employees", methods=["GET", "POST"])
@login_required
def employees():
    u = cur_user()
    if u.role != "supervisor" and u.role != "admin":
        # only supervisors (and admin viewing his own) use this page
        abort(403)
    if request.method == "POST":
        emp_number = (request.form.get("emp_number") or "").strip()
        name = (request.form.get("name") or "").strip()
        department = (request.form.get("department") or "").strip()
        site = (request.form.get("site") or "").strip()
        if not emp_number or not name:
            flash("Employee Number and Name are required.", "danger")
        else:
            e = Employee(
                user_id=u.id, emp_number=emp_number, name=name,
                department=department, site=site, is_active=True
            )
            db.session.add(e)
            db.session.commit()
            flash("Employee added.", "success")
        return redirect(url_for("employees"))
    emps = Employee.query.filter_by(user_id=u.id, is_active=True).order_by(Employee.name).all()
    return render_template("employees.html", user=u, employees=emps)

# ----- New Evaluation (employee) -----
@app.route("/evaluate/<int:emp_id>/new", methods=["GET", "POST"])
@login_required
def evaluate_new(emp_id):
    u = cur_user()
    emp = Employee.query.filter_by(id=emp_id, user_id=u.id).first_or_404()

    if request.method == "POST":
        week_start = parse_date(request.form.get("week_start"))
        week_end   = parse_date(request.form.get("week_end"))
        ok, msg = validate_week_sun_to_thu(week_start, week_end)
        if not ok:
            flash(msg, "danger")
            return redirect(url_for("evaluate_new", emp_id=emp.id))

        if Evaluation.query.filter_by(employee_id=emp.id, week_start=week_start, week_end=week_end).first():
            flash("An evaluation for this week already exists.", "warning")
            return redirect(url_for("report_employee", emp_id=emp.id,
                                    week_start=week_start.isoformat(), week_end=week_end.isoformat()))

        ev = Evaluation(
            employee_id=emp.id, evaluator_id=u.id,
            week_start=week_start, week_end=week_end,

            t1_text=request.form.get("t1_text",""), t1_percent=float(request.form.get("t1_percent") or 0), t1_remarks=request.form.get("t1_remarks",""),
            t2_text=request.form.get("t2_text",""), t2_percent=float(request.form.get("t2_percent") or 0), t2_remarks=request.form.get("t2_remarks",""),
            t3_text=request.form.get("t3_text",""), t3_percent=float(request.form.get("t3_percent") or 0), t3_remarks=request.form.get("t3_remarks",""),
            t4_text=request.form.get("t4_text",""), t4_percent=float(request.form.get("t4_percent") or 0), t4_remarks=request.form.get("t4_remarks",""),

            p_punctuality=int(request.form.get("p_punctuality") or 0), c_punctuality=request.form.get("c_punctuality",""),
            p_quality=int(request.form.get("p_quality") or 0), c_quality=request.form.get("c_quality",""),
            p_productivity=int(request.form.get("p_productivity") or 0), c_productivity=request.form.get("c_productivity",""),
            p_communication=int(request.form.get("p_communication") or 0), c_communication=request.form.get("c_communication",""),
            p_problemsolving=int(request.form.get("p_problemsolving") or 0), c_problemsolving=request.form.get("c_problemsolving",""),
            p_compliance=int(request.form.get("p_compliance") or 0), c_compliance=request.form.get("c_compliance",""),

            strengths=request.form.get("strengths",""),
            improvements=request.form.get("improvements",""),
            training_needed=request.form.get("training_needed",""),
        )
        compute_scores(ev)
        db.session.add(ev)
        db.session.commit()
        flash("Evaluation saved.", "success")
        return redirect(url_for("report_employee", emp_id=emp.id,
                                week_start=week_start.isoformat(), week_end=week_end.isoformat()))

    ws, we = default_week_today()
    return render_template("eval_form.html", employee=emp, ws=ws, we=we, weights=WEIGHTS)

# ----- Reports picker (generic) -----
@app.route("/reports")
@login_required
def reports_picker():
    ws, we = default_week_today()
    return render_template("report_picker.html", ws=ws, we=we)

# ----- Employee report (detailed) -----
@app.route("/reports/employee/<int:emp_id>")
@login_required
def report_employee(emp_id):
    u = cur_user()
    # admin can view any employee; supervisor only his own
    if u.role == "admin":
        emp = Employee.query.get_or_404(emp_id)
    else:
        emp = Employee.query.filter_by(id=emp_id, user_id=u.id).first_or_404()

    week_start = request.args.get("week_start")
    week_end = request.args.get("week_end")
    if not week_start or not week_end:
        flash("Missing week dates.", "warning")
        return redirect(url_for("employees"))

    ws = parse_date(week_start); we = parse_date(week_end)
    ev = Evaluation.query.filter_by(employee_id=emp.id, week_start=ws, week_end=we).first_or_404()

    evaluator = db.session.get(User, ev.evaluator_id)
    evaluator_code = evaluator.supervisor_code if evaluator else ""
    evaluator_name = evaluator.name if (evaluator and evaluator.name) else ""

    return render_template(
        "report_employee.html",
        employee=emp,
        ev=ev,
        evaluator_code=evaluator_code,
        evaluator_name=evaluator_name,
    )

# ----- Supervisor reports picker (for supervisors) -----
@app.route("/reports/supervisor/select", methods=["GET", "POST"])
@login_required
def supervisor_report_select():
    u = cur_user()
    employees = Employee.query.filter_by(user_id=u.id, is_active=True).order_by(Employee.name.asc()).all()

    if request.method == "POST":
        emp_id = int(request.form["emp_id"])
        ws = parse_date(request.form["week_start"])
        we = parse_date(request.form["week_end"])
        ok, msg = validate_week_sun_to_thu(ws, we)
        if not ok:
            flash(msg, "danger")
            return redirect(url_for("supervisor_report_select"))
        return redirect(url_for("report_employee",
                                emp_id=emp_id,
                                week_start=ws.isoformat(),
                                week_end=we.isoformat()))
    ws, we = default_week_today()
    return render_template("supervisor_report_picker.html", employees=employees, ws=ws, we=we)

# ----- Admin: Reports picker -----
@app.route("/admin/reports", methods=["GET", "POST"])
@admin_required
def admin_report_picker():
    if request.method == "POST":
        ws = parse_date(request.form["week_start"])
        we = parse_date(request.form["week_end"])
        ok, msg = validate_week_sun_to_thu(ws, we)
        if not ok:
            flash(msg, "danger")
            return redirect(url_for("admin_report_picker"))
        return redirect(url_for("admin_reports_all",
                                week_start=ws.isoformat(),
                                week_end=we.isoformat()))
    ws, we = default_week_today()
    return render_template("report_admin_picker.html", ws=ws, we=we)

# ----- Admin: consolidated employee reports -----
@app.route("/admin/reports/all")
@admin_required
def admin_reports_all():
    week_start = request.args.get("week_start")
    week_end   = request.args.get("week_end")
    q = (request.args.get("q") or "").strip()

    if not (week_start and week_end):
        return redirect(url_for("admin_report_picker"))

    ws = parse_date(week_start)
    we = parse_date(week_end)

    # حدّد سوبرفايزر من نص البحث (ID أو اسم مطابق تمامًا)
    focus_sup = find_supervisor_from_query(q)

    # الاستعلام الأساسي
    query = (db.session.query(Evaluation, Employee, User)
             .join(Employee, Evaluation.employee_id == Employee.id)
             .join(User, Employee.user_id == User.id)
             .filter(Evaluation.week_start == ws, Evaluation.week_end == we))

    if focus_sup:
        query = query.filter(Employee.user_id == focus_sup.id)

    if q and not focus_sup:
        like = f"%{q}%"
        query = query.filter(or_(
            Employee.name.ilike(like),
            Employee.emp_number.ilike(like),
            Employee.department.ilike(like),
            Employee.site.ilike(like),
            User.supervisor_code.ilike(like),
            Evaluation.overall_band.ilike(like),
        ))

    rows = query.order_by(User.supervisor_code.asc(), Employee.name.asc()).all()

    # ----- KPI + غير المُقيّمين عند تحديد سوبرفايزر -----
    sup_cov_pct = None
    missing_emps = []
    total_emp = 0
    done_emp_count = 0

    if focus_sup:
        emps = (Employee.query
                .filter_by(user_id=focus_sup.id, is_active=True)
                .order_by(Employee.name.asc())
                .all())
        total_emp = len(emps)
        if total_emp > 0:
            emp_ids = [e.id for e in emps]
            done_ids = set(
                r[0] for r in db.session.query(Evaluation.employee_id)
                .filter(Evaluation.week_start == ws,
                        Evaluation.week_end == we,
                        Evaluation.employee_id.in_(emp_ids))
                .all()
            )
            done_emp_count = len(done_ids)
            sup_cov_pct = (done_emp_count / total_emp) * 100.0
            missing_emps = [e for e in emps if e.id not in done_ids]

    # ----- قائمة السوبرفايزر + تفريد المقترحات للـ datalist -----
    supervisors = (User.query
                   .filter_by(role="supervisor", is_active=True)
                   .order_by(User.name.asc(), User.supervisor_code.asc())
                   .all())

    # نحضّر [(value, label)] بدون تكرار (case-insensitive)
    sup_suggestions = []
    seen = set()

    # أولاً: قيم الـID (هي فريدة غالبًا)
    for s in supervisors:
        val = (s.supervisor_code or "").strip()
        if not val:
            continue
        key = val.lower()
        if key in seen:
            continue
        label = f"{s.name or '—'} — ID: {s.supervisor_code}"
        sup_suggestions.append((val, label))
        seen.add(key)

    # ثانيًا: الأسماء (قد تتكرر، لذلك نفردها)
    for s in supervisors:
        nm = (s.name or "").strip()
        if not nm:
            continue
        key = nm.lower()
        if key in seen:
            continue
        label = f"{nm} — ID: {s.supervisor_code}"
        sup_suggestions.append((nm, label))
        seen.add(key)

    return render_template(
        "report_admin_all.html",
        ws=ws, we=we, rows=rows, q=q,
        focus_sup=focus_sup,
        sup_cov_pct=sup_cov_pct,
        total_emp=total_emp,
        done_emp_count=done_emp_count,
        missing_emps=missing_emps,
        supervisors=supervisors,
        sup_suggestions=sup_suggestions,  # ← استخدم هذه في القالب
    )


# ----- Supervisor weekly list (own employees) -----
@app.route("/reports/supervisor")
@login_required
def report_supervisor():
    u = cur_user()
    week_start = request.args.get("week_start"); week_end = request.args.get("week_end")
    if not week_start or not week_end:
        flash("Choose a week (start & end).", "warning")
        return redirect(url_for("employees"))
    ws = parse_date(week_start); we = parse_date(week_end)
    evals = (db.session.query(Evaluation, Employee)
             .join(Employee, Evaluation.employee_id == Employee.id)
             .filter(Employee.user_id == u.id,
                     Evaluation.week_start == ws, Evaluation.week_end == we)
             .order_by(Employee.name.asc()).all())
    return render_template("report_supervisor.html", user=u, ws=ws, we=we, evals=evals)

# ----- Admin: All Employees + search + history -----
@app.route("/admin/employees")
@admin_required
def admin_employees():
    q = (request.args.get("q") or "").strip()

    emp_q = (db.session.query(Employee, User)
             .join(User, Employee.user_id == User.id))
    if q:
        like = f"%{q}%"
        emp_q = emp_q.filter(or_(
            Employee.name.ilike(like),
            Employee.emp_number.ilike(like),
            Employee.department.ilike(like),
            Employee.site.ilike(like),
            User.supervisor_code.ilike(like),
            User.name.ilike(like),
        ))
    emp_rows = emp_q.order_by(User.supervisor_code.asc(), Employee.name.asc()).all()

    users_q = User.query.filter(User.role.in_(["supervisor", "site_supervisor"]))
    if q:
        like = f"%{q}%"
        users_q = users_q.filter(or_(
            User.supervisor_code.ilike(like),
            User.name.ilike(like),
            User.role.ilike(like),
        ))
    users = users_q.order_by(User.supervisor_code.asc()).all()

    return render_template("admin_employees.html", rows=emp_rows, users=users, q=q)

@app.route("/admin/employee/<int:emp_id>/history")
@admin_required
def admin_employee_history(emp_id):
    emp = Employee.query.get_or_404(emp_id)
    evals = (Evaluation.query.filter_by(employee_id=emp.id)
             .order_by(Evaluation.week_start.desc()).all())
    return render_template("employee_history.html", emp=emp, evals=evals)

# ===================== Site Supervisor Features =====================
# Manage assigned supervisors
@app.route("/site/supervisors", methods=["GET", "POST"])
@login_required
def site_supervisors():
    u = cur_user()
    if u.role != "site_supervisor":
        abort(403)

    if request.method == "POST":
        sup_code = (request.form.get("supervisor_code") or "").strip()
        sup_name = (request.form.get("supervisor_name") or "").strip()
        if not sup_code:
            flash("Please enter a Supervisor ID.", "danger")
            return redirect(url_for("site_supervisors"))

        # جرّب نلقى مستخدم بهذا الـID
        sup_user = User.query.filter_by(supervisor_code=sup_code).first()

        # لو ما وُجد: أنشئه كمشرف (Supervisor) مفعَّل
        if not sup_user:
            sup_user = User(
                supervisor_code=sup_code,
                name=sup_name,
                role="supervisor",
                is_active=True
            )
            db.session.add(sup_user)
            db.session.commit()
            flash("Supervisor user created and assigned.", "success")
        else:
            # لو موجود لكنه مو مشرف، ما نسمح بربطه
            if sup_user.role != "supervisor":
                flash("This ID exists but is not a Supervisor role.", "danger")
                return redirect(url_for("site_supervisors"))
            if not sup_user.is_active:
                flash("This Supervisor is inactive. Ask Admin to activate.", "warning")
                return redirect(url_for("site_supervisors"))

        # اربطه إن ما كان مرتبط مسبقًا
        exists = SiteSupervisorMap.query.filter_by(site_sup_id=u.id, supervisor_id=sup_user.id).first()
        if exists:
            flash("This supervisor is already assigned.", "warning")
        else:
            link = SiteSupervisorMap(site_sup_id=u.id, supervisor_id=sup_user.id)
            db.session.add(link)
            db.session.commit()
            flash("Supervisor assigned.", "success")

        return redirect(url_for("site_supervisors"))

    links = (db.session.query(SiteSupervisorMap, User)
             .join(User, SiteSupervisorMap.supervisor_id == User.id)
             .filter(SiteSupervisorMap.site_sup_id == u.id).all())
    return render_template("site_supervisors.html", links=links)

@app.post("/site/supervisors/<int:link_id>/remove")
@login_required
def site_supervisors_remove(link_id):
    u = cur_user()
    if u.role != "site_supervisor":
        abort(403)
    link = SiteSupervisorMap.query.get_or_404(link_id)
    if link.site_sup_id != u.id:
        abort(403)
    db.session.delete(link)
    db.session.commit()
    flash("Removed.", "success")
    return redirect(url_for("site_supervisors"))

# New evaluation for a supervisor
@app.route("/site/evaluate/<int:sup_user_id>/new", methods=["GET", "POST"])
@login_required
def site_evaluate_new(sup_user_id):
    u = cur_user()
    if u.role != "site_supervisor":
        abort(403)

    # تأكد أنه ضمن قائمته
    link = SiteSupervisorMap.query.filter_by(site_sup_id=u.id, supervisor_id=sup_user_id).first()
    if not link:
        abort(403)

    supervisor = User.query.get_or_404(sup_user_id)
    if request.method == "POST":
        ws = parse_date(request.form.get("week_start"))
        we = parse_date(request.form.get("week_end"))
        ok, msg = validate_week_sun_to_thu(ws, we)
        if not ok:
            flash(msg, "danger")
            return redirect(url_for("site_evaluate_new", sup_user_id=sup_user_id))

        if SupervisorEvaluation.query.filter_by(supervisor_id=sup_user_id, week_start=ws, week_end=we).first():
            flash("An evaluation for this supervisor already exists this week.", "warning")
            return redirect(url_for("site_report_supervisor", sup_user_id=sup_user_id,
                                    week_start=ws.isoformat(), week_end=we.isoformat()))

        se = SupervisorEvaluation(
            supervisor_id=sup_user_id, evaluator_id=u.id,
            week_start=ws, week_end=we,

            # Targets
            t1_text=request.form.get("t1_text",""), t1_percent=float(request.form.get("t1_percent") or 0), t1_remarks=request.form.get("t1_remarks",""),
            t2_text=request.form.get("t2_text",""), t2_percent=float(request.form.get("t2_percent") or 0), t2_remarks=request.form.get("t2_remarks",""),
            t3_text=request.form.get("t3_text",""), t3_percent=float(request.form.get("t3_percent") or 0), t3_remarks=request.form.get("t3_remarks",""),
            t4_text=request.form.get("t4_text",""), t4_percent=float(request.form.get("t4_percent") or 0), t4_remarks=request.form.get("t4_remarks",""),

            # Performance
            p_punctuality=int(request.form.get("p_punctuality") or 0), c_punctuality=request.form.get("c_punctuality",""),
            p_quality=int(request.form.get("p_quality") or 0), c_quality=request.form.get("c_quality",""),
            p_productivity=int(request.form.get("p_productivity") or 0), c_productivity=request.form.get("c_productivity",""),
            p_communication=int(request.form.get("p_communication") or 0), c_communication=request.form.get("c_communication",""),
            p_problemsolving=int(request.form.get("p_problemsolving") or 0), c_problemsolving=request.form.get("c_problemsolving",""),
            p_compliance=int(request.form.get("p_compliance") or 0), c_compliance=request.form.get("c_compliance",""),

            strengths=request.form.get("strengths",""),
            improvements=request.form.get("improvements",""),
            training_needed=request.form.get("training_needed",""),
        )

        # نفس المعادلة بالضبط
        compute_scores(se)
        db.session.add(se)
        db.session.commit()
        flash("Supervisor evaluation saved.", "success")
        return redirect(url_for("site_report_supervisor", sup_user_id=sup_user_id,
                                week_start=ws.isoformat(), week_end=we.isoformat()))

    ws, we = default_week_today()
    return render_template("site_eval_form.html", supervisor=supervisor, ws=ws, we=we, weights=WEIGHTS)

# Site supervisor report picker
@app.route("/site/reports/select", methods=["GET", "POST"])
@login_required
def site_report_select():
    u = cur_user()
    if u.role != "site_supervisor":
        abort(403)
    links = (db.session.query(SiteSupervisorMap, User)
             .join(User, SiteSupervisorMap.supervisor_id == User.id)
             .filter(SiteSupervisorMap.site_sup_id == u.id).all())
    supervisors = [su for _, su in links]

    if request.method == "POST":
        sup_user_id = int(request.form["sup_user_id"])
        ws = parse_date(request.form["week_start"])
        we = parse_date(request.form["week_end"])
        ok, msg = validate_week_sun_to_thu(ws, we)
        if not ok:
            flash(msg, "danger")
            return redirect(url_for("site_report_select"))
        return redirect(url_for("site_report_supervisor",
                                sup_user_id=sup_user_id,
                                week_start=ws.isoformat(),
                                week_end=we.isoformat()))
    ws, we = default_week_today()
    return render_template("site_report_picker.html", supervisors=supervisors, ws=ws, we=we)

# Detailed supervisor report
@app.route("/site/reports/supervisor/<int:sup_user_id>")
@login_required
def site_report_supervisor(sup_user_id):
    u = cur_user()
    if u.role not in ["site_supervisor", "admin"]:
        abort(403)
    if u.role == "site_supervisor":
        link = SiteSupervisorMap.query.filter_by(site_sup_id=u.id, supervisor_id=sup_user_id).first()
        if not link:
            abort(403)

    week_start = request.args.get("week_start")
    week_end = request.args.get("week_end")
    if not (week_start and week_end):
        flash("Missing week dates.", "warning")
        return redirect(url_for("site_report_select") if u.role == "site_supervisor" else url_for("admin_site_report_picker"))

    ws = parse_date(week_start); we = parse_date(week_end)
    se = SupervisorEvaluation.query.filter_by(supervisor_id=sup_user_id, week_start=ws, week_end=we).first_or_404()
    supervisor = User.query.get_or_404(sup_user_id)
    evaluator = User.query.get(se.evaluator_id)
    return render_template("site_report_supervisor.html", se=se, supervisor=supervisor, evaluator=evaluator)

# ----- Admin: Site reviews picker & consolidated -----
@app.route("/admin/site/reports", methods=["GET", "POST"])
@admin_required
def admin_site_report_picker():
    if request.method == "POST":
        ws = parse_date(request.form["week_start"])
        we = parse_date(request.form["week_end"])
        ok, msg = validate_week_sun_to_thu(ws, we)
        if not ok:
            flash(msg, "danger")
            return redirect(url_for("admin_site_report_picker"))
        return redirect(url_for("admin_site_reports_all",
                                week_start=ws.isoformat(),
                                week_end=we.isoformat()))
    ws, we = default_week_today()
    return render_template("site_admin_picker.html", ws=ws, we=we)

@app.route("/admin/site/reports/all")
@admin_required
def admin_site_reports_all():
    week_start = request.args.get("week_start")
    week_end   = request.args.get("week_end")
    q_raw = request.args.get("q") or ""
    q = q_raw.strip().lower()

    if not (week_start and week_end):
        return redirect(url_for("admin_site_report_picker"))

    ws = parse_date(week_start); we = parse_date(week_end)

    # اجلب جميع تقييمات الأسبوع واربط الأسماء يدويًا (أضمن من joins متعددة على User)
    rows = []
    se_list = SupervisorEvaluation.query.filter_by(week_start=ws, week_end=we).all()
    for se in se_list:
        sup_user  = db.session.get(User, se.supervisor_id)   # المشرف المُقيَّم
        site_user = db.session.get(User, se.evaluator_id)    # مشرف السايت المُقيِّم

        if q:
            hay = " ".join([
                sup_user.name or "", sup_user.supervisor_code or "",
                site_user.name or "", site_user.supervisor_code or "",
                se.overall_band or ""
            ]).lower()
            if q not in hay:
                continue
        rows.append((se, sup_user, site_user))

    return render_template("site_admin_all.html", ws=ws, we=we, rows=rows, q=q_raw)

# ===================== Main =====================
if __name__ == "__main__":
    ensure_db_and_admin()
    app.run(host="0.0.0.0", port=8080, debug=True)
