# === Imports ===
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — rely on real environment variables

import os, logging, urllib.parse, threading
from datetime import datetime, date, timedelta
from typing import Tuple
from functools import wraps
from collections import defaultdict, Counter
from flask import (
    Flask, render_template, render_template_string, request, redirect, url_for,
    session, flash, abort, g, Response, current_app, send_file, jsonify, make_response
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, or_, func, text
from sqlalchemy.exc import OperationalError
from typing import Optional
from datetime import timezone
from zoneinfo import ZoneInfo  # بايثون 3.9+ موجودة افتراضيًا

# === Paths (عرّف BASE_DIR أولاً) ===
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# === Flask app ===
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "nsh-safetrack-2024-a7f3c9e2b1d8k4m6p0q5r"
)
app.config["SQLALCHEMY_ECHO"] = False  # تم تعطيله في production
application = app 

# (اختياري) logging بعد إنشاء app
try:
    os.makedirs(os.path.join(BASE_DIR, "tmp"), exist_ok=True)
    fh = logging.FileHandler(os.path.join(BASE_DIR, "tmp", "app.log"))
    fh.setLevel(logging.INFO)
    app.logger.setLevel(logging.INFO)
    app.logger.addHandler(fh)
    app.logger.info("App booted")
except Exception as e:
    print("log init error:", e)

# ---- Database (FORCE MySQL ONLY) ----

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "zappimiw_nsh")
DB_USER = os.getenv("DB_USER", "zappimiw_nsh")
DB_PASS = os.getenv("DB_PASS", "")  # لا تضع كلمة المرور هنا — استخدم environment variable

# تأكد أن كل القيم موجودة
if not all([DB_HOST, DB_NAME, DB_USER, DB_PASS]):
    raise Exception("❌ Database environment variables are missing")

# ترميز كلمة المرور
safe_pass = urllib.parse.quote_plus(DB_PASS)

# ربط MySQL فقط (بدون SQLite نهائياً)
app.config["SQLALCHEMY_DATABASE_URI"] = (
    f"mysql+pymysql://{DB_USER}:{safe_pass}@{DB_HOST}/{DB_NAME}?charset=utf8mb4"
)

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# pool_recycle أقصر من wait_timeout على shared hosting (60-90s).
# pool_size=1 / max_overflow=2 يقلل الكونكشنات المتزامنة.
# أخطاء Command Out of Sync / Lost connection يتعامل معها _safe_db_teardown.
app.config["SQLALCHEMY_POOL_SIZE"]    = 1
app.config["SQLALCHEMY_MAX_OVERFLOW"] = 2
app.config["SQLALCHEMY_POOL_TIMEOUT"] = 20
app.config["SQLALCHEMY_POOL_RECYCLE"] = 25
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_use_lifo": True,
    "connect_args": {
        "connect_timeout": 10,
        "read_timeout": 30,
        "write_timeout": 30,
        "init_command": "SET SESSION wait_timeout=60, interactive_timeout=60",
    },
}

db = SQLAlchemy(app)

# ── CSRF Protection ──────────────────────────────────────────────
# API routes (Bearer token) are skipped; only web form POSTs are protected.
try:
    from flask_wtf.csrf import CSRFProtect, generate_csrf
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False  # نتحكم يدوياً عبر before_request
    csrf = CSRFProtect(app)

    @app.before_request
    def _csrf_protect():
        if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
            return
        if request.path.startswith("/api/"):
            return   # API routes use Bearer token — no CSRF needed
        csrf.protect()

    @app.after_request
    def _set_csrf_cookie(response):
        response.headers["X-CSRF-Token"] = generate_csrf()
        return response

except ImportError:
    pass  # Flask-WTF not installed yet — run: pip install Flask-WTF

# Ensure csrf_token is always available in templates even if Flask-WTF failed
if 'csrf_token' not in app.jinja_env.globals:
    app.jinja_env.globals['csrf_token'] = lambda: ''

# فلتر enumerate لـ Jinja2
app.jinja_env.filters['enumerate'] = enumerate

# ── Rate Limiter ─────────────────────────────────────────────────────
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    limiter = Limiter(
        app,
        key_func=get_remote_address,
        default_limits=[],
        storage_uri="memory://",
    )
except ImportError:
    limiter = None  # flask-limiter not installed — run: pip install flask-limiter

# decorator helper: applies rate limit only on POST (login attempts), not GET page loads
_login_limit = (limiter.limit("20 per minute", methods=["POST"]) if limiter is not None else lambda f: f)

# ── Swagger / OpenAPI ────────────────────────────────────────────────
try:
    from flasgger import Swagger
    app.config["SWAGGER"] = {
        "title":   "NSH SafeTrack API",
        "version": "1.0",
        "uiversion": 3,
        "specs_route": "/api/docs/",
        "description": "REST API for NSH SafeTrack — iOS + Web SaaS platform",
        "securityDefinitions": {
            "Bearer": {
                "type": "apiKey",
                "name": "Authorization",
                "in":   "header",
                "description": "JWT token: Bearer <token>",
            }
        },
        "security": [{"Bearer": []}],
        "specs": [{"endpoint": "apispec", "route": "/api/docs/apispec.json"}],
    }
    swagger = Swagger(app)
except ImportError:
    pass  # flasgger not installed — run: pip install flasgger

# طباعة للتأكد
print(f"USING DATABASE: {DB_USER}@{DB_HOST}/{DB_NAME}")
from werkzeug.exceptions import NotFound

@app.before_request
def load_logged_in_user():
    if request.endpoint and request.endpoint.startswith('static'):
        return
    user_id = session.get("user_id")
    try:
        g.user = db.session.get(User, user_id) if user_id else None
        g.role = g.user.role if g.user else None
        g.company_id = session.get("company_id")
        g.company = db.session.get(Company, g.company_id) if g.company_id else None
    except Exception:
        try:
            db.session.remove()
        except Exception:
            try:
                db.engine.dispose()
            except Exception:
                pass
        g.user = None
        g.role = None
        g.company_id = None
        g.company = None
    g._user_loaded = True


@app.teardown_appcontext
def _safe_db_teardown(exc):
    """يمنع أخطاء MySQL (Command Out of Sync, Lost connection) من كسر الـ WSGI response."""
    try:
        db.session.remove()
    except Exception:
        try:
            db.engine.dispose()
        except Exception:
            pass


# ===================== Constants =====================
WEIGHTS = {
    "targets_rows": 4,
    "target_per_row": 10,
    "perf_items": {
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

SE_WEIGHTS = {
    "perf_items": {
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


RIYADH_TZ = ZoneInfo("Asia/Riyadh")

def to_local(dt):
    if dt is None:
        return None
    # لو التاريخ naive (بدون tzinfo)، اعتبره UTC لأنه محفوظ بـ utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(RIYADH_TZ)
    
@app.template_filter("local_dt")
def local_dt(dt, fmt="%Y-%m-%d %H:%M"):
    dt_local = to_local(dt)
    return dt_local.strftime(fmt) if dt_local else "—"


@app.template_filter("from_json")
def from_json_filter(value):
    import json as _json
    try:
        return _json.loads(value) if value else {}
    except Exception:
        return {}


@app.template_global("enumerate")
def jinja_enumerate(iterable, start=0):
    return enumerate(iterable, start)
    

class WarningReason(db.Model):
    """أسباب الإنذار — قابلة للإضافة من المشرف"""
    __tablename__ = "warning_reason"
    id         = db.Column(db.Integer, primary_key=True)
    text       = db.Column(db.String(255), nullable=False, unique=True)
    is_default = db.Column(db.Boolean, default=False)   # الأسباب الافتراضية
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class WarningSignature(db.Model):
    """توقيع الموظف على نموذج الإنذار"""
    __tablename__ = "warning_signature"
    id           = db.Column(db.Integer, primary_key=True)
    request_id   = db.Column(db.Integer, db.ForeignKey("requests.id"), nullable=False, unique=True)
    employee_name= db.Column(db.String(255), nullable=True)
    job_title    = db.Column(db.String(255), nullable=True)  # المسمى الوظيفي — يُكتب يدوياً وقت التوقيع
    signed_at    = db.Column(db.DateTime, default=datetime.utcnow)
    signature    = db.Column(db.Text, nullable=False)  # base64 PNG

    request = db.relationship("Request", foreign_keys=[request_id],
                              backref=db.backref("warning_sig", uselist=False))


class WarningFieldReport(db.Model):
    """تقرير مخالفة ميداني يعبّئه المشرف (Supervisor) مباشرة بنفس شكل
    النموذج الورقي (اليوم/التاريخ/الموظف/المشرف/سبب الإنذار/توقيع المشرف).
    هذا سجل توثيقي داخلي فقط — لا يظهر بالـ PDF الرسمي للإنذار الذي يبقى
    بنفس تصميمه الحالي (توقيع الموظف + اعتماد HR)."""
    __tablename__ = "warning_field_report"
    id                        = db.Column(db.Integer, primary_key=True)
    request_id                = db.Column(db.Integer, db.ForeignKey("requests.id"), nullable=False, unique=True)
    day_name                  = db.Column(db.String(20), nullable=True)
    report_date               = db.Column(db.Date, nullable=True)
    employee_name_snapshot    = db.Column(db.String(255), nullable=True)
    employee_no_snapshot      = db.Column(db.String(50), nullable=True)
    supervisor_name_snapshot  = db.Column(db.String(255), nullable=True)
    supervisor_code_snapshot  = db.Column(db.String(50), nullable=True)
    reason_text                = db.Column(db.Text, nullable=True)
    signature                 = db.Column(db.Text, nullable=True)  # base64 PNG — توقيع المشرف وقت الإنشاء
    created_at                = db.Column(db.DateTime, default=datetime.utcnow)

    request = db.relationship("Request", foreign_keys=[request_id],
                              backref=db.backref("warning_field_report", uselist=False))

class LeaveSignature(db.Model):
    """توقيع الموظف على نموذج الإجازة"""
    __tablename__ = "leave_signature"
    id           = db.Column(db.Integer, primary_key=True)
    request_id   = db.Column(db.Integer, db.ForeignKey("requests.id"), nullable=False, unique=True)
    employee_name= db.Column(db.String(255), nullable=True)
    signed_at    = db.Column(db.DateTime, default=datetime.utcnow)
    signature    = db.Column(db.Text, nullable=False)  # base64 PNG
    request      = db.relationship("Request", foreign_keys=[request_id],
                                   backref=db.backref("leave_sig", uselist=False))

class PermissionSignature(db.Model):
    """توقيع الموظف على نموذج الاستئذان"""
    __tablename__ = "permission_signature"
    id           = db.Column(db.Integer, primary_key=True)
    request_id   = db.Column(db.Integer, db.ForeignKey("requests.id"), nullable=False, unique=True)
    employee_name= db.Column(db.String(255), nullable=True)
    signed_at    = db.Column(db.DateTime, default=datetime.utcnow)
    signature    = db.Column(db.Text, nullable=False)  # base64 PNG
    request      = db.relationship("Request", foreign_keys=[request_id],
                                   backref=db.backref("permission_sig", uselist=False))

# ===================== Models =====================
# ========== Model: Request ==========
class Request(db.Model):
    __tablename__ = "requests"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    supervisor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    supervisor = db.relationship("User", foreign_keys=[supervisor_id])

    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=True)
    employee = db.relationship("Employee", foreign_keys=[employee_id])

    # safety_supervisor requests: target is an officer User, not an Employee record
    officer_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    officer_user = db.relationship("User", foreign_keys=[officer_user_id])

    type = db.Column(db.String(32), nullable=False, default="leave")
    start_date = db.Column(db.Date, nullable=True)
    end_date   = db.Column(db.Date, nullable=True)
    reason = db.Column(db.Text, nullable=True)

    status = db.Column(db.String(16), nullable=False, default="pending")
    decided_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)
    admin_comment = db.Column(db.Text, nullable=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)

    __table_args__ = (
        db.Index("ix_requests_emp_created", "employee_id", "created_at"),
        db.Index("ix_requests_status", "status"),
    )

class HRTask(db.Model):
    __tablename__ = "hr_task"
    id          = db.Column(db.Integer, primary_key=True)
    request_id  = db.Column(db.Integer, db.ForeignKey("requests.id"), nullable=False, unique=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    type        = db.Column(db.String(32), nullable=False)  # مطابق ل requests.type
    status      = db.Column(db.String(16), nullable=False, default="pending")  # pending/applied/cancelled
    created_at  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    applied_at  = db.Column(db.DateTime, nullable=True)
    applied_by      = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    official_reason = db.Column(db.Text, nullable=True)
    warning_pdf     = db.Column(db.String(255), nullable=True)
    warning_company = db.Column(db.String(10), nullable=True, default="NSH")  # NSH أو GA — شركة خطاب الإنذار

    request  = db.relationship("Request",  foreign_keys=[request_id])
    employee = db.relationship("Employee", foreign_keys=[employee_id])
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)


# ===================== Models =====================

class DailyEvaluation(db.Model):
    __tablename__ = "daily_evaluation"
    id = db.Column(db.Integer, primary_key=True)

    # FK صحيحة: employee (مفرد) و user
    employee_id  = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    evaluator_id = db.Column(db.Integer, db.ForeignKey("user.id"),     nullable=False)

    eval_date = db.Column(db.Date, nullable=False)

    # Targets
    t1_text = db.Column(db.Text); t1_percent = db.Column(db.Float); t1_remarks = db.Column(db.Text)
    t2_text = db.Column(db.Text); t2_percent = db.Column(db.Float); t2_remarks = db.Column(db.Text)
    t3_text = db.Column(db.Text); t3_percent = db.Column(db.Float); t3_remarks = db.Column(db.Text)
    t4_text = db.Column(db.Text); t4_percent = db.Column(db.Float); t4_remarks = db.Column(db.Text)

    # Performance + comments
    p_punctuality = db.Column(db.Integer);    c_punctuality = db.Column(db.Text)
    p_quality = db.Column(db.Integer);        c_quality = db.Column(db.Text)
    p_productivity = db.Column(db.Integer);   c_productivity = db.Column(db.Text)
    p_communication = db.Column(db.Integer);  c_communication = db.Column(db.Text)
    p_problemsolving = db.Column(db.Integer); c_problemsolving = db.Column(db.Text)
    p_compliance = db.Column(db.Integer);     c_compliance = db.Column(db.Text)

    strengths = db.Column(db.Text)
    improvements = db.Column(db.Text)
    training_needed = db.Column(db.Text)

    targets_score = db.Column(db.Float)
    performance_score = db.Column(db.Float)
    total_score = db.Column(db.Float)
    overall_band = db.Column(db.String(30))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)

    __table_args__ = (
        db.UniqueConstraint("employee_id", "eval_date", name="uq_daily_emp_date"),
    )

    employee  = db.relationship("Employee", backref=db.backref("daily_evaluations", lazy="dynamic"))
    evaluator = db.relationship("User",     backref=db.backref("daily_evaluations_given", lazy="dynamic"))




# compute_daily_scores: النسخة الصحيحة (targets 40% + perf 60%) موجودة أسفل بعد Models


class Company(db.Model):
    __tablename__ = "company"
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(255), nullable=False)
    slug        = db.Column(db.String(100), unique=True, nullable=False)
    plan        = db.Column(db.Enum("free", "pro"), default="free")
    max_users   = db.Column(db.Integer, default=50)
    is_active   = db.Column(db.Boolean, default=False)  # pending until super admin approves
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    owner_email = db.Column(db.String(255), nullable=True)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    supervisor_code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(120), default="")
    role = db.Column(db.String(20), default="supervisor")  # supervisor | admin | site_supervisor | safety_officer | safety_supervisor | super_admin | safety_welfare | environment_officer
    is_active = db.Column(db.Boolean, default=True)
    is_hidden = db.Column(db.Boolean, default=False)
    # SaaS additions
    company_id    = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    email         = db.Column(db.String(255), nullable=True)
    password_hash = db.Column(db.String(255), nullable=True)
    company       = db.relationship("Company", foreign_keys=[company_id])

    ptw_training_active = db.Column(db.Boolean, default=False)
    lms_active          = db.Column(db.Boolean, default=False)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, pw)

    @property
    def is_supervisor(self):
        return self.role in ("supervisor", "site_supervisor", "safety_supervisor")

class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)   # nullable للموظف غير المعيّن
    emp_number = db.Column(db.String(50), nullable=False, unique=True)
    name = db.Column(db.String(120), nullable=False)
    department = db.Column(db.String(120), default="")
    site = db.Column(db.String(120), default="")
    is_active = db.Column(db.Boolean, default=True)
    status = db.Column(db.String(20), nullable=False, default="active")   # active | resigned | unassigned
    resigned_at = db.Column(db.Date, nullable=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    id_expiry_date = db.Column(db.Date, nullable=True)   # تاريخ انتهاء بطاقة الهوية/الإقامة
    gender = db.Column(db.Enum("male", "female"), nullable=True)   # backend-only, never shown in reports


class Evaluation(db.Model):
    __table_args__ = (UniqueConstraint("employee_id", "week_start", "week_end", name="uq_emp_week"),)
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
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)

class SiteSupervisorMap(db.Model):
    __table_args__ = (UniqueConstraint("site_sup_id", "supervisor_id", name="uq_site_sup_pair"),)
    id = db.Column(db.Integer, primary_key=True)
    site_sup_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    supervisor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)

class EmployeeAssignmentLog(db.Model):
    """سجل حركة الموظف بين المشرفين — لا يوجد حذف نهائي، كل حركة تُسجَّل هنا."""
    __tablename__ = "employee_assignment_log"
    id           = db.Column(db.Integer, primary_key=True)
    employee_id  = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False, index=True)
    from_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    to_user_id   = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    # transfer | unassign | resign | assign | reactivate
    action       = db.Column(db.String(20), nullable=False, default="transfer")
    actor_id     = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    note         = db.Column(db.String(255), default="")
    created_at   = db.Column(db.DateTime, default=lambda: datetime.now(RIYADH_TZ))
    company_id   = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)


class SafetySupervisorMap(db.Model):
    __table_args__ = (UniqueConstraint("safety_sup_id", "officer_id", name="uq_safety_sup_pair"),)
    id = db.Column(db.Integer, primary_key=True)
    safety_sup_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    officer_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id    = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)

class SupervisorEvaluation(db.Model):
    __table_args__ = (UniqueConstraint("supervisor_id", "week_start", "week_end", name="uq_sup_week"),)
    id = db.Column(db.Integer, primary_key=True)
    supervisor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
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
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)

class Attendance(db.Model):
    __table_args__ = (UniqueConstraint("employee_id", "date", name="uq_emp_date"),)
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    supervisor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False)  # present / absent / leave
    remarks = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship("Employee", backref="attendances")
    supervisor = db.relationship("User", backref="attendances")


# ===================== Helpers =====================
def _apply_leave_attendance_for_request(req: Request):
    """
    يعلّم حضور الموظّف كـ Leave لكل يوم بين start_date و end_date (شاملاً).
    يتعامل مع التكرار (يحدّث السجل إن وُجد أو ينشئه إن لم يوجد).
    نستخدم supervisor_id من الطلب لأنه مطلوب في Attendance.
    نضع وسم صغير في remarks لتمييز السجلات المرتبطة بهذا الطلب (للتراجع إن لزم).
    """
    if not (req and req.employee_id and req.start_date and req.end_date):
        return

    marker = f"auto_leave_req_{req.id}"
    cur = req.start_date
    while cur <= req.end_date:
        rec = Attendance.query.filter_by(employee_id=req.employee_id, date=cur).first()
        if rec:
            # غيّر الحالة إلى إجازة
            rec.status = "leave"
            # أضف الوسم إذا مو موجود
            if marker not in (rec.remarks or ""):
                rec.remarks = (rec.remarks + " " + marker).strip() if rec.remarks else marker
        else:
            # أنشئ سجل جديد (supervisor_id مطلوب)
            rec = Attendance(
                employee_id=req.employee_id,
                supervisor_id=req.supervisor_id,  # صاحب الطلب
                date=cur,
                status="leave",
                remarks=marker,
            )
            db.session.add(rec)
        cur += timedelta(days=1)

def _apply_sick_attendance_for_request(req: Request):
    """يسجّل غياب (absent) للسكليف تلقائياً لكل يوم بين start_date و end_date."""
    if not (req and req.employee_id and req.start_date and req.end_date):
        return
    marker = f"auto_sick_req_{req.id}"
    cur = req.start_date
    while cur <= req.end_date:
        rec = Attendance.query.filter_by(employee_id=req.employee_id, date=cur).first()
        if rec:
            rec.status  = "absent"
            if marker not in (rec.remarks or ""):
                rec.remarks = (rec.remarks + " " + marker).strip() if rec.remarks else marker
        else:
            db.session.add(Attendance(
                employee_id=req.employee_id,
                supervisor_id=req.supervisor_id,
                date=cur,
                status="absent",
                remarks=marker,
            ))
        cur += timedelta(days=1)

def get_employee_summary(emp_id: int):
    from calendar import monthrange
    today = datetime.now(RIYADH_TZ).date()

    # ── الحضور: الشهر الحالي ──
    month_start = today.replace(day=1)
    _, days_in_month = monthrange(today.year, today.month)
    q_att = Attendance.query.filter(
        Attendance.employee_id == emp_id,
        Attendance.date >= month_start,
        Attendance.date <= today
    ).all()
    present = sum(1 for a in q_att if (a.status or "").lower() == "present")
    absent  = sum(1 for a in q_att if (a.status or "").lower() == "absent")
    leave   = sum(1 for a in q_att if (a.status or "").lower() == "leave")
    recorded = present + absent + leave
    att_rate = round(present / days_in_month * 100, 1) if present > 0 else 0.0

    # ── التقييمات: آخر 8 أسابيع ──
    weeks_since = today - timedelta(days=7 * 8)
    evals = (Evaluation.query
             .filter(Evaluation.employee_id == emp_id,
                     Evaluation.week_start >= weeks_since)
             .order_by(Evaluation.week_start.desc())
             .all())
    avg_score = round(sum((e.total_score or 0) for e in evals) / len(evals), 1) if evals else None

    # ── التقييمات: كل الأسابيع (بلا حد زمني) ──
    evals_all = (Evaluation.query
                 .filter(Evaluation.employee_id == emp_id)
                 .order_by(Evaluation.week_start.asc())
                 .all())
    avg_score_all = round(sum((e.total_score or 0) for e in evals_all) / len(evals_all), 1) if evals_all else None

    # ── الاتجاه: متوسط النصف الثاني مقابل النصف الأول من كل التقييمات ──
    trend = None
    if len(evals_all) >= 2:
        mid = len(evals_all) // 2
        first_half  = evals_all[:mid]
        second_half = evals_all[mid:]
        avg_first  = sum((e.total_score or 0) for e in first_half) / len(first_half)
        avg_second = sum((e.total_score or 0) for e in second_half) / len(second_half)
        if avg_second > avg_first:
            trend = "up"
        elif avg_second < avg_first:
            trend = "down"
        else:
            trend = "flat"

    # ── الغياب: كل الفترة (بلا حد زمني) ──
    absent_all = Attendance.query.filter_by(employee_id=emp_id, status="absent").count()

    # ── آخر تقييم أسبوعي ──
    last_eval = evals[0] if evals else None
    last_eval_data = None
    if last_eval:
        last_eval_data = {
            "week_start":  str(last_eval.week_start),
            "week_end":    str(last_eval.week_end),
            "total_score": last_eval.total_score,
            "band":        last_eval.overall_band,
            "targets":     last_eval.targets_score,
            "perf":        last_eval.perf_score,
        }

    return {
        # حضور الشهر الحالي
        "month_year":    f"{today.year}-{today.month:02d}",
        "present":       present,
        "absent":        absent,
        "leave":         leave,
        "recorded":      recorded,
        "days_in_month": days_in_month,
        "att_rate":      att_rate,
        # تقييمات آخر 8 أسابيع
        "avg_score_8w":  avg_score,
        "eval_count_8w": len(evals),
        "last_eval":     last_eval_data,
        # تقييمات كل الفترة + الاتجاه + الغياب الكلي
        "avg_score_all":  avg_score_all,
        "eval_count_all": len(evals_all),
        "trend":          trend,
        "absent_all":     absent_all,
    }


def previous_week_range(ws: date) -> Tuple[date, date]:
    prev_ws = ws - timedelta(days=7)
    prev_we = prev_ws + timedelta(days=4)
    return prev_ws, prev_we


def compute_supervisor_scores(se: SupervisorEvaluation) -> None:
    items = {
        "leadership": getattr(se, "p_leadership", 0),
        "communication": se.p_communication,
        "scheduling": getattr(se, "p_scheduling", 0),
        "compliance": se.p_compliance,
        "team_support": getattr(se, "p_team_support", 0),
        "reporting": getattr(se, "p_reporting", 0),
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
    today = datetime.now(RIYADH_TZ).date()
    days_since_sun = (today.weekday() - WEEK_START_WEEKDAY) % 7
    start = today - timedelta(days=days_since_sun)
    end = start + timedelta(days=4)
    return start, end

def ensure_db_and_admin() -> None:
    """Create DB tables and bootstrap admin if ADMIN_ID is set."""
    with app.app_context():
        db.create_all()

        # db.create_all() ينشئ الجداول الجديدة فقط، ولا يضيف أعمدة جديدة لجداول
        # موجودة مسبقاً — لذلك أي عمود أُضيف لموديل قائم (مثل hr_task.warning_company)
        # يجب إضافته يدوياً هنا وإلا كل استعلام على الجدول ينهار بـ
        # "Unknown column ... in 'SELECT'"
        try:
            from sqlalchemy import text as _text, inspect as _inspect
            _insp = _inspect(db.engine)
            _existing_cols = {c["name"] for c in _insp.get_columns("hr_task")}
            if "warning_company" not in _existing_cols:
                with db.engine.begin() as _conn:
                    _conn.execute(_text(
                        "ALTER TABLE hr_task ADD COLUMN warning_company VARCHAR(10) NULL"))
                app.logger.info("DB migration: added hr_task.warning_company")

            _existing_wsig_cols = {c["name"] for c in _insp.get_columns("warning_signature")}
            if "job_title" not in _existing_wsig_cols:
                with db.engine.begin() as _conn:
                    _conn.execute(_text(
                        "ALTER TABLE warning_signature ADD COLUMN job_title VARCHAR(255) NULL"))
                app.logger.info("DB migration: added warning_signature.job_title")

            if _insp.has_table("env_checklist"):
                _existing_ec_cols = {c["name"] for c in _insp.get_columns("env_checklist")}
                if "signatory_name" not in _existing_ec_cols:
                    with db.engine.begin() as _conn:
                        _conn.execute(_text(
                            "ALTER TABLE env_checklist ADD COLUMN signatory_name VARCHAR(255) NULL"))
                    app.logger.info("DB migration: added env_checklist.signatory_name")
                if "signature_data" not in _existing_ec_cols:
                    with db.engine.begin() as _conn:
                        _conn.execute(_text(
                            "ALTER TABLE env_checklist ADD COLUMN signature_data LONGTEXT NULL"))
                    app.logger.info("DB migration: added env_checklist.signature_data")
                if "attendees_sigs" not in _existing_ec_cols:
                    with db.engine.begin() as _conn:
                        _conn.execute(_text(
                            "ALTER TABLE env_checklist ADD COLUMN attendees_sigs LONGTEXT NULL"))
                    app.logger.info("DB migration: added env_checklist.attendees_sigs")
                if "is_official" not in _existing_ec_cols:
                    with db.engine.begin() as _conn:
                        _conn.execute(_text(
                            "ALTER TABLE env_checklist ADD COLUMN is_official TINYINT(1) NOT NULL DEFAULT 0"))
                    app.logger.info("DB migration: added env_checklist.is_official")

            _existing_emp_cols = {c["name"] for c in _insp.get_columns("employee")}
            if "id_expiry_date" not in _existing_emp_cols:
                with db.engine.begin() as _conn:
                    _conn.execute(_text(
                        "ALTER TABLE employee ADD COLUMN id_expiry_date DATE NULL"))
                app.logger.info("DB migration: added employee.id_expiry_date")

            if "gender" not in _existing_emp_cols:
                with db.engine.begin() as _conn:
                    _conn.execute(_text(
                        "ALTER TABLE employee ADD COLUMN gender ENUM('male','female') NULL"))
                app.logger.info("DB migration: added employee.gender")

            _existing_user_cols = {c["name"] for c in _insp.get_columns("user")}
            if "ptw_training_active" not in _existing_user_cols:
                with db.engine.begin() as _conn:
                    _conn.execute(_text(
                        "ALTER TABLE user ADD COLUMN ptw_training_active BOOLEAN NOT NULL DEFAULT FALSE"))
                app.logger.info("DB migration: added user.ptw_training_active")

            if "lms_active" not in _existing_user_cols:
                with db.engine.begin() as _conn:
                    _conn.execute(_text(
                        "ALTER TABLE user ADD COLUMN lms_active BOOLEAN NOT NULL DEFAULT FALSE"))
                app.logger.info("DB migration: added user.lms_active")

        except Exception as _mig_err:
            app.logger.error("DB self-migration check failed: %s", _mig_err)

        admin_id = os.environ.get("ADMIN_ID")
        if admin_id:
            existing = User.query.filter_by(supervisor_code=admin_id).first()
            # Pick the single company in the DB (single-tenant setup)
            company = Company.query.first()
            company_id = company.id if company else None
            if not existing:
                admin = User(supervisor_code=admin_id, name="Admin", role="admin",
                             is_active=True, company_id=company_id)
                db.session.add(admin)
                db.session.commit()
            elif existing.company_id is None and company_id:
                # Fix existing bootstrap admin that has no company_id
                existing.company_id = company_id
                db.session.commit()
                
# parse_date و validate_week_sun_to_thu: النسخ الصحيحة موجودة أسفل في قسم Auth & Helpers
def _safe_date(s):
    """Parse date or return None if invalid/empty."""
    try:
        return parse_date(s) if s else None
    except Exception:
        return None

def cur_user():
    # Use g.user set by load_logged_in_user to avoid repeated DB queries per request.
    # If load_logged_in_user hasn't run yet (e.g., called outside request context), fall back to DB.
    if hasattr(g, '_user_loaded'):
        return g.user
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None

def login_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        if not cur_user():
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return inner

def admin_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        u = cur_user()
        if not u or u.role != "admin":
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return inner

def hr_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        u = cur_user()
        if not u or u.role != "hr":
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return inner

def super_admin_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        u = cur_user()
        if not u or u.role != "super_admin":
            abort(403)
        return f(*args, **kwargs)
    return inner

def cid():
    """Returns current company_id. None for super_admin (sees all)."""
    u = cur_user()
    if u and u.role == "super_admin":
        return None
    return session.get("company_id")

def apply_company_filter(query, model):
    """Adds company_id filter. Super_admin sees all; everyone else is scoped."""
    u = cur_user()
    if u and u.role == "super_admin":
        return query  # super_admin sees everything
    company_id = session.get("company_id")
    # Always filter — even if company_id is None (returns only unscoped rows,
    # which is safe; after migration all rows have company_id set)
    query = query.filter(model.company_id == company_id)
    return query

# يحدد مشرفًا من نص البحث (ID مطابق أو اسم مطابق بالكامل)
# يحدد مشرفًا من نص البحث (ID مطابق أو اسم مطابق بالكامل)
def find_supervisor_from_query(q: str):
    if not q:
        return None
    q = q.strip()
    # جرّب Supervisor ID (supervisor_code) أولًا
    sup = User.query.filter(
        User.role == "supervisor",
        User.supervisor_code == q
    ).first()
    if sup:
        return sup
    # جرّب اسم مطابق بالكامل (case-insensitive)
    return (User.query
            .filter(User.role == "supervisor",
                    func.lower(User.name) == q.lower())
            .first())


# ===================== Auth & Helpers =====================
# parse_date و validate_week_sun_to_thu: النسخ الصحيحة موجودة أسفل
def compute_scores(
    ev,
    target_percent_attrs=("t1_percent","t2_percent","t3_percent","t4_percent"),
    rating_attrs=("p_punctuality","p_quality","p_productivity",
                  "p_communication","p_problemsolving","p_compliance"),
):
    def clamp(x, lo, hi):
        try:
            return max(lo, min(hi, x))
        except Exception:
            return lo

    # ===== Targets (40) =====
    target_values = []
    for attr in target_percent_attrs:
        v = getattr(ev, attr, None)
        try:
            v = float(v) if v not in (None, "") else 0.0
        except Exception:
            v = 0.0
        target_values.append(clamp(v, 0.0, 100.0))

    n_targets = len(target_values) or 1
    per_target_points = 40.0 / n_targets
    ev.targets_score = round(sum((v / 100.0) * per_target_points for v in target_values), 2)

    # ===== Performance (60) — الآن الافتراضي 0 =====
    rating_values = []
    for attr in rating_attrs:
        raw = getattr(ev, attr, None)

        # لو فاضية أو None → 0
        if raw in (None, "", 0):
            r = 0
        else:
            try:
                r = int(raw)
            except Exception:
                r = 0

        # خليه يقبل 0 إلى 5
        rating_values.append(clamp(r, 0, 5))

    n_ratings = len(rating_values) or 1
    per_item_points = 60.0 / n_ratings
    ev.perf_score = round(sum((r / 5.0) * per_item_points for r in rating_values), 2)

    # ===== Total & Band =====
    ev.total_score = round(ev.targets_score + ev.perf_score, 2)
    ev.overall_band = ("Excellent" if ev.total_score >= 90 else
                       "Good" if ev.total_score >= 80 else
                       "Satisfactory" if ev.total_score >= 70 else
                       "Needs Improvement")

# (اختياري) لو عندك استدعاءات قديمة:
# compute_weekly_scores = compute_scores
                       
# ==== Auth & helpers glue (ضعها بعد الـ Helpers) ====


# 5) parse_date بسيطة متسامحة مع YYYY-MM-DD
def parse_date(s: str) -> date:
    s = (s or "").strip()
    try:
        return date.fromisoformat(s)          # 2025-10-16
    except Exception:
        # fallback: dd/mm/yyyy أو dd-mm-yyyy
        for sep in ("/", "-"):
            parts = s.split(sep)
            if len(parts) == 3 and len(parts[0]) <= 2:
                d, m, y = map(int, parts)
                return date(y, m, d)
        raise

# 6) تحقق أن الأسبوع أحد→خميس
def validate_week_sun_to_thu(ws: date, we: date):
    if not ws or not we:
        return False, "Week start/end are required."
    if we != ws + timedelta(days=4):
        return False, "Week must be 5 days (Sun→Thu)."
    if ws.weekday() != WEEK_START_WEEKDAY or we.weekday() != WEEK_END_WEEKDAY:
        return False, "Start must be Sunday and end must be Thursday."
    return True, ""


@app.get("/logo")
def logo():
    return app.send_static_file("img/logo.png")
    

# ===================== Routes =====================
@app.route("/requests/new", methods=["GET", "POST"])
@login_required
def request_new():
    u = cur_user()
    if not (u and getattr(u, "role", None) in ("supervisor", "site_supervisor",
                                               "safety_supervisor", "safety_manager", "admin")):
        abort(403)

    if u.role in ("safety_supervisor", "safety_manager", "admin", "super_admin"):
        officer_ids = [o.id for o in _get_safety_officers(u)]
        employees = (Employee.query
                     .filter(Employee.user_id.in_(officer_ids), Employee.is_active == True)
                     .order_by(Employee.name.asc()).all())
        valid_sup_ids = set(officer_ids)
    else:
        employees = (Employee.query
                     .filter_by(user_id=u.id, is_active=True)
                     .order_by(Employee.name.asc()).all())
        valid_sup_ids = {u.id}

    def _parse_date(s):
        s = (s or "").strip()
        if not s: return None
        from datetime import datetime
        try: return datetime.strptime(s, "%Y-%m-%d").date()
        except: return None

    if request.method == "POST":
        # التقاط
        emp_id     = request.form.get("employee_id")
        rtype      = (request.form.get("type") or "").strip().lower()
        reason     = (request.form.get("reason") or "").strip()
        start_date = _parse_date(request.form.get("start_date"))
        end_date   = _parse_date(request.form.get("end_date"))
        from_time  = (request.form.get("from_time") or "").strip()
        to_time    = (request.form.get("to_time") or "").strip()
        priority   = (request.form.get("priority") or "").strip().lower()

        # ── إنذار من حساب مشرف: تحويل مباشر لصفحة "تقرير سبب الإنذار" المخصصة
        # بدل إنشاء طلب عام بخانة سبب بسيطة. التحويل بالكامل من السيرفر
        # (بدون أي جافاسكربت/قوالب إضافية) لتفادي أي تلف بالملف عند الرفع.
        if rtype == "warning" and u.role == "supervisor":
            return redirect(url_for("warning_report_new", employee_id=emp_id or ""))

        # تحقق أساسي
        try: emp_id = int(emp_id or 0)
        except: emp_id = 0
        emp = db.session.get(Employee, emp_id) if emp_id else None
        if not emp or emp.user_id not in valid_sup_ids:
            flash("Please choose a valid employee.", "danger")
            return render_template("requests_new.html", employees=employees,
                                   pre_emp_id=emp_id, pre_type=rtype,
                                   pre_start=request.form.get("start_date"),
                                   pre_end=request.form.get("end_date"),
                                   pre_from_time=from_time, pre_to_time=to_time,
                                   pre_priority=priority, pre_reason=reason)

        # قواعد خفيفة لكل نوع (بدون ترحيل DB)
        if rtype in {"leave","shift","overtime","absence","late"}:
            if not start_date:
                flash("Start date is required for the selected type.", "danger")
                return render_template("requests_new.html", employees=employees,
                                       pre_emp_id=emp_id, pre_type=rtype,
                                       pre_start=request.form.get("start_date"),
                                       pre_end=request.form.get("end_date"),
                                       pre_priority=priority, pre_reason=reason)
        if rtype == "permission":
            # ننسّق الوقت داخل الـ reason
            tag = f"[Permission {from_time or '?'}→{to_time or '?'}]"
            reason = f"{tag} {reason}".strip()
            # تواريخ ليست مطلوبة هنا
            start_date = start_date or None
            end_date   = end_date or None
        if rtype == "warning":
            # نضيف أولوية لو موجودة
            if priority in {"low","normal","high"}:
                reason = f"[Warning {priority}] {reason}".strip()

        # الإنشاء
        r = Request(
            supervisor_id=u.id,
            employee_id=emp.id,
            type=rtype if rtype in {"leave","sick","permission","warning","late","other"} else "other",
            start_date=start_date,
            end_date=end_date,
            reason=reason,
            status="pending",
            company_id=cid(),
        )
        db.session.add(r)
        db.session.flush()  # نحتاج r.id قبل حفظ المرفق

        # ─── رفع مرفق (سكليف) إن وُجد ───
        uploaded_file = request.files.get("pdf_file")
        if uploaded_file and uploaded_file.filename and allowed_file(uploaded_file.filename):
            original_name = secure_filename(uploaded_file.filename)
            ext           = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "pdf"
            saved_name    = f"req_{r.id}_{uuid.uuid4().hex[:8]}.{ext}"
            uploaded_file.save(os.path.join(UPLOAD_FOLDER, saved_name))
            db.session.add(RequestAttachment(
                request_id=r.id,
                filename=saved_name,
                original_name=original_name,
            ))

        db.session.commit()
        flash("Request submitted.", "success")
        return redirect(url_for("requests_mine"))

    # GET
    return render_template("requests_new.html", employees=employees,
                           pre_emp_id=None, pre_type="leave")


_AR_DAY_NAMES = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]


@app.route("/requests/new-warning-report", methods=["GET", "POST"])
@login_required
def warning_report_new():
    """تقرير مخالفة/سبب إنذار يعبّئه المشرف مباشرة — بنفس شكل النموذج الورقي.
    يُنشئ طلب إنذار عادي (نفس مسار HR الحالي) + يحفظ نسخة توثيقية بالتقرير
    الميداني (تاريخ/يوم/توقيع المشرف وقت التعبئة) دون التأثير على تصميم
    الـ PDF الرسمي الحالي."""
    u = cur_user()
    if u.role != "supervisor":
        abort(403)

    employees = (Employee.query.filter_by(user_id=u.id, is_active=True)
                 .order_by(Employee.name.asc()).all())

    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Asia/Riyadh")).date()

    if request.method == "POST":
        emp_id = request.form.get("employee_id")
        try:
            emp_id = int(emp_id or 0)
        except Exception:
            emp_id = 0
        emp = db.session.get(Employee, emp_id) if emp_id else None
        if not emp or emp.user_id != u.id:
            flash("Please select a valid employee from your list.", "danger")
            return redirect(url_for("warning_report_new"))

        emp_name_manual = (request.form.get("employee_name_manual") or "").strip()
        reason_text = (request.form.get("reason_text") or "").strip()
        signature   = (request.form.get("signature") or "").strip()
        report_date_str = (request.form.get("report_date") or "").strip()

        if not emp_name_manual:
            flash("Employee name is required.", "danger")
            return redirect(url_for("warning_report_new"))
        if not reason_text:
            flash("Warning reason is required.", "danger")
            return redirect(url_for("warning_report_new"))
        if not signature:
            flash("Supervisor signature is required to complete the report.", "danger")
            return redirect(url_for("warning_report_new"))

        try:
            report_date = datetime.strptime(report_date_str, "%Y-%m-%d").date() if report_date_str else today
        except Exception:
            report_date = today
        day_name = _AR_DAY_NAMES[report_date.weekday()]

        r = Request(
            supervisor_id=u.id,
            employee_id=emp.id,
            type="warning",
            reason=reason_text,
            status="pending",
            company_id=cid(),
        )
        db.session.add(r)
        db.session.flush()  # نحتاج r.id

        wfr = WarningFieldReport(
            request_id=r.id,
            day_name=day_name,
            report_date=report_date,
            employee_name_snapshot=emp_name_manual,   # يكتبه المشرف يدوياً
            employee_no_snapshot=emp.emp_number,      # يُختار بالـ ID من قائمة موظفيه
            supervisor_name_snapshot=u.name,
            supervisor_code_snapshot=u.supervisor_code,
            reason_text=reason_text,
            signature=signature,
            created_at=datetime.now(ZoneInfo("Asia/Riyadh")),
        )
        db.session.add(wfr)
        db.session.commit()

        flash("Warning report submitted successfully.", "success")
        return redirect(url_for("requests_mine"))

    pre_emp_id = request.args.get("employee_id", "")
    return render_template("warning_report_new.html", employees=employees,
                           sup=u, today=today.isoformat(),
                           today_day_name=_AR_DAY_NAMES[today.weekday()],
                           pre_emp_id=pre_emp_id)


@app.route("/requests/mine")
@login_required
def requests_mine():
    u = cur_user()
    reqs = Request.query.filter(Request.supervisor_id == u.id) \
                        .order_by(Request.created_at.desc()).all()
    # نجلب HRTask لكل طلب لمعرفة official_reason وحالة التطبيق
    req_ids = [r.id for r in reqs]
    tasks_map = {}
    if req_ids:
        for t in HRTask.query.filter(HRTask.request_id.in_(req_ids)).all():
            tasks_map[t.request_id] = t
    return render_template("requests_mine.html", rows=reqs, tasks_map=tasks_map)


@app.route("/admin/employee/<int:emp_id>/summary", methods=["GET"])
@login_required
@admin_required
def employee_summary(emp_id):
    emp = Employee.query.get_or_404(emp_id)
    summary = get_employee_summary(emp_id)
    return render_template("employee_summary.html", emp=emp, summary=summary)


# ---------- Admin: Requests ----------


@app.route("/attendance", methods=["GET", "POST"])
@admin_required
def attendance_admin():
    """
    صفحة الأدمن الرئيسية للحضور:
      - mode=day  : تاريخ واحد (d)
      - mode=week : أسبوع (ws..we) أحد->خميس
    """
    mode = request.args.get("mode") or "day"

    if request.method == "POST":
        mode = request.form.get("mode") or "day"
        if mode == "day":
            d = request.form.get("day_date") or ""
            return redirect(url_for("attendance_admin", mode="day", d=d))
        else:
            ws = request.form.get("week_start") or ""
            we = request.form.get("week_end") or ""
            # لو وضع أسبوع فقط تاريخ بداية، احسب النهاية = +4 أيام
            if ws and not we:
                try:
                    ws_dt = parse_date(ws)
                    we = (ws_dt + timedelta(days=4)).isoformat()
                except:
                    pass
            return redirect(url_for("attendance_admin", mode="week", ws=ws, we=we))

    # قراءة المعطيات من الquerystring
    if mode == "week":
        ws = _safe_date(request.args.get("ws")) or default_week_today()[0]
        we = _safe_date(request.args.get("we")) or (ws + timedelta(days=4))
        # تأكد أحد→خميس
        ok, msg = validate_week_sun_to_thu(ws, we)
        if not ok:
            flash(msg, "danger")
            ws, we = default_week_today()
    else:
        d = _safe_date(request.args.get("d")) or date.today()

    # الموظفون
    _emp_q = (db.session.query(Employee, User)
              .join(User, Employee.user_id == User.id)
              .filter(Employee.is_active == True))
    _cid = cid()
    if _cid is not None:
        _emp_q = _emp_q.filter(Employee.company_id == _cid)
    employees = _emp_q.order_by(User.supervisor_code.asc(), Employee.name.asc()).all()

    rows = []
    kpi = {"total_days": 0, "total_present": 0}

    if mode == "day":
        emp_ids = [emp.id for emp, _ in employees]
        atts = []
        if emp_ids:
            atts = (Attendance.query
                    .filter(Attendance.employee_id.in_(emp_ids),
                            Attendance.date == d)
                    .all())
        att_by_emp = {a.employee_id: a for a in atts}
        for emp, sup in employees:
            a = att_by_emp.get(emp.id)
            rows.append({
                "emp": emp,
                "sup": sup,
                "status": (a.status if a else None),
                "remarks": (a.remarks if a else ""),
                "marked_by": (db.session.get(User, a.supervisor_id) if a else None)
            })
        # KPI يومي بسيط
        kpi["total_days"] = len([r for r in rows if r["status"]])
        kpi["total_present"] = len([r for r in rows if r["status"] == "present"])

        return render_template("attendance_admin.html",
                               mode="day", day_date=d, rows=rows, kpi=kpi)

    else:  # mode == "week"
        # اجلب كل الحضور ضمن المدى
        emp_ids = [emp.id for emp, _ in employees]
        atts = []
        if emp_ids:
            atts = (Attendance.query
                    .filter(Attendance.employee_id.in_(emp_ids),
                            Attendance.date >= ws,
                            Attendance.date <= we)
                    .all())
        # احصائيات لكل موظف
        stats = defaultdict(lambda: {"present": 0, "absent": 0, "leave": 0, "total": 0})
        for a in atts:
            stats[a.employee_id]["total"] += 1
            if a.status in ("present", "absent", "leave"):
                stats[a.employee_id][a.status] += 1
        for emp, sup in employees:
            s = stats[emp.id]
            pct = (s["present"] / s["total"] * 100.0) if s["total"] else 0.0
            kpi["total_days"] += s["total"]
            kpi["total_present"] += s["present"]
            rows.append({
                "emp": emp,
                "sup": sup,
                "present": s["present"],
                "absent": s["absent"],
                "leave": s["leave"],
                "total": s["total"],
                "percent": round(pct, 1)
            })

        return render_template("attendance_admin.html",
                               mode="week", ws=ws, we=we, rows=rows, kpi=kpi)


from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
@app.route("/employees/add", methods=["POST"], endpoint="employees_add", strict_slashes=False)
@login_required
def employees_add():
    u = cur_user()
    
    # فقط السوبرفايزر
    if not u or getattr(u, "role", "") != "supervisor":
        abort(403)

    emp_number = (request.form.get("emp_number") or "").strip()
    name       = (request.form.get("name") or "").strip()
    department = (request.form.get("department") or "").strip()
    site       = (request.form.get("site") or "").strip()

    if not emp_number or not name:
        flash("Employee number and name are required.", "danger")
        return redirect(url_for("employees"))

    # رقم الموظف أرقام فقط
    if not emp_number.isdigit():
        flash("Employee number must contain digits only (0-9).", "danger")
        return redirect(url_for("employees"))

    # بحث عن الموظف (نشط أو غير نشط)
    emp = Employee.query.filter_by(emp_number=emp_number).first()

    if emp:
        # إعادة تفعيل إذا كان ملغي
        if not emp.is_active:
            emp.is_active = True

        # نقل الملكية إلى السوبرفايزر الحالي
        if emp.user_id != u.id:
            emp.user_id = u.id
            db.session.commit()
            flash(f"Employee {emp.name} ({emp.emp_number}) reactivated and assigned to you.", "success")
        else:
            flash(f"Employee {emp.name} ({emp.emp_number}) is already under your account.", "info")

        return redirect(url_for("employees"))

    # إنشاء موظف جديد
    try:
        emp = Employee(
            emp_number=emp_number,
            name=name,
            department=department,
            site=site,
            user_id=u.id,
            is_active=True,
            company_id=cid(),
        )
        db.session.add(emp)
        db.session.commit()
        flash(f"Employee {emp.name} ({emp.emp_number}) added successfully.", "success")

    except IntegrityError:
        db.session.rollback()
        flash("Duplicate employee number. Try again.", "danger")

    return redirect(url_for("employees"))

@app.route("/employees/<int:emp_id>/deactivate", methods=["POST"], endpoint="employees_deactivate", strict_slashes=False)
@login_required
def employees_deactivate(emp_id):
    u = cur_user()

    # فقط السوبرفايزر
    if not u or getattr(u, "role", "") != "supervisor":
        abort(403)

    emp = Employee.query.get_or_404(emp_id)

    # تأكد أنه تحت حساب السوبرفايزر نفسه
    if emp.user_id != u.id:
        abort(403)

    emp.is_active = False

    try:
        db.session.commit()
        flash(f"Employee {emp.name} ({emp.emp_number}) deactivated.", "success")
    except:
        db.session.rollback()
        flash("Error occurred while deactivating employee.", "danger")

    return redirect(url_for("employees"))



@app.route("/admin/requests", methods=["GET", "POST"])
@login_required
@admin_required
def requests_inbox():
    status = request.args.get("status", "pending")
    qterm  = (request.args.get("q") or "").strip()

    q = apply_company_filter(Request.query, Request)

    # فلتر الحالة (لو مو "all")
    if status and status != "all":
        q = q.filter(Request.status == status)

    # فلتر البحث بالاسم / رقم الموظف
    if qterm:
        q = (q.join(Employee, Request.employee_id == Employee.id)
               .filter(or_(
                   Employee.emp_number.like(f"%{qterm}%"),
                   Employee.name.ilike(f"%{qterm}%"),
               )))

    rows = q.order_by(Request.created_at.desc()).all()

    # مرفقات السكليف (لو وُجدت) لكل طلب
    req_ids = [r.id for r in rows]
    attachment_url_map = {}
    if req_ids:
        atts = RequestAttachment.query.filter(RequestAttachment.request_id.in_(req_ids)).all()
        for att in atts:
            attachment_url_map[att.request_id] = url_for("admin_request_attachment", req_id=att.request_id)

    # 1) رابط "New" لو الراوت موجود
    request_new_url = None
    if "request_new" in current_app.view_functions:
        request_new_url = url_for("request_new")

    # 2) خرائط روابط ملخص الموظف حسب الراوتات المتاحة في مشروعك
    # جرّب بالترتيب: employee_summary → admin_employee_summary → admin_employee_history
    employee_summary_url_map = {}
    endpoint_candidates = ["employee_summary", "admin_employee_summary", "admin_employee_history"]
    target_endpoint = next((ep for ep in endpoint_candidates if ep in current_app.view_functions), None)

    if target_endpoint:
        for r in rows:
            if r.employee_id:
                try:
                    employee_summary_url_map[r.employee_id] = url_for(target_endpoint, emp_id=r.employee_id)
                except Exception:
                    # لو الراوت يطلب باراميتر باسم مختلف، نتركه بدون رابط
                    pass

    return render_template(
        "requests_inbox.html",
        rows=rows,
        status=status,
        q=qterm,
        request_new_url=request_new_url,
        employee_summary_url_map=employee_summary_url_map,
        attachment_url_map=attachment_url_map,
    )


@app.route("/admin/requests/<int:req_id>/attachment")
@login_required
@admin_required
def admin_request_attachment(req_id):
    from flask import send_from_directory
    req = Request.query.get_or_404(req_id)
    att = RequestAttachment.query.filter_by(request_id=req.id).order_by(RequestAttachment.uploaded_at.desc()).first()
    if not att:
        abort(404)
    return send_from_directory(UPLOAD_FOLDER, att.filename, as_attachment=False,
                               download_name=att.original_name or att.filename)


@app.route("/admin/requests/<int:req_id>/decide", methods=["POST"])
@login_required
@admin_required
def request_decide(req_id):
    req = Request.query.get_or_404(req_id)
    decision = request.form.get("decision")  # "approve" أو "reject"
    comment  = (request.form.get("admin_comment") or "").strip()

    if decision not in ("approve", "reject"):
        flash("Invalid decision.", "danger")
        return redirect(url_for("requests_inbox"))

    # حدّث بيانات الطلب
    req.status = "approved" if decision == "approve" else "rejected"
    req.decided_by = cur_user().id
    req.decided_at = datetime.now(timezone.utc)
    req.admin_comment = comment

    if decision == "approve":
        # إجازة → تسجيل حضور + توليد PDF
        if req.type == "leave":
            _apply_leave_attendance_for_request(req)
            existing_form = LeaveForm.query.filter_by(request_id=req.id).first()
            if not existing_form:
                try:
                    pdf_name = generate_leave_pdf(req)
                    if pdf_name:
                        db.session.add(LeaveForm(request_id=req.id, filename=pdf_name))
                except Exception as e:
                    app.logger.error("leave PDF error: %s", e)

        # سكليف → تسجيل غياب تلقائي
        if req.type == "sick":
            _apply_sick_attendance_for_request(req)

        # إنشاء مهمة HR لجميع الأنواع
        existing = HRTask.query.filter_by(request_id=req.id).first()
        if not existing:
            try:
                db.session.add(HRTask(
                    request_id=req.id,
                    employee_id=req.employee_id,
                    type=req.type or "leave",
                    status="pending",
                    company_id=req.company_id,
                ))
            except IntegrityError:
                db.session.rollback()

    db.session.commit()
    flash("Request updated.", "success")
    return redirect(url_for("requests_inbox"))


@app.route("/requests/<int:req_id>/leave-sign", methods=["GET", "POST"])
@login_required
def web_leave_sign(req_id):
    req = Request.query.get_or_404(req_id)
    lf  = LeaveForm.query.filter_by(request_id=req_id).first()
    sig = LeaveSignature.query.filter_by(request_id=req_id).first()

    # ── ترميم ذاتي: إذا فشل توليد النموذج وقت الموافقة (مثلاً بسبب خطأ سابق
    #    في مكتبة PDF)، نحاول توليده الآن بدل تعليق الشاشة على "جاري الإعداد" للأبد
    if not lf and req.type == "leave":
        try:
            pdf_name = generate_leave_pdf(req, sig)
            if pdf_name:
                lf = LeaveForm(request_id=req_id, filename=pdf_name)
                db.session.add(lf)
                db.session.commit()
        except Exception as e:
            app.logger.error("leave-sign auto-generate PDF: %s", e)
            db.session.rollback()

    if request.method == "POST":
        signature  = (request.form.get("signature") or "").strip()
        emp_name   = (request.form.get("employee_name") or (req.employee.name if req.employee else "")).strip()
        if not signature:
            flash("Signature is required.", "danger")
            return redirect(url_for("web_leave_sign", req_id=req_id))

        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Asia/Riyadh"))
        if sig:
            sig.signature = signature; sig.employee_name = emp_name; sig.signed_at = now
        else:
            sig = LeaveSignature(request_id=req_id, signature=signature,
                                 employee_name=emp_name, signed_at=now)
            db.session.add(sig)
        db.session.flush()

        # إذا ما زال النموذج غير موجود (فشل التوليد أعلاه)، ننشئه الآن بعد التوقيع
        if not lf:
            try:
                pdf_name = generate_leave_pdf(req, sig)
                if pdf_name:
                    lf = LeaveForm(request_id=req_id, filename=pdf_name)
                    db.session.add(lf)
            except Exception as e:
                app.logger.error("leave sign PDF: %s", e)

        if lf:
            lf.signed = True; lf.signed_at = now
            try:
                pdf_name = generate_leave_pdf(req, sig)
                if pdf_name:
                    lf.filename = pdf_name
            except Exception as e:
                app.logger.error("leave sign PDF: %s", e)

        db.session.commit()
        if lf and lf.signed:
            flash("Signed successfully.", "success")
        else:
            flash("Signature saved, but the PDF could not be generated — please contact support.", "warning")
        return redirect(url_for("web_leave_sign", req_id=req_id))

    return render_template("leave_sign.html", req=req, lf=lf, sig=sig)


@app.route("/requests/<int:req_id>/permission-sign", methods=["GET", "POST"])
@login_required
def web_permission_sign(req_id):
    req  = Request.query.get_or_404(req_id)
    sig  = PermissionSignature.query.filter_by(request_id=req_id).first()
    task = HRTask.query.filter_by(request_id=req_id).first()

    if request.method == "POST":
        signature = (request.form.get("signature") or "").strip()
        emp_name  = (request.form.get("employee_name") or (req.employee.name if req.employee else "")).strip()
        if not signature:
            flash("Signature is required.", "danger")
            return redirect(url_for("web_permission_sign", req_id=req_id))

        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Asia/Riyadh"))
        if sig:
            sig.signature = signature; sig.employee_name = emp_name; sig.signed_at = now
        else:
            sig = PermissionSignature(request_id=req_id, signature=signature,
                                      employee_name=emp_name, signed_at=now)
            db.session.add(sig)
        db.session.flush()

        try:
            pdf_name = generate_permission_pdf(req, sig)
            if pdf_name and task:
                task.warning_pdf = pdf_name
        except Exception as e:
            app.logger.error("permission sign PDF: %s", e)
        db.session.commit()
        flash("Signed successfully.", "success")
        return redirect(url_for("web_permission_sign", req_id=req_id))

    return render_template("permission_sign.html", req=req, sig=sig, task=task)


@app.get("/requests/<int:req_id>/leave-pdf")
@login_required
def web_leave_pdf(req_id):
    lf = LeaveForm.query.filter_by(request_id=req_id).first_or_404()
    return send_file(os.path.join(UPLOAD_FOLDER, lf.filename),
                     mimetype="application/pdf", as_attachment=False,
                     download_name=f"leave_form_{req_id}.pdf")


@app.route("/requests/<int:req_id>/warning-sign", methods=["GET", "POST"])
@login_required
def web_warning_sign(req_id):
    req  = Request.query.get_or_404(req_id)
    task = HRTask.query.filter_by(request_id=req_id).first()
    sig  = WarningSignature.query.filter_by(request_id=req_id).first()

    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Asia/Riyadh")).date()
    greg_str = f"{today.day:02d} / {today.month:02d} / {today.year}"
    reason_for_text = (task.official_reason if task and task.official_reason else (req.reason or "—"))
    emp_no_for_text = req.employee.emp_number if req.employee else "—"

    # ── ترميم ذاتي: إذا الموظف موقّع فعلاً لكن ملف الـ PDF ما انولّد
    #    (بسبب خطأ سابق)، نحاول نولّده الآن بدل ما تبقى الشاشة بدون رابط تحميل للأبد
    if sig and task and not task.warning_pdf:
        try:
            pdf_name = generate_warning_pdf(req, sig)
            if pdf_name:
                task.warning_pdf = pdf_name
                db.session.commit()
        except Exception as e:
            app.logger.error("warning-sign auto-generate PDF: %s", e)
            db.session.rollback()

    if request.method == "POST":
        signature = (request.form.get("signature") or "").strip()
        emp_name  = (request.form.get("employee_name") or (req.employee.name if req.employee else "")).strip()
        job_title = (request.form.get("job_title") or (req.employee.department if req.employee else "")).strip()
        if not signature:
            flash("Signature is required.", "danger")
            return redirect(url_for("web_warning_sign", req_id=req_id))

        now = datetime.now(ZoneInfo("Asia/Riyadh"))
        if sig:
            sig.signature = signature; sig.employee_name = emp_name; sig.job_title = job_title; sig.signed_at = now
        else:
            sig = WarningSignature(request_id=req_id, signature=signature,
                                   employee_name=emp_name, job_title=job_title, signed_at=now)
            db.session.add(sig)
        db.session.flush()
        # توليد PDF الإنذار
        pdf_ok = False
        try:
            pdf_name = generate_warning_pdf(req, sig)
            if pdf_name and task:
                task.warning_pdf = pdf_name
                pdf_ok = True
        except Exception as e:
            app.logger.error("warning sign PDF: %s", e)
        db.session.commit()
        if pdf_ok:
            flash("Signed successfully.", "success")
        else:
            flash("Signature saved, but the PDF could not be generated — refresh the page, and contact support if it persists.", "warning")
        return redirect(url_for("web_warning_sign", req_id=req_id))

    return render_template("warning_sign.html", req=req, task=task, sig=sig,
                           greg_str=greg_str, reason_for_text=reason_for_text,
                           emp_no_for_text=emp_no_for_text)


@app.get("/requests/<int:req_id>/warning-pdf")
@login_required
def web_warning_pdf(req_id):
    task = HRTask.query.filter_by(request_id=req_id).first_or_404()
    if not task.warning_pdf:
        abort(404)
    return send_file(os.path.join(UPLOAD_FOLDER, task.warning_pdf),
                     mimetype="application/pdf", as_attachment=False,
                     download_name=f"warning_{req_id}.pdf")


@app.get("/requests/<int:req_id>/warning-report")
@login_required
def web_warning_report_view(req_id):
    """عرض تقرير سبب الإنذار الأصلي اللي عبّاه المشرف (صفحة منفصلة عن
    صفحة الإنذار الرسمي/PDF الخاصة بـ HR)"""
    req = Request.query.get_or_404(req_id)
    wfr = WarningFieldReport.query.filter_by(request_id=req_id).first_or_404()

    u = cur_user()
    is_owner = (req.supervisor_id == u.id)
    is_staff = (u.role in ("admin", "hr", "site_supervisor"))
    if not (is_owner or is_staff):
        abort(403)

    return render_template("warning_report_view.html", req=req, wfr=wfr)


@app.get("/requests/attachment/<int:att_id>")
@login_required
def request_attachment_download(att_id):
    att = RequestAttachment.query.get_or_404(att_id)
    ext = att.filename.rsplit(".", 1)[-1].lower() if "." in att.filename else "pdf"
    mime = "application/pdf" if ext == "pdf" else f"image/{ext}"
    return send_file(os.path.join(UPLOAD_FOLDER, att.filename),
                     mimetype=mime, as_attachment=False,
                     download_name=att.original_name or att.filename)


@app.route("/attendance/report", methods=["GET"])
@admin_required
def attendance_report():
    # نطاق التاريخ (اختياري عبر ?from=YYYY-MM-DD&to=YYYY-MM-DD)
    frm = request.args.get("from"); to = request.args.get("to")
    try:
        d_from = parse_date(frm) if frm else date.today().replace(day=1)
    except: d_from = date.today().replace(day=1)
    try:
        d_to = parse_date(to) if to else date.today()
    except: d_to = date.today()

    employees = (db.session.query(Employee, User)
                 .join(User, Employee.user_id == User.id)
                 .filter(Employee.is_active == True)
                 .order_by(User.supervisor_code.asc(), Employee.name.asc())
                 .all())

    emp_ids = [emp.id for emp, sup in employees]
    counts_by_emp = defaultdict(Counter)
    if emp_ids:
        att_rows = (db.session.query(Attendance.employee_id, Attendance.status,
                                      func.count(Attendance.id))
                    .filter(Attendance.employee_id.in_(emp_ids),
                            Attendance.date >= d_from,
                            Attendance.date <= d_to)
                    .group_by(Attendance.employee_id, Attendance.status)
                    .all())
        for emp_id, status, cnt in att_rows:
            counts_by_emp[emp_id][status] = cnt

    rows = []
    for emp, sup in employees:
        c = counts_by_emp.get(emp.id, Counter())
        present = c.get("present", 0)
        leave   = c.get("leave", 0)
        absent  = c.get("absent", 0)
        total   = present + leave + absent
        pct = (present / total * 100.0) if total else 0.0
        rows.append({
            "emp": emp, "sup": sup,
            "total": total, "present": present,
            "leave": leave, "absent": absent,
            "percent": round(pct,1)
        })

    # KPI إجمالي
    total_days = sum(r["total"] for r in rows)
    total_present = sum(r["present"] for r in rows)
    kpi_attendance = round((total_present/total_days*100.0),1) if total_days else 0.0

    return render_template("attendance_report.html",
                           rows=rows, d_from=d_from, d_to=d_to,
                           kpi_attendance=kpi_attendance)

@app.route("/attendance/print", methods=["GET"])
@admin_required
def attendance_print():
    mode = request.args.get("mode") or "day"

    if mode == "week":
        ws = _safe_date(request.args.get("ws")) or default_week_today()[0]
        we = _safe_date(request.args.get("we")) or (ws + timedelta(days=4))
        ok, msg = validate_week_sun_to_thu(ws, we)
        if not ok:
            flash(msg, "danger")
            ws, we = default_week_today()
    else:
        d = _safe_date(request.args.get("d")) or date.today()

    employees = (db.session.query(Employee, User)
                 .join(User, Employee.user_id == User.id)
                 .filter(Employee.is_active == True)
                 .order_by(User.supervisor_code.asc(), Employee.name.asc())
                 .all())

    if mode == "day":
        atts = Attendance.query.filter_by(date=d).all()
        att_by_emp = {a.employee_id: a for a in atts}
        rows = []
        for emp, sup in employees:
            a = att_by_emp.get(emp.id)
            rows.append({
                "emp": emp, "sup": sup,
                "status": (a.status if a else None),
                "remarks": (a.remarks if a else ""),
                "marked_by": (db.session.get(User, a.supervisor_id) if a else None)
            })
        return render_template("attendance_print.html", mode="day", day_date=d, rows=rows)

    else:
        emp_ids = [emp.id for emp, _ in employees]
        atts = []
        if emp_ids:
            atts = (Attendance.query
                    .filter(Attendance.employee_id.in_(emp_ids),
                            Attendance.date >= ws,
                            Attendance.date <= we).all())
        from collections import defaultdict
        stats = defaultdict(lambda: {"present": 0, "absent": 0, "leave": 0, "total": 0})
        for a in atts:
            stats[a.employee_id]["total"] += 1
            if a.status in ("present", "absent", "leave"):
                stats[a.employee_id][a.status] += 1
        rows = []
        for emp, sup in employees:
            s = stats[emp.id]
            pct = (s["present"]/s["total"]*100.0) if s["total"] else 0.0
            rows.append({
                "emp": emp, "sup": sup,
                "present": s["present"], "absent": s["absent"], "leave": s["leave"],
                "total": s["total"], "percent": round(pct,1)
            })
        return render_template("attendance_print.html", mode="week", ws=ws, we=we, rows=rows)

@app.route("/attendance/pdf", methods=["GET"])
@admin_required
def attendance_pdf():
    frm = request.args.get("from"); to = request.args.get("to")
    try:
        d_from = parse_date(frm) if frm else date.today().replace(day=1)
    except: d_from = date.today().replace(day=1)
    try:
        d_to = parse_date(to) if to else date.today()
    except: d_to = date.today()

    employees = (db.session.query(Employee, User)
                 .join(User, Employee.user_id == User.id)
                 .filter(Employee.is_active == True)
                 .order_by(User.supervisor_code.asc(), Employee.name.asc())
                 .all())

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.add_page()
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, f"Attendance Report {d_from} to {d_to}", ln=True, align="C")

    pdf.set_font("Arial", "B", 10)
    pdf.cell(60, 8, "Employee", 1)
    pdf.cell(35, 8, "Supervisor ID", 1)
    pdf.cell(20, 8, "Total", 1)
    pdf.cell(25, 8, "Present", 1)
    pdf.cell(25, 8, "Leave", 1)
    pdf.cell(25, 8, "Absent", 1)
    pdf.cell(25, 8, "Attendance %", 1)
    pdf.ln()

    pdf.set_font("Arial", "", 10)
    for emp, sup in employees:
        q = Attendance.query.filter(Attendance.employee_id==emp.id,
                                    Attendance.date>=d_from,
                                    Attendance.date<=d_to)
        total = q.count()
        present = q.filter_by(status="present").count()
        leave   = q.filter_by(status="leave").count()
        absent  = q.filter_by(status="absent").count()
        pct = (present/total*100.0) if total else 0.0

        pdf.cell(60, 8, emp.name[:28], 1)
        pdf.cell(35, 8, (sup.supervisor_code or "")[:12], 1)
        pdf.cell(20, 8, str(total), 1, align="C")
        pdf.cell(25, 8, str(present), 1, align="C")
        pdf.cell(25, 8, str(leave), 1, align="C")
        pdf.cell(25, 8, str(absent), 1, align="C")
        pdf.cell(25, 8, f"{pct:.1f}%", 1, align="C")
        pdf.ln()

    return Response(pdf.output(dest="S").encode("latin1"),
                    mimetype="application/pdf",
                    headers={"Content-Disposition":"inline; filename=attendance.pdf"})

@app.route("/attendance/mark", methods=["GET", "POST"])
@login_required
def attendance_mark():
    u = cur_user()
    if not u or u.role not in ("supervisor", "admin"):
        flash("Unauthorized", "danger")
        return redirect(url_for("index"))

    # الموظفين للمشرف الحالي
    employees = (Employee.query
                 .filter_by(user_id=u.id, is_active=True)
                 .order_by(Employee.name.asc())
                 .all())

    # هذا اللي نعرضه في الفورم
    today = date.today()

    if request.method == "POST":
        # اقرأ التاريخ اللي جاي من الفورم (name="date" في التمبليت)
        d_raw = request.form.get("date")
        if d_raw:
            # نحلّلها يدوي عشان ما يصير خطأ 500 لو السيرفر قديم
            try:
                y, m, d = map(int, d_raw.split("-"))
                mark_date = date(y, m, d)
            except ValueError:
                mark_date = today
        else:
            mark_date = today

        changed = 0
        for emp in employees:
            status = (request.form.get(f"emp_{emp.id}_status") or "").strip()
            remarks = (request.form.get(f"emp_{emp.id}_remarks") or "").strip()
            if not status:
                continue

            # استخدم التاريخ اللي اختاره المستخدم
            rec = Attendance.query.filter_by(employee_id=emp.id, date=mark_date).first()
            if not rec:
                rec = Attendance(
                    employee_id=emp.id,
                    supervisor_id=u.id,
                    date=mark_date,
                    status=status,
                    remarks=remarks,
                    company_id=cid(),
                )
                db.session.add(rec)
                changed += 1
            else:
                if rec.status != status or rec.remarks != remarks:
                    rec.status = status
                    rec.remarks = remarks
                    changed += 1

        if changed:
            db.session.commit()
            flash("Attendance saved.", "success")
        else:
            flash("No changes.", "info")

        # نرجع لنفس الصفحة زي أول
        return redirect(url_for("attendance_mark"))

    # GET: اعرض حضور اليوم
    saved = {a.employee_id: a for a in Attendance.query.filter_by(date=today).all()}
    return render_template("attendance_mark.html",
                           employees=employees,
                           today=today,
                           saved=saved)


# ─────────────────────────────────────────────
#  صفحة الدعم — Apple App Store requirement
#  https://nsh.z-app.biz/support
# ─────────────────────────────────────────────
@app.route("/support")
def support_page():
    path = os.path.join(BASE_DIR, "static", "support.html")
    with open(path, "r", encoding="utf-8") as f:
        return f.read(), 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/")
def index():
    u = cur_user()
    if not u:
        return render_template("landing.html")
    role = u.role
    if role == "super_admin":
        return redirect(url_for("superadmin_dashboard"))
    if role == "admin":
        return redirect(url_for("admin_kpi"))
    if role == "site_supervisor":
        return redirect(url_for("site_supervisors"))
    if role == "hr":
        return redirect(url_for("hr_inbox"))
    if role == "safety_officer":
        if getattr(u, "ptw_training_active", False):
            return redirect(url_for("ptw_training_home"))
        return redirect(url_for("hse_checkin"))
    if role == "safety_supervisor":
        return redirect(url_for("safety_supervisor_home"))
    if role == "safety_manager":
        return redirect(url_for("safety_manager_dashboard"))
    if role == "safety_welfare":
        return redirect(url_for("welfare_home"))
    if role == "environment_officer":
        return redirect(url_for("env_dashboard"))
    if role == "supervisor":
        return redirect(url_for("employees"))
    return redirect(url_for("login"))

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

    # أسبوع افتراضي (أو من الاستعلام)
    ws, we = default_week_today()
    if request.args.get("week_start") and request.args.get("week_end"):
        ws = parse_date(request.args["week_start"])
        we = parse_date(request.args["week_end"])
    pws, pwe = previous_week_range(ws)

    # ---------- Supervisor → Employees ----------
    supervisors = (
        apply_company_filter(User.query.filter_by(role="supervisor", is_active=True), User)
        .order_by(User.supervisor_code.asc()).all()
    )

    emp_totals_map = dict(
        apply_company_filter(
            db.session.query(Employee.user_id, func.count(Employee.id))
            .filter(Employee.is_active == True), Employee)
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

    # avg score per supervisor this week
    avg_score_map = dict(
        db.session.query(Employee.user_id, func.avg(Evaluation.total_score))
        .join(Evaluation, Evaluation.employee_id == Employee.id)
        .filter(Evaluation.week_start == ws, Evaluation.week_end == we)
        .group_by(Employee.user_id).all()
    )

    sup_rows, sum_target_emp, sum_done_emp = [], 0, 0
    growth_pool_prev, growth_pool_curr = 0, 0

    for sup in supervisors:
        target = emp_totals_map.get(sup.id, 0)
        done_c = emp_cnt_this.get(sup.id, 0)
        done_p = emp_cnt_prev.get(sup.id, 0)
        coverage = (done_c / target * 100.0) if target else None
        prev_coverage = (done_p / target * 100.0) if target else None

        sum_target_emp += target
        sum_done_emp += done_c

        if done_c > 0 and done_p > 0:
            growth_pool_prev += done_p
            growth_pool_curr += done_c

        avg_sc = avg_score_map.get(sup.id)
        sup_rows.append({
            "id": sup.id,
            "code": sup.supervisor_code,
            "name": sup.name,
            "target": target,
            "done_c": done_c,
            "done_p": done_p,
            "coverage": coverage,
            "prev_coverage": prev_coverage,
            "avg_score": round(float(avg_sc), 1) if avg_sc else None,
            "delta": (done_c - done_p),
            "trend": ("up" if done_c > done_p else "down" if done_c < done_p else "flat"),
        })

    # ---------- Site Supervisor → Supervisors ----------
    site_sups = (
        apply_company_filter(User.query.filter_by(role="site_supervisor", is_active=True), User)
        .order_by(User.supervisor_code.asc()).all()
    )

    assigned_counts = dict(
        db.session.query(
            SiteSupervisorMap.site_sup_id,
            func.count(func.distinct(SiteSupervisorMap.supervisor_id)),
        )
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

    site_rows, sum_target_sup, sum_done_sup = [], 0, 0
    for s in site_sups:
        target = assigned_counts.get(s.id, 0)
        done_c = se_cnt_this.get(s.id, 0)
        done_p = se_cnt_prev.get(s.id, 0)
        coverage = (done_c / target * 100.0) if target else None
        prev_coverage = (done_p / target * 100.0) if target else None

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
            "prev_coverage": prev_coverage,
            "delta": (done_c - done_p),
            "trend": ("up" if done_c > done_p else "down" if done_c < done_p else "flat"),
        })

    # ---------- إجماليات ----------
    _ev_this = apply_company_filter(
        db.session.query(func.count(Evaluation.id))
        .filter(Evaluation.week_start == ws, Evaluation.week_end == we), Evaluation)
    _se_this = apply_company_filter(
        db.session.query(func.count(SupervisorEvaluation.id))
        .filter(SupervisorEvaluation.week_start == ws, SupervisorEvaluation.week_end == we), SupervisorEvaluation)
    total_this_week = (_ev_this.scalar() or 0) + (_se_this.scalar() or 0)

    _ev_prev = apply_company_filter(
        db.session.query(func.count(Evaluation.id))
        .filter(Evaluation.week_start == pws, Evaluation.week_end == pwe), Evaluation)
    _se_prev = apply_company_filter(
        db.session.query(func.count(SupervisorEvaluation.id))
        .filter(SupervisorEvaluation.week_start == pws, SupervisorEvaluation.week_end == pwe), SupervisorEvaluation)
    total_prev_week = (_ev_prev.scalar() or 0) + (_se_prev.scalar() or 0)

    growth_pct = 0.0
    if growth_pool_prev > 0:
        growth_pct = (growth_pool_curr - growth_pool_prev) / growth_pool_prev * 100.0

    overall_emp_coverage = (sum_done_emp / sum_target_emp * 100.0) if sum_target_emp else None
    overall_sup_coverage = (sum_done_sup / sum_target_sup * 100.0) if sum_target_sup else None

    # ---------- Attendance & Leave Rates (من جميع الفرص) ----------
    # الموظفون النشطون + عدد أيام الأسبوع (Sun→Thu بعد التحقق)
    active_emp_ids = [e.id for e in apply_company_filter(Employee.query.filter_by(is_active=True), Employee).all()]
    active_count = len(active_emp_ids)
    workdays = (we - ws).days + 1  # يفترض Sun..Thu بعد validate_week_sun_to_thu

    total_opportunities = active_count * workdays  # المقام

    # تعداد الحالات المسجلة
    present_count = db.session.query(func.count(Attendance.id)).filter(
        Attendance.date >= ws,
        Attendance.date <= we,
        Attendance.employee_id.in_(active_emp_ids),
        Attendance.status == 'present'
    ).scalar() or 0

    leave_count = db.session.query(func.count(Attendance.id)).filter(
        Attendance.date >= ws,
        Attendance.date <= we,
        Attendance.employee_id.in_(active_emp_ids),
        Attendance.status == 'leave'
    ).scalar() or 0

    # الغياب = الباقي (يشمل الأيام/الموظفين غير المسجلين)
    absent_count = max(total_opportunities - (present_count + leave_count), 0)

    att_rate   = round(present_count / total_opportunities * 100.0, 1) if total_opportunities else None
    leave_rate = round(leave_count   / total_opportunities * 100.0, 1) if total_opportunities else None
    # (اختياري) احسب غياب:
    # absent_rate = round(absent_count / total_opportunities * 100.0, 1) if total_opportunities else None

    # band distribution this week
    band_rows = (db.session.query(Evaluation.overall_band, func.count(Evaluation.id))
                 .filter(Evaluation.week_start == ws, Evaluation.week_end == we)
                 .group_by(Evaluation.overall_band).all())
    band_counts = {b: c for b, c in band_rows}

    # overall avg score this week
    avg_score_val = (db.session.query(func.avg(Evaluation.total_score))
                     .filter(Evaluation.week_start == ws, Evaluation.week_end == we).scalar())
    avg_score = round(float(avg_score_val), 1) if avg_score_val else None

    # ---------- Pending requests ----------
    pending_requests = apply_company_filter(
        Request.query.filter_by(status="pending"), Request).count()

    # ---------- Active evaluated employees this week ----------
    active_evaluated = sum(r["done_c"] for r in sup_rows)
    active_total     = sum(r["target"] for r in sup_rows)

    # ---------- Chart: top supervisors by coverage ----------
    chart_sups = sorted(
        [r for r in sup_rows if r["coverage"] is not None],
        key=lambda x: x["coverage"], reverse=True
    )[:10]
    chart_labels  = [r["name"].split()[0] if r["name"] else r["code"] for r in chart_sups]
    chart_values  = [round(r["coverage"], 1) for r in chart_sups]

    return render_template(
        "admin_kpi.html",
        ws=ws, we=we, pws=pws, pwe=pwe,
        total_this_week=total_this_week, total_prev_week=total_prev_week,
        growth_pct=growth_pct,
        overall_emp_cov=overall_emp_coverage,
        overall_sup_cov=overall_sup_coverage,
        att_rate=att_rate,
        leave_rate=leave_rate,
        avg_score=avg_score,
        band_counts=band_counts,
        sup_rows=sup_rows, site_rows=site_rows,
        pending_requests=pending_requests,
        active_evaluated=active_evaluated,
        active_total=active_total,
        chart_labels=chart_labels,
        chart_values=chart_values,
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


@app.route("/admin/supervisor/<int:sup_id>/employees")
@admin_required
def admin_supervisor_employees(sup_id):
    sup = User.query.filter_by(id=sup_id, role="supervisor").first_or_404()

    ws, we = default_week_today()
    if request.args.get("week_start") and request.args.get("week_end"):
        ws = parse_date(request.args["week_start"])
        we = parse_date(request.args["week_end"])

    employees = (Employee.query.filter_by(user_id=sup.id, is_active=True)
                 .order_by(Employee.name.asc()).all())
    emp_ids = [e.id for e in employees]

    evals_map = {}
    if emp_ids:
        evals = (Evaluation.query
                 .filter(Evaluation.employee_id.in_(emp_ids),
                         Evaluation.week_start == ws, Evaluation.week_end == we)
                 .all())
        evals_map = {e.employee_id: e for e in evals}

    rows = []
    for emp in employees:
        ev = evals_map.get(emp.id)
        rows.append({"emp": emp, "ev": ev})

    kpi_target = len(employees)
    kpi_done   = len(evals_map)
    kpi_coverage = round(kpi_done / kpi_target * 100.0, 0) if kpi_target else None
    scored = [e.total_score for e in evals_map.values() if e.total_score is not None]
    kpi_avg = round(sum(scored) / len(scored), 1) if scored else None

    return render_template("admin_supervisor_employees.html", sup=sup, rows=rows,
                           ws=ws, we=we, kpi_target=kpi_target, kpi_done=kpi_done,
                           kpi_coverage=kpi_coverage, kpi_avg=kpi_avg)


@app.get("/_health")
def _health():
    return "ok"

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

@app.get("/hr/inbox")
@hr_required
def hr_inbox():
    rows = apply_company_filter(HRTask.query, HRTask).order_by(HRTask.created_at.desc()).all()

    # ── ترميم ذاتي: أي إنذار موقّع من الموظف لكن ملف الـ PDF غير موجود
    #    (بسبب فشل توليد سابق) — نحاول توليده الآن قبل عرض صندوق HR
    for t in rows:
        if t.type == "warning" and t.request and t.request.warning_sig and not t.warning_pdf:
            try:
                pdf_name = generate_warning_pdf(t.request, t.request.warning_sig)
                if pdf_name:
                    t.warning_pdf = pdf_name
                    db.session.commit()
            except Exception as e:
                app.logger.error("hr_inbox auto-generate warning PDF (task %s): %s", t.id, e)
                db.session.rollback()

    return render_template("hr_inbox.html", rows=rows)


@app.get("/hr/id-expiry")
@hr_required
def hr_id_expiry():
    """صفحة HR لمتابعة بطاقات/إقامات الموظفين القريبة من الانتهاء أو المنتهية فعلاً"""
    from datetime import date, timedelta
    q = (request.args.get("q") or "").strip()

    today = date.today()
    soon  = today + timedelta(days=30)

    emp_q = apply_company_filter(
        Employee.query.filter(Employee.id_expiry_date.isnot(None),
                              Employee.id_expiry_date <= soon,
                              Employee.is_active == True),
        Employee)
    if q:
        like = f"%{q}%"
        emp_q = emp_q.filter(or_(Employee.name.ilike(like), Employee.emp_number.ilike(like)))

    rows = emp_q.order_by(Employee.id_expiry_date.asc()).all()

    expired = [e for e in rows if e.id_expiry_date < today]
    expiring_soon = [e for e in rows if e.id_expiry_date >= today]

    return render_template("hr_id_expiry.html", expired=expired, expiring_soon=expiring_soon,
                           q=q, today=today)


@app.post("/hr/task/<int:task_id>/apply")
@hr_required
def hr_task_apply(task_id):
    t = db.session.get(HRTask, task_id)
    if not t:
        abort(404)
    if t.status == "pending":
        t.status = "applied"
        t.applied_at = datetime.now(timezone.utc)
        t.applied_by = cur_user().id
        db.session.commit()
    return redirect(url_for("hr_inbox"))


@app.post("/hr/task/<int:task_id>/regenerate-warning-pdf")
@hr_required
def hr_task_regenerate_warning_pdf(task_id):
    """إعادة توليد PDF الإنذار يدوياً — تُظهر سبب الفشل الفعلي بدل الفشل الصامت"""
    t = db.session.get(HRTask, task_id)
    if not t or t.type != "warning":
        abort(404)
    req = t.request
    sig = req.warning_sig if req else None
    if not req or not sig:
        flash("This warning has no employee signature yet.", "danger")
        return redirect(url_for("hr_inbox"))
    try:
        pdf_name = generate_warning_pdf(req, sig)
        if pdf_name:
            t.warning_pdf = pdf_name
            db.session.commit()
            flash("PDF generated successfully.", "success")
        else:
            flash("PDF generation failed: no filename returned (check the reportlab library).", "danger")
    except Exception as e:
        db.session.rollback()
        app.logger.error("manual regenerate warning PDF (task %s): %s", task_id, e)
        flash(f"PDF generation failed: {e}", "danger")
    return redirect(url_for("hr_inbox"))


@app.post("/hr/task/<int:task_id>/set-reason")
@hr_required
def hr_task_set_reason(task_id):
    """HR يحدد السبب الرسمي والشركة (NSH/GA) لخطاب الإنذار من الويب"""
    t = db.session.get(HRTask, task_id)
    if not t or t.type != "warning":
        abort(404)
    reason  = (request.form.get("official_reason") or "").strip()
    company = (request.form.get("warning_company") or "NSH").strip().upper()
    if company not in ("NSH", "GA"):
        company = "NSH"
    if not reason:
        flash("Official reason is required.", "danger")
        return redirect(url_for("hr_inbox"))
    try:
        t.official_reason = reason
        t.warning_company = company
    except Exception:
        from sqlalchemy import text as _text
        with db.engine.begin() as _conn:
            _conn.execute(_text("ALTER TABLE hr_task ADD COLUMN IF NOT EXISTS official_reason TEXT NULL"))
            _conn.execute(_text("ALTER TABLE hr_task ADD COLUMN IF NOT EXISTS warning_company VARCHAR(10) NULL"))
        t.official_reason = reason
        t.warning_company = company
    db.session.commit()
    # إشعار للمشرف
    if t.request:
        threading.Thread(
            target=_send_push_to_user,
            args=(t.request.supervisor_id, "إنذار — جاهز للتوقيع",
                  "تم تحديد السبب الرسمي، يمكنك الآن فتح نموذج الإنذار"),
            daemon=True,
        ).start()
    flash("Official reason recorded.", "success")
    return redirect(url_for("hr_inbox"))

@app.route("/admin")
@admin_required
def admin_home():
    return redirect(url_for("admin_kpi"))

@app.route("/login", methods=["GET", "POST"])
@_login_limit
def login():
    if request.method == "POST":
        mode     = request.form.get("mode", "code")
        password = (request.form.get("password") or "").strip()

        user = None
        if mode == "email":
            email = (request.form.get("email") or "").strip().lower()
            if not email:
                flash("Please enter your email.", "danger")
                return render_template("login.html", login_mode="email")
            user = User.query.filter_by(email=email, is_active=True).first()
            if not user or not user.check_password(password):
                flash("Invalid email or password.", "danger")
                return render_template("login.html", login_mode="email")
        else:
            code = (request.form.get("code") or request.form.get("sup_code") or "").strip()
            if not code:
                flash("Please enter your Supervisor ID.", "danger")
                return redirect(url_for("login"))
            user = User.query.filter_by(supervisor_code=code, is_active=True).first()
            if not user:
                flash("ID not found or inactive.", "danger")
                return redirect(url_for("login"))

        # Check company is active (skip for super_admin)
        if user.role != "super_admin" and user.company_id:
            co = db.session.get(Company, user.company_id)
            if co and not co.is_active:
                flash("Your company account is pending approval.", "warning")
                return redirect(url_for("login"))

        session.clear()
        session["user_id"] = user.id
        # Always store company_id (None is fine for super_admin)
        session["company_id"] = user.company_id

        if user.role == "super_admin":
            return redirect(url_for("superadmin_dashboard"))
        elif user.role == "admin":
            return redirect(url_for("admin_kpi"))
        elif user.role == "site_supervisor":
            return redirect(url_for("site_supervisors"))
        elif user.role == "hr":
            return redirect(url_for("hr_inbox"))
        elif user.role == "safety_officer":
            if getattr(user, "ptw_training_active", False):
                return redirect(url_for("ptw_training_home"))
            return redirect(url_for("hse_checkin"))
        elif user.role == "safety_supervisor":
            return redirect(url_for("safety_supervisor_home"))
        elif user.role == "safety_manager":
            return redirect(url_for("safety_manager_dashboard"))
        elif user.role == "safety_welfare":
            return redirect(url_for("welfare_home"))
        elif user.role == "environment_officer":
            return redirect(url_for("env_dashboard"))
        elif user.role == "supervisor":
            return redirect(url_for("employees"))
        else:
            return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/register/choose")
def register_choose():
    if cur_user():
        return redirect(url_for("index"))
    return render_template("register_choose.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if cur_user():
        return redirect(url_for("index"))

    if request.method == "POST":
        company_name = (request.form.get("company_name") or "").strip()
        your_name    = (request.form.get("your_name") or "").strip()
        email        = (request.form.get("email") or "").strip().lower()
        password     = (request.form.get("password") or "").strip()
        password2    = (request.form.get("password2") or "").strip()

        errors = []
        if not company_name:
            errors.append("Company name is required.")
        if not your_name:
            errors.append("Your name is required.")
        if not email or "@" not in email:
            errors.append("Valid email is required.")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != password2:
            errors.append("Passwords do not match.")
        if User.query.filter_by(email=email).first():
            errors.append("This email is already registered.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("register.html",
                                   company_name=company_name, your_name=your_name, email=email)

        # Build slug from company name
        import re
        slug = re.sub(r"[^a-z0-9]+", "-", company_name.lower()).strip("-")
        base_slug = slug
        counter = 1
        while Company.query.filter_by(slug=slug).first():
            slug = f"{base_slug}-{counter}"
            counter += 1

        company = Company(name=company_name, slug=slug, owner_email=email, is_active=False)
        db.session.add(company)
        db.session.flush()  # get company.id

        # Generate a unique supervisor_code for the new admin
        import secrets as _sec
        sup_code = f"adm-{_sec.token_hex(4)}"
        while User.query.filter_by(supervisor_code=sup_code).first():
            sup_code = f"adm-{_sec.token_hex(4)}"

        admin_user = User(
            supervisor_code=sup_code,
            name=your_name,
            email=email,
            role="admin",
            is_active=True,
            company_id=company.id,
        )
        admin_user.set_password(password)
        db.session.add(admin_user)
        db.session.commit()

        flash("Registration submitted! Your account is pending approval. You will be notified once approved.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", company_name="", your_name="", email="")


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
                u = User(supervisor_code=code, name=name, role=role,
                         is_active=True, company_id=cid())
                db.session.add(u)
                db.session.commit()
                flash("User added.", "success")
        return redirect(url_for("admin_users"))
    show_hidden = request.args.get("show_hidden") == "1"
    q = apply_company_filter(User.query, User)
    if not show_hidden:
        q = q.filter(db.or_(User.is_hidden == False, User.is_hidden == None))
    users = q.order_by(User.role.desc(), User.supervisor_code.asc()).all()
    return render_template("admin_users.html", users=users, show_hidden=show_hidden)
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
    supervisors = (
        apply_company_filter(User.query.filter_by(role="supervisor", is_active=True), User)
        .order_by(User.supervisor_code.asc()).all()
    )
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


@app.post("/admin/users/<int:user_id>/hide")
@admin_required
def admin_users_hide(user_id):
    u = db.session.get(User, user_id) or abort(404)
    if u.role == "admin":
        flash("Cannot hide admin.", "warning")
    else:
        current = getattr(u, "is_hidden", False) or False
        u.is_hidden = not current
        db.session.commit()
        flash("Hidden." if u.is_hidden else "تم الإظهار.", "success")
    return redirect(url_for("admin_users"))

@app.post("/admin/users/<int:user_id>/ptw-training")
@admin_required
def admin_users_toggle_ptw(user_id):
    u = db.session.get(User, user_id) or abort(404)
    if u.role != "safety_officer":
        flash("PTW training is only for safety officers.", "warning")
    else:
        u.ptw_training_active = not bool(getattr(u, "ptw_training_active", False))
        db.session.commit()
        flash(("PTW training activated." if u.ptw_training_active else "PTW training deactivated."), "success")
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

    if new_role not in ["supervisor", "site_supervisor", "safety_officer", "safety_supervisor",
                        "safety_welfare", "environment_officer"]:
        flash("Invalid role.", "danger")
    else:
        u.role = new_role
        db.session.commit()
        flash("Role updated.", "success")

    return redirect(url_for("admin_users"))


# ── Admin: Safety Team Assignment ────────────────────────────────────

OFFICER_ROLES = ("safety_officer", "safety_welfare", "environment_officer")


# ── PTW Training: 7 modules × 6 doors (door 6 = reference, no submission) ────────
PTW_MODULES = [
  {"seq": 1, "title": "Foundation: System, Roles and Documents",
   "title_ar": "الأساس: النظام والأدوار والوثائق",
   "doors": [
    {"seq": 1, "title": "Orientation: project permit types and where to find them",
     "ref": "CSAR 4.12 · CSM I-4 / 4.3 · 4.5",
     "brief": "Go with your supervisor to an active work area. Identify three permits of different types — note the type, where posted, and how to tell them apart visually. Do not verify anything. Max permit validity on this project: 12 hours.",
     "questions": [
      "Identify three permits of different types.",
      "For each: what type is it? Where is it posted? How do you tell it apart from the others visually?",
      "Do not verify anything on the permit — this door is for visual orientation only.",
     ]},
    {"seq": 2, "title": "Roles: who they are and where they sign",
     "ref": "CSM I-4 / 4.4 · CSM 4.3 · CSAR 7 · 8",
     "brief": "Hold an active cold work permit with your supervisor. Identify all six roles by name, verify each signature is in the correct section, and speak to one of the six role-holders about their responsibility.",
     "questions": [
      "Identify all six roles by name from the permit itself — write them in a table.",
      "Check: is each role's signature in the correct section?",
      "Go to one of the six role-holders — ask them: what is your responsibility under this permit?",
      "Does their answer match the table above?",
     ]},
    {"seq": 3, "title": "The system: documents behind the permit",
     "ref": "CSAR 5 · 6 · CSM 4.6.3.D · CSM II-15 · SMG 06-003 · CSM 4.10.9 · CSM 4.6.3.E",
     "brief": "Choose a different active cold work permit in a different area. Request and verify: WMS, JSA, SGL, daily checklist, gas test record. Compare SGL names to actual headcount and one JSA step to the work being done.",
     "questions": [
      "Ask the receiver for: WMS, JSA, SGL, and the daily checklist.",
      "For each document: does it exist? Does its number match what is written in Section 1? Is its content specific to this job or generic?",
      "Compare SGL names against who is actually in the area — is everyone listed?",
      "Compare one JSA step with the work being done in front of you — does it match?",
     ]},
    {"seq": 4, "title": "Audit: documents, roles and formal observations",
     "ref": "CSM 4.6.3 · CSAR 5 · 6 · 7",
     "brief": "Field practical (3 hours). New active cold work area. Apply the full checklist — permit position, six roles, Sections 1 and 2, all supporting documents. Each finding written as: Observation / Requirement reference / Required action.",
     "questions": [
      "Go to an active work area (active cold permit) not visited in the previous doors.",
      "Ask the receiver for: the permit, WMS, JSA, SGL, and the daily checklist.",
      "Apply the checklist item by item.",
      "Write your findings in formal format.",
      "Present your findings to your field supervisor before the session ends.",
     ]},
    {"seq": 5, "title": "Evaluation: independent report on an active cold work permit",
     "ref": "CSM 4.6.3 · 4.10 · CSAR 5 · 6 · 7",
     "brief": "Work alone — no supervisor. Active cold work permit, not visited before (3 hours field). Verify all six roles, Sections 1 and 2, all supporting documents. Speak to two workers about task hazards. Write a complete report.",
     "questions": [
      "Go to an active cold work permit location not visited before.",
      "Meet the receiver and supervisor. Explain your purpose: independent training evaluation.",
      "Apply the full checklist on your own.",
      "Talk to two workers in the area about the task hazards.",
      "Write a report: what was compliant, what was deficient, what you recommend.",
     ]},
    {"seq": 6, "title": "Reference — System background and technical references",
     "ref": "CSM I-4 / 4.1 · 4.3 · CSAR 4.12 · CSM Intro · CSAR 6.2 Table 6.1 · CSAR 7.1 · 7.7",
     "brief": "Reference material for Module 1 — consult when needed. Covers: why the permit system exists, two-case framework (operating vs. new-construction), manual structure (CSAR vs. WSSM), CSAR Table 6.1 mandatory HIP topics, the six-role signature map, Clause 7.7 five responsibilities of the Field HSE Officer, and the activity-document comparison (HIP / WMS / JSA).",
     "ref_only": True,
     "questions": [],
    },
  ]},
  {"seq": 2, "title": "Hot Work",
   "title_ar": "الأعمال الساخنة",
   "doors": [
    {"seq": 1, "title": "Orientation: the red permit and where to find it on site",
     "ref": "CSM I-4 / 4.5 · 4.10.2 · II-10 / 10.1",
     "brief": "Go with your supervisor to an active welding, cutting or heavy-equipment area. Locate the red permit (Form 9873-2). Observe: fire watcher position, PWAS unit on equipment, gas test record. Do not verify — observe and note only.",
     "questions": [
      "Locate the red permit — posted or held by the receiver.",
      "Observe: where is the fire watcher positioned? Can you see a PWAS unit on the equipment? Is a gas tester present or is a gas test record visible?",
      "Do not verify anything — observe and note only.",
     ]},
    {"seq": 2, "title": "The complete hot work system",
     "ref": "CSM 4.6.3.B · 4.10.3–4.10.5 · 4.10.10 · 4.10.12 · 4.10.13 · GI 2.709 · CSM II-10 · III-2",
     "brief": "With your supervisor. Different welding/cutting area. Request the active red permit and verify the five elements: fire watcher (name/cert/30-min rule), PWAS, gas test Section 4 at 0% LEL, Section 2 hazards specific not generic, WMS and JSA present.",
     "questions": [
      "Request the active red permit.",
      "Verify all five elements above one by one.",
      "Go to the fire watcher — ask: how many minutes do you stay after welding stops?",
      "Look at the heavy equipment — is the PWAS fitted and working?",
     ]},
    {"seq": 3, "title": "Independent verification on a different hot work permit",
     "ref": "CSM 4.6.3 · 4.10 · II-10 · III-2 · SMG 06-003 · CSM II-15",
     "brief": "Fully independent. New hot work area not visited before. Request the red permit and all attachments. Apply the complete hot work checklist and write at least one formal finding.",
     "questions": [
      "Request the red permit and all its attachments.",
      "Apply the complete checklist above on your own.",
      "Verify in the field: fire watcher at his post, PWAS working, gas test record current.",
      "Write at least one formal finding: observation / reference / required action.",
     ]},
    {"seq": 4, "title": "Audit: formal findings on active hot work permits",
     "ref": "CSM 4.6.3 · 4.10 · II-10 · III-2",
     "brief": "Field practical (3 hours). New welding/cutting/grinding area. Apply the full audit checklist and write all findings in formal format: observation / reference / required action.",
     "questions": [
      "Go to an active welding, cutting or grinding area not visited before.",
      "Request the active hot work permit and all its attachments.",
      "Apply the audit checklist item by item.",
      "Verify in the field: fire watcher in position, PWAS operational, gas test record present.",
      "Write all findings in formal format and present them.",
     ]},
    {"seq": 5, "title": "Evaluation: independent report at the welding and cutting area",
     "ref": "CSM 4.6.3 · 4.10 · II-10 · III-2",
     "brief": "Work alone. Active hot work permit (welding or torch cutting), not visited before (3 hours field). Apply the full system: six roles, Sections 1/2/4, fire watcher, PWAS on all heavy equipment, JSA vs actual work, SGL, firefighting equipment ready.",
     "questions": [
      "Go to an active hot work area (welding or torch cutting) not visited before.",
      "Meet the receiver, Originator and safety officer in the area.",
      "Apply the full checklist on your own.",
      "Speak to the fire watcher: ask how many minutes he stays after welding stops and what he checks.",
      "Write a report: what matched requirements, what needs correction, what you observed in the field that is absent from the permit.",
     ]},
    {"seq": 6, "title": "Reference — Hot work: technical references and scenarios",
     "ref": "CSM I-4 / 4.5 · II-10 / 10.1 · III-2 · GI 2.709 · CSM 4.6.3.B · 4.10.12 · 4.10.13",
     "brief": "Reference material for Module 2 — consult when needed. Covers: WSSM Chapter II-10 (cutting and welding), Chapter III-2 (heavy equipment), approach distances for overhead power lines, gas testing limits (O₂, LEL, H₂S), and common scenarios with answers.",
     "ref_only": True,
     "questions": [],
    },
  ]},
  {"seq": 3, "title": "Work at Height and Grating",
   "title_ar": "العمل على ارتفاع والقريتنق",
   "doors": [
    {"seq": 1, "title": "Orientation: work at height and grating on site",
     "ref": "CSM II-5 / 5.3.2 · II-4 / 4.5 · CSAR 4.18",
     "brief": "Go with your supervisor to an active work-at-height area. Observe: fall protection in use, harness condition, grating permit if any grating was opened, rescue equipment location. Do not verify — observe and note only.",
     "questions": [
      "Write the work-at-height activities you saw and where the permit was posted.",
      "What fall protection equipment did you observe being used? Was the harness twin-lanyard type?",
      "Was any grating or handrail opened? If so, was a grating permit visible? Write what you observed.",
     ]},
    {"seq": 2, "title": "The complete work-at-height system",
     "ref": "CSM II-5 / 5.3.2 · 5.5 · I-6 · CSAR 4.18 · 4.10 · 4.13",
     "brief": "With your supervisor. Different height work area. Request the permit and verify: rescue plan (A–G sections site-specific), fall protection (twin lanyard, shock absorber), all floor openings covered, grating permit Parts 1–6 if applicable, daily checklist seven groups.",
     "questions": [
      "Is a rescue plan attached to the permit? Is it site-specific or generic? State what you found for each section (A–G).",
      "Fall protection: twin lanyard? Shock absorber? Harness condition? Write findings for each item.",
      "Are all floor openings in the area covered, secured and marked? Write the exact status of each opening.",
     ]},
    {"seq": 3, "title": "Independent verification at a different height work area",
     "ref": "CSM II-5 · I-6 · II-4 · CSAR 4.18 · 4.10",
     "brief": "Fully independent. New work-at-height area not visited before. Request the permit and all attachments. Apply the complete height work checklist and write at least one formal finding.",
     "questions": [
      "Grating permit: issued for this shift only? Has Part 6 been completed from the previous shift?",
      "Rescue plan sections A through G — which sections are complete and which are incomplete? Write your evidence.",
      "Write at least one formal finding: observation / reference / required action.",
     ]},
    {"seq": 4, "title": "Audit: formal findings on height work and grating permits",
     "ref": "CSM II-4 · II-5 · I-6 · CSAR 4.18",
     "brief": "Field practical (3 hours). New height work area. Apply the full audit checklist — rescue plan, fall protection, grating permit Parts 1–6, floor openings, SGL. Write all findings in formal format.",
     "questions": [
      "Is a rescue plan attached and site-specific? Write the evidence for each of sections A through G.",
      "Check all floor openings: covered, secured and marked? Write the exact status of each.",
      "Grating permit (if applicable): issued for this shift only? Has Part 6 been completed from the previous shift?",
      "Write one formal finding from today's field audit.",
     ]},
    {"seq": 5, "title": "Evaluation: independent report at the height work area",
     "ref": "CSM II-4 · II-5 · CSAR 4.18",
     "brief": "Work alone. Active work-at-height area not visited before (3 hours field). Verify all six roles, Sections 1 and 2, rescue plan A–G, fall protection, grating permit Parts 1–6 (if applicable), daily checklist seven groups, SGL complete.",
     "questions": [
      "Verify the fall protection: harness condition, lanyard type (twin?), shock absorber present? Write your findings.",
      "Review rescue plan sections A through G — write whether each section is complete or incomplete.",
      "Write a report: what was compliant, what was deficient, your observations versus the permit.",
     ]},
    {"seq": 6, "title": "Reference — Work at height and grating: technical references and scenarios",
     "ref": "CSM II-5 / 5.3.2 · 5.5 · II-4 / 4.5 · I-6 · CSAR 4.18 · 6.2 · 4.10 · 4.13 · 7.6 · 7.7",
     "brief": "Reference material for Module 3 — consult when needed. Covers: grating permit six-part structure, rescue plan requirements, fall protection specifications, floor opening coverage, daily checklist seven groups, and common work-at-height scenarios with answers.",
     "ref_only": True,
     "questions": [],
    },
  ]},
  {"seq": 4, "title": "Confined Spaces and Excavations",
   "title_ar": "الأماكن المحصورة والحفريات",
   "doors": [
    {"seq": 1, "title": "Orientation: confined spaces and excavations on site",
     "ref": "CSM I-6 / 6.3 · II-1 / 1.4 · 4.10.2",
     "brief": "Go with your supervisor to an active confined space or excavation area. Observe: entry permit posted, gas tester present or record visible, entry attendant at the entry point, shoring or sloping in place. Do not verify — observe and note only.",
     "questions": [
      "Write the confined space or excavation activities you saw and where the permit was posted.",
      "Was the entry attendant physically at the entry point? Was a gas test record visible? Write what you observed.",
      "Was shoring or sloping in place? What class was the confined space (if marked on the permit)?",
     ]},
    {"seq": 2, "title": "The complete confined space and excavation system",
     "ref": "CSM I-6 / 6.3 · 6.3.2 · II-1 / 1.4 · 4.10.3–4.10.5 · 4.10.9 · 4.10.10",
     "brief": "With your supervisor. Different confined space or excavation. Verify: classification (A/B/C), gas test current with four readings within limits, rescue plan site-specific, entry attendant in position, shoring or sloping in place, underground services marked.",
     "questions": [
      "What class is the confined space? Is it correctly classified? Write your evidence.",
      "Gas test record: attached and current? Write the four readings (O₂, LEL, H₂S, CO) and whether within acceptable limits.",
      "Rescue plan attached? Entry attendant physically present at the entry point? Write what you found.",
     ]},
    {"seq": 3, "title": "Independent verification at a different confined space or excavation",
     "ref": "CSM I-6 · II-1 · 4.10",
     "brief": "Fully independent. New confined space or excavation not visited before. Request the permit and all attachments. Apply the complete checklist and write at least one formal finding.",
     "questions": [
      "Classify the confined space (A, B or C) based on what you found — justify your classification.",
      "Verify gas readings: write the four values and confirm they are within acceptable limits.",
      "Write at least one formal finding: observation / reference / required action.",
     ]},
    {"seq": 4, "title": "Audit: formal findings on confined space and excavation permits",
     "ref": "CSM I-6 · II-1 · 4.10",
     "brief": "Field practical (3 hours). New confined space or excavation area. Apply the full audit checklist — classification, gas test, rescue plan, entry attendant, shoring/sloping, underground services. Write all findings in formal format.",
     "questions": [
      "Classification correct? Gas test current with all four values within limits? Write both findings.",
      "Rescue plan attached and site-specific? Entry attendant physically present at entry? Write your evidence.",
      "Shoring or sloping in place? Underground services located and marked before digging? Write findings.",
      "Write one formal finding from today's field audit.",
     ]},
    {"seq": 5, "title": "Evaluation: independent report at the confined space or excavation area",
     "ref": "CSM I-6 · II-1 · 4.10",
     "brief": "Work alone. Active confined space or excavation not visited before (3 hours field). Verify all six roles, classification, gas test, rescue plan A–G, entry attendant in position, shoring/sloping, underground services, daily inspection, SGL complete.",
     "questions": [
      "Verify gas readings at the time of your visit. Write the four values and compare to acceptable limits.",
      "Check shoring or sloping: in place? Competent person inspection signed today?",
      "Write a report: what was compliant, what was deficient, your recommendations.",
     ]},
    {"seq": 6, "title": "Reference — Confined spaces and excavations: technical references and scenarios",
     "ref": "CSM I-6 / 6.3 · 6.3.2 · II-1 / 1.4 · 4.10.3–4.10.5 · 4.10.9 · 4.10.10 · GI 2.709",
     "brief": "Reference material for Module 4 — consult when needed. Covers: three-class classification system (A/B/C), gas testing four parameters and limits, excavation shoring requirements (depth >1.2 m), competent person inspection requirements, and confined space scenarios with answers.",
     "ref_only": True,
     "questions": [],
    },
  ]},
  {"seq": 5, "title": "Lifting and Heavy Equipment",
   "title_ar": "الرفع والمعدات الثقيلة",
   "doors": [
    {"seq": 1, "title": "Orientation: lifting and heavy equipment on site",
     "ref": "CSM III-7 / 7.7 · III-2 · 4.6.3.B",
     "brief": "Go with your supervisor to an active lifting or heavy-equipment area. Observe: lifting permit posted, PWAS unit on equipment, rigger and operator, load chart on the crane, exclusion zone marked. Do not verify — observe and note only.",
     "questions": [
      "Write the lifting activities you saw and where the permit was posted or held.",
      "Was the PWAS unit visible and fitted to the equipment? Was the exclusion zone marked?",
      "Could you see the load chart on the crane? Write what you observed.",
     ]},
    {"seq": 2, "title": "The complete lifting and heavy equipment system",
     "ref": "CSM III-7 / 7.7 · III-2 · 4.6.3.B · 4.10.12 · CSAR 4.10 · 4.13",
     "brief": "With your supervisor. Different lifting area. Verify: lifting permit present and lift correctly classified (critical or not), certified rigger and operator, load chart on crane, exclusion zone established (max boom + 20%), PWAS functioning, rigging tags current, no personnel under load.",
     "questions": [
      "Is the lift correctly classified as critical or non-critical? Write your justification.",
      "PWAS: verify it is installed and functioning on the crane or excavator. Write the unit and status found.",
      "Rigging inspection tags: all items tagged with current colour code? Write each item — acceptable or rejected.",
     ]},
    {"seq": 3, "title": "Independent verification at a different lifting and equipment area",
     "ref": "CSM III-7 · III-2 · 4.10 · CSAR 4.10 · 4.13",
     "brief": "Fully independent. New lifting area not visited before. Request the permit and all attachments. Apply the complete lifting checklist and write at least one formal finding.",
     "questions": [
      "Exclusion zone: established and enforced? Estimate the radius — does it match the requirement (max boom + 20%)?",
      "Ground conditions and outrigger pads: assessed and on solid ground? Write what you found.",
      "Write at least one formal finding: observation / reference / required action.",
     ]},
    {"seq": 4, "title": "Audit: formal findings on lifting and heavy equipment permits",
     "ref": "CSM III-7 · III-2 · 4.10",
     "brief": "Field practical (3 hours). New lifting area. Apply the full audit checklist — permit, classification, certified rigger and operator, load chart, exclusion zone, PWAS, rigging tags, no personnel under load. Write all findings in formal format.",
     "questions": [
      "Lifting permit present? Is the lift correctly classified as critical or non-critical?",
      "Rigging tags: all items tagged with current colour code? Write each item — acceptable or rejected.",
      "Exclusion zone: established and enforced? Estimate the radius — does it match the requirement?",
      "Write one formal finding from today's field audit.",
     ]},
    {"seq": 5, "title": "Evaluation: independent report at the lifting and equipment area",
     "ref": "CSM III-7 · III-2 · 4.10",
     "brief": "Work alone. Active lifting or heavy-equipment area not visited before (3 hours field). Verify all six roles, permit classification, load chart, rigging inspection, exclusion zone, PWAS, ground assessment, outriggers, no personnel under load, SGL complete.",
     "questions": [
      "Verify PWAS on all heavy equipment in the lifting area. Write the unit number and whether functioning.",
      "Ground conditions and outrigger pads: on solid ground? Ground assessment available?",
      "Write a report: what was compliant, what was deficient, your recommendations.",
     ]},
    {"seq": 6, "title": "Reference — Lifting and heavy equipment: technical references and scenarios",
     "ref": "CSM III-7 / 7.7 · III-2 · 4.6.3.B · 4.10.12 · CSAR 4.10 · 4.13",
     "brief": "Reference material for Module 5 — consult when needed. Covers: critical lift definition (four conditions), exclusion zone calculation, PWAS mandatory requirement, rigging colour-code inspection system, rejection criteria for wire rope and synthetic slings, and lifting scenarios with answers.",
     "ref_only": True,
     "questions": [],
    },
  ]},
  {"seq": 6, "title": "Electrical Isolation, Pressure Testing and Radiography",
   "title_ar": "العزل الكهربائي واختبار الضغط والإشعاع",
   "doors": [
    {"seq": 1, "title": "Orientation: electrical isolation, pressure testing and radiography on site",
     "ref": "CSM I-5 / 5.5.14 · III-3 / 3.6.5 · III-4 / 4.3 · III-6 / 6.3.9",
     "brief": "Go with your supervisor to an active electrical isolation, pressure test or radiography area. Observe: isolation permit posted, locks and tags on isolation points, pressure gauge present, exclusion zone for radiography. Do not verify — observe and note only.",
     "questions": [
      "Write the type of permit (isolation, pressure test or radiography) and where it was posted.",
      "For electrical isolation: could you see locks and tags on each isolation point? How many isolation points were there?",
      "For pressure testing: was the exclusion zone marked? For radiography: was radiation area cordoned off?",
     ]},
    {"seq": 2, "title": "The complete electrical isolation, pressure testing and radiography system",
     "ref": "CSM I-5 / 5.5.14 · III-3 / 3.6.5 · III-4 / 4.3 · 4.3.1 · III-6 / 6.3.9 · III-9",
     "brief": "With your supervisor. Different isolation/pressure/radiography area. Verify: all isolation points locked and tagged, each worker has personal lock, zero energy verified, or pressure gauge calibrated with test pressure within limits, or exclusion zone established with radiation officer present.",
     "questions": [
      "Electrical isolation: count isolation points — is each locked and tagged with a personal lock? Write each point found.",
      "Pressure test: test pressure vs. design pressure — is the ratio within limits? Is the gauge calibrated?",
      "Radiography: who was present? Was the exclusion zone established and marked? Write what you found.",
     ]},
    {"seq": 3, "title": "Independent verification at a different isolation, pressure or radiation area",
     "ref": "CSM I-5 · III-3 · III-4 · III-6 · III-9",
     "brief": "Fully independent. New isolation/pressure/radiography area not visited before. Request the permit and all attachments. Apply the complete checklist and write at least one formal finding.",
     "questions": [
      "List all isolation points and their lock/tag status. Was zero energy verified at each point?",
      "Pressure test gauge: calibrated? What is the calibration expiry date? Is the test pressure within limits?",
      "Write at least one formal finding: observation / reference / required action.",
     ]},
    {"seq": 4, "title": "Audit: formal findings on electrical, pressure and radiation permits",
     "ref": "CSM I-5 · III-3 · III-4 · III-6 · III-9",
     "brief": "Field practical (3 hours). New isolation/pressure/radiography area. Apply the full audit checklist. Write all findings in formal format.",
     "questions": [
      "Energy isolation: count isolation points — is each locked and tagged? Write each point found.",
      "Pressure test gauge: calibrated? What is the calibration expiry date?",
      "Exclusion zone demarcated? Were any personnel inside the zone during the operation?",
      "Write one formal finding from today's field audit.",
     ]},
    {"seq": 5, "title": "Evaluation: independent report at the isolation, pressure or radiation area",
     "ref": "CSM I-5 · III-3 · III-4 · III-6",
     "brief": "Work alone. Active isolation/pressure/radiography area not visited before (3 hours field). Verify all six roles, all isolation points, personal locks, zero energy verification, pressure test permit, calibrated gauge, exclusion zone, SGL complete.",
     "questions": [
      "Verify zero energy at each isolation point. Write the verification method and reading at each point.",
      "Pressure test: test pressure vs. design pressure — is the ratio within limits?",
      "Write a report: what was compliant, what was deficient, your recommendations.",
     ]},
    {"seq": 6, "title": "Reference — Electrical isolation, pressure testing and radiography: technical references",
     "ref": "CSM I-5 / 5.5.14 · III-3 / 3.6.5 · III-4 / 4.3 · 4.3.1 · III-6 / 6.3.9 · III-9 · CSAR 10.4",
     "brief": "Reference material for Module 6 — consult when needed. Covers: LOTO five-step sequence, personal lock rule, zero energy verification, hydrostatic vs. pneumatic test pressure limits (1.5× and 1.1×), radiography exclusion zone calculation, dosimeter requirements, and scenarios with answers.",
     "ref_only": True,
     "questions": [],
    },
  ]},
  {"seq": 7, "title": "Permit Sections, Governance and Final Audit",
   "title_ar": "أقسام التصريح والحوكمة والتدقيق النهائي",
   "doors": [
    {"seq": 1, "title": "Orientation: the main permit on site",
     "ref": "CSM 4.6.3.A–B · 4.7.1–4.7.2 · 4.10.6 · 4.10.8",
     "brief": "Go with your supervisor and examine an active permit in full — all eleven sections. Note which sections are signed, what the approval period is, how Section 10 is filled, and whether Sections 8 and 9 have been used. Do not assess compliance — observe and note.",
     "questions": [
      "What is the maximum validity period shown on the permit (Section 5)? When does it expire?",
      "Section 10: who signed it and when? What field verification must precede the Section 10 signature?",
      "Observe Sections 8 and 9 — have they been used (suspension or daily revalidation)? Write what you found.",
     ]},
    {"seq": 2, "title": "Guided verification: critical rules in the main permit",
     "ref": "CSM 4.6.3.A–B · 4.7.1–4.7.5 · 4.10.3–4.10.5 · 4.10.6 · 4.10.8–4.10.10 · 4.10.11",
     "brief": "With your supervisor. Different permit. Verify renewal, suspension and cancellation compliance: Approver re-signed before expiry, Section 8 used when conditions changed, Section 11 completed at closure. Check endorsement matrix for Section 7.",
     "questions": [
      "When must a permit be suspended? List three conditions that require suspension.",
      "After suspension, what must happen before work restarts? Who verifies?",
      "Scenario: Section 7 blank on a confined space entry permit. Is this a finding? Justify with the endorsement matrix.",
     ]},
    {"seq": 3, "title": "Independent verification using the 15-deficiency list on a different permit",
     "ref": "All sections",
     "brief": "Fully independent. Different permit from a different area. Apply the 15-deficiency list from the manual on the full permit. Write formal findings for each deficiency found.",
     "questions": [
      "Describe deficiency No. 15 from the manual and its penalty tier.",
      "Apply the 15-deficiency list to the permit you reviewed — which deficiencies did you find?",
      "Write one formal finding in full format (Observation / Reference / Required action) for each deficiency found.",
     ]},
    {"seq": 4, "title": "Audit: 15-deficiency list on a multi-permit area",
     "ref": "All sections",
     "brief": "Field practical (3 hours). Audit two active permits of different types using the 15-deficiency list. Verify Sections 1–10, all supporting documents, all roles, all field controls. Write formal findings for each gap.",
     "questions": [
      "Audit two active permits of different types. For each: write the permit type, most significant finding, and formal finding text.",
      "Compare Section 10 signatures across the two permits — were both signed today after field verification?",
      "Write a summary finding report with recommendations.",
     ]},
    {"seq": 5, "title": "Evaluation: final comprehensive audit",
     "ref": "All sections — all modules",
     "brief": "Final assessment. Work alone. One active permit of your choice, full audit across all seven module areas. Write a complete formal finding report with references and present to your supervisor.",
     "questions": [
      "Select one active permit and conduct a complete audit covering all seven module areas. Write the permit type and location.",
      "Write all findings in formal format (Observation / Reference / Required action) — at least three findings.",
      "What was the most significant finding during this full training programme? Why does it matter?",
     ]},
    {"seq": 6, "title": "Reference — Permit sections and governance: technical references and scenarios",
     "ref": "CSM 4.6.3.A–B · 4.7.1–4.7.5 · 4.8.3 · 4.9 · 4.10.3–4.10.14 · 4.11 · CSAR 4.19 · 7.3.B · 7.7 · 7.9 · 8.2",
     "brief": "Reference material for Module 7 — consult when needed. Covers: all eleven permit sections and their signatories, endorsement matrix (Section 7), renewal vs. suspension vs. cancellation procedures, the 15-deficiency list with penalty tiers, governance roles and responsibilities.",
     "ref_only": True,
     "questions": [],
    },
  ]},
]
PTW_MOD_BY_SEQ = {m["seq"]: m for m in PTW_MODULES}


class PtwDoorSubmission(db.Model):
    __tablename__ = "ptw_door_submission"
    id            = db.Column(db.Integer, primary_key=True)
    officer_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    module_seq    = db.Column(db.Integer, nullable=False)
    door_seq      = db.Column(db.Integer, nullable=False)
    submitted_at  = db.Column(db.DateTime, default=datetime.utcnow)
    answers       = db.Column(db.Text, nullable=True)    # JSON list of answer strings
    photo_path    = db.Column(db.String(255), nullable=True)
    status        = db.Column(db.String(20), default="pending")  # pending | approved | rejected
    reviewer_id   = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    reviewed_at   = db.Column(db.DateTime, nullable=True)
    reviewer_note = db.Column(db.Text, nullable=True)


@app.route("/admin/safety-teams", methods=["GET", "POST"])
@admin_required
def admin_safety_teams():
    """Admin: assign officers to safety supervisors."""
    _c = cid()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "assign":
            sup_id = int(request.form.get("sup_id") or 0)
            off_id = int(request.form.get("off_id") or 0)
            if sup_id and off_id:
                exists = OfficerTeam.query.filter_by(supervisor_id=sup_id, officer_id=off_id).first()
                if not exists:
                    db.session.add(OfficerTeam(supervisor_id=sup_id, officer_id=off_id, company_id=_c))
                    db.session.commit()
                    flash("Officer assigned.", "success")
        elif action == "remove":
            tid = int(request.form.get("team_id") or 0)
            row = db.session.get(OfficerTeam, tid)
            if row:
                db.session.delete(row)
                db.session.commit()
                flash("Removed.", "success")
        return redirect(url_for("admin_safety_teams"))

    supervisors_q = User.query.filter(User.role == "safety_supervisor", User.is_active == True)
    if _c:
        supervisors_q = supervisors_q.filter(User.company_id == _c)
    supervisors = supervisors_q.order_by(User.name).all()

    officers_q = User.query.filter(User.role.in_(OFFICER_ROLES), User.is_active == True)
    if _c:
        officers_q = officers_q.filter(User.company_id == _c)
    all_officers = officers_q.order_by(User.role, User.name).all()

    teams = {}
    for sup in supervisors:
        rows = (db.session.query(OfficerTeam, User)
                .join(User, OfficerTeam.officer_id == User.id)
                .filter(OfficerTeam.supervisor_id == sup.id)
                .order_by(User.role, User.name).all())
        teams[sup.id] = rows

    assigned_ids = {row.OfficerTeam.officer_id
                    for sup in supervisors for row in teams[sup.id]}
    unassigned = [o for o in all_officers if o.id not in assigned_ids]

    return render_template("admin_safety_teams.html",
                           supervisors=supervisors, teams=teams,
                           all_officers=all_officers, unassigned=unassigned)


# ── Admin: Officers Report (multi-select) ────────────────────────────

@app.route("/admin/officers-report", methods=["GET", "POST"])
@admin_required
def admin_officers_report():
    _c = cid()
    today = datetime.now(RIYADH_TZ).date()

    officers_q = User.query.filter(User.role.in_(OFFICER_ROLES), User.is_active == True)
    if _c:
        officers_q = officers_q.filter(User.company_id == _c)
    all_officers = officers_q.order_by(User.role, User.name).all()

    rows = []
    date_from = date_to = None
    selected_ids = []

    if request.method == "POST":
        raw_from     = (request.form.get("date_from") or "").strip()
        raw_to       = (request.form.get("date_to")   or "").strip()
        selected_ids = [int(x) for x in request.form.getlist("officer_ids") if x.isdigit()]
        try:
            date_from = parse_date(raw_from)
            date_to   = parse_date(raw_to)
        except Exception:
            flash("Invalid dates.", "danger")
            return redirect(url_for("admin_officers_report"))

        if not selected_ids:
            selected_ids = [o.id for o in all_officers]

        for o in all_officers:
            if o.id not in selected_ids:
                continue
            if o.role == "safety_officer":
                submissions = HseCheckin.query.filter(
                    HseCheckin.officer_id == o.id,
                    HseCheckin.date.between(date_from, date_to)
                ).count()
                finds = (WlfFinding.query
                         .filter(WlfFinding.officer_id == o.id,
                                 WlfFinding.date.between(date_from, date_to))
                         .count()) if hasattr(WlfFinding, "date") else 0
                rows.append({"user": o, "role_label": "Safety Officer",
                             "submissions": submissions, "findings": finds,
                             "detail": f"{submissions} check-ins"})
            elif o.role == "safety_welfare":
                submissions = WlfLevelWork.query.filter(
                    WlfLevelWork.officer_id == o.id,
                    WlfLevelWork.date.between(date_from, date_to)
                ).count()
                finds = WlfFinding.query.filter(
                    WlfFinding.officer_id == o.id,
                    WlfFinding.date.between(date_from, date_to)
                ).count()
                rows.append({"user": o, "role_label": "Welfare Officer",
                             "submissions": submissions, "findings": finds,
                             "detail": f"{submissions} field submissions"})
            elif o.role == "environment_officer":
                submissions = EnvLevelWork.query.filter(
                    EnvLevelWork.officer_id == o.id,
                    EnvLevelWork.date.between(date_from, date_to)
                ).count()
                finds = WlfFinding.query.filter(
                    WlfFinding.officer_id == o.id,
                    WlfFinding.date.between(date_from, date_to)
                ).count()
                rows.append({"user": o, "role_label": "Environment Officer",
                             "submissions": submissions, "findings": finds,
                             "detail": f"{submissions} env submissions"})

    return render_template("admin_officers_report.html",
                           all_officers=all_officers, rows=rows,
                           date_from=date_from, date_to=date_to,
                           selected_ids=selected_ids, today=today)


# ----- Supervisor: Employees -----
@app.route("/employees", methods=["GET"])
@login_required
def employees():
    u = cur_user()
    if u.role not in ("supervisor", "admin"):
        _role_home = {
            "safety_welfare": "welfare_home",
            "environment_officer": "env_dashboard",
            "safety_officer": "hse_checkin",
            "safety_supervisor": "safety_supervisor_home",
            "safety_manager": "safety_manager_dashboard",
            "hr": "hr_inbox",
            "site_supervisor": "site_supervisors",
            "super_admin": "superadmin_dashboard",
        }
        dest = _role_home.get(u.role)
        if dest:
            return redirect(url_for(dest))
        abort(403)

    emps = (Employee.query
            .filter_by(user_id=u.id, is_active=True)
            .order_by(Employee.name)
            .all())
    return render_template("employees.html", user=u, employees=emps)


@app.route("/employee/<int:emp_id>/edit", methods=["GET", "POST"])
@login_required
def employee_edit(emp_id):
    """تعديل بيانات الموظف: الاسم، رقم الموظف (emp_number)، وتاريخ انتهاء
    البطاقة/الإقامة. يقدر يعدّل: المشرف (لموظفيه هو بس) أو الأدمن (أي موظف)."""
    u = cur_user()
    if u.role not in ("supervisor", "admin"):
        abort(403)

    emp = db.session.get(Employee, emp_id)
    if not emp:
        abort(404)
    if u.role == "supervisor" and emp.user_id != u.id:
        abort(403)

    # الصفحة التي نرجع لها بعد الحفظ — نفس الصفحة اللي جاء منها المستخدم
    back_url = url_for("admin_employees") if u.role == "admin" else url_for("employees")

    if request.method == "POST":
        new_name   = (request.form.get("name") or "").strip()
        new_number = (request.form.get("emp_number") or "").strip()
        expiry_str = (request.form.get("id_expiry_date") or "").strip()

        if not new_name:
            flash("Employee name is required.", "danger")
            return redirect(url_for("employee_edit", emp_id=emp_id))
        if not new_number:
            flash("Employee ID is required.", "danger")
            return redirect(url_for("employee_edit", emp_id=emp_id))

        expiry_date = None
        if expiry_str:
            try:
                expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            except Exception:
                flash("Invalid expiry date format.", "danger")
                return redirect(url_for("employee_edit", emp_id=emp_id))

        emp.name = new_name
        emp.emp_number = new_number
        emp.id_expiry_date = expiry_date

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash(f"Employee ID ({new_number}) is already used by another employee.", "danger")
            return redirect(url_for("employee_edit", emp_id=emp_id))

        flash("Employee details updated successfully.", "success")
        return redirect(back_url)

    return render_template("employee_edit.html", emp=emp, back_url=back_url)



# ----- New Evaluation (employee) -----
@app.route("/evaluate/<int:emp_id>/new", methods=["GET", "POST"])
@login_required
def evaluate_new(emp_id):
    u = cur_user()

    if not u:
        abort(403)

    # admin و site_supervisor يشوفون أي موظف، supervisor العادي موظفيه فقط
    if u.role in ("admin", "site_supervisor"):
        emp = Employee.query.get_or_404(emp_id)
    else:
        emp = Employee.query.filter_by(id=emp_id, user_id=u.id).first_or_404()

    # POST = حفظ (إنشاء أو تعديل)
    if request.method == "POST":
        week_start_raw = request.form.get("week_start")
        week_end_raw   = request.form.get("week_end")

        if not week_start_raw or not week_end_raw:
            flash("Week dates are required.", "danger")
            return redirect(url_for("evaluate_new", emp_id=emp.id))

        ws = parse_date(week_start_raw)
        we = parse_date(week_end_raw)

        # تحقّق أن الأسبوع من الأحد إلى الخميس (نفس ما كنت تستخدم)
        ok, msg = validate_week_sun_to_thu(ws, we)
        if not ok:
            flash(msg, "danger")
            return redirect(url_for("evaluate_new", emp_id=emp.id))

        # 🔴 هنا الفكرة المهمّة:
        # إذا في تقييم لنفس الموظف ونفس الأسبوع → استخدمه وحدثه
        # إذا مافي → أنشئ واحد جديد
        ev = (Evaluation.query
              .filter_by(employee_id=emp.id, week_start=ws, week_end=we)
              .first())

        if ev is None:
            ev = Evaluation(
                employee_id=emp.id,
                evaluator_id=u.id,
                week_start=ws,
                week_end=we,
                company_id=cid(),
            )
            db.session.add(ev)
        else:
            # لو حاب تعتبر أن آخر من عدّل هو المقيم الحالي
            ev.evaluator_id = u.id

        # 1) Weekly Targets
        ev.t1_text     = request.form.get("t1_text") or ""
        ev.t2_text     = request.form.get("t2_text") or ""
        ev.t3_text     = request.form.get("t3_text") or ""
        ev.t4_text     = request.form.get("t4_text") or ""

        ev.t1_percent  = request.form.get("t1_percent", type=float)
        ev.t2_percent  = request.form.get("t2_percent", type=float)
        ev.t3_percent  = request.form.get("t3_percent", type=float)
        ev.t4_percent  = request.form.get("t4_percent", type=float)

        ev.t1_remarks  = request.form.get("t1_remarks") or ""
        ev.t2_remarks  = request.form.get("t2_remarks") or ""
        ev.t3_remarks  = request.form.get("t3_remarks") or ""
        ev.t4_remarks  = request.form.get("t4_remarks") or ""

        # 2) Performance ratings
        ev.p_punctuality    = request.form.get("p_punctuality", type=int)
        ev.p_quality        = request.form.get("p_quality", type=int)
        ev.p_productivity   = request.form.get("p_productivity", type=int)
        ev.p_communication  = request.form.get("p_communication", type=int)
        ev.p_problemsolving = request.form.get("p_problemsolving", type=int)
        ev.p_compliance     = request.form.get("p_compliance", type=int)

        # 3) Performance comments
        ev.c_punctuality    = request.form.get("c_punctuality") or ""
        ev.c_quality        = request.form.get("c_quality") or ""
        ev.c_productivity   = request.form.get("c_productivity") or ""
        ev.c_communication  = request.form.get("c_communication") or ""
        ev.c_problemsolving = request.form.get("c_problemsolving") or ""
        ev.c_compliance     = request.form.get("c_compliance") or ""

        # 4) Summary
        ev.strengths        = request.form.get("strengths") or ""
        ev.improvements     = request.form.get("improvements") or ""
        ev.training_needed  = request.form.get("training_needed") or ""

        # إعادة حساب الدرجات (نفس الفنكشن اللي تستخدمه أصلًا)
        compute_scores(ev)

        db.session.commit()
        flash("Weekly evaluation saved.", "success")

        # بعد الحفظ، افتح تقرير الموظف لهذا الأسبوع
        return redirect(url_for(
            "report_employee",
            emp_id=emp.id,
            week_start=ws.isoformat(),
            week_end=we.isoformat()
        ))

    # GET = فتح النموذج لأول مرة
    # نفس منطقك القديم: ws / we يتم حسابها أو أخذها من الكويري
    week_start = request.args.get("week_start")
    week_end   = request.args.get("week_end")

    if week_start and week_end:
        ws = parse_date(week_start)
        we = parse_date(week_end)
    else:
        # لو ما جا شيء، استخدم الأسبوع الحالي (عدّلها لو عندك دالة خاصة)
        today = date.today()
        # مثال بسيط: نخلي ws = الأحد، we = الخميس لنفس الأسبوع
        # weekday(): الاثنين=0 ... الأحد=6
        offset_to_sun = (today.weekday() + 1) % 7  # يخلي الأحد = 0
        ws = today - timedelta(days=offset_to_sun)
        we = ws + timedelta(days=4)

    return render_template(
        "eval_form.html",
        employee=emp,
        ws=ws,
        we=we,
        weights=WEIGHTS,
        ev=None  # مهم: وضع إنشاء جديد
    )


@app.route("/evaluate/<int:ev_id>/edit", methods=["GET", "POST"])
@login_required
def evaluate_edit(ev_id):
    u = cur_user()
    ev = Evaluation.query.get_or_404(ev_id)
    emp = db.session.get(Employee, ev.employee_id)

    if not emp:
        abort(404)

    # نفس منطق الصلاحيات في report_employee
    if u.role != "admin" and emp.user_id != u.id:
        abort(403)

    if request.method == "POST":
        try:
            week_start = parse_date(request.form.get("week_start"))
            week_end   = parse_date(request.form.get("week_end"))

            ok, msg = validate_week_sun_to_thu(week_start, week_end)
            if not ok:
                flash(msg, "danger")
                return redirect(url_for("evaluate_edit", ev_id=ev.id))

            # منع تكرار نفس الأسبوع لنفس الموظف (ما عدا هذا السجل)
            dup = (Evaluation.query
                   .filter(
                       Evaluation.employee_id == emp.id,
                       Evaluation.week_start == week_start,
                       Evaluation.week_end == week_end,
                       Evaluation.id != ev.id
                   )
                   .first())
            if dup:
                flash("An evaluation for this week already exists.", "warning")
                return redirect(url_for(
                    "report_employee",
                    emp_id=emp.id,
                    week_start=week_start.isoformat(),
                    week_end=week_end.isoformat()
                ))

            # تحديث الحقول
            ev.week_start = week_start
            ev.week_end   = week_end

            # 1) Targets
            ev.t1_text     = request.form.get("t1_text") or ""
            ev.t2_text     = request.form.get("t2_text") or ""
            ev.t3_text     = request.form.get("t3_text") or ""
            ev.t4_text     = request.form.get("t4_text") or ""
            ev.t1_percent  = request.form.get("t1_percent", type=float)
            ev.t2_percent  = request.form.get("t2_percent", type=float)
            ev.t3_percent  = request.form.get("t3_percent", type=float)
            ev.t4_percent  = request.form.get("t4_percent", type=float)
            ev.t1_remarks  = request.form.get("t1_remarks") or ""
            ev.t2_remarks  = request.form.get("t2_remarks") or ""
            ev.t3_remarks  = request.form.get("t3_remarks") or ""
            ev.t4_remarks  = request.form.get("t4_remarks") or ""

            # 2) Performance ratings
            ev.p_punctuality   = request.form.get("p_punctuality", type=int)
            ev.p_quality       = request.form.get("p_quality", type=int)
            ev.p_productivity  = request.form.get("p_productivity", type=int)
            ev.p_communication = request.form.get("p_communication", type=int)
            ev.p_problemsolving = request.form.get("p_problemsolving", type=int)
            ev.p_compliance    = request.form.get("p_compliance", type=int)

            # 2) Performance comments
            ev.c_punctuality   = request.form.get("c_punctuality") or ""
            ev.c_quality       = request.form.get("c_quality") or ""
            ev.c_productivity  = request.form.get("c_productivity") or ""
            ev.c_communication = request.form.get("c_communication") or ""
            ev.c_problemsolving = request.form.get("c_problemsolving") or ""
            ev.c_compliance    = request.form.get("c_compliance") or ""

            # 3) Summary
            ev.strengths       = request.form.get("strengths") or ""
            ev.improvements    = request.form.get("improvements") or ""
            ev.training_needed = request.form.get("training_needed") or ""

            # إعادة حساب الدرجات
            compute_scores(ev)
            db.session.commit()

            flash("Evaluation updated.", "success")
            return redirect(url_for(
                "report_employee",
                emp_id=emp.id,
                week_start=week_start.isoformat(),
                week_end=week_end.isoformat()
            ))
        except Exception:
            db.session.rollback()
            flash("Error while updating evaluation.", "danger")
            return redirect(url_for("evaluate_edit", ev_id=ev.id))

    # GET → افتح نفس نموذج التقييم لكن مع تعبئة البيانات
    ws = ev.week_start
    we = ev.week_end
    return render_template("eval_form.html", employee=emp, ws=ws, we=we, weights=WEIGHTS, ev=ev)


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
    elif u.role == "site_supervisor":
        # ضمن نطاقه الآن أو كان ضمنه سابقاً (حسب سجل الحركة)
        emp = Employee.query.get_or_404(emp_id)
        scope = _site_scope_sup_ids(u)
        allowed = emp.user_id in scope
        if not allowed:
            allowed = db.session.query(EmployeeAssignmentLog.id).filter(
                EmployeeAssignmentLog.employee_id == emp.id,
                or_(EmployeeAssignmentLog.from_user_id.in_(scope),
                    EmployeeAssignmentLog.to_user_id.in_(scope))).first() is not None
        if not allowed:
            abort(403)
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
    return redirect(url_for("admin_reports_weekly",
                                week_start=ws.isoformat(),
                                week_end=we.isoformat()))

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

    if cid():
        query = query.filter(Employee.company_id == cid())

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
    supervisors = (
        apply_company_filter(User.query.filter_by(role="supervisor", is_active=True), User)
        .order_by(User.name.asc(), User.supervisor_code.asc())
        .all()
    )

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

@app.route("/admin/reports/weekly")
@admin_required
def admin_reports_weekly():
    week_start = request.args.get("week_start")
    week_end   = request.args.get("week_end")
    q = (request.args.get("q") or "").strip()

    # لو ما فيه تاريخ نرجع لصفحة الاختيار
    if not (week_start and week_end):
        return redirect(url_for("admin_report_picker"))

    ws = parse_date(week_start)
    we = parse_date(week_end)

    # نحاول نحدد سوبرفايزر من البحث (ID أو اسم)
    focus_sup = find_supervisor_from_query(q)

    # الاستعلام الأساسي: التقييمات الأسبوعية
    query = (db.session.query(Evaluation, Employee, User)
             .join(Employee, Evaluation.employee_id == Employee.id)
             .join(User, Employee.user_id == User.id)
             .filter(Evaluation.week_start == ws,
                     Evaluation.week_end == we))

    # لو حددنا سوبرفايزر نفلتر عليه
    if focus_sup:
        query = query.filter(Employee.user_id == focus_sup.id)

    # لو فيه بحث عام وما لقينا سوبرفايزر
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

    # KPI لو كان فيه سوبرفايزر محدد
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

    # نجهز قائمة السوبرفايزر عشان الـ datalist
    supervisors = (
        apply_company_filter(User.query.filter_by(role="supervisor", is_active=True), User)
        .order_by(User.name.asc(), User.supervisor_code.asc())
        .all()
    )
    sup_suggestions = []
    seen = set()
    for s in supervisors:
        key = f"ID:{s.supervisor_code}"
        sup_suggestions.append((s.supervisor_code, f"{s.name or '—'} — ID: {s.supervisor_code}"))
        seen.add(key)
    for s in supervisors:
        nm = (s.name or "").strip()
        if not nm:
            continue
        key = nm.lower()
        if key in seen:
            continue
        sup_suggestions.append((nm, f"{nm} — ID: {s.supervisor_code}"))
        seen.add(key)

    return render_template(
        "report_admin_weekly.html",
        ws=ws, we=we,
        rows=rows,
        q=q,
        focus_sup=focus_sup,
        sup_cov_pct=sup_cov_pct,
        total_emp=total_emp,
        done_emp_count=done_emp_count,
        missing_emps=missing_emps,
        sup_suggestions=sup_suggestions,
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
    q      = (request.args.get("q") or "").strip()
    flt    = (request.args.get("filter") or "").strip()   # unassigned | deactivated

    if flt == "unassigned":
        # موظفون بدون مشرف
        emp_q = apply_company_filter(
            Employee.query.filter(Employee.user_id.is_(None)), Employee)
        if q:
            like = f"%{q}%"
            emp_q = emp_q.filter(or_(
                Employee.name.ilike(like),
                Employee.emp_number.ilike(like),
                Employee.department.ilike(like),
                Employee.site.ilike(like),
            ))
        emp_rows = [(emp, None) for emp in emp_q.order_by(Employee.name.asc()).all()]

    elif flt == "deactivated":
        # موظفون حذفهم مشرفهم (is_active=False, لديهم مشرف)
        emp_q = (db.session.query(Employee, User)
                 .join(User, Employee.user_id == User.id)
                 .filter(Employee.is_active == False))
        if cid():
            emp_q = emp_q.filter(Employee.company_id == cid())
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

    else:
        # الكل (نشطون فقط مع مشرف)
        emp_q = (db.session.query(Employee, User)
                 .join(User, Employee.user_id == User.id)
                 .filter(Employee.is_active == True))
        if cid():
            emp_q = emp_q.filter(Employee.company_id == cid())
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

    # عدد كل فئة
    _base = apply_company_filter(Employee.query, Employee)
    count_unassigned  = _base.filter(Employee.user_id.is_(None)).count()
    count_deactivated = _base.filter(Employee.is_active == False, Employee.user_id.isnot(None)).count()

    users_q = apply_company_filter(
        User.query.filter(User.role.in_(["supervisor", "site_supervisor"])), User)
    if q:
        like = f"%{q}%"
        users_q = users_q.filter(or_(
            User.supervisor_code.ilike(like),
            User.name.ilike(like),
            User.role.ilike(like),
        ))
    users = users_q.order_by(User.supervisor_code.asc()).all()

    return render_template("admin_employees.html", rows=emp_rows, users=users,
                           q=q, flt=flt,
                           count_unassigned=count_unassigned,
                           count_deactivated=count_deactivated)

@app.route("/admin/employee/<int:emp_id>/history")
@admin_required
def admin_employee_history(emp_id):
    emp = Employee.query.get_or_404(emp_id)
    evals = (Evaluation.query.filter_by(employee_id=emp.id)
             .order_by(Evaluation.week_start.desc()).all())
    avg_score = round(sum((e.total_score or 0) for e in evals) / len(evals), 1) if evals else None
    return render_template("employee_history.html", emp=emp, evals=evals, avg_score=avg_score)

# ----- Employee full report history (supervisor: own employees, site_supervisor: employees of
#       supervisors they oversee (read-only), admin: any) -----
@app.route("/reports/employee/<int:emp_id>/all")
@login_required
def employee_reports_all(emp_id):
    u = cur_user()
    if u.role == "admin":
        emp = Employee.query.get_or_404(emp_id)
    elif u.role == "site_supervisor":
        emp = Employee.query.get_or_404(emp_id)
        link = SiteSupervisorMap.query.filter_by(site_sup_id=u.id, supervisor_id=emp.user_id).first()
        if not link:
            abort(403)
    else:
        emp = Employee.query.filter_by(id=emp_id, user_id=u.id).first_or_404()

    evals = (Evaluation.query.filter_by(employee_id=emp.id)
             .order_by(Evaluation.week_start.desc()).all())
    avg_score = round(sum((e.total_score or 0) for e in evals) / len(evals), 1) if evals else None
    back_url = url_for("site_employee_requests") if u.role == "site_supervisor" else url_for("employees")
    return render_template("employee_history.html", emp=emp, evals=evals, avg_score=avg_score,
                           back_url=back_url)

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
                is_active=True,
                company_id=cid(),
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
            link = SiteSupervisorMap(site_sup_id=u.id, supervisor_id=sup_user.id, company_id=cid())
            db.session.add(link)
            db.session.commit()
            flash("Supervisor assigned.", "success")

        return redirect(url_for("site_supervisors"))

    links = (db.session.query(SiteSupervisorMap, User)
             .join(User, SiteSupervisorMap.supervisor_id == User.id)
             .filter(SiteSupervisorMap.site_sup_id == u.id).all())

    # ── KPIs + نسبة الإنجاز لكل مشرف (أي أسبوع) ──
    cur_ws, cur_we = default_week_today()
    ws = _safe_date(request.args.get("ws")) or cur_ws
    we = _safe_date(request.args.get("we")) or (ws + timedelta(days=4))
    prev_ws = ws - timedelta(days=7)
    next_ws = ws + timedelta(days=7)
    is_current_week = (ws, we) == (cur_ws, cur_we)

    sup_ids = [sup.id for _, sup in links]
    emp_totals_map, emp_done_map = _week_scope(sup_ids, ws, we, is_current_week)

    sup_rows = []
    for link, sup in links:
        target = emp_totals_map.get(sup.id, 0)
        done_c = emp_done_map.get(sup.id, 0)
        coverage = round(done_c / target * 100.0, 0) if target else None
        sup_rows.append({
            "link_id": link.id, "id": sup.id, "code": sup.supervisor_code,
            "name": sup.name, "target": target, "done_c": done_c,
            "coverage": coverage,
        })

    kpi_total_sups   = len(links)
    kpi_evaluated    = sum(1 for r in sup_rows if r["target"] > 0 and r["done_c"] >= r["target"])
    kpi_total_emps   = sum(r["target"] for r in sup_rows)
    kpi_done_emps    = sum(r["done_c"] for r in sup_rows)

    return render_template("site_supervisors.html", links=links, sup_rows=sup_rows,
                           kpi_total_sups=kpi_total_sups, kpi_evaluated=kpi_evaluated,
                           kpi_total_emps=kpi_total_emps, kpi_done_emps=kpi_done_emps,
                           ws=ws, we=we, prev_ws=prev_ws, next_ws=next_ws,
                           is_current_week=is_current_week)

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


@app.get("/site/supervisor/<int:sup_id>/detail")
@login_required
def site_supervisor_detail(sup_id):
    u = cur_user()
    if u.role != "site_supervisor":
        abort(403)

    link = SiteSupervisorMap.query.filter_by(site_sup_id=u.id, supervisor_id=sup_id).first()
    if not link:
        abort(403)
    sup = User.query.filter_by(id=sup_id, role="supervisor").first_or_404()

    # صلاحية استعراض أسابيع سابقة: نفس نمط ws/we المستخدم بباقي صفحات التقارير
    ws = _safe_date(request.args.get("ws")) or default_week_today()[0]
    we = _safe_date(request.args.get("we")) or (ws + timedelta(days=4))
    prev_ws = ws - timedelta(days=7)
    next_ws = ws + timedelta(days=7)
    is_current_week = (ws, we) == default_week_today()

    # الأسبوع الحالي → القائمة الحيّة. أسبوع ماضٍ → القائمة كما كانت وقتها.
    if is_current_week:
        employees = (Employee.query.filter_by(user_id=sup.id, is_active=True)
                     .order_by(Employee.name.asc()).all())
    else:
        hist_ids = _roster_at(sup.id, we)
        employees = (Employee.query.filter(Employee.id.in_(hist_ids))
                     .order_by(Employee.name.asc()).all()) if hist_ids else []
    emp_ids = [e.id for e in employees]

    # التقييمات تُنسب لمن قيّم فعلاً — لا تنتقل مع الموظف عند نقله
    evaluated_ids = set()
    if emp_ids:
        evaluated_ids = {row[0] for row in
                        db.session.query(Evaluation.employee_id)
                        .filter(Evaluation.employee_id.in_(emp_ids),
                                Evaluation.evaluator_id == sup.id,
                                Evaluation.week_start == ws, Evaluation.week_end == we)
                        .all()}

    avg_sc = (db.session.query(func.avg(Evaluation.total_score))
              .filter(Evaluation.evaluator_id == sup.id,
                      Evaluation.week_start == ws, Evaluation.week_end == we)
              .scalar())

    # غير المكتملين أولاً، ثم المكتملين — كلاهما مرتب بالاسم
    not_done = [e for e in employees if e.id not in evaluated_ids]
    done     = [e for e in employees if e.id in evaluated_ids]

    kpi_target   = len(employees)
    kpi_done     = len(done)
    kpi_coverage = round(kpi_done / kpi_target * 100.0, 0) if kpi_target else None

    return render_template("site_supervisor_detail.html", sup=sup,
                           not_done=not_done, done=done,
                           kpi_target=kpi_target, kpi_done=kpi_done,
                           kpi_coverage=kpi_coverage,
                           avg_score=round(float(avg_sc), 1) if avg_sc else None,
                           ws=ws, we=we, prev_ws=prev_ws, next_ws=next_ws,
                           is_current_week=is_current_week)


# ─────────────────────────────────────────────
#  Site Supervisor → إدارة موظفي المشرفين (نقل / فصل / استقالة)
# ─────────────────────────────────────────────

def _site_scope_sup_ids(u):
    """أرقام المشرفين التابعين لهذا الـ site_supervisor."""
    return [row[0] for row in
            db.session.query(SiteSupervisorMap.supervisor_id)
            .filter(SiteSupervisorMap.site_sup_id == u.id).all()]


def _site_scope_supervisors(u):
    """كائنات المشرفين التابعين لهذا الـ site_supervisor مرتبة بالاسم."""
    return (db.session.query(User)
            .join(SiteSupervisorMap, SiteSupervisorMap.supervisor_id == User.id)
            .filter(SiteSupervisorMap.site_sup_id == u.id)
            .order_by(User.name.asc()).all())


def _roster_at(sup_id, on_date):
    """موظفو المشرف كما كانوا في تاريخ محدد — يعيد بناء القائمة من سجل الحركة.

    القاعدة: مالك الموظف في تاريخ ما = from_user_id لأول حركة سُجّلت بعد ذلك
    التاريخ. إن لم توجد حركة بعده، فالمالك الحالي هو نفسه المالك وقتها.
    (تتعامل بشكل صحيح مع النقل المتعدد أ→ب→ج، لأنها تحسب المالك لا الطرفين.)

    ملاحظة: السجل يبدأ من تاريخ تفعيل الميزة — لأي أسبوع أقدم يرجع المالك الحالي.
    """
    # created_at مخزّن naive في MySQL — قارن بـ naive
    cutoff = datetime.combine(on_date, datetime.max.time())

    # المرشحون: من هم عنده الآن + كل من ظهر في سجله (دخولاً أو خروجاً)
    cand = {e.id for e in Employee.query.filter_by(user_id=sup_id).all()}
    linked = (EmployeeAssignmentLog.query
              .filter(or_(EmployeeAssignmentLog.from_user_id == sup_id,
                          EmployeeAssignmentLog.to_user_id == sup_id)).all())
    cand |= {m.employee_id for m in linked}
    if not cand:
        return set()

    # أول حركة بعد التاريخ لكل موظف مرشح
    rows = (EmployeeAssignmentLog.query
            .filter(EmployeeAssignmentLog.employee_id.in_(cand),
                    EmployeeAssignmentLog.created_at > cutoff)
            .order_by(EmployeeAssignmentLog.created_at.asc()).all())
    first_after = {}
    for m in rows:
        first_after.setdefault(m.employee_id, m)

    # المالك الحالي لكل مرشح
    cur_owner = dict(
        db.session.query(Employee.id, Employee.user_id)
        .filter(Employee.id.in_(cand)).all())

    out = set()
    for e_id in cand:
        owner = (first_after[e_id].from_user_id if e_id in first_after
                 else cur_owner.get(e_id))
        if owner == sup_id:
            out.add(e_id)
    return out


def _roster_at_bulk(sup_ids, on_date):
    """نفس منطق _roster_at لكن لعدة مشرفين دفعة واحدة — 3 استعلامات بدل 3 لكل مشرف.

    يعيد dict: {sup_id: set(employee_ids)}
    """
    out = {sid: set() for sid in sup_ids}
    if not sup_ids:
        return out

    cutoff = datetime.combine(on_date, datetime.max.time())

    # (1) المالك الحالي لكل موظف يخص أياً من هؤلاء المشرفين
    cur_owner = dict(
        db.session.query(Employee.id, Employee.user_id)
        .filter(Employee.user_id.in_(sup_ids)).all())

    # (2) كل من ظهر في سجل الحركة مرتبطاً بأحدهم
    linked = (EmployeeAssignmentLog.query
              .filter(or_(EmployeeAssignmentLog.from_user_id.in_(sup_ids),
                          EmployeeAssignmentLog.to_user_id.in_(sup_ids))).all())
    cand = set(cur_owner.keys()) | {m.employee_id for m in linked}
    if not cand:
        return out

    # مالكهم الحالي (قد يكون موظف غادر نطاق هؤلاء المشرفين)
    missing = cand - set(cur_owner.keys())
    if missing:
        cur_owner.update(dict(
            db.session.query(Employee.id, Employee.user_id)
            .filter(Employee.id.in_(missing)).all()))

    # (3) أول حركة بعد التاريخ لكل موظف مرشح
    rows = (EmployeeAssignmentLog.query
            .filter(EmployeeAssignmentLog.employee_id.in_(cand),
                    EmployeeAssignmentLog.created_at > cutoff)
            .order_by(EmployeeAssignmentLog.created_at.asc()).all())
    first_after = {}
    for m in rows:
        first_after.setdefault(m.employee_id, m)

    for e_id in cand:
        owner = (first_after[e_id].from_user_id if e_id in first_after
                 else cur_owner.get(e_id))
        if owner in out:
            out[owner].add(e_id)
    return out


def _week_scope(u_sup_ids, ws, we, is_current):
    """يعيد (target_map, done_map) لكل مشرف في أسبوع محدد.

    done: يُنسب لمن قيّم فعلاً (evaluator_id) — لا يتأثر بنقل الموظف لاحقاً.
    target: الأسبوع الحالي → القائمة الحيّة، الأسابيع الماضية → من سجل الحركة.
    """
    if not u_sup_ids:
        return {}, {}

    done_map = dict(
        db.session.query(Evaluation.evaluator_id, func.count(Evaluation.id))
        .filter(Evaluation.evaluator_id.in_(u_sup_ids),
                Evaluation.week_start == ws, Evaluation.week_end == we)
        .group_by(Evaluation.evaluator_id).all())

    if is_current:
        target_map = dict(
            db.session.query(Employee.user_id, func.count(Employee.id))
            .filter(Employee.user_id.in_(u_sup_ids), Employee.is_active == True)
            .group_by(Employee.user_id).all())
    else:
        rosters = _roster_at_bulk(u_sup_ids, we)
        target_map = {sid: len(ids) for sid, ids in rosters.items()}

    return target_map, done_map


def _log_emp_assignment(emp, action, actor, from_user_id=None, to_user_id=None, note=""):
    """تسجيل أي حركة على الموظف — لا حذف نهائي، كل شيء يُسجَّل."""
    try:
        db.session.add(EmployeeAssignmentLog(
            employee_id=emp.id,
            from_user_id=from_user_id,
            to_user_id=to_user_id,
            action=action,
            actor_id=getattr(actor, "id", None),
            note=(note or "")[:255],
            company_id=getattr(emp, "company_id", None) or cid(),
        ))
    except Exception as _e:
        app.logger.error("assignment log failed: %s", _e)


def _site_guard_emp(u, emp):
    """يتأكد أن الموظف تابع لأحد مشرفي هذا الـ site_supervisor."""
    if u.role == "admin":
        return True
    return emp.user_id in _site_scope_sup_ids(u)


@app.route("/site/employees/manage", methods=["GET"])
@login_required
def site_manage_employees():
    """اختيار مشرف تابع → عرض موظفيه مع أزرار النقل / الفصل / الاستقالة."""
    u = cur_user()
    if u.role not in ("site_supervisor", "admin"):
        abort(403)

    my_supervisors = _site_scope_supervisors(u) if u.role == "site_supervisor" else \
        apply_company_filter(User.query.filter_by(role="supervisor", is_active=True), User).order_by(User.name.asc()).all()

    sup_id = request.args.get("sup_id", type=int)
    show   = (request.args.get("show") or "active").strip()   # active | resigned | all
    sel_sup, employees, logs = None, [], []

    if sup_id:
        allowed_ids = [s.id for s in my_supervisors]
        if sup_id not in allowed_ids:
            abort(403)
        sel_sup = User.query.get_or_404(sup_id)

        q = Employee.query.filter_by(user_id=sup_id)
        if show == "active":
            q = q.filter(Employee.status == "active", Employee.is_active == True)
        elif show == "resigned":
            q = q.filter(Employee.status == "resigned")
        employees = q.order_by(Employee.name.asc()).all()

        emp_ids = [e.id for e in employees]
        if emp_ids:
            logs = (db.session.query(EmployeeAssignmentLog)
                    .filter(EmployeeAssignmentLog.employee_id.in_(emp_ids))
                    .order_by(EmployeeAssignmentLog.created_at.desc())
                    .limit(30).all())

    # خريطة أسماء المستخدمين لعرض السجل
    uid_set = set()
    for lg in logs:
        uid_set.update([lg.from_user_id, lg.to_user_id, lg.actor_id])
    uid_set.discard(None)
    name_map = {}
    if uid_set:
        name_map = {x.id: (x.name or x.supervisor_code)
                    for x in User.query.filter(User.id.in_(list(uid_set))).all()}

    # عدد موظفي كل مشرف (لعرضه في القائمة المنسدلة)
    counts = {}
    all_ids = [s.id for s in my_supervisors]
    if all_ids:
        counts = dict(
            db.session.query(Employee.user_id, func.count(Employee.id))
            .filter(Employee.user_id.in_(all_ids),
                    Employee.status == "active", Employee.is_active == True)
            .group_by(Employee.user_id).all())

    return render_template("site_manage_employees.html",
                           my_supervisors=my_supervisors, sel_sup=sel_sup,
                           employees=employees, logs=logs, name_map=name_map,
                           counts=counts, show=show)


@app.post("/site/employees/<int:emp_id>/transfer")
@login_required
def site_employee_transfer(emp_id):
    """نقل موظف من مشرف إلى مشرف آخر ضمن نطاق الـ site_supervisor."""
    u = cur_user()
    if u.role not in ("site_supervisor", "admin"):
        abort(403)

    emp = Employee.query.get_or_404(emp_id)
    if not _site_guard_emp(u, emp):
        abort(403)

    target_id = request.form.get("target_sup_id", type=int)
    back      = request.form.get("back") or url_for("site_manage_employees", sup_id=emp.user_id)

    if not target_id:
        flash("Select the receiving supervisor first.", "danger")
        return redirect(back)
    if target_id == emp.user_id:
        flash("This employee is already under that supervisor.", "warning")
        return redirect(back)

    if u.role == "site_supervisor" and target_id not in _site_scope_sup_ids(u):
        flash("The receiving supervisor is not within your scope.", "danger")
        return redirect(back)

    target = User.query.get(target_id)
    if not target or target.role != "supervisor":
        flash("Invalid receiving supervisor.", "danger")
        return redirect(back)

    old_id = emp.user_id
    emp.user_id   = target_id
    emp.status    = "active"
    emp.is_active = True
    _log_emp_assignment(emp, "transfer", u, from_user_id=old_id, to_user_id=target_id,
                        note=request.form.get("note", ""))
    db.session.commit()
    flash(f"{emp.name} transferred to {target.name or target.supervisor_code}.", "success")
    return redirect(url_for("site_manage_employees", sup_id=old_id))


@app.post("/site/employees/bulk-transfer")
@login_required
def site_employees_bulk_transfer():
    """نقل عدة موظفين دفعة واحدة."""
    u = cur_user()
    if u.role not in ("site_supervisor", "admin"):
        abort(403)

    emp_ids   = request.form.getlist("emp_ids", type=int)
    target_id = request.form.get("target_sup_id", type=int)
    from_id   = request.form.get("from_sup_id", type=int)

    if not emp_ids:
        flash("No employee selected.", "warning")
        return redirect(url_for("site_manage_employees", sup_id=from_id))
    if not target_id:
        flash("Select the receiving supervisor.", "danger")
        return redirect(url_for("site_manage_employees", sup_id=from_id))
    if u.role == "site_supervisor" and target_id not in _site_scope_sup_ids(u):
        flash("The receiving supervisor is not within your scope.", "danger")
        return redirect(url_for("site_manage_employees", sup_id=from_id))

    target = User.query.get(target_id)
    if not target or target.role != "supervisor":
        flash("Invalid receiving supervisor.", "danger")
        return redirect(url_for("site_manage_employees", sup_id=from_id))

    moved = 0
    for e_id in emp_ids:
        emp = Employee.query.get(e_id)
        if not emp or not _site_guard_emp(u, emp) or emp.user_id == target_id:
            continue
        old_id = emp.user_id
        emp.user_id   = target_id
        emp.status    = "active"
        emp.is_active = True
        _log_emp_assignment(emp, "transfer", u, from_user_id=old_id, to_user_id=target_id,
                            note="bulk")
        moved += 1
    db.session.commit()
    flash(f"{moved} employee(s) transferred to {target.name or target.supervisor_code}.", "success")
    return redirect(url_for("site_manage_employees", sup_id=from_id))


@app.post("/site/employees/<int:emp_id>/unassign")
@login_required
def site_employee_unassign(emp_id):
    """فصل الموظف عن مشرفه — يصبح unassigned (بدون حذف نهائي)."""
    u = cur_user()
    if u.role not in ("site_supervisor", "admin"):
        abort(403)

    emp = Employee.query.get_or_404(emp_id)
    if not _site_guard_emp(u, emp):
        abort(403)

    old_id = emp.user_id
    emp.status  = "unassigned"
    emp.user_id = None
    _log_emp_assignment(emp, "unassign", u, from_user_id=old_id, to_user_id=None,
                        note=request.form.get("note", ""))
    db.session.commit()
    flash(f"{emp.name} unassigned from the supervisor — moved to Unassigned.", "success")
    return redirect(url_for("site_manage_employees", sup_id=old_id))


@app.post("/site/employees/<int:emp_id>/resign")
@login_required
def site_employee_resign(emp_id):
    """تسجيل استقالة الموظف — يبقى في السجل ولا يُحذف."""
    u = cur_user()
    if u.role not in ("site_supervisor", "admin"):
        abort(403)

    emp = Employee.query.get_or_404(emp_id)
    if not _site_guard_emp(u, emp):
        abort(403)

    old_id = emp.user_id
    emp.status      = "resigned"
    emp.is_active   = False
    emp.resigned_at = datetime.now(RIYADH_TZ).date()
    _log_emp_assignment(emp, "resign", u, from_user_id=old_id, to_user_id=None,
                        note=request.form.get("note", ""))
    db.session.commit()
    flash(f"{emp.name} marked as resigned.", "success")
    return redirect(url_for("site_manage_employees", sup_id=old_id, show="resigned"))


@app.post("/site/employees/<int:emp_id>/restore")
@login_required
def site_employee_restore(emp_id):
    """إرجاع موظف مستقيل/مفصول إلى مشرف — عكس عملية الاستقالة."""
    u = cur_user()
    if u.role not in ("site_supervisor", "admin"):
        abort(403)

    emp = Employee.query.get_or_404(emp_id)
    target_id = request.form.get("target_sup_id", type=int) or emp.user_id
    if u.role == "site_supervisor" and target_id not in _site_scope_sup_ids(u):
        abort(403)

    target = User.query.get(target_id)
    if not target or target.role != "supervisor":
        flash("Invalid receiving supervisor.", "danger")
        return redirect(url_for("site_manage_employees", sup_id=emp.user_id))

    old_id = emp.user_id
    emp.user_id     = target_id
    emp.status      = "active"
    emp.is_active   = True
    emp.resigned_at = None
    _log_emp_assignment(emp, "reactivate", u, from_user_id=old_id, to_user_id=target_id)
    db.session.commit()
    flash(f"{emp.name} restored to {target.name or target.supervisor_code}.", "success")
    return redirect(url_for("site_manage_employees", sup_id=target_id))


@app.get("/site/employee/<int:emp_id>/profile")
@login_required
def site_employee_profile(emp_id):
    """ملف الموظف الكامل: تقييمات + حضور + طلبات + سجل الحركة."""
    u = cur_user()
    if u.role not in ("site_supervisor", "admin"):
        abort(403)

    emp = Employee.query.get_or_404(emp_id)

    # الصلاحية: تابع لأحد مشرفيك الآن، أو كان تابعاً لهم سابقاً (سجل الحركة)
    if u.role == "site_supervisor":
        scope = _site_scope_sup_ids(u)
        allowed = emp.user_id in scope
        if not allowed:
            allowed = db.session.query(EmployeeAssignmentLog.id).filter(
                EmployeeAssignmentLog.employee_id == emp.id,
                or_(EmployeeAssignmentLog.from_user_id.in_(scope),
                    EmployeeAssignmentLog.to_user_id.in_(scope))).first() is not None
        if not allowed:
            abort(403)

    # ── التقييمات ──
    evals = (Evaluation.query.filter_by(employee_id=emp.id)
             .order_by(Evaluation.week_start.desc()).all())
    avg_score = round(sum((e.total_score or 0) for e in evals) / len(evals), 1) if evals else None
    last_score = evals[0].total_score if evals else None

    # اسم المُقيِّم لكل تقييم (من قيّم فعلاً، لا المالك الحالي)
    ev_uids = {e.evaluator_id for e in evals if e.evaluator_id}
    ev_names = {x.id: (x.name or x.supervisor_code)
                for x in User.query.filter(User.id.in_(list(ev_uids))).all()} if ev_uids else {}

    # ── الحضور: آخر 60 يوم + ملخص ──
    since = datetime.now(RIYADH_TZ).date() - timedelta(days=60)
    att = (Attendance.query.filter(Attendance.employee_id == emp.id,
                                   Attendance.date >= since)
           .order_by(Attendance.date.desc()).all())
    att_summary = Counter(a.status for a in att)
    att_total = len(att)
    att_rate = round(att_summary.get("present", 0) / att_total * 100.0, 0) if att_total else None

    # ── الطلبات ──
    reqs = (Request.query.filter_by(employee_id=emp.id)
            .order_by(Request.created_at.desc()).limit(50).all())
    req_summary = Counter(r.status for r in reqs)

    # ── سجل الحركة بين المشرفين ──
    moves = (EmployeeAssignmentLog.query.filter_by(employee_id=emp.id)
             .order_by(EmployeeAssignmentLog.created_at.desc()).all())
    mv_uids = set()
    for m in moves:
        mv_uids.update([m.from_user_id, m.to_user_id, m.actor_id])
    mv_uids.discard(None)
    mv_names = {x.id: (x.name or x.supervisor_code)
                for x in User.query.filter(User.id.in_(list(mv_uids))).all()} if mv_uids else {}

    cur_sup = User.query.get(emp.user_id) if emp.user_id else None
    back_url = url_for("site_manage_employees", sup_id=emp.user_id) if emp.user_id \
               else url_for("site_manage_employees")

    return render_template("site_employee_profile.html",
                           emp=emp, cur_sup=cur_sup, back_url=back_url,
                           evals=evals, ev_names=ev_names,
                           avg_score=avg_score, last_score=last_score,
                           att=att[:30], att_summary=att_summary,
                           att_total=att_total, att_rate=att_rate,
                           reqs=reqs, req_summary=req_summary,
                           moves=moves, mv_names=mv_names)


@app.get("/site/employee/<int:emp_id>/movement")
@login_required
def site_employee_movement(emp_id):
    """سجل حركة موظف واحد — JSON للعرض داخل الصفحة."""
    u = cur_user()
    if u.role not in ("site_supervisor", "admin"):
        return jsonify(error="forbidden"), 403
    emp = Employee.query.get_or_404(emp_id)

    rows = (EmployeeAssignmentLog.query.filter_by(employee_id=emp.id)
            .order_by(EmployeeAssignmentLog.created_at.desc()).limit(50).all())
    uids = set()
    for r in rows:
        uids.update([r.from_user_id, r.to_user_id, r.actor_id])
    uids.discard(None)
    nm = {x.id: (x.name or x.supervisor_code)
          for x in User.query.filter(User.id.in_(list(uids))).all()} if uids else {}

    return jsonify({
        "employee": {"id": emp.id, "name": emp.name, "emp_number": emp.emp_number,
                     "status": emp.status},
        "rows": [{
            "action": r.action,
            "from":   nm.get(r.from_user_id, "—"),
            "to":     nm.get(r.to_user_id, "—"),
            "by":     nm.get(r.actor_id, "—"),
            "note":   r.note or "",
            "at":     r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
        } for r in rows]
    })




@app.route("/site/employees/unassigned", methods=["GET", "POST"])
@login_required
def site_unassigned_employees():
    u = cur_user()
    if u.role != "site_supervisor":
        abort(403)

    my_supervisors = (db.session.query(User)
                       .join(SiteSupervisorMap, SiteSupervisorMap.supervisor_id == User.id)
                       .filter(SiteSupervisorMap.site_sup_id == u.id)
                       .order_by(User.name.asc()).all())

    if request.method == "POST":
        emp_id        = request.form.get("emp_id", type=int)
        supervisor_id = request.form.get("supervisor_id", type=int)
        emp = Employee.query.get_or_404(emp_id) if emp_id else None
        if not emp or not supervisor_id:
            flash("Please choose an employee and a supervisor.", "danger")
        else:
            ok, msg, _status = _assign_employee_to_supervisor(emp, supervisor_id, u)
            flash(msg, "success" if ok else "danger")
        return redirect(url_for("site_unassigned_employees"))

    emp_q = apply_company_filter(Employee.query.filter_by(status="unassigned"), Employee)
    employees = emp_q.order_by(Employee.name.asc()).all()
    return render_template("site_unassigned_employees.html",
                           employees=employees, my_supervisors=my_supervisors)


@app.get("/site/employees/requests")
@login_required
def site_employee_requests():
    """طلبات موظفي المشرفين الخاضعين لهذا الـ site_supervisor — قراءة فقط"""
    u = cur_user()
    if u.role != "site_supervisor":
        abort(403)

    my_sup_ids = [row[0] for row in
                 db.session.query(SiteSupervisorMap.supervisor_id)
                 .filter(SiteSupervisorMap.site_sup_id == u.id).all()]

    rows = []
    if my_sup_ids:
        rows = (Request.query
                .join(Employee, Request.employee_id == Employee.id)
                .filter(Employee.user_id.in_(my_sup_ids))
                .order_by(Request.created_at.desc()).all())

    return render_template("site_employee_requests.html", rows=rows)


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
            company_id=cid(),
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
    supervisor = User.query.get_or_404(sup_user_id)
    se = SupervisorEvaluation.query.filter_by(supervisor_id=sup_user_id, week_start=ws, week_end=we).first()
    if not se:
        flash(f"No evaluation found for {supervisor.name} in week {ws} → {we}.", "warning")
        return redirect(url_for("site_report_select") if u.role == "site_supervisor" else url_for("admin_site_report_picker"))
    evaluator = db.session.get(User, se.evaluator_id)
    return render_template("site_report_supervisor.html", se=se, supervisor=supervisor, evaluator=evaluator)

@app.route("/site/attendance")
@login_required
def site_attendance_site():
    u = cur_user()
    if u.role != "site_supervisor":
        abort(403)

    d_s = request.args.get("d")
    if d_s:
        d_val = parse_date(d_s)
    else:
        d_val = date.today()

    # اشوف المشرفين اللي تحت هذا الـ site sup
    links = (db.session.query(SiteSupervisorMap, User)
             .join(User, SiteSupervisorMap.supervisor_id == User.id)
             .filter(SiteSupervisorMap.site_sup_id == u.id)
             .all())
    sup_ids = [su.id for _, su in links]

    # هذا يفترض إن عندك موديل Attendance بنفس شكل admin
    q = (db.session.query(Attendance, Employee, User)
         .join(Employee, Attendance.employee_id == Employee.id)
         .join(User, Employee.user_id == User.id)
         .filter(Attendance.date == d_val))

    if sup_ids:
        q = q.filter(Employee.user_id.in_(sup_ids))

    rows = q.order_by(User.name.asc(), Employee.name.asc()).all()

    # نرجّع نفس قالب الحضور اللي عندك
    return render_template("attendance_admin.html", rows=rows, d=d_val)

@app.route("/site/requests")
@login_required
def site_requests_site():
    u = cur_user()
    if u.role != "site_supervisor":
        abort(403)

    # المشرفين اللي تحته
    links = (db.session.query(SiteSupervisorMap, User)
             .join(User, SiteSupervisorMap.supervisor_id == User.id)
             .filter(SiteSupervisorMap.site_sup_id == u.id)
             .all())
    sup_ids = [su.id for _, su in links]

    # نجيب الطلبات اللي أنشأها هؤلاء المشرفون
    req_q = (Request.query
             .filter(Request.supervisor_id.in_(sup_ids))
             .order_by(Request.created_at.desc()))
    items = req_q.all()

    return render_template("requests_inbox.html", items=items)


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

import traceback

# مسارات يطلبها المتصفح تلقائياً — 404 طبيعي، لا داعي لتلويث اللوق
_QUIET_404 = {
    "/favicon.ico", "/apple-touch-icon.png",
    "/apple-touch-icon-precomposed.png", "/robots.txt",
}


@app.errorhandler(404)
def _not_found(e):
    if request.path not in _QUIET_404:
        app.logger.error("404 path=%s method=%s", request.path, request.method)
    return "Not Found", 404


@app.errorhandler(413)
def _too_large(e):
    flash("الصورة كبيرة جداً — الحد الأقصى 10 ميجابايت لكل طلب.", "warning")
    return redirect(request.referrer or url_for("index"))


@app.route("/favicon.ico")
@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
def _browser_icons():
    """المتصفح يطلب هذه تلقائياً — نخدمها من الشعار بدل 404 متكرر."""
    return redirect(url_for("static", filename="img/logo.png"))


@app.errorhandler(OperationalError)
def _db_connection_lost(e):
    """انقطاع اتصال MySQL — نظّف الجلسة وأعطِ رسالة مفهومة بدل صفحة 500."""
    app.logger.error("DB connection lost on %s: %s", request.path, e)
    try:
        db.session.rollback()
        db.session.remove()
    except Exception:
        pass
    if request.path.startswith("/api/"):
        return jsonify(error="تعذّر الاتصال بقاعدة البيانات، حاول مرة أخرى"), 503
    return ("<div style='font-family:system-ui;padding:40px;text-align:center'>"
            "<h2>انقطع الاتصال بقاعدة البيانات</h2>"
            "<p style='color:#64748b'>حاول تحديث الصفحة بعد لحظات.</p>"
            "<a href='javascript:location.reload()' "
            "style='display:inline-block;margin-top:12px;padding:10px 22px;"
            "background:#1d4ed8;color:#fff;border-radius:8px;text-decoration:none'>"
            "إعادة المحاولة</a></div>"), 503

# ============================================================
#  ملف التعديلات للتطبيق — أضف هذا الكود في آخر ملف main.py
#  قبل سطر: ensure_db_and_admin()
# ============================================================

# المكتبات المُستوردة أعلى الملف — uuid, json, secrets, freq مستوردة هنا للـ API section
import uuid
import json
import secrets
from flask import jsonify, request as freq

from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# ─────────────────────────────────────────────
#  إعداد رفع الملفات
# ─────────────────────────────────────────────
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 ميجا كحد أقصى

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# ─────────────────────────────────────────────
#  موديل التوكن للتطبيق (جدول منفصل)
# ─────────────────────────────────────────────
class MobileToken(db.Model):
    __tablename__ = "mobile_token"
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    token        = db.Column(db.String(64), unique=True, nullable=False)
    device_token = db.Column(db.String(255), nullable=True)
    company_id   = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    user         = db.relationship("User", backref="mobile_tokens")

# ─────────────────────────────────────────────
#  موديل مرفق الطلب (PDF)
# ─────────────────────────────────────────────
class RequestAttachment(db.Model):
    __tablename__ = "request_attachment"
    id          = db.Column(db.Integer, primary_key=True)
    request_id  = db.Column(db.Integer, db.ForeignKey("requests.id"), nullable=False)
    filename    = db.Column(db.String(255), nullable=False)   # اسم الملف المحفوظ
    original_name = db.Column(db.String(255), nullable=True)  # الاسم الأصلي
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    request     = db.relationship("Request", backref="attachments")

# ─────────────────────────────────────────────
#  موديل نموذج الإجازة الثابت
# ─────────────────────────────────────────────
class LeaveForm(db.Model):
    __tablename__ = "leave_form"
    id          = db.Column(db.Integer, primary_key=True)
    request_id  = db.Column(db.Integer, db.ForeignKey("requests.id"), nullable=False, unique=True)
    filename    = db.Column(db.String(255), nullable=False)   # اسم ملف PDF المُولّد
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    signed      = db.Column(db.Boolean, default=False)
    signed_at   = db.Column(db.DateTime, nullable=True)
    request     = db.relationship("Request", backref="leave_form", uselist=False)

# db.create_all() تُستدعى مرة واحدة في نهاية الملف

# ─────────────────────────────────────────────
#  مساعد: التحقق من توكن التطبيق
# ─────────────────────────────────────────────
def get_api_user():
    auth = freq.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token_str = auth[7:]
    else:
        # قبول التوكن من query parameter (لفتح الملفات في Safari)
        token_str = freq.args.get("token", "")
    if not token_str:
        return None
    tok = MobileToken.query.filter_by(token=token_str).first()
    return tok.user if tok else None

def get_api_token():
    auth = freq.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return MobileToken.query.filter_by(token=auth[7:]).first()

def api_cid():
    """Returns company_id from the API token (for data isolation in API routes)."""
    tok = get_api_token()
    if not tok:
        return None
    u = tok.user
    if u and u.role == "super_admin":
        return None
    return tok.company_id or (u.company_id if u else None)

def api_login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        u = get_api_user()
        if not u or not u.is_active:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper

def api_admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        u = get_api_user()
        if not u or u.role != "admin":
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return wrapper

def api_hr_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        u = get_api_user()
        if not u or u.role != "hr":
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return wrapper

# ─────────────────────────────────────────────
#  مساعد توليد نموذج الإجازة PDF
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
#  بيانات شركات خطاب الإنذار — فقط اللوقو واسم الشركة يختلفان حسب اختيار
#  الموارد البشرية، وباقي بيانات الترويسة (العنوان/الهاتف...) ثابتة كما بالخطاب الأصلي
# ─────────────────────────────────────────────
WARNING_COMPANY_NAMES = {
    "NSH": {"logo": "logo.png",  "name_ar": "شركة ناصر سعيد الهاجري وشركاه للمقاولات",
            "name_en": "NASSER S. AL-HAJRI & PARTNERS FOR CONT. CO."},
    "GA":  {"logo": "logo2.png", "name_ar": "شركة الخليج آسيا",
            "name_en": "GULF ASIA CO."},
}

WARNING_LETTERHEAD = {
    "lines_en": [
        "C.R. 2060006231",
        "P.O. Box No. 495, Dhahran Airport 31932",
        "SAUDI ARABIA",
        "Tel.: (013) 814 7779 / (013) 8650009",
        "Fax: (013) 814 4441 / (013) 8984211",
        "E-mail: nsh@alhajricorporation.com",
        "Website: www.alhajricorporation.com",
    ],
    "footer_en": "Jubail: Tel.: (013) 3448768, 69, 71, 75 - Fax: (013) 3448770, 3448772",
    "footer_branches_en": "Branches: DUBAI, KUWAIT, BAHRAIN",
}


def decode_signature_image(raw_value):
    """يفك ترميز صورة التوقيع (PNG) من base64.
    المتصفح يرسل التوقيع عبر canvas.toDataURL() الذي يُنتج قيمة كاملة على
    شكل 'data:image/png;base64,XXXXX' — فك التشفير المباشر لهذه القيمة
    كاملة (بدون إزالة البادئة) يفشل بخطأ 'Incorrect padding' لأن الرموز
    ':' و'/' و';' و',' ليست من أبجدية base64، وهذا كان يسبب عدم ظهور
    التوقيع أبداً بأي من نماذج PDF الثلاثة (إجازة/استئذان/إنذار) رغم
    رسمه بنجاح بالمتصفح."""
    import base64
    s = (raw_value or "").strip()
    if s.lower().startswith("data:") and "," in s:
        s = s.split(",", 1)[1]
    return base64.b64decode(s)


def build_warning_declaration(emp_name, job_title, emp_no, date_str, reason_text):
    """نص الإقرار الرسمي الموحّد — تستخدمه دالة توليد الـ PDF وأيضاً تحقق صفحة
    التوقيع (كتابة النص يدوياً) لضمان تطابق النصين حرفياً دائماً"""
    return (f"أقر أنا / {emp_name} وظيفتي {job_title} رقم {emp_no} الموقع على هذا الإنذار "
            f"على المخالفة الصادرة مني بتاريخ {date_str} وهي {reason_text} .")


def generate_warning_pdf(req: "Request", sig: "WarningSignature") -> str:
    """يولّد خطاب إنذار PDF بنفس تصميم الترويسة الرسمية للشركة مع توقيع الموظف"""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage, HRFlowable, PageBreak
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        import base64, io
    except ImportError:
        return ""

    # ── تسجيل الخط العربي ──
    # مهم جداً: نجرب DejaVu Sans أولاً. الجملة الواحدة غالباً تخلط عربي مع
    # اسم إنجليزي/أرقام/علامة % و/ — وخط Noto Naskh Arabic عربي فقط بدون أي
    # غطاء للحروف اللاتينية أو / أو %، فتظهر هذي الرموز كمربعات فارغة رغم
    # صحة النص العربي نفسه. DejaVu Sans يغطي العربي والإنجليزي والأرقام
    # والرموز بخط واحد فلا يحصل نقص أبداً بجملة مختلطة.
    ARABIC_FONT = "Arabic"
    ARABIC_TTF_CANDIDATES = [
        os.path.join(BASE_DIR, "static", "fonts", "DejaVuSans.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        os.path.join(BASE_DIR, "static", "fonts", "NotoNaskhArabic-Regular.ttf"),
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        os.path.join(BASE_DIR, "static", "fonts", "DroidSansArabic.ttf"),
        "/usr/share/fonts/google-droid/DroidSansArabic.ttf",
        "C:\\Windows\\Fonts\\tahoma.ttf",
    ]
    LATIN_FONT  = "Helvetica"
    font_registered = False
    for _ttf_path in ARABIC_TTF_CANDIDATES:
        if os.path.exists(_ttf_path):
            try:
                pdfmetrics.registerFont(TTFont(ARABIC_FONT, _ttf_path))
                font_registered = True
                break
            except Exception:
                continue
    if not font_registered:
        ARABIC_FONT = "Helvetica"  # fallback أخير — لا يدعم العربية

    def ar(text):
        """عكس النص العربي فقط لـ ReportLab (RTL)"""
        t = str(text)
        if not any("؀" <= c <= "ۿ" for c in t):
            return t
        try:
            from arabic_reshaper import reshape
            from bidi.algorithm import get_display
            return get_display(reshape(t))
        except Exception:
            try:
                from bidi.algorithm import get_display
                return get_display(t)
            except Exception:
                return t

    def ar_wrap(text, max_chars=58):
        """لفقرات عربية طويلة تحتاج أكثر من سطر: نقسّم النص يدوياً لأسطر
        ونطبّق ar() على كل سطر لوحده بدل الفقرة كاملة. لو طبّقنا bidi على
        الفقرة كوحدة واحدة ثم تركنا reportlab يكسرها لأسطر، يطلع ترتيب
        الأسطر معكوساً (السطر الثاني منطقياً يظهر فوق الأول) لأن reportlab
        يكسر السطور حسب الترتيب البصري بعد إعادة الترتيب لا قبلها."""
        words = str(text).split(" ")
        lines, cur = [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if len(trial) > max_chars and cur:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        if cur:
            lines.append(cur)
        return "<br/>".join(ar(l) for l in lines)

    def font_for(text):
        if any("؀" <= c <= "ۿ" for c in str(text)):
            return ARABIC_FONT
        return LATIN_FONT

    def cell(text, size=10.5, color=None):
        t = ar(text)
        is_ar = any("؀" <= c <= "ۿ" for c in str(text))
        return Paragraph(t, ParagraphStyle("c%s" % uuid.uuid4().hex[:6], fontName=font_for(text),
                                           fontSize=size, alignment=2 if is_ar else 0,
                                           textColor=color or colors.black))

    emp  = req.employee
    sup  = req.supervisor
    task_item = HRTask.query.filter_by(request_id=req.id).first()
    try:
        reason_text = (task_item.official_reason if task_item and task_item.official_reason else req.reason) or "—"
    except Exception:
        reason_text = req.reason or "—"

    company_code = "NSH"
    try:
        if task_item and task_item.warning_company in WARNING_COMPANY_NAMES:
            company_code = task_item.warning_company
    except Exception:
        pass
    co_name = WARNING_COMPANY_NAMES.get(company_code, WARNING_COMPANY_NAMES["NSH"])
    lh = WARNING_LETTERHEAD

    fname = f"warning_{req.id}_{uuid.uuid4().hex[:8]}.pdf"
    fpath = os.path.join(UPLOAD_FOLDER, fname)

    doc = SimpleDocTemplate(fpath, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)

    styles = getSampleStyleSheet()
    elements = []

    # ══════════════════════════════════════════════════════
    #  الصفحة الأولى: تقرير سبب الإنذار (تعبئة المشرف الميدانية)
    #  — تُضاف فقط لو فيه تقرير ميداني محفوظ لهذا الطلب (WarningFieldReport)،
    #  وإلا تبدأ الصفحة الرسمية مباشرة كما كانت (توافقاً مع الإنذارات القديمة)
    # ══════════════════════════════════════════════════════
    wfr = getattr(req, "warning_field_report", None)
    if wfr:
        elements.append(Paragraph(
            ar("تقرير سبب الإنذار"),
            ParagraphStyle("wfr_title", fontName=ARABIC_FONT, fontSize=17, alignment=1,
                           textColor=colors.HexColor("#1a1f3a"), spaceAfter=16)
        ))

        wfr_hdr = Table([
            [cell("اليوم"), cell("التاريخ"), cell("اسم الموظف"), cell("ID"), cell("اسم المشرف"), cell("ID")],
            [cell(wfr.day_name or "—"),
             cell(wfr.report_date.strftime("%Y-%m-%d") if wfr.report_date else "—"),
             cell(wfr.employee_name_snapshot or "—"),
             cell(wfr.employee_no_snapshot or "—"),
             cell(wfr.supervisor_name_snapshot or "—"),
             cell(wfr.supervisor_code_snapshot or "—")],
        ], colWidths=[2.2*cm, 2.8*cm, 3.6*cm, 2.2*cm, 3.6*cm, 2.2*cm])
        wfr_hdr.setStyle(TableStyle([
            ("GRID",          (0, 0), (-1, -1), 0.7, colors.HexColor("#555555")),
            ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
            ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        elements.append(wfr_hdr)
        elements.append(Spacer(1, 1.2*cm))

        elements.append(Paragraph(
            ar("سبب الإنذار"),
            ParagraphStyle("wfr_reason_title", fontName=ARABIC_FONT, fontSize=13, alignment=1,
                           textColor=colors.HexColor("#1a1f3a"))
        ))
        reason_box = Table([[Paragraph(
            ar_wrap(wfr.reason_text or "—", max_chars=70),
            ParagraphStyle("wfr_reason", fontName=ARABIC_FONT, fontSize=11.5, leading=26, alignment=2)
        )]], colWidths=[17*cm])
        reason_box.setStyle(TableStyle([
            ("BOX",           (0, 0), (-1, -1), 0.7, colors.HexColor("#555555")),
            ("TOPPADDING",    (0, 0), (-1, -1), 14),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
            ("LEFTPADDING",   (0, 0), (-1, -1), 12),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
        ]))
        elements.append(Spacer(1, 0.3*cm))
        elements.append(reason_box)
        elements.append(Spacer(1, 1.5*cm))

        wfr_sign_img = None
        try:
            wfr_sign_img = RLImage(io.BytesIO(decode_signature_image(wfr.signature)), width=4.5*cm, height=1.8*cm)
        except Exception:
            wfr_sign_img = None

        sig_tbl = Table([[
            wfr_sign_img if wfr_sign_img else Paragraph("_" * 25, styles["Normal"]),
            cell("توقيع المشرف", size=11, color=colors.HexColor("#1a1f3a")),
        ]], colWidths=[12*cm, 5*cm])
        sig_tbl.setStyle(TableStyle([("ALIGN", (0, 0), (0, 0), "LEFT"), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                                     ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        elements.append(sig_tbl)
        elements.append(PageBreak())

    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Asia/Riyadh")).date()
    greg_str = f"{today.day:02d} / {today.month:02d} / {today.year}"
    try:
        from hijridate import Gregorian
        h = Gregorian(today.year, today.month, today.day).to_hijri()
        hijri_str = f"{h.day:02d} / {h.month:02d} / {h.year}"
    except Exception:
        hijri_str = "—"

    # ══ ترويسة الشركة (شعار + بيانات إنجليزية يسار / اسم عربي يمين) ══
    # اللوقو واسم الشركة فقط يتغيران حسب اختيار الموارد البشرية (NSH/GA)،
    # وباقي بيانات الترويسة (العنوان والتواصل) ثابتة كما بالخطاب الأصلي
    logo_path = os.path.join(BASE_DIR, "static", "img", co_name["logo"])
    logo_cell = Paragraph("", styles["Normal"])
    if os.path.exists(logo_path):
        try:
            logo_cell = RLImage(logo_path, width=2.8*cm, height=2.8*cm)
        except Exception as _logo_err:
            app.logger.error("warning PDF: invalid logo file %s: %s", logo_path, _logo_err)
            logo_cell = Paragraph("", styles["Normal"])

    en_block = [Paragraph(co_name["name_en"], ParagraphStyle("co_en_name", fontName=LATIN_FONT, fontSize=10,
                          alignment=0, textColor=colors.HexColor("#1a1f3a"), spaceAfter=2))]
    for ln in lh["lines_en"]:
        en_block.append(Paragraph(ln, ParagraphStyle("co_en_l", fontName=LATIN_FONT, fontSize=7.5,
                        alignment=0, textColor=colors.HexColor("#444444"), leading=10)))

    ar_block = [Paragraph(ar(co_name["name_ar"]), ParagraphStyle("co_ar_name", fontName=ARABIC_FONT, fontSize=13,
                          alignment=2, textColor=colors.HexColor("#1a1f3a")))]

    header_tbl = Table([[en_block, logo_cell, ar_block]],
                        colWidths=[7.5*cm, 2.8*cm, 6.7*cm])
    header_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN",  (0, 0), (0, 0), "LEFT"),
        ("ALIGN",  (1, 0), (1, 0), "CENTER"),
        ("ALIGN",  (2, 0), (2, 0), "RIGHT"),
    ]))
    elements.append(header_tbl)
    elements.append(Spacer(1, 0.2*cm))
    elements.append(HRFlowable(width="100%", thickness=1.2, color=colors.HexColor("#c0392b")))
    elements.append(Spacer(1, 0.7*cm))

    # ══ التاريخ (هجري وميلادي) — الأرقام تبقى بالخط اللاتيني ══
    date_tbl = Table([[
        cell(f"الموافق : {greg_str} م", size=10.5),
        cell(f"التاريخ : {hijri_str} هـ", size=10.5),
    ]], colWidths=[8.5*cm, 8.5*cm])
    date_tbl.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    elements.append(date_tbl)
    elements.append(Spacer(1, 0.6*cm))

    # ══ الموضوع ══
    elements.append(Paragraph(
        ar(f"الموضوع : {reason_text}"),
        ParagraphStyle("subject", fontName=ARABIC_FONT, fontSize=13, alignment=1,
                       textColor=colors.HexColor("#1a1f3a"), spaceAfter=4)
    ))
    elements.append(HRFlowable(width="45%", thickness=0.8, color=colors.HexColor("#1a1f3a"), hAlign="CENTER"))
    elements.append(Spacer(1, 0.9*cm))

    # ══ نص الإقرار الأول ══
    emp_name   = (sig.employee_name if sig and sig.employee_name else (emp.name if emp else "—"))
    emp_no     = emp.emp_number if emp else "—"
    job_title  = (sig.job_title if sig and sig.job_title else (emp.department if emp and emp.department else "—"))
    body1 = build_warning_declaration(emp_name, job_title, emp_no, greg_str, reason_text)
    elements.append(Paragraph(
        ar_wrap(body1),
        ParagraphStyle("body1", fontName=ARABIC_FONT, fontSize=11.5, leading=20, alignment=2)
    ))
    elements.append(Spacer(1, 0.6*cm))

    # ══ نص الإقرار الثاني (الخصم والتعهد) ══
    body2 = ("كما أقر وأوافق على خصم 25% من أجر اليوم وأتعهد بعدم تكرار ذلك مستقبلاً "
             "وإلا حق للشركة اتخاذ الإجراءات النظامية بموجب نظام العمل السعودي .")
    elements.append(Paragraph(
        ar_wrap(body2),
        ParagraphStyle("body2", fontName=ARABIC_FONT, fontSize=11.5, leading=20, alignment=2)
    ))
    elements.append(Spacer(1, 1.2*cm))

    # ══ توقيع الموظف — الاسم يُسجَّل مع التوقيع نفسه ══
    sig_name = (sig.employee_name if sig and sig.employee_name else emp_name)
    sign_img = None
    try:
        img_data = decode_signature_image(sig.signature)
        sign_img = RLImage(io.BytesIO(img_data), width=4.5*cm, height=1.8*cm)
    except Exception:
        sign_img = None

    name_row = Table([[
        Spacer(1, 0.2*cm),
        cell(sig_name, size=11),
        cell("الأسم :", size=11, color=colors.HexColor("#1a1f3a")),
    ]], colWidths=[6*cm, 6*cm, 3*cm])
    name_row.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    elements.append(name_row)
    elements.append(Spacer(1, 0.3*cm))

    sig_row = Table([[
        sign_img if sign_img else Paragraph("_" * 25, styles["Normal"]),
        cell("التوقيع :", size=11, color=colors.HexColor("#1a1f3a")),
    ]], colWidths=[12*cm, 3*cm])
    sig_row.setStyle(TableStyle([("ALIGN", (0, 0), (0, 0), "LEFT"), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    elements.append(sig_row)
    elements.append(Spacer(1, 1.3*cm))

    # ══ توقيع مدير الموارد البشرية في المشروع ══
    elements.append(Paragraph(
        ar("توقيع مدير الموارد البشرية في المشروع"),
        ParagraphStyle("hr_mgr_lbl", fontName=ARABIC_FONT, fontSize=11, alignment=1,
                       textColor=colors.HexColor("#1a1f3a"))
    ))
    elements.append(Spacer(1, 1.1*cm))
    elements.append(HRFlowable(width="45%", thickness=0.6, color=colors.grey, hAlign="CENTER"))
    elements.append(Spacer(1, 1.1*cm))

    # ══ أعتماد إدارة الموارد البشرية (صندوق فارغ للاعتماد) ══
    elements.append(Paragraph(
        ar("أعتماد إدارة الموارد البشرية"),
        ParagraphStyle("hr_dept_lbl", fontName=ARABIC_FONT, fontSize=11, alignment=1,
                       textColor=colors.HexColor("#1a1f3a"))
    ))
    elements.append(Spacer(1, 0.3*cm))
    approval_box = Table([[Spacer(1, 1.6*cm)]], colWidths=[7*cm])
    approval_box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#1a1f3a"))]))
    approval_box.hAlign = "CENTER"
    elements.append(approval_box)
    elements.append(Spacer(1, 1*cm))

    # ══ تذييل الصفحة (بيانات التواصل والفروع) ══
    if lh.get("footer_en"):
        elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
        elements.append(Spacer(1, 0.2*cm))
        elements.append(Paragraph(lh["footer_en"], ParagraphStyle("f_en", fontName=LATIN_FONT, fontSize=7,
                        alignment=1, textColor=colors.HexColor("#888888"))))
        if lh.get("footer_branches_en"):
            elements.append(Paragraph(lh["footer_branches_en"], ParagraphStyle("f_en2", fontName=LATIN_FONT,
                            fontSize=7, alignment=1, textColor=colors.HexColor("#888888"))))

    doc.build(elements)
    return fname

def generate_leave_pdf(req: "Request", sig: "LeaveSignature" = None) -> str:
    """نموذج إجازة رسمي عربي/إنجليزي صفحة واحدة مع إمكانية التوقيع."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                        Paragraph, Spacer, Image as RLImage,
                                        HRFlowable)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        import base64, io
    except ImportError:
        return ""

    # ── خط عربي ──
    # مهم: DejaVu Sans أولاً — يغطي العربي والإنجليزي والأرقام بخط واحد،
    # فيتفادى مشكلة المربعات عند اختلاط اسم إنجليزي داخل نص عربي
    ARABIC_FONT = "Arabic"
    ARABIC_TTF_CANDIDATES = [
        os.path.join(BASE_DIR, "static", "fonts", "DejaVuSans.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        os.path.join(BASE_DIR, "static", "fonts", "NotoNaskhArabic-Regular.ttf"),
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        os.path.join(BASE_DIR, "static", "fonts", "DroidSansArabic.ttf"),
        "/usr/share/fonts/google-droid/DroidSansArabic.ttf",
    ]
    LATIN_FONT  = "Helvetica"
    use_arabic = False
    for _ttf_path in ARABIC_TTF_CANDIDATES:
        if os.path.exists(_ttf_path):
            try:
                pdfmetrics.registerFont(TTFont(ARABIC_FONT, _ttf_path))
                use_arabic = True
                break
            except Exception:
                continue
    if not use_arabic:
        ARABIC_FONT = "Helvetica"

    def ar(txt: str) -> str:
        if not use_arabic:
            return txt
        try:
            import arabic_reshaper
            from bidi.algorithm import get_display
            return get_display(arabic_reshaper.reshape(txt))
        except Exception:
            return txt

    emp  = req.employee
    sup  = req.supervisor
    company_name = ""
    try:
        from sqlalchemy.orm import relationship
        if emp and emp.company_id:
            from main import Company
            c = Company.query.get(emp.company_id)
            if c:
                company_name = c.name or ""
    except Exception:
        pass

    days_count = "—"
    if req.start_date and req.end_date:
        days_count = str((req.end_date - req.start_date).days + 1)

    fname = f"leave_{req.id}_{uuid.uuid4().hex[:8]}.pdf"
    fpath = os.path.join(UPLOAD_FOLDER, fname)

    W, H = A4
    doc  = SimpleDocTemplate(fpath, pagesize=A4,
                              rightMargin=1.8*cm, leftMargin=1.8*cm,
                              topMargin=1.5*cm, bottomMargin=1.5*cm)

    styles   = getSampleStyleSheet()
    ar_style = ParagraphStyle("ar", fontName=ARABIC_FONT, fontSize=10,
                               alignment=2, leading=16)
    en_style = ParagraphStyle("en", fontName=LATIN_FONT,  fontSize=9,
                               alignment=0, textColor=colors.HexColor("#555555"), leading=14)
    hdr_style= ParagraphStyle("hdr",fontName=ARABIC_FONT, fontSize=14,
                               alignment=1, spaceAfter=2, textColor=colors.HexColor("#1a1f3a"))

    DARK  = colors.HexColor("#1a1f3a")
    BLUE  = colors.HexColor("#2563eb")
    LIGHT = colors.HexColor("#f0f4ff")
    GREY  = colors.HexColor("#f8f8f8")

    def cell(ar_text, en_text="", bold=False, bg=None, center=False):
        align = 1 if center else 2
        f     = ARABIC_FONT
        p_ar  = Paragraph(ar(ar_text), ParagraphStyle("c", fontName=f,
                          fontSize=10, alignment=align, leading=14,
                          textColor=colors.black))
        p_en  = Paragraph(en_text, ParagraphStyle("ce", fontName=LATIN_FONT,
                          fontSize=8,  alignment=1 if center else 0,
                          textColor=colors.HexColor("#777777"), leading=12))
        return [p_ar, p_en] if en_text else p_ar

    elements = []

    # ══ رأس الصفحة: شعار + اسم الشركة + عنوان ══
    logo_path = os.path.join(BASE_DIR, "static", "img", "logo.png")
    header_cols = []
    if os.path.exists(logo_path):
        try:
            header_cols.append(RLImage(logo_path, width=2.5*cm, height=2.5*cm))
        except Exception:
            header_cols.append(Paragraph("", styles["Normal"]))
    else:
        header_cols.append(Paragraph("", styles["Normal"]))

    header_cols.append(
        Table([[Paragraph(ar("نموذج طلب إجازة"), ParagraphStyle("t1",
                          fontName=ARABIC_FONT, fontSize=16, alignment=1,
                          textColor=DARK, spaceAfter=4))],
               [Paragraph("LEAVE REQUEST FORM", ParagraphStyle("t2",
                          fontName=LATIN_FONT,  fontSize=10, alignment=1,
                          textColor=BLUE))],
               [Paragraph(ar(company_name) if company_name else "",
                          ParagraphStyle("t3", fontName=ARABIC_FONT, fontSize=9,
                          alignment=1, textColor=colors.HexColor("#888888")))]],
              colWidths=[W - 3.6*cm - 5*cm])
    )
    header_cols.append(Paragraph("", styles["Normal"]))

    # ملاحظة: العمود الثالث كان بعرض None سابقاً، وهذا كان يسبب LayoutError
    # (ارتفاع خيالي 2147483659pt) لأن العمودين الأولين كانا يستهلكان كامل عرض
    # الصفحة فلا يبقى شيء للعمود الثالث — نعطيه الآن عرضاً صريحاً ثابتاً
    hdr_tbl = Table([header_cols], colWidths=[2.5*cm, W - 3.6*cm - 5*cm, 2.5*cm])
    hdr_tbl.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    elements.append(hdr_tbl)
    elements.append(HRFlowable(width="100%", thickness=2, color=BLUE, spaceAfter=10))

    # ══ بيانات الموظف ══
    def info_row(label_ar, label_en, val):
        val_str = val or "—"
        is_ar_val = any("؀" <= c <= "ۿ" for c in str(val_str))
        return [
            Paragraph(ar(label_ar), ParagraphStyle("lbl", fontName=ARABIC_FONT,
                      fontSize=9, alignment=2, textColor=colors.HexColor("#555"))),
            Paragraph(label_en, ParagraphStyle("lble", fontName=LATIN_FONT,
                      fontSize=7.5, alignment=0, textColor=colors.HexColor("#999"))),
            Paragraph(ar(str(val_str)) if is_ar_val else str(val_str),
                      ParagraphStyle("val", fontName=ARABIC_FONT if is_ar_val else LATIN_FONT,
                      fontSize=10, alignment=2 if is_ar_val else 0, textColor=DARK)),
        ]

    info_data = [
        info_row("اسم الموظف",    "Employee Name",   emp.name if emp else ""),
        info_row("رقم الموظف",    "Employee No.",    emp.emp_number if emp else ""),
        info_row("القسم",         "Department",      emp.department if emp else ""),
        info_row("الموقع",        "Site",            emp.site if emp else ""),
        info_row("المشرف المباشر","Direct Supervisor",sup.name if sup else ""),
    ]
    info_tbl = Table(info_data, colWidths=[3.5*cm, 3*cm, 9*cm])
    info_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), GREY),
        ("ROWBACKGROUNDS",(0,0), (-1,-1), [colors.white, GREY]),
        ("GRID",          (0,0), (-1,-1), 0.3, colors.HexColor("#dddddd")),
        ("TOPPADDING",    (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ("RIGHTPADDING",  (0,0), (-1,-1), 6),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]))
    elements.append(info_tbl)
    elements.append(Spacer(1, 0.4*cm))

    # ══ بيانات الإجازة ══
    elements.append(
        Paragraph(ar("تفاصيل الإجازة  |  Leave Details"),
                  ParagraphStyle("sec", fontName=ARABIC_FONT, fontSize=11,
                  alignment=1, textColor=BLUE, spaceAfter=4))
    )

    leave_data = [
        info_row("نوع الإجازة",    "Leave Type",     "إجازة اعتيادية" if req.type == "leave" else req.type),
        info_row("تاريخ البداية",  "Start Date",     str(req.start_date) if req.start_date else ""),
        info_row("تاريخ الانتهاء", "End Date",       str(req.end_date)   if req.end_date   else ""),
        info_row("عدد الأيام",     "No. of Days",    days_count),
        info_row("السبب",          "Reason",         req.reason or ""),
    ]
    leave_tbl = Table(leave_data, colWidths=[3.5*cm, 3*cm, 9*cm])
    leave_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), LIGHT),
        ("ROWBACKGROUNDS",(0,0), (-1,-1), [LIGHT, colors.white]),
        ("GRID",          (0,0), (-1,-1), 0.3, colors.HexColor("#c0d0ff")),
        ("TOPPADDING",    (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ("RIGHTPADDING",  (0,0), (-1,-1), 6),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]))
    elements.append(leave_tbl)
    elements.append(Spacer(1, 0.5*cm))

    # ══ التوقيعات ══
    elements.append(
        Paragraph(ar("التوقيعات  |  Signatures"),
                  ParagraphStyle("sec2", fontName=ARABIC_FONT, fontSize=11,
                  alignment=1, textColor=BLUE, spaceAfter=4))
    )

    # بناء خلية توقيع الموظف
    emp_sig_cell = []
    if sig and sig.signature:
        try:
            img_data = decode_signature_image(sig.signature)
            img_buf  = io.BytesIO(img_data)
            emp_sig_cell.append(RLImage(img_buf, width=4.5*cm, height=1.8*cm))
        except Exception:
            emp_sig_cell.append(Paragraph("_" * 20, styles["Normal"]))
        emp_sig_cell.append(Paragraph(
            ar(sig.employee_name or (emp.name if emp else "")),
            ParagraphStyle("sn", fontName=ARABIC_FONT, fontSize=8.5,
                           alignment=1, textColor=DARK)))
        signed_date = sig.signed_at.strftime("%Y/%m/%d") if sig.signed_at else ""
        emp_sig_cell.append(Paragraph(signed_date,
            ParagraphStyle("sd", fontName=LATIN_FONT, fontSize=8,
                           alignment=1, textColor=colors.HexColor("#777"))))
    else:
        emp_sig_cell = [Spacer(1, 1.2*cm),
                        Paragraph(ar("توقيع الموظف"),
                            ParagraphStyle("sl", fontName=ARABIC_FONT, fontSize=8.5,
                                           alignment=1, textColor=colors.HexColor("#aaa")))]

    sup_sig_cell  = [Spacer(1, 1.2*cm),
                     Paragraph(ar("توقيع المشرف"),
                         ParagraphStyle("s2", fontName=ARABIC_FONT, fontSize=8.5,
                                        alignment=1, textColor=colors.HexColor("#aaa")))]
    hr_sig_cell   = [Spacer(1, 1.2*cm),
                     Paragraph(ar("توقيع الموارد البشرية"),
                         ParagraphStyle("s3", fontName=ARABIC_FONT, fontSize=8.5,
                                        alignment=1, textColor=colors.HexColor("#aaa")))]

    sig_tbl = Table([[emp_sig_cell, sup_sig_cell, hr_sig_cell]],
                    colWidths=[5.3*cm, 5.3*cm, 5.3*cm])
    sig_tbl.setStyle(TableStyle([
        ("BOX",           (0,0), (0,0), 1, colors.HexColor("#2563eb")),
        ("BOX",           (1,0), (1,0), 1, colors.HexColor("#cccccc")),
        ("BOX",           (2,0), (2,0), 1, colors.HexColor("#cccccc")),
        ("ALIGN",         (0,0), (-1,-1), "CENTER"),
        ("VALIGN",        (0,0), (-1,-1), "BOTTOM"),
        ("TOPPADDING",    (0,0), (-1,-1), 10),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("BACKGROUND",    (0,0), (0,0), colors.HexColor("#f0f4ff")),
    ]))
    elements.append(sig_tbl)

    # ══ تذييل ══
    elements.append(Spacer(1, 0.5*cm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    from datetime import datetime as dt_cls
    elements.append(Paragraph(
        f"Generated: {dt_cls.utcnow().strftime('%Y-%m-%d')}  |  NSH SafeTrack",
        ParagraphStyle("ft", fontName=LATIN_FONT, fontSize=7.5,
                       alignment=1, textColor=colors.HexColor("#aaaaaa"))))

    doc.build(elements)
    return fname


def generate_permission_pdf(req: "Request", sig: "PermissionSignature" = None) -> str:
    """نموذج استئذان رسمي عربي/إنجليزي مع التوقيع."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                        Paragraph, Spacer, Image as RLImage,
                                        HRFlowable)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        import base64, io
    except ImportError:
        return ""

    ARABIC_FONT = "Arabic"
    ARABIC_TTF_CANDIDATES = [
        os.path.join(BASE_DIR, "static", "fonts", "DejaVuSans.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        os.path.join(BASE_DIR, "static", "fonts", "NotoNaskhArabic-Regular.ttf"),
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        os.path.join(BASE_DIR, "static", "fonts", "DroidSansArabic.ttf"),
        "/usr/share/fonts/google-droid/DroidSansArabic.ttf",
    ]
    LATIN_FONT  = "Helvetica"
    use_arabic = False
    for _ttf_path in ARABIC_TTF_CANDIDATES:
        if os.path.exists(_ttf_path):
            try:
                pdfmetrics.registerFont(TTFont(ARABIC_FONT, _ttf_path))
                use_arabic = True
                break
            except Exception:
                continue
    if not use_arabic:
        ARABIC_FONT = "Helvetica"

    def ar(txt):
        if not use_arabic:
            return txt
        try:
            import arabic_reshaper
            from bidi.algorithm import get_display
            return get_display(arabic_reshaper.reshape(txt))
        except Exception:
            return txt

    emp = req.employee
    sup = req.supervisor
    fname = f"permission_{req.id}_{uuid.uuid4().hex[:8]}.pdf"
    fpath = os.path.join(UPLOAD_FOLDER, fname)
    W, H  = A4

    styles = getSampleStyleSheet()
    DARK   = colors.HexColor("#1a1f3a")
    BLUE   = colors.HexColor("#2563eb")
    LIGHT  = colors.HexColor("#f0f4ff")
    GREY   = colors.HexColor("#f8f8f8")

    doc = SimpleDocTemplate(fpath, pagesize=A4,
                            rightMargin=1.8*cm, leftMargin=1.8*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)
    elements = []

    def info_row(label_ar, label_en, val):
        val_str = val or "—"
        is_ar_val = any("؀" <= c <= "ۿ" for c in str(val_str))
        return [
            Paragraph(ar(label_ar), ParagraphStyle("lbl", fontName=ARABIC_FONT, fontSize=9,
                      alignment=2, textColor=colors.HexColor("#555"))),
            Paragraph(label_en, ParagraphStyle("lble", fontName=LATIN_FONT, fontSize=7.5,
                      alignment=0, textColor=colors.HexColor("#999"))),
            Paragraph(ar(str(val_str)) if is_ar_val else str(val_str),
                      ParagraphStyle("val", fontName=ARABIC_FONT if is_ar_val else LATIN_FONT,
                      fontSize=10, alignment=2 if is_ar_val else 0, textColor=DARK)),
        ]

    # رأس
    logo_path = os.path.join(BASE_DIR, "static", "img", "logo.png")
    logo_cell = Paragraph("", styles["Normal"])
    if os.path.exists(logo_path):
        try:
            logo_cell = RLImage(logo_path, width=2.5*cm, height=2.5*cm)
        except Exception as _logo_err:
            app.logger.error("permission PDF: invalid logo file %s: %s", logo_path, _logo_err)
            logo_cell = Paragraph("", styles["Normal"])
    title_tbl = Table([[logo_cell,
        Table([[Paragraph(ar("نموذج استئذان"), ParagraphStyle("t1", fontName=ARABIC_FONT,
                              fontSize=16, alignment=1, textColor=DARK))],
               [Paragraph("PERMISSION FORM", ParagraphStyle("t2", fontName=LATIN_FONT,
                              fontSize=10, alignment=1, textColor=BLUE))]],
              colWidths=[W - 3.6*cm - 2.5*cm])]], colWidths=[2.5*cm, W - 3.6*cm - 2.5*cm])
    title_tbl.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                                   ("TOPPADDING",(0,0),(-1,-1),6),
                                   ("BOTTOMPADDING",(0,0),(-1,-1),6)]))
    elements.append(title_tbl)
    elements.append(HRFlowable(width="100%", thickness=2, color=BLUE, spaceAfter=10))

    rows = [
        info_row("اسم الموظف",   "Employee Name",  emp.name if emp else ""),
        info_row("رقم الموظف",   "Employee No.",   emp.emp_number if emp else ""),
        info_row("القسم",        "Department",     emp.department if emp else ""),
        info_row("المشرف",       "Supervisor",     sup.name if sup else ""),
        info_row("التاريخ",      "Date",           str(req.start_date or req.created_at.date())),
        info_row("السبب",        "Reason",         req.reason or ""),
    ]
    tbl = Table(rows, colWidths=[3.5*cm, 3*cm, 9*cm])
    tbl.setStyle(TableStyle([
        ("ROWBACKGROUNDS",(0,0),(-1,-1),[LIGHT, colors.white]),
        ("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#c0d0ff")),
        ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ]))
    elements.append(tbl)
    elements.append(Spacer(1, 0.6*cm))

    # توقيعات
    elements.append(Paragraph(ar("التوقيعات  |  Signatures"),
        ParagraphStyle("sec", fontName=ARABIC_FONT, fontSize=11, alignment=1,
                       textColor=BLUE, spaceAfter=4)))

    def sig_cell(sig_obj, label_ar, label_en, name_str=""):
        if sig_obj and sig_obj.signature:
            try:
                img_buf = io.BytesIO(decode_signature_image(sig_obj.signature))
                img     = RLImage(img_buf, width=4.5*cm, height=1.8*cm)
                return [img,
                        Paragraph(ar(sig_obj.employee_name or name_str),
                            ParagraphStyle("sn", fontName=ARABIC_FONT, fontSize=8.5, alignment=1, textColor=DARK)),
                        Paragraph(sig_obj.signed_at.strftime("%Y/%m/%d") if sig_obj.signed_at else "",
                            ParagraphStyle("sd", fontName=LATIN_FONT, fontSize=8, alignment=1,
                                           textColor=colors.HexColor("#777")))]
            except Exception:
                pass
        return [Spacer(1, 1.2*cm),
                Paragraph(ar(label_ar), ParagraphStyle("s_lbl", fontName=ARABIC_FONT, fontSize=8.5,
                             alignment=1, textColor=colors.HexColor("#aaa"))),
                Paragraph(label_en, ParagraphStyle("s_lble", fontName=LATIN_FONT, fontSize=7.5,
                             alignment=1, textColor=colors.HexColor("#ccc")))]

    emp_col = sig_cell(sig, "توقيع الموظف", "Employee Signature", emp.name if emp else "")
    sup_col = sig_cell(None, "توقيع المشرف", "Supervisor Signature")
    mgr_col = sig_cell(None, "توقيع الإدارة", "Management Signature")

    sig_tbl = Table([[emp_col, sup_col, mgr_col]], colWidths=[5.3*cm, 5.3*cm, 5.3*cm])
    sig_tbl.setStyle(TableStyle([
        ("BOX",(0,0),(0,0),1,BLUE),("BOX",(1,0),(1,0),1,colors.HexColor("#cccccc")),
        ("BOX",(2,0),(2,0),1,colors.HexColor("#cccccc")),
        ("ALIGN",(0,0),(-1,-1),"CENTER"),("VALIGN",(0,0),(-1,-1),"BOTTOM"),
        ("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),8),
        ("BACKGROUND",(0,0),(0,0),LIGHT),
    ]))
    elements.append(sig_tbl)
    elements.append(Spacer(1, 0.4*cm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    from datetime import datetime as dt_cls
    elements.append(Paragraph(
        f"Generated: {dt_cls.utcnow().strftime('%Y-%m-%d')}  |  NSH SafeTrack",
        ParagraphStyle("ft", fontName=LATIN_FONT, fontSize=7.5, alignment=1,
                       textColor=colors.HexColor("#aaaaaa"))))
    doc.build(elements)
    return fname


# ═══════════════════════════════════════════════════════════════
#  API Routes — كلها تبدأ بـ /api/
# ═══════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────
#  1. تسجيل الدخول
# ─────────────────────────────────────────────
# Rate limiting بسيط للـ login — بدون مكتبات خارجية
_login_attempts: dict = {}  # {ip: [timestamps]}
_LOGIN_MAX    = 10   # محاولات
_LOGIN_WINDOW = 60   # ثانية
_LOGIN_CLEANUP_INTERVAL = 300  # تنظيف كل 5 دقائق
_last_cleanup = 0.0

def _is_rate_limited(ip: str) -> bool:
    import time
    global _last_cleanup
    now = time.time()

    # تنظيف دوري لمنع تسرب الذاكرة
    if now - _last_cleanup > _LOGIN_CLEANUP_INTERVAL:
        stale_ips = [k for k, v in _login_attempts.items()
                     if all(now - t >= _LOGIN_WINDOW for t in v)]
        for k in stale_ips:
            del _login_attempts[k]
        _last_cleanup = now

    attempts = _login_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < _LOGIN_WINDOW]
    if len(attempts) >= _LOGIN_MAX:
        _login_attempts[ip] = attempts
        return True
    attempts.append(now)
    _login_attempts[ip] = attempts
    return False

@app.post("/api/register")
def api_register():
    """Public company registration from mobile app — creates pending company + admin user."""
    data     = request.get_json(force=True) or {}
    name     = (data.get("name") or "").strip()
    email    = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()
    if not name or not email or not password:
        return jsonify({"error": "جميع الحقول مطلوبة"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "البريد الإلكتروني مسجّل مسبقاً"}), 400
    import re, unicodedata
    slug_base = re.sub(r"[^a-z0-9]+", "-",
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    ).strip("-") or "co"
    slug = slug_base
    i = 1
    while Company.query.filter_by(slug=slug).first():
        slug = f"{slug_base}-{i}"; i += 1
    co = Company(name=name, slug=slug, plan="free", is_active=False, owner_email=email)
    db.session.add(co)
    db.session.flush()
    admin_u = User(
        name=name + " Admin",
        supervisor_code=str(co.id) + "adm",
        role="admin", is_active=True,
        company_id=co.id, email=email
    )
    admin_u.set_password(password)
    db.session.add(admin_u)
    db.session.commit()
    return jsonify({"ok": True, "company_id": co.id}), 201


@app.post("/api/login")
@_login_limit
def api_login():
    # فحص rate limiting
    client_ip = freq.headers.get("X-Forwarded-For", freq.remote_addr or "unknown").split(",")[0].strip()
    if _is_rate_limited(client_ip):
        return jsonify({"error": "Too many attempts, please wait"}), 429

    data = freq.get_json(force=True) or {}
    code         = (data.get("code") or "").strip()
    email        = (data.get("email") or "").strip().lower()
    password     = (data.get("password") or "").strip()
    device_token = (data.get("device_token") or "").strip()

    user = None
    if email:
        user = User.query.filter_by(email=email, is_active=True).first()
        if user and not user.check_password(password):
            return jsonify({"error": "Invalid email or password"}), 401
        if not user:
            return jsonify({"error": "Account not found or inactive"}), 401
    elif code:
        user = User.query.filter_by(supervisor_code=code, is_active=True).first()
        if not user:
            return jsonify({"error": "ID not found or inactive"}), 401
    else:
        return jsonify({"error": "email or code is required"}), 400

    # Check company active (skip for super_admin)
    if user.role != "super_admin" and user.company_id:
        co = db.session.get(Company, user.company_id)
        if co and not co.is_active:
            return jsonify({"error": "Company account pending approval"}), 403

    # حذف tokens القديمة لهذا المستخدم (أقدم من 30 يوم) لمنع التراكم
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    MobileToken.query.filter(
        MobileToken.user_id == user.id,
        MobileToken.created_at < cutoff
    ).delete(synchronize_session=False)

    # أنشئ توكن جلسة جديد
    token_str = secrets.token_hex(32)
    tok = MobileToken(user_id=user.id, token=token_str,
                      device_token=device_token or None,
                      company_id=user.company_id)
    db.session.add(tok)
    db.session.commit()

    co = db.session.get(Company, user.company_id) if user.company_id else None
    return jsonify({
        "token": token_str,
        "user": {
            "id":           user.id,
            "name":         user.name,
            "code":         user.supervisor_code,
            "role":         user.role,
            "hse_access":   is_hse_supervisor(user),
            "company_id":   user.company_id,
            "company_name": co.name if co else None,
            "ptw_training_active": bool(getattr(user, "ptw_training_active", False)),
        }
    })


# ─────────────────────────────────────────────
#  2. تسجيل الخروج
# ─────────────────────────────────────────────
@app.post("/api/logout")
@api_login_required
def api_logout():
    auth = freq.headers.get("Authorization", "")[7:]
    MobileToken.query.filter_by(token=auth).delete()
    db.session.commit()
    return jsonify({"message": "logged out"})


# ─────────────────────────────────────────────
#  3. بيانات المستخدم الحالي
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
#  badge count — لتحديث أيقونة التطبيق
# ─────────────────────────────────────────────
@app.get("/api/badge")
@api_login_required
def api_badge_count():
    u = get_api_user()
    cid = api_cid()
    if u.role == "admin":
        q = Request.query.filter_by(status="pending")
        if cid:
            q = q.filter_by(company_id=cid)
        count = q.count()
    elif u.role == "hr":
        q = HRTask.query.filter_by(status="pending")
        if cid:
            q = q.filter_by(company_id=cid)
        count = q.count()
    else:
        count = Request.query.filter_by(supervisor_id=u.id, status="pending").count()
    return jsonify({"badge": count})

@app.get("/api/me")
@api_login_required
def api_me():
    u = get_api_user()
    return jsonify({
        "id":   u.id,
        "name": u.name,
        "code": u.supervisor_code,
        "role": u.role,
    })


# ─────────────────────────────────────────────
#  4. قائمة الموظفين (للسوبرفايزر)
# ─────────────────────────────────────────────
@app.get("/api/employees")
@api_login_required
def api_employees():
    u = get_api_user()
    if u.role == "admin":
        emps = Employee.query.filter_by(is_active=True).order_by(Employee.name).all()
        return jsonify([{"id": e.id, "name": e.name, "emp_number": e.emp_number,
                         "department": e.department, "site": e.site} for e in emps])
    if u.role == "safety_supervisor":
        officers = _get_safety_officers(u)
        return jsonify([{"id": o.id, "name": o.name, "emp_number": o.supervisor_code,
                         "department": None, "site": None, "_is_officer": True} for o in officers])
    emps = Employee.query.filter_by(user_id=u.id, is_active=True).order_by(Employee.name).all()
    return jsonify([{"id": e.id, "name": e.name, "emp_number": e.emp_number,
                     "department": e.department, "site": e.site} for e in emps])


# ─────────────────────────────────────────────
#  5. ملخص موظف (حضور + متوسط تقييم)
# ─────────────────────────────────────────────
@app.get("/api/employees/<int:emp_id>/summary")
@api_login_required
def api_employee_summary(emp_id):
    u = get_api_user()
    emp = (Employee.query.get_or_404(emp_id) if u.role == "admin"
           else Employee.query.filter_by(id=emp_id, user_id=u.id).first_or_404())
    summary = get_employee_summary(emp_id)
    return jsonify({
        "employee": {"id": emp.id, "name": emp.name, "emp_number": emp.emp_number},
        "summary":  summary,
    })


# ─────────────────────────────────────────────
#  6. رفع طلب جديد (إجازة أو سكليف) مع PDF اختياري
# ─────────────────────────────────────────────
@app.post("/api/requests/new")
@api_login_required
def api_request_new():
    u = get_api_user()

    # البيانات تأتي إما JSON أو Form (لو فيه ملف)
    if freq.content_type and "multipart" in freq.content_type:
        emp_id     = freq.form.get("employee_id")
        rtype      = (freq.form.get("type") or "leave").strip().lower()
        reason     = (freq.form.get("reason") or "").strip()
        start_date = freq.form.get("start_date")
        end_date   = freq.form.get("end_date")
    else:
        data       = freq.get_json(force=True) or {}
        emp_id     = data.get("employee_id")
        rtype      = (data.get("type") or "leave").strip().lower()
        reason     = (data.get("reason") or "").strip()
        start_date = data.get("start_date")
        end_date   = data.get("end_date")

    # تحقق من الموظف / الأفسر
    try:
        emp_id = int(emp_id or 0)
    except Exception:
        emp_id = 0

    def _pd(s):
        try:
            return parse_date(s) if s else None
        except Exception:
            return None

    if u.role == "safety_supervisor":
        # emp_id here is a User.id (officer)
        valid_ids = {o.id for o in _get_safety_officers(u)}
        if emp_id not in valid_ids:
            return jsonify({"error": "Invalid officer"}), 400
        r = Request(
            supervisor_id=u.id,
            officer_user_id=emp_id,
            type=rtype if rtype in {"leave", "sick", "permission", "warning", "late", "other", "secondment"} else "other",
            start_date=_pd(start_date),
            end_date=_pd(end_date),
            reason=reason,
            status="pending",
            company_id=api_cid(),
        )
    else:
        emp = db.session.get(Employee, emp_id) if emp_id else None
        if not emp or (u.role != "admin" and emp.user_id != u.id):
            return jsonify({"error": "Invalid employee"}), 400
        r = Request(
            supervisor_id=u.id,
            employee_id=emp.id,
            type=rtype if rtype in {"leave", "sick", "permission", "warning", "late", "other", "secondment"} else "other",
            start_date=_pd(start_date),
            end_date=_pd(end_date),
            reason=reason,
            status="pending",
            company_id=api_cid(),
        )
    db.session.add(r)
    db.session.flush()   # نحتاج r.id قبل الحفظ النهائي

    # ─── رفع ملف PDF (إن وُجد) ───
    uploaded_file = freq.files.get("pdf_file")
    if uploaded_file and allowed_file(uploaded_file.filename):
        original_name = secure_filename(uploaded_file.filename)
        ext           = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "pdf"
        saved_name    = f"req_{r.id}_{uuid.uuid4().hex[:8]}.{ext}"
        uploaded_file.save(os.path.join(UPLOAD_FOLDER, saved_name))

        att = RequestAttachment(
            request_id=r.id,
            filename=saved_name,
            original_name=original_name,
        )
        db.session.add(att)

    db.session.commit()

    # ── إشعار لجميع الأدمن ──
    threading.Thread(target=_notify_admins_new_request,
                     args=(r.id, emp.name, u.name), daemon=True).start()

    return jsonify({
        "message":    "Request submitted",
        "request_id": r.id,
        "status":     r.status,
    }), 201


# ─────────────────────────────────────────────
#  7. طلباتي (السوبرفايزر يشوف طلباته)
# ─────────────────────────────────────────────
@app.get("/api/requests/mine")
@api_login_required
def api_requests_mine():
    u = get_api_user()
    from sqlalchemy.orm import joinedload
    reqs = (Request.query
            .options(joinedload(Request.employee), joinedload(Request.attachments))
            .filter_by(supervisor_id=u.id)
            .order_by(Request.created_at.desc())
            .all())

    result = []
    for r in reqs:
        emp = r.employee
        _task = None
        try:
            _task     = HRTask.query.filter_by(request_id=r.id).first()
            _official = _task.official_reason if _task and r.type == "warning" else None
        except Exception:
            _official = None
        # نموذج الإجازة
        _leave_form = LeaveForm.query.filter_by(request_id=r.id).first() if r.type == "leave" else None
        _leave_sig  = LeaveSignature.query.filter_by(request_id=r.id).first() if r.type == "leave" else None
        _perm_sig   = PermissionSignature.query.filter_by(request_id=r.id).first() if r.type == "permission" else None
        _warn_sig   = WarningSignature.query.filter_by(request_id=r.id).first() if r.type == "warning" else None
        result.append({
            "id":            r.id,
            "type":          r.type,
            "status":        r.status,
            "start_date":    str(r.start_date) if r.start_date else None,
            "end_date":      str(r.end_date)   if r.end_date   else None,
            "reason":        r.reason,
            "created_at":    r.created_at.isoformat() if r.created_at else None,
            "admin_comment": r.admin_comment,
            "employee": {
                "id":   emp.id   if emp else (r.officer_user_id if r.officer_user_id else None),
                "name": emp.name if emp else (r.officer_user.name if r.officer_user else None),
            },
            "has_attachment":  len(r.attachments) > 0,
            "official_reason": _official,
            # إجازة
            "has_leave_form":  _leave_form is not None,
            "leave_signed":    _leave_form.signed if _leave_form else False,
            "leave_pdf_url":   f"/api/leave-form/{r.id}/download" if _leave_form else None,
            # استئذان
            "permission_signed": _perm_sig is not None,
            # إنذار
            "warning_signed":    _warn_sig is not None,
            "warning_pdf_url":   f"/api/warning/{r.id}/download" if (_task and _task.warning_pdf) else None,
        })

    return jsonify(result)


# ─────────────────────────────────────────────
#  8. صندوق طلبات الأدمن/المانجر
# ─────────────────────────────────────────────
@app.get("/api/admin/requests")
@api_admin_required
def api_admin_requests():
    status = freq.args.get("status", "pending")
    q_str  = (freq.args.get("q") or "").strip()

    from sqlalchemy.orm import joinedload
    q = Request.query.options(
        joinedload(Request.employee),
        joinedload(Request.supervisor),
        joinedload(Request.attachments),
    )
    if status and status != "all":
        q = q.filter(Request.status == status)
    if q_str:
        q = (q.join(Employee, Request.employee_id == Employee.id)
               .filter(Employee.name.ilike(f"%{q_str}%")))

    reqs = q.order_by(Request.created_at.desc()).all()

    result = []
    for r in reqs:
        emp = r.employee
        sup = r.supervisor
        result.append({
            "id":           r.id,
            "type":         r.type,
            "status":       r.status,
            "start_date":   str(r.start_date) if r.start_date else None,
            "end_date":     str(r.end_date)   if r.end_date   else None,
            "reason":       r.reason,
            "created_at":   r.created_at.isoformat() if r.created_at else None,
            "admin_comment": r.admin_comment,
            "employee": {
                "id":         emp.id         if emp else None,
                "name":       emp.name       if emp else None,
                "emp_number": emp.emp_number if emp else None,
                "department": emp.department if emp else None,
            },
            "supervisor": {
                "id":   sup.id   if sup else None,
                "name": sup.name if sup else None,
            },
            "has_attachment": len(r.attachments) > 0,
        })

    return jsonify(result)


# ─────────────────────────────────────────────
#  9. قرار الأدمن على الطلب (قبول / رفض)
# ─────────────────────────────────────────────
@app.post("/api/admin/requests/<int:req_id>/decide")
@api_admin_required
def api_request_decide(req_id):
    r = Request.query.get_or_404(req_id)
    data     = freq.get_json(force=True) or {}
    decision = data.get("decision")   # "approve" أو "reject"
    comment  = (data.get("comment") or "").strip()

    # نقبل approve/approved و reject/rejected
    if decision in ("approved",): decision = "approve"
    if decision in ("rejected",): decision = "reject"
    if decision not in ("approve", "reject"):
        return jsonify({"error": "decision must be approve or reject"}), 400

    u = get_api_user()
    r.status       = "approved" if decision == "approve" else "rejected"
    r.decided_by   = u.id
    r.decided_at   = datetime.now(timezone.utc)
    r.admin_comment = comment

    leave_pdf_url = None

    if decision == "approve":
        # إجازة → تسجيل حضور + نموذج PDF
        if r.type == "leave":
            _apply_leave_attendance_for_request(r)
            existing_form = LeaveForm.query.filter_by(request_id=r.id).first()
            if not existing_form:
                pdf_name = generate_leave_pdf(r)
                if pdf_name:
                    lf = LeaveForm(request_id=r.id, filename=pdf_name)
                    db.session.add(lf)
                    leave_pdf_url = f"/api/leave-form/{r.id}/download"

        # سكليف → تسجيل غياب تلقائي
        if r.type == "sick":
            _apply_sick_attendance_for_request(r)

        # إنشاء مهمة HR لجميع الأنواع
        existing = HRTask.query.filter_by(request_id=r.id).first()
        if not existing:
            db.session.add(HRTask(
                request_id=r.id,
                employee_id=r.employee_id,
                type=r.type or "leave",
                status="pending",
                company_id=r.company_id,
            ))

    db.session.commit()

    # ─── إشعار للسوبرفايزر ───
    type_labels = {"leave":"إجازة","sick":"سكليف","permission":"استئذان",
                   "warning":"إنذار","secondment":"إعارة","late":"تأخر"}
    type_ar = type_labels.get(r.type, r.type or "")
    _send_push_to_user(r.supervisor_id,
                       title="تحديث حالة الطلب",
                       body=f"طلب {type_ar} للموظف {r.employee.name if r.employee else ''} — {'مقبول' if r.status=='approved' else 'مرفوض'}")

    return jsonify({
        "message":       "Decision recorded",
        "status":        r.status,
        "leave_pdf_url": leave_pdf_url,
    })


# ─────────────────────────────────────────────
#  10. تحميل مرفق PDF الطلب
# ─────────────────────────────────────────────
@app.get("/api/requests/<int:req_id>/attachment")
@api_login_required
def api_request_attachment(req_id):
    from flask import send_from_directory
    r   = Request.query.get_or_404(req_id)
    att = RequestAttachment.query.filter_by(request_id=req_id).first()
    if not att:
        return jsonify({"error": "No attachment found"}), 404
    return send_from_directory(UPLOAD_FOLDER, att.filename,
                               as_attachment=True,
                               download_name=att.original_name or att.filename)


# ─────────────────────────────────────────────
#  11. تحميل نموذج الإجازة (للسوبرفايزر)
# ─────────────────────────────────────────────
@app.get("/api/leave-form/<int:req_id>/download")
@api_login_required
def api_leave_form_download(req_id):
    from flask import send_from_directory
    lf = LeaveForm.query.filter_by(request_id=req_id).first()
    if not lf:
        return jsonify({"error": "Leave form not generated yet"}), 404
    return send_from_directory(UPLOAD_FOLDER, lf.filename,
                               as_attachment=True,
                               download_name=f"leave_form_{req_id}.pdf")


# ─────────────────────────────────────────────
#  12. صندوق الموارد البشرية
# ─────────────────────────────────────────────
@app.get("/api/hr/inbox")
@api_hr_required
def api_hr_inbox():
    from sqlalchemy.orm import joinedload
    tasks = (HRTask.query
             .options(joinedload(HRTask.employee), joinedload(HRTask.request))
             .order_by(HRTask.created_at.desc()).all())
    result = []
    for t in tasks:
        r   = t.request
        emp = t.employee
        try:
            _warning_pdf     = t.warning_pdf
            _official_reason = t.official_reason
        except Exception:
            _warning_pdf     = None
            _official_reason = None
        _has_attachment = (r and r.type == "sick" and
                           RequestAttachment.query.filter_by(request_id=r.id).first() is not None)
        result.append({
            "id":         t.id,
            "type":       t.type,
            "status":     t.status,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "applied_at": t.applied_at.isoformat() if t.applied_at else None,
            "employee": {
                "id":         emp.id         if emp else None,
                "name":       emp.name       if emp else None,
                "emp_number": emp.emp_number if emp else None,
            },
            "request": {
                "id":         r.id         if r else None,
                "start_date": str(r.start_date) if r and r.start_date else None,
                "end_date":   str(r.end_date)   if r and r.end_date   else None,
                "reason":     r.reason          if r else None,
            },
            "has_leave_form": (r and r.type == "leave" and
                               LeaveForm.query.filter_by(request_id=r.id).first() is not None)
                               if r else False,
            "warning_pdf_url":        f"/api/warning/{r.id}/download" if (r and _warning_pdf) else None,
            "official_reason":        _official_reason,
            "supervisor_description": r.reason if r and t.type == "warning" else None,
            "reason_set":             _official_reason is not None,
            "has_attachment":         _has_attachment,
            "attachment_url":         (f"/api/requests/{r.id}/attachment" if (_has_attachment and r) else None),
        })
    return jsonify(result)


# ─────────────────────────────────────────────
#  HR يختار السبب الرسمي للإنذار
# ─────────────────────────────────────────────
@app.post("/api/hr/tasks/<int:task_id>/set-reason")
@api_hr_required
def api_hr_set_warning_reason(task_id):
    t    = HRTask.query.get_or_404(task_id)
    data = freq.get_json(force=True) or {}
    reason = (data.get("official_reason") or "").strip()

    if not reason:
        return jsonify(error="السبب مطلوب"), 400
    if t.type != "warning":
        return jsonify(error="هذه المهمة ليست إنذاراً"), 400

    try:
        t.official_reason = reason
    except Exception:
        from sqlalchemy import text as _text
        with db.engine.begin() as _conn:
            _conn.execute(_text("ALTER TABLE hr_task ADD COLUMN IF NOT EXISTS official_reason TEXT NULL"))
            _conn.execute(_text("ALTER TABLE hr_task ADD COLUMN IF NOT EXISTS warning_pdf VARCHAR(255) NULL"))
        t.official_reason = reason
    # لا نحدث request.reason — يبقى وصف المشرف كما هو
    db.session.commit()

    # إشعار للمشرف
    if t.request:
        threading.Thread(
            target=_send_push_to_user,
            args=(t.request.supervisor_id,
                  "إنذار جاهز للتوقيع",
                  f"نموذج إنذار الموظف {t.employee.name if t.employee else ''} جاهز"),
            daemon=True
        ).start()

    return jsonify(message="تم إرسال السبب الرسمي للمشرف", request_id=t.request_id), 200


# ─────────────────────────────────────────────
#  13. تنفيذ مهمة HR (تحويل إلى "تم التنفيذ")
# ─────────────────────────────────────────────
@app.post("/api/hr/tasks/<int:task_id>/apply")
@api_hr_required
def api_hr_task_apply(task_id):
    t = HRTask.query.get_or_404(task_id)
    if t.status == "pending":
        u = get_api_user()
        t.status     = "applied"
        t.applied_at = datetime.now(timezone.utc)
        t.applied_by = u.id
        db.session.commit()

        # إشعار للسوبرفايزر
        if t.request:
            _send_push_to_user(t.request.supervisor_id,
                               title="تم تنفيذ الطلب",
                               body=f"تم تنفيذ طلب {t.type} للموظف {t.employee.name if t.employee else ''}")

    return jsonify({"message": "Task applied", "status": t.status})


# ─────────────────────────────────────────────
#  14. لوحة KPI للأدمن (ملخص سريع)
# ─────────────────────────────────────────────
@app.get("/api/admin/kpi")
@api_admin_required
def api_admin_kpi():
    ws, we = default_week_today()
    ws_str = freq.args.get("week_start")
    we_str = freq.args.get("week_end")
    if ws_str and we_str:
        try:
            ws = parse_date(ws_str)
            we = parse_date(we_str)
        except Exception:
            pass

    pws, pwe = previous_week_range(ws)

    # ── إحصائيات أساسية ──
    total_employees  = Employee.query.filter_by(is_active=True).count()
    total_evals_week = Evaluation.query.filter_by(week_start=ws, week_end=we).count()
    total_evals_prev = Evaluation.query.filter_by(week_start=pws, week_end=pwe).count()
    pending_requests = Request.query.filter_by(status="pending").count()
    hr_pending       = HRTask.query.filter_by(status="pending").count()

    # ── تغطية الموظفين ──
    sum_target_emp = Employee.query.filter_by(is_active=True).count()
    sum_done_emp   = db.session.query(func.count(func.distinct(Evaluation.employee_id)))\
        .filter(Evaluation.week_start == ws, Evaluation.week_end == we).scalar() or 0
    emp_coverage = round(sum_done_emp / sum_target_emp * 100.0, 1) if sum_target_emp else 0.0

    # ── معدل الحضور ──
    active_emp_ids = [e.id for e in Employee.query.filter_by(is_active=True).with_entities(Employee.id).all()]
    workdays = (we - ws).days + 1
    total_opp = len(active_emp_ids) * workdays

    present_count = db.session.query(func.count(Attendance.id)).filter(
        Attendance.date >= ws, Attendance.date <= we,
        Attendance.employee_id.in_(active_emp_ids),
        Attendance.status == 'present'
    ).scalar() or 0

    leave_count = db.session.query(func.count(Attendance.id)).filter(
        Attendance.date >= ws, Attendance.date <= we,
        Attendance.employee_id.in_(active_emp_ids),
        Attendance.status == 'leave'
    ).scalar() or 0

    att_rate   = round(present_count / total_opp * 100.0, 1) if total_opp else 0.0
    leave_rate = round(leave_count   / total_opp * 100.0, 1) if total_opp else 0.0

    return jsonify({
        "week_start":       ws.isoformat(),
        "week_end":         we.isoformat(),
        "total_employees":  total_employees,
        "evals_this_week":  total_evals_week,
        "evals_last_week":  total_evals_prev,
        "pending_requests": pending_requests,
        "hr_pending_tasks": hr_pending,
        "emp_coverage":     emp_coverage,
        "att_rate":         att_rate,
        "leave_rate":       leave_rate,
        "present_count":    present_count,
        "leave_count":      leave_count,
    })



# ─────────────────────────────────────────────
#  مقارنة المشرفين
# ─────────────────────────────────────────────
@app.get("/api/admin/supervisors/ranking")
@api_admin_required
def api_supervisors_ranking():
    ws, we = default_week_today()
    ws_str = freq.args.get("week_start")
    we_str = freq.args.get("week_end")
    if ws_str and we_str:
        try: ws = parse_date(ws_str); we = parse_date(we_str)
        except: pass

    pws, pwe = previous_week_range(ws)

    supervisors = User.query.filter(
        User.role == "supervisor",
        User.is_active == True,
        db.or_(User.is_hidden == False, User.is_hidden == None)
    ).order_by(User.supervisor_code.asc()).all()

    emp_totals = dict(
        db.session.query(Employee.user_id, func.count(Employee.id))
        .filter(Employee.is_active == True)
        .group_by(Employee.user_id).all()
    )
    emp_done_this = dict(
        db.session.query(Employee.user_id, func.count(Evaluation.id))
        .join(Evaluation, Evaluation.employee_id == Employee.id)
        .filter(Evaluation.week_start == ws, Evaluation.week_end == we)
        .group_by(Employee.user_id).all()
    )
    emp_done_prev = dict(
        db.session.query(Employee.user_id, func.count(Evaluation.id))
        .join(Evaluation, Evaluation.employee_id == Employee.id)
        .filter(Evaluation.week_start == pws, Evaluation.week_end == pwe)
        .group_by(Employee.user_id).all()
    )
    avg_scores = dict(
        db.session.query(Employee.user_id, func.avg(Evaluation.total_score))
        .join(Evaluation, Evaluation.employee_id == Employee.id)
        .filter(Evaluation.week_start == ws, Evaluation.week_end == we)
        .group_by(Employee.user_id).all()
    )

    rows = []
    for sup in supervisors:
        target   = emp_totals.get(sup.id, 0)
        done_c   = emp_done_this.get(sup.id, 0)
        done_p   = emp_done_prev.get(sup.id, 0)
        avg      = avg_scores.get(sup.id)
        coverage = round(done_c / target * 100.0, 1) if target else 0.0
        delta    = done_c - done_p
        trend    = "up" if delta > 0 else "down" if delta < 0 else "flat"
        rows.append({
            "id":         sup.id,
            "name":       sup.name,
            "code":       sup.supervisor_code,
            "target":     target,
            "done":       done_c,
            "done_prev":  done_p,
            "coverage":   coverage,
            "avg_score":  round(float(avg), 1) if avg else None,
            "delta":      delta,
            "trend":      trend,
        })

    rows.sort(key=lambda x: x["coverage"], reverse=True)
    return jsonify({"week_start": ws.isoformat(), "week_end": we.isoformat(), "rows": rows})


# ─────────────────────────────────────────────
#  تقرير مشرف محدد
# ─────────────────────────────────────────────
@app.get("/api/admin/supervisors/<int:sup_id>/report")
@api_admin_required
def api_supervisor_report(sup_id):
    sup = User.query.get_or_404(sup_id)
    ws, we = default_week_today()
    ws_str = freq.args.get("week_start")
    we_str = freq.args.get("week_end")
    if ws_str and we_str:
        try: ws = parse_date(ws_str); we = parse_date(we_str)
        except: pass

    pws, pwe = previous_week_range(ws)

    # موظفو المشرف
    employees = Employee.query.filter_by(user_id=sup_id, is_active=True).order_by(Employee.name).all()
    emp_ids   = [e.id for e in employees]

    # تقييمات الأسبوع
    evals = {ev.employee_id: ev for ev in
             Evaluation.query.filter(
                 Evaluation.employee_id.in_(emp_ids),
                 Evaluation.week_start == ws,
                 Evaluation.week_end   == we).all()}

    # حضور الأسبوع
    att_records = Attendance.query.filter(
        Attendance.employee_id.in_(emp_ids),
        Attendance.date >= ws,
        Attendance.date <= we).all()
    att_map = {}
    for a in att_records:
        att_map.setdefault(a.employee_id, []).append({
            "date": str(a.date), "status": a.status})

    # إحصائيات سريعة
    total      = len(employees)
    evaluated  = len(evals)
    avg_score  = round(sum(ev.total_score or 0 for ev in evals.values()) / evaluated, 1) if evaluated else None
    present_cnt = sum(1 for a in att_records if a.status == "present")

    emp_rows = []
    for e in employees:
        ev = evals.get(e.id)
        emp_rows.append({
            "emp_id":     e.id,
            "emp_number": e.emp_number,
            "name":       e.name,
            "department": e.department,
            "evaluated":  ev is not None,
            "total":      ev.total_score  if ev else None,
            "band":       ev.overall_band if ev else None,
            "att":        att_map.get(e.id, []),
        })

    return jsonify({
        "supervisor":  {"id": sup.id, "name": sup.name, "code": sup.supervisor_code},
        "week_start":  ws.isoformat(),
        "week_end":    we.isoformat(),
        "total_emp":   total,
        "evaluated":   evaluated,
        "avg_score":   avg_score,
        "present_cnt": present_cnt,
        "employees":   emp_rows,
    })



@app.get("/api/weekly-eval/<int:ev_id>")
@api_login_required
def api_weekly_eval_detail(ev_id):
    u  = get_api_user()
    ev = Evaluation.query.get_or_404(ev_id)
    emp = Employee.query.get_or_404(ev.employee_id)
    if u.role != "admin" and emp.user_id != u.id:
        return jsonify({"error": "Forbidden"}), 403
    return jsonify({
        "t1_text": ev.t1_text, "t1_percent": ev.t1_percent, "t1_remarks": ev.t1_remarks,
        "t2_text": ev.t2_text, "t2_percent": ev.t2_percent, "t2_remarks": ev.t2_remarks,
        "t3_text": ev.t3_text, "t3_percent": ev.t3_percent, "t3_remarks": ev.t3_remarks,
        "t4_text": ev.t4_text, "t4_percent": ev.t4_percent, "t4_remarks": ev.t4_remarks,
        "p_punctuality":   ev.p_punctuality,   "c_punctuality":   ev.c_punctuality,
        "p_quality":       ev.p_quality,       "c_quality":       ev.c_quality,
        "p_productivity":  ev.p_productivity,  "c_productivity":  ev.c_productivity,
        "p_communication": ev.p_communication, "c_communication": ev.c_communication,
        "p_problemsolving":ev.p_problemsolving,"c_problemsolving":ev.c_problemsolving,
        "p_compliance":    ev.p_compliance,    "c_compliance":    ev.c_compliance,
        "strengths":       ev.strengths,
        "improvements":    ev.improvements,
        "training_needed": ev.training_needed,
        "week_start":      ev.week_start.isoformat(),
        "week_end":        ev.week_end.isoformat(),
    })

# ─────────────────────────────────────────────
#  تعديل التقييم الأسبوعي
# ─────────────────────────────────────────────
@app.put("/api/weekly-eval/<int:ev_id>")
@api_login_required
def api_weekly_eval_edit(ev_id):
    u  = get_api_user()
    ev = Evaluation.query.get_or_404(ev_id)
    emp = Employee.query.get_or_404(ev.employee_id)

    if u.role != "admin" and emp.user_id != u.id:
        return jsonify({"error": "Forbidden"}), 403

    data = freq.get_json(force=True) or {}

    # تحقق من الأسبوع إذا تغيّر
    ws_str = data.get("week_start")
    we_str = data.get("week_end")
    if ws_str and we_str:
        try:
            ws = parse_date(ws_str)
            we = parse_date(we_str)
        except:
            return jsonify({"error": "Invalid dates"}), 400
        ok, msg = validate_week_sun_to_thu(ws, we)
        if not ok:
            return jsonify({"error": msg}), 400
        dup = Evaluation.query.filter(
            Evaluation.employee_id == emp.id,
            Evaluation.week_start  == ws,
            Evaluation.week_end    == we,
            Evaluation.id          != ev.id).first()
        if dup:
            return jsonify({"error": "تقييم لهذا الأسبوع موجود مسبقاً"}), 409
        ev.week_start = ws
        ev.week_end   = we

    ev.t1_text       = data.get("t1_text",       ev.t1_text)
    ev.t2_text       = data.get("t2_text",       ev.t2_text)
    ev.t3_text       = data.get("t3_text",       ev.t3_text)
    ev.t4_text       = data.get("t4_text",       ev.t4_text)
    ev.t1_percent    = data.get("t1_percent",    ev.t1_percent)
    ev.t2_percent    = data.get("t2_percent",    ev.t2_percent)
    ev.t3_percent    = data.get("t3_percent",    ev.t3_percent)
    ev.t4_percent    = data.get("t4_percent",    ev.t4_percent)
    ev.t1_remarks    = data.get("t1_remarks",    ev.t1_remarks)
    ev.t2_remarks    = data.get("t2_remarks",    ev.t2_remarks)
    ev.t3_remarks    = data.get("t3_remarks",    ev.t3_remarks)
    ev.t4_remarks    = data.get("t4_remarks",    ev.t4_remarks)
    ev.p_punctuality   = data.get("p_punctuality",   ev.p_punctuality)
    ev.p_quality       = data.get("p_quality",       ev.p_quality)
    ev.p_productivity  = data.get("p_productivity",  ev.p_productivity)
    ev.p_communication = data.get("p_communication", ev.p_communication)
    ev.p_problemsolving= data.get("p_problemsolving",ev.p_problemsolving)
    ev.p_compliance    = data.get("p_compliance",    ev.p_compliance)
    ev.c_punctuality   = data.get("c_punctuality",   ev.c_punctuality)
    ev.c_quality       = data.get("c_quality",       ev.c_quality)
    ev.c_productivity  = data.get("c_productivity",  ev.c_productivity)
    ev.c_communication = data.get("c_communication", ev.c_communication)
    ev.c_problemsolving= data.get("c_problemsolving",ev.c_problemsolving)
    ev.c_compliance    = data.get("c_compliance",    ev.c_compliance)
    ev.strengths       = data.get("strengths",       ev.strengths)
    ev.improvements    = data.get("improvements",    ev.improvements)
    ev.training_needed = data.get("training_needed", ev.training_needed)

    compute_scores(ev)
    db.session.commit()
    return jsonify({"message": "Updated", "total": ev.total_score, "band": ev.overall_band})

# ─────────────────────────────────────────────
#  15. تسجيل توكن الإشعارات (APNs)
# ─────────────────────────────────────────────
@app.post("/api/device-token")
@api_login_required
def api_register_device_token():
    u    = get_api_user()
    data = freq.get_json(force=True) or {}
    device_token = (data.get("device_token") or "").strip()
    if not device_token:
        return jsonify({"error": "device_token required"}), 400

    auth_token = freq.headers.get("Authorization", "")[7:]
    tok = MobileToken.query.filter_by(token=auth_token).first()
    if tok:
        tok.device_token = device_token
        db.session.commit()

    return jsonify({"message": "Device token registered"})


# ─────────────────────────────────────────────
#  مساعد إشعار الأدمن عند طلب جديد
# ─────────────────────────────────────────────
def _notify_admins_new_request(req_id: int, emp_name: str, sup_name: str):
    with app.app_context():
        admins = User.query.filter_by(role="admin", is_active=True).all()
        for admin in admins:
            _send_push_to_user(
                admin.id,
                title="طلب جديد 📋",
                body=f"{sup_name} رفع طلباً للموظف {emp_name}",
            )


# ─────────────────────────────────────────────
#  مساعد إرسال الإشعارات (APNs)
#  — يحتاج مكتبة: pip install httpx PyJWT
# ─────────────────────────────────────────────
def _send_push_to_user(user_id: int, title: str, body: str, badge: int = None):
    with app.app_context():
        try:
            import jwt as pyjwt
            import httpx
            KEY_ID    = os.getenv("APNS_KEY_ID")    or "69QBYQL8PG"
            TEAM_ID   = os.getenv("APNS_TEAM_ID")   or "76JS5SQN7L"
            BUNDLE_ID = os.getenv("APNS_BUNDLE_ID") or "com.nsh.zNSH"
            KEY_PATH  = os.getenv("APNS_KEY_PATH")  or os.path.join(BASE_DIR, "keys", "AuthKey_69QBYQL8PG.p8")
            if not os.path.exists(KEY_PATH):
                app.logger.error("APNs: ملف .p8 غير موجود في %s", KEY_PATH)
                return
            with open(KEY_PATH, "r") as f:
                private_key = f.read()
            token = pyjwt.encode(
                {"iss": TEAM_ID, "iat": datetime.now(timezone.utc)},
                private_key,
                algorithm="ES256",
                headers={"kid": KEY_ID},
            )
            device_tokens = [
                t.device_token for t in
                MobileToken.query.filter_by(user_id=user_id).all()
                if t.device_token
            ]
            app.logger.info("APNs: إرسال لـ user_id=%s، عدد الأجهزة=%s", user_id, len(device_tokens))
            if not device_tokens:
                app.logger.warning("APNs: لا يوجد device_token مسجل للمستخدم %s", user_id)
                return
            # حساب الـ badge الفعلي حسب دور المستخدم
            if badge is None:
                try:
                    u = db.session.get(User, user_id)
                    if u and u.role == "admin":
                        badge = Request.query.filter_by(status="pending").count()
                    elif u and u.role == "hr":
                        badge = HRTask.query.filter_by(status="pending").count()
                    elif u:
                        badge = Request.query.filter_by(supervisor_id=user_id, status="pending").count()
                    else:
                        badge = 1
                except Exception:
                    badge = 1

            for dt in device_tokens:
                payload = json.dumps({
                    "aps": {
                        "alert": {"title": title, "body": body},
                        "sound": "default",
                        "badge": badge,
                    }
                }, ensure_ascii=False)
                hdrs = {
                    "authorization": f"bearer {token}",
                    "apns-topic":    BUNDLE_ID,
                    "apns-push-type": "alert",
                    "content-type":  "application/json",
                }
                url = f"https://api.push.apple.com/3/device/{dt}"
                with httpx.Client(http2=True, timeout=5) as client:
                    resp = client.post(url, content=payload.encode("utf-8"), headers=hdrs)
                status = resp.status_code
                try:
                    reason = resp.json().get("reason", "")
                except Exception:
                    reason = ""
                app.logger.info("APNs: status=%s reason=%s device=%s", status, reason, dt[:20])
                if status != 200:
                    app.logger.warning("APNs: failed device=%s status=%s reason=%s", dt[:20], status, reason)
        except Exception as e:
            app.logger.error("APNs error: %s", e, exc_info=True)


# ─────────────────────────────────────────────
#  16. قائمة طلبات السوبرفايزر كاملة (للأدمن يشوف)
# ─────────────────────────────────────────────
@app.get("/api/admin/requests/supervisor/<int:sup_id>")
@api_admin_required
def api_admin_supervisor_requests(sup_id):
    reqs = (Request.query
            .filter_by(supervisor_id=sup_id)
            .order_by(Request.created_at.desc())
            .all())
    result = []
    for r in reqs:
        emp = r.employee
        result.append({
            "id":         r.id,
            "type":       r.type,
            "status":     r.status,
            "start_date": str(r.start_date) if r.start_date else None,
            "end_date":   str(r.end_date)   if r.end_date   else None,
            "reason":     r.reason,
            "employee":   {"id": emp.id, "name": emp.name} if emp else None,
        })
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════
#  نهاية ملف التعديلات
# ═══════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────
#  17. Allowed Users — قائمة المستخدمين
# ─────────────────────────────────────────────
@app.get("/api/admin/users")
@api_admin_required
def api_admin_users():
    cid = api_cid()
    q = User.query
    if cid:
        q = q.filter_by(company_id=cid)
    users = q.order_by(User.id.asc()).all()
    return jsonify([{
        "id":        u.id,
        "name":      u.name,
        "code":      u.supervisor_code,
        "role":      u.role,
        "is_active": u.is_active,
        "is_hidden": bool(u.is_hidden) if u.is_hidden is not None else False,
    } for u in users])


# ─────────────────────────────────────────────
#  18. Allowed Users — إضافة مستخدم
# ─────────────────────────────────────────────
@app.post("/api/admin/users/add")
@api_admin_required
def api_admin_users_add():
    data = freq.get_json(force=True) or {}

    code = (data.get("code") or "").strip()
    name = (data.get("name") or "").strip()
    role = (data.get("role") or "supervisor").strip().lower()

    if not code:
        return jsonify({"error": "code is required"}), 400

    # التحقق إن الاسم إنجليزي فقط
    if name and not all(ord(c) < 128 for c in name):
        return jsonify({"error": "الاسم يجب أن يكون بالإنجليزية فقط"}), 400

    if role not in {"admin", "hr", "supervisor"}:
        return jsonify({"error": "invalid role"}), 400

    existing = User.query.filter_by(supervisor_code=code).first()
    if existing:
        return jsonify({"error": "user code already exists"}), 400

    u = User(
        supervisor_code=code,
        name=name or code,
        role=role,
        is_active=True,
    )
    db.session.add(u)
    db.session.commit()

    return jsonify({
        "message": "user added",
        "user": {
            "id": u.id,
            "name": u.name,
            "code": u.supervisor_code,
            "role": u.role,
            "is_active": u.is_active,
        }
    }), 201


# ─────────────────────────────────────────────
#  19. Allowed Users — تعطيل مستخدم
# ─────────────────────────────────────────────
@app.post("/api/admin/users/<int:user_id>/deactivate")
@api_admin_required
def api_admin_users_deactivate(user_id):
    u = User.query.get_or_404(user_id)

    if u.role == "admin" and User.query.filter_by(role="admin", is_active=True).count() <= 1:
        return jsonify({"error": "cannot deactivate the last active admin"}), 400

    u.is_active = False
    db.session.commit()

    return jsonify({"message": "user deactivated", "id": u.id, "is_active": u.is_active})


# ─────────────────────────────────────────────
#  إخفاء/إظهار مستخدم
# ─────────────────────────────────────────────
@app.post("/api/admin/users/<int:user_id>/hide")
@api_admin_required
def api_admin_users_hide(user_id):
    u = User.query.get_or_404(user_id)
    if u.role == "admin":
        return jsonify({"error": "Cannot hide admin"}), 400
    u.is_hidden = not (u.is_hidden or False)
    db.session.commit()
    return jsonify({"message": "updated", "is_hidden": u.is_hidden})



# ─────────────────────────────────────────────
#  استقالة مشرف — يُعطَّل ويتحول موظفوه لـ unassigned
#  POST /api/admin/users/:id/resign
# ─────────────────────────────────────────────
@app.post("/api/admin/users/<int:user_id>/resign")
@api_admin_required
def api_admin_users_resign(user_id):
    u = User.query.get_or_404(user_id)

    # لا يمكن تطبيق الاستقالة على admin
    if u.role == "admin":
        return jsonify({"error": "لا يمكن تطبيق الاستقالة على حساب أدمن"}), 400

    # تعطيل الحساب وإخفاؤه
    u.is_active = False
    u.is_hidden = True

    # تحويل موظفيه لـ unassigned
    employees = Employee.query.filter_by(user_id=user_id, is_active=True).all()
    count = len(employees)
    for emp in employees:
        emp.user_id = None
        emp.status  = "unassigned"

    db.session.commit()

    app.logger.info("استقالة مشرف: user_id=%s name=%s — %s موظف تحول لـ unassigned",
                    user_id, u.name, count)

    return jsonify({
        "message":            f"تم تسجيل استقالة {u.name}",
        "user_id":            u.id,
        "employees_unassigned": count,
    })

# ─────────────────────────────────────────────
#  21. Supervisor Reports — أسبوعي مع فلتر
# ─────────────────────────────────────────────
@app.get("/api/admin/reports/supervisors")
@api_admin_required
def api_admin_reports_supervisors():
    ws, we = default_week_today()

    ws_str = freq.args.get("week_start")
    we_str = freq.args.get("week_end")
    sup_code = (freq.args.get("supervisor_code") or "").strip()

    if ws_str and we_str:
        try:
            ws = parse_date(ws_str)
            we = parse_date(we_str)
        except Exception:
            return jsonify({"error": "invalid week_start/week_end"}), 400

    q = (Evaluation.query
         .join(Employee, Evaluation.employee_id == Employee.id)
         .join(User, Employee.user_id == User.id)
         .filter(Evaluation.week_start == ws, Evaluation.week_end == we))

    if sup_code:
        q = q.filter(User.supervisor_code == sup_code)

    rows = q.order_by(User.name.asc(), Employee.name.asc()).all()

    result = []
    for r in rows:
        emp = r.employee
        sup = emp.user if emp else None

        result.append({
            "id": getattr(r, "id", None),
            "week_start": str(r.week_start) if r.week_start else None,
            "week_end": str(r.week_end) if r.week_end else None,
            "supervisor": {
                "id": sup.id if sup else None,
                "name": sup.name if sup else None,
                "code": sup.supervisor_code if sup else None,
            },
            "employee": {
                "id": emp.id if emp else None,
                "name": emp.name if emp else None,
                "emp_number": emp.emp_number if emp else None,
                "department": emp.department if emp else None,
                "site": emp.site if emp else None,
            },
            "targets": r.targets_score,
            "perf":    r.perf_score,
            "total":   r.total_score,
            "band":    r.overall_band,
        })

    return jsonify(result)


# ─────────────────────────────────────────────
#  22. Attendance Admin — يومي / أسبوعي
# ─────────────────────────────────────────────
@app.get("/api/admin/attendance")
@api_admin_required
def api_admin_attendance():
    mode = (freq.args.get("mode") or "day").strip().lower()
    date_str = freq.args.get("date")
    week_start_str = freq.args.get("week_start")
    week_end_str = freq.args.get("week_end")

    result = {
        "mode": mode,
        "summary": {},
        "records": []
    }

    if mode == "week":
        ws, we = default_week_today()
        if week_start_str and week_end_str:
            try:
                ws = parse_date(week_start_str)
                we = parse_date(week_end_str)
            except Exception:
                return jsonify({"error": "invalid week range"}), 400

        rows = (Attendance.query
                .join(Employee, Attendance.employee_id == Employee.id)
                .join(User, Employee.user_id == User.id, isouter=True)
                .filter(Attendance.date >= ws, Attendance.date <= we)
                .order_by(Attendance.date.desc(), Employee.name.asc())
                .all())

        present = 0
        absent = 0
        leave = 0

        for a in rows:
            status = (a.status or "").lower()
            if status == "present":
                present += 1
            elif status == "absent":
                absent += 1
            elif status == "leave":
                leave += 1

            emp = a.employee
            sup = emp.user if emp else None

            result["records"].append({
                "id": a.id,
                "date": str(a.date) if a.date else None,
                "employee": {
                    "id": emp.id if emp else None,
                    "name": emp.name if emp else None,
                    "emp_number": emp.emp_number if emp else None,
                    "department": emp.department if emp else None,
                    "site": emp.site if emp else None,
                },
                "supervisor": {
                    "id": sup.id if sup else None,
                    "name": sup.name if sup else None,
                    "code": sup.supervisor_code if sup else None,
                },
                "status": a.status,
                "remarks": getattr(a, "remarks", None),
            })

        result["summary"] = {
            "week_start": str(ws),
            "week_end": str(we),
            "present": present,
            "absent": absent,
            "leave": leave,
            "count": len(rows),
        }
        return jsonify(result)

    # day mode
    if not date_str:
        date_str = date.today().isoformat()

    try:
        d = parse_date(date_str)
    except Exception:
        return jsonify({"error": "invalid date"}), 400

    rows = (Attendance.query
            .join(Employee, Attendance.employee_id == Employee.id)
            .join(User, Employee.user_id == User.id, isouter=True)
            .filter(Attendance.date == d)
            .order_by(Employee.name.asc())
            .all())

    present = 0
    absent = 0
    leave = 0

    for a in rows:
        status = (a.status or "").lower()
        if status == "present":
            present += 1
        elif status == "absent":
            absent += 1
        elif status == "leave":
            leave += 1

        emp = a.employee
        sup = emp.user if emp else None

        result["records"].append({
            "id": a.id,
            "date": str(a.date) if a.date else None,
            "employee": {
                "id": emp.id if emp else None,
                "name": emp.name if emp else None,
                "emp_number": emp.emp_number if emp else None,
                "department": emp.department if emp else None,
                "site": emp.site if emp else None,
            },
            "supervisor": {
                "id": sup.id if sup else None,
                "name": sup.name if sup else None,
                "code": sup.supervisor_code if sup else None,
            },
            "status": a.status,
            "remarks": getattr(a, "remarks", None),
        })

    result["summary"] = {
        "date": str(d),
        "present": present,
        "absent": absent,
        "leave": leave,
        "count": len(rows),
    }
    return jsonify(result)

# ─────────────────────────────────────────────
#  ملخص الحضور الشهري للأدمن
#  GET /api/admin/attendance/monthly?year=2025&month=3
# ─────────────────────────────────────────────
@app.get("/api/admin/attendance/monthly")
@api_admin_required
def api_admin_attendance_monthly():
    from calendar import monthrange
    year_str  = freq.args.get("year")
    month_str = freq.args.get("month")

    try:
        today = datetime.now(RIYADH_TZ).date()
        year  = int(year_str)  if year_str  else today.year
        month = int(month_str) if month_str else today.month
    except (ValueError, TypeError):
        return jsonify({"error": "invalid year or month"}), 400

    # أول وآخر يوم في الشهر
    _, days_in_month = monthrange(year, month)
    month_start = date(year, month, 1)
    month_end   = date(year, month, days_in_month)

    # كل الموظفين النشطين مع بيانات المشرف دفعة واحدة
    from sqlalchemy.orm import joinedload
    emps = (Employee.query
            .filter_by(is_active=True)
            .order_by(Employee.name)
            .all())

    emp_ids = [e.id for e in emps]

    # جلب بيانات المشرفين دفعة واحدة بدل N+1
    sup_map = {u.id: u.name for u in User.query.filter(
        User.id.in_([e.user_id for e in emps if e.user_id])
    ).all()}

    # كل سجلات الحضور في الشهر دفعة واحدة
    all_att = Attendance.query.filter(
        Attendance.employee_id.in_(emp_ids),
        Attendance.date >= month_start,
        Attendance.date <= month_end
    ).all()

    # نبني dict سريع: {emp_id: {date_str: status}}
    att_map = {}
    for a in all_att:
        if a.employee_id not in att_map:
            att_map[a.employee_id] = {}
        att_map[a.employee_id][str(a.date)] = a.status

    # نحسب ملخص كل موظف
    rows = []
    total_present = total_absent = total_leave = total_days = 0

    for emp in emps:
        emp_att = att_map.get(emp.id, {})
        present  = sum(1 for s in emp_att.values() if s == "present")
        absent   = sum(1 for s in emp_att.values() if s == "absent")
        leave    = sum(1 for s in emp_att.values() if s == "leave")
        recorded = present + absent + leave

        total_present += present
        total_absent  += absent
        total_leave   += leave
        total_days    += recorded

        sup_name = sup_map.get(emp.user_id, "—") if emp.user_id else "—"

        # النسبة = حاضر ÷ أيام الشهر — يكشف من ما سُجّل حضوره أصلاً
        rate = round(present / days_in_month * 100, 1) if present > 0 else 0.0

        rows.append({
            "emp_id":     emp.id,
            "emp_number": emp.emp_number,
            "name":       emp.name,
            "department": emp.department or "",
            "site":       emp.site or "",
            "supervisor": sup_name,
            "present":    present,
            "absent":     absent,
            "leave":      leave,
            "recorded":   recorded,
            "rate":       rate,
        })

    # متوسط الحضور = حاضر ÷ كل الموظفين النشطين (يكشف من ما سُجّل حضوره)
    total_active = len(emps)
    avg_rate = round(total_present / total_active * 100, 1) if total_active > 0 else 0.0

    return jsonify({
        "year":    year,
        "month":   month,
        "days":    days_in_month,
        "summary": {
            "total_employees": len(emps),
            "total_present":   total_present,
            "total_absent":    total_absent,
            "total_leave":     total_leave,
            "avg_rate":        avg_rate,
        },
        "employees": rows,
    })


# ─────────────────────────────────────────────
#  تقرير شهري شامل للأدمن — PDF
#  GET /api/admin/monthly-report?year=2026&month=3
# ─────────────────────────────────────────────
@app.get("/api/admin/monthly-report")
@api_admin_required
def api_admin_monthly_report():
    """يولّد تقرير PDF شهري شامل: ملخص + صفحة لكل مشرف"""
    from calendar import monthrange
    from datetime import date as dt_date
    from zoneinfo import ZoneInfo

    year_str  = freq.args.get("year")
    month_str = freq.args.get("month")
    try:
        today = datetime.now(ZoneInfo("Asia/Riyadh")).date()
        year  = int(year_str)  if year_str  else today.year
        month = int(month_str) if month_str else today.month
    except (ValueError, TypeError):
        return jsonify({"error": "invalid year or month"}), 400

    report_type = (freq.args.get("type") or "detailed").strip().lower()
    # type=summary  → تقرير إجمالي للشركة (صفحة واحدة)
    # type=detailed → تقرير مفصّل (صفحة لكل مشرف)

    _, days_in_month = monthrange(year, month)
    month_start = dt_date(year, month, 1)
    month_end   = dt_date(year, month, days_in_month)

    month_names_ar = ["","يناير","فبراير","مارس","أبريل","مايو","يونيو",
                      "يوليو","أغسطس","سبتمبر","أكتوبر","نوفمبر","ديسمبر"]
    month_name = month_names_ar[month]

    # ── جلب البيانات ──
    supervisors = User.query.filter(
        User.role == "supervisor",
        User.is_active == True,
        User.is_hidden == False
    ).order_by(User.name).all()

    sup_ids = [s.id for s in supervisors]

    # كل الموظفين النشطين
    all_emps = Employee.query.filter_by(is_active=True).all()
    emp_by_sup = {}
    for e in all_emps:
        if e.user_id:
            emp_by_sup.setdefault(e.user_id, []).append(e)

    # كل سجلات الحضور في الشهر
    emp_ids = [e.id for e in all_emps]
    sup_map = {u.id: u.name for u in supervisors}

    all_att = Attendance.query.filter(
        Attendance.employee_id.in_(emp_ids),
        Attendance.date >= month_start,
        Attendance.date <= month_end
    ).all()
    att_map = {}
    for a in all_att:
        att_map.setdefault(a.employee_id, {})[str(a.date)] = a.status

    # كل التقييمات الأسبوعية في الشهر
    all_evals = Evaluation.query.filter(
        Evaluation.employee_id.in_(emp_ids),
        Evaluation.week_start >= month_start,
        Evaluation.week_end   <= month_end
    ).all()
    eval_map = {}
    for ev in all_evals:
        eval_map.setdefault(ev.employee_id, []).append(ev.total_score or 0)

    # ── توليد PDF ──
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                         Paragraph, Spacer, PageBreak)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        return jsonify({"error": "reportlab غير مثبت"}), 500

    # تسجيل الخطوط
    ARABIC_FONT = "Arabic"
    LATIN_FONT  = "Helvetica"
    try:
        pdfmetrics.registerFont(TTFont(ARABIC_FONT,
            "/usr/share/fonts/google-droid/DroidSansArabic.ttf"))
    except Exception:
        ARABIC_FONT = LATIN_FONT

    def ar(text):
        t = str(text)
        if not any("؀" <= c <= "ۿ" for c in t):
            return t
        try:
            from arabic_reshaper import reshape
            from bidi.algorithm import get_display
            return get_display(reshape(t))
        except Exception:
            return t

    def font_for(text):
        return ARABIC_FONT if any("؀" <= c <= "ۿ" for c in str(text)) else LATIN_FONT

    fname = f"monthly_{'summary' if report_type == 'summary' else 'detailed'}_{year}_{month:02d}_{uuid.uuid4().hex[:6]}.pdf"
    fpath = os.path.join(UPLOAD_FOLDER, fname)

    doc = SimpleDocTemplate(fpath, pagesize=A4,
                            rightMargin=1.5*cm, leftMargin=1.5*cm,
                            topMargin=2*cm, bottomMargin=1.5*cm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("t", fontName=ARABIC_FONT, fontSize=16,
                                  alignment=1, spaceAfter=6,
                                  textColor=colors.HexColor("#1a1f3a"))
    sub_style   = ParagraphStyle("s", fontName=ARABIC_FONT, fontSize=11,
                                  alignment=1, spaceAfter=4,
                                  textColor=colors.HexColor("#7b5ea7"))
    normal_r    = ParagraphStyle("n", fontName=ARABIC_FONT, fontSize=9,
                                  alignment=2)
    small_r     = ParagraphStyle("sm", fontName=LATIN_FONT,  fontSize=8,
                                  alignment=1)

    HDR_COLOR  = colors.HexColor("#1a1f3a")
    SUB_COLOR  = colors.HexColor("#7b5ea7")
    ROW1_COLOR = colors.HexColor("#f0f4ff")
    GREEN      = colors.HexColor("#27ae60")
    RED        = colors.HexColor("#e74c3c")
    ORANGE     = colors.HexColor("#f39c12")

    elements = []

    # ══════════════════════════════════════════
    # صفحة 1: ملخص الشركة
    # ══════════════════════════════════════════
    logo_path = os.path.join(BASE_DIR, "static", "img", "logo.png")
    if os.path.exists(logo_path):
        from reportlab.platypus import Image as RLImage
        try:
            elements.append(RLImage(logo_path, width=4*cm, height=2*cm))
            elements.append(Spacer(1, 0.3*cm))
        except Exception as _logo_err:
            app.logger.error("monthly report: invalid logo file %s: %s", logo_path, _logo_err)

    elements.append(Paragraph(ar(f"التقرير الشهري — {month_name} {year}"), title_style))
    elements.append(Paragraph(ar("ملخص الأداء والحضور"), sub_style))
    elements.append(Spacer(1, 0.5*cm))

    # إحصائيات عامة
    total_emps    = len(all_emps)
    total_present = sum(sum(1 for s in att_map.get(e.id, {}).values() if s=="present") for e in all_emps)
    total_evals   = len(all_evals)
    avg_score     = round(sum(ev.total_score or 0 for ev in all_evals) / total_evals, 1) if total_evals else 0
    att_rate      = round(total_present / total_emps * 100, 1) if total_emps else 0

    summary_data = [
        [Paragraph(ar("القيمة"), normal_r), Paragraph(ar("البند"), normal_r)],
        [Paragraph(str(total_emps),    small_r), Paragraph(ar("إجمالي الموظفين"),     normal_r)],
        [Paragraph(str(len(supervisors)), small_r), Paragraph(ar("عدد المشرفين"),      normal_r)],
        [Paragraph(f"{att_rate}%",     small_r), Paragraph(ar("نسبة الحضور الكلية"),   normal_r)],
        [Paragraph(str(total_present), small_r), Paragraph(ar("إجمالي أيام الحضور"), normal_r)],
        [Paragraph(str(total_evals),   small_r), Paragraph(ar("تقييمات أسبوعية"),    normal_r)],
        [Paragraph(str(avg_score),     small_r), Paragraph(ar("متوسط الدرجات"),      normal_r)],
    ]
    sum_tbl = Table(summary_data, colWidths=[4*cm, 10*cm])
    sum_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,0), HDR_COLOR),
        ("TEXTCOLOR",    (0,0), (-1,0), colors.white),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[ROW1_COLOR, colors.white]),
        ("GRID",         (0,0), (-1,-1), 0.5, colors.grey),
        ("ALIGN",        (0,0), (-1,-1), "CENTER"),
        ("TOPPADDING",   (0,0), (-1,-1), 6),
        ("BOTTOMPADDING",(0,0), (-1,-1), 6),
    ]))
    elements.append(sum_tbl)
    elements.append(Spacer(1, 0.8*cm))

    # ملخص المشرفين
    elements.append(Paragraph(ar("ملخص المشرفين"), sub_style))
    elements.append(Spacer(1, 0.3*cm))

    sup_summary_data = [[
        Paragraph(ar("النسبة"), normal_r),
        Paragraph(ar("متوسط الدرجة"), normal_r),
        Paragraph(ar("تقييمات"), normal_r),
        Paragraph(ar("حضور"), normal_r),
        Paragraph(ar("موظفون"), normal_r),
        Paragraph(ar("المشرف"), normal_r),
    ]]

    for sup in supervisors:
        s_emps = emp_by_sup.get(sup.id, [])
        s_present = sum(sum(1 for st in att_map.get(e.id,{}).values() if st=="present") for e in s_emps)
        s_evals   = [ev for e in s_emps for ev in eval_map.get(e.id, [])]
        s_avg     = round(sum(s_evals)/len(s_evals),1) if s_evals else 0
        s_rate    = round(s_present/len(s_emps)*100,1) if s_emps else 0

        latin_r = ParagraphStyle("lat", fontName=LATIN_FONT, fontSize=8, alignment=1)
        sup_summary_data.append([
            Paragraph(f"{s_rate}%", latin_r),
            Paragraph(str(s_avg),   latin_r),
            Paragraph(str(len(s_evals)), latin_r),
            Paragraph(str(s_present), latin_r),
            Paragraph(str(len(s_emps)), latin_r),
            Paragraph(sup.name, latin_r),
        ])

    sup_tbl = Table(sup_summary_data, colWidths=[2.5*cm,3*cm,2.5*cm,2.5*cm,2.5*cm,5.5*cm])
    sup_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,0), SUB_COLOR),
        ("TEXTCOLOR",    (0,0), (-1,0), colors.white),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[ROW1_COLOR, colors.white]),
        ("GRID",         (0,0), (-1,-1), 0.4, colors.grey),
        ("ALIGN",        (0,0), (-1,-1), "CENTER"),
        ("FONTSIZE",     (0,0), (-1,-1), 8),
        ("TOPPADDING",   (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0), (-1,-1), 5),
    ]))
    elements.append(sup_tbl)

    # ══════════════════════════════════════════
    # تقرير إجمالي فقط (بدون تفاصيل المشرفين)
    # ══════════════════════════════════════════
    if report_type == "summary":
        doc.build(elements)
        return send_file(fpath, mimetype="application/pdf",
                         as_attachment=False,
                         download_name=fname)

    # ══════════════════════════════════════════
    # صفحة لكل مشرف (التقرير المفصّل)
    # ══════════════════════════════════════════
    for sup in supervisors:
        s_emps = emp_by_sup.get(sup.id, [])
        if not s_emps:
            continue

        elements.append(PageBreak())
        elements.append(Paragraph(sup.name, title_style))
        elements.append(Paragraph(ar(f"كود: {sup.supervisor_code} | {month_name} {year}"), sub_style))
        elements.append(Spacer(1, 0.4*cm))

        # جدول موظفي المشرف
        emp_data = [[
            Paragraph(ar("النسبة"),     normal_r),
            Paragraph(ar("متوسط الدرجة"), normal_r),
            Paragraph(ar("إجازة"),      normal_r),
            Paragraph(ar("غياب"),       normal_r),
            Paragraph(ar("حضور"),       normal_r),
            Paragraph(ar("القسم"),      normal_r),
            Paragraph(ar("الموظف"),     normal_r),
        ]]

        for emp in sorted(s_emps, key=lambda e: e.name):
            emp_att  = att_map.get(emp.id, {})
            present  = sum(1 for s in emp_att.values() if s=="present")
            absent   = sum(1 for s in emp_att.values() if s=="absent")
            leave    = sum(1 for s in emp_att.values() if s=="leave")
            scores   = eval_map.get(emp.id, [])
            avg_s    = round(sum(scores)/len(scores),1) if scores else 0
            rate     = round(present/days_in_month*100,1) if present else 0

            # لون النسبة
            rate_color = GREEN if rate >= 80 else (ORANGE if rate >= 50 else RED)

            emp_data.append([
                Paragraph(f"{rate}%", ParagraphStyle("rc", fontName=LATIN_FONT,
                          fontSize=8, alignment=1, textColor=rate_color)),
                Paragraph(str(avg_s) if avg_s else "—", small_r),
                Paragraph(str(leave),   small_r),
                Paragraph(str(absent),  small_r),
                Paragraph(str(present), small_r),
                Paragraph(emp.department or "—", ParagraphStyle("dep",
                           fontName=LATIN_FONT, fontSize=8, alignment=1)),
                Paragraph(emp.name, ParagraphStyle("emp_n",
                           fontName=LATIN_FONT, fontSize=8, alignment=1)),
            ])

        emp_tbl = Table(emp_data, colWidths=[2*cm,3*cm,2*cm,2*cm,2*cm,3*cm,5.5*cm])
        emp_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,0), HDR_COLOR),
            ("TEXTCOLOR",     (0,0), (-1,0), colors.white),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [ROW1_COLOR, colors.white]),
            ("GRID",          (0,0), (-1,-1), 0.4, colors.grey),
            ("ALIGN",         (0,0), (-1,-1), "CENTER"),
            ("FONTSIZE",      (0,0), (-1,-1), 8),
            ("TOPPADDING",    (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ]))
        elements.append(emp_tbl)

        # ملخص المشرف
        s_present = sum(sum(1 for st in att_map.get(e.id,{}).values() if st=="present") for e in s_emps)
        s_evals   = [ev for e in s_emps for ev in eval_map.get(e.id, [])]
        s_avg     = round(sum(s_evals)/len(s_evals),1) if s_evals else 0
        s_rate    = round(s_present/len(s_emps)*100,1) if s_emps else 0

        elements.append(Spacer(1, 0.3*cm))
        footer_data = [[
            Paragraph(f"{s_rate}%", small_r),
            Paragraph(str(s_avg), small_r),
            Paragraph(str(s_present), small_r),
            Paragraph(str(len(s_emps)), small_r),
            Paragraph(ar("الإجمالي"), normal_r),
        ]]
        footer_tbl = Table(footer_data, colWidths=[3*cm,4*cm,3*cm,3*cm,6.5*cm])
        footer_tbl.setStyle(TableStyle([
            ("BACKGROUND",   (0,0), (-1,-1), colors.HexColor("#ecf0f1")),
            ("GRID",         (0,0), (-1,-1), 0.4, colors.grey),
            ("ALIGN",        (0,0), (-1,-1), "CENTER"),
            ("FONTSIZE",     (0,0), (-1,-1), 8),
            ("TOPPADDING",   (0,0), (-1,-1), 5),
            ("BOTTOMPADDING",(0,0), (-1,-1), 5),
        ]))
        elements.append(footer_tbl)

    doc.build(elements)
    return send_file(fpath, mimetype="application/pdf",
                     as_attachment=False,
                     download_name=fname)

# ─────────────────────────────────────────────
#  الحضور — جلب (التطبيق)
# ─────────────────────────────────────────────
@app.get("/api/attendance")
@api_login_required
def api_attendance_get():
    u     = get_api_user()
    d_str = freq.args.get("date") or datetime.now(RIYADH_TZ).date().isoformat()
    try:
        d_val = parse_date(d_str)
    except Exception:
        return jsonify({"error": "Invalid date"}), 400

    if u.role == "admin":
        emps = Employee.query.filter_by(is_active=True).order_by(Employee.name).all()
    else:
        emps = Employee.query.filter_by(user_id=u.id, is_active=True).order_by(Employee.name).all()

    emp_ids = [e.id for e in emps]
    atts    = {a.employee_id: a for a in
               Attendance.query.filter(
                   Attendance.employee_id.in_(emp_ids),
                   Attendance.date == d_val).all()}

    return jsonify([{
        "emp_id":     e.id,
        "emp_number": e.emp_number,
        "name":       e.name,
        "emp_name":   e.name,
        "department": e.department,
        "site":       e.site,
        "status":     atts[e.id].status  if e.id in atts else None,
        "remarks":    atts[e.id].remarks if e.id in atts else "",
    } for e in emps])


# ─────────────────────────────────────────────
#  الحضور — حفظ (التطبيق)
# ─────────────────────────────────────────────
@app.post("/api/attendance/save")
@api_login_required
def api_attendance_save():
    u        = get_api_user()
    data     = freq.get_json(force=True) or {}
    att_date = data.get("date") or datetime.now(RIYADH_TZ).date().isoformat()
    records  = data.get("records") or []

    try:
        att_date = parse_date(att_date)
    except Exception:
        return jsonify({"error": "Invalid date"}), 400

    changed = 0
    for rec in records:
        emp_id  = rec.get("emp_id")
        status  = rec.get("status")
        remarks = rec.get("remarks") or ""
        if not emp_id or not status:
            continue
        emp = db.session.get(Employee, emp_id)
        if not emp:
            continue
        if u.role != "admin" and emp.user_id != u.id:
            continue
        existing = Attendance.query.filter_by(employee_id=emp_id, date=att_date).first()
        if existing:
            existing.status  = status
            existing.remarks = remarks
        else:
            db.session.add(Attendance(
                employee_id=emp_id, supervisor_id=u.id,
                date=att_date, status=status, remarks=remarks))
        changed += 1

    db.session.commit()
    return jsonify({"message": f"Saved {changed} records"})


# ─────────────────────────────────────────────
#  إضافة موظف (التطبيق)
# ─────────────────────────────────────────────
@app.post("/api/employees/add")
@api_login_required
def api_employee_add():
    u    = get_api_user()
    data = freq.get_json(force=True) or {}
    emp_number = (data.get("emp_number") or "").strip()
    name       = (data.get("name") or "").strip()
    department = (data.get("department") or "").strip()
    site       = (data.get("site") or "").strip()

    if not emp_number or not name:
        return jsonify({"error": "emp_number and name are required"}), 400
    if not emp_number.isdigit():
        return jsonify({"error": "emp_number must be digits only"}), 400
    # التحقق إن الاسم والقسم والموقع إنجليزي فقط
    for field, val in [("name", name), ("department", department), ("site", site)]:
        if val and not all(ord(c) < 128 for c in val):
            return jsonify({"error": f"{field} must be in English only"}), 400

    emp = Employee.query.filter_by(emp_number=emp_number).first()
    if emp:
        if not emp.is_active:
            emp.is_active = True
            emp.user_id   = u.id
            db.session.commit()
            return jsonify({"message": "Employee reactivated", "id": emp.id})
        return jsonify({"error": "Employee already exists"}), 409

    emp = Employee(emp_number=emp_number, name=name,
                   department=department, site=site,
                   user_id=u.id, is_active=True)
    db.session.add(emp)
    db.session.commit()
    return jsonify({"message": "Employee added", "id": emp.id}), 201


# ─────────────────────────────────────────────
#  تعطيل موظف (التطبيق)
# ─────────────────────────────────────────────
@app.post("/api/employees/<int:emp_id>/deactivate")
@api_login_required
def api_employee_deactivate(emp_id):
    u   = get_api_user()
    emp = Employee.query.get_or_404(emp_id)
    if u.role != "admin" and emp.user_id != u.id:
        return jsonify({"error": "Forbidden"}), 403
    emp.is_active = False
    db.session.commit()
    return jsonify({"message": "Employee deactivated"})


# ─────────────────────────────────────────────
#  إدارة حالة الموظف (supervisor + site_supervisor)
# ─────────────────────────────────────────────

@app.post("/api/employees/<int:emp_id>/resign")
@api_login_required
def api_employee_resign(emp_id):
    """تعيين موظف كمستقيل — supervisor يملك الموظف أو admin"""
    u   = get_api_user()
    emp = Employee.query.get_or_404(emp_id)
    if u.role not in ("admin", "supervisor") or (u.role == "supervisor" and emp.user_id != u.id):
        return jsonify(error="Forbidden"), 403
    emp.status      = "resigned"
    emp.is_active   = False
    emp.resigned_at = datetime.now(RIYADH_TZ).date()
    db.session.commit()
    return jsonify(message="تم تسجيل الاستقالة")


@app.post("/api/employees/<int:emp_id>/unassign")
@api_login_required
def api_employee_unassign(emp_id):
    """فصل موظف عن مشرفه (يصبح unassigned) — supervisor أو admin"""
    u   = get_api_user()
    emp = Employee.query.get_or_404(emp_id)
    if u.role not in ("admin", "supervisor") or (u.role == "supervisor" and emp.user_id != u.id):
        return jsonify(error="Forbidden"), 403
    emp.status  = "unassigned"
    emp.user_id = None
    db.session.commit()
    return jsonify(message="تم فصل الموظف عن المشرف")


@app.post("/api/employees/<int:emp_id>/reactivate")
@api_login_required
def api_employee_reactivate(emp_id):
    """إعادة تفعيل موظف مستقيل أو غير معيّن — admin فقط"""
    u   = get_api_user()
    if u.role != "admin":
        return jsonify(error="Forbidden"), 403
    emp = Employee.query.get_or_404(emp_id)
    emp.status      = "active"
    emp.is_active   = True
    emp.resigned_at = None
    db.session.commit()
    return jsonify(message="تم إعادة التفعيل")


def _assign_employee_to_supervisor(emp, supervisor_id, actor):
    """تعيين موظف unassigned لمشرف — يتحقق من الصلاحيات ويُرجع (ok, message, http_status)"""
    if emp.status != "unassigned":
        return False, "الموظف ليس في حالة unassigned", 400

    if actor.role == "site_supervisor":
        link = SiteSupervisorMap.query.filter_by(
            site_sup_id=actor.id, supervisor_id=supervisor_id).first()
        if not link:
            return False, "المشرف ليس تحت إشرافك", 403
    elif actor.role != "admin":
        return False, "Forbidden", 403

    sup = User.query.get(supervisor_id)
    if not sup:
        return False, "المشرف غير موجود", 404
    if sup.role != "supervisor":
        return False, "المستخدم المختار ليس مشرفاً", 400

    emp.user_id   = supervisor_id
    emp.status    = "active"
    emp.is_active = True
    db.session.commit()
    return True, f"تم تعيين الموظف للمشرف {sup.name}", 200


@app.post("/api/employees/<int:emp_id>/assign")
@api_login_required
def api_employee_assign(emp_id):
    """تعيين موظف unassigned لمشرف — site_supervisor أو admin"""
    u    = get_api_user()
    data = freq.get_json(force=True) or {}
    supervisor_id = data.get("supervisor_id")
    if not supervisor_id:
        return jsonify(error="supervisor_id مطلوب"), 400

    emp = Employee.query.get_or_404(emp_id)
    ok, msg, status = _assign_employee_to_supervisor(emp, supervisor_id, u)
    if not ok:
        return jsonify(error=msg), status
    return jsonify(message=msg)


@app.get("/api/employees/unassigned")
@api_login_required
def api_employees_unassigned():
    """الموظفون غير المعيّنين — site_supervisor يرى نطاقه، admin يرى الكل"""
    u = get_api_user()
    if u.role not in ("site_supervisor", "admin"):
        return jsonify(error="Forbidden"), 403

    emps = Employee.query.filter_by(status="unassigned").order_by(Employee.name).all()
    return jsonify([{
        "id":         e.id,
        "name":       e.name,
        "emp_number": e.emp_number,
        "department": e.department or "",
        "site":       e.site or "",
    } for e in emps])


@app.get("/api/supervisor/employees")
@api_login_required
def api_supervisor_employees_full():
    """موظفو المشرف بكل الحالات — active + unassigned + resigned"""
    u = get_api_user()
    if u.role not in ("supervisor", "admin"):
        return jsonify(error="Forbidden"), 403

    uid = u.id
    # المشرف: موظفوه الحاليون (active) + من كانوا تحته وأصبحوا مستقيلين
    actives   = Employee.query.filter_by(user_id=uid, status="active").all()
    resigned  = Employee.query.filter_by(user_id=uid, status="resigned").all()

    def row(e):
        return {
            "id":          e.id,
            "name":        e.name,
            "emp_number":  e.emp_number,
            "department":  e.department or "",
            "site":        e.site or "",
            "status":      e.status,
            "resigned_at": str(e.resigned_at) if e.resigned_at else None,
        }

    return jsonify({
        "active":   [row(e) for e in actives],
        "resigned": [row(e) for e in resigned],
    })


# ─────────────────────────────────────────────
#  التقييم الأسبوعي — حفظ (التطبيق)
# ─────────────────────────────────────────────
@app.post("/api/weekly-eval/save")
@api_login_required
def api_weekly_eval_save():
    u    = get_api_user()
    data = freq.get_json(force=True) or {}
    emp_id     = data.get("employee_id")
    week_start = data.get("week_start")
    week_end   = data.get("week_end")

    emp = (db.session.get(Employee, emp_id) if u.role == "admin"
           else Employee.query.filter_by(id=emp_id, user_id=u.id).first())
    if not emp:
        return jsonify({"error": "Employee not found"}), 404

    try:
        ws = parse_date(week_start)
        we = parse_date(week_end)
    except Exception:
        return jsonify({"error": "Invalid dates"}), 400

    ok, msg = validate_week_sun_to_thu(ws, we)
    if not ok:
        return jsonify({"error": msg}), 400

    if Evaluation.query.filter_by(employee_id=emp.id, week_start=ws, week_end=we).first():
        return jsonify({"error": "Evaluation already exists for this week"}), 409

    ev = Evaluation(
        employee_id=emp.id,
        evaluator_id=u.id,
        week_start=ws,
        week_end=we,
        t1_text=data.get("t1_text",""),    t1_percent=data.get("t1_percent",0),    t1_remarks=data.get("t1_remarks",""),
        t2_text=data.get("t2_text",""),    t2_percent=data.get("t2_percent",0),    t2_remarks=data.get("t2_remarks",""),
        t3_text=data.get("t3_text",""),    t3_percent=data.get("t3_percent",0),    t3_remarks=data.get("t3_remarks",""),
        t4_text=data.get("t4_text",""),    t4_percent=data.get("t4_percent",0),    t4_remarks=data.get("t4_remarks",""),
        p_punctuality=data.get("p_punctuality",0),   c_punctuality=data.get("c_punctuality",""),
        p_quality=data.get("p_quality",0),           c_quality=data.get("c_quality",""),
        p_productivity=data.get("p_productivity",0), c_productivity=data.get("c_productivity",""),
        p_communication=data.get("p_communication",0),c_communication=data.get("c_communication",""),
        p_problemsolving=data.get("p_problemsolving",0),c_problemsolving=data.get("c_problemsolving",""),
        p_compliance=data.get("p_compliance",0),     c_compliance=data.get("c_compliance",""),
        strengths=data.get("strengths",""),
        improvements=data.get("improvements",""),
        training_needed=data.get("training_needed",""),
        company_id=api_cid(),
    )
    compute_scores(ev)
    db.session.add(ev)
    db.session.commit()
    return jsonify({"message": "Saved", "total": ev.total_score, "band": ev.overall_band}), 201


# ─────────────────────────────────────────────
#  تقارير أسبوعية (التطبيق)
# ─────────────────────────────────────────────
@app.get("/api/reports/weekly")
@api_login_required
def api_weekly_reports():
    u      = get_api_user()
    ws_str = freq.args.get("week_start")
    we_str = freq.args.get("week_end")

    if ws_str and we_str:
        try:
            ws = parse_date(ws_str)
            we = parse_date(we_str)
        except Exception:
            ws, we = default_week_today()
    else:
        ws, we = default_week_today()

    q = (db.session.query(Evaluation, Employee)
         .join(Employee, Evaluation.employee_id == Employee.id)
         .filter(Evaluation.week_start == ws, Evaluation.week_end == we))
    if u.role != "admin":
        q = q.filter(Employee.user_id == u.id)

    return jsonify([{
        "eval_id":    ev.id,
        "emp_id":     emp.id,
        "emp_name":   emp.name,
        "emp_number": emp.emp_number,
        "department": emp.department,
        "targets":    ev.targets_score,
        "perf":       ev.perf_score,
        "total":      ev.total_score,
        "band":       ev.overall_band,
        "week_start": str(ev.week_start),
        "week_end":   str(ev.week_end),
    } for ev, emp in q.order_by(Employee.name).all()])


# ─────────────────────────────────────────────
#  كل التقارير الأسبوعية لموظف معين
# ─────────────────────────────────────────────
@app.get("/api/employees/<int:emp_id>/reports/weekly")
@api_login_required
def api_employee_weekly_reports(emp_id):
    u   = get_api_user()
    emp = Employee.query.get_or_404(emp_id)
    if u.role == "supervisor" and emp.user_id != u.id:
        return jsonify(error="Forbidden"), 403
    if u.role == "site_supervisor":
        link = SiteSupervisorMap.query.filter_by(site_sup_id=u.id, supervisor_id=emp.user_id).first()
        if not link:
            return jsonify(error="Forbidden"), 403

    evals = (Evaluation.query
             .filter_by(employee_id=emp.id)
             .order_by(Evaluation.week_start.desc())
             .all())

    return jsonify([{
        "eval_id":    ev.id,
        "emp_id":     emp.id,
        "emp_name":   emp.name,
        "emp_number": emp.emp_number,
        "department": emp.department,
        "targets":    ev.targets_score,
        "perf":       ev.perf_score,
        "total":      ev.total_score,
        "band":       ev.overall_band,
        "week_start": str(ev.week_start),
        "week_end":   str(ev.week_end),
    } for ev in evals])


# ══════════════════════════════════════════════════════════
#  site_supervisor  API
# ══════════════════════════════════════════════════════════

@app.get("/api/site/supervisors")
@api_login_required
def api_site_supervisors():
    """قائمة المشرفين التابعين لهذا site_supervisor"""
    u = get_api_user()
    if not u or u.role != "site_supervisor":
        return jsonify(error="forbidden"), 403

    ws_str = request.args.get("week_start")
    we_str = request.args.get("week_end")
    ws, we = (parse_date(ws_str), parse_date(we_str)) if ws_str and we_str else default_week_today()
    rows = (db.session.query(SiteSupervisorMap, User)
            .join(User, SiteSupervisorMap.supervisor_id == User.id)
            .filter(SiteSupervisorMap.site_sup_id == u.id)
            .all())

    result = []
    for link, sup in rows:
        # عدد موظفيه
        emp_ids = [e.id for e in Employee.query.filter_by(user_id=sup.id, is_active=True).all()]
        emp_count = len(emp_ids)

        # كم منهم تم تقييمه هذا الأسبوع
        evaluated_count = 0
        if emp_ids:
            evaluated_count = Evaluation.query.filter(
                Evaluation.employee_id.in_(emp_ids),
                Evaluation.week_start == ws,
                Evaluation.week_end   == we
            ).count()
        evaluated = evaluated_count > 0
        progress = round((evaluated_count / emp_count * 100)) if emp_count > 0 else 0

        result.append({
            "id":              sup.id,
            "name":            sup.name,
            "code":            sup.supervisor_code,
            "emp_count":       emp_count,
            "evaluated":       evaluated,
            "evaluated_count": evaluated_count,
            "progress":        progress,
            "week_start":      str(ws),
            "week_end":        str(we),
        })

    return jsonify(result)


@app.get("/api/site/supervisors/<int:sup_id>/report")
@api_login_required
def api_site_supervisor_report(sup_id):
    """تقرير مشرف محدد — يعيد قائمة تقييماته"""
    u = get_api_user()
    if not u or u.role not in ("site_supervisor", "admin", "super_admin"):
        return jsonify(error="forbidden"), 403
    if u.role == "site_supervisor":
        link = SiteSupervisorMap.query.filter_by(site_sup_id=u.id, supervisor_id=sup_id).first()
        if not link:
            return jsonify(error="forbidden"), 403

    ws_str = request.args.get("week_start")
    we_str = request.args.get("week_end")
    if ws_str and we_str:
        ws = parse_date(ws_str); we = parse_date(we_str)
    else:
        ws, we = default_week_today()

    se = SupervisorEvaluation.query.filter_by(
        supervisor_id=sup_id, week_start=ws, week_end=we
    ).first()

    sup = User.query.get_or_404(sup_id)

    return jsonify({
        "supervisor": {"id": sup.id, "name": sup.name, "code": sup.supervisor_code},
        "week_start": str(ws),
        "week_end":   str(we),
        "evaluation": {
            "id":            se.id,
            "targets_score": se.targets_score,
            "perf_score":    se.perf_score,
            "total_score":   se.total_score,
            "overall_band":  se.overall_band,
            "targets": [
                {"text": se.t1_text, "percent": se.t1_percent, "remarks": se.t1_remarks},
                {"text": se.t2_text, "percent": se.t2_percent, "remarks": se.t2_remarks},
                {"text": se.t3_text, "percent": se.t3_percent, "remarks": se.t3_remarks},
                {"text": se.t4_text, "percent": se.t4_percent, "remarks": se.t4_remarks},
            ],
            "performance": {
                "punctuality":   {"score": se.p_punctuality,   "comment": se.c_punctuality},
                "quality":       {"score": se.p_quality,       "comment": se.c_quality},
                "productivity":  {"score": se.p_productivity,  "comment": se.c_productivity},
                "communication": {"score": se.p_communication, "comment": se.c_communication},
                "problemsolving":{"score": se.p_problemsolving,"comment": se.c_problemsolving},
                "compliance":    {"score": se.p_compliance,    "comment": se.c_compliance},
            },
            "strengths":        se.strengths,
            "improvements":     se.improvements,
            "training_needed":  se.training_needed,
        } if se else None
    })


@app.post("/api/site/evaluate/<int:sup_id>")
@api_login_required
def api_site_evaluate(sup_id):
    """تقييم مشرف من site_supervisor"""
    u = get_api_user()
    if not u or u.role != "site_supervisor":
        return jsonify(error="forbidden"), 403

    link = SiteSupervisorMap.query.filter_by(site_sup_id=u.id, supervisor_id=sup_id).first()
    if not link:
        return jsonify(error="forbidden"), 403

    data = freq.get_json(force=True)
    ws = parse_date(data.get("week_start")); we = parse_date(data.get("week_end"))
    if not (ws and we):
        return jsonify(error="week_start and week_end required"), 400

    ok, msg = validate_week_sun_to_thu(ws, we)
    if not ok:
        return jsonify(error=msg), 400

    if SupervisorEvaluation.query.filter_by(supervisor_id=sup_id, week_start=ws, week_end=we).first():
        return jsonify(error="Evaluation already exists for this week"), 409

    se = SupervisorEvaluation(
        supervisor_id=sup_id, evaluator_id=u.id,
        week_start=ws, week_end=we,
        t1_text=data.get("t1_text",""), t1_percent=float(data.get("t1_percent") or 0), t1_remarks=data.get("t1_remarks",""),
        t2_text=data.get("t2_text",""), t2_percent=float(data.get("t2_percent") or 0), t2_remarks=data.get("t2_remarks",""),
        t3_text=data.get("t3_text",""), t3_percent=float(data.get("t3_percent") or 0), t3_remarks=data.get("t3_remarks",""),
        t4_text=data.get("t4_text",""), t4_percent=float(data.get("t4_percent") or 0), t4_remarks=data.get("t4_remarks",""),
        p_punctuality=int(data.get("p_punctuality") or 0), c_punctuality=data.get("c_punctuality",""),
        p_quality=int(data.get("p_quality") or 0), c_quality=data.get("c_quality",""),
        p_productivity=int(data.get("p_productivity") or 0), c_productivity=data.get("c_productivity",""),
        p_communication=int(data.get("p_communication") or 0), c_communication=data.get("c_communication",""),
        p_problemsolving=int(data.get("p_problemsolving") or 0), c_problemsolving=data.get("c_problemsolving",""),
        p_compliance=int(data.get("p_compliance") or 0), c_compliance=data.get("c_compliance",""),
        strengths=data.get("strengths",""),
        improvements=data.get("improvements",""),
        training_needed=data.get("training_needed",""),
        company_id=api_cid(),
    )
    compute_scores(se)
    db.session.add(se)
    db.session.commit()
    return jsonify(id=se.id, total_score=se.total_score, overall_band=se.overall_band), 201


@app.get("/api/site/attendance")
@api_login_required
def api_site_attendance():
    """حضور موظفي المشرفين التابعين"""
    u = get_api_user()
    if not u or u.role != "site_supervisor":
        return jsonify(error="forbidden"), 403

    d_str = request.args.get("d")
    d_val = parse_date(d_str) if d_str else datetime.now(RIYADH_TZ).date()

    links = (db.session.query(SiteSupervisorMap, User)
             .join(User, SiteSupervisorMap.supervisor_id == User.id)
             .filter(SiteSupervisorMap.site_sup_id == u.id).all())
    sup_ids = [su.id for _, su in links]

    q = (db.session.query(Attendance, Employee, User)
         .join(Employee, Attendance.employee_id == Employee.id)
         .join(User, Employee.user_id == User.id)
         .filter(Attendance.date == d_val))
    if sup_ids:
        q = q.filter(Employee.user_id.in_(sup_ids))

    rows = q.order_by(User.name.asc(), Employee.name.asc()).all()
    return jsonify([{
        "att_id":       att.id,
        "emp_id":       emp.id,
        "emp_name":     emp.name,
        "supervisor":   sup.name,
        "status":       att.status,
        "remarks":      att.remarks,
        "date":         str(att.date),
    } for att, emp, sup in rows])


@app.get("/api/site/supervisors/<int:sup_id>/employees")
@api_login_required
def api_site_supervisor_employees(sup_id):
    """موظفو مشرف محدد مع حالة تقييمهم الأسبوعي"""
    u = get_api_user()
    if not u or u.role not in ("site_supervisor", "admin", "super_admin"):
        return jsonify(error="forbidden"), 403
    if u.role == "site_supervisor":
        link = SiteSupervisorMap.query.filter_by(site_sup_id=u.id, supervisor_id=sup_id).first()
        if not link:
            return jsonify(error="forbidden"), 403

    ws, we = default_week_today()
    employees = Employee.query.filter_by(user_id=sup_id, is_active=True).order_by(Employee.name).all()

    result = []
    for emp in employees:
        eval_this_week = Evaluation.query.filter_by(
            employee_id=emp.id, week_start=ws, week_end=we
        ).first()
        result.append({
            "id":         emp.id,
            "name":       emp.name,
            "emp_number": emp.emp_number,
            "department": emp.department or "",
            "evaluated":  eval_this_week is not None,
            "eval_id":    eval_this_week.id if eval_this_week else None,
            "total_score": eval_this_week.total_score if eval_this_week else None,
            "overall_band": eval_this_week.overall_band if eval_this_week else None,
            "week_start": str(ws),
            "week_end":   str(we),
        })
    return jsonify(result)


# ---- API (تطبيق الآيفون) ----
@app.post("/api/site/employees/<int:emp_id>/transfer")
@api_login_required
def api_site_employee_transfer(emp_id):
    u = get_api_user()
    if not u or u.role not in ("site_supervisor", "admin"):
        return jsonify(error="forbidden"), 403

    data = freq.get_json(force=True) or {}
    target_id = data.get("target_sup_id")
    if not target_id:
        return jsonify(error="target_sup_id مطلوب"), 400

    emp = Employee.query.get_or_404(emp_id)
    if u.role == "site_supervisor":
        scope = _site_scope_sup_ids(u)
        if emp.user_id not in scope or int(target_id) not in scope:
            return jsonify(error="خارج نطاقك"), 403

    target = User.query.get(int(target_id))
    if not target or target.role != "supervisor":
        return jsonify(error="المشرف المستلم غير صالح"), 400

    old_id = emp.user_id
    emp.user_id   = int(target_id)
    emp.status    = "active"
    emp.is_active = True
    _log_emp_assignment(emp, "transfer", u, from_user_id=old_id,
                        to_user_id=int(target_id), note=data.get("note", ""))
    db.session.commit()
    return jsonify(message=f"تم نقل الموظف إلى {target.name or target.supervisor_code}")


@app.get("/api/site/employee/<int:emp_id>/movement")
@api_login_required
def api_site_employee_movement(emp_id):
    u = get_api_user()
    if not u or u.role not in ("site_supervisor", "admin", "super_admin"):
        return jsonify(error="forbidden"), 403
    rows = (EmployeeAssignmentLog.query.filter_by(employee_id=emp_id)
            .order_by(EmployeeAssignmentLog.created_at.desc()).limit(50).all())
    uids = set()
    for r in rows:
        uids.update([r.from_user_id, r.to_user_id, r.actor_id])
    uids.discard(None)
    nm = {x.id: (x.name or x.supervisor_code)
          for x in User.query.filter(User.id.in_(list(uids))).all()} if uids else {}
    return jsonify([{
        "action": r.action, "from": nm.get(r.from_user_id), "to": nm.get(r.to_user_id),
        "by": nm.get(r.actor_id), "note": r.note or "",
        "at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows])




@app.get("/api/site/reports/summary")
@api_login_required
def api_site_reports_summary():
    """ملخص تقارير جميع موظفي المشرفين التابعين"""
    u = get_api_user()
    if not u or u.role != "site_supervisor":
        return jsonify(error="forbidden"), 403

    ws_str = request.args.get("week_start")
    we_str = request.args.get("week_end")
    ws, we = (parse_date(ws_str), parse_date(we_str)) if ws_str and we_str else default_week_today()

    links = (db.session.query(SiteSupervisorMap, User)
             .join(User, SiteSupervisorMap.supervisor_id == User.id)
             .filter(SiteSupervisorMap.site_sup_id == u.id).all())
    sup_ids = [su.id for _, su in links]

    all_emps = Employee.query.filter(Employee.user_id.in_(sup_ids), Employee.is_active == True).all()
    emp_ids  = [e.id for e in all_emps]

    evals = Evaluation.query.filter(
        Evaluation.employee_id.in_(emp_ids),
        Evaluation.week_start == ws,
        Evaluation.week_end   == we
    ).all()
    eval_map = {ev.employee_id: ev for ev in evals}

    evaluated     = [e for e in all_emps if e.id in eval_map]
    not_evaluated = [e for e in all_emps if e.id not in eval_map]

    def emp_row(emp, ev=None):
        sup = next((su for _, su in links if su.id == emp.user_id), None)
        return {
            "id":           emp.id,
            "name":         emp.name,
            "emp_number":   emp.emp_number,
            "department":   emp.department or "",
            "supervisor":   sup.name if sup else "",
            "evaluated":    ev is not None,
            "total_score":  ev.total_score if ev else None,
            "overall_band": ev.overall_band if ev else None,
        }

    return jsonify({
        "week_start":     str(ws),
        "week_end":       str(we),
        "total":          len(all_emps),
        "evaluated_count": len(evaluated),
        "not_evaluated_count": len(not_evaluated),
        "not_evaluated":  [emp_row(e) for e in not_evaluated],
        "evaluated":      [emp_row(e, eval_map[e.id]) for e in evaluated],
    })


@app.get("/api/site/tbt")
@api_login_required
def api_site_tbt():
    """جلسات TBT التي أجراها سيفتي أوفسر لمشرفي الـ site supervisor"""
    u = get_api_user()
    if not u or u.role not in ("site_supervisor", "admin", "super_admin"):
        return jsonify(error="forbidden"), 403
    links = (db.session.query(SiteSupervisorMap, User)
             .join(User, SiteSupervisorMap.supervisor_id == User.id)
             .filter(SiteSupervisorMap.site_sup_id == u.id).all())
    sup_ids = [su.id for _, su in links]
    sup_map = {su.id: su.name for _, su in links}
    if not sup_ids:
        return jsonify([])
    # آخر 30 يوم بشكل افتراضي، أو حسب المرسل
    days = int(request.args.get("days", 30))
    since = datetime.now(RIYADH_TZ).date() - timedelta(days=days)
    tbts = (HseTbt.query
            .filter(HseTbt.supervisor_id.in_(sup_ids))
            .filter(HseTbt.date >= since)
            .order_by(HseTbt.date.desc())
            .all())
    result = []
    for t in tbts:
        officer = User.query.get(t.officer_id)
        att_count = HseTbtAttendance.query.filter_by(tbt_id=t.id).count()
        result.append({
            "id":              t.id,
            "date":            str(t.date),
            "topic":           t.topic or "",
            "location":        t.location or "",
            "supervisor_id":   t.supervisor_id,
            "supervisor_name": sup_map.get(t.supervisor_id, ""),
            "officer_name":    officer.name if officer else "",
            "attendee_count":  att_count,
        })
    return jsonify(result)


@app.get("/api/site/tbt/<int:tbt_id>")
@api_login_required
def api_site_tbt_detail(tbt_id):
    """تفاصيل جلسة TBT مع قائمة الحضور"""
    u = get_api_user()
    if not u or u.role not in ("site_supervisor", "admin", "super_admin"):
        return jsonify(error="forbidden"), 403
    t = HseTbt.query.get_or_404(tbt_id)
    # تحقق أن المشرف المرتبط بالـ TBT تابع للـ site supervisor
    if u.role == "site_supervisor":
        link = SiteSupervisorMap.query.filter_by(
            site_sup_id=u.id, supervisor_id=t.supervisor_id).first()
        if not link:
            return jsonify(error="forbidden"), 403
    officer = User.query.get(t.officer_id)
    supervisor = User.query.get(t.supervisor_id) if t.supervisor_id else None
    attendees = HseTbtAttendance.query.filter_by(tbt_id=t.id).all()
    return jsonify({
        "id":              t.id,
        "date":            str(t.date),
        "topic":           t.topic or "",
        "location":        t.location or "",
        "officer_name":    officer.name if officer else "",
        "supervisor_name": supervisor.name if supervisor else "",
        "attendees": [{"emp_number": a.emp_number, "emp_name": a.emp_name}
                      for a in attendees],
    })


@app.get("/api/site/attendance/supervisors")
@api_login_required
def api_site_attendance_supervisors():
    """المشرفون — هل سجلوا حضور موظفيهم اليوم"""
    u = get_api_user()
    if not u or u.role != "site_supervisor":
        return jsonify(error="forbidden"), 403

    d_str = request.args.get("d")
    d_val = parse_date(d_str) if d_str else datetime.now(RIYADH_TZ).date()

    links = (db.session.query(SiteSupervisorMap, User)
             .join(User, SiteSupervisorMap.supervisor_id == User.id)
             .filter(SiteSupervisorMap.site_sup_id == u.id).all())

    result = []
    for link, sup in links:
        emp_ids = [e.id for e in Employee.query.filter_by(user_id=sup.id, is_active=True).all()]
        recorded = Attendance.query.filter(
            Attendance.employee_id.in_(emp_ids),
            Attendance.date == d_val
        ).count() if emp_ids else 0

        result.append({
            "id":          sup.id,
            "name":        sup.name,
            "code":        sup.supervisor_code,
            "emp_count":   len(emp_ids),
            "recorded":    recorded,
            "did_attend":  recorded > 0,
            "date":        str(d_val),
        })
    return jsonify(result)


@app.get("/api/site/attendance/supervisor/<int:sup_id>")
@api_login_required
def api_site_attendance_supervisor_detail(sup_id):
    """حضور موظفي مشرف محدد"""
    u = get_api_user()
    if not u or u.role != "site_supervisor":
        return jsonify(error="forbidden"), 403

    link = SiteSupervisorMap.query.filter_by(site_sup_id=u.id, supervisor_id=sup_id).first()
    if not link:
        return jsonify(error="forbidden"), 403

    d_str = request.args.get("d")
    d_val = parse_date(d_str) if d_str else datetime.now(RIYADH_TZ).date()

    rows = (db.session.query(Attendance, Employee)
            .join(Employee, Attendance.employee_id == Employee.id)
            .filter(Employee.user_id == sup_id, Attendance.date == d_val)
            .order_by(Employee.name).all())

    present = sum(1 for a, _ in rows if a.status == "present")
    absent  = sum(1 for a, _ in rows if a.status == "absent")
    leave   = sum(1 for a, _ in rows if a.status == "leave")

    return jsonify({
        "date":    str(d_val),
        "summary": {"present": present, "absent": absent, "leave": leave},
        "rows": [{
            "att_id":   att.id,
            "emp_id":   emp.id,
            "emp_name": emp.name,
            "emp_number": emp.emp_number,
            "status":   att.status,
            "remarks":  att.remarks or "",
        } for att, emp in rows]
    })


@app.get("/api/site/requests")
@api_login_required
def api_site_requests():
    """طلبات المشرفين التابعين"""
    u = get_api_user()
    if not u or u.role != "site_supervisor":
        return jsonify(error="forbidden"), 403

    links = (db.session.query(SiteSupervisorMap, User)
             .join(User, SiteSupervisorMap.supervisor_id == User.id)
             .filter(SiteSupervisorMap.site_sup_id == u.id).all())
    sup_ids = [su.id for _, su in links]

    items = (Request.query
             .filter(Request.supervisor_id.in_(sup_ids))
             .order_by(Request.created_at.desc()).all())

    sup_map = {su.id: su.name for _, su in links}

    return jsonify([{
        "id":           r.id,
        "type":         r.type,
        "status":       r.status,
        "supervisor_id": r.supervisor_id,
        "supervisor_name": sup_map.get(r.supervisor_id, ""),
        "created_at":   str(r.created_at)[:10],
        "notes":        r.reason or "",
    } for r in items])


# ─────────────────────────────────────────────
#  إشعار مخصص من الأدمن للمشرفين
# ─────────────────────────────────────────────

@app.post("/api/admin/notify")
@api_admin_required
def api_admin_notify():
    """الأدمن يرسل إشعار لمشرف/مشرفين أو للكل"""
    data       = freq.get_json(force=True) or {}
    title      = (data.get("title") or "").strip()
    body       = (data.get("body")  or "").strip()
    target     = data.get("target", "all")   # "all" | "supervisors" | "site_supervisors" | "safety_officers" | "safety_supervisors" | "custom"
    user_ids   = data.get("user_ids", [])    # قائمة ids إذا target == "custom"

    if not title or not body:
        return jsonify(error="العنوان والرسالة مطلوبان"), 400

    cid = api_cid()
    # تحديد المستلمين
    if target == "all":
        users = User.query.filter(
            User.role.in_(["supervisor", "site_supervisor", "safety_officer", "safety_supervisor"]),
            User.is_active == True,
            User.is_hidden == False,
            User.company_id == cid
        ).all()
    elif target == "supervisors":
        users = User.query.filter(User.role=="supervisor", User.is_active==True, User.is_hidden==False, User.company_id==cid).all()
    elif target == "site_supervisors":
        users = User.query.filter(User.role=="site_supervisor", User.is_active==True, User.is_hidden==False, User.company_id==cid).all()
    elif target == "safety_officers":
        users = User.query.filter(User.role=="safety_officer", User.is_active==True, User.is_hidden==False, User.company_id==cid).all()
    elif target == "safety_supervisors":
        users = User.query.filter(User.role=="safety_supervisor", User.is_active==True, User.is_hidden==False, User.company_id==cid).all()
    elif target == "custom" and user_ids:
        users = User.query.filter(User.id.in_(user_ids), User.is_active == True).all()
    else:
        return jsonify(error="target غير صالح"), 400

    sent = 0
    for u in users:
        tokens = [t.device_token for t in MobileToken.query.filter_by(user_id=u.id).all() if t.device_token]
        if tokens:
            threading.Thread(
                target=_send_push_to_user,
                args=(u.id, title, body),
                daemon=True
            ).start()
            sent += 1

    return jsonify(message=f"تم إرسال الإشعار لـ {sent} مستخدم", sent=sent, total=len(users))


@app.get("/api/admin/notify/users")
@api_admin_required
def api_admin_notify_users():
    """قائمة المشرفين للاختيار منهم عند الإرسال المخصص"""
    users = User.query.filter(
        User.role.in_(["supervisor", "site_supervisor", "safety_officer", "safety_supervisor"]),
        User.is_active == True,
        User.is_hidden == False,
        User.company_id == api_cid()
    ).order_by(User.role, User.name).all()
    return jsonify([{
        "id":   u.id,
        "name": u.name,
        "role": u.role,
        "code": u.supervisor_code,
    } for u in users])



# ══════════════════════════════════════════════════════════
#  نظام الإنذارات
# ══════════════════════════════════════════════════════════

DEFAULT_WARNING_REASONS = [
    "عدم اتباع أوامر المشرف",
    "التغيب بدون إذن",
    "التأخر المتكرر",
    "الإهمال في العمل",
    "سوء السلوك",
]

def seed_warning_reasons():
    """إضافة الأسباب الافتراضية إذا لم تكن موجودة"""
    for text in DEFAULT_WARNING_REASONS:
        if not WarningReason.query.filter_by(text=text).first():
            db.session.add(WarningReason(text=text, is_default=True))
    db.session.commit()

@app.get("/api/warning/reasons")
@api_login_required
def api_warning_reasons():
    """قائمة أسباب الإنذار"""
    reasons = WarningReason.query.order_by(WarningReason.is_default.desc(), WarningReason.id).all()
    return jsonify([{"id": r.id, "text": r.text, "is_default": r.is_default} for r in reasons])

@app.post("/api/warning/reasons")
@api_login_required
def api_warning_reasons_add():
    """إضافة سبب إنذار مخصص"""
    u    = get_api_user()
    data = freq.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify(error="النص مطلوب"), 400
    if WarningReason.query.filter_by(text=text).first():
        return jsonify(error="السبب موجود مسبقاً"), 409
    r = WarningReason(text=text, is_default=False, created_by=u.id)
    db.session.add(r)
    db.session.commit()
    return jsonify({"id": r.id, "text": r.text}), 201

@app.get("/api/warning/form/<int:request_id>")
@api_login_required
def api_warning_form(request_id):
    """بيانات نموذج الإنذار للمشرف"""
    u   = get_api_user()
    req = Request.query.get_or_404(request_id)
    if u.role not in ("admin",) and req.supervisor_id != u.id:
        return jsonify(error="Forbidden"), 403

    sig = WarningSignature.query.filter_by(request_id=request_id).first()

    from zoneinfo import ZoneInfo
    now_riyadh = datetime.now(ZoneInfo("Asia/Riyadh"))

    task_item = HRTask.query.filter_by(request_id=request_id).first()
    try:
        official_reason = (task_item.official_reason if task_item and task_item.official_reason else req.reason) or "—"
    except Exception:
        official_reason = req.reason or "—"

    return jsonify({
        "request_id":    req.id,
        "employee_name": req.employee.name,
        "emp_number":    req.employee.emp_number,
        "department":    req.employee.department,
        "reason":        official_reason,
        "date":          now_riyadh.strftime("%Y/%m/%d"),
        "signed":        sig is not None,
        "signed_at":     str(sig.signed_at) if sig else None,
        "signer_name":   sig.employee_name if sig else None,
    })

@app.post("/api/warning/sign/<int:request_id>")
@api_login_required
def api_warning_sign(request_id):
    """حفظ توقيع الموظف"""
    u   = get_api_user()
    req = Request.query.get_or_404(request_id)
    if req.supervisor_id != u.id and u.role != "admin":
        return jsonify(error="Forbidden"), 403

    data      = freq.get_json(force=True) or {}
    signature = (data.get("signature") or "").strip()
    emp_name  = (data.get("employee_name") or req.employee.name).strip()
    job_title = (data.get("job_title") or (req.employee.department if req.employee else "") or "").strip()

    if not signature:
        return jsonify(error="Signature is required."), 400

    sig = WarningSignature.query.filter_by(request_id=request_id).first()
    if sig:
        sig.signature     = signature
        sig.employee_name = emp_name
        sig.job_title      = job_title
        from zoneinfo import ZoneInfo
        sig.signed_at     = datetime.now(ZoneInfo("Asia/Riyadh"))
    else:
        from zoneinfo import ZoneInfo
        sig = WarningSignature(
            request_id    = request_id,
            signature     = signature,
            employee_name = emp_name,
            job_title     = job_title,
            signed_at     = datetime.now(ZoneInfo("Asia/Riyadh")),
        )
        db.session.add(sig)
    db.session.commit()

    # توليد PDF الإنذار
    pdf_ok = False
    try:
        pdf_name = generate_warning_pdf(req, sig)
        if pdf_name:
            # تحديث HRTask بمسار الـ PDF
            task = HRTask.query.filter_by(request_id=req.id).first()
            if task:
                task.warning_pdf = pdf_name
                db.session.commit()
                pdf_ok = True
    except Exception as e:
        app.logger.error("warning PDF error: %s", e)

    return jsonify(message="تم حفظ التوقيع", pdf_generated=pdf_ok), 200


# ─────────────────────────────────────────────
#  تحميل PDF الإنذار
# ─────────────────────────────────────────────
@app.get("/api/warning/<int:request_id>/download")
def api_warning_download(request_id):
    # يقبل token من header أو query string (لأن Safari لا يرسل headers)
    from flask import request as freq2
    token = freq2.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not token:
        token = freq2.args.get("token", "")
    if not token:
        return jsonify(error="Unauthorized"), 401
    mt = MobileToken.query.filter_by(token=token).first()
    if not mt:
        return jsonify(error="Unauthorized"), 401

    task = HRTask.query.filter_by(request_id=request_id).first_or_404()
    if not task.warning_pdf:
        return jsonify(error="PDF غير موجود"), 404
    fpath = os.path.join(UPLOAD_FOLDER, task.warning_pdf)
    if not os.path.exists(fpath):
        return jsonify(error="الملف غير موجود على السيرفر"), 404
    return send_file(fpath,
                     mimetype="application/pdf",
                     as_attachment=False,
                     download_name=task.warning_pdf)

# ─────────────────────────────────────────────────────────────────
#  توقيع الموظف على نموذج الإجازة
# ─────────────────────────────────────────────────────────────────
@app.post("/api/leave/sign/<int:request_id>")
@api_login_required
def api_leave_sign(request_id):
    u   = get_api_user()
    req = Request.query.get_or_404(request_id)
    if req.supervisor_id != u.id and u.role not in ("admin", "hr"):
        return jsonify(error="Forbidden"), 403

    data      = freq.get_json(force=True) or {}
    signature = (data.get("signature") or "").strip()
    emp_name  = (data.get("employee_name") or (req.employee.name if req.employee else "")).strip()

    if not signature:
        return jsonify(error="Signature is required."), 400

    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Riyadh"))
    sig = LeaveSignature.query.filter_by(request_id=request_id).first()
    if sig:
        sig.signature = signature; sig.employee_name = emp_name; sig.signed_at = now
    else:
        sig = LeaveSignature(request_id=request_id, signature=signature,
                             employee_name=emp_name, signed_at=now)
        db.session.add(sig)

    # تحديث حالة نموذج الإجازة
    lf = LeaveForm.query.filter_by(request_id=request_id).first()
    if lf:
        lf.signed    = True
        lf.signed_at = now
        # أعد توليد PDF مع التوقيع
        try:
            pdf_name = generate_leave_pdf(req, sig)
            if pdf_name:
                lf.filename = pdf_name
        except Exception as e:
            app.logger.error("leave PDF regen error: %s", e)

    db.session.commit()

    # إشعار HR بعد التوقيع
    try:
        hr_users = User.query.filter_by(role="hr", company_id=req.company_id, is_active=True).all()
        for hr in hr_users:
            _send_push_to_user(hr.id, title="توقيع نموذج إجازة",
                               body=f"وقّع {emp_name} على نموذج إجازة {req.employee.name if req.employee else ''}")
    except Exception:
        pass

    return jsonify(message="تم حفظ التوقيع", signed=True), 200


@app.get("/api/leave/form/<int:request_id>")
@api_login_required
def api_leave_form_info(request_id):
    """معلومات نموذج الإجازة + حالة التوقيع"""
    req = Request.query.get_or_404(request_id)
    lf  = LeaveForm.query.filter_by(request_id=request_id).first()
    sig = LeaveSignature.query.filter_by(request_id=request_id).first()
    emp = req.employee
    sup = req.supervisor
    return jsonify({
        "request_id":    request_id,
        "employee_name": emp.name       if emp else "",
        "emp_number":    emp.emp_number if emp else "",
        "department":    emp.department if emp else "",
        "site":          emp.site       if emp else "",
        "supervisor":    sup.name       if sup else "",
        "start_date":    str(req.start_date) if req.start_date else "",
        "end_date":      str(req.end_date)   if req.end_date   else "",
        "days":          str((req.end_date - req.start_date).days + 1) if req.start_date and req.end_date else "",
        "reason":        req.reason or "",
        "approved_at":   str(req.decided_at.date()) if req.decided_at else "",
        "has_form":      lf is not None,
        "pdf_url":       f"/api/leave-form/{request_id}/download" if lf else None,
        "signed":        lf.signed if lf else False,
        "signed_at":     str(lf.signed_at) if lf and lf.signed_at else None,
        "signer_name":   sig.employee_name if sig else None,
    })


# ─────────────────────────────────────────────────────────────────
#  توقيع الموظف على نموذج الاستئذان
# ─────────────────────────────────────────────────────────────────
@app.post("/api/permission/sign/<int:request_id>")
@api_login_required
def api_permission_sign(request_id):
    u   = get_api_user()
    req = Request.query.get_or_404(request_id)
    if req.type != "permission":
        return jsonify(error="ليس طلب استئذان"), 400
    if req.supervisor_id != u.id and u.role not in ("admin", "hr"):
        return jsonify(error="Forbidden"), 403

    data      = freq.get_json(force=True) or {}
    signature = (data.get("signature") or "").strip()
    emp_name  = (data.get("employee_name") or (req.employee.name if req.employee else "")).strip()

    if not signature:
        return jsonify(error="Signature is required."), 400

    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Riyadh"))
    sig = PermissionSignature.query.filter_by(request_id=request_id).first()
    if sig:
        sig.signature = signature; sig.employee_name = emp_name; sig.signed_at = now
    else:
        sig = PermissionSignature(request_id=request_id, signature=signature,
                                  employee_name=emp_name, signed_at=now)
        db.session.add(sig)
    db.session.commit()

    # توليد PDF الاستئذان بعد التوقيع
    try:
        pdf_name = generate_permission_pdf(req, sig)
        if pdf_name:
            task = HRTask.query.filter_by(request_id=req.id).first()
            if task:
                task.warning_pdf = pdf_name  # نعيد استخدام حقل warning_pdf
                db.session.commit()
    except Exception as e:
        app.logger.error("permission PDF error: %s", e)

    return jsonify(message="تم حفظ التوقيع", signed=True), 200


@app.get("/api/permission/form/<int:request_id>")
@api_login_required
def api_permission_form_info(request_id):
    req = Request.query.get_or_404(request_id)
    sig = PermissionSignature.query.filter_by(request_id=request_id).first()
    emp = req.employee
    sup = req.supervisor
    task = HRTask.query.filter_by(request_id=request_id).first()
    return jsonify({
        "request_id":    request_id,
        "employee_name": emp.name       if emp else "",
        "emp_number":    emp.emp_number if emp else "",
        "department":    emp.department if emp else "",
        "supervisor":    sup.name       if sup else "",
        "date":          str(req.start_date or req.created_at.date()),
        "reason":        req.reason or "",
        "status":        req.status,
        "signed":        sig is not None,
        "signed_at":     str(sig.signed_at) if sig else None,
        "signer_name":   sig.employee_name  if sig else None,
        "pdf_url":       f"/api/warning/{request_id}/download" if (task and task.warning_pdf) else None,
    })


# =====================================================================
# WEEKLY REPORT EXPORTS
# =====================================================================

def _weekly_report_data(ws, we):
    """جمع بيانات التقرير الأسبوعي: قائمة الموظفين + تقييماتهم + حضورهم."""
    rows = (db.session.query(Evaluation, Employee, User)
            .join(Employee, Evaluation.employee_id == Employee.id)
            .join(User, Employee.user_id == User.id)
            .filter(Evaluation.week_start == ws, Evaluation.week_end == we)
            .order_by(User.supervisor_code.asc(), Employee.name.asc())
            .all())

    # الحضور لهذا الأسبوع
    emp_ids = [e.id for _, e, _ in rows]
    att_map = {}
    if emp_ids:
        att_records = (Attendance.query
                       .filter(Attendance.employee_id.in_(emp_ids),
                               Attendance.date >= ws, Attendance.date <= we)
                       .all())
        for a in att_records:
            att_map.setdefault(a.employee_id, []).append(a)

    result = []
    for ev, emp, sup in rows:
        att_list = att_map.get(emp.id, [])
        present  = sum(1 for a in att_list if a.status == "present")
        absent   = sum(1 for a in att_list if a.status == "absent")
        leave    = sum(1 for a in att_list if a.status == "leave")
        result.append({
            "sup_code":    sup.supervisor_code,
            "sup_name":    sup.name,
            "emp_number":  emp.emp_number,
            "emp_name":    emp.name,
            "department":  emp.department or "",
            "site":        emp.site or "",
            "band":        ev.overall_band or "—",
            "total_score": ev.total_score or 0,
            "targets":     ev.targets_score or 0,
            "perf":        ev.perf_score or 0,
            "present":     present,
            "absent":      absent,
            "leave":       leave,
        })
    return result


@app.get("/admin/report/weekly/print")
@admin_required
def admin_report_weekly_print():
    ws = parse_date(request.args.get("week_start", ""))
    we = parse_date(request.args.get("week_end", ""))
    if not ws or not we:
        ws, we = default_week_today()
    data = _weekly_report_data(ws, we)
    from datetime import datetime as _dt
    return render_template("report_weekly_print.html", rows=data, ws=ws, we=we,
                           now=_dt.now(RIYADH_TZ))


@app.get("/admin/report/weekly/excel")
@admin_required
def admin_report_weekly_excel():
    import io
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from flask import make_response

    ws_date = parse_date(request.args.get("week_start", ""))
    we_date = parse_date(request.args.get("week_end", ""))
    if not ws_date or not we_date:
        ws_date, we_date = default_week_today()

    data = _weekly_report_data(ws_date, we_date)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Weekly Report"

    # Header row
    headers = ["#", "Supervisor", "Sup ID", "Employee", "Emp #",
               "Department", "Site", "Band", "Score", "Targets", "Perf",
               "Present", "Absent", "Leave"]
    header_fill = PatternFill("solid", fgColor="0F172A")
    header_font = Font(color="FFFFFF", bold=True)
    thin = Side(style="thin", color="D1D5DB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border

    band_colors = {
        "Excellent":        "C8E6C9",
        "Good":             "BBDEFB",
        "Satisfactory":     "FFE0B2",
        "Needs Improvement":"FFCDD2",
    }

    for i, r in enumerate(data, 2):
        row_vals = [i-1, r["sup_name"], r["sup_code"], r["emp_name"],
                    r["emp_number"], r["department"], r["site"],
                    r["band"], r["total_score"], r["targets"], r["perf"],
                    r["present"], r["absent"], r["leave"]]
        fill_color = band_colors.get(r["band"], "FFFFFF")
        for col, val in enumerate(row_vals, 1):
            cell = ws.cell(row=i, column=col, value=val)
            cell.border = border
            cell.alignment = Alignment(vertical="center")
            if col == 8:  # Band column
                cell.fill = PatternFill("solid", fgColor=fill_color)
                cell.font = Font(bold=True)
            if col in (9, 10, 11):  # Score columns
                cell.number_format = "0.0"

    # Column widths
    widths = [5, 22, 12, 24, 12, 18, 14, 16, 8, 8, 8, 9, 9, 9]
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = w

    ws.row_dimensions[1].height = 22
    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    fname = f"weekly_{ws_date.isoformat()}_{we_date.isoformat()}.xlsx"
    resp = make_response(buf.read())
    resp.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    resp.headers["Content-Disposition"] = f"attachment; filename={fname}"
    return resp


@app.get("/admin/report/weekly/pdf")
@admin_required
def admin_report_weekly_pdf():
    import io
    from flask import make_response
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    ws_date = parse_date(request.args.get("week_start", ""))
    we_date = parse_date(request.args.get("week_end", ""))
    if not ws_date or not we_date:
        ws_date, we_date = default_week_today()

    data = _weekly_report_data(ws_date, we_date)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=10*mm, rightMargin=10*mm,
                            topMargin=12*mm, bottomMargin=12*mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Heading1"],
                                 fontSize=14, spaceAfter=4)
    sub_style   = ParagraphStyle("sub", parent=styles["Normal"],
                                 fontSize=9, textColor=colors.grey, spaceAfter=10)

    BAND_COLORS = {
        "Excellent":        colors.Color(0.78, 0.93, 0.78),
        "Good":             colors.Color(0.73, 0.87, 0.98),
        "Satisfactory":     colors.Color(1.0,  0.88, 0.7),
        "Needs Improvement":colors.Color(1.0,  0.80, 0.80),
    }

    # Table data
    header = ["#", "Supervisor", "Employee", "Emp #", "Dept",
              "Band", "Score", "T", "P", "Pres", "Abs", "Lv"]
    table_data = [header]
    row_styles = []

    for i, r in enumerate(data, 1):
        table_data.append([
            str(i), r["sup_name"][:20], r["emp_name"][:22],
            r["emp_number"], r["department"][:14],
            r["band"], f"{r['total_score']:.1f}",
            f"{r['targets']:.1f}", f"{r['perf']:.1f}",
            str(r["present"]), str(r["absent"]), str(r["leave"]),
        ])
        bg = BAND_COLORS.get(r["band"])
        if bg:
            row_styles.append(("BACKGROUND", (5, i+1), (5, i+1), bg))

    col_widths = [8*mm, 38*mm, 40*mm, 22*mm, 25*mm,
                  26*mm, 14*mm, 11*mm, 11*mm, 12*mm, 10*mm, 10*mm]

    tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
    base_style = [
        ("BACKGROUND",  (0,0), (-1,0), colors.Color(0.06, 0.09, 0.16)),
        ("TEXTCOLOR",   (0,0), (-1,0), colors.white),
        ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,0), 8),
        ("FONTSIZE",    (0,1), (-1,-1), 7.5),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.Color(0.97,0.97,0.97)]),
        ("GRID",        (0,0), (-1,-1), 0.4, colors.Color(0.82,0.82,0.82)),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN",       (6,0), (-1,-1), "CENTER"),
        ("TOPPADDING",  (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0), (-1,-1), 3),
    ] + row_styles
    tbl.setStyle(TableStyle(base_style))

    story = [
        Paragraph(f"Weekly Employee Evaluation Report", title_style),
        Paragraph(f"Week: {ws_date.strftime('%d %b %Y')} — {we_date.strftime('%d %b %Y')}  |  Total: {len(data)} evaluations", sub_style),
        tbl,
    ]
    doc.build(story)
    buf.seek(0)

    fname = f"weekly_{ws_date.isoformat()}.pdf"
    resp = make_response(buf.read())
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f"attachment; filename={fname}"
    return resp


# =====================================================================
# HSE MODULE — Safety Officer Tracking
# =====================================================================

ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
HSE_UPLOAD_DIR = os.path.join(UPLOAD_FOLDER, "hse")


# ── PDF Arabic support (shared) ──────────────────────────────────────
# يُسجَّل مرة واحدة ويُعاد استخدامه في كل تقارير الـ PDF.
# DejaVu Sans مقصود: يغطي العربي واللاتيني والأرقام والرموز بخط واحد،
# بينما Noto Naskh عربي فقط فتظهر الأسماء الإنجليزية والأرقام كمربعات.
_PDF_AR_FONT = None

def pdf_arabic_font():
    """يسجّل خطاً يدعم العربية ويعيد اسمه — أو 'Helvetica' إن لم يوجد."""
    global _PDF_AR_FONT
    if _PDF_AR_FONT:
        return _PDF_AR_FONT
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        _PDF_AR_FONT = "Helvetica"
        return _PDF_AR_FONT

    for path in (
        os.path.join(BASE_DIR, "static", "fonts", "DejaVuSans.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        os.path.join(BASE_DIR, "static", "fonts", "NotoNaskhArabic-Regular.ttf"),
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        os.path.join(BASE_DIR, "static", "fonts", "DroidSansArabic.ttf"),
        "/usr/share/fonts/google-droid/DroidSansArabic.ttf",
    ):
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("Arabic", path))
                # نسخة عريضة إن توفّرت، وإلا نعيد استخدام العادي
                bold = path.replace("-Regular", "-Bold").replace("DejaVuSans.ttf",
                                                                 "DejaVuSans-Bold.ttf")
                try:
                    if os.path.exists(bold):
                        pdfmetrics.registerFont(TTFont("Arabic-Bold", bold))
                    else:
                        pdfmetrics.registerFont(TTFont("Arabic-Bold", path))
                except Exception:
                    pass
                _PDF_AR_FONT = "Arabic"
                app.logger.info("PDF Arabic font registered: %s", path)
                return _PDF_AR_FONT
            except Exception as e:
                app.logger.warning("font register failed %s: %s", path, e)
                continue

    app.logger.warning("No Arabic-capable TTF found — Arabic will render as boxes.")
    _PDF_AR_FONT = "Helvetica"
    return _PDF_AR_FONT


def pdf_ar(text):
    """يشكّل النص العربي ويعكسه ليُعرض صحيحاً في ReportLab (RTL).

    النص الخالي من العربية يُعاد كما هو دون تكلفة.
    """
    t = "" if text is None else str(text)
    if not any("\u0600" <= c <= "\u06FF" for c in t):
        return t
    try:
        from arabic_reshaper import reshape
        from bidi.algorithm import get_display
        return get_display(reshape(t))
    except Exception:
        try:
            from bidi.algorithm import get_display
            return get_display(t)
        except Exception:
            return t



def _parse_date(s, default=None):
    s = (s or "").strip()
    if not s:
        return default
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return default
os.makedirs(HSE_UPLOAD_DIR, exist_ok=True)
OBS_CATEGORIES = [
    "PPE", "Excavation", "Housekeeping", "Barrier Management",
    "Vehicle & Equipment", "Work at Height", "Lifting Operations",
    "Line of Fire", "Control of Chemicals", "High Risk Situation", "Other",
]


def _allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def _save_hse_photo(file, prefix, company_id=None):
    if not file or not file.filename:
        return None
    if not _allowed_image(file.filename):
        return None
    ext = file.filename.rsplit(".", 1)[1].lower()
    fname = f"hse_{prefix}_{uuid.uuid4().hex[:10]}.{ext}"
    if company_id:
        subfolder = os.path.join(HSE_UPLOAD_DIR, str(company_id))
        os.makedirs(subfolder, exist_ok=True)
        file.save(os.path.join(subfolder, fname))
        return f"{company_id}/{fname}"
    file.save(os.path.join(HSE_UPLOAD_DIR, fname))
    return fname


def hse_officer_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        u = cur_user()
        if not u:
            return redirect(url_for("login"))
        if u.role not in ("safety_officer", "safety_supervisor", "safety_manager",
                          "admin", "super_admin"):
            flash("This page is for safety officers only.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return inner


HSE_SUPERVISOR_CODE = os.environ.get("HSE_SUPERVISOR_CODE", "39468")


def is_safety_manager(u):
    if not u or not getattr(u, "is_active", False):
        return False
    return getattr(u, "role", None) in ("safety_manager", "super_admin", "admin")


def safety_manager_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        u = cur_user()
        if not u:
            return redirect(url_for("login"))
        if not is_safety_manager(u):
            flash("Access restricted to Safety Managers.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return inner


def hse_supervisor_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        u = cur_user()
        if not u:
            return redirect(url_for("login"))
        if not is_hse_supervisor(u):
            flash("This page is for supervisors only.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return inner


# ── HSE Models ──────────────────────────────────────────────────────

class HseCheckin(db.Model):
    __tablename__ = "hse_checkin"
    id            = db.Column(db.Integer, primary_key=True)
    officer_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    date          = db.Column(db.Date, nullable=False)
    location      = db.Column(db.String(255), nullable=False)
    supervisor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    company_id    = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("officer_id", "date", name="uq_hse_checkin_od"),)


class HseObservation(db.Model):
    __tablename__ = "hse_observation"
    id             = db.Column(db.Integer, primary_key=True)
    officer_id     = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    date           = db.Column(db.Date, nullable=False)
    location       = db.Column(db.String(255))
    obs_type       = db.Column(db.Enum("unsafe_act", "unsafe_condition", "positive"), nullable=False)
    category       = db.Column(db.String(100))
    risk_level     = db.Column(db.Enum("L", "M", "H"))
    description    = db.Column(db.Text)
    action_taken   = db.Column(db.Text)
    status         = db.Column(db.Enum("open", "closed"), default="open")
    closed_at      = db.Column(db.Date, nullable=True)
    closure_action = db.Column(db.Text, nullable=True)
    company_id     = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    photos         = db.relationship("HseObservationPhoto", backref="observation", lazy="dynamic")
    officer        = db.relationship("User", foreign_keys=[officer_id])


class HseObservationPhoto(db.Model):
    __tablename__ = "hse_observation_photos"
    id             = db.Column(db.Integer, primary_key=True)
    observation_id = db.Column(db.Integer, db.ForeignKey("hse_observation.id"), nullable=False)
    photo_path     = db.Column(db.String(500))
    photo_type     = db.Column(db.Enum("before", "after"))
    uploaded_at    = db.Column(db.DateTime, default=datetime.utcnow)
    company_id     = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)


class HseJsoClosure(db.Model):
    __tablename__ = "hse_jso_closure"
    id           = db.Column(db.Integer, primary_key=True)
    officer_id   = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    jso_number   = db.Column(db.String(50), nullable=False)
    date         = db.Column(db.Date, nullable=False)
    location     = db.Column(db.String(255))
    action_taken = db.Column(db.Text)
    photo_path   = db.Column(db.String(500), nullable=True)
    company_id   = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)


class HseTbt(db.Model):
    __tablename__ = "hse_tbt"
    id              = db.Column(db.Integer, primary_key=True)
    officer_id      = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    date            = db.Column(db.Date, nullable=False)
    topic           = db.Column(db.String(255))
    location        = db.Column(db.String(255))
    supervisor_id   = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    sign_photo_path = db.Column(db.String(500), nullable=True)
    company_id      = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    attendance      = db.relationship("HseTbtAttendance", backref="tbt", lazy="dynamic")


class HseTbtAttendance(db.Model):
    __tablename__ = "hse_tbt_attendance"
    id         = db.Column(db.Integer, primary_key=True)
    tbt_id     = db.Column(db.Integer, db.ForeignKey("hse_tbt.id"), nullable=False)
    emp_number = db.Column(db.String(50))
    emp_name   = db.Column(db.String(120))
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)


class HseNearMiss(db.Model):
    __tablename__ = "hse_near_miss"
    id              = db.Column(db.Integer, primary_key=True)
    officer_id      = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    date            = db.Column(db.Date, nullable=False)
    location        = db.Column(db.String(255))
    description     = db.Column(db.Text)
    immediate_cause = db.Column(db.Text)
    action_taken    = db.Column(db.Text)
    reported_to     = db.Column(db.String(255))
    photo_path      = db.Column(db.String(500), nullable=True)
    company_id      = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)


class HseBbs(db.Model):
    __tablename__ = "hse_bbs"
    id          = db.Column(db.Integer, primary_key=True)
    officer_id  = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    date        = db.Column(db.Date, nullable=False)
    card_count  = db.Column(db.Integer, nullable=False)
    notes       = db.Column(db.Text, nullable=True)
    company_id  = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("officer_id", "date", name="uq_hse_bbs_od"),)


class HseSupervisorAccess(db.Model):
    """مستخدمون منحهم الأدمن صلاحية لوحة تحكم HSE"""
    __tablename__ = "hse_supervisor_access"
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True)
    granted_at = db.Column(db.DateTime, default=lambda: datetime.now(RIYADH_TZ))
    granted_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)


class UserLocation(db.Model):
    """موقع المستخدم اليومي — يُحدَّث يدوياً من التطبيق أو الموقع"""
    __tablename__ = "user_location"
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True)
    pkg        = db.Column(db.Integer)           # 2 أو 3
    unit       = db.Column(db.String(20))        # مثال: "320", "500"
    area_text  = db.Column(db.String(100))       # مثال: "str3000", "sub station"
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    user       = db.relationship("User", backref=db.backref("location", uselist=False))


# ── Phase 2 Models ────────────────────────────────────────────────────

class HsePtw(db.Model):
    __tablename__ = "hse_ptw"
    id             = db.Column(db.Integer, primary_key=True)
    officer_id     = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id     = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    permit_number  = db.Column(db.String(50), nullable=False)
    permit_type    = db.Column(db.String(50), nullable=False)
    description    = db.Column(db.Text)
    location       = db.Column(db.String(255))
    week_start     = db.Column(db.Date, nullable=False)
    week_end       = db.Column(db.Date, nullable=False)
    status         = db.Column(db.Enum("active", "suspended", "closed"), default="active")
    attached_to_id = db.Column(db.Integer, db.ForeignKey("hse_ptw.id"), nullable=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)


class HseManpower(db.Model):
    __tablename__ = "hse_manpower"
    id          = db.Column(db.Integer, primary_key=True)
    officer_id  = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id  = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date        = db.Column(db.Date, nullable=False)
    location    = db.Column(db.String(255))
    total_count = db.Column(db.Integer, nullable=False)
    breakdown   = db.Column(db.Text, nullable=True)
    notes       = db.Column(db.Text, nullable=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("officer_id", "date", name="uq_hse_manpower_od"),)


class HseCorrectiveAction(db.Model):
    __tablename__ = "hse_corrective_action"
    id               = db.Column(db.Integer, primary_key=True)
    observation_id   = db.Column(db.Integer, db.ForeignKey("hse_observation.id"), nullable=False)
    company_id       = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    assigned_to      = db.Column(db.String(255))
    due_date         = db.Column(db.Date, nullable=False)
    action_required  = db.Column(db.Text, nullable=False)
    status           = db.Column(db.Enum("open", "in_progress", "completed"), default="open")
    completed_at     = db.Column(db.Date, nullable=True)
    completion_notes = db.Column(db.Text, nullable=True)
    created_by       = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)
    observation      = db.relationship("HseObservation", backref="corrective_actions")


INSPECTION_ITEMS = [
    "PPE Compliance (Helmet, Vest, Boots)",
    "Housekeeping & Work Area Clean",
    "Barricading & Warning Signs in Place",
    "Fire Extinguisher Accessible",
    "PTW Displayed at Work Site",
    "First Aid Kit Available",
    "No Unauthorized Personnel in Area",
    "Electrical Safety (No Exposed Wires)",
    "Scaffolding Tagged & Inspected",
    "Emergency Exits Clear",
]


class HseInspection(db.Model):
    __tablename__ = "hse_inspection"
    id            = db.Column(db.Integer, primary_key=True)
    officer_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id    = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date          = db.Column(db.Date, nullable=False)
    location      = db.Column(db.String(255))
    checklist     = db.Column(db.Text, nullable=False)
    overall_score = db.Column(db.Float, nullable=True)
    notes         = db.Column(db.Text, nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("officer_id", "date", name="uq_hse_inspection_od"),)


def is_hse_supervisor(u):
    """صحيح إذا كان المستخدم لديه صلاحية HSE supervisor.

    السيفتي مانجر أعلى من السيفتي سوبرفايزر تنظيمياً، فيرث صلاحياته
    (عرض تقارير وتفاصيل الضباط). كذلك الأدمن وسوبر أدمن.
    """
    if not u or not getattr(u, "is_active", False):
        return False
    if getattr(u, "role", None) in ("safety_supervisor", "safety_manager",
                                    "admin", "super_admin"):
        return True
    if getattr(u, "supervisor_code", None) == HSE_SUPERVISOR_CODE:
        return True
    return HseSupervisorAccess.query.filter_by(user_id=u.id).first() is not None


@app.context_processor
def inject_hse_flag():
    from flask import g as _g
    u = getattr(_g, "user", None)
    try:
        flag = is_hse_supervisor(u)
    except Exception:
        flag = False
    try:
        mgr_flag = is_safety_manager(u)
    except Exception:
        mgr_flag = False
    return {"g_is_hse_supervisor": flag, "g_is_safety_manager": mgr_flag}


# ── HSE Helpers ──────────────────────────────────────────────────────

def _hse_today_location(officer_id):
    today = datetime.now(RIYADH_TZ).date()
    from sqlalchemy import text as _text
    row = db.session.execute(
        _text("SELECT location FROM hse_checkin WHERE officer_id=:oid AND date=:dt LIMIT 1"),
        {"oid": officer_id, "dt": today}
    ).fetchone()
    return row[0] if row else ""


def _hse_locations():
    q = db.session.query(HseCheckin.location,
                         func.count(HseCheckin.location).label("cnt"))
    _cid = cid()
    if _cid:
        q = q.filter(HseCheckin.company_id == _cid)
    rows = q.group_by(HseCheckin.location).order_by(func.count(HseCheckin.location).desc()).all()
    return [r.location for r in rows]


def _amiral_units(company_id=None):
    """Returns list of (display_label, value) tuples for registered pkg/unit combos.
    Uses UserLocation data so the dropdown reflects actual registered units.
    Falls back to empty list if no locations registered yet."""
    try:
        q = db.session.query(
            UserLocation.pkg, UserLocation.unit
        ).filter(UserLocation.pkg != None, UserLocation.unit != None)
        if company_id:
            q = q.filter(UserLocation.company_id == company_id)
        rows = q.distinct().order_by(UserLocation.pkg, UserLocation.unit).all()
        result = []
        seen = set()
        for pkg, unit in rows:
            val = f"PKG{pkg}/{unit}"
            if val not in seen:
                seen.add(val)
                label = f"PKG-0{pkg} — وحدة {unit}" if pkg < 10 else f"PKG-{pkg} — وحدة {unit}"
                result.append((label, val))
        return result
    except Exception:
        return []


def _notify_hse_supervisors(title, body):
    """يرسل push notification لكل من لديه صلاحية HSE supervisor"""
    import threading as _thr
    # المشرف الأساسي
    primary = User.query.filter_by(supervisor_code=HSE_SUPERVISOR_CODE, is_active=True).first()
    recipients = {primary.id} if primary else set()
    # المستخدمون الآخرون الممنوحة لهم الصلاحية
    for acc in HseSupervisorAccess.query.all():
        u = db.session.get(User, acc.user_id)
        if u and u.is_active:
            recipients.add(u.id)
    for uid in recipients:
        _thr.Thread(target=_send_push_to_user, args=(uid, title, body), daemon=True).start()


# ── HSE: Serve photos ────────────────────────────────────────────────

@app.get("/hse/photo/<path:filename>")
@login_required
def hse_photo(filename):
    from flask import send_from_directory
    return send_from_directory(HSE_UPLOAD_DIR, filename)


# ── HSE API ──────────────────────────────────────────────────────────

@app.get("/hse/api/locations")
@login_required
def hse_api_locations():
    return jsonify(_hse_locations())


@app.get("/hse/api/supervisor_lookup")
@login_required
def hse_supervisor_lookup():
    code = request.args.get("code", "").strip()
    if not code:
        return jsonify({"found": False})
    sup = User.query.filter(User.supervisor_code == code, User.is_active == True).first()
    if sup:
        return jsonify({"found": True, "name": sup.name})
    return jsonify({"found": False})


@app.get("/hse/api/employee_lookup")
@login_required
def hse_employee_lookup():
    emp_num = request.args.get("emp_number", "").strip()
    if not emp_num:
        return jsonify({"found": False})
    emp = Employee.query.filter_by(emp_number=emp_num, status="active").first()
    if emp:
        return jsonify({"found": True, "name": emp.name})
    for prefix in ("NSH-", "GA-"):
        if emp_num.upper().startswith(prefix):
            bare = emp_num[len(prefix):]
            emp = Employee.query.filter_by(emp_number=bare, status="active").first()
            if emp:
                return jsonify({"found": True, "name": emp.name})
        cand = Employee.query.filter_by(emp_number=f"{prefix}{emp_num}", status="active").first()
        if cand:
            return jsonify({"found": True, "name": cand.name})
    return jsonify({"found": False})


# ── HSE: Daily Check-in ───────────────────────────────────────────────

@app.route("/hse/checkin", methods=["GET", "POST"])
@login_required
@hse_officer_required
def hse_checkin():
    u = cur_user()
    today = datetime.now(RIYADH_TZ).date()

    # safety_officer: location IS the check-in — redirect to location page
    if u.role == "safety_officer":
        return redirect(url_for("user_location_page"))

    # safety_supervisor / safety_manager: show all officers' locations today
    if u.role in ("safety_supervisor", "safety_manager", "admin", "super_admin"):
        officers = _get_safety_officers(u)
        officer_ids = [o.id for o in officers]
        locs = {l.user_id: l for l in
                UserLocation.query.filter(UserLocation.user_id.in_(officer_ids)).all()}
        rows = []
        for o in officers:
            loc = locs.get(o.id)
            present = False
            loc_text = None
            if loc and loc.updated_at:
                lu = (loc.updated_at.replace(tzinfo=None) if loc.updated_at.tzinfo is None
                      else loc.updated_at.astimezone(RIYADH_TZ).replace(tzinfo=None))
                present = lu.date() == today
                if present:
                    parts = ([f"PKG{loc.pkg}"] if loc.pkg else []) + \
                            ([f"Unit {loc.unit}"] if loc.unit else []) + \
                            ([loc.area_text] if loc.area_text else [])
                    loc_text = " · ".join(parts)
            rows.append({"officer": o, "present": present, "location": loc_text})
        return render_template("hse_checkin_supervisor.html",
                               rows=rows, today=today)

    existing = HseCheckin.query.filter_by(officer_id=u.id, date=today).first()
    locations = _hse_locations()

    if request.method == "POST":
        location = request.form.get("location", "").strip()
        sup_code = request.form.get("supervisor_code", "").strip()

        if not location:
            flash("Location is required.", "warning")
            return redirect(url_for("hse_checkin"))

        supervisor = None
        if sup_code:
            supervisor = User.query.filter(
                User.supervisor_code == sup_code, User.is_active == True
            ).first()
            if not supervisor:
                flash("Supervisor code not found.", "warning")
                return redirect(url_for("hse_checkin"))

        if existing:
            existing.location = location
            existing.supervisor_id = supervisor.id if supervisor else None
            flash("Check-in updated.", "success")
        else:
            ci = HseCheckin(
                officer_id=u.id, date=today, location=location,
                supervisor_id=supervisor.id if supervisor else None,
                company_id=cid(),
            )
            db.session.add(ci)
            flash("Checked in successfully.", "success")

        db.session.commit()
        return redirect(url_for("hse_checkin"))

    checkin_supervisor = None
    if existing and existing.supervisor_id:
        checkin_supervisor = db.session.get(User, existing.supervisor_id)

    return render_template("hse_checkin.html",
                           existing=existing, today=today,
                           locations=locations, checkin_supervisor=checkin_supervisor)


# ── HSE: Daily Observations ───────────────────────────────────────────

_OBS_PAGE_ROLES = (
    "safety_officer", "safety_welfare", "environment_officer",
    "safety_supervisor", "safety_manager", "admin", "super_admin",
)


@app.route("/hse/observations", methods=["GET"])
@login_required
def hse_observations():
    u = cur_user()
    if not u or getattr(u, "role", None) not in _OBS_PAGE_ROLES:
        flash("Access denied.", "danger")
        return redirect(url_for("index"))
    _c = cid()
    status_filter = request.args.get("status", "")
    page = request.args.get("page", 1, type=int)
    is_supervisor = u.role not in ("safety_officer", "safety_welfare", "environment_officer")

    if is_supervisor:
        safety_ids = [o.id for o in _get_safety_officers(u)]
        wlf_env = User.query.filter(
            User.role.in_(["safety_welfare", "environment_officer"]),
            User.company_id == _c, User.is_active == True,
        ).with_entities(User.id).all()
        all_ids = safety_ids + [r.id for r in wlf_env]
        q = HseObservation.query.filter(
            HseObservation.officer_id.in_(all_ids),
            HseObservation.company_id == _c,
        )
    else:
        q = HseObservation.query.filter_by(officer_id=u.id, company_id=_c)

    if status_filter in ("open", "closed"):
        q = q.filter(HseObservation.status == status_filter)
    pagination = q.order_by(HseObservation.date.desc()).paginate(
        page=page, per_page=15, error_out=False)
    obs = pagination.items
    obs_photos = {o.id: list(o.photos) for o in obs}
    return render_template("hse_observations.html",
                           obs=obs, obs_photos=obs_photos,
                           status_filter=status_filter, pagination=pagination,
                           is_supervisor=is_supervisor, cur_user_id=u.id,
                           cur_role=u.role)


@app.route("/hse/observation/<int:obs_id>/view", methods=["GET"])
@login_required
def hse_observation_view(obs_id):
    u = cur_user()
    obs = HseObservation.query.get_or_404(obs_id)
    # officer sees own obs; supervisor/manager sees company obs
    if not (
        obs.officer_id == u.id
        or is_hse_supervisor(u)
        or is_safety_manager(u)
        or getattr(u, "role", "") in ("admin", "super_admin")
    ):
        flash("Access denied.", "danger")
        return redirect(url_for("index"))
    photos = list(obs.photos)
    cas    = HseCorrectiveAction.query.filter_by(observation_id=obs.id).all()
    officer = User.query.get(obs.officer_id)
    return render_template_string("""
{% extends "base.html" %}
{% block title %}Observation #{{ obs.id }}{% endblock %}
{% block content %}
<style>
.obs-view-card{background:#fff;border:1px solid #e2e8f0;border-radius:12px;
  padding:22px 24px;max-width:820px;margin:0 auto}
.obs-meta-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));
  gap:12px;margin:16px 0 20px}
.obs-meta-item .label{font-size:11px;font-weight:700;text-transform:uppercase;
  color:#64748b;margin-bottom:3px}
.obs-meta-item .val{font-size:13px;font-weight:600;color:#0f172a}
.risk-H{background:#fee2e2;color:#b91c1c;padding:2px 10px;border-radius:6px;
  font-size:12px;font-weight:700}
.risk-M{background:#fef3c7;color:#92400e;padding:2px 10px;border-radius:6px;
  font-size:12px;font-weight:700}
.risk-L{background:#dcfce7;color:#166534;padding:2px 10px;border-radius:6px;
  font-size:12px;font-weight:700}
.photo-row{display:flex;flex-wrap:wrap;gap:10px;margin-top:10px}
.photo-thumb{border-radius:8px;object-fit:cover;border:1px solid #e2e8f0;
  cursor:pointer;transition:transform .15s}
.photo-thumb:hover{transform:scale(1.04)}
.ca-row{background:#f8fafc;border-radius:8px;padding:10px 14px;margin-bottom:8px;
  border-left:3px solid #f59e0b}
.ca-row.done{border-color:#22c55e}
</style>

<div class="obs-view-card">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;
              flex-wrap:wrap;gap:10px;margin-bottom:4px">
    <h2 style="margin:0;font-size:18px">
      Observation #{{ obs.id }}
      <span class="risk-{{ obs.risk_level }}">{{ obs.risk_level }}-Risk</span>
    </h2>
    <div style="display:flex;gap:8px">
      <a href="{{ url_for('hse_observations') }}" class="btn" style="font-size:12px">← Back</a>
      {% if obs.status == 'open' and obs.officer_id == g.user.id %}
      <a href="{{ url_for('hse_observation_close', obs_id=obs.id) }}"
         class="btn" style="background:#dc2626;color:#fff;font-size:12px">Close Obs</a>
      {% endif %}
    </div>
  </div>
  <div style="font-size:12px;color:#64748b;margin-bottom:16px">
    {% if obs.status == 'open' %}
      <span style="background:#fef3c7;color:#92400e;padding:1px 10px;border-radius:10px;
                   font-size:11px;font-weight:700">Open</span>
    {% else %}
      <span style="background:#dcfce7;color:#166534;padding:1px 10px;border-radius:10px;
                   font-size:11px;font-weight:700">Closed</span>
    {% endif %}
    &nbsp;{{ obs.date.strftime('%d %b %Y') if obs.date else '—' }}
    {% if officer %} &nbsp;·&nbsp; {{ officer.name }}{% endif %}
  </div>

  <div class="obs-meta-grid">
    <div class="obs-meta-item">
      <div class="label">Location</div>
      <div class="val">{{ obs.location or '—' }}</div>
    </div>
    <div class="obs-meta-item">
      <div class="label">Type</div>
      <div class="val">{{ obs.obs_type or '—' }}</div>
    </div>
    <div class="obs-meta-item">
      <div class="label">Category</div>
      <div class="val">{{ obs.category or '—' }}</div>
    </div>
    <div class="obs-meta-item">
      <div class="label">Risk Level</div>
      <div class="val"><span class="risk-{{ obs.risk_level }}">{{ obs.risk_level }}</span></div>
    </div>
  </div>

  {% if obs.description %}
  <div style="margin-bottom:14px">
    <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;
                margin-bottom:4px">Description</div>
    <div style="font-size:13px;line-height:1.6;color:#1e293b">{{ obs.description }}</div>
  </div>
  {% endif %}

  {% if obs.action_taken %}
  <div style="margin-bottom:14px">
    <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;
                margin-bottom:4px">Immediate Action Taken</div>
    <div style="font-size:13px;line-height:1.6;color:#1e293b">{{ obs.action_taken }}</div>
  </div>
  {% endif %}

  {% if photos %}
  <div style="margin-bottom:16px">
    <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;
                margin-bottom:8px">Photos</div>
    <div class="photo-row">
      {% for ph in photos %}
      <a href="{{ url_for('hse_photo', filename=ph.photo_path) }}" target="_blank">
        <img src="{{ url_for('hse_photo', filename=ph.photo_path) }}"
             class="photo-thumb" width="120" height="90"
             title="{{ ph.photo_type }}" alt="{{ ph.photo_type }}">
      </a>
      {% endfor %}
    </div>
  </div>
  {% endif %}

  {% if cas %}
  <div style="border-top:1px solid #e2e8f0;padding-top:14px;margin-top:4px">
    <div style="font-size:12px;font-weight:700;color:#0f172a;margin-bottom:10px">
      Corrective Actions ({{ cas|length }})
    </div>
    {% for ca in cas %}
    <div class="ca-row {{ 'done' if ca.status == 'completed' }}">
      <div style="font-weight:600;font-size:13px">{{ ca.action_required }}</div>
      <div style="font-size:11px;color:#64748b;margin-top:3px">
        Due: {{ ca.due_date.strftime('%d %b %Y') if ca.due_date else '—' }}
        {% if ca.assigned_to %} · {{ ca.assigned_to }}{% endif %}
        · Status: <strong>{{ ca.status }}</strong>
      </div>
    </div>
    {% endfor %}
  </div>
  {% endif %}

</div>
{% endblock %}
""", obs=obs, photos=photos, cas=cas, officer=officer)


@app.route("/hse/observation/new", methods=["GET", "POST"])
@login_required
@hse_officer_required
def hse_observation_new():
    u = cur_user()
    today = datetime.now(RIYADH_TZ).date()
    today_location = _hse_today_location(u.id)
    locations = _hse_locations()

    if request.method == "POST":
        obs_date = _safe_date(request.form.get("date")) or today
        location = request.form.get("location", "").strip()
        obs_type = request.form.get("obs_type", "").strip()
        category = request.form.get("category", "").strip()
        risk_level = request.form.get("risk_level", "").strip() or None
        description = request.form.get("description", "").strip()
        action_taken = request.form.get("action_taken", "").strip()

        if not obs_type:
            flash("Observation type is required.", "warning")
            return redirect(url_for("hse_observation_new"))

        try:
            obs = HseObservation(
                officer_id=u.id, date=obs_date, location=location,
                obs_type=obs_type, category=category, risk_level=risk_level,
                description=description, action_taken=action_taken,
                company_id=cid(),
            )
            db.session.add(obs)
            db.session.flush()

            f = request.files.get("photo_1")
            path = _save_hse_photo(f, "obs", company_id=cid())
            if path:
                db.session.add(HseObservationPhoto(
                    observation_id=obs.id, photo_path=path, photo_type="before"
                ))

            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            app.logger.error("hse_observation_new failed: %s", exc)
            flash("حدث خطأ أثناء الحفظ، حاول مرة أخرى.", "danger")
            return redirect(url_for("hse_observation_new"))

        flash("Observation saved.", "success")
        if risk_level == "H":
            _notify_hse_supervisors("⚠ High-Risk Observation",
                                    f"{u.name}: {category or 'No category'} at {location}")
        return redirect(url_for("hse_observations"))

    return render_template("hse_observation_new.html",
                           today=today, today_location=today_location,
                           locations=locations, categories=OBS_CATEGORIES)


@app.route("/hse/observation/<int:obs_id>/close", methods=["GET", "POST"])
@login_required
def hse_observation_close(obs_id):
    u = cur_user()
    if not u or getattr(u, "role", None) not in _OBS_PAGE_ROLES:
        flash("Access denied.", "danger")
        return redirect(url_for("index"))
    obs = HseObservation.query.filter_by(id=obs_id, officer_id=u.id).first_or_404()

    if obs.status == "closed":
        flash("This observation is already closed.", "info")
        return redirect(url_for("hse_observations", status="closed"))

    if request.method == "POST":
        closure_action = request.form.get("closure_action", "").strip()
        if not closure_action:
            flash("Closure action is required.", "warning")
            return redirect(url_for("hse_observation_close", obs_id=obs_id))

        f = request.files.get("closure_photo")
        path = _save_hse_photo(f, "obs_close", company_id=cid())
        if path:
            db.session.add(HseObservationPhoto(
                observation_id=obs.id, photo_path=path, photo_type="after"
            ))

        obs.status = "closed"
        obs.closed_at = datetime.now(RIYADH_TZ).date()
        obs.closure_action = closure_action
        db.session.commit()
        flash("Observation closed successfully.", "success")
        return redirect(url_for("hse_observations", status="closed"))

    return render_template("hse_observation_close.html", obs=obs)


# ── HSE: JSO Closure ──────────────────────────────────────────────────

@app.route("/hse/jso", methods=["GET"])
@login_required
@hse_officer_required
def hse_jso_list():
    u = cur_user()
    page = request.args.get("page", 1, type=int)
    pagination = (HseJsoClosure.query.filter_by(officer_id=u.id)
                  .order_by(HseJsoClosure.date.desc())
                  .paginate(page=page, per_page=15, error_out=False))
    return render_template("hse_jso.html", items=pagination.items, pagination=pagination)


@app.route("/hse/jso/new", methods=["GET", "POST"])
@login_required
@hse_officer_required
def hse_jso_new():
    u = cur_user()
    today = datetime.now(RIYADH_TZ).date()
    today_location = _hse_today_location(u.id)
    locations = _hse_locations()

    if request.method == "POST":
        jso_number = request.form.get("jso_number", "").strip()
        jso_date = _safe_date(request.form.get("date")) or today
        location = request.form.get("location", "").strip()
        action_taken = request.form.get("action_taken", "").strip()

        if not jso_number:
            flash("JSO number is required.", "warning")
            return redirect(url_for("hse_jso_new"))

        photo_path = _save_hse_photo(request.files.get("photo"), "jso", company_id=cid())

        db.session.add(HseJsoClosure(
            officer_id=u.id, jso_number=jso_number, date=jso_date,
            location=location, action_taken=action_taken, photo_path=photo_path,
            company_id=cid(),
        ))
        db.session.commit()
        flash("JSO closure recorded.", "success")
        return redirect(url_for("hse_jso_list"))

    return render_template("hse_jso_new.html",
                           today=today, today_location=today_location, locations=locations)


# ── HSE: TBT ──────────────────────────────────────────────────────────

# ── SGL — Safety Green Light (عرض) ───────────────────────────────

@app.route("/hse/sgl", methods=["GET"])
@login_required
@hse_officer_required
def hse_sgl_view():
    """صفحة عرض جلسات الـ SGL — تصفية بالتاريخ والضابط، مع الحضور والتوقيع."""
    u = cur_user()

    d_from = _safe_date(request.args.get("from"))
    d_to   = _safe_date(request.args.get("to"))
    today  = datetime.now(RIYADH_TZ).date()
    if not d_to:
        d_to = today
    if not d_from:
        d_from = d_to - timedelta(days=13)

    officer_id = request.args.get("officer_id", type=int)

    if u.role in ("safety_supervisor", "safety_manager", "admin", "super_admin"):
        officers = _get_safety_officers(u)
    else:
        officers = [u]
    officer_ids = [o.id for o in officers]

    q = HseTbt.query.filter(HseTbt.officer_id.in_(officer_ids),
                            HseTbt.date >= d_from, HseTbt.date <= d_to)
    if officer_id and officer_id in officer_ids:
        q = q.filter(HseTbt.officer_id == officer_id)
    sessions = q.order_by(HseTbt.date.desc(), HseTbt.id.desc()).all()

    # الحضور دفعة واحدة
    ids = [t.id for t in sessions]
    att_map = {}
    if ids:
        for a in HseTbtAttendance.query.filter(HseTbtAttendance.tbt_id.in_(ids)).all():
            att_map.setdefault(a.tbt_id, []).append(a)

    off_map = {o.id: (o.name or o.supervisor_code) for o in officers}

    # مشرفو الموظفين الحاضرين — قد لا يكونون ضمن قائمة الضباط
    sup_ids = {t.supervisor_id for t in sessions if t.supervisor_id}
    sup_map = ({x.id: (x.name or x.supervisor_code)
                for x in User.query.filter(User.id.in_(list(sup_ids))).all()}
               if sup_ids else {})

    # تجميع حسب اليوم
    by_day = {}
    for t in sessions:
        by_day.setdefault(t.date, []).append(t)
    days = sorted(by_day.keys(), reverse=True)

    total_att = sum(len(att_map.get(t.id, [])) for t in sessions)

    return render_template("hse_sgl.html",
                           days=days, by_day=by_day, att_map=att_map,
                           off_map=off_map, sup_map=sup_map, officers=officers,
                           officer_id=officer_id,
                           d_from=d_from, d_to=d_to, today=today,
                           total_sessions=len(sessions), total_att=total_att)


@app.route("/hse/tbt", methods=["GET"])
@login_required
@hse_officer_required
def hse_tbt_list():
    u = cur_user()
    page = request.args.get("page", 1, type=int)
    if u.role in ("safety_supervisor", "safety_manager", "admin", "super_admin"):
        officer_ids = [o.id for o in _get_safety_officers(u)]
        base_q = HseTbt.query.filter(HseTbt.officer_id.in_(officer_ids))
    else:
        base_q = HseTbt.query.filter_by(officer_id=u.id)
    pagination = (base_q
                  .order_by(HseTbt.date.desc())
                  .paginate(page=page, per_page=15, error_out=False))
    tbts = pagination.items
    tbt_ids = [t.id for t in tbts]
    tbt_counts = dict(
        db.session.query(HseTbtAttendance.tbt_id, func.count())
        .filter(HseTbtAttendance.tbt_id.in_(tbt_ids))
        .group_by(HseTbtAttendance.tbt_id).all()
    ) if tbt_ids else {}
    sup_ids = {t.supervisor_id for t in tbts if t.supervisor_id}
    supervisors = {u.id: u.name for u in User.query.filter(User.id.in_(sup_ids)).all()} if sup_ids else {}
    return render_template("hse_tbt.html", tbts=tbts, tbt_counts=tbt_counts,
                           pagination=pagination, supervisors=supervisors)


@app.route("/hse/tbt/new", methods=["GET", "POST"])
@login_required
@hse_officer_required
def hse_tbt_new():
    u = cur_user()
    today = datetime.now(RIYADH_TZ).date()
    today_location = _hse_today_location(u.id)
    locations = _hse_locations()

    if request.method == "POST":
        tbt_date = _safe_date(request.form.get("date")) or today
        topic = request.form.get("topic", "").strip()
        location = request.form.get("location", "").strip()
        sup_code = request.form.get("supervisor_code", "").strip()

        supervisor = None
        if sup_code:
            supervisor = User.query.filter(
                User.supervisor_code == sup_code, User.is_active == True
            ).first()

        sign_path = _save_hse_photo(request.files.get("sign_photo"), "tbt", company_id=cid())

        tbt = HseTbt(
            officer_id=u.id, date=tbt_date, topic=topic, location=location,
            supervisor_id=supervisor.id if supervisor else None,
            sign_photo_path=sign_path,
            company_id=cid(),
        )
        db.session.add(tbt)
        db.session.flush()

        emp_numbers = request.form.getlist("emp_number[]")
        emp_names = request.form.getlist("emp_name[]")
        for num, name in zip(emp_numbers, emp_names):
            num = num.strip(); name = name.strip()
            if num and name:
                db.session.add(HseTbtAttendance(tbt_id=tbt.id, emp_number=num, emp_name=name))

        db.session.commit()
        flash("SGL session saved.", "success")
        return redirect(url_for("hse_tbt_list"))

    return render_template("hse_tbt_new.html",
                           today=today, today_location=today_location, locations=locations)


# ── HSE: Near Miss ────────────────────────────────────────────────────

@app.route("/hse/nearmiss", methods=["GET"])
@login_required
@hse_officer_required
def hse_nearmiss_list():
    u = cur_user()
    page = request.args.get("page", 1, type=int)
    pagination = (HseNearMiss.query.filter_by(officer_id=u.id)
                  .order_by(HseNearMiss.date.desc())
                  .paginate(page=page, per_page=15, error_out=False))
    return render_template("hse_nearmiss.html", items=pagination.items, pagination=pagination)


@app.route("/hse/nearmiss/new", methods=["GET", "POST"])
@login_required
@hse_officer_required
def hse_nearmiss_new():
    u = cur_user()
    today = datetime.now(RIYADH_TZ).date()
    today_location = _hse_today_location(u.id)
    locations = _hse_locations()

    if request.method == "POST":
        nm_date = _safe_date(request.form.get("date")) or today
        location = request.form.get("location", "").strip()
        description = request.form.get("description", "").strip()
        immediate_cause = request.form.get("immediate_cause", "").strip()
        action_taken = request.form.get("action_taken", "").strip()
        reported_to = request.form.get("reported_to", "").strip()

        if not all([location, description, immediate_cause, action_taken, reported_to]):
            flash("All fields except photo are required.", "warning")
            return redirect(url_for("hse_nearmiss_new"))

        photo_path = _save_hse_photo(request.files.get("photo"), "nm", company_id=cid())

        db.session.add(HseNearMiss(
            officer_id=u.id, date=nm_date, location=location,
            description=description, immediate_cause=immediate_cause,
            action_taken=action_taken, reported_to=reported_to, photo_path=photo_path,
            company_id=cid(),
        ))
        db.session.commit()
        flash("Near miss recorded.", "success")
        return redirect(url_for("hse_nearmiss_list"))

    return render_template("hse_nearmiss_new.html",
                           today=today, today_location=today_location, locations=locations)


# ── HSE: BBS Daily Count ──────────────────────────────────────────────

@app.route("/hse/bbs", methods=["GET", "POST"])
@login_required
@hse_officer_required
def hse_bbs():
    u = cur_user()
    today = datetime.now(RIYADH_TZ).date()

    if request.method == "POST":
        bbs_date = _safe_date(request.form.get("date")) or today
        try:
            card_count = int(request.form.get("card_count", 0))
        except ValueError:
            card_count = 0
        notes = request.form.get("notes", "").strip()

        rec = HseBbs.query.filter_by(officer_id=u.id, date=bbs_date).first()
        if rec:
            rec.card_count = card_count
            rec.notes = notes
            flash("BBS count updated.", "success")
        else:
            db.session.add(HseBbs(officer_id=u.id, date=bbs_date,
                                  card_count=card_count, notes=notes,
                                  company_id=cid()))
            flash("BBS count saved.", "success")
        db.session.commit()
        return redirect(url_for("hse_bbs"))

    existing = HseBbs.query.filter_by(officer_id=u.id, date=today).first()
    history = (HseBbs.query.filter_by(officer_id=u.id)
               .order_by(HseBbs.date.desc()).limit(30).all())
    return render_template("hse_bbs.html", existing=existing, today=today, history=history)


# ── HSE: Supervisor Dashboard ─────────────────────────────────────────

@app.route("/hse/dashboard", methods=["GET"])
@login_required
@hse_supervisor_required
def hse_dashboard():
    today = datetime.now(RIYADH_TZ).date()
    # Saudi work week starts Sunday; isoweekday: Mon=1 … Sun=7
    days_since_sunday = (today.isoweekday() % 7)
    week_start = today - timedelta(days=days_since_sunday)

    _cid = cid()
    officers_q = User.query.filter_by(role="safety_officer", is_active=True)
    if _cid:
        officers_q = officers_q.filter(User.company_id == _cid)
    officers = officers_q.all()

    checkin_q = HseCheckin.query.filter_by(date=today)
    if _cid:
        checkin_q = checkin_q.filter(HseCheckin.company_id == _cid)
    today_checkins = {ci.officer_id: ci for ci in checkin_q.all()}

    officer_ids = [o.id for o in officers]

    # ── استعلامات مجمّعة GROUP BY بدلاً من N+1 حلقة ──────────────────
    def _grp(model, col, filt_date_col, extra=None):
        q = (db.session.query(col, func.count())
             .filter(col.in_(officer_ids),
                     filt_date_col.between(week_start, today))
             .group_by(col))
        if extra is not None:
            q = q.filter(extra)
        return dict(q.all())

    obs_map = _grp(HseObservation, HseObservation.officer_id, HseObservation.date)
    jso_map = _grp(HseJsoClosure,  HseJsoClosure.officer_id,  HseJsoClosure.date)
    tbt_map = _grp(HseTbt,         HseTbt.officer_id,         HseTbt.date)
    nm_map  = _grp(HseNearMiss,    HseNearMiss.officer_id,    HseNearMiss.date)

    bbs_map = dict(
        db.session.query(HseBbs.officer_id,
                         func.coalesce(func.sum(HseBbs.card_count), 0))
        .filter(HseBbs.officer_id.in_(officer_ids),
                HseBbs.date.between(week_start, today))
        .group_by(HseBbs.officer_id).all()
    )

    ci_map = dict(
        db.session.query(HseCheckin.officer_id, func.count())
        .filter(HseCheckin.officer_id.in_(officer_ids),
                HseCheckin.date.between(week_start, today))
        .group_by(HseCheckin.officer_id).all()
    )

    ptw_map = dict(
        db.session.query(HsePtw.officer_id, func.count())
        .filter(HsePtw.officer_id.in_(officer_ids),
                HsePtw.status == "active",
                HsePtw.week_end >= today)
        .group_by(HsePtw.officer_id).all()
    )

    insp_map = dict(
        db.session.query(HseInspection.officer_id, func.count())
        .filter(HseInspection.officer_id.in_(officer_ids),
                HseInspection.date.between(week_start, today))
        .group_by(HseInspection.officer_id).all()
    )

    mp_map = {
        mp.officer_id: mp.total_count
        for mp in HseManpower.query.filter(
            HseManpower.officer_id.in_(officer_ids),
            HseManpower.date == today
        ).all()
    }

    insp_score_map = {
        ins.officer_id: ins.overall_score
        for ins in HseInspection.query.filter(
            HseInspection.officer_id.in_(officer_ids),
            HseInspection.date == today
        ).all()
    }

    weekly = {}
    for o in officers:
        oid = o.id
        obs = obs_map.get(oid, 0)
        jso = jso_map.get(oid, 0)
        tbt = tbt_map.get(oid, 0)
        nm  = nm_map.get(oid, 0)
        bbs = int(bbs_map.get(oid, 0))
        ci  = ci_map.get(oid, 0)
        ptw = ptw_map.get(oid, 0)
        insp = insp_map.get(oid, 0)
        score = round(obs*2 + tbt*3 + ptw*1, 1)
        weekly[oid] = {
            "obs": obs, "jso": jso, "tbt": tbt, "nm": nm, "bbs": bbs,
            "total": obs + jso + tbt + nm,
            "ptw": ptw, "manpower": mp_map.get(oid, 0),
            "insp_score": insp_score_map.get(oid),
            "score": score,
        }
    # ─────────────────────────────────────────────────────────────────

    high_risk_q = HseObservation.query.filter_by(status="open", risk_level="H")
    if _cid:
        high_risk_q = high_risk_q.filter(HseObservation.company_id == _cid)
    high_risk = high_risk_q.order_by(HseObservation.date.asc()).all()
    officer_map = {o.id: o for o in officers}

    # Week totals for KPI cards
    total_obs_week  = sum(weekly[o.id]["obs"] for o in officers)
    total_jso_week  = sum(weekly[o.id]["jso"] for o in officers)
    total_tbt_week  = sum(weekly[o.id]["tbt"] for o in officers)
    total_nm_week   = sum(weekly[o.id]["nm"]  for o in officers)
    total_bbs_week  = sum(weekly[o.id]["bbs"] for o in officers)

    obs_base = HseObservation.query.filter(HseObservation.date.between(week_start, today))
    if _cid:
        obs_base = obs_base.filter(HseObservation.company_id == _cid)
    obs_unsafe_act  = obs_base.filter(HseObservation.obs_type == "unsafe_act").count()
    obs_unsafe_cond = obs_base.filter(HseObservation.obs_type == "unsafe_condition").count()
    obs_positive    = obs_base.filter(HseObservation.obs_type == "positive").count()

    max_activity = max((weekly[o.id]["total"] for o in officers), default=1) or 1

    # Corrective actions
    ca_q = HseCorrectiveAction.query
    if _cid: ca_q = ca_q.filter(HseCorrectiveAction.company_id == _cid)
    overdue_ca = ca_q.filter(
        HseCorrectiveAction.status != "completed",
        HseCorrectiveAction.due_date < today
    ).order_by(HseCorrectiveAction.due_date.asc()).all()
    open_ca = ca_q.filter(HseCorrectiveAction.status != "completed").count()

    # PTW expiring soon
    ptw_expiring_q = HsePtw.query.filter(
        HsePtw.status == "active",
        HsePtw.week_end >= today,
        HsePtw.week_end <= today + timedelta(days=2)
    )
    if _cid: ptw_expiring_q = ptw_expiring_q.filter(HsePtw.company_id == _cid)
    ptw_expiring = ptw_expiring_q.order_by(HsePtw.week_end.asc()).all()

    # Total man power today
    mp_today_q = db.session.query(func.coalesce(func.sum(HseManpower.total_count), 0))\
                   .filter(HseManpower.date == today)
    if _cid: mp_today_q = mp_today_q.filter(HseManpower.company_id == _cid)
    total_manpower_today = int(mp_today_q.scalar() or 0)

    # Total active PTW
    ptw_active_q = HsePtw.query.filter(HsePtw.status == "active", HsePtw.week_end >= today)
    if _cid: ptw_active_q = ptw_active_q.filter(HsePtw.company_id == _cid)
    total_ptw_active = ptw_active_q.count()

    # Inspection avg score this week
    insp_q = HseInspection.query.filter(HseInspection.date.between(week_start, today))
    if _cid: insp_q = insp_q.filter(HseInspection.company_id == _cid)
    insp_scores = [i.overall_score for i in insp_q.all() if i.overall_score is not None]
    avg_insp_score = round(sum(insp_scores) / len(insp_scores), 1) if insp_scores else None

    # Officers with no activity today
    inactive_officers = [o for o in officers if o.id not in today_checkins
                         and weekly[o.id]["total"] == 0]

    # ── Welfare summary for dashboard ─────────────────────────────────
    wlf_officers_q = User.query.filter_by(role="safety_welfare", is_active=True)
    if _cid:
        wlf_officers_q = wlf_officers_q.filter(User.company_id == _cid)
    wlf_officers = wlf_officers_q.all()
    wlf_oids = [o.id for o in wlf_officers]

    # Level work submitted today per officer
    wlf_rounds_today = {}
    if wlf_oids:
        for oid, cnt in (db.session.query(WlfLevelWork.officer_id, func.count())
                         .filter(WlfLevelWork.officer_id.in_(wlf_oids),
                                 WlfLevelWork.date == today)
                         .group_by(WlfLevelWork.officer_id).all()):
            wlf_rounds_today[oid] = cnt

    wlf_score_today = {}

    # Open Critical/High findings per officer
    wlf_crit_open = {}
    if wlf_oids:
        for oid, cnt in (db.session.query(WlfFinding.officer_id, func.count())
                         .filter(WlfFinding.officer_id.in_(wlf_oids),
                                 WlfFinding.status == "open",
                                 WlfFinding.severity.in_(["Critical", "High"]))
                         .group_by(WlfFinding.officer_id).all()):
            wlf_crit_open[oid] = cnt

    # Complaints this week per officer
    wlf_complaints_week = {}
    if wlf_oids:
        for oid, cnt in (db.session.query(WlfComplaint.officer_id, func.count())
                         .filter(WlfComplaint.officer_id.in_(wlf_oids),
                                 WlfComplaint.date.between(week_start, today))
                         .group_by(WlfComplaint.officer_id).all()):
            wlf_complaints_week[oid] = cnt

    # Current level per officer
    wlf_level = {}
    if wlf_oids:
        for p in WlfProgress.query.filter(WlfProgress.officer_id.in_(wlf_oids)).all():
            wlf_level[p.officer_id] = p

    # Weekly level work count per officer
    wlf_rounds_week = {}
    if wlf_oids:
        for oid, cnt in (db.session.query(WlfLevelWork.officer_id, func.count())
                         .filter(WlfLevelWork.officer_id.in_(wlf_oids),
                                 WlfLevelWork.date.between(week_start, today))
                         .group_by(WlfLevelWork.officer_id).all()):
            wlf_rounds_week[oid] = cnt

    wlf_avg_score_week = {}

    # Weekly new findings per officer
    wlf_finds_week = {}
    if wlf_oids:
        for oid, cnt in (db.session.query(WlfFinding.officer_id, func.count())
                         .filter(WlfFinding.officer_id.in_(wlf_oids),
                                 WlfFinding.date.between(week_start, today))
                         .group_by(WlfFinding.officer_id).all()):
            wlf_finds_week[oid] = cnt

    # Weekly closed findings per officer
    wlf_closed_week = {}
    if wlf_oids:
        for oid, cnt in (db.session.query(WlfFinding.officer_id, func.count())
                         .filter(WlfFinding.officer_id.in_(wlf_oids),
                                 WlfFinding.status == "closed",
                                 WlfFinding.date.between(week_start, today))
                         .group_by(WlfFinding.officer_id).all()):
            wlf_closed_week[oid] = cnt

    # Aggregate KPIs
    wlf_total_rounds_today = sum(wlf_rounds_today.values())
    wlf_total_crit_open    = sum(wlf_crit_open.values())
    wlf_total_complaints_week = sum(wlf_complaints_week.values())

    # Build per-officer welfare rows
    wlf_rows = []
    for o in wlf_officers:
        oid = o.id
        lvl_p = wlf_level.get(oid)
        done_idx = -1
        if lvl_p and lvl_p.status in ("active", "pending_gate", "completed"):
            try:
                done_idx = WLF_LEVEL_CODES.index(lvl_p.level_code)
            except (ValueError, AttributeError):
                done_idx = -1
        rw  = wlf_rounds_week.get(oid, 0)
        fc  = wlf_closed_week.get(oid, 0)
        co  = wlf_crit_open.get(oid, 0)
        cmp = wlf_complaints_week.get(oid, 0)
        # Points: 3 per round + 2 per closed finding − 1 per open critical/high
        pts = rw * 3 + fc * 2 - co
        wlf_rows.append({
            "u":            o,
            "rounds_today": wlf_rounds_today.get(oid, 0),
            "rounds_week":  rw,
            "avg_score":    wlf_avg_score_week.get(oid),
            "finds_week":   wlf_finds_week.get(oid, 0),
            "closed_week":  fc,
            "crit_open":    co,
            "complaints":   cmp,
            "level_no":     done_idx + 1,
            "level_total":  len(WLF_LEVEL_CODES),
            "points":       pts,
        })
    wlf_max_points = max((r["points"] for r in wlf_rows), default=1) or 1

    # ── Flash alerts for dashboard visitor ────────────────────────────
    if overdue_ca:
        flash(f"⚠ {len(overdue_ca)} Corrective Action(s) are overdue — please follow up.", "warning")
    if ptw_expiring:
        flash(f"📋 {len(ptw_expiring)} PTW permit(s) expire within 48 hours.", "warning")
    if inactive_officers:
        names = ", ".join(o.name for o in inactive_officers[:3])
        extra = f" (+{len(inactive_officers)-3} more)" if len(inactive_officers) > 3 else ""
        flash(f"🔴 No activity today: {names}{extra}", "warning")
    if wlf_total_crit_open:
        flash(f"{wlf_total_crit_open} Critical/High welfare finding(s) still open.", "warning")

    return render_template("hse_dashboard.html",
                           officers=officers, today=today, week_start=week_start,
                           today_checkins=today_checkins, weekly=weekly,
                           high_risk=high_risk, officer_map=officer_map,
                           total_obs_week=total_obs_week, total_jso_week=total_jso_week,
                           wlf_rows=wlf_rows, wlf_max_points=wlf_max_points,
                           wlf_total_rounds_today=wlf_total_rounds_today,
                           wlf_total_crit_open=wlf_total_crit_open,
                           wlf_total_complaints_week=wlf_total_complaints_week,
                           wlf_officers_count=len(wlf_officers),
                           total_tbt_week=total_tbt_week, total_nm_week=total_nm_week,
                           total_bbs_week=total_bbs_week,
                           obs_unsafe_act=obs_unsafe_act, obs_unsafe_cond=obs_unsafe_cond,
                           obs_positive=obs_positive, max_activity=max_activity,
                           overdue_ca=overdue_ca, open_ca=open_ca,
                           ptw_expiring=ptw_expiring, total_ptw_active=total_ptw_active,
                           total_manpower_today=total_manpower_today,
                           avg_insp_score=avg_insp_score,
                           inactive_officers=inactive_officers)


@app.route("/hse/dashboard/officer/<int:officer_id>", methods=["GET"])
@login_required
@hse_supervisor_required
def hse_officer_detail(officer_id):
    _cid = cid()
    officer_q = User.query.filter_by(id=officer_id, role="safety_officer")
    if _cid is not None:
        officer_q = officer_q.filter(User.company_id == _cid)
    officer = officer_q.first_or_404()

    date_from = _safe_date(request.args.get("from"))
    date_to = _safe_date(request.args.get("to"))

    def apply_dates(q, col):
        if date_from:
            q = q.filter(col >= date_from)
        if date_to:
            q = q.filter(col <= date_to)
        return q

    checkins = apply_dates(
        HseCheckin.query.filter_by(officer_id=officer_id), HseCheckin.date
    ).order_by(HseCheckin.date.desc()).all()

    observations = apply_dates(
        HseObservation.query.filter_by(officer_id=officer_id), HseObservation.date
    ).order_by(HseObservation.date.desc()).all()

    jsos = apply_dates(
        HseJsoClosure.query.filter_by(officer_id=officer_id), HseJsoClosure.date
    ).order_by(HseJsoClosure.date.desc()).all()

    tbts = apply_dates(
        HseTbt.query.filter_by(officer_id=officer_id), HseTbt.date
    ).order_by(HseTbt.date.desc()).all()

    nearmisses = apply_dates(
        HseNearMiss.query.filter_by(officer_id=officer_id), HseNearMiss.date
    ).order_by(HseNearMiss.date.desc()).all()

    bbs_records = apply_dates(
        HseBbs.query.filter_by(officer_id=officer_id), HseBbs.date
    ).order_by(HseBbs.date.desc()).all()

    obs_photos    = {o.id: list(o.photos) for o in observations}
    _tbt_ids      = [t.id for t in tbts]
    tbt_counts    = dict(
        db.session.query(HseTbtAttendance.tbt_id, func.count())
        .filter(HseTbtAttendance.tbt_id.in_(_tbt_ids))
        .group_by(HseTbtAttendance.tbt_id).all()
    ) if _tbt_ids else {}
    tbt_attendees = {t.id: list(t.attendance) for t in tbts}

    # Phase 2 data
    ptw_q = HsePtw.query.filter_by(officer_id=officer_id)
    if date_from:
        ptw_q = ptw_q.filter(HsePtw.week_end >= date_from)
    if date_to:
        ptw_q = ptw_q.filter(HsePtw.week_start <= date_to)
    ptw_records = ptw_q.order_by(HsePtw.week_start.desc()).all()

    mp_records = apply_dates(
        HseManpower.query.filter_by(officer_id=officer_id), HseManpower.date
    ).order_by(HseManpower.date.desc()).all()

    insp_records = apply_dates(
        HseInspection.query.filter_by(officer_id=officer_id), HseInspection.date
    ).order_by(HseInspection.date.desc()).all()

    # Weekly scores — last 8 weeks
    import json as _json
    today = datetime.now(RIYADH_TZ).date()
    weekly_scores = []
    for i in range(7, -1, -1):
        days_since_sun = today.isoweekday() % 7
        ws_i = today - timedelta(days=days_since_sun + i * 7)
        we_i = ws_i + timedelta(days=4)
        weekly_scores.append({
            "label": ws_i.strftime("%d %b"),
            "score": _hse_officer_score(officer_id, ws_i, we_i),
        })

    return render_template("hse_officer_detail.html",
                           officer=officer,
                           checkins=checkins,
                           observations=observations, obs_photos=obs_photos,
                           jsos=jsos,
                           tbts=tbts, tbt_counts=tbt_counts, tbt_attendees=tbt_attendees,
                           nearmisses=nearmisses,
                           bbs_records=bbs_records,
                           ptw_records=ptw_records,
                           mp_records=mp_records,
                           insp_records=insp_records,
                           weekly_scores=weekly_scores,
                           date_from=request.args.get("from", ""),
                           date_to=request.args.get("to", ""))


# ===================== HSE Edit / Delete =====================

@app.route("/hse/observation/<int:obs_id>/edit", methods=["GET", "POST"])
@login_required
@hse_officer_required
def hse_observation_edit(obs_id):
    u = cur_user()
    obs = HseObservation.query.filter_by(id=obs_id, officer_id=u.id).first_or_404()
    locations = _hse_locations()
    existing_photos = list(obs.photos)
    if request.method == "POST":
        obs.date        = _safe_date(request.form.get("date")) or obs.date
        obs.location    = request.form.get("location", "").strip()
        obs.obs_type    = request.form.get("obs_type", "").strip()
        obs.category    = request.form.get("category", "").strip()
        obs.risk_level  = request.form.get("risk_level", "").strip() or None
        obs.description = request.form.get("description", "").strip()
        obs.action_taken= request.form.get("action_taken", "").strip()
        f = request.files.get("photo_1")
        path = _save_hse_photo(f, "obs", company_id=cid())
        if path:
            db.session.add(HseObservationPhoto(
                observation_id=obs.id, photo_path=path, photo_type="before"
            ))
        db.session.commit()
        flash("Observation updated.", "success")
        return redirect(url_for("hse_observations"))
    return render_template("hse_observation_edit.html",
                           obs=obs, locations=locations, categories=OBS_CATEGORIES,
                           existing_photos=existing_photos)


@app.route("/hse/observation/<int:obs_id>/delete", methods=["POST"])
@login_required
@hse_officer_required
def hse_observation_delete(obs_id):
    u = cur_user()
    obs = HseObservation.query.filter_by(id=obs_id, officer_id=u.id).first_or_404()
    for p in list(obs.photos):
        db.session.delete(p)
    db.session.delete(obs)
    db.session.commit()
    flash("Observation deleted.", "success")
    return redirect(url_for("hse_observations"))


@app.route("/hse/jso/<int:jso_id>/edit", methods=["GET", "POST"])
@login_required
@hse_officer_required
def hse_jso_edit(jso_id):
    u = cur_user()
    jso = HseJsoClosure.query.filter_by(id=jso_id, officer_id=u.id).first_or_404()
    locations = _hse_locations()
    if request.method == "POST":
        jso.jso_number  = request.form.get("jso_number", "").strip()
        jso.date        = _safe_date(request.form.get("date")) or jso.date
        jso.location    = request.form.get("location", "").strip()
        jso.action_taken= request.form.get("action_taken", "").strip()
        new_photo = _save_hse_photo(request.files.get("photo"), "jso", company_id=cid())
        if new_photo:
            jso.photo_path = new_photo
        db.session.commit()
        flash("JSO record updated.", "success")
        return redirect(url_for("hse_jso_list"))
    return render_template("hse_jso_edit.html", jso=jso, locations=locations)


@app.route("/hse/jso/<int:jso_id>/delete", methods=["POST"])
@login_required
@hse_officer_required
def hse_jso_delete(jso_id):
    u = cur_user()
    jso = HseJsoClosure.query.filter_by(id=jso_id, officer_id=u.id).first_or_404()
    db.session.delete(jso)
    db.session.commit()
    flash("JSO record deleted.", "success")
    return redirect(url_for("hse_jso_list"))


@app.route("/hse/tbt/<int:tbt_id>/edit", methods=["GET", "POST"])
@login_required
@hse_officer_required
def hse_tbt_edit(tbt_id):
    u = cur_user()
    tbt = HseTbt.query.filter_by(id=tbt_id, officer_id=u.id).first_or_404()
    locations = _hse_locations()
    attendees = HseTbtAttendance.query.filter_by(tbt_id=tbt_id).all()
    if request.method == "POST":
        tbt.date     = _safe_date(request.form.get("date")) or tbt.date
        tbt.topic    = request.form.get("topic", "").strip()
        tbt.location = request.form.get("location", "").strip()
        sup_code = request.form.get("supervisor_code", "").strip()
        if sup_code:
            sup = User.query.filter(User.supervisor_code == sup_code, User.is_active == True).first()
            if sup:
                tbt.supervisor_id = sup.id
        new_sign = _save_hse_photo(request.files.get("sign_photo"), "tbt", company_id=cid())
        if new_sign:
            tbt.sign_photo_path = new_sign
        HseTbtAttendance.query.filter_by(tbt_id=tbt.id).delete()
        for num, name in zip(request.form.getlist("emp_number[]"), request.form.getlist("emp_name[]")):
            num = num.strip(); name = name.strip()
            if num and name:
                db.session.add(HseTbtAttendance(tbt_id=tbt.id, emp_number=num, emp_name=name))
        db.session.commit()
        flash("SGL session updated.", "success")
        return redirect(url_for("hse_tbt_list"))
    sup_code_val = ""
    if tbt.supervisor_id:
        sup = db.session.get(User, tbt.supervisor_id)
        sup_code_val = sup.supervisor_code if sup else ""
    return render_template("hse_tbt_edit.html",
                           tbt=tbt, locations=locations,
                           attendees=attendees, sup_code_val=sup_code_val)


@app.route("/hse/tbt/<int:tbt_id>/delete", methods=["POST"])
@login_required
@hse_officer_required
def hse_tbt_delete(tbt_id):
    u = cur_user()
    tbt = HseTbt.query.filter_by(id=tbt_id, officer_id=u.id).first_or_404()
    HseTbtAttendance.query.filter_by(tbt_id=tbt.id).delete()
    db.session.delete(tbt)
    db.session.commit()
    flash("SGL session deleted.", "success")
    return redirect(url_for("hse_tbt_list"))


# ── User Location Web Page ─────────────────────────────────────────────
@app.route("/location", methods=["GET"])
@login_required
def user_location_page():
    u = cur_user()

    # Safety officer in PTW training mode → redirect to training
    if u.role == "safety_officer" and getattr(u, "ptw_training_active", False):
        return redirect(url_for("ptw_training_home"))

    # Admin/super_admin: show all employees' locations, no registration
    if u.role in ("admin", "super_admin"):
        cutoff = datetime.utcnow() - timedelta(hours=24)
        rows = (db.session.query(User, UserLocation)
                .join(UserLocation, User.id == UserLocation.user_id)
                .filter(User.company_id == u.company_id)
                .filter(User.is_active == True)
                .filter(User.role.in_(["supervisor", "site_supervisor", "safety_officer"]))
                .filter(UserLocation.updated_at >= cutoff).all())
        all_locations = [{"user_id": usr.id, "name": usr.name, "role": usr.role,
                           "supervisor_code": usr.supervisor_code,
                           "pkg": loc.pkg, "unit": loc.unit, "area_text": loc.area_text or "",
                           "updated_at": loc.updated_at.isoformat() if loc.updated_at else ""}
                          for usr, loc in rows]
        all_locations.sort(key=lambda x: (x["role"], x["pkg"] or 0, x["unit"] or ""))
        return render_template("user_location.html", all_locations=all_locations)

    my_loc = UserLocation.query.filter_by(user_id=u.id).first()
    # nearest persons
    nearest = []
    if my_loc:
        if u.role == "safety_officer":
            target_roles = ["supervisor", "site_supervisor"]
        else:
            target_roles = ["safety_officer"]
        cutoff = datetime.utcnow() - timedelta(hours=24)
        candidates = (db.session.query(User, UserLocation)
                      .join(UserLocation, User.id == UserLocation.user_id)
                      .filter(User.role.in_(target_roles))
                      .filter(User.company_id == u.company_id)
                      .filter(User.is_active == True)
                      .filter(UserLocation.updated_at >= cutoff).all())
        for c, cloc in candidates:
            if cloc.pkg == my_loc.pkg and cloc.unit == my_loc.unit:
                score = 0
            elif cloc.pkg == my_loc.pkg:
                score = 1
            else:
                score = 2
            nearest.append({"user_id": c.id, "name": c.name,
                             "role": c.role, "supervisor_code": c.supervisor_code,
                             "pkg": cloc.pkg, "unit": cloc.unit,
                             "area_text": cloc.area_text or "",
                             "proximity": score})
        nearest.sort(key=lambda x: (x["proximity"], x["name"]))
    # supervisors list for site_supervisor
    supervisors = []
    if u.role == "site_supervisor":
        sups = (db.session.query(User, UserLocation)
                .join(UserLocation, User.id == UserLocation.user_id)
                .filter(User.role == "supervisor")
                .filter(User.company_id == u.company_id)
                .filter(User.is_active == True).all())
        supervisors = [{"user_id": s.id, "name": s.name,
                         "supervisor_code": s.supervisor_code,
                         "pkg": loc.pkg, "unit": loc.unit,
                         "area_text": loc.area_text or ""}
                        for s, loc in sups]
        supervisors.sort(key=lambda x: (x["pkg"] or 0, x["unit"] or ""))
    return render_template("user_location.html",
                           my_location=my_loc, nearest=nearest, supervisors=supervisors)


@app.route("/hse/nearmiss/<int:nm_id>/edit", methods=["GET", "POST"])
@login_required
@hse_officer_required
def hse_nearmiss_edit(nm_id):
    u = cur_user()
    nm = HseNearMiss.query.filter_by(id=nm_id, officer_id=u.id).first_or_404()
    locations = _hse_locations()
    if request.method == "POST":
        nm.date            = _safe_date(request.form.get("date")) or nm.date
        nm.location        = request.form.get("location", "").strip()
        nm.description     = request.form.get("description", "").strip()
        nm.immediate_cause = request.form.get("immediate_cause", "").strip()
        nm.action_taken    = request.form.get("action_taken", "").strip()
        nm.reported_to     = request.form.get("reported_to", "").strip()
        new_photo = _save_hse_photo(request.files.get("photo"), "nm", company_id=cid())
        if new_photo:
            nm.photo_path = new_photo
        db.session.commit()
        flash("Near miss updated.", "success")
        return redirect(url_for("hse_nearmiss_list"))
    return render_template("hse_nearmiss_edit.html", nm=nm, locations=locations)


@app.route("/hse/nearmiss/<int:nm_id>/delete", methods=["POST"])
@login_required
@hse_officer_required
def hse_nearmiss_delete(nm_id):
    u = cur_user()
    nm = HseNearMiss.query.filter_by(id=nm_id, officer_id=u.id).first_or_404()
    db.session.delete(nm)
    db.session.commit()
    flash("Near miss deleted.", "success")
    return redirect(url_for("hse_nearmiss_list"))


@app.route("/hse/bbs/<int:bbs_id>/delete", methods=["POST"])
@login_required
@hse_officer_required
def hse_bbs_delete(bbs_id):
    u = cur_user()
    rec = HseBbs.query.filter_by(id=bbs_id, officer_id=u.id).first_or_404()
    db.session.delete(rec)
    db.session.commit()
    flash("BBS record deleted.", "success")
    return redirect(url_for("hse_bbs"))


# ===================== HSE Officer Export =====================

def _collect_officer_data(officer_id, date_from=None, date_to=None):
    def apply_dates(q, col):
        if date_from: q = q.filter(col >= date_from)
        if date_to:   q = q.filter(col <= date_to)
        return q
    checkins     = apply_dates(HseCheckin.query.filter_by(officer_id=officer_id),     HseCheckin.date).order_by(HseCheckin.date.desc()).all()
    observations = apply_dates(HseObservation.query.filter_by(officer_id=officer_id), HseObservation.date).order_by(HseObservation.date.desc()).all()
    jsos         = apply_dates(HseJsoClosure.query.filter_by(officer_id=officer_id),  HseJsoClosure.date).order_by(HseJsoClosure.date.desc()).all()
    tbts         = apply_dates(HseTbt.query.filter_by(officer_id=officer_id),         HseTbt.date).order_by(HseTbt.date.desc()).all()
    nearmisses   = apply_dates(HseNearMiss.query.filter_by(officer_id=officer_id),    HseNearMiss.date).order_by(HseNearMiss.date.desc()).all()
    bbs          = apply_dates(HseBbs.query.filter_by(officer_id=officer_id),         HseBbs.date).order_by(HseBbs.date.desc()).all()
    return checkins, observations, jsos, tbts, nearmisses, bbs


@app.get("/hse/dashboard/officer/<int:officer_id>/excel")
@login_required
@hse_supervisor_required
def hse_officer_detail_excel(officer_id):
    import io, openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from flask import make_response

    officer = User.query.filter_by(id=officer_id, role="safety_officer").first_or_404()
    date_from = _safe_date(request.args.get("from"))
    date_to   = _safe_date(request.args.get("to"))
    checkins, observations, jsos, tbts, nearmisses, bbs_list = _collect_officer_data(officer_id, date_from, date_to)
    _excel_tbt_ids = [t.id for t in tbts]
    _excel_att_map = dict(
        db.session.query(HseTbtAttendance.tbt_id, func.count())
        .filter(HseTbtAttendance.tbt_id.in_(_excel_tbt_ids))
        .group_by(HseTbtAttendance.tbt_id).all()
    ) if _excel_tbt_ids else {}

    wb = openpyxl.Workbook()
    thin  = Side(style="thin", color="D1D5DB")
    bdr   = Border(left=thin, right=thin, top=thin, bottom=thin)
    hfill = PatternFill("solid", fgColor="0F172A")
    hfont = Font(color="FFFFFF", bold=True)

    def make_sheet(title, headers, rows_data):
        ws = wb.create_sheet(title)
        for ci, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=ci, value=h)
            c.fill = hfill; c.font = hfont
            c.alignment = Alignment(horizontal="center"); c.border = bdr
        for ri, row in enumerate(rows_data, 2):
            for ci, val in enumerate(row, 1):
                c = ws.cell(row=ri, column=ci, value=val)
                c.border = bdr; c.alignment = Alignment(vertical="center")
        for ci in range(1, len(headers)+1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = 18
        ws.row_dimensions[1].height = 18
        ws.freeze_panes = "A2"

    make_sheet("Check-ins", ["Date", "Location"],
               [(c.date.isoformat(), c.location) for c in checkins])
    make_sheet("Observations", ["Date", "Type", "Category", "Risk", "Status", "Description", "Action"],
               [(o.date.isoformat(), o.obs_type, o.category, o.risk_level or "", o.status,
                 o.description or "", o.action_taken or "") for o in observations])
    make_sheet("JSO Closures", ["Date", "JSO #", "Location", "Action Taken"],
               [(j.date.isoformat(), j.jso_number, j.location or "", j.action_taken or "") for j in jsos])
    make_sheet("SGL", ["Date", "Topic", "Location", "Attendees"],
               [(t.date.isoformat(), t.topic or "", t.location or "",
                 _excel_att_map.get(t.id, 0)) for t in tbts])
    make_sheet("Near Misses", ["Date", "Location", "Description", "Cause", "Action", "Reported To"],
               [(n.date.isoformat(), n.location or "", n.description or "",
                 n.immediate_cause or "", n.action_taken or "", n.reported_to or "") for n in nearmisses])
    make_sheet("BBS", ["Date", "Cards", "Notes"],
               [(b.date.isoformat(), b.card_count, b.notes or "") for b in bbs_list])

    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]

    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    fname = f"officer_{officer.supervisor_code}_{officer_id}.xlsx"
    resp = make_response(buf.read())
    resp.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    resp.headers["Content-Disposition"] = f"attachment; filename={fname}"
    return resp


@app.get("/hse/dashboard/officer/<int:officer_id>/pdf")
@login_required
@hse_supervisor_required
def hse_officer_detail_pdf(officer_id):
    import io
    from flask import make_response
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    officer = User.query.filter_by(id=officer_id, role="safety_officer").first_or_404()
    date_from = _safe_date(request.args.get("from"))
    date_to   = _safe_date(request.args.get("to"))
    checkins, observations, jsos, tbts, nearmisses, bbs_list = _collect_officer_data(officer_id, date_from, date_to)
    _pdf_tbt_ids = [t.id for t in tbts]
    _pdf_att_map = dict(
        db.session.query(HseTbtAttendance.tbt_id, func.count())
        .filter(HseTbtAttendance.tbt_id.in_(_pdf_tbt_ids))
        .group_by(HseTbtAttendance.tbt_id).all()
    ) if _pdf_tbt_ids else {}

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=12*mm, rightMargin=12*mm,
                            topMargin=14*mm, bottomMargin=14*mm)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=14, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=9, spaceAfter=10, textColor=colors.HexColor("#6B7280"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11, spaceBefore=10, spaceAfter=4)
    hdr_fill = colors.HexColor("#0F172A")
    alt_fill = colors.HexColor("#F9FAFB")

    def build_table(header, rows, col_widths):
        data = [header] + (rows if rows else [["—"] * len(header)])
        t = Table(data, colWidths=col_widths, repeatRows=1)
        style = TableStyle([
            ("BACKGROUND",  (0,0), (-1,0), hdr_fill),
            ("TEXTCOLOR",   (0,0), (-1,0), colors.white),
            ("FONTSIZE",    (0,0), (-1,-1), 8),
            ("ALIGN",       (0,0), (-1,-1), "LEFT"),
            ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, alt_fill]),
            ("GRID",        (0,0), (-1,-1), 0.3, colors.HexColor("#E5E7EB")),
            ("TOPPADDING",  (0,0), (-1,-1), 3),
            ("BOTTOMPADDING",(0,0), (-1,-1), 3),
        ])
        t.setStyle(style)
        return t

    W = 186*mm
    story = [
        Paragraph(f"HSE Officer Report — {officer.name}", h1),
        Paragraph(f"Generated: {datetime.now(RIYADH_TZ).strftime('%d %b %Y %H:%M')}" +
                  (f"  |  From: {date_from}" if date_from else "") +
                  (f"  To: {date_to}" if date_to else ""), sub),
        Paragraph(f"Check-ins ({len(checkins)})", h2),
        build_table(["Date", "Location"],
                    [(c.date.isoformat(), c.location) for c in checkins],
                    [30*mm, W-30*mm]),
        Paragraph(f"Observations ({len(observations)})", h2),
        build_table(["Date", "Type", "Category", "Risk", "Status"],
                    [(o.date.isoformat(), o.obs_type, o.category or "", o.risk_level or "", o.status) for o in observations],
                    [22*mm, 28*mm, 38*mm, 16*mm, W-104*mm]),
        Paragraph(f"JSO Closures ({len(jsos)})", h2),
        build_table(["Date", "JSO #", "Action Taken"],
                    [(j.date.isoformat(), j.jso_number, (j.action_taken or "")[:80]) for j in jsos],
                    [22*mm, 30*mm, W-52*mm]),
        Paragraph(f"SGL Sessions ({len(tbts)})", h2),
        build_table(["Date", "Topic", "Location", "Attendees"],
                    [(t.date.isoformat(), (t.topic or "")[:40], t.location or "", str(_pdf_att_map.get(t.id, 0))) for t in tbts],
                    [22*mm, 60*mm, 60*mm, W-142*mm]),
        Paragraph(f"Near Misses ({len(nearmisses)})", h2),
        build_table(["Date", "Location", "Description"],
                    [(n.date.isoformat(), n.location or "", (n.description or "")[:80]) for n in nearmisses],
                    [22*mm, 40*mm, W-62*mm]),
        Paragraph(f"BBS Cards ({len(bbs_list)})", h2),
        build_table(["Date", "Cards", "Notes"],
                    [(b.date.isoformat(), str(b.card_count), b.notes or "") for b in bbs_list],
                    [22*mm, 22*mm, W-44*mm]),
    ]

    doc.build(story)
    buf.seek(0)
    fname = f"officer_{officer_id}_report.pdf"
    resp = make_response(buf.read())
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f"attachment; filename={fname}"
    return resp


@app.get("/hse/dashboard/daily-observations")
@login_required
@hse_supervisor_required
def hse_daily_observations_picker():
    """Date picker page before generating the PDF."""
    today = datetime.now(RIYADH_TZ).date()
    return render_template_string("""
{% extends "base.html" %}
{% block content %}
<div style="max-width:480px;margin:40px auto">
  <a href="{{ url_for('hse_dashboard') }}" class="btn" style="margin-bottom:16px;display:inline-block">← Dashboard</a>
  <div class="card">
    <h2 style="margin-top:0">Daily Observations Report</h2>
    <p style="color:var(--muted);font-size:14px">Select a date to generate a PDF report of all safety officer observations.</p>
    <form method="get" action="{{ url_for('hse_daily_observations_pdf') }}" style="display:flex;flex-direction:column;gap:14px">
      <label style="font-weight:600;font-size:14px">
        Date
        <input type="date" name="date" value="{{ today }}"
               style="display:block;width:100%;margin-top:4px;padding:8px;border:1px solid var(--border);border-radius:6px;font-size:14px">
      </label>
      <button type="submit" class="btn btn-primary" style="padding:10px;font-size:15px">
        Generate PDF
      </button>
    </form>
  </div>
</div>
{% endblock %}
""", today=today)


@app.get("/hse/dashboard/daily-observations/pdf")
@login_required
@hse_supervisor_required
def hse_daily_observations_pdf():
    """PDF — landscape table matching the standard HSE observation sheet + inline photos."""
    import io
    from flask import make_response
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    target_date = _safe_date(request.args.get("date")) or datetime.now(RIYADH_TZ).date()
    _cid = cid()

    obs_q = (HseObservation.query
             .filter(HseObservation.date == target_date)
             .order_by(HseObservation.officer_id, HseObservation.id))
    if _cid:
        obs_q = obs_q.filter(HseObservation.company_id == _cid)
    observations = obs_q.all()

    officer_ids = list({o.officer_id for o in observations})
    officers = {u.id: u for u in User.query.filter(User.id.in_(officer_ids)).all()} if officer_ids else {}

    # ── Page setup (landscape A4) ─────────────────────────────────────
    buf = io.BytesIO()
    PAGE = landscape(A4)          # 297 × 210 mm
    LM = RM = 10*mm
    TM = BM = 10*mm
    W = PAGE[0] - LM - RM         # ≈ 277 mm usable

    doc = SimpleDocTemplate(buf, pagesize=PAGE,
                            leftMargin=LM, rightMargin=RM,
                            topMargin=TM, bottomMargin=BM)
    styles = getSampleStyleSheet()

    # خط يدعم العربية — بدونه تظهر الأسماء العربية كمربعات فارغة
    _AR = pdf_arabic_font()
    _AR_B = "Arabic-Bold" if _AR == "Arabic" else "Helvetica-Bold"

    def _ps(name, size=7.5, leading=10, bold=False, color=None, align=0):
        return ParagraphStyle(name, parent=styles["Normal"],
                              fontSize=size, leading=leading,
                              fontName=(_AR_B if bold else _AR),
                              textColor=color or colors.black, alignment=align)

    title_s = _ps("dopt", 13, 16, bold=True)
    sub_s   = _ps("dops",  8, 11, color=colors.HexColor("#6B7280"))
    cell_s  = _ps("dopc",  7.5, 10)
    cap_s   = _ps("docc",  6.5,  8, color=colors.HexColor("#555555"), align=1)

    HDR_BG  = colors.HexColor("#1E293B")
    ALT_BG  = colors.HexColor("#F8FAFC")
    BDR     = colors.HexColor("#CBD5E1")
    R_HIGH  = colors.HexColor("#FCA5A5")
    R_MED   = colors.HexColor("#FDE68A")
    R_LOW   = colors.HexColor("#86EFAC")

    TYPE_MAP = {"unsafe_act": "Unsafe Act", "unsafe_condition": "Unsafe Condition", "positive": "Positive"}

    # ── Column widths (total = W) ──────────────────────────────────────
    # SN | Date | Observed By | ID | Location | Type | Category | Risk |
    # Observation Description | Immediate Action Taken | Photos
    C = [8, 18, 30, 18, 28, 24, 24, 10, 48, 38, 31]   # mm
    assert abs(sum(C) - W/mm) < 2, f"cols={sum(C)} W={W/mm:.1f}"
    col_w = [c*mm for c in C]

    HEADERS = ["SN", "Date", "Observed By", "ID", "Location",
               "Type", "Category", "Risk",
               "Observation Description", "Immediate Action Taken", "Photos"]

    # thumbnail size — fits inside the Photos column
    PH_W = 28*mm
    PH_H = 20*mm

    def _load_photos(obs):
        """Return list of (RLImage, label) for an observation."""
        from PIL import Image as _PIL
        result = []
        for ph in obs.photos:
            ph_path = os.path.join(HSE_UPLOAD_DIR, ph.photo_path)
            if not os.path.isfile(ph_path):
                continue
            try:
                # Resize + compress to thumbnail before embedding — keeps PDF small
                buf_img = io.BytesIO()
                with _PIL.open(ph_path) as pil:
                    pil = pil.convert("RGB")
                    pil.thumbnail((320, 240), _PIL.LANCZOS)
                    pil.save(buf_img, format="JPEG", quality=70, optimize=True)
                buf_img.seek(0)
                result.append((
                    RLImage(buf_img, width=PH_W, height=PH_H, kind="proportional"),
                    ph.photo_type.title()
                ))
            except Exception:
                continue
        return result

    def _photo_cell(photo_list):
        """Stack thumbnails + labels into a single table cell."""
        if not photo_list:
            return ""
        rows = []
        for img, lbl in photo_list:
            rows.append([img])
            rows.append([Paragraph(lbl, cap_s)])
        t = Table(rows, colWidths=[PH_W])
        t.setStyle(TableStyle([
            ("ALIGN",  (0,0),(-1,-1), "CENTER"),
            ("VALIGN", (0,0),(-1,-1), "MIDDLE"),
            ("TOPPADDING",   (0,0),(-1,-1), 1),
            ("BOTTOMPADDING",(0,0),(-1,-1), 1),
        ]))
        return t

    # ── Build table rows ───────────────────────────────────────────────
    tbl_data = [HEADERS]
    risk_row_styles = []   # collect per-row Risk cell colour

    for i, obs in enumerate(observations, 1):
        off   = officers.get(obs.officer_id)
        name  = off.name if off else f"#{obs.officer_id}"
        code  = off.supervisor_code if off else "—"
        rval  = obs.risk_level or ""
        rlabel = {"H": "H", "M": "M", "L": "L"}.get(rval, "—")
        rcol   = {"H": R_HIGH, "M": R_MED, "L": R_LOW}.get(rval)

        if rcol:
            risk_row_styles.append(("BACKGROUND", (7, i), (7, i), rcol))

        photo_list = _load_photos(obs)

        tbl_data.append([
            str(i),
            obs.date.strftime("%Y-%m-%d"),
            Paragraph(pdf_ar(name), cell_s),
            code,
            Paragraph(pdf_ar(obs.location or "—"), cell_s),
            TYPE_MAP.get(obs.obs_type, obs.obs_type),
            Paragraph(pdf_ar(obs.category or "—"), cell_s),
            rlabel,
            Paragraph(pdf_ar(obs.description or "—"), cell_s),
            Paragraph(pdf_ar(obs.action_taken or "—"), cell_s),
            _photo_cell(photo_list),
        ])

    tbl = Table(tbl_data, colWidths=col_w, repeatRows=1)
    base_style = [
        ("BACKGROUND",    (0,0),  (-1,0),  HDR_BG),
        ("TEXTCOLOR",     (0,0),  (-1,0),  colors.white),
        ("FONTNAME",      (0,0),  (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),  (-1,-1), 7.5),
        ("ALIGN",         (0,0),  (0,-1),  "CENTER"),   # SN
        ("ALIGN",         (1,0),  (1,-1),  "CENTER"),   # Date
        ("ALIGN",         (7,0),  (7,-1),  "CENTER"),   # Risk
        ("ALIGN",         (10,0), (10,-1), "CENTER"),   # Photos
        ("VALIGN",        (0,0),  (-1,-1), "TOP"),
        ("ROWBACKGROUNDS",(0,1),  (-1,-1), [colors.white, ALT_BG]),
        ("GRID",          (0,0),  (-1,-1), 0.35, BDR),
        ("TOPPADDING",    (0,0),  (-1,-1), 3),
        ("BOTTOMPADDING", (0,0),  (-1,-1), 3),
        ("LEFTPADDING",   (0,0),  (-1,-1), 3),
        ("RIGHTPADDING",  (0,0),  (-1,-1), 3),
    ] + risk_row_styles

    tbl.setStyle(TableStyle(base_style))

    # ── SGL section (Safety Green Light — formerly TBT) ──────────
    # يُدرج فوق جدول الملاحظات. إن لم يسجّل أحد SGL اليوم، يُحذف القسم
    # بالكامل ويبقى التقرير ملاحظاتٍ فقط — بلا عنوان فارغ أو جدول خالٍ.
    sgl_q = HseTbt.query.filter(HseTbt.date == target_date)
    if _cid:
        sgl_q = sgl_q.filter(HseTbt.company_id == _cid)
    sgls = sgl_q.order_by(HseTbt.id.asc()).all()

    sgl_flow = []
    if sgls:
        sgl_officer_ids = list({t.officer_id for t in sgls})
        sgl_officers = ({x.id: x for x in User.query.filter(User.id.in_(sgl_officer_ids)).all()}
                        if sgl_officer_ids else {})

        # مشرفو الموظفين الحاضرين
        sgl_sup_ids = list({t.supervisor_id for t in sgls if t.supervisor_id})
        sgl_sups = ({x.id: (x.name or x.supervisor_code)
                     for x in User.query.filter(User.id.in_(sgl_sup_ids)).all()}
                    if sgl_sup_ids else {})

        # الحضور دفعة واحدة
        sgl_ids = [t.id for t in sgls]
        att_rows = (HseTbtAttendance.query
                    .filter(HseTbtAttendance.tbt_id.in_(sgl_ids)).all()) if sgl_ids else []
        att_by_tbt = {}
        for a in att_rows:
            att_by_tbt.setdefault(a.tbt_id, []).append(a)

        # SN | Topic | Location | Conducted By | Supervisor | Att. | Attendees | Signature
        SGL_C = [8, 36, 32, 30, 30, 12, 74, 55]      # mm — مجموعها يساوي W
        _diff = (W/mm) - sum(SGL_C)
        SGL_C[6] += _diff                            # اضبط عمود الأسماء ليطابق العرض
        sgl_w = [c*mm for c in SGL_C]

        sgl_rows = [[Paragraph(h, _ps("sh", 7.5, 10, bold=True, color=colors.white))
                     for h in ["SN", "Topic", "Location", "Conducted By", "Supervisor",
                               "Att.", "Attendees", "Signature"]]]

        for i, t in enumerate(sgls, 1):
            off = sgl_officers.get(t.officer_id)
            att = att_by_tbt.get(t.id, [])
            # الأسماء في عمودين متجاورين — أسهل قراءة من سطر طويل
            labels = [
                (f"{a.emp_name or ''} ({a.emp_number})" if a.emp_number else (a.emp_name or "—"))
                for a in att
            ]
            if labels:
                half = (len(labels) + 1) // 2
                left_col, right_col = labels[:half], labels[half:]
                n_rows = max(len(left_col), len(right_col))
                pair_rows = []
                for r in range(n_rows):
                    lft = f"{r+1}. {left_col[r]}" if r < len(left_col) else ""
                    rgt = (f"{half + r + 1}. {right_col[r]}"
                           if r < len(right_col) else "")
                    pair_rows.append([Paragraph(pdf_ar(lft), cell_s),
                                      Paragraph(pdf_ar(rgt), cell_s)])
                half_w = (SGL_C[6] * mm) / 2.0
                names_cell = Table(pair_rows, colWidths=[half_w, half_w])
                names_cell.setStyle(TableStyle([
                    ("VALIGN",       (0,0), (-1,-1), "TOP"),
                    ("LEFTPADDING",  (0,0), (-1,-1), 1),
                    ("RIGHTPADDING", (0,0), (-1,-1), 1),
                    ("TOPPADDING",   (0,0), (-1,-1), 0.5),
                    ("BOTTOMPADDING",(0,0), (-1,-1), 0.5),
                ]))
            else:
                names_cell = Paragraph("—", cell_s)

            sign_cell = ""
            if t.sign_photo_path:
                sp = os.path.join(HSE_UPLOAD_DIR, t.sign_photo_path)
                if os.path.isfile(sp):
                    try:
                        sign_cell = RLImage(sp, width=52*mm, height=26*mm, kind="proportional")
                    except Exception:
                        sign_cell = ""

            sgl_rows.append([
                Paragraph(str(i), cell_s),
                Paragraph(pdf_ar(t.topic or "—"), cell_s),
                Paragraph(pdf_ar(t.location or "—"), cell_s),
                Paragraph(pdf_ar((off.name or off.supervisor_code) if off else "—"), cell_s),
                Paragraph(pdf_ar(sgl_sups.get(t.supervisor_id, "—")), cell_s),
                Paragraph(str(len(att)), cell_s),
                names_cell,
                sign_cell,
            ])

        sgl_tbl = Table(sgl_rows, colWidths=sgl_w, repeatRows=1)
        sgl_tbl.setStyle(TableStyle([
            ("BACKGROUND",   (0,0), (-1,0), HDR_BG),
            ("TEXTCOLOR",    (0,0), (-1,0), colors.white),
            ("GRID",         (0,0), (-1,-1), 0.4, BDR),
            ("VALIGN",       (0,0), (-1,-1), "MIDDLE"),
            ("ALIGN",        (0,1), (0,-1), "CENTER"),
            ("ALIGN",        (5,1), (5,-1), "CENTER"),
            ("ALIGN",        (7,1), (7,-1), "CENTER"),
            ("LEFTPADDING",  (0,0), (-1,-1), 3),
            ("RIGHTPADDING", (0,0), (-1,-1), 3),
            ("TOPPADDING",   (0,0), (-1,-1), 3),
            ("BOTTOMPADDING",(0,0), (-1,-1), 3),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, ALT_BG]),
        ]))

        total_att = sum(len(att_by_tbt.get(t.id, [])) for t in sgls)
        sgl_flow = [
            Paragraph("SGL — Safety Green Light", _ps("sgt", 10.5, 13, bold=True)),
            Paragraph(f"Sessions: {len(sgls)}  ·  Total attendance: {total_att}", sub_s),
            Spacer(1, 2*mm),
            sgl_tbl,
            Spacer(1, 6*mm),
        ]

    story = [
        Paragraph("Daily HSE Report", title_s),
        Paragraph(
            f"Date: {target_date.strftime('%d %b %Y')}  ·  "
            f"Observations: {len(observations)}  ·  "
            f"SGL: {len(sgls)}  ·  "
            f"Generated: {datetime.now(RIYADH_TZ).strftime('%d %b %Y  %H:%M')}",
            sub_s
        ),
        Spacer(1, 3*mm),
    ] + sgl_flow + [
        Paragraph("Observations", _ps("obt", 10.5, 13, bold=True)),
        Spacer(1, 2*mm),
        tbl,
    ]

    doc.build(story)
    buf.seek(0)
    fname = f"observations_{target_date.isoformat()}.pdf"
    resp = make_response(buf.read())
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f"inline; filename={fname}"
    return resp


# ===================== HSE Monthly Report =====================

def _hse_monthly_report_data(year, month, company_id=None):
    from calendar import monthrange
    first_day = date(year, month, 1)
    last_day  = date(year, month, monthrange(year, month)[1])
    return _hse_weekly_report_data(first_day, last_day, company_id=company_id)


@app.route("/hse/reports/monthly", methods=["GET"])
@login_required
@hse_supervisor_required
def hse_report_monthly_picker():
    today = datetime.now(RIYADH_TZ).date()
    return render_template("hse_report_monthly_picker.html", today=today)


@app.route("/hse/reports/monthly/view", methods=["GET"])
@login_required
@hse_supervisor_required
def hse_report_monthly():
    today = datetime.now(RIYADH_TZ).date()
    try:
        year  = int(request.args.get("year",  today.year))
        month = int(request.args.get("month", today.month))
    except (TypeError, ValueError):
        return redirect(url_for("hse_report_monthly_picker"))
    from calendar import monthrange, month_name as _mn
    first_day = date(year, month, 1)
    last_day  = date(year, month, monthrange(year, month)[1])
    rows = _hse_monthly_report_data(year, month, company_id=cid())
    from datetime import datetime as _dt
    return render_template("hse_report_monthly.html",
                           rows=rows, year=year, month=month,
                           month_name=_mn[month], first_day=first_day, last_day=last_day,
                           now=_dt.now(RIYADH_TZ))


@app.get("/hse/reports/monthly/excel")
@login_required
@hse_supervisor_required
def hse_report_monthly_excel():
    import io, openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from flask import make_response
    from calendar import monthrange

    today = datetime.now(RIYADH_TZ).date()
    try:
        year  = int(request.args.get("year",  today.year))
        month = int(request.args.get("month", today.month))
    except (TypeError, ValueError):
        return redirect(url_for("hse_report_monthly_picker"))

    rows = _hse_monthly_report_data(year, month, company_id=cid())
    wb = openpyxl.Workbook(); ws = wb.active
    ws.title = f"{year}-{month:02d}"
    thin = Side(style="thin", color="D1D5DB")
    bdr  = Border(left=thin, right=thin, top=thin, bottom=thin)
    hfill = PatternFill("solid", fgColor="0F172A")
    hfont = Font(color="FFFFFF", bold=True)
    headers = ["Officer","Days Check-in","Obs Total","Unsafe Act","Unsafe Cond","Positive",
               "High Risk","Open Obs","JSO","SGL","SGL Attend.",
               "PTW","Open CAs","Score"]
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=ci, value=h)
        c.fill = hfill; c.font = hfont
        c.alignment = Alignment(horizontal="center"); c.border = bdr
    for ri, r in enumerate(rows, 2):
        vals = [r["officer_name"], r["checkin_days"], r["obs_total"],
                r["obs_unsafe_act"], r["obs_unsafe_cond"], r["obs_positive"],
                r["obs_high"], r["obs_open"], r["jso"], r["tbt"],
                r["tbt_attendees"],
                r.get("ptw", 0), r.get("ca_open", 0), r.get("score", 0)]
        for ci, v in enumerate(vals, 1):
            c = ws.cell(row=ri, column=ci, value=v)
            c.border = bdr; c.alignment = Alignment(vertical="center")
    for ci, w in enumerate([24,14,10,11,12,10,11,10,9,8,13,11,10,8], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w
    ws.freeze_panes = "A2"
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    fname = f"hse_monthly_{year}_{month:02d}.xlsx"
    resp = make_response(buf.read())
    resp.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    resp.headers["Content-Disposition"] = f"attachment; filename={fname}"
    return resp


@app.get("/hse/reports/monthly/pdf")
@login_required
@hse_supervisor_required
def hse_report_monthly_pdf():
    import io
    from flask import make_response
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from calendar import monthrange, month_name as _mn

    today = datetime.now(RIYADH_TZ).date()
    try:
        year  = int(request.args.get("year",  today.year))
        month = int(request.args.get("month", today.month))
    except (TypeError, ValueError):
        return redirect(url_for("hse_report_monthly_picker"))

    rows = _hse_monthly_report_data(year, month, company_id=cid())
    first_day = date(year, month, 1)
    last_day  = date(year, month, monthrange(year, month)[1])

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=10*mm, rightMargin=10*mm,
                            topMargin=12*mm, bottomMargin=12*mm)
    styles = getSampleStyleSheet()
    title_s = ParagraphStyle("t", parent=styles["Heading1"], fontSize=13, spaceAfter=3)
    sub_s   = ParagraphStyle("s", parent=styles["Normal"],   fontSize=9, spaceAfter=8,
                             textColor=colors.HexColor("#6B7280"))
    story = [
        Paragraph(f"HSE Monthly Report — {_mn[month]} {year}", title_s),
        Paragraph(f"{first_day.strftime('%d %b')} — {last_day.strftime('%d %b %Y')}", sub_s),
        Spacer(1, 4*mm),
    ]
    col_headers = ["Officer","Days\nCheck-in","Obs\nTotal","Unsafe\nAct","Unsafe\nCond",
                   "Positive","High\nRisk","Open","JSO","SGL","SGL\nAttend.",
                   "PTW","Open\nCAs","Score"]
    data = [col_headers] + [
        [r["officer_name"], str(r["checkin_days"]), str(r["obs_total"]),
         str(r["obs_unsafe_act"]), str(r["obs_unsafe_cond"]), str(r["obs_positive"]),
         str(r["obs_high"]), str(r["obs_open"]), str(r["jso"]),
         str(r["tbt"]), str(r["tbt_attendees"]),
         str(r.get("ptw", 0)),
         str(r.get("ca_open", 0)), str(r.get("score", 0))]
        for r in rows
    ]
    col_widths = [44*mm,14*mm,12*mm,12*mm,12*mm,12*mm,12*mm,10*mm,10*mm,10*mm,14*mm,
                  10*mm,10*mm,10*mm]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0),(-1,0), colors.HexColor("#0F172A")),
        ("TEXTCOLOR",  (0,0),(-1,0), colors.white),
        ("FONTSIZE",   (0,0),(-1,-1), 8),
        ("ALIGN",      (1,0),(-1,-1), "CENTER"),
        ("VALIGN",     (0,0),(-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, colors.HexColor("#F9FAFB")]),
        ("GRID",       (0,0),(-1,-1), 0.4, colors.HexColor("#E5E7EB")),
        ("TOPPADDING", (0,0),(-1,-1), 4),
        ("BOTTOMPADDING",(0,0),(-1,-1), 4),
    ]))
    story.append(t)
    doc.build(story)
    buf.seek(0)
    fname = f"hse_monthly_{year}_{month:02d}.pdf"
    resp = make_response(buf.read())
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f"attachment; filename={fname}"
    return resp


# ===================== Safety Manager Dashboard =====================

def _wlf_monthly_card_data(officer, first_day, last_day):
    """Stub — welfare module not yet implemented."""
    return {"rounds": 0, "avg_score": 0, "finds_closed": 0, "finds_open": 0, "complaints": 0}


@app.route("/safety-manager/dashboard", methods=["GET"])
@login_required
@safety_manager_required
def safety_manager_dashboard():
    import json as _json
    from calendar import monthrange

    today        = datetime.now(RIYADH_TZ).date()
    period       = request.args.get("period", "week")
    week_offset  = int(request.args.get("week_offset", 0))
    _cid         = cid()

    # ── Date range ──────────────────────────────────────────────────────
    if period == "month":
        first_day  = date(today.year, today.month, 1)
        last_day   = today
        period_lbl = today.strftime("%B %Y")
        week_offset = 0  # not used for month
        # previous month
        if today.month == 1:
            pm_first = date(today.year - 1, 12, 1)
            pm_last  = date(today.year - 1, 12, monthrange(today.year - 1, 12)[1])
        else:
            pm_first = date(today.year, today.month - 1, 1)
            pm_last  = date(today.year, today.month - 1,
                            monthrange(today.year, today.month - 1)[1])
        prev_rows = _hse_weekly_report_data(pm_first, pm_last, company_id=_cid)
    else:  # week (default)
        # week_offset: 0=current week, -1=last week, -2=two weeks ago …
        days_since_sun = today.isoweekday() % 7
        this_sun   = today - timedelta(days=days_since_sun)
        first_day  = this_sun + timedelta(weeks=week_offset)
        # last_day = end of that week (Saturday) or today if it's the current week
        raw_last   = first_day + timedelta(days=6)
        last_day   = min(raw_last, today)
        period_lbl = f"{first_day.strftime('%d %b')} – {last_day.strftime('%d %b %Y')}"
        prev_first = first_day - timedelta(days=7)
        prev_last  = first_day - timedelta(days=1)
        prev_rows  = _hse_weekly_report_data(prev_first, prev_last, company_id=_cid)

    # ── Current period data ─────────────────────────────────────────────
    rows = _hse_weekly_report_data(first_day, last_day, company_id=_cid)

    # Attach trend (score delta vs previous period)
    prev_score_map = {r["officer_id"]: r["score"] for r in prev_rows}
    for r in rows:
        prev = prev_score_map.get(r["officer_id"], 0)
        r["prev_score"] = prev
        r["trend"]      = round(r["score"] - prev, 1)

    rows.sort(key=lambda r: r["score"], reverse=True)

    # ── Team KPIs ───────────────────────────────────────────────────────
    total_officers   = len(rows)
    avg_score        = round(sum(r["score"] for r in rows) / total_officers, 1) if total_officers else 0
    total_obs        = sum(r["obs_total"]   for r in rows)
    total_tbt        = sum(r["tbt"]         for r in rows)
    total_nm         = sum(r["nm"]          for r in rows)
    total_ua         = sum(r["obs_unsafe_act"]  for r in rows)
    total_uc         = sum(r["obs_unsafe_cond"] for r in rows)
    total_pos        = sum(r["obs_positive"]    for r in rows)
    active_officers  = sum(1 for r in rows if r["checkin_days"] > 0)
    inactive_officers = [r for r in rows if r["checkin_days"] == 0]

    # compliance = officers who checked in / total
    compliance_pct = round(active_officers / total_officers * 100) if total_officers else 0

    # ── High-risk open observations ─────────────────────────────────────
    hr_q = HseObservation.query.filter_by(status="open", risk_level="H")
    if _cid:
        hr_q = hr_q.filter(HseObservation.company_id == _cid)
    high_risk_open = hr_q.order_by(HseObservation.date.asc()).limit(15).all()

    # officer name map for alerts
    oid_name = {r["officer_id"]: r["officer_name"] for r in rows}

    # ── Overdue corrective actions ───────────────────────────────────────
    ca_q = HseCorrectiveAction.query.filter(
        HseCorrectiveAction.status != "completed",
        HseCorrectiveAction.due_date < today
    )
    if _cid:
        ca_q = ca_q.filter(HseCorrectiveAction.company_id == _cid)
    ca_overdue = ca_q.order_by(HseCorrectiveAction.due_date.asc()).limit(10).all()

    # ── 6-week team score trend (for chart) ─────────────────────────────
    weekly_labels, weekly_avg = [], []
    for i in range(5, -1, -1):
        dsun  = today.isoweekday() % 7
        ws_i  = today - timedelta(days=dsun + i * 7)
        we_i  = ws_i + timedelta(days=4)
        wr    = _hse_weekly_report_data(ws_i, we_i, company_id=_cid)
        wavg  = round(sum(x["score"] for x in wr) / len(wr), 1) if wr else 0
        weekly_labels.append(ws_i.strftime("%d %b"))
        weekly_avg.append(wavg)

    # ── Per-officer 6-week scores (top 5 for chart) ──────────────────────
    top5 = rows[:5]
    officer_trend_data = []
    for r in top5:
        oid   = r["officer_id"]
        pts   = []
        for i in range(5, -1, -1):
            dsun = today.isoweekday() % 7
            ws_i = today - timedelta(days=dsun + i * 7)
            we_i = ws_i + timedelta(days=4)
            s    = _hse_officer_score(oid, ws_i, we_i)
            pts.append(s)
        officer_trend_data.append({"name": r["officer_name"], "data": pts})

    # ── Welfare summary ────────────────────────────────────────────────
    wlf_officers_q = User.query.filter_by(role="safety_welfare", is_active=True)
    if _cid:
        wlf_officers_q = wlf_officers_q.filter(User.company_id == _cid)
    wlf_officers = wlf_officers_q.order_by(User.name).all()
    wlf_rows = []
    for o in wlf_officers:
        d = _wlf_monthly_card_data(o, first_day, last_day)
        wlf_rows.append({
            "officer_id":   o.id,
            "officer_name": o.name,
            "rounds":       d["rounds"],
            "avg_score":    d["avg_score"],
            "finds_closed": d["finds_closed"],
            "finds_open":   d["finds_open"],
            "complaints":   d["complaints"],
            "zero":         d["rounds"] == 0,
        })
    wlf_rows.sort(key=lambda r: r["rounds"], reverse=True)

    return render_template(
        "safety_manager_dashboard.html",
        today=today, period=period, period_lbl=period_lbl,
        week_offset=week_offset,
        first_day=first_day, last_day=last_day,
        rows=rows,
        wlf_rows=wlf_rows,
        total_officers=total_officers,
        active_officers=active_officers,
        inactive_officers=inactive_officers,
        compliance_pct=compliance_pct,
        avg_score=avg_score,
        total_obs=total_obs,
        total_tbt=total_tbt,
        total_nm=total_nm,
        total_ua=total_ua,
        total_uc=total_uc,
        total_pos=total_pos,
        high_risk_open=high_risk_open,
        oid_name=oid_name,
        ca_overdue=ca_overdue,
        weekly_labels=_json.dumps(weekly_labels),
        weekly_avg=_json.dumps(weekly_avg),
        officer_trend_data=_json.dumps(officer_trend_data),
    )


# ===================== HSE Reports =====================

def _hse_weekly_report_data(ws, we, company_id=None):
    """Returns list of dicts, one per safety officer — uses batch GROUP BY queries."""
    officers_q = User.query.filter_by(role="safety_officer", is_active=True)
    if company_id:
        officers_q = officers_q.filter(User.company_id == company_id)
    officers = officers_q.order_by(User.name).all()
    if not officers:
        return []
    oids = [o.id for o in officers]

    # ── batch queries (one per metric) ─────────────────────────────────
    ci_map = dict(db.session.query(HseCheckin.officer_id, func.count())
                  .filter(HseCheckin.officer_id.in_(oids), HseCheckin.date.between(ws, we))
                  .group_by(HseCheckin.officer_id).all())

    # observations: load once, aggregate in Python
    obs_rows = (db.session.query(HseObservation.officer_id, HseObservation.obs_type,
                                  HseObservation.risk_level, HseObservation.status)
                .filter(HseObservation.officer_id.in_(oids), HseObservation.date.between(ws, we))
                .all())
    from collections import defaultdict
    obs_data = defaultdict(lambda: {"total":0,"unsafe_act":0,"unsafe_cond":0,
                                     "positive":0,"high":0,"open":0})
    for oid, otype, risk, status in obs_rows:
        d = obs_data[oid]
        d["total"] += 1
        if otype == "unsafe_act":      d["unsafe_act"] += 1
        elif otype == "unsafe_condition": d["unsafe_cond"] += 1
        elif otype == "positive":      d["positive"] += 1
        if risk == "H":   d["high"] += 1
        if status == "open": d["open"] += 1

    jso_map = dict(db.session.query(HseJsoClosure.officer_id, func.count())
                   .filter(HseJsoClosure.officer_id.in_(oids), HseJsoClosure.date.between(ws, we))
                   .group_by(HseJsoClosure.officer_id).all())

    tbt_map = dict(db.session.query(HseTbt.officer_id, func.count())
                   .filter(HseTbt.officer_id.in_(oids), HseTbt.date.between(ws, we))
                   .group_by(HseTbt.officer_id).all())

    # tbt_attendees: join TBT with attendance, sum per officer
    tbt_att_map = dict(
        db.session.query(HseTbt.officer_id, func.count(HseTbtAttendance.id))
        .join(HseTbtAttendance, HseTbt.id == HseTbtAttendance.tbt_id)
        .filter(HseTbt.officer_id.in_(oids), HseTbt.date.between(ws, we))
        .group_by(HseTbt.officer_id).all()
    )

    nm_map = dict(db.session.query(HseNearMiss.officer_id, func.count())
                  .filter(HseNearMiss.officer_id.in_(oids), HseNearMiss.date.between(ws, we))
                  .group_by(HseNearMiss.officer_id).all())

    bbs_map = dict(db.session.query(HseBbs.officer_id,
                                     func.coalesce(func.sum(HseBbs.card_count), 0))
                   .filter(HseBbs.officer_id.in_(oids), HseBbs.date.between(ws, we))
                   .group_by(HseBbs.officer_id).all())

    ptw_map = dict(db.session.query(HsePtw.officer_id, func.count())
                   .filter(HsePtw.officer_id.in_(oids),
                           HsePtw.week_start <= we, HsePtw.week_end >= ws)
                   .group_by(HsePtw.officer_id).all())

    mp_map = dict(db.session.query(HseManpower.officer_id,
                                    func.coalesce(func.sum(HseManpower.total_count), 0))
                  .filter(HseManpower.officer_id.in_(oids), HseManpower.date.between(ws, we))
                  .group_by(HseManpower.officer_id).all())

    # inspection avg per officer
    insp_rows = (db.session.query(HseInspection.officer_id,
                                   func.avg(HseInspection.overall_score))
                 .filter(HseInspection.officer_id.in_(oids),
                         HseInspection.date.between(ws, we),
                         HseInspection.overall_score.isnot(None))
                 .group_by(HseInspection.officer_id).all())
    insp_map = {oid: round(float(avg), 1) for oid, avg in insp_rows if avg is not None}

    # inspection count (needed for score)
    insp_cnt_map = dict(db.session.query(HseInspection.officer_id, func.count())
                        .filter(HseInspection.officer_id.in_(oids),
                                HseInspection.date.between(ws, we))
                        .group_by(HseInspection.officer_id).all())

    # open corrective actions per officer (via join)
    ca_map = dict(
        db.session.query(HseObservation.officer_id, func.count(HseCorrectiveAction.id))
        .join(HseCorrectiveAction, HseCorrectiveAction.observation_id == HseObservation.id)
        .filter(HseObservation.officer_id.in_(oids),
                HseCorrectiveAction.status != "completed")
        .group_by(HseObservation.officer_id).all()
    )

    rows = []
    for o in officers:
        oid = o.id
        obs  = obs_data[oid]
        ci_w   = ci_map.get(oid, 0)
        obs_t  = obs["total"]
        tbt_w  = tbt_map.get(oid, 0)
        nm_w   = nm_map.get(oid, 0)
        bbs_w  = int(bbs_map.get(oid, 0))
        insp_w = insp_cnt_map.get(oid, 0)
        ptw_w  = ptw_map.get(oid, 0)
        score  = round(ci_w*1 + obs_t*2 + tbt_w*3 + ptw_w*1, 1)
        rows.append({
            "officer_id":      oid,
            "officer_name":    o.name,
            "checkin_days":    ci_w,
            "obs_total":       obs_t,
            "obs_unsafe_act":  obs["unsafe_act"],
            "obs_unsafe_cond": obs["unsafe_cond"],
            "obs_positive":    obs["positive"],
            "obs_high":        obs["high"],
            "obs_open":        obs["open"],
            "jso":             jso_map.get(oid, 0),
            "tbt":             tbt_w,
            "tbt_attendees":   tbt_att_map.get(oid, 0),
            "nm":              nm_w,
            "bbs":             bbs_w,
            "ptw":             ptw_w,
            "mp_total":        int(mp_map.get(oid, 0)),
            "avg_insp":        insp_map.get(oid),
            "ca_open":         ca_map.get(oid, 0),
            "score":           score,
            "total_activity":  obs_t + jso_map.get(oid, 0) + tbt_w + nm_w,
            "is_trainee":      bool(getattr(o, "ptw_training_active", False)),
        })
    return rows


@app.route("/hse/reports", methods=["GET"])
@login_required
@hse_supervisor_required
def hse_report_picker():
    today = datetime.now(RIYADH_TZ).date()
    days_since_sunday = today.isoweekday() % 7
    default_ws = today - timedelta(days=days_since_sunday)
    return render_template("hse_report_picker.html", default_ws=default_ws)


@app.route("/hse/reports/weekly", methods=["GET"])
@login_required
@hse_supervisor_required
def hse_report_weekly():
    ws = _safe_date(request.args.get("week_start"))
    if not ws:
        return redirect(url_for("hse_report_picker"))
    we = ws + timedelta(days=4)          # Sun → Thu
    rows = _hse_weekly_report_data(ws, we, company_id=cid())
    from datetime import datetime as _dt
    return render_template("hse_report_weekly.html",
                           rows=rows, ws=ws, we=we,
                           now=_dt.now(RIYADH_TZ))


# ===================== HSE Phase 2 Routes =====================

def _ptw_current_week():
    today = datetime.now(RIYADH_TZ).date()
    days_since_sun = (today.weekday() - 6) % 7   # الأحد = weekday 6 في Python
    week_start = today - timedelta(days=days_since_sun)
    week_end   = week_start + timedelta(days=4)   # الأحد + 4 = الخميس
    return week_start, week_end


def _hse_officer_score(officer_id, week_start, week_end):
    ci  = HseCheckin.query.filter(HseCheckin.officer_id == officer_id,
                                   HseCheckin.date.between(week_start, week_end)).count()
    obs = HseObservation.query.filter(HseObservation.officer_id == officer_id,
                                       HseObservation.date.between(week_start, week_end)).count()
    tbt = HseTbt.query.filter(HseTbt.officer_id == officer_id,
                               HseTbt.date.between(week_start, week_end)).count()
    nm  = HseNearMiss.query.filter(HseNearMiss.officer_id == officer_id,
                                    HseNearMiss.date.between(week_start, week_end)).count()
    bbs = db.session.query(func.coalesce(func.sum(HseBbs.card_count), 0)).filter(
           HseBbs.officer_id == officer_id,
           HseBbs.date.between(week_start, week_end)).scalar() or 0
    insp = HseInspection.query.filter(HseInspection.officer_id == officer_id,
                                       HseInspection.date.between(week_start, week_end)).count()
    ptw  = HsePtw.query.filter(HsePtw.officer_id == officer_id,
                                 HsePtw.week_start == week_start).count()
    return round(ci*1 + obs*2 + tbt*3 + ptw*1, 1)


# ── PTW ──────────────────────────────────────────────────────────────

@app.route("/hse/ptw", methods=["GET", "POST"])
@login_required
@hse_officer_required
def hse_ptw():
    u = cur_user()
    today = datetime.now(RIYADH_TZ).date()
    ws, we = _ptw_current_week()

    if request.method == "POST":
        permit_number = (request.form.get("permit_number") or "").strip()
        permit_type   = (request.form.get("permit_type") or "").strip()
        description   = (request.form.get("description") or "").strip()
        location      = (request.form.get("location") or "").strip() or _hse_today_location(u.id)
        w_start       = _safe_date(request.form.get("week_start")) or ws
        w_end         = w_start + timedelta(days=5)
        attached_to   = request.form.get("attached_to_id") or None

        if not permit_number or not permit_type:
            flash("Permit number and type are required.", "danger")
            return redirect(url_for("hse_ptw"))

        p = HsePtw(
            officer_id=u.id, company_id=u.company_id,
            permit_number=permit_number, permit_type=permit_type,
            description=description, location=location,
            week_start=w_start, week_end=w_end,
            attached_to_id=int(attached_to) if attached_to else None,
        )
        db.session.add(p)
        db.session.commit()
        flash("Permit saved.", "success")
        return redirect(url_for("hse_ptw"))

    ptw_list = HsePtw.query.filter_by(officer_id=u.id).order_by(HsePtw.week_start.desc()).all()
    active_ptw = [p for p in ptw_list if p.status == "active"]
    loc = _hse_today_location(u.id)
    locations = _hse_locations()
    permit_numbers = [r[0] for r in db.session.query(HsePtw.permit_number).filter_by(
        company_id=u.company_id).distinct().all()]
    return render_template("hse_ptw.html", ptw_list=ptw_list, active_ptw=active_ptw,
                           ws=ws, we=we, today=today, loc=loc,
                           locations=locations, permit_numbers=permit_numbers)


@app.post("/hse/ptw/<int:ptw_id>/status")
@login_required
@hse_officer_required
def hse_ptw_status(ptw_id):
    u = cur_user()
    p = HsePtw.query.filter_by(id=ptw_id, officer_id=u.id).first_or_404()
    new_status = request.form.get("status", "active")
    if new_status in ("active", "suspended", "closed"):
        p.status = new_status
        db.session.commit()
    return redirect(url_for("hse_ptw"))


@app.post("/hse/ptw/<int:ptw_id>/renew")
@login_required
@hse_officer_required
def hse_ptw_renew(ptw_id):
    u = cur_user()
    p = HsePtw.query.filter_by(id=ptw_id, officer_id=u.id).first_or_404()
    ws, we = _ptw_current_week()
    p.week_start = ws
    p.week_end   = we
    p.status     = "active"
    db.session.commit()
    flash("Permit renewed for the current week.", "success")
    return redirect(url_for("hse_ptw"))


@app.get("/hse/ptw/suggest")
@login_required
def hse_ptw_suggest():
    u = cur_user()
    q = (request.args.get("q") or "").strip()
    rows = (HsePtw.query
            .filter(HsePtw.company_id == u.company_id,
                    HsePtw.permit_number.ilike(f"%{q}%"))
            .with_entities(HsePtw.permit_number, HsePtw.permit_type,
                           HsePtw.description, HsePtw.location)
            .distinct().limit(10).all())
    return __import__("flask").jsonify([
        {"number": r[0], "type": r[1], "desc": r[2] or "", "loc": r[3] or ""}
        for r in rows
    ])


# ── Man Power ─────────────────────────────────────────────────────────

@app.route("/hse/manpower", methods=["GET", "POST"])
@login_required
@hse_officer_required
def hse_manpower():
    u = cur_user()
    today = datetime.now(RIYADH_TZ).date()

    existing = HseManpower.query.filter_by(officer_id=u.id, date=today).first()

    if request.method == "POST":
        total = request.form.get("total_count", "0")
        notes = (request.form.get("notes") or "").strip()
        loc   = (request.form.get("location") or "").strip() or _hse_today_location(u.id)

        import json as _json
        trades  = request.form.getlist("trade[]")
        counts  = request.form.getlist("count[]")
        bkdown  = {t.strip(): int(c) for t, c in zip(trades, counts)
                   if t.strip() and c.isdigit() and int(c) > 0}

        try:
            total = max(0, int(total))
        except ValueError:
            total = sum(bkdown.values())

        if existing:
            existing.total_count = total
            existing.location    = loc
            existing.breakdown   = _json.dumps(bkdown, ensure_ascii=False) if bkdown else None
            existing.notes       = notes
        else:
            rec = HseManpower(officer_id=u.id, company_id=u.company_id,
                               date=today, total_count=total, location=loc,
                               breakdown=_json.dumps(bkdown, ensure_ascii=False) if bkdown else None,
                               notes=notes)
            db.session.add(rec)
        db.session.commit()
        flash("Man Power saved.", "success")
        return redirect(url_for("hse_manpower"))

    history = (HseManpower.query.filter_by(officer_id=u.id)
               .order_by(HseManpower.date.desc()).limit(14).all())
    loc = _hse_today_location(u.id)
    locations = _hse_locations()
    return render_template("hse_manpower.html", existing=existing, history=history,
                           today=today, loc=loc, locations=locations)


# ── Inspection Checklist ──────────────────────────────────────────────

@app.route("/hse/inspection", methods=["GET", "POST"])
@login_required
@hse_officer_required
def hse_inspection():
    u = cur_user()
    today = datetime.now(RIYADH_TZ).date()
    existing = HseInspection.query.filter_by(officer_id=u.id, date=today).first()

    if request.method == "POST":
        import json as _json
        loc   = (request.form.get("location") or "").strip() or _hse_today_location(u.id)
        notes = (request.form.get("notes") or "").strip()
        items = []
        ok_count = 0
        for i, item_text in enumerate(INSPECTION_ITEMS):
            ok   = request.form.get(f"item_{i}") == "ok"
            note = (request.form.get(f"note_{i}") or "").strip()
            items.append({"item": item_text, "ok": ok, "note": note})
            if ok:
                ok_count += 1
        score = round(ok_count / len(INSPECTION_ITEMS) * 100, 1)

        if existing:
            existing.location      = loc
            existing.checklist     = _json.dumps(items, ensure_ascii=False)
            existing.overall_score = score
            existing.notes         = notes
        else:
            rec = HseInspection(officer_id=u.id, company_id=u.company_id,
                                 date=today, location=loc,
                                 checklist=_json.dumps(items, ensure_ascii=False),
                                 overall_score=score, notes=notes)
            db.session.add(rec)
        db.session.commit()
        flash(f"Checklist saved — score: {score}%", "success")
        return redirect(url_for("hse_inspection"))

    import json as _json
    existing_items = None
    if existing and existing.checklist:
        try:
            existing_items = _json.loads(existing.checklist)
        except Exception:
            existing_items = None

    history = (HseInspection.query.filter_by(officer_id=u.id)
               .order_by(HseInspection.date.desc()).limit(10).all())
    loc = _hse_today_location(u.id)
    locations = _hse_locations()
    return render_template("hse_inspection.html",
                           existing=existing, existing_items=existing_items,
                           items=INSPECTION_ITEMS, history=history,
                           today=today, loc=loc, locations=locations)


# ── Corrective Actions ────────────────────────────────────────────────

@app.post("/hse/corrective/add")
@login_required
@hse_supervisor_required
def hse_corrective_add():
    obs_id         = request.form.get("observation_id")
    assigned_to    = (request.form.get("assigned_to") or "").strip()
    due_date       = _safe_date(request.form.get("due_date"))
    action_required = (request.form.get("action_required") or "").strip()

    if not obs_id or not due_date or not action_required:
        flash("All fields are required.", "danger")
        return redirect(url_for("hse_dashboard"))

    u = cur_user()
    obs = HseObservation.query.get_or_404(int(obs_id))
    ca = HseCorrectiveAction(
        observation_id=int(obs_id), company_id=u.company_id,
        assigned_to=assigned_to, due_date=due_date,
        action_required=action_required, created_by=u.id,
    )
    db.session.add(ca)
    db.session.commit()
    # notify supervisors
    _notify_hse_supervisors(
        "📋 Corrective Action Added",
        f"New CA assigned to {assigned_to or 'N/A'} — due {due_date.strftime('%d %b %Y')}"
    )
    # notify the officer who owns the observation
    import threading as _thr
    _thr.Thread(
        target=_send_push_to_user,
        args=(obs.officer_id,
              "📋 إجراء تصحيحي جديد",
              f"تم فتح CA على ملاحظتك — مطلوب: {action_required[:80]}"),
        daemon=True,
    ).start()
    flash("Corrective action added.", "success")
    return redirect(url_for("hse_dashboard"))


@app.post("/hse/corrective/<int:ca_id>/update")
@login_required
@hse_supervisor_required
def hse_corrective_update(ca_id):
    u = cur_user()
    ca = HseCorrectiveAction.query.get_or_404(ca_id)
    status = request.form.get("status", "open")
    if status in ("open", "in_progress", "completed"):
        ca.status = status
        if status == "completed":
            ca.completed_at     = datetime.now(RIYADH_TZ).date()
            ca.completion_notes = (request.form.get("completion_notes") or "").strip()
        db.session.commit()
        flash("Action updated.", "success")
    return redirect(url_for("hse_dashboard"))

# ── Officer: My Corrective Actions ───────────────────────────────────

@app.get("/hse/my-ca")
@login_required
@hse_officer_required
def hse_my_ca():
    u    = cur_user()
    today = datetime.now(RIYADH_TZ).date()
    cas  = (HseCorrectiveAction.query
            .join(HseObservation, HseCorrectiveAction.observation_id == HseObservation.id)
            .filter(HseObservation.officer_id == u.id)
            .order_by(HseCorrectiveAction.due_date.asc())
            .all())
    open_cas     = [c for c in cas if c.status != "completed"]
    closed_cas   = [c for c in cas if c.status == "completed"]
    overdue_cas  = [c for c in open_cas if c.due_date < today]
    return render_template("hse_my_ca.html",
                           open_cas=open_cas, closed_cas=closed_cas,
                           overdue_cas=overdue_cas, today=today)


# ── Charts API ────────────────────────────────────────────────────────

@app.get("/api/hse/charts")
@login_required
@hse_supervisor_required
def hse_charts_data():
    import json as _json
    _cid = cid()
    today = datetime.now(RIYADH_TZ).date()

    # آخر 4 أسابيع
    weeks = []
    for i in range(3, -1, -1):
        days_since_sun = today.isoweekday() % 7
        ws = today - timedelta(days=days_since_sun + i * 7)
        we = ws + timedelta(days=4)
        weeks.append((ws, we))

    obs_trend = {"labels": [], "unsafe_act": [], "unsafe_cond": [], "positive": []}
    for ws, we in weeks:
        obs_trend["labels"].append(ws.strftime("%d %b"))
        q = HseObservation.query.filter(HseObservation.date.between(ws, we))
        if _cid: q = q.filter(HseObservation.company_id == _cid)
        obs_trend["unsafe_act"].append(q.filter_by(obs_type="unsafe_act").count())
        obs_trend["unsafe_cond"].append(q.filter_by(obs_type="unsafe_condition").count())
        obs_trend["positive"].append(q.filter_by(obs_type="positive").count())

    # Category distribution this month
    first_of_month = today.replace(day=1)
    cat_q = db.session.query(HseObservation.category, func.count(HseObservation.id))\
              .filter(HseObservation.date >= first_of_month)
    if _cid: cat_q = cat_q.filter(HseObservation.company_id == _cid)
    cat_data = {r[0] or "Other": r[1] for r in cat_q.group_by(HseObservation.category).all()}

    # Officer scores this week
    days_since_sun = today.isoweekday() % 7
    ws_cur = today - timedelta(days=days_since_sun)
    we_cur = ws_cur + timedelta(days=4)
    officers_q = User.query.filter_by(role="safety_officer", is_active=True)
    if _cid: officers_q = officers_q.filter(User.company_id == _cid)
    officers = officers_q.all()
    if officers:
        _score_rows = _hse_weekly_report_data(ws_cur, we_cur, company_id=_cid)
        _score_lookup = {r["officer_id"]: r["score"] for r in _score_rows}
        officer_scores = {
            "labels": [o.name for o in officers],
            "scores": [_score_lookup.get(o.id, 0.0) for o in officers],
        }
    else:
        officer_scores = {"labels": [], "scores": []}

    # Man power last 7 days
    mp_data = {"labels": [], "counts": []}
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        mp_data["labels"].append(d.strftime("%a %d"))
        total_q = db.session.query(func.coalesce(func.sum(HseManpower.total_count), 0))\
                    .filter(HseManpower.date == d)
        if _cid: total_q = total_q.filter(HseManpower.company_id == _cid)
        mp_data["counts"].append(int(total_q.scalar() or 0))

    return __import__("flask").jsonify({
        "obs_trend": obs_trend,
        "cat_data": cat_data,
        "officer_scores": officer_scores,
        "mp_data": mp_data,
    })


@app.get("/hse/reports/weekly/excel")
@login_required
@hse_supervisor_required
def hse_report_weekly_excel():
    import io, openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from flask import make_response

    ws_date = _safe_date(request.args.get("week_start"))
    if not ws_date:
        return redirect(url_for("hse_report_picker"))
    we_date = ws_date + timedelta(days=4)
    rows = _hse_weekly_report_data(ws_date, we_date, company_id=cid())

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "HSE Weekly"

    headers = ["Officer", "Check-in Days",
               "Obs Total", "Unsafe Act", "Unsafe Cond", "Positive", "High-Risk", "Open",
               "JSO Closures", "SGL", "SGL Attendees"]
    hfill = PatternFill("solid", fgColor="0F172A")
    hfont = Font(color="FFFFFF", bold=True)
    thin  = Side(style="thin", color="D1D5DB")
    bdr   = Border(left=thin, right=thin, top=thin, bottom=thin)

    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=ci, value=h)
        c.fill = hfill; c.font = hfont
        c.alignment = Alignment(horizontal="center"); c.border = bdr

    for ri, r in enumerate(rows, 2):
        vals = [r["officer_name"], r["checkin_days"],
                r["obs_total"], r["obs_unsafe_act"], r["obs_unsafe_cond"],
                r["obs_positive"], r["obs_high"], r["obs_open"],
                r["jso"], r["tbt"], r["tbt_attendees"]]
        zero_fill = PatternFill("solid", fgColor="FEF2F2") if r["total_activity"] == 0 else None
        for ci, v in enumerate(vals, 1):
            c = ws.cell(row=ri, column=ci, value=v)
            c.border = bdr
            c.alignment = Alignment(vertical="center")
            if zero_fill:
                c.fill = zero_fill

    widths = [24, 14, 10, 11, 12, 10, 11, 8, 13, 8, 14]
    for ci, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w
    ws.row_dimensions[1].height = 20
    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    fname = f"hse_weekly_{ws_date.isoformat()}.xlsx"
    resp = make_response(buf.read())
    resp.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    resp.headers["Content-Disposition"] = f"attachment; filename={fname}"
    return resp


@app.get("/hse/reports/weekly/pdf")
@login_required
@hse_supervisor_required
def hse_report_weekly_pdf():
    import io
    from flask import make_response
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    ws_date = _safe_date(request.args.get("week_start"))
    if not ws_date:
        return redirect(url_for("hse_report_picker"))
    we_date = ws_date + timedelta(days=4)
    rows = _hse_weekly_report_data(ws_date, we_date, company_id=cid())

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=10*mm, rightMargin=10*mm,
                            topMargin=12*mm, bottomMargin=12*mm)
    styles = getSampleStyleSheet()
    title_s = ParagraphStyle("t", parent=styles["Heading1"], fontSize=13, spaceAfter=3)
    sub_s   = ParagraphStyle("s", parent=styles["Normal"],   fontSize=9,  spaceAfter=8, textColor=colors.HexColor("#6B7280"))

    story = [
        Paragraph("HSE Weekly Report", title_s),
        Paragraph(f"Week: {ws_date.strftime('%d %b %Y')} — {we_date.strftime('%d %b %Y')}", sub_s),
        Spacer(1, 4*mm),
    ]

    col_headers = ["Officer", "Days\nChecked In",
                   "Obs\nTotal", "Unsafe\nAct", "Unsafe\nCond", "Positive", "High\nRisk", "Open",
                   "JSO", "SGL", "SGL\nAttend."]
    data = [col_headers]
    for r in rows:
        data.append([
            r["officer_name"], str(r["checkin_days"]),
            str(r["obs_total"]), str(r["obs_unsafe_act"]), str(r["obs_unsafe_cond"]),
            str(r["obs_positive"]), str(r["obs_high"]), str(r["obs_open"]),
            str(r["jso"]), str(r["tbt"]), str(r["tbt_attendees"]),
        ])

    col_widths = [50*mm, 16*mm, 14*mm, 14*mm, 14*mm, 14*mm, 14*mm, 12*mm,
                  14*mm, 12*mm, 16*mm]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = TableStyle([
        ("BACKGROUND",  (0,0), (-1,0), colors.HexColor("#0F172A")),
        ("TEXTCOLOR",   (0,0), (-1,0), colors.white),
        ("FONTSIZE",    (0,0), (-1,0), 8),
        ("FONTSIZE",    (0,1), (-1,-1), 8),
        ("ALIGN",       (1,0), (-1,-1), "CENTER"),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F9FAFB")]),
        ("GRID",        (0,0), (-1,-1), 0.4, colors.HexColor("#E5E7EB")),
        ("TOPPADDING",  (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",(0,0), (-1,-1), 4),
    ])
    for ri, r in enumerate(rows, 1):
        if r["total_activity"] == 0:
            style.add("BACKGROUND", (0, ri), (-1, ri), colors.HexColor("#FEF2F2"))
    t.setStyle(style)
    story.append(t)

    doc.build(story)
    buf.seek(0)
    fname = f"hse_weekly_{ws_date.isoformat()}.pdf"
    resp = make_response(buf.read())
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f"attachment; filename={fname}"
    return resp


# ===================== HSE Mobile API =====================

def api_hse_officer_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        u = get_api_user()
        if not u or not u.is_active or u.role not in ("safety_officer", "safety_supervisor",
                                                       "safety_manager", "admin", "super_admin"):
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return wrapper

def api_hse_supervisor_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        u = get_api_user()
        if not u or not u.is_active or not is_hse_supervisor(u):
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return wrapper




# ── API: Corrective Action (iOS) ──────────────────────────────────────

@app.post("/api/hse/corrective_action")
@api_hse_supervisor_required
def api_hse_corrective_action_add():
    u = get_api_user()
    data = freq.get_json(force=True) or {}
    obs_id         = data.get("observation_id")
    assigned_to    = (data.get("assigned_to") or "").strip()
    due_date_str   = (data.get("due_date") or "").strip()
    action_required = (data.get("action_required") or "").strip()

    if not all([obs_id, due_date_str, action_required]):
        return jsonify({"error": "observation_id, due_date, action_required are required"}), 400

    try:
        due_date = parse_date(due_date_str)
    except Exception:
        return jsonify({"error": "invalid due_date format, use YYYY-MM-DD"}), 400

    obs = HseObservation.query.filter_by(id=int(obs_id), company_id=u.company_id).first()
    if not obs:
        return jsonify({"error": "observation not found"}), 404

    ca = HseCorrectiveAction(
        observation_id=obs.id,
        company_id=u.company_id,
        assigned_to=assigned_to or None,
        due_date=due_date,
        action_required=action_required,
        created_by=u.id,
    )
    db.session.add(ca)
    db.session.commit()

    import threading as _thr
    _thr.Thread(
        target=_send_push_to_user,
        args=(obs.officer_id,
              "📋 إجراء تصحيحي جديد",
              f"تم فتح CA على ملاحظتك — مطلوب: {action_required[:80]}"),
        daemon=True,
    ).start()

    return jsonify({"ok": True, "id": ca.id}), 201


@app.put("/api/hse/corrective_action/<int:ca_id>")
@api_hse_supervisor_required
def api_hse_corrective_action_update(ca_id):
    u = get_api_user()
    ca = HseCorrectiveAction.query.filter_by(id=ca_id, company_id=u.company_id).first_or_404()
    data = freq.get_json(force=True) or {}
    status = data.get("status", ca.status)
    if status not in ("open", "in_progress", "completed"):
        return jsonify({"error": "invalid status"}), 400
    ca.status = status
    if status == "completed":
        ca.completed_at     = datetime.now(RIYADH_TZ).date()
        ca.completion_notes = (data.get("completion_notes") or "").strip() or None
    db.session.commit()
    return jsonify({"ok": True})


@app.get("/api/hse/officer/<int:officer_id>/pdf")
@api_hse_supervisor_required
def api_hse_officer_pdf(officer_id):
    """PDF officer report via Bearer token (iOS)."""
    import io
    from flask import make_response
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    officer = User.query.filter_by(id=officer_id, role="safety_officer").first_or_404()
    date_from = _safe_date(freq.args.get("from"))
    date_to   = _safe_date(freq.args.get("to"))
    checkins, observations, jsos, tbts, nearmisses, bbs_list = _collect_officer_data(officer_id, date_from, date_to)
    _att_ids = [t.id for t in tbts]
    _att_map = dict(
        db.session.query(HseTbtAttendance.tbt_id, func.count())
        .filter(HseTbtAttendance.tbt_id.in_(_att_ids))
        .group_by(HseTbtAttendance.tbt_id).all()
    ) if _att_ids else {}

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=12*mm, rightMargin=12*mm,
                            topMargin=14*mm, bottomMargin=14*mm)
    styles = getSampleStyleSheet()
    h1  = ParagraphStyle("h1",  parent=styles["Heading1"], fontSize=14, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=styles["Normal"],   fontSize=9,  spaceAfter=10,
                         textColor=colors.HexColor("#6B7280"))
    h2  = ParagraphStyle("h2",  parent=styles["Heading2"], fontSize=11, spaceBefore=10, spaceAfter=4)
    hdr_fill = colors.HexColor("#0F172A")
    alt_fill = colors.HexColor("#F9FAFB")

    def _tbl(header, rows, col_widths):
        data = [header] + (rows if rows else [["-"] * len(header)])
        t = Table(data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND",  (0,0), (-1,0), hdr_fill),
            ("TEXTCOLOR",   (0,0), (-1,0), colors.white),
            ("FONTSIZE",    (0,0), (-1,0), 9),
            ("FONTSIZE",    (0,1), (-1,-1), 8),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, alt_fill]),
            ("GRID",        (0,0), (-1,-1), 0.4, colors.HexColor("#E5E7EB")),
            ("LEFTPADDING",  (0,0), (-1,-1), 5),
            ("RIGHTPADDING", (0,0), (-1,-1), 5),
            ("TOPPADDING",   (0,0), (-1,-1), 3),
            ("BOTTOMPADDING",(0,0), (-1,-1), 3),
        ]))
        return t

    content = [
        Paragraph("HSE Officer Report - " + officer.name, h1),
        Paragraph("Period: " + str(date_from or "All time") + " to " + str(date_to or "Today"), sub),
        Spacer(1, 4*mm),
        Paragraph("Check-in History", h2),
        _tbl(["Date","Location","Time"],
             [[str(c.date), c.location or "-", str(c.created_at)[:10]] for c in checkins],
             [40*mm, 80*mm, 40*mm]),
        Spacer(1, 4*mm),
        Paragraph("Observations", h2),
        _tbl(["Date","Type","Category","Risk","Status"],
             [[str(o.date), o.obs_type or "-", o.category or "-", o.risk_level or "-", o.status]
              for o in observations],
             [28*mm, 32*mm, 36*mm, 20*mm, 24*mm]),
        Spacer(1, 4*mm),
        Paragraph("SGL Sessions", h2),
        _tbl(["Date","Topic","Location","Attendees"],
             [[str(t.date), (t.topic or "-")[:40], t.location or "-",
               str(_att_map.get(t.id, 0))] for t in tbts],
             [25*mm, 70*mm, 45*mm, 20*mm]),
        Spacer(1, 4*mm),
        Paragraph("Near Miss", h2),
        _tbl(["Date","Location","Description"],
             [[str(n.date), n.location or "-", (n.description or "-")[:60]] for n in nearmisses],
             [25*mm, 40*mm, 95*mm]),
    ]
    doc.build(content)
    buf.seek(0)
    resp = make_response(buf.read())
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f"attachment; filename=officer_{officer_id}_report.pdf"
    return resp


# ── API: Weekly Report JSON (for iOS) ────────────────────────────────
@app.get("/api/hse/reports/weekly")
@api_hse_supervisor_required
def api_hse_report_weekly():
    ws = _safe_date(freq.args.get("week_start"))
    if not ws:
        today = datetime.now(RIYADH_TZ).date()
        days_since_sunday = today.isoweekday() % 7
        ws = today - timedelta(days=days_since_sunday)
    we = ws + timedelta(days=6)
    _company = get_api_user().company_id
    rows = _hse_weekly_report_data(ws, we, company_id=_company)
    prev_ws = ws - timedelta(days=7)
    prev_we = prev_ws + timedelta(days=6)
    prev_rows = _hse_weekly_report_data(prev_ws, prev_we, company_id=_company)
    return jsonify({"week_start": ws.isoformat(), "week_end": we.isoformat(),
                    "rows": rows, "prev_rows": prev_rows})


# ── API: Monthly Report JSON (for iOS) ───────────────────────────────
@app.get("/api/hse/reports/monthly")
@api_hse_supervisor_required
def api_hse_report_monthly_api():
    today = datetime.now(RIYADH_TZ).date()
    try:
        year  = int(freq.args.get("year",  today.year))
        month = int(freq.args.get("month", today.month))
    except (TypeError, ValueError):
        year, month = today.year, today.month
    _company = get_api_user().company_id
    rows = _hse_monthly_report_data(year, month, company_id=_company)
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    prev_rows = _hse_monthly_report_data(prev_year, prev_month, company_id=_company)
    return jsonify({"year": year, "month": month, "rows": rows, "prev_rows": prev_rows})


@app.route("/api/hse/locations")
@api_login_required
def api_hse_locations():
    locs = db.session.query(HseCheckin.location).distinct().all()
    return jsonify([l[0] for l in locs if l[0]])

# ── Check-in ──────────────────────────────────────────
@app.route("/api/hse/checkin/today", methods=["GET"])
@api_hse_officer_required
def api_hse_checkin_today():
    u = get_api_user()
    today = datetime.now(RIYADH_TZ).date()
    ci = HseCheckin.query.filter_by(officer_id=u.id, date=today).first()
    if not ci:
        return jsonify(None)
    return jsonify({"id": ci.id, "date": ci.date.isoformat(), "location": ci.location})

@app.route("/api/hse/checkin", methods=["POST"])
@api_hse_officer_required
def api_hse_checkin_api():
    u = get_api_user()
    today = datetime.now(RIYADH_TZ).date()
    data = freq.get_json()
    location = (data.get("location") or "").strip()
    if not location:
        return jsonify({"error": "Location required"}), 400
    ci = HseCheckin.query.filter_by(officer_id=u.id, date=today).first()
    if ci:
        ci.location = location
    else:
        ci = HseCheckin(officer_id=u.id, date=today, location=location)
        db.session.add(ci)
    db.session.commit()
    return jsonify({"ok": True, "location": ci.location})

# ── Observations ──────────────────────────────────────
@app.route("/api/hse/observations", methods=["GET"])
@api_hse_officer_required
def api_hse_observations():
    u = get_api_user()
    page = int(freq.args.get("page", 1))
    status = freq.args.get("status", "")
    if u.role in ("safety_supervisor", "safety_manager", "admin", "super_admin"):
        officer_ids = [o.id for o in _get_safety_officers(u)]
        q = HseObservation.query.filter(HseObservation.officer_id.in_(officer_ids))
    else:
        q = HseObservation.query.filter_by(officer_id=u.id)
    if status in ("open", "closed"):
        q = q.filter_by(status=status)
    pg = q.order_by(HseObservation.date.desc()).paginate(page=page, per_page=15, error_out=False)
    officer_cache = {}
    def _officer_name(oid):
        if oid not in officer_cache:
            off = db.session.get(User, oid)
            officer_cache[oid] = off.name if off else ""
        return officer_cache[oid]
    items = [{"id": o.id, "date": o.date.isoformat(), "location": o.location or "",
              "obs_type": o.obs_type or "", "category": o.category or "",
              "risk_level": o.risk_level or "", "description": o.description or "",
              "action_taken": o.action_taken or "", "status": o.status,
              "closed_at": o.closed_at.isoformat() if o.closed_at else None,
              "closure_action": o.closure_action or "",
              "officer_name": _officer_name(o.officer_id)} for o in pg.items]
    return jsonify({"items": items, "page": pg.page, "pages": pg.pages, "total": pg.total})

@app.route("/api/hse/observation", methods=["POST"])
@api_hse_officer_required
def api_hse_observation_create():
    u = get_api_user()
    data = freq.get_json()
    today = datetime.now(RIYADH_TZ).date()
    date_val = _parse_date(data.get("date"), today)
    risk_level = data.get("risk_level", "L")
    location = (data.get("location") or "").strip()
    category = (data.get("category") or "").strip()
    o = HseObservation(
        officer_id=u.id,
        date=date_val,
        location=location,
        obs_type=data.get("obs_type", "unsafe_act"),
        category=category,
        risk_level=risk_level,
        description=(data.get("description") or "").strip(),
        action_taken=(data.get("action_taken") or "").strip(),
        company_id=api_cid(),
    )
    db.session.add(o)
    db.session.commit()
    if risk_level == "H":
        _notify_hse_supervisors("⚠ High-Risk Observation",
                                f"{u.name}: {category or 'No category'} at {location}")
    return jsonify({"ok": True, "id": o.id})

@app.route("/api/hse/observation/<int:obs_id>", methods=["GET"])
@api_login_required
def api_hse_observation_get(obs_id):
    u = get_api_user()
    if u.role in ("safety_supervisor", "safety_manager", "admin", "super_admin"):
        officer_ids = [o.id for o in _get_safety_officers(u)]
        obs = HseObservation.query.filter(HseObservation.id == obs_id,
                                          HseObservation.officer_id.in_(officer_ids)).first_or_404()
    else:
        obs = HseObservation.query.filter_by(id=obs_id, officer_id=u.id).first_or_404()
    photos = [{"path": p.photo_path, "photo_type": p.photo_type or "before"} for p in obs.photos]
    return jsonify({
        "id": obs.id, "date": obs.date.isoformat(), "location": obs.location or "",
        "obs_type": obs.obs_type or "", "category": obs.category or "",
        "risk_level": obs.risk_level or "", "description": obs.description or "",
        "action_taken": obs.action_taken or "", "status": obs.status,
        "closed_at": obs.closed_at.isoformat() if obs.closed_at else None,
        "closure_action": obs.closure_action or "",
        "photos": photos
    })


@app.route("/api/hse/observation/<int:obs_id>/close", methods=["POST"])
@api_hse_officer_required
def api_hse_observation_close(obs_id):
    u = get_api_user()
    o = HseObservation.query.filter_by(id=obs_id, officer_id=u.id).first_or_404()
    data = freq.get_json()
    closure_action = (data.get("closure_action") or "").strip()
    if not closure_action:
        return jsonify({"error": "Closure action required"}), 400
    o.status = "closed"
    o.closed_at = datetime.now(RIYADH_TZ).date()
    o.closure_action = closure_action
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/api/hse/observation/<int:obs_id>", methods=["PUT"])
@api_hse_officer_required
def api_hse_observation_update(obs_id):
    u = get_api_user()
    o = HseObservation.query.filter_by(id=obs_id, officer_id=u.id).first_or_404()
    data = freq.get_json()
    old_risk = o.risk_level
    o.location    = (data.get("location")    or o.location or "").strip()
    o.obs_type    = data.get("obs_type")    or o.obs_type
    o.category    = (data.get("category")    or o.category or "").strip()
    o.risk_level  = data.get("risk_level")  or o.risk_level
    o.description = (data.get("description") or o.description or "").strip()
    o.action_taken = (data.get("action_taken") or o.action_taken or "").strip()
    db.session.commit()
    if o.risk_level == "H" and old_risk != "H":
        _notify_hse_supervisors("⚠ High-Risk Observation (updated)",
                                f"{u.name}: {o.category or 'No category'} at {o.location}")
    return jsonify({"ok": True})

@app.route("/api/hse/observation/<int:obs_id>/photo", methods=["POST"])
@api_hse_officer_required
def api_hse_observation_photo(obs_id):
    u = get_api_user()
    o = HseObservation.query.filter_by(id=obs_id, officer_id=u.id).first_or_404()
    f = freq.files.get("photo")
    path = _save_hse_photo(f, "obs", company_id=api_cid())
    if not path:
        return jsonify({"error": "Invalid or missing photo"}), 400
    photo_type = freq.form.get("photo_type", "before")
    db.session.add(HseObservationPhoto(observation_id=o.id, photo_path=path, photo_type=photo_type))
    db.session.commit()
    return jsonify({"ok": True, "path": path})

@app.route("/api/hse/observation/<int:obs_id>", methods=["DELETE"])
@api_hse_officer_required
def api_hse_observation_delete_api(obs_id):
    u = get_api_user()
    o = HseObservation.query.filter_by(id=obs_id, officer_id=u.id).first_or_404()
    HseObservationPhoto.query.filter_by(observation_id=o.id).delete()
    db.session.delete(o)
    db.session.commit()
    return jsonify({"ok": True})

# ── JSO Closure ────────────────────────────────────────
@app.route("/api/hse/jso", methods=["GET"])
@api_hse_officer_required
def api_hse_jso_list():
    u = get_api_user()
    page = int(freq.args.get("page", 1))
    if u.role in ("safety_supervisor", "safety_manager", "admin", "super_admin"):
        officer_ids = [o.id for o in _get_safety_officers(u)]
        base_q = HseJsoClosure.query.filter(HseJsoClosure.officer_id.in_(officer_ids))
    else:
        base_q = HseJsoClosure.query.filter_by(officer_id=u.id)
    pg = base_q.order_by(HseJsoClosure.date.desc()).paginate(page=page, per_page=15, error_out=False)
    off_map = {}
    def _off(oid):
        if oid not in off_map:
            obj = db.session.get(User, oid)
            off_map[oid] = obj.name if obj else ""
        return off_map[oid]
    items = [{"id": j.id, "date": j.date.isoformat(), "jso_number": j.jso_number,
              "location": j.location or "", "action_taken": j.action_taken or "",
              "officer_name": _off(j.officer_id)} for j in pg.items]
    return jsonify({"items": items, "page": pg.page, "pages": pg.pages, "total": pg.total})

@app.route("/api/hse/jso", methods=["POST"])
@api_hse_officer_required
def api_hse_jso_create():
    u = get_api_user()
    data = freq.get_json()
    today = datetime.now(RIYADH_TZ).date()
    jso_number = (data.get("jso_number") or "").strip()
    if not jso_number:
        return jsonify({"error": "JSO number required"}), 400
    j = HseJsoClosure(
        officer_id=u.id,
        date=_parse_date(data.get("date"), today),
        jso_number=jso_number,
        location=(data.get("location") or "").strip(),
        action_taken=(data.get("action_taken") or "").strip(),
        company_id=api_cid(),
    )
    db.session.add(j)
    db.session.commit()
    return jsonify({"ok": True, "id": j.id})

@app.route("/api/hse/jso/<int:jso_id>", methods=["PUT"])
@api_hse_officer_required
def api_hse_jso_update(jso_id):
    u = get_api_user()
    j = HseJsoClosure.query.filter_by(id=jso_id, officer_id=u.id).first_or_404()
    data = freq.get_json()
    j.jso_number  = (data.get("jso_number")  or j.jso_number).strip()
    j.location    = (data.get("location")    or j.location or "").strip()
    j.action_taken = (data.get("action_taken") or j.action_taken or "").strip()
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/api/hse/jso/<int:jso_id>/photo", methods=["POST"])
@api_hse_officer_required
def api_hse_jso_photo(jso_id):
    u = get_api_user()
    j = HseJsoClosure.query.filter_by(id=jso_id, officer_id=u.id).first_or_404()
    f = freq.files.get("photo")
    path = _save_hse_photo(f, "jso", company_id=api_cid())
    if not path:
        return jsonify({"error": "Invalid or missing photo"}), 400
    if j.photo_path:
        try: os.remove(os.path.join(HSE_UPLOAD_DIR, j.photo_path))
        except Exception: pass
    j.photo_path = path
    db.session.commit()
    return jsonify({"ok": True, "path": path})

@app.route("/api/hse/jso/<int:jso_id>", methods=["DELETE"])
@api_hse_officer_required
def api_hse_jso_delete_api(jso_id):
    u = get_api_user()
    j = HseJsoClosure.query.filter_by(id=jso_id, officer_id=u.id).first_or_404()
    db.session.delete(j)
    db.session.commit()
    return jsonify({"ok": True})

# ── TBT ────────────────────────────────────────────────
@app.route("/api/hse/tbt", methods=["GET"])
@api_hse_officer_required
def api_hse_tbt_list():
    u = get_api_user()
    page = int(freq.args.get("page", 1))
    if u.role in ("safety_supervisor", "safety_manager", "admin", "super_admin"):
        officer_ids = [o.id for o in _get_safety_officers(u)]
        base_q = HseTbt.query.filter(HseTbt.officer_id.in_(officer_ids))
    else:
        base_q = HseTbt.query.filter_by(officer_id=u.id)
    pg = base_q.order_by(HseTbt.date.desc()).paginate(page=page, per_page=15, error_out=False)
    officer_cache = {}
    def _officer_name(oid):
        if oid not in officer_cache:
            off = db.session.get(User, oid)
            officer_cache[oid] = off.name if off else ""
        return officer_cache[oid]
    items = []
    for t in pg.items:
        att = HseTbtAttendance.query.filter_by(tbt_id=t.id).all()
        sup = db.session.get(User, t.supervisor_id) if t.supervisor_id else None
        items.append({"id": t.id, "date": t.date.isoformat(), "topic": t.topic or "",
                      "location": t.location or "", "attendee_count": len(att),
                      "supervisor_name": sup.name if sup else "",
                      "supervisor_role": sup.role if sup else "",
                      "officer_name": _officer_name(t.officer_id),
                      "attendees": [{"emp_number": a.emp_number, "emp_name": a.emp_name} for a in att]})
    return jsonify({"items": items, "page": pg.page, "pages": pg.pages, "total": pg.total})

@app.route("/api/hse/supervisor_lookup")
@api_login_required
def api_hse_supervisor_lookup():
    code = freq.args.get("code", "").strip()
    if not code:
        return jsonify({"found": False})
    sup = User.query.filter(User.supervisor_code == code, User.is_active == True).first()
    if sup:
        return jsonify({"found": True, "name": sup.name, "id": sup.id})
    return jsonify({"found": False})

@app.route("/api/hse/tbt", methods=["POST"])
@api_hse_officer_required
def api_hse_tbt_create():
    u = get_api_user()
    data = freq.get_json()
    today = datetime.now(RIYADH_TZ).date()
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "Topic required"}), 400
    # Resolve supervisor by code if provided
    sup_id = None
    sup_code = (data.get("supervisor_code") or "").strip()
    if sup_code:
        sup = User.query.filter(User.supervisor_code == sup_code, User.is_active == True).first()
        if sup:
            sup_id = sup.id
    t = HseTbt(officer_id=u.id, date=_parse_date(data.get("date"), today),
               topic=topic, location=(data.get("location") or "").strip(),
               supervisor_id=sup_id, company_id=api_cid())
    db.session.add(t)
    db.session.flush()
    for att in (data.get("attendees") or []):
        db.session.add(HseTbtAttendance(tbt_id=t.id,
                                        emp_number=att.get("emp_number", ""),
                                        emp_name=att.get("emp_name", "")))
    db.session.commit()
    return jsonify({"ok": True, "id": t.id})

@app.route("/api/hse/tbt/<int:tbt_id>", methods=["PUT"])
@api_hse_officer_required
def api_hse_tbt_update(tbt_id):
    u = get_api_user()
    t = HseTbt.query.filter_by(id=tbt_id, officer_id=u.id).first_or_404()
    data = freq.get_json()
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "Topic required"}), 400
    t.topic    = topic
    t.location = (data.get("location") or t.location or "").strip()
    HseTbtAttendance.query.filter_by(tbt_id=t.id).delete()
    for att in (data.get("attendees") or []):
        db.session.add(HseTbtAttendance(tbt_id=t.id,
                                        emp_number=att.get("emp_number", ""),
                                        emp_name=att.get("emp_name", "")))
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/api/hse/tbt/<int:tbt_id>/photo", methods=["POST"])
@api_hse_officer_required
def api_hse_tbt_photo(tbt_id):
    u = get_api_user()
    t = HseTbt.query.filter_by(id=tbt_id, officer_id=u.id).first_or_404()
    f = freq.files.get("photo")
    path = _save_hse_photo(f, "tbt", company_id=api_cid())
    if not path:
        return jsonify({"error": "Invalid or missing photo"}), 400
    if t.sign_photo_path:
        try: os.remove(os.path.join(HSE_UPLOAD_DIR, t.sign_photo_path))
        except Exception: pass
    t.sign_photo_path = path
    db.session.commit()
    return jsonify({"ok": True, "path": path})

@app.route("/api/hse/tbt/<int:tbt_id>", methods=["DELETE"])
@api_hse_officer_required
def api_hse_tbt_delete_api(tbt_id):
    u = get_api_user()
    t = HseTbt.query.filter_by(id=tbt_id, officer_id=u.id).first_or_404()
    HseTbtAttendance.query.filter_by(tbt_id=t.id).delete()
    db.session.delete(t)
    db.session.commit()
    return jsonify({"ok": True})

# ── Supervisor Employees for TBT ───────────────────────
@app.route("/api/hse/tbt/supervisor-employees", methods=["GET"])
def api_tbt_supervisor_employees():
    u = get_api_user() or cur_user()
    if not u or not u.is_active:
        return jsonify({"error": "Unauthorized"}), 401
    if u.role not in ("safety_officer", "safety_supervisor", "admin", "super_admin"):
        return jsonify({"error": "Forbidden"}), 403
    sup_code = freq.args.get("supervisor_code", "").strip()
    if not sup_code:
        return jsonify({"error": "missing supervisor_code"}), 400
    sup = User.query.filter_by(supervisor_code=sup_code, is_active=True).first()
    if not sup:
        return jsonify({"error": "not_found"}), 404
    emps = Employee.query.filter_by(user_id=sup.id, is_active=True).filter(
        Employee.status != "resigned").all()
    return jsonify({"supervisor": {"id": sup.id, "name": sup.name},
                    "employees": [{"emp_number": e.emp_number, "name": e.name,
                                   "department": e.department} for e in emps]})


# ── User Location ───────────────────────────────────────
@app.route("/api/location", methods=["POST"])
def api_location_save():
    u = get_api_user() or cur_user()
    if not u: return jsonify(error="Unauthorized"), 401
    body = freq.get_json() or {}
    pkg = body.get("pkg")
    unit = str(body.get("unit", "")).strip()
    area_text = str(body.get("area_text", "")).strip()
    if not pkg or not unit:
        return jsonify(error="pkg and unit required"), 400
    loc = UserLocation.query.filter_by(user_id=u.id).first()
    if not loc:
        loc = UserLocation(user_id=u.id, company_id=u.company_id)
        db.session.add(loc)
    loc.pkg = int(pkg)
    loc.unit = unit
    loc.area_text = area_text
    loc.updated_at = datetime.utcnow()

    # Safety officers: auto-create HseCheckin so reports/dashboards keep working
    if getattr(u, "role", None) == "safety_officer":
        today_local = datetime.now(RIYADH_TZ).date()
        loc_text = f"PKG{pkg} Unit{unit}"
        if area_text:
            loc_text += f" - {area_text}"
        existing_ci = HseCheckin.query.filter_by(officer_id=u.id, date=today_local).first()
        if existing_ci:
            existing_ci.location = loc_text
        else:
            db.session.add(HseCheckin(officer_id=u.id, date=today_local,
                                      location=loc_text, company_id=u.company_id))

    db.session.commit()
    return jsonify(ok=True)


@app.route("/api/location/me", methods=["GET"])
def api_location_me():
    u = get_api_user() or cur_user()
    if not u: return jsonify(error="Unauthorized"), 401
    loc = UserLocation.query.filter_by(user_id=u.id).first()
    if not loc:
        return jsonify(location=None)
    return jsonify(location={
        "pkg": loc.pkg, "unit": loc.unit,
        "area_text": loc.area_text,
        "updated_at": loc.updated_at.isoformat() if loc.updated_at else None
    })


@app.route("/api/location/nearest", methods=["GET"])
def api_location_nearest():
    u = get_api_user() or cur_user()
    if not u: return jsonify(error="Unauthorized"), 401
    my_loc = UserLocation.query.filter_by(user_id=u.id).first()
    if not my_loc:
        return jsonify(error="no_location"), 400
    # السيفتي يرى المشرفين — المشرف يرى السيفتي
    if u.role == "safety_officer":
        target_roles = ["supervisor", "site_supervisor", "safety_supervisor"]
    elif u.role in ["supervisor", "site_supervisor", "safety_supervisor", "admin"]:
        target_roles = ["safety_officer"]
    else:
        return jsonify(nearest=[])
    cutoff = datetime.utcnow() - timedelta(hours=24)
    candidates = (db.session.query(User, UserLocation)
                  .join(UserLocation, User.id == UserLocation.user_id)
                  .filter(User.role.in_(target_roles))
                  .filter(User.company_id == u.company_id)
                  .filter(User.is_active == True)
                  .filter(UserLocation.updated_at >= cutoff)
                  .all())
    results = []
    for c, cloc in candidates:
        if cloc.pkg == my_loc.pkg and cloc.unit == my_loc.unit:
            score = 0
        elif cloc.pkg == my_loc.pkg:
            score = 1
        else:
            score = 2
        results.append({
            "user_id": c.id, "name": c.name, "role": c.role,
            "supervisor_code": c.supervisor_code,
            "pkg": cloc.pkg, "unit": cloc.unit, "area_text": cloc.area_text,
            "updated_at": cloc.updated_at.isoformat(),
            "proximity": score
        })
    results.sort(key=lambda x: (x["proximity"], x["name"]))
    return jsonify(nearest=results[:10])


@app.route("/api/location/all", methods=["GET"])
def api_location_all():
    u = get_api_user() or cur_user()
    if not u: return jsonify(error="Unauthorized"), 401
    if u.role not in ("admin", "super_admin"):
        return jsonify(error="forbidden"), 403
    cutoff = datetime.utcnow() - timedelta(hours=24)
    rows = (db.session.query(User, UserLocation)
            .join(UserLocation, User.id == UserLocation.user_id)
            .filter(User.company_id == u.company_id)
            .filter(User.is_active == True)
            .filter(User.role.in_(["supervisor", "site_supervisor", "safety_officer"]))
            .filter(UserLocation.updated_at >= cutoff)
            .all())
    result = [{"user_id": usr.id, "name": usr.name, "role": usr.role,
               "supervisor_code": usr.supervisor_code,
               "pkg": loc.pkg, "unit": loc.unit, "area_text": loc.area_text or "",
               "updated_at": loc.updated_at.isoformat() if loc.updated_at else ""}
              for usr, loc in rows]
    result.sort(key=lambda x: (x["role"], x["pkg"] or 0, x["unit"] or ""))
    return jsonify(result)


@app.route("/api/location/my-supervisors", methods=["GET"])
def api_location_my_supervisors():
    u = get_api_user() or cur_user()
    if not u: return jsonify(error="Unauthorized"), 401
    if u.role not in ["site_supervisor", "admin", "super_admin"]:
        return jsonify(error="forbidden"), 403
    sups = (db.session.query(User, UserLocation)
            .join(UserLocation, User.id == UserLocation.user_id)
            .filter(User.role == "supervisor")
            .filter(User.company_id == u.company_id)
            .filter(User.is_active == True)
            .all())
    result = [{"user_id": s.id, "name": s.name,
               "supervisor_code": s.supervisor_code,
               "pkg": loc.pkg, "unit": loc.unit, "area_text": loc.area_text,
               "updated_at": loc.updated_at.isoformat() if loc.updated_at else None}
              for s, loc in sups]
    result.sort(key=lambda x: (x["pkg"] or 0, x["unit"] or ""))
    return jsonify(supervisors=result)


# ── Near Miss ──────────────────────────────────────────
@app.route("/api/hse/nearmiss", methods=["GET"])
@api_hse_officer_required
def api_hse_nearmiss_list():
    u = get_api_user()
    page = int(freq.args.get("page", 1))
    if u.role in ("safety_supervisor", "safety_manager", "admin", "super_admin"):
        officer_ids = [o.id for o in _get_safety_officers(u)]
        q = HseNearMiss.query.filter(HseNearMiss.officer_id.in_(officer_ids))
    else:
        q = HseNearMiss.query.filter_by(officer_id=u.id)
    pg = q.order_by(HseNearMiss.date.desc()).paginate(page=page, per_page=15, error_out=False)
    officer_cache = {}
    def _off(oid):
        if oid not in officer_cache:
            off = db.session.get(User, oid)
            officer_cache[oid] = off.name if off else ""
        return officer_cache[oid]
    items = [{"id": nm.id, "date": nm.date.isoformat(), "location": nm.location or "",
              "description": nm.description or "", "immediate_cause": nm.immediate_cause or "",
              "action_taken": nm.action_taken or "", "reported_to": nm.reported_to or "",
              "officer_name": _off(nm.officer_id)} for nm in pg.items]
    return jsonify({"items": items, "page": pg.page, "pages": pg.pages, "total": pg.total})

@app.route("/api/hse/nearmiss", methods=["POST"])
@api_hse_officer_required
def api_hse_nearmiss_create():
    u = get_api_user()
    data = freq.get_json()
    today = datetime.now(RIYADH_TZ).date()
    description = (data.get("description") or "").strip()
    immediate_cause = (data.get("immediate_cause") or "").strip()
    action_taken = (data.get("action_taken") or "").strip()
    reported_to = (data.get("reported_to") or "").strip()
    if not all([description, immediate_cause, action_taken, reported_to]):
        return jsonify({"error": "All fields required"}), 400
    nm = HseNearMiss(officer_id=u.id, date=_parse_date(data.get("date"), today),
                     location=(data.get("location") or "").strip(),
                     description=description, immediate_cause=immediate_cause,
                     action_taken=action_taken, reported_to=reported_to,
                     company_id=api_cid())
    db.session.add(nm)
    db.session.commit()
    return jsonify({"ok": True, "id": nm.id})

@app.route("/api/hse/nearmiss/<int:nm_id>", methods=["PUT"])
@api_hse_officer_required
def api_hse_nearmiss_update(nm_id):
    u = get_api_user()
    nm = HseNearMiss.query.filter_by(id=nm_id, officer_id=u.id).first_or_404()
    data = freq.get_json()
    nm.location        = (data.get("location")        or nm.location or "").strip()
    nm.description     = (data.get("description")     or nm.description or "").strip()
    nm.immediate_cause = (data.get("immediate_cause") or nm.immediate_cause or "").strip()
    nm.action_taken    = (data.get("action_taken")    or nm.action_taken or "").strip()
    nm.reported_to     = (data.get("reported_to")     or nm.reported_to or "").strip()
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/api/hse/nearmiss/<int:nm_id>/photo", methods=["POST"])
@api_hse_officer_required
def api_hse_nearmiss_photo(nm_id):
    u = get_api_user()
    nm = HseNearMiss.query.filter_by(id=nm_id, officer_id=u.id).first_or_404()
    f = freq.files.get("photo")
    path = _save_hse_photo(f, "nm", company_id=api_cid())
    if not path:
        return jsonify({"error": "Invalid or missing photo"}), 400
    if nm.photo_path:
        try: os.remove(os.path.join(HSE_UPLOAD_DIR, nm.photo_path))
        except Exception: pass
    nm.photo_path = path
    db.session.commit()
    return jsonify({"ok": True, "path": path})

@app.route("/api/hse/nearmiss/<int:nm_id>", methods=["DELETE"])
@api_hse_officer_required
def api_hse_nearmiss_delete_api(nm_id):
    u = get_api_user()
    nm = HseNearMiss.query.filter_by(id=nm_id, officer_id=u.id).first_or_404()
    db.session.delete(nm)
    db.session.commit()
    return jsonify({"ok": True})

# ── BBS ────────────────────────────────────────────────
@app.route("/api/hse/bbs/today", methods=["GET"])
@api_hse_officer_required
def api_hse_bbs_today():
    u = get_api_user()
    today = datetime.now(RIYADH_TZ).date()
    rec = HseBbs.query.filter_by(officer_id=u.id, date=today).first()
    if not rec:
        return jsonify(None)
    return jsonify({"id": rec.id, "date": rec.date.isoformat(),
                    "card_count": rec.card_count, "notes": rec.notes or ""})

@app.route("/api/hse/bbs", methods=["GET"])
@api_hse_officer_required
def api_hse_bbs_list():
    u = get_api_user()
    if u.role in ("safety_supervisor", "safety_manager", "admin", "super_admin"):
        officer_ids = [o.id for o in _get_safety_officers(u)]
        q = HseBbs.query.filter(HseBbs.officer_id.in_(officer_ids))
        limit = 100
    else:
        q = HseBbs.query.filter_by(officer_id=u.id)
        limit = 30
    recs = q.order_by(HseBbs.date.desc()).limit(limit).all()
    off_map = {}
    def _off(oid):
        if oid not in off_map:
            obj = db.session.get(User, oid)
            off_map[oid] = obj.name if obj else ""
        return off_map[oid]
    return jsonify([{"id": r.id, "date": r.date.isoformat(),
                     "card_count": r.card_count, "notes": r.notes or "",
                     "officer_name": _off(r.officer_id)} for r in recs])

@app.route("/api/hse/bbs", methods=["POST"])
@api_hse_officer_required
def api_hse_bbs_save():
    u = get_api_user()
    data = freq.get_json()
    today = datetime.now(RIYADH_TZ).date()
    card_count = int(data.get("card_count") or 0)
    notes = (data.get("notes") or "").strip()
    rec = HseBbs.query.filter_by(officer_id=u.id, date=today).first()
    if rec:
        rec.card_count = card_count
        rec.notes = notes
    else:
        rec = HseBbs(officer_id=u.id, date=today, card_count=card_count,
                     notes=notes, company_id=api_cid())
        db.session.add(rec)
    db.session.commit()
    return jsonify({"ok": True, "id": rec.id})

@app.route("/api/hse/bbs/<int:bbs_id>", methods=["DELETE"])
@api_hse_officer_required
def api_hse_bbs_delete_api(bbs_id):
    u = get_api_user()
    rec = HseBbs.query.filter_by(id=bbs_id, officer_id=u.id).first_or_404()
    db.session.delete(rec)
    db.session.commit()
    return jsonify({"ok": True})

# ── Employee Lookup (for TBT attendance) ──────────────
@app.route("/api/hse/employee_lookup", methods=["GET"])
@api_hse_officer_required
def api_hse_employee_lookup():
    emp_number = (freq.args.get("emp_number") or "").strip()
    if not emp_number:
        return jsonify({"found": False})
    emp = Employee.query.filter_by(emp_number=emp_number, status="active").first()
    if not emp:
        for prefix in ["NSH-", "GA-"]:
            if emp_number.startswith(prefix):
                emp = Employee.query.filter_by(emp_number=emp_number[len(prefix):], status="active").first()
                if emp:
                    break
        if not emp:
            for prefix in ["NSH-", "GA-"]:
                emp = Employee.query.filter_by(emp_number=prefix + emp_number, status="active").first()
                if emp:
                    break
    if emp:
        return jsonify({"found": True, "name": emp.name, "emp_number": emp.emp_number})
    return jsonify({"found": False})

# ── HSE Dashboard (supervisor 39468 only) ─────────────
@app.route("/api/hse/dashboard", methods=["GET"])
@api_hse_supervisor_required
def api_hse_dashboard():
    u = get_api_user()
    company = u.company_id
    today = datetime.now(RIYADH_TZ).date()
    days_since_sunday = today.isoweekday() % 7
    week_start = today - timedelta(days=days_since_sunday)

    officers_q = User.query.filter_by(role="safety_officer", is_active=True)
    if company:
        officers_q = officers_q.filter_by(company_id=company)
    officers = officers_q.all()
    officer_ids = [o.id for o in officers]

    # batch all aggregate queries — one query per metric instead of N per officer
    def _grp(col, date_col):
        return dict(db.session.query(col, func.count())
                    .filter(col.in_(officer_ids), date_col.between(week_start, today))
                    .group_by(col).all())

    ci_map  = {r.officer_id: r for r in
               HseCheckin.query.filter(HseCheckin.officer_id.in_(officer_ids),
                                       HseCheckin.date == today).all()}
    obs_map = _grp(HseObservation.officer_id, HseObservation.date)
    jso_map = _grp(HseJsoClosure.officer_id, HseJsoClosure.date)
    tbt_map = _grp(HseTbt.officer_id, HseTbt.date)
    nm_map  = _grp(HseNearMiss.officer_id, HseNearMiss.date)
    bbs_map = dict(
        db.session.query(HseBbs.officer_id,
                         func.coalesce(func.sum(HseBbs.card_count), 0))
        .filter(HseBbs.officer_id.in_(officer_ids),
                HseBbs.date.between(week_start, today))
        .group_by(HseBbs.officer_id).all()
    )

    data = []
    for o in officers:
        ci = ci_map.get(o.id)
        obs_w = obs_map.get(o.id, 0)
        jso_w = jso_map.get(o.id, 0)
        tbt_w = tbt_map.get(o.id, 0)
        nm_w  = nm_map.get(o.id, 0)
        bbs_w = int(bbs_map.get(o.id, 0))
        data.append({"id": o.id, "name": o.name, "checked_in": ci is not None,
                     "location": ci.location if ci else None,
                     "obs_week": obs_w, "jso_week": jso_w, "tbt_week": tbt_w,
                     "nm_week": nm_w, "bbs_week": bbs_w,
                     "total_week": obs_w + jso_w + tbt_w + nm_w})

    # high-risk open observations scoped to this company's officers
    high_risk = HseObservation.query.filter(
        HseObservation.status == "open",
        HseObservation.risk_level == "H",
        HseObservation.officer_id.in_(officer_ids)
    ).order_by(HseObservation.date).all()
    off_map = {o.id: o for o in officers}
    hr_items = []
    for obs in high_risk:
        off_obj = off_map.get(obs.officer_id)
        hr_items.append({"id": obs.id, "date": obs.date.isoformat(),
                         "officer": off_obj.name if off_obj else "?",
                         "category": obs.category or "", "location": obs.location or "",
                         "description": obs.description or ""})

    return jsonify({"today": today.isoformat(), "week_start": week_start.isoformat(),
                    "officers": data, "high_risk_open": hr_items})


@app.get("/api/hse/photo/<path:filename>")
@api_login_required
def api_hse_photo_serve(filename):
    from flask import send_from_directory
    return send_from_directory(HSE_UPLOAD_DIR, filename)


@app.route("/api/hse/officer/<int:officer_id>/detail", methods=["GET"])
@api_hse_supervisor_required
def api_hse_officer_detail(officer_id):
    u = get_api_user()
    days = min(int(freq.args.get("days", 30)), 180)
    q = User.query.filter_by(id=officer_id, role="safety_officer", is_active=True)
    if u.company_id:
        q = q.filter_by(company_id=u.company_id)
    officer = q.first_or_404()
    today = datetime.now(RIYADH_TZ).date()
    since = today - timedelta(days=days)
    ci = HseCheckin.query.filter_by(officer_id=officer_id, date=today).first()
    checkin_hist = HseCheckin.query.filter(
        HseCheckin.officer_id == officer_id,
        HseCheckin.date >= since
    ).order_by(HseCheckin.date.desc()).all()
    obs_list = HseObservation.query.filter(
        HseObservation.officer_id == officer_id,
        HseObservation.date >= since
    ).order_by(HseObservation.date.desc()).all()
    tbt_list = HseTbt.query.filter(
        HseTbt.officer_id == officer_id,
        HseTbt.date >= since
    ).order_by(HseTbt.date.desc()).all()
    jso_list = HseJsoClosure.query.filter(
        HseJsoClosure.officer_id == officer_id,
        HseJsoClosure.date >= since
    ).order_by(HseJsoClosure.date.desc()).all()
    nm_list = HseNearMiss.query.filter(
        HseNearMiss.officer_id == officer_id,
        HseNearMiss.date >= since
    ).order_by(HseNearMiss.date.desc()).all()
    bbs_list = HseBbs.query.filter(
        HseBbs.officer_id == officer_id,
        HseBbs.date >= since
    ).order_by(HseBbs.date.desc()).all()
    return jsonify({
        "officer": {"id": officer.id, "name": officer.name},
        "checkin_today": {"id": ci.id, "date": ci.date.isoformat(),
                          "location": ci.location} if ci else None,
        "checkin_history": [{"id": c.id, "date": c.date.isoformat(),
                              "location": c.location} for c in checkin_hist],
        "period_days": days,
        "observations": [{
            "id": o.id, "date": o.date.isoformat(), "location": o.location or "",
            "obs_type": o.obs_type or "", "category": o.category or "",
            "risk_level": o.risk_level or "", "description": o.description or "",
            "action_taken": o.action_taken or "", "status": o.status,
            "closed_at": o.closed_at.isoformat() if o.closed_at else None,
            "closure_action": o.closure_action or "",
            "photos": [{"path": p.photo_path, "photo_type": p.photo_type}
                       for p in HseObservationPhoto.query.filter_by(observation_id=o.id).all()]
        } for o in obs_list],
        "tbts": [{
            "id": t.id, "date": t.date.isoformat(), "topic": t.topic or "",
            "location": t.location or "",
            "sign_photo_path": t.sign_photo_path or "",
            "supervisor_name": (db.session.get(User, t.supervisor_id).name
                                if t.supervisor_id and db.session.get(User, t.supervisor_id) else ""),
            "attendee_count": HseTbtAttendance.query.filter_by(tbt_id=t.id).count(),
            "attendees": [{"emp_number": a.emp_number, "emp_name": a.emp_name}
                          for a in HseTbtAttendance.query.filter_by(tbt_id=t.id).all()]
        } for t in tbt_list],
        "jso_closures": [{
            "id": j.id, "date": j.date.isoformat(), "jso_number": j.jso_number,
            "location": j.location or "", "action_taken": j.action_taken or "",
            "photo_path": j.photo_path or ""
        } for j in jso_list],
        "near_misses": [{
            "id": n.id, "date": n.date.isoformat(), "location": n.location or "",
            "description": n.description or "", "immediate_cause": n.immediate_cause or "",
            "action_taken": n.action_taken or "", "reported_to": n.reported_to or "",
            "photo_path": n.photo_path or ""
        } for n in nm_list],
        "bbs": [{
            "id": b.id, "date": b.date.isoformat(),
            "card_count": b.card_count, "notes": b.notes or ""
        } for b in bbs_list]
    })


# ===================== HSE Phase 2 Mobile APIs =====================

@app.get("/api/hse/ptw")
@api_hse_officer_required
def api_hse_ptw_list():
    u = get_api_user()
    today = datetime.now(RIYADH_TZ).date()
    if u.role in ("safety_supervisor", "safety_manager", "admin", "super_admin"):
        officer_ids = [o.id for o in _get_safety_officers(u)]
        q = HsePtw.query.filter(HsePtw.officer_id.in_(officer_ids))
    else:
        q = HsePtw.query.filter_by(officer_id=u.id)
    ptw_list = q.order_by(HsePtw.week_start.desc()).limit(50).all()
    officer_cache = {}
    def _off(oid):
        if oid not in officer_cache:
            off = db.session.get(User, oid)
            officer_cache[oid] = off.name if off else ""
        return officer_cache[oid]
    return jsonify([{
        "id": p.id,
        "permit_number": p.permit_number,
        "permit_type": p.permit_type,
        "description": p.description or "",
        "location": p.location or "",
        "week_start": p.week_start.isoformat(),
        "week_end": p.week_end.isoformat(),
        "status": p.status,
        "attached_to_id": p.attached_to_id,
        "expired": p.week_end < today,
        "officer_name": _off(p.officer_id),
    } for p in ptw_list])


@app.post("/api/hse/ptw")
@api_hse_officer_required
def api_hse_ptw_create():
    u = get_api_user()
    data = freq.get_json(silent=True) or {}
    permit_number = (data.get("permit_number") or "").strip()
    permit_type   = (data.get("permit_type") or "").strip()
    if not permit_number or not permit_type:
        return jsonify({"error": "permit_number and permit_type required"}), 400
    ws, we = _ptw_current_week()
    if data.get("week_start"):
        try:
            ws = date.fromisoformat(data["week_start"])
            we = ws + timedelta(days=5)
        except ValueError:
            pass
    loc = (data.get("location") or "").strip() or _hse_today_location(u.id)
    p = HsePtw(
        officer_id=u.id, company_id=u.company_id,
        permit_number=permit_number, permit_type=permit_type,
        description=(data.get("description") or "").strip(),
        location=loc, week_start=ws, week_end=we,
        attached_to_id=data.get("attached_to_id"),
    )
    db.session.add(p)
    db.session.commit()
    return jsonify({"id": p.id, "status": "created"}), 201


@app.post("/api/hse/ptw/<int:ptw_id>/status")
@api_hse_officer_required
def api_hse_ptw_status(ptw_id):
    u = get_api_user()
    p = HsePtw.query.filter_by(id=ptw_id, officer_id=u.id).first_or_404()
    data = freq.get_json(silent=True) or {}
    new_status = data.get("status", "active")
    if new_status not in ("active", "suspended", "closed"):
        return jsonify({"error": "invalid status"}), 400
    if new_status == "active" and p.week_end < datetime.now(RIYADH_TZ).date():
        ws, we = _ptw_current_week()
        p.week_start = ws
        p.week_end   = we
    p.status = new_status
    db.session.commit()
    return jsonify({"id": p.id, "status": p.status})


@app.get("/api/hse/manpower/today")
@api_hse_officer_required
def api_hse_manpower_today():
    u = get_api_user()
    today = datetime.now(RIYADH_TZ).date()
    rec = HseManpower.query.filter_by(officer_id=u.id, date=today).first()
    if not rec:
        return jsonify(None)
    import json as _json
    return jsonify({
        "id": rec.id, "date": rec.date.isoformat(),
        "location": rec.location or "",
        "total_count": rec.total_count,
        "breakdown": _json.loads(rec.breakdown) if rec.breakdown else {},
        "notes": rec.notes or "",
    })


@app.post("/api/hse/manpower")
@api_hse_officer_required
def api_hse_manpower_save():
    u = get_api_user()
    data = freq.get_json(silent=True) or {}
    today = datetime.now(RIYADH_TZ).date()
    total = int(data.get("total_count", 0))
    loc   = (data.get("location") or "").strip() or _hse_today_location(u.id)
    notes = (data.get("notes") or "").strip()
    import json as _json
    breakdown = data.get("breakdown") or {}
    breakdown_json = _json.dumps(breakdown, ensure_ascii=False) if breakdown else None

    existing = HseManpower.query.filter_by(officer_id=u.id, date=today).first()
    if existing:
        existing.total_count = total
        existing.location    = loc
        existing.notes       = notes
        existing.breakdown   = breakdown_json
    else:
        existing = HseManpower(
            officer_id=u.id, company_id=u.company_id,
            date=today, total_count=total,
            location=loc, notes=notes, breakdown=breakdown_json,
        )
        db.session.add(existing)
    db.session.commit()
    return jsonify({"id": existing.id, "status": "saved"})


@app.get("/api/hse/manpower/history")
@api_hse_officer_required
def api_hse_manpower_history():
    u = get_api_user()
    import json as _json
    if u.role in ("safety_supervisor", "safety_manager", "admin", "super_admin"):
        officer_ids = [o.id for o in _get_safety_officers(u)]
        q = HseManpower.query.filter(HseManpower.officer_id.in_(officer_ids))
        limit = 50
    else:
        q = HseManpower.query.filter_by(officer_id=u.id)
        limit = 14
    recs = q.order_by(HseManpower.date.desc()).limit(limit).all()
    off_map = {}
    def _off(oid):
        if oid not in off_map:
            obj = db.session.get(User, oid)
            off_map[oid] = obj.name if obj else ""
        return off_map[oid]
    return jsonify([{
        "id": r.id, "date": r.date.isoformat(),
        "location": r.location or "",
        "total_count": r.total_count,
        "breakdown": _json.loads(r.breakdown) if r.breakdown else {},
        "notes": r.notes or "",
        "officer_name": _off(r.officer_id),
    } for r in recs])


@app.get("/api/hse/inspection/today")
@api_hse_officer_required
def api_hse_inspection_today():
    u = get_api_user()
    today = datetime.now(RIYADH_TZ).date()
    rec = HseInspection.query.filter_by(officer_id=u.id, date=today).first()
    if not rec:
        return jsonify(None)
    import json as _json
    return jsonify({
        "id": rec.id, "date": rec.date.isoformat(),
        "location": rec.location or "",
        "overall_score": rec.overall_score,
        "notes": rec.notes or "",
        "checklist": _json.loads(rec.checklist) if rec.checklist else [],
    })


@app.post("/api/hse/inspection")
@api_hse_officer_required
def api_hse_inspection_save():
    u = get_api_user()
    data = freq.get_json(silent=True) or {}
    today = datetime.now(RIYADH_TZ).date()
    import json as _json
    loc    = (data.get("location") or "").strip() or _hse_today_location(u.id)
    notes  = (data.get("notes") or "").strip()
    items  = data.get("checklist", [])
    if not items:
        items = [{"item": t, "ok": False, "note": ""} for t in INSPECTION_ITEMS]
    ok_count = sum(1 for i in items if i.get("ok"))
    score    = round(ok_count / len(items) * 100, 1) if items else 0.0

    existing = HseInspection.query.filter_by(officer_id=u.id, date=today).first()
    if existing:
        existing.location      = loc
        existing.checklist     = _json.dumps(items, ensure_ascii=False)
        existing.overall_score = score
        existing.notes         = notes
    else:
        existing = HseInspection(
            officer_id=u.id, company_id=u.company_id,
            date=today, location=loc,
            checklist=_json.dumps(items, ensure_ascii=False),
            overall_score=score, notes=notes,
        )
        db.session.add(existing)
    db.session.commit()
    return jsonify({"id": existing.id, "score": score, "status": "saved"})


@app.get("/api/hse/inspection/history")
@api_hse_officer_required
def api_hse_inspection_history():
    u = get_api_user()
    if u.role in ("safety_supervisor", "safety_manager", "admin", "super_admin"):
        officer_ids = [o.id for o in _get_safety_officers(u)]
        q = HseInspection.query.filter(HseInspection.officer_id.in_(officer_ids))
        limit = 50
    else:
        q = HseInspection.query.filter_by(officer_id=u.id)
        limit = 10
    recs = q.order_by(HseInspection.date.desc()).limit(limit).all()
    off_map = {}
    def _off(oid):
        if oid not in off_map:
            obj = db.session.get(User, oid)
            off_map[oid] = obj.name if obj else ""
        return off_map[oid]
    return jsonify([{
        "id": r.id, "date": r.date.isoformat(),
        "location": r.location or "",
        "overall_score": r.overall_score,
        "notes": r.notes or "",
        "officer_name": _off(r.officer_id),
    } for r in recs])


@app.get("/api/hse/my-ca")
@api_hse_officer_required
def api_hse_my_ca():
    u     = get_api_user()
    today = datetime.now(RIYADH_TZ).date()
    cas   = (HseCorrectiveAction.query
             .join(HseObservation, HseCorrectiveAction.observation_id == HseObservation.id)
             .filter(HseObservation.officer_id == u.id)
             .order_by(HseCorrectiveAction.due_date.asc())
             .all())
    return jsonify([{
        "id":              c.id,
        "observation_id":  c.observation_id,
        "assigned_to":     c.assigned_to or "",
        "due_date":        c.due_date.isoformat(),
        "action_required": c.action_required,
        "status":          c.status,
        "overdue":         c.status != "completed" and c.due_date < today,
        "completed_at":    c.completed_at.isoformat() if c.completed_at else None,
        "completion_notes": c.completion_notes or "",
        "created_at":      c.created_at.strftime("%Y-%m-%d") if c.created_at else "",
    } for c in cas])




# ===================== PTW Training Mobile API =====================

def api_ptw_officer_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        u = get_api_user()
        if not u or not u.is_active or u.role not in ("safety_officer", "safety_supervisor",
                                                       "admin", "super_admin"):
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return wrapper

def _ptw_door_to_dict(mod_seq, door, sub):
    return {
        "seq":       door["seq"],
        "title":     door["title"],
        "ref":       door.get("ref", ""),
        "brief":     door.get("brief", ""),
        "questions": door.get("questions", []),
        "ref_only":  bool(door.get("ref_only")),
        "status":    sub.status if sub else "not_started",
        "sub_id":    sub.id if sub else None,
        "answers":   json.loads(sub.answers) if sub and sub.answers else [],
        "photo_path": sub.photo_path if sub else None,
        "reviewer_note": sub.reviewer_note if sub else None,
        "submitted_at": sub.submitted_at.isoformat() if sub and sub.submitted_at else None,
    }

@app.get("/api/ptw-training/status")
@api_ptw_officer_required
def api_ptw_training_status():
    u = get_api_user()
    if u.role != "safety_officer":
        return jsonify({"error": "Only safety_officer can access PTW training"}), 403
    active = bool(getattr(u, "ptw_training_active", False))
    if not active:
        return jsonify({"active": False, "modules": []})
    progress = _ptw_progress(u.id)
    mods = []
    for m in PTW_MODULES:
        field_doors = [d for d in m["doors"] if not d.get("ref_only")]
        done_count = sum(1 for d in field_doors
                        if progress.get((m["seq"], d["seq"])) and
                           progress[(m["seq"], d["seq"])].status == "approved")
        mods.append({
            "seq":     m["seq"],
            "title":   m["title"],
            "title_ar": m.get("title_ar", ""),
            "done":    done_count,
            "total":   len(field_doors),
            "locked":  not _ptw_module_unlocked(progress, m["seq"]),
        })
    return jsonify({"active": True, "modules": mods})


@app.get("/api/ptw-training/module/<int:mod_seq>/doors")
@api_ptw_officer_required
def api_ptw_module_doors(mod_seq):
    u = get_api_user()
    mod = PTW_MOD_BY_SEQ.get(mod_seq)
    if not mod:
        return jsonify({"error": "Module not found"}), 404
    progress = _ptw_progress(u.id)
    doors = []
    for d in mod["doors"]:
        sub = progress.get((mod_seq, d["seq"]))
        door_dict = _ptw_door_to_dict(mod_seq, d, sub)
        door_dict["unlocked"] = _ptw_door_unlocked(progress, mod_seq, d["seq"])
        doors.append(door_dict)
    return jsonify({"module_seq": mod_seq, "title": mod["title"], "doors": doors})


@app.get("/api/ptw-training/module/<int:mod_seq>/door/<int:door_seq>")
@api_ptw_officer_required
def api_ptw_door_detail(mod_seq, door_seq):
    u = get_api_user()
    mod = PTW_MOD_BY_SEQ.get(mod_seq)
    if not mod:
        return jsonify({"error": "Module not found"}), 404
    door = next((d for d in mod["doors"] if d["seq"] == door_seq), None)
    if not door:
        return jsonify({"error": "Door not found"}), 404
    progress = _ptw_progress(u.id)
    sub = progress.get((mod_seq, door_seq))
    return jsonify(_ptw_door_to_dict(mod_seq, door, sub))


@app.post("/api/ptw-training/module/<int:mod_seq>/door/<int:door_seq>/submit")
@api_ptw_officer_required
def api_ptw_door_submit(mod_seq, door_seq):
    u = get_api_user()
    if u.role != "safety_officer":
        return jsonify({"error": "Forbidden"}), 403
    mod = PTW_MOD_BY_SEQ.get(mod_seq)
    if not mod:
        return jsonify({"error": "Module not found"}), 404
    door = next((d for d in mod["doors"] if d["seq"] == door_seq), None)
    if not door:
        return jsonify({"error": "Door not found"}), 404
    progress = _ptw_progress(u.id)
    if not _ptw_door_unlocked(progress, mod_seq, door_seq):
        return jsonify({"error": "Door is locked"}), 400
    existing = progress.get((mod_seq, door_seq))
    if existing and existing.status in ("pending", "approved"):
        return jsonify({"error": "Already submitted"}), 400
    # Reference door: auto-approve
    if door.get("ref_only"):
        sub = PtwDoorSubmission(
            officer_id=u.id, module_seq=mod_seq, door_seq=door_seq,
            answers=json.dumps([], ensure_ascii=False),
            photo_path=None, status="approved"
        )
        db.session.add(sub)
        db.session.commit()
        return jsonify({"status": "approved", "id": sub.id})
    # Field door: parse JSON body or multipart
    if freq.content_type and "multipart" in freq.content_type:
        answers_raw = freq.form.get("answers", "[]")
        try:
            answers = json.loads(answers_raw)
        except Exception:
            answers = []
        photo = freq.files.get("photo")
        photo_path = None
        if photo and photo.filename:
            import os as _os
            ext = _os.path.splitext(photo.filename)[1].lower()
            fname = f"ptw_{u.id}_{mod_seq}_{door_seq}_{int(datetime.utcnow().timestamp())}{ext}"
            save_dir = _os.path.join(app.root_path, "static", "hse_photos")
            _os.makedirs(save_dir, exist_ok=True)
            photo.save(_os.path.join(save_dir, fname))
            photo_path = fname
    else:
        data = freq.get_json(force=True) or {}
        answers = data.get("answers", [])
        photo_path = None
    sub = PtwDoorSubmission(
        officer_id=u.id, module_seq=mod_seq, door_seq=door_seq,
        answers=json.dumps(answers, ensure_ascii=False),
        photo_path=photo_path, status="pending"
    )
    db.session.add(sub)
    db.session.commit()
    return jsonify({"status": "pending", "id": sub.id})


@app.get("/api/ptw-training/supervisor/pending")
@api_ptw_officer_required
def api_ptw_supervisor_pending():
    u = get_api_user()
    if u.role not in ("safety_supervisor", "admin", "super_admin"):
        return jsonify({"error": "Forbidden"}), 403
    trainees = User.query.filter_by(role="safety_officer", is_active=True)
    if u.company_id:
        trainees = trainees.filter_by(company_id=u.company_id)
    trainee_ids = [o.id for o in trainees.all()
                   if getattr(o, "ptw_training_active", False)]
    pending = (PtwDoorSubmission.query
               .filter(PtwDoorSubmission.officer_id.in_(trainee_ids),
                       PtwDoorSubmission.status == "pending")
               .order_by(PtwDoorSubmission.submitted_at).all())
    officer_cache = {}
    def _off(oid):
        if oid not in officer_cache:
            off = db.session.get(User, oid)
            officer_cache[oid] = off.name if off else "?"
        return officer_cache[oid]
    rows = []
    for s in pending:
        mod  = PTW_MOD_BY_SEQ.get(s.module_seq, {})
        doors = mod.get("doors", [])
        door = next((d for d in doors if d["seq"] == s.door_seq), {})
        rows.append({
            "id":          s.id,
            "officer_id":  s.officer_id,
            "officer_name": _off(s.officer_id),
            "module_seq":  s.module_seq,
            "module_title": mod.get("title", ""),
            "door_seq":    s.door_seq,
            "door_title":  door.get("title", ""),
            "answers":     json.loads(s.answers) if s.answers else [],
            "photo_path":  s.photo_path or "",
            "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
        })
    return jsonify(rows)


@app.post("/api/ptw-training/supervisor/review/<int:sub_id>")
@api_ptw_officer_required
def api_ptw_supervisor_review(sub_id):
    u = get_api_user()
    if u.role not in ("safety_supervisor", "admin", "super_admin"):
        return jsonify({"error": "Forbidden"}), 403
    sub = db.session.get(PtwDoorSubmission, sub_id)
    if not sub:
        return jsonify({"error": "Submission not found"}), 404
    data = freq.get_json(force=True) or {}
    action = data.get("action", "")
    note   = (data.get("note") or "").strip()
    if action not in ("approve", "reject"):
        return jsonify({"error": "action must be approve or reject"}), 400
    sub.status        = "approved" if action == "approve" else "rejected"
    sub.reviewer_id   = u.id
    sub.reviewer_note = note or None
    db.session.commit()
    return jsonify({"status": sub.status})


# ===================== Welfare / Environment Mobile API =====================

def api_welfare_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        u = get_api_user()
        if not u or not u.is_active or u.role not in (
                "welfare_officer", "environment_officer",
                "welfare_supervisor", "admin", "super_admin"):
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return wrapper

def _welfare_obs_to_dict(obs, officer_cache=None):
    if officer_cache is not None:
        if obs.officer_id not in officer_cache:
            off = db.session.get(User, obs.officer_id)
            officer_cache[obs.officer_id] = off.name if off else "?"
        officer_name = officer_cache[obs.officer_id]
    else:
        off = db.session.get(User, obs.officer_id)
        officer_name = off.name if off else "?"
    photos = HseObservationPhoto.query.filter_by(observation_id=obs.id).all()
    return {
        "id":           obs.id,
        "date":         obs.date.isoformat(),
        "location":     obs.location or "",
        "obs_type":     obs.obs_type or "",
        "category":     obs.category or "",
        "risk_level":   obs.risk_level or "",
        "description":  obs.description or "",
        "action_taken": obs.action_taken or "",
        "status":       obs.status,
        "officer_name": officer_name,
        "closed_at":    obs.closed_at.isoformat() if obs.closed_at else None,
        "closure_action": obs.closure_action or "",
        "photos": [{"path": p.photo_path, "photo_type": p.photo_type} for p in photos],
    }

@app.get("/api/welfare/observations")
@api_welfare_required
def api_welfare_observations():
    u = get_api_user()
    if u.role in ("welfare_supervisor", "admin", "super_admin"):
        q = HseObservation.query
        if u.company_id:
            officers = User.query.filter(
                User.role.in_(["welfare_officer", "environment_officer"]),
                User.company_id == u.company_id, User.is_active == True
            ).all()
            oids = [o.id for o in officers]
            q = q.filter(HseObservation.officer_id.in_(oids))
        else:
            officers = User.query.filter(
                User.role.in_(["welfare_officer", "environment_officer"]),
                User.is_active == True
            ).all()
            q = q.filter(HseObservation.officer_id.in_([o.id for o in officers]))
    else:
        q = HseObservation.query.filter_by(officer_id=u.id)
    page  = int(freq.args.get("page", 1))
    per   = 20
    total = q.count()
    obs_list = q.order_by(HseObservation.date.desc()).offset((page-1)*per).limit(per).all()
    cache = {}
    return jsonify({
        "items": [_welfare_obs_to_dict(o, cache) for o in obs_list],
        "page":  page,
        "pages": max(1, -(-total // per)),
        "total": total,
    })

@app.post("/api/welfare/observation")
@api_welfare_required
def api_welfare_observation_create():
    u = get_api_user()
    if u.role not in ("welfare_officer", "environment_officer"):
        return jsonify({"error": "Only welfare/environment officer can submit"}), 403
    data = freq.get_json(force=True) or {}
    date_str    = (data.get("date") or "").strip()
    location    = (data.get("location") or "").strip()
    obs_type    = (data.get("obs_type") or "unsafe_condition").strip()
    category    = (data.get("category") or "").strip()
    risk_level  = (data.get("risk_level") or "L").strip()
    description = (data.get("description") or "").strip()
    action_taken = (data.get("action_taken") or "").strip()
    if not description:
        return jsonify({"error": "description required"}), 400
    try:
        obs_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else datetime.now(RIYADH_TZ).date()
    except ValueError:
        obs_date = datetime.now(RIYADH_TZ).date()
    obs = HseObservation(
        officer_id=u.id, company_id=u.company_id,
        date=obs_date, location=location,
        obs_type=obs_type, category=category, risk_level=risk_level,
        description=description, action_taken=action_taken, status="open"
    )
    db.session.add(obs)
    db.session.commit()
    return jsonify({"id": obs.id})

@app.post("/api/welfare/observation/<int:obs_id>/photo")
@api_welfare_required
def api_welfare_obs_photo(obs_id):
    u = get_api_user()
    obs = HseObservation.query.filter_by(id=obs_id, officer_id=u.id).first_or_404()
    photo = freq.files.get("photo")
    if not photo or not photo.filename:
        return jsonify({"error": "No photo"}), 400
    import os as _os
    ext   = _os.path.splitext(photo.filename)[1].lower()
    fname = f"wlf_obs_{obs_id}_{int(datetime.utcnow().timestamp())}{ext}"
    save_dir = _os.path.join(app.root_path, "static", "hse_photos")
    _os.makedirs(save_dir, exist_ok=True)
    photo.save(_os.path.join(save_dir, fname))
    p = HseObservationPhoto(observation_id=obs_id, photo_path=fname, photo_type="before")
    db.session.add(p)
    db.session.commit()
    return jsonify({"path": fname})

@app.get("/api/welfare/reports")
@api_welfare_required
def api_welfare_reports():
    u = get_api_user()
    if u.role in ("welfare_supervisor", "admin", "super_admin"):
        q = ReportFile.query
        if u.company_id:
            q = q.filter_by(company_id=u.company_id)
    else:
        q = ReportFile.query.filter_by(uploaded_by=u.id)
    items = q.order_by(ReportFile.report_date.desc()).limit(50).all()
    officer_cache = {}
    def _off(oid):
        if oid not in officer_cache:
            off = db.session.get(User, oid)
            officer_cache[oid] = off.name if off else "?"
        return officer_cache[oid]
    return jsonify([{
        "id":          r.id,
        "report_type": r.report_type or "",
        "report_date": r.report_date.isoformat() if r.report_date else "",
        "role_type":   r.role_type or "",
        "notes":       r.notes or "",
        "uploaded_by": r.uploaded_by,
        "officer_name": _off(r.uploaded_by),
        "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else "",
    } for r in items])

@app.post("/api/welfare/report/upload")
@api_welfare_required
def api_welfare_report_upload():
    u = get_api_user()
    if u.role not in ("welfare_officer", "environment_officer"):
        return jsonify({"error": "Forbidden"}), 403
    pdf_file  = freq.files.get("file")
    report_type = (freq.form.get("report_type") or "").strip()
    report_date_str = (freq.form.get("report_date") or "").strip()
    notes       = (freq.form.get("notes") or "").strip()
    role_type   = "welfare" if u.role == "welfare_officer" else "environment"
    if not pdf_file or not pdf_file.filename:
        return jsonify({"error": "No file"}), 400
    import os as _os, uuid as _uuid
    ext = _os.path.splitext(pdf_file.filename)[1].lower()
    if ext not in (".pdf",):
        return jsonify({"error": "Only PDF files accepted"}), 400
    try:
        rdate = datetime.strptime(report_date_str, "%Y-%m-%d").date() if report_date_str else datetime.now(RIYADH_TZ).date()
    except ValueError:
        rdate = datetime.now(RIYADH_TZ).date()
    co_id = u.company_id or 0
    save_dir = _os.path.join(app.root_path, "uploads", "reports", str(co_id))
    _os.makedirs(save_dir, exist_ok=True)
    fname = f"{_uuid.uuid4().hex}_{pdf_file.filename}"
    pdf_file.save(_os.path.join(save_dir, fname))
    file_path = f"{co_id}/{fname}"
    rf = ReportFile(
        company_id=u.company_id, uploaded_by=u.id,
        role_type=role_type, report_type=report_type,
        file_path=file_path, report_date=rdate, notes=notes
    )
    db.session.add(rf)
    db.session.commit()
    return jsonify({"id": rf.id})

@app.get("/api/welfare/report/<int:rid>")
@api_welfare_required
def api_welfare_report_view(rid):
    u = get_api_user()
    if u.role in ("welfare_supervisor", "admin", "super_admin"):
        rf = ReportFile.query.filter_by(id=rid)
        if u.company_id:
            rf = rf.filter_by(company_id=u.company_id)
        rf = rf.first_or_404()
    else:
        rf = ReportFile.query.filter_by(id=rid, uploaded_by=u.id).first_or_404()
    import os as _os
    path = _os.path.join(app.root_path, "uploads", "reports", rf.file_path)
    if not _os.path.exists(path):
        return jsonify({"error": "File not found"}), 404
    return send_file(path, mimetype="application/pdf",
                     as_attachment=False, download_name=f"report_{rid}.pdf")

@app.delete("/api/welfare/report/<int:rid>")
@api_welfare_required
def api_welfare_report_delete(rid):
    u = get_api_user()
    if u.role not in ("welfare_supervisor", "admin", "super_admin"):
        return jsonify({"error": "Forbidden"}), 403
    rf = ReportFile.query.filter_by(id=rid)
    if u.company_id:
        rf = rf.filter_by(company_id=u.company_id)
    rf = rf.first_or_404()
    import os as _os
    path = _os.path.join(app.root_path, "uploads", "reports", rf.file_path)
    try:
        if _os.path.exists(path):
            _os.remove(path)
    except Exception:
        pass
    db.session.delete(rf)
    db.session.commit()
    return jsonify({"ok": True})

@app.get("/api/welfare/supervisor/all")
@api_welfare_required
def api_welfare_supervisor_all():
    u = get_api_user()
    if u.role not in ("welfare_supervisor", "admin", "super_admin"):
        return jsonify({"error": "Forbidden"}), 403
    role_filter = freq.args.get("role", "")
    date_from   = freq.args.get("from", "")
    date_to     = freq.args.get("to", "")
    officers = User.query.filter(
        User.role.in_(["welfare_officer", "environment_officer"]),
        User.is_active == True
    )
    if u.company_id:
        officers = officers.filter_by(company_id=u.company_id)
    if role_filter in ("welfare", "environment"):
        role_map = {"welfare": "welfare_officer", "environment": "environment_officer"}
        officers = officers.filter_by(role=role_map[role_filter])
    officer_list = officers.all()
    oids = [o.id for o in officer_list]
    cache = {o.id: o.name for o in officer_list}
    obs_q = HseObservation.query.filter(HseObservation.officer_id.in_(oids))
    if date_from:
        try: obs_q = obs_q.filter(HseObservation.date >= datetime.strptime(date_from, "%Y-%m-%d").date())
        except ValueError: pass
    if date_to:
        try: obs_q = obs_q.filter(HseObservation.date <= datetime.strptime(date_to, "%Y-%m-%d").date())
        except ValueError: pass
    obs_list = obs_q.order_by(HseObservation.date.desc()).limit(100).all()
    rpt_q = ReportFile.query.filter(ReportFile.uploaded_by.in_(oids))
    if u.company_id:
        rpt_q = rpt_q.filter_by(company_id=u.company_id)
    if role_filter in ("welfare", "environment"):
        rpt_q = rpt_q.filter_by(role_type=role_filter)
    if date_from:
        try: rpt_q = rpt_q.filter(ReportFile.report_date >= datetime.strptime(date_from, "%Y-%m-%d").date())
        except ValueError: pass
    if date_to:
        try: rpt_q = rpt_q.filter(ReportFile.report_date <= datetime.strptime(date_to, "%Y-%m-%d").date())
        except ValueError: pass
    rpt_list = rpt_q.order_by(ReportFile.report_date.desc()).limit(100).all()
    def _off(oid): return cache.get(oid, "?")
    return jsonify({
        "observations": [_welfare_obs_to_dict(o, cache) for o in obs_list],
        "reports": [{
            "id":          r.id,
            "report_type": r.report_type or "",
            "report_date": r.report_date.isoformat() if r.report_date else "",
            "role_type":   r.role_type or "",
            "notes":       r.notes or "",
            "officer_name": _off(r.uploaded_by),
            "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else "",
        } for r in rpt_list],
    })


# ===================== Welfare & Wellbeing Module =====================
# Self-contained. Does not modify or read any HSE table.
# All tables prefixed wlf_ ; all routes prefixed /welfare/ ; role: safety_welfare
# ----------------------------------------------------------------------

import json as _wlf_json

WLF_LOCATIONS = [
    {"code": "340",       "label": "Unit 340",           "type": "unit"},
    {"code": "350",       "label": "Unit 350",           "type": "unit"},
    {"code": "360",       "label": "Unit 360",           "type": "unit"},
    {"code": "370",       "label": "Unit 370",           "type": "unit"},
    {"code": "380",       "label": "Unit 380",           "type": "unit"},
    {"code": "422",       "label": "Unit 422",           "type": "unit"},
    {"code": "TCF",       "label": "TCF",                "type": "tcf"},
    {"code": "MESS-SITE", "label": "Mess Hall - Site",   "type": "mess"},
    {"code": "MESS-TCF",  "label": "Mess Hall - TCF",    "type": "mess"},
    {"code": "CLINIC",    "label": "Clinic / First Aid", "type": "clinic"},
    {"code": "BUS",       "label": "Bus Pickup Points",  "type": "transport"},
    {"code": "PRAYER",    "label": "Prayer Areas",       "type": "prayer"},
]
WLF_LOC_BY_CODE = {l["code"]: l for l in WLF_LOCATIONS}
WLF_UNITS = [l["code"] for l in WLF_LOCATIONS if l["type"] == "unit"]

WLF_SEVERITY = ["Critical", "High", "Medium", "Low"]
WLF_LOCKED_SEVERITY = ("Critical", "High")   # trainee may not close these

WLF_FLAGS = [("green", "🟢 Green"), ("yellow", "🟡 Yellow"),
             ("orange", "🟠 Orange"), ("red", "🔴 Red")]

WLF_ITEMS = [
    {"key": "water",   "title": "M1: Drinking Water & Rest Areas",  "kind": "stations"},
    {"key": "shelter", "title": "M1b: Shaded Rest Areas",           "kind": "shelters"},
    {"key": "camp",    "title": "M2: Camp Management",              "kind": "simple"},
    {"key": "heat",    "title": "M3: Heat Stress Management",       "kind": "heat"},
    {"key": "medical", "title": "M4: Medical Welfare",              "kind": "simple"},
    {"key": "docs",    "title": "M5: Documentation",                "kind": "simple"},
]

# Seasonal item appended to WLF_ITEMS dynamically by _wlf_items()
WLF_SEASONAL_ITEM = {
    "summer": {"key": "summer", "title": "Summer / Heat Readiness", "kind": "simple"},
    "winter": {"key": "winter", "title": "Winter / Rain Readiness", "kind": "simple"},
}


def _wlf_season():
    """Returns 'summer' or 'winter' from WlfSetting, defaults to 'summer'."""
    try:
        _c = cid()
        q = WlfSetting.query.filter_by(key="season")
        if _c:
            q = q.filter(WlfSetting.company_id == _c)
        s = q.first()
        if s and s.value in ("summer", "winter"):
            return s.value
    except Exception:
        pass
    return "summer"


def _wlf_items():
    """Core WLF_ITEMS + current seasonal item."""
    season = _wlf_season()
    return WLF_ITEMS + [WLF_SEASONAL_ITEM[season]]

# 15-level development path — one level per training chapter (5 modules, CSM Aug 2026)
WLF_LEVELS = [
    # ═══ Module 1: Sanitation, Water & Rest Areas ═══
    {"code": "L1",  "phase": 1, "module": "M1", "chapter": "1.1",
     "title": "Toilets & Washing Facilities",
     "ref": "CSM 11.4.D + SAEHC-S-07",
     "what": "Field: Visit every toilet block and washing facility at camp and site. For each facility: confirm soap is available at every sink, paper is stocked, ventilation is working (no odour build-up), water flows from every tap and flush, and any fault is noted (broken lock, cracked bowl, blocked drain).\nDesk: Enter the inspection for each facility block — location, soap (Y/N), paper (Y/N), ventilation (pass/fail), water (pass/fail), faults found — and raise a Finding for each deficiency.\nAnalysis: Compare facility status over the last 4 inspections. Any block failing soap or paper every time? That is a supply chain problem — escalate to camp management with a frequency note.",
     "why": "Sanitation directly prevents communicable disease on site. CSM 11.4 mandates maintained, stocked, ventilated facilities as a minimum standard — not a target.",
     "how": "1. List all toilet blocks and washing stations at your site and camp.\n2. For each block: enter and check every cubicle and sink.\n3. At each sink: soap dispenser full? Paper towel or tissue available?\n4. Ventilation: is there airflow? Noticeable odour? Fan working?\n5. Water: flush each toilet — does it drain fully? Run each tap — consistent flow?\n6. Check doors and locks: does every cubicle close properly?\n7. Note any fault (broken lock, blocked drain, cracked fixture). Photograph.\n——— At your desk ———\n8. Enter for each facility block: soap, paper, ventilation, water, fault list.\n9. Raise a Finding for every missing supply or working fault. Severity: High for no water/drain blockage, Medium for supply gaps.\n——— Trend check ———\n10. Compare to last 4 rounds. Any block consistently failing? Write escalation note: Block B: soap missing in 3 of 4 inspections — recommend direct supply contract.",
     "right": "Field: 4 blocks inspected. Blocks A, C, D: all items present ✓. Block B: no soap at sinks 2 and 3, vent fan off → Finding F-011 raised (soap, severity Medium) and F-012 (fan, severity High).\nDesk: All 4 blocks logged same day; F-011 and F-012 assigned to camp facilities supervisor.\nAnalysis: Block B soap missing in 3 of 4 rounds — escalation note added to weekly report.",
     "wrong": "The toilets are generally fine.",
     "tasks": [{"k": "rounds_submitted", "label": "Sanitation facility inspections logged", "target": 3}]},

    {"code": "L2",  "phase": 1, "module": "M1", "chapter": "1.2",
     "title": "Drinking Water Stations",
     "ref": "CSM 11.4.G + SAEHC-01 + GI 151.006",
     "what": "Field: Count dispensers per unit, pace distance from furthest active worker (must be ≤100 m), test chlorine with kit (0.5–3.0 ppm), photograph any violation.\nDesk: Enter each station into the system — location ID, distance (m), chlorine (ppm), pass/fail — and raise a Finding for every non-compliant station.\nAnalysis: Review the week's chlorine log. Flag any station showing repeated out-of-range readings — these need root-cause action, not repeat findings.",
     "why": "Shared cups are banned. Chlorine outside 0.5–3.0 ppm renders water non-potable under SAEHC. Distance >100 m means workers skip drinking — a direct heat stress risk.",
     "how": "1. Walk all active work faces and identify every water dispenser.\n2. For each station: pace the distance from the furthest worker in that unit.\n3. Test chlorine with the kit — record the exact ppm reading.\n4. Photograph the dispenser label and any visible contamination or missing seal.\n——— At your desk ———\n5. Open the system and enter each station: unit, location code, distance (m), chlorine (ppm), pass/fail.\n6. For any fail: raise a Finding — attach the photo, set severity, assign responsible person.\n——— End of week ———\n7. Open the week's chlorine records. List daily readings per station.\n8. Any station with ≥2 low readings this week: write one line in your weekly notes — \"Station WS-07 failed 3 of 5 days — recommend maintenance team check.",
     "right": "Field: WS-07 sealed, 85 m, chlorine 1.2 ppm ✓ — WS-12 open container, 118 m → Finding F-042 raised with photo.\nDesk: Both stations entered same day; F-042 assigned to catering supervisor, due today.\nAnalysis: WS-12 failed distance in 3 of 4 rounds this week — weekly note added: recommend relocating dispenser.",
     "wrong": "Checked the water stations — they look fine.",
     "tasks": [{"k": "rounds_submitted", "label": "Water inspection rounds submitted", "target": 3}]},

    {"code": "L3",  "phase": 1, "module": "M1", "chapter": "1.3",
     "title": "Shaded Rest Areas",
     "ref": "CSM 11.4.H + CSM I-13 §13.4.1.A",
     "what": "Field: Walk each work unit and pace the distance from the furthest active worker to the nearest shade structure. In Cat III/IV heat: verify cooling (A/C or fan) and water available inside the shelter.\nDesk: Log each shelter — location, distance (m), temperature inside (°C), compliance status — and raise a Finding for any >100 m gap.\nAnalysis: If the same unit fails the distance rule in 2+ consecutive rounds, the issue is structural (not enough shelters) — escalate to supervisor, not just another finding.",
     "why": "Shade is a mandatory requirement, not an amenity. Cat III/IV requires water and cooling within the shade structure — shade alone is not sufficient at those temperatures.",
     "how": "1. Identify active worker groups per unit.\n2. For the furthest-positioned worker: pace to the nearest shade structure. Record distance in metres.\n3. If Cat III or IV: step inside — confirm A/C or fan is working and water bottles are present.\n4. Photograph any shelter where distance >100 m or Cat III/IV shelter lacks cooling.\n——— At your desk ———\n5. Enter each shelter: unit, shelter ID, distance (m), temp inside (°C), cooling status, water present.\n6. Raise a Finding for every shelter where distance >100 m or Cat III/IV cooling/water missing.\n——— Weekly analysis ———\n7. List units that had shade violations in any round this week.\n8. If 2+ violations from same unit: add escalation note — \"Structural gap at Unit 340 north end — recommend additional shelter, not a disciplinary finding.",
     "right": "Field: Unit 3000 — 3 shelters, furthest worker 78 m ✓. Unit 340 — nearest shelter 112 m, 4 workers exposed → Finding F-055 raised with photo.\nDesk: Both shelters logged; F-055 assigned to project supervisor, category: infrastructure gap.\nAnalysis: Unit 340 failed shade distance in 3 of 4 rounds — escalation note written for weekly report.",
     "wrong": "There are shelters in the area.",
     "tasks": [{"k": "rounds_submitted", "label": "Shade inspection rounds submitted", "target": 3}]},

    # ═══ Module 2: Camp & Accommodation Management ═══
    {"code": "L4",  "phase": 1, "module": "M2", "chapter": "2.1",
     "title": "Accommodation Standards",
     "ref": "CSM 11.2-11.3 + SAES-M-100 + SAEHC-S-07",
     "what": "Field: Choose 5 rooms at random (different buildings if possible). For each: count occupants, estimate floor area, test A/C operation, check every cabinet and under beds for cooking appliances, confirm emergency exit is unblocked.\nDesk: Enter each room inspection — building, room #, occupant count, A/C status, violations found — and raise a Finding for each violation with photo attached.\nAnalysis: Review the last 4 inspection records. Any room appearing twice with the same issue? Escalate persistent cases to camp management directly.",
     "why": "Cooking inside rooms is a fire and CO poisoning risk — strictly prohibited. A/C is mandatory 24/7 in summer. Overcrowding (below 4.6 m² per person) violates SAEHC-S-07.",
     "how": "1. Get the room list from the camp register or select at random.\n2. For each of 5 rooms: count occupants, estimate area from floor plan or rough measure.\n3. Turn A/C on if off — confirm airflow within 2 minutes.\n4. Open every shelf and check under beds for cookers, kettles, rice cookers.\n5. Confirm the emergency exit door opens freely — no padlock, no blocked path.\n6. Photograph any violation before leaving the room.\n——— At your desk ———\n7. Enter each room: building, room #, occupant count, A/C result, appliances found, exit status.\n8. Raise a Finding for every violation (cooking appliance or blocked exit = severity High).\n——— Historical review ———\n9. Open the last 4 room inspection entries. Flag any room appearing in 2+ entries with the same issue.\n10. If same room keeps failing: write escalation note to camp management — do not just open another finding.",
     "right": "Field: 5 rooms inspected — Room 14 (4 occupants, 22 m², A/C ok, no appliances, exit clear ✓). Room 22: rice cooker found → confiscated, Finding F-031 raised, severity High.\nDesk: All 5 rooms logged same day; F-031 photo attached, assigned to camp supervisor.\nAnalysis: Room 22 appeared in 3 consecutive inspection logs — escalation note written to camp management.",
     "wrong": "The rooms look clean.",
     "tasks": [{"k": "rounds_submitted", "label": "Accommodation inspections logged", "target": 2}]},

    {"code": "L5",  "phase": 1, "module": "M2", "chapter": "2.2",
     "title": "Food & Nutrition",
     "ref": "CSM 11.2 E,F,G,H + SAEHC Section 07",
     "what": "Field: Arrive at the kitchen during or just before meal service. Confirm 3 meals per day are scheduled and served. Measure refrigeration temperature (must be 2–4°C), inspect food surfaces and utensils, confirm no cooking appliance vendors inside camp.\nDesk: Enter inspection result — meal schedule (Y/N), fridge temps (°C), surface cleanliness score (1–5), violations — and raise a Finding for any critical failure.\nAnalysis: Plot fridge temperatures across the week. A temperature drifting from 2°C to 6°C over 5 days is equipment failure — flag as a maintenance trend, not a new finding.",
     "why": "3 balanced meals are mandatory. Refrigeration >4°C puts food safety at risk under SAEHC-07. No cooking appliances or vendors inside camp — fire hazard and prohibited sale.",
     "how": "1. Check the kitchen meal schedule board: breakfast, lunch, dinner — all three posted?\n2. Open each refrigerator and read the internal thermometer. Record exact temperature in °C.\n3. Inspect prep surfaces and utensils — raw meat cross-contamination risk? Stains?\n4. Walk camp common areas: any mobile vendors selling cooking appliances?\n5. Photograph the fridge thermometer display and any violation.\n——— At your desk ———\n6. Enter: date, meal count (1/2/3), fridge 1 temp, fridge 2 temp, cleanliness score (1–5), violations.\n7. Raise a Finding for temp >4°C, fewer than 3 meals, or uncleaned surfaces.\n——— Weekly trend ———\n8. List fridge temperature readings from all rounds this week.\n9. If temps rising day-by-day (e.g. 2.5 → 3.4 → 4.2°C): equipment is failing — write maintenance request note in weekly report, not just a finding.",
     "right": "Field: Kitchen inspection 08:45 — 3 meals on board ✓. Fridge 1: 2.8°C ✓, Fridge 2: 4.6°C ✗ → Finding F-018 raised. Surfaces clean.\nDesk: Inspection logged; F-018 assigned to catering company, priority High.\nAnalysis: Fridge 2 above 4°C in 3 of 4 rounds this week — maintenance request added to weekly report.",
     "wrong": "The kitchen is running.",
     "tasks": [{"k": "rounds_submitted", "label": "Kitchen inspections logged", "target": 2}]},

    {"code": "L6",  "phase": 1, "module": "M2", "chapter": "2.3",
     "title": "General Hygiene & Pest Control",
     "ref": "SAEHC-S-07 + SAES-M-100",
     "what": "Field: Walk all camp corridors, entrances, bin areas, and outdoor spaces. Count covered bins vs. uncovered/overflowing, note any pest signs (droppings, entry holes, insects), confirm the maintenance contact board is posted with a working number.\nDesk: Enter the hygiene inspection — bin count, pest activity level (none/low/medium/high), maintenance board status — and raise a Finding if overflow is widespread or pest activity is medium/high.\nAnalysis: Compare pest activity levels across the last 4 rounds. An increase from none → low → medium over consecutive rounds is a pest control program failure — escalate with specific recommendation.",
     "why": "24-hour maintenance is mandatory. Periodic pest control is required by SAEHC. Waste accumulation breeds pests and creates health hazards for all camp residents.",
     "how": "1. Walk every corridor of every building, checking for waste spillage and uncovered bins.\n2. Count: how many bins are covered and sealed? How many overflowing?\n3. Look for pest signs: droppings, gaps in walls, flying insects near bins.\n4. Find the maintenance contact board — is it posted and current?\n5. Photograph any overflowing bin or pest evidence.\n——— At your desk ———\n6. Enter: date, covered bins (count), uncovered/overflowing (count), pest level (none/low/medium/high), board status.\n7. Raise a Finding if: ≥3 overflowing bins, pest level medium or above, or maintenance board missing.\n——— Weekly trend ———\n8. List pest levels from all rounds this week.\n9. If trend escalates (none → low → medium): pest control schedule is failing — note in weekly report with recommendation: \"Pest control contractor should be on-site this week.",
     "right": "Field: 2 buildings checked, 12 bins — 10 covered ✓, 2 overflowing at building B entrance → Finding F-027 raised. No pest signs. Maintenance board posted with 0555-1234.\nDesk: Logged same day; F-027 assigned to facility supervisor.\nAnalysis: Pest level none for 4 consecutive rounds — no escalation. Bin overflow at building B for 2nd week — structural fix recommended in weekly report.",
     "wrong": "The camp looks clean.",
     "tasks": [{"k": "rounds_submitted", "label": "Hygiene inspection rounds completed", "target": 3}]},

    # ═══ Module 3: Heat Stress Management ═══
    {"code": "L7",  "phase": 2, "module": "M3", "chapter": "3.1",
     "title": "Heat Index System",
     "ref": "CSM I-13 Appendix A (Aug 2026)",
     "what": "Field: Use calibrated equipment to measure temperature (°C) and humidity (%RH) at start of shift, midday, and whenever conditions change. Calculate Heat Index and determine the category (I–IV) and its required action.\nDesk: Enter each reading into the system — date, time, temperature, humidity, Heat Index value, category, and action required.\nAnalysis: Review the day's readings as a curve. When does the site hit Cat III? How long does it stay in Cat IV? Share the daily profile with your supervisor weekly — it shows whether the midday ban alone is sufficient.",
     "why": "Cat I (25-29): normal | Cat II (30-38): 50:10 rest ratio | Cat III (39-51): 30:10 | Cat IV (>52): 20:10 + buddy system. The category changes the legal obligation for every supervisor on site.",
     "how": "1. Take your first reading at start of shift (07:00–08:00).\n2. Record: exact time, temperature (°C), humidity (%RH).\n3. Calculate Heat Index using the formula or the chart.\n4. Determine category: I (HI 25–29), II (30–38), III (39–51), IV (>52).\n5. Note the required action for that category.\n6. Repeat at midday (12:00) and at 14:00 — and any time you notice a significant change.\n——— At your desk ———\n7. Enter each reading: time, temp, humidity, HI value, category.\n8. If category changed during the shift: log each change as a separate entry.\n——— Weekly analysis ———\n9. List all readings from the week. What was the peak category each day?\n10. If Cat IV appeared 3+ days: include in weekly report — \"Peak heat stress reached daily. Recommend reviewing outdoor task scheduling.",
     "right": "Field: 07:30 — 38°C, 58% → HI 44.1 → Cat III (30:10). 12:00 — 43°C, 65% → HI 54.2 → Cat IV (20:10 + buddy system).\nDesk: Both readings entered same day with categories and required actions noted.\nAnalysis: Cat IV reached 4 of 5 days this week — noted in weekly report with recommendation to limit non-essential outdoor work after 11:00.",
     "wrong": "It's hot today.",
     "tasks": [{"k": "rounds_measured", "label": "Heat Index readings logged", "target": 5}]},

    {"code": "L8",  "phase": 2, "module": "M3", "chapter": "3.2",
     "title": "Work/Rest Schedules & Water",
     "ref": "CSM I-13 Appendix A + §13.4.2",
     "what": "Field: Based on today's Heat Index category, physically visit 4+ active work areas. Observe whether supervisors stop work at the correct time. Check that water (1 cup per 15–20 min) is physically present at each work face. In Cat IV: confirm no one is working alone.\nDesk: Log each site observation — site ID, category, rest time compliant (Y/N), delay in minutes, water present, buddy system (Cat IV). Raise a Finding for every violation.\nAnalysis: Which supervisor or area has the most violations this week? A pattern (same area, 3+ times) needs a direct conversation with the foreman, not another finding.",
     "why": "Category IV: Buddy System mandatory, 2 litres per worker within reach, no solo work — these are not recommendations, they are Aramco requirements with no exceptions.",
     "how": "1. Check today's Heat Index category before going to the field.\n2. Visit at least 4 active work areas.\n3. At each area: note the time. Wait until the scheduled rest period — does the supervisor stop workers on time?\n4. Check: is there water available within the work area itself — not just at the shelter?\n5. In Cat IV: confirm workers are paired — no one working alone.\n6. Record: site ID, category, actual rest time vs. scheduled, delay (min), water present (Y/N), buddy check.\n7. Photograph any water absence or workers still active during rest.\n——— At your desk ———\n8. Enter all site observations in the system.\n9. Raise a Finding for: rest delay >5 min, no water at work face, or solo work in Cat IV.\n——— Weekly review ———\n10. List violations by site and supervisor. If one appears 3+ times: flag for supervisor to address directly with foreman.",
     "right": "Field: Cat III (HI 42) — 5 sites visited. Sites 340/350/360: 30:10 respected ✓. Site 370: rest 7 min late → Finding F-044. Site 380: no water at work face → Finding F-045.\nDesk: All 5 sites logged; F-044 and F-045 raised and assigned.\nAnalysis: Site 370 had late rest in 3 of 4 rounds — flagged for supervisor to address directly with foreman.",
     "wrong": "Workers are resting.",
     "tasks": [{"k": "rounds_submitted", "label": "Rest schedule verification rounds", "target": 4}]},

    {"code": "L9",  "phase": 2, "module": "M3", "chapter": "3.3",
     "title": "Midday Work Ban",
     "ref": "CSM I-13 §13.6 + Saudi Labour Law",
     "what": "Field: Between 12:00 and 15:00 (active period: June 15–Sep 15), walk every outdoor work area. Record time arrived, location, any workers found outdoors, headcount, and action taken. If you find anyone working: stop it immediately and raise a Finding before leaving that location.\nDesk: Log your midday tour — start time, end time, all areas covered, any violations found.\nAnalysis: Review the week's midday tours. Any contractor appearing more than once? That is a compliance culture problem — include the contractor name in weekly report.",
     "why": "The ban is mandatory under Saudi Labour Law and Aramco standards. No exceptions exist for schedule pressure or supervisor approval. Violation = immediate finding, severity High.",
     "how": "1. Start your midday tour between 12:00 and 12:30. Note start time.\n2. Walk every outdoor work area on your route — do not skip any.\n3. If workers found outdoors: stop them immediately. Record: location, number of workers, contractor, supervisor present.\n4. Photograph workers if actively working.\n5. Note end time when you finish the tour.\n——— At your desk ———\n6. Enter midday tour record: start time, end time, areas covered (list all), violations (count + description).\n7. If any violation: raise a Finding immediately — attach photo, severity High, assign to project superintendent.\n——— Weekly pattern ———\n8. List any violations from the week. Which contractor appeared?\n9. If same contractor violated 2+ times: name them in weekly report with escalation recommendation.",
     "right": "Field: 12:15–13:40 — 8 work areas inspected. Areas 1–7 clear ✓. Area 8 (Block 340): 4 workers found outdoors, contractor TCF → stopped immediately, supervisor notified, Finding F-061 raised with photo.\nDesk: Full tour logged; F-061 submitted with photo, severity High.\nAnalysis: TCF contractor violated midday ban for 2nd week — named in weekly report with escalation recommendation.",
     "wrong": "I didn't see anyone.",
     "tasks": [{"k": "rounds_submitted", "label": "Midday ban tours logged", "target": 5}]},

    {"code": "L10", "phase": 2, "module": "M3", "chapter": "3.4",
     "title": "Acclimatization",
     "ref": "CSM I-13 §13.4.2.B + §13.2.1.F",
     "what": "Field: At the start of each shift, get the new-arrival list from each unit supervisor. For each new worker or worker returning from leave: confirm which acclimatization day they are on and verify that their workload matches the allowed percentage (Day 1: 20%, Day 2: 40%, Day 3: 60%, Day 4: 80%, Day 5: 100%).\nDesk: Enter the acclimatization log — worker name, arrival date, day #, target %, workload assessment. Raise a Finding if any new worker is at full load before Day 5.\nAnalysis: At week end, review who cleared acclimatization this week and flag any worker with no status update to the supervisor.",
     "why": "Day 1: 20% workload, Day 2: 40%… Day 5: 100%. Workers returning from leave restart at Day 1 — prior experience does not override this. Skipping the ramp is a leading cause of heat illness in new arrivals.",
     "how": "1. At start of shift, get the new-arrivals list from each unit supervisor.\n2. For each new worker: confirm arrival date and calculate today's acclimatization day.\n3. Ask the supervisor: what is this worker doing today? Assess whether work intensity matches the allowed daily percentage.\n4. For workers returning from leave: confirm they restart at Day 1 regardless of experience.\n5. Record: name, arrival date, acclimatization day #, target %, confirmed assignment.\n——— At your desk ———\n6. Enter all acclimatization records for the day.\n7. Raise a Finding for any worker confirmed at full load before Day 5.\n——— End-of-week ———\n8. List all workers who started acclimatization this week. Who cleared Day 5?\n9. Flag any worker still on the list with no update — follow up with supervisor.",
     "right": "Field: 3 new workers — Hamad (Day 2 → 40% ✓), Mahmoud (Day 1 → 20% ✓), Rida returned from leave → restarted Day 1 ✓.\nDesk: All 3 logged; day/% confirmed with supervisor. No violations found.\nAnalysis: 5 workers cleared acclimatization this week. Worker Khalid (started Mon) on Day 3 — follow-up scheduled for Day 5 clearance.",
     "wrong": "No new workers.",
     "tasks": [{"k": "rounds_submitted", "label": "Acclimatization follow-ups logged", "target": 3}]},

    {"code": "L11", "phase": 2, "module": "M3", "chapter": "3.5",
     "title": "Heat Illness & First Aid",
     "ref": "CSM I-13 Appendix B",
     "what": "Field: On every round, watch for any worker showing unusual symptoms. Know the three types: heat cramps (muscle cramps, sweating, alert), heat exhaustion (heavy sweating, pale, weak), heat stroke (hot dry skin, no sweating, confused — 911 emergency). At every shelter: confirm the emergency protocol poster is displayed and legible.\nDesk: Log any suspected case (worker name, symptoms, location, action taken, outcome) and each shelter's poster status. Raise a Finding for any missing poster.\nAnalysis: Track heat illness frequency by week and by heat category. If cases are increasing, include the trend in your weekly report.",
     "why": "3 severity levels: heat cramps (rest + fluids) → heat exhaustion (medical attention) → heat stroke (911 + cool immediately). Misidentifying a heat stroke as exhaustion costs minutes that determine survival.",
     "how": "1. During every field visit: observe worker behaviour — anyone moving slowly, sitting alone, confused?\n2. Know the 3 types:\n   — Heat cramps: painful muscle spasms, still sweating → move to shade, rest, fluids\n   — Heat exhaustion: pale, weak, heavy sweating → lie down, cool, fluids, medical attention\n   — Heat stroke: hot dry skin, no sweating, confused → 911 immediately + cool by all means\n3. At each shelter: check if the emergency protocol poster is present and legible.\n4. If you observe a case: stay with the worker, initiate the correct response, call for help.\n——— At your desk ———\n5. Enter any case: date, time, worker name, location, type (cramps/exhaustion/stroke), action taken, outcome.\n6. Enter shelter poster check: location, poster present (Y/N).\n7. Raise a Finding for any missing or illegible poster.\n——— Weekly trend ———\n8. Count total cases this week vs. last week. Were those days Cat III or IV?\n9. If trend is upward: include in weekly report — \"3 heat exhaustion cases this week on Cat III/IV days — recommend hydration reminder at shift start.",
     "right": "Field: Worker at site 340 — pale, sitting alone, heavy sweating → heat exhaustion protocol initiated: moved to A/C area, fluids given, clinic contacted. 6 shelters checked — 1 poster missing (shelter 3) → Finding F-033.\nDesk: Case logged with full details; F-033 raised for missing poster.\nAnalysis: 2 cases this week vs. 0 last week — both on Cat III days. Noted in weekly report.",
     "wrong": "The worker is tired from the sun.",
     "tasks": [{"k": "findings_written", "label": "Heat illness observations documented", "target": 3}]},

    # ═══ Module 4: Medical Welfare ═══
    {"code": "L12", "phase": 3, "module": "M4", "chapter": "4.1",
     "title": "First Aid Kit Inspection",
     "ref": "CSM CSAR Section 9 (Medical Facilities)",
     "what": "Field: At each work unit's first aid station, open and inspect the kit. Check every item against the standard contents list, verify expiry dates, confirm the responsible person's name and phone number is posted, and confirm at least one HSFA-trained person is on-site.\nDesk: Log the full inspection for each unit — kit completeness (%), expired/missing items, responsible person name, HSFA count. Raise a Finding for any gap.\nAnalysis: Any unit where the kit has been incomplete in 2+ consecutive rounds? The replacement process is broken — escalate to the supply team, not just another finding.",
     "why": "Separate kits required every >300 m. HSFA-AED mandatory on site. A kit that passes the eye test but has expired epinephrine fails when it matters most.",
     "how": "1. Visit each unit's first aid station.\n2. Open the kit. Check every item against the standard contents list.\n3. Read every expiry date — remove and note any expired item.\n4. Read the responsible person board: name + phone number posted?\n5. Confirm at least one HSFA-trained person is currently on-site for this unit.\n6. Photograph kit interior.\n——— At your desk ———\n7. Enter for each unit: kit completeness (%), expired items (list), responsible person name and number, HSFA count on-site.\n8. Raise a Finding for: missing mandatory items, expired items, no responsible person posted, zero HSFA-trained on-site.\n——— Trend check ———\n9. Compare this inspection to the previous round for the same unit. Any item missing in both?\n10. If same item missing twice: supply problem — add to weekly report: \"Unit 340 kit missing tourniquet for 2nd consecutive round — procurement action needed.",
     "right": "Field: Unit 340 — all items present, no expired items, Ahmed (0509-XXXX) posted, 2 HSFA on-site ✓. Unit 360 — tourniquet missing, epinephrine expired → Finding F-029 raised with photo.\nDesk: Both units logged; F-029 assigned to supply supervisor, due today.\nAnalysis: Unit 360 tourniquet was missing last round too — procurement action added to weekly report.",
     "wrong": "The kit is there.",
     "tasks": [{"k": "rounds_submitted", "label": "First aid kit inspections logged", "target": 4}]},

    {"code": "L13", "phase": 3, "module": "M4", "chapter": "4.2",
     "title": "Emergency Numbers & Reporting",
     "ref": "CSM I-1 + I-2",
     "what": "Field: Walk to every shelter, unit gate, and muster point on your route. At each: confirm the 911 sign is present and legible. Ask one random worker per unit to verbally name the emergency number and nearest hospital.\nDesk: Log signage status per location and the outcome of each verbal test. Raise a Finding for any missing or damaged sign.\nAnalysis: Are the same locations repeatedly missing signs? This points to a maintenance/accountability gap — include location names in weekly report with a recommendation for permanent mounting.",
     "why": "When calling 911: exact location + nature of emergency + number of casualties + your name, number, badge. Workers who don't know the number or hospital route cost critical minutes in an emergency.",
     "how": "1. Walk to each shelter, unit gate, and muster point on your round.\n2. At each: look for the 911 emergency sign — present? Legible? Not torn, faded, or blocked?\n3. Select one worker at random per unit: ask verbally — \"What number do you call in an emergency?\" and \"Where is the nearest medical facility?\"\n4. Record: location, sign status (present/torn/missing), worker test result (pass/fail).\n5. Photograph any torn or missing sign.\n——— At your desk ———\n6. Enter each location: sign status, worker test result, comments.\n7. Raise a Finding for every missing or illegible sign.\n——— Weekly pattern ———\n8. List locations with sign issues this week.\n9. If any location appears in 2+ rounds: add to weekly report — \"Shelter 3 sign torn for 2 consecutive weeks — recommend permanent weatherproof sign.",
     "right": "Field: 6 locations checked — 5 have clear 911 signs ✓. Shelter 3 sign torn → Finding F-048 raised with photo. Worker verbal test: 4/5 passed; Unit 380 worker gave incorrect number — supervisor notified.\nDesk: All 6 locations logged; F-048 raised and assigned.\nAnalysis: Shelter 3 flagged for 2nd week — weekly report includes recommendation for permanent sign installation.",
     "wrong": "The numbers are known.",
     "tasks": [{"k": "findings_written", "label": "Emergency signage violations documented", "target": 2}]},

    # ═══ Module 5: Documentation ═══
    {"code": "L14", "phase": 4, "module": "M5", "chapter": "5.1",
     "title": "Full Daily Welfare Tour",
     "ref": "Aramco General Requirements + GI 151.006",
     "what": "Field: Conduct a structured tour of ≥3 hours covering all active units — camp and site. Complete all 7 checklist items (water, shade, heat index, work/rest, midday ban, first aid, acclimatization) and photograph every violation.\nDesk: Enter all 7 items in the system before end of shift. Every violation must have a Finding raised same day with a photo attached. Submit the tour record before leaving.\nAnalysis: Review your own weekly tour completion rate. Were all 7 items entered every day? Any item you consistently skip? That blind spot becomes a KPI gap your supervisor will see.",
     "why": "A complete tour = camp + site + all 7 items entered same day. Submitting 4 of 7 items counts as an incomplete tour in the KPI report — there is no partial credit.",
     "how": "1. Plan your route before starting — list all units you will cover.\n2. Begin with the Heat Index reading — this sets the work/rest requirement for the day.\n3. Item 1 — Water: dispenser count, distance, chlorine.\n4. Item 2 — Shade: distance check per unit.\n5. Item 3 — Heat Index: record reading if not already done.\n6. Item 4 — Work/rest: verify at 2+ active sites.\n7. Item 5 — Midday (if applicable): confirm no outdoor work 12:00–15:00.\n8. Item 6 — First aid: check 1 kit per unit.\n9. Item 7 — Acclimatization: new worker status.\n10. Record duration: start time to end time. Minimum 3 hours for a valid full tour.\n——— At your desk ———\n11. Enter all 7 items in the system with photos for every finding raised.\n12. Submit tour before end of shift.\n——— Weekly self-review ———\n13. Check your own submission record: how many tours submitted each day? Were all 7 items filled in each time?\n14. If any item consistently missing: identify why and correct the route.",
     "right": "Field: Tour 07:15–10:40 (205 min) — all 7 items completed across 6 units. 2 violations raised: F-055 (shade distance Unit 340) and F-056 (water station Unit 380).\nDesk: All 7 items entered, 2 findings submitted with photos, tour submitted by 11:00.\nAnalysis: Week review — 5/5 tours submitted, all 7 items complete each day. No gaps.",
     "wrong": "It was a quiet day, nothing to report.",
     "tasks": [{"k": "rounds_submitted", "label": "Full daily tours submitted", "target": 10},
               {"k": "findings_written", "label": "Violations documented with photos", "target": 5}]},

    {"code": "L15", "phase": 4, "module": "M5", "chapter": "5.2",
     "title": "Training & Monitoring Records",
     "ref": "CSM I-13 §13.5",
     "what": "Field: At the start of the week, collect the heat stress training attendance register. Cross-reference with the full worker roster: which workers have not been trained? Walk to their units and confirm the gap with the supervisor in person.\nDesk: Enter training attendance records, flag untrained workers by name, and complete the full weekly report — heat index summary, tour completion rate, findings opened/closed, complaints resolved, training coverage %.\nAnalysis: Calculate training coverage: (trained / total workers) × 100. Track week by week. If coverage is dropping, new workers are arriving faster than training is being delivered — recommend an extra session.",
     "why": "Mandatory documentation includes: training attendance lists, acclimatization register, incident log, and daily temperature readings. Gaps in any of these are audit findings under GI 151.006.",
     "how": "1. Retrieve the heat stress training attendance list from the safety team.\n2. Cross-reference with the full worker roster: who is missing?\n3. Walk to those workers' units — confirm with supervisor who has and has not been trained.\n4. Record untrained workers by name and notify the supervisor in writing.\n——— At your desk ———\n5. Enter training attendance: date, course name, attendee names.\n6. Flag untrained workers in the system.\n7. Complete the weekly report:\n   — Heat index summary (peak readings, days at Cat III/IV)\n   — Tour completion rate (X of 5 days, all 7 items)\n   — Findings: opened / closed this week\n   — Complaints: opened / resolved\n   — Acclimatization register summary\n   — Training coverage % (trained ÷ total × 100)\n8. Submit weekly report by end of Thursday.\n——— Trend ———\n9. Compare training coverage % to last week. If dropping: recommend adding a training session in the report.",
     "right": "Field: Attendance list reviewed — 3 workers not trained (Hamad, Ali, Juan). Supervisors of Units 340/360 notified in person.\nDesk: Training attendance entered for 28 workers; 3 untrained flagged. Weekly report submitted Thursday — coverage 88% (↓ from 95% due to 7 new arrivals). Extra session recommended.\nAnalysis: Coverage trend: 95% → 88% over 2 weeks — recommendation for extra training session included in weekly report.",
     "wrong": "Weekly report sent.",
     "tasks": [{"k": "weeks_reported", "label": "Weekly reports submitted", "target": 4},
               {"k": "complaints", "label": "Complaints documented and followed up", "target": 5}]},
]
WLF_LEVEL_BY_CODE = {l["code"]: l for l in WLF_LEVELS}
WLF_LEVEL_CODES = [l["code"] for l in WLF_LEVELS]


# ── Environment Officer: 17-level development path (6 modules, GI 430.001 / GI 2.401 / HAZCOM) ──
# Module order: EM4 (HAZCOM) → EM2 (Soil/Water) → EM3 (Dust) → EM1 (Waste) → EM5 (Sanitation) → EM6 (Docs)
ENV_LEVELS = [
    # ═══ Module 4: Hazardous Materials (HAZCOM) ═══
    {"code": "E10", "phase": 3, "module": "EM4", "chapter": "4.1",
     "title": "Identification & Labelling",
     "ref": "OSHA HAZCOM + GI 430.001 + Aramco Chemical Standard",
     "what": "Field: Select 5 chemical containers at random across the site. For each: is the original label present and legible? Does the label include product name, hazard pictogram, supplier name, and signal word (Danger/Warning)? Is the container in good condition — no rust, no leak, no illegible label?\nDesk: Log each container inspected — product name, location, label present (Y/N), pictogram (Y/N), signal word (Y/N), container condition (good/damaged). Raise a Finding for each container with a missing or illegible label.\nAnalysis: Which area has the most unlabelled containers? That area needs a HAZCOM refresher — recommend targeted toolbox talk.",
     "why": "Every chemical container must be identifiable by any worker, visitor, or emergency responder — not just the person who uses it. An unlabelled container is a HAZCOM violation and an emergency response hazard.",
     "how": "1. Walk chemical storage areas, maintenance bays, and painting/coating areas.\n2. Select 5 containers at random — different types, different locations.\n3. For each: find the label. Read it — product name? Hazard pictogram (skull, flame, exclamation)? Signal word (Danger/Warning)?\n4. Check container condition: rust? Dents? Leaking?\n5. Photograph any missing or damaged label.\n——— At your desk ———\n6. Enter each container: product, location, label OK (Y/N), pictogram (Y/N), signal word (Y/N), condition.\n7. Raise a Finding for: missing label, illegible label, or damaged container with unknown content.\n——— Pattern ———\n8. Which area had the most unlabelled containers this week? Targeted toolbox talk recommendation in weekly report.",
     "right": "Field: 5 containers — containers 1–4: labels present with hazard symbols ✓. Container 5 (painting area): label completely torn off, content unknown → Finding F-013 (High — unknown content).\nDesk: All 5 logged; F-013 assigned to site chemical supervisor.\nAnalysis: Painting area had 2 unlabelled containers this week — HAZCOM toolbox talk recommended for that area's crew.",
     "wrong": "Most of the containers have labels.",
     "tasks": [{"k": "rounds_submitted", "label": "Chemical container inspections", "target": 3}]},

    {"code": "E11", "phase": 3, "module": "EM4", "chapter": "4.2",
     "title": "SDS / CHB",
     "ref": "OSHA HAZCOM + Aramco Chemical Standard",
     "what": "Field: For 3 different chemical products in use today, locate the Safety Data Sheet (SDS). Is it physically available near the chemical (not just in the office)? Is it printed in colour? Is it accessible to the work crew using that chemical?\nDesk: Log each SDS check — product name, SDS found (Y/N), location (work area / office / not found), colour print (Y/N), workers aware of location (Y/N). Raise a Finding for any SDS not found at point of use.\nAnalysis: Which chemicals have the most SDS access gaps? Recommend a laminated SDS station be installed at the work area — a one-time fix beats repeated findings.",
     "why": "The SDS must be at the point of use — not filed in the site office. Emergency responders and first aiders need it within 30 seconds, not after a 5-minute walk.",
     "how": "1. Choose 3 chemicals currently in use on site (e.g., diesel, paint thinner, adhesive primer).\n2. Ask the crew using each chemical: \"Where is the safety data sheet for this product?\"\n3. Go to the location they point to — is it there? In colour? Readable?\n4. Time it: if finding the SDS takes more than 30 seconds from the work area — that's a failure.\n5. Photograph the SDS location (or the absence of it).\n——— At your desk ———\n6. Enter each chemical: product, SDS location found, colour (Y/N), time to locate (estimate), workers aware (Y/N).\n7. Raise a Finding for: SDS not at point of use, not in colour, or crew not aware of its location.\n——— Systemic fix ———\n8. If the same area keeps failing SDS availability: recommend a permanent laminated SDS board at that location in the weekly report.",
     "right": "Field: 3 chemicals checked — diesel SDS at pump station ✓. Paint thinner: SDS in site office only (not at painting area) → Finding F-014. Adhesive primer: SDS present, colour, workers aware ✓.\nDesk: All 3 logged; F-014 assigned to HSE supervisor.\nAnalysis: Painting area SDS gap appeared twice — recommendation for permanent SDS board at painting area added to weekly report.",
     "wrong": "There's an SDS file in the office.",
     "tasks": [{"k": "rounds_submitted", "label": "SDS availability checks", "target": 3}]},

    {"code": "E12", "phase": 3, "module": "EM4", "chapter": "4.3",
     "title": "Safe Storage & Compatibility",
     "ref": "OSHA HAZCOM + GI 430.001 + Aramco Chemical Standard",
     "what": "Field: Inspect the chemical storage area. Using the compatibility matrix: confirm oxidisers and flammables are separated, corrosives are not stored above eye level, incompatible pairs are not adjacent. Check that day-use quantities do not exceed a 1-day supply. Confirm the area is ventilated and locked.\nDesk: Log the storage area inspection — location, oxidiser/flammable separation (pass/fail), corrosive storage height (pass/fail), day-use quantity exceeded (Y/N), ventilation (pass/fail), access locked (Y/N). Raise a Finding for any failure.\nAnalysis: Any incompatibility issue appearing in 2+ inspections? The storage layout needs a permanent fix — include in monthly report with a layout recommendation.",
     "why": "Acetone (flammable) stored next to an oxidiser creates a fire risk that can self-ignite without a spark. Corrosives stored above eye level spill on the face during retrieval. These are not hypothetical — they are documented accident causes.",
     "how": "1. Enter the chemical storage area.\n2. Identify all chemicals present. Group them mentally: flammables, oxidisers, corrosives, others.\n3. Check separation: oxidisers (hydrogen peroxide, bleach) ← minimum 3m or barrier → flammables (acetone, thinner, diesel).\n4. Corrosives: are they stored at or below waist height? Not on top shelves?\n5. Estimate day-use quantity: is the quantity for one day's work or a week's supply?\n6. Ventilation: any noticeable chemical odour? Exhaust fan working?\n7. Access: door locked when unattended?\n8. Photograph any incompatible pair or excess quantity.\n——— At your desk ———\n9. Enter: separation pass/fail, corrosive height pass/fail, day-use (Y/N), ventilation pass/fail, locked (Y/N).\n10. Raise a Finding for any failure (incompatible pair = severity Critical).\n——— Long-term fix ———\n11. If same incompatibility found in 2+ rounds: include layout recommendation in monthly report.",
     "right": "Field: Chemical store inspected — flammables and oxidisers separated by 4m barrier ✓. Corrosives on waist-height shelf ✓. Acetone quantity: 25L for one day's painting — acceptable. Vent fan on ✓. Door locked ✓.\nDesk: Inspection logged, all pass.\nAnalysis: Storage area passing for 3 consecutive rounds — noting in monthly report as compliant.",
     "wrong": "Everything is in the chemical store.",
     "tasks": [{"k": "rounds_submitted", "label": "Chemical storage inspections", "target": 2}]},

    # ═══ Module 2: Soil & Water Protection ═══
    {"code": "E4",  "phase": 1, "module": "EM2", "chapter": "2.1",
     "title": "Excavation Water Management",
     "ref": "GI 2.401 + Aramco EMP",
     "what": "Field: Inspect all active excavations with dewatering pumps. Confirm discharge water is directed to an approved settling area — not directly to stormwater drains, soil, or any natural area. Check water colour (turbid = high sediment, abnormal colour = contamination).\nDesk: Log each excavation — location, pump active (Y/N), discharge destination, water colour (clear/turbid/abnormal), compliant (Y/N). Raise a Finding for any direct discharge to soil or stormwater or for abnormal colour.\nAnalysis: Any excavation repeatedly failing? If the same contractor keeps discharging to soil, the issue is behavioural — recommend contractor toolbox talk in weekly report.",
     "why": "Untreated dewatering discharge to natural areas or stormwater violates Aramco's GI 2.401. Turbid water causes sedimentation. Abnormal colour means chemical contamination — an immediate escalation trigger.",
     "how": "1. List all active excavations from the project map.\n2. At each: is a pump running? Where is the discharge going?\n3. Trace the discharge line to its end point — is it an approved settling area?\n4. Observe water colour at the discharge point: clear? turbid? unusual colour (blue, grey, oily)?\n5. Photograph the discharge point and water colour.\n——— At your desk ———\n6. Enter each excavation: location, pump, discharge point, water colour, compliant.\n7. Raise a Finding for: discharge to soil/drain, turbid water without settling, or abnormal colour (severity High).\n——— Weekly trend ———\n8. Which excavations failed this week? Same contractor? Note for toolbox talk recommendation.",
     "right": "Field: 4 active excavations. Ex-1 and Ex-2: discharge to approved settling pond, water clear ✓. Ex-3: discharge directed to open soil → Finding F-006 raised (High). Ex-4: water turbid → noted, settling check initiated.\nDesk: All 4 logged; F-006 assigned to site superintendent.\nAnalysis: Ex-3 contractor failed discharge management for 2nd week — contractor toolbox talk recommended in weekly report.",
     "wrong": "The pump is running.",
     "tasks": [{"k": "rounds_submitted", "label": "Excavation water discharge checks", "target": 3}]},

    {"code": "E5",  "phase": 1, "module": "EM2", "chapter": "2.2",
     "title": "Concrete Areas",
     "ref": "Aramco EMP + GI 2.401",
     "what": "Field: Inspect all concrete washout areas. Confirm a designated washout pit exists, is lined (plastic sheeting), is not overflowing, and that no concrete trucks are washing out directly on soil.\nDesk: Log each washout area — location, pit present (Y/N), lined (Y/N), overflow (Y/N), direct discharge to soil (Y/N). Raise a Finding for any missing pit, unlined pit, or direct discharge.\nAnalysis: If the same location keeps lacking a washout pit, the contractor has not complied with their environmental plan — escalate beyond a Finding.",
     "why": "Concrete washout water has a pH of 11–12 and kills soil biology. GI 2.401 prohibits direct discharge to soil or stormwater. The lining prevents seepage to groundwater.",
     "how": "1. Identify all areas where concrete is being mixed, poured, or transported.\n2. At each concrete truck unload point: is there a washout pit?\n3. Inspect the pit: plastic lining intact? Level below 75% full?\n4. Walk around the area: any concrete residue or milky water on the soil?\n5. Photograph any missing pit or direct discharge evidence.\n——— At your desk ———\n6. Enter each concrete area: location, pit present, lined, fill level (%), direct discharge evidence.\n7. Raise a Finding for: missing pit, damaged lining, overflow, or concrete residue on soil (all severity High).\n——— Trend ———\n8. Same contractor without a pit in 2+ rounds? Escalate to project superintendent — not just another Finding.",
     "right": "Field: 3 concrete areas. Areas 1–2: pits lined, at 40% and 55% ✓. Area 3: no washout pit, concrete residue on soil → Finding F-007 (High).\nDesk: All 3 areas logged; F-007 assigned to concrete contractor, due immediately.\nAnalysis: Area 3 (same contractor) has no pit in 2nd inspection — escalation added to weekly report.",
     "wrong": "The concrete is set.",
     "tasks": [{"k": "rounds_submitted", "label": "Concrete washout area inspections", "target": 3}]},

    {"code": "E6",  "phase": 2, "module": "EM2", "chapter": "2.3",
     "title": "Oil Spill Prevention",
     "ref": "GI 2.401 + Aramco EMP",
     "what": "Field: Inspect every maintenance area, fuel storage, and refuelling point. For each: drip trays or spill containment pans present and dry? Portable oil-water separators in place where required? No visible leaks under any equipment?\nDesk: Log each area — location, containment present (Y/N), separator in place (Y/N), leaks observed (Y/N), estimated volume if any. Raise a Finding for missing containment or any observed leak.\nAnalysis: Which equipment or area generates the most spill risk? Track across 4 rounds and include in monthly report — a pattern means the equipment needs maintenance, not just a finding.",
     "why": "Petroleum hydrocarbons contaminate soil and groundwater for years. Aramco GI 2.401 requires all oil-handling areas to have secondary containment. Prevention is far cheaper than remediation.",
     "how": "1. List all maintenance areas, fuel storage tanks, and refuelling stations.\n2. At each: look under equipment and around the base of storage — any sheen, wet patch, or staining?\n3. Check drip trays: are they in place? Dry? Not overflowing?\n4. Check fuel hose condition: cracking, drips at fittings?\n5. Photograph any oil on soil, wet drip tray, or staining.\n——— At your desk ———\n6. Enter each area: containment present, separator present, leak observed, estimated volume if spilled.\n7. Raise a Finding for: missing containment (High), any oil on soil (Critical — initiate spill response).\n——— Monthly pattern ———\n8. Which area or piece of equipment keeps showing leaks? Schedule maintenance recommendation for monthly report.",
     "right": "Field: 4 areas inspected. Fuel storage ✓. Maintenance bay: drip tray absent under hydraulic excavator → Finding F-008 (High). Generator: minor oil film on tray — tray not overflowing, recorded.\nDesk: All 4 areas logged; F-008 assigned to mechanical supervisor.\nAnalysis: Hydraulic excavator has had drip tray issues in 3 of 4 rounds — maintenance check recommended in monthly report.",
     "wrong": "No major spills.",
     "tasks": [{"k": "rounds_submitted", "label": "Oil spill prevention checks", "target": 3}]},

    {"code": "E7",  "phase": 2, "module": "EM2", "chapter": "2.4",
     "title": "Spill Response",
     "ref": "GI 2.401 + Aramco ERP",
     "what": "Field: Inspect spill response kits at all oil storage and refuelling areas. Confirm absorbent pads, booms, and a labelled disposal bag are present and unused (not depleted). If a spill is found: initiate response immediately — contain, absorb, photograph, report.\nDesk: Log kit inspection for each area — location, kit present (Y/N), contents complete (Y/N), sealed bag for used materials present. Raise a Finding for any incomplete kit. Log any actual spill with full details.\nAnalysis: Any kit being depleted repeatedly? Either spills are happening frequently (prevention issue) or staff are not restocking. Identify which and escalate appropriately.",
     "why": "Scenario: 2 m² oil patch under a crane. Step 1 — stop the source. Step 2 — deploy boom around perimeter. Step 3 — absorbent pads on the spill. Step 4 — photograph. Step 5 — report to EHS. Step 6 — collect contaminated material in labelled bag. Step 7 — dispose through licensed contractor.",
     "how": "1. Locate all spill response kits — fuel storage, maintenance bay, generator areas.\n2. Open each kit: absorbent pads (count), boom (present?), labelled disposal bag (sealed and ready?).\n3. Are items still serviceable — not wet, degraded, or already used?\n4. Photograph kit contents.\n5. If you find an active spill: contain first (boom or sand berm), then absorb, then document.\n——— At your desk ———\n6. Enter each kit: location, items present, condition, ready (Y/N).\n7. Raise a Finding for: incomplete kit or depleted items.\n8. If actual spill occurred: log the full incident — location, estimated volume, material, steps taken, photos, disposal method.\n——— Trend ———\n9. Same kit depleted 2+ times? Are spills happening there? Or is the kit not being restocked after drill use? Identify and escalate.",
     "right": "Field: 3 kits checked — kits A and B complete and sealed ✓. Kit C: pads depleted, no disposal bag → Finding F-009. During round: small hydraulic oil spill (0.5L) at Unit 360 → contained with 2 pads, photographed, reported to EHS, contaminated pad placed in labelled bag.\nDesk: All 3 kits logged; spill incident logged with full details, photos, and disposal record.\nAnalysis: Kit C depleted in 2nd round — usage pattern suggests micro-spills at that location; preventive maintenance check recommended.",
     "wrong": "I'll deal with it if something happens.",
     "tasks": [{"k": "rounds_submitted", "label": "Spill kit inspections and any response logs", "target": 3}]},

    # ═══ Module 3: Dust Control & Public Protection ═══
    {"code": "E8",  "phase": 2, "module": "EM3", "chapter": "3.1",
     "title": "Dust Control Program",
     "ref": "Aramco EMP + GI 2.401 §Dust",
     "what": "Field: Walk active earthworks and haul roads. Is water spraying happening at the required frequency? Are all dump trucks carrying uncovered loads? Check speed limits are being followed at haul roads (>10 km/h causes dust). Observe from a distance — is a visible dust plume leaving the site perimeter?\nDesk: Log each dust observation — location, spraying active (Y/N), trucks covered (Y/N), visible dust plume (Y/N), wind direction. Raise a Finding for any uncovered load, no spraying during earthworks, or dust reaching site perimeter.\nAnalysis: Which areas generate the most dust? Is the problem equipment-related (no water truck) or behavioural (trucks not slowing down)? Track and recommend targeted action.",
     "why": "Dust crossing the site boundary is a community impact — and a regulatory violation. Aramco's EMP requires active dust suppression during earthworks at all times. Haul roads must be watered, not just earthworks.",
     "how": "1. Walk active earthworks areas — any visible dust cloud above the work area?\n2. Locate the water truck: is it actively spraying? Or parked?\n3. At haul road entry/exit: watch 5 trucks — any uncovered loads?\n4. Estimate wind direction: is dust blowing toward the site boundary or community?\n5. Photograph any visible dust plume or uncovered load.\n——— At your desk ———\n6. Enter: location, earthworks active (Y/N), spraying active (Y/N), uncovered trucks (count), plume visible (Y/N), wind direction.\n7. Raise a Finding for: no spraying during active earthworks, uncovered loads, or visible plume reaching boundary (High).\n——— Pattern ———\n8. Which area or contractor consistently creates dust? Targeted Finding is more effective than a general note.",
     "right": "Field: Unit 350 earthworks — water truck spraying ✓. Haul road: 2 of 5 trucks uncovered → Finding F-010. Dust plume reaching site boundary (westward wind) → Finding F-010b (High).\nDesk: Both locations logged; F-010 assigned to transport supervisor, F-010b to project manager.\nAnalysis: Unit 350 boundary dust appeared in 3 rounds on westward wind days — recommend dust barrier installation in weekly report.",
     "wrong": "There's dust but it's a construction site.",
     "tasks": [{"k": "rounds_submitted", "label": "Dust control inspections", "target": 4}]},

    {"code": "E9",  "phase": 2, "module": "EM3", "chapter": "3.2",
     "title": "Public Protection",
     "ref": "Aramco EMP + GI 2.401 §Community",
     "what": "Field: Walk the full perimeter fence. Identify any gap, damage, or section where dust, stone debris, or noise could reach the public. At each vulnerable section: is there a secondary barrier or shrouding in place? Is any heavy equipment operating within 50 m of the perimeter without dust/debris screening?\nDesk: Log each perimeter section — location, fence status (intact/damaged/gap), secondary barrier (Y/N), equipment proximity (Y/N). Raise a Finding for any gap or equipment within 50 m without screening.\nAnalysis: Perimeter gaps in the same location 2+ rounds? Maintenance is not responding. Escalate to project management — do not just keep logging the same gap.",
     "why": "Flying stone chips and concrete debris from construction are a public safety hazard. Dust crossing the perimeter is both a health risk to neighbours and a community relations issue.",
     "how": "1. Walk the full perimeter fence — all sides, not just the main gate.\n2. Check each section: fence intact? Any gap where a person or debris could pass through?\n3. Note equipment operating near the perimeter — within 50 m?\n4. Is there dust or debris accumulating on the public side of the fence?\n5. Photograph any gap or equipment without screening.\n——— At your desk ———\n6. Enter each perimeter section: status, secondary barrier, closest equipment distance.\n7. Raise a Finding for: fence gap, equipment within 50 m without screening, dust accumulation on public side.\n——— Escalation trigger ———\n8. Same gap in 2+ rounds: escalate directly to project manager with a maintenance deadline request.",
     "right": "Field: Full perimeter walked — 3 sides intact ✓. North side: 4m fence section collapsed → Finding F-011 (High, immediate). Crane operating 35 m from perimeter without debris screen → Finding F-012.\nDesk: Both findings logged and assigned immediately.\nAnalysis: North fence section repaired after F-011 but recollapsed — escalation to project manager with structural assessment request.",
     "wrong": "The fence is mostly fine.",
     "tasks": [{"k": "rounds_submitted", "label": "Perimeter protection inspections", "target": 3}]},

    # ═══ Module 1: Waste Management ═══
    {"code": "E1",  "phase": 1, "module": "EM1", "chapter": "1.1",
     "title": "Non-Hazardous Waste",
     "ref": "GI 430.001 §4 + Aramco Waste Segregation Standard",
     "what": "Field: Inspect every waste collection point on site. For each: is the bin covered and sealed? Is it clearly labelled (general / recyclable)? Is it being emptied daily — or is there overflow? Check for cross-contamination (hazardous items in general bins).\nDesk: Enter each waste point — location, bin type, cover status (Y/N), labelled (Y/N), overflow (Y/N), cross-contamination (Y/N). Raise a Finding for any overflow, missing cover, or cross-contamination.\nAnalysis: Compare waste point status over 4 inspections. Any location consistently overflowing? The collection frequency is inadequate — escalate to waste contractor with a schedule recommendation.",
     "why": "Non-hazardous waste that overflows or mixes with hazardous items loses its classification and must be disposed of as hazardous — at significant cost. Daily collection is mandatory.",
     "how": "1. Walk every waste collection area on your site route.\n2. For each bin: lid on and secured? Labelled with waste type? Filled to what level?\n3. Look inside — any hazardous items (paint cans, oil rags, chemical containers) mixed in?\n4. Photograph any overflow or mislabelled bin.\n——— At your desk ———\n5. Enter each waste point: location, type, cover, label, fill level (%), cross-contamination.\n6. Raise a Finding for: overflow (>80% full), missing cover or label, or hazardous items mixed in.\n——— Weekly trend ———\n7. List overflowing points across the week. Same location each time? Collection schedule is broken — note in weekly report: \"Waste point at area 340 NE overflows 3 of 5 days — recommend twice-daily collection.",
     "right": "Field: 8 waste points checked. Points 1–6: covered, labelled, <60% full ✓. Point 7: overflowing general waste → Finding F-001. Point 8: paint can in general bin → Finding F-002 (cross-contamination, severity High).\nDesk: All 8 points logged; F-001 assigned to waste contractor, F-002 escalated to HSE supervisor.\nAnalysis: Point 7 overflowed in 4 consecutive rounds — collection frequency note added to weekly report.",
     "wrong": "There are bins on site.",
     "tasks": [{"k": "rounds_submitted", "label": "Waste point inspections logged", "target": 3}]},

    {"code": "E2",  "phase": 1, "module": "EM1", "chapter": "1.2",
     "title": "Hazardous Waste & GI 430.001",
     "ref": "GI 430.001 §5-6 + Aramco HAZWASTE Standard",
     "what": "Field: Identify and inspect all hazardous waste accumulation areas (HAAs). For each: confirm dedicated labelled container in place, correct colour coding, waste quantity recorded, no free liquids or leaks, area secured and bunded if required.\nDesk: Enter the HAA inspection — location, waste type, container label, quantity (kg/L), leak status, bunding adequate. Raise a Finding for any missing label, unlabelled container, or free liquid.\nAnalysis: Review HAA quantities over the week. Is accumulation growing faster than disposal? Flag to supervisor before it reaches the 12-month storage limit.",
     "why": "GI 430.001 requires that every hazardous waste type has a dedicated, labelled, secondary-contained container. Unlabelled or mixed hazardous waste is a regulatory violation and a spill risk.",
     "how": "1. Locate all HAAs on site from the site waste plan.\n2. At each HAA: confirm dedicated containers exist for each waste type.\n3. Check labels: waste type name, hazard class, generator info — all present and legible?\n4. Inspect for leaks: any free liquid under the container? Bunding dry?\n5. Note approximate quantity in each container (kg or L).\n6. Photograph any unlabelled container, leak, or bunding failure.\n——— At your desk ———\n7. Enter each HAA: waste types, container count, label status, quantity, leak (Y/N), bunding (pass/fail).\n8. Raise a Finding for: missing label, free liquid, bunding failed, or container without secondary containment.\n——— Accumulation trend ———\n9. Compare quantities to last 2 inspections. Growing? Alert supervisor before disposal is overdue.",
     "right": "Field: 2 HAAs — HAA-A: used oil (clearly labelled, bunded, no leak ✓). HAA-B: container unlabelled, slight oil film under drum → Finding F-003 raised (unlabelled, High) + F-004 (leak, High).\nDesk: Both HAAs logged; F-003 and F-004 raised, assigned to EHS coordinator.\nAnalysis: HAA-A used oil volume increased from 40L to 95L in 2 weeks — disposal request added to weekly report.",
     "wrong": "There's a hazardous waste area.",
     "tasks": [{"k": "rounds_submitted", "label": "Hazardous waste area inspections", "target": 3}]},

    {"code": "E3",  "phase": 1, "module": "EM1", "chapter": "1.3",
     "title": "Waste Management Plan",
     "ref": "GI 430.001 §3 + Site EMP",
     "what": "Field: Review the site Waste Management Plan (WMP) document. Then walk the site to check whether practices match the plan — correct collection points, correct bins, segregation applied, contractor following the approved schedule.\nDesk: Enter your WMP compliance review — date reviewed, sections audited, conformance (Y/N per section), deviations found. Raise a Finding for each deviation from the approved plan.\nAnalysis: Track WMP deviations over 4 rounds. Recurring gaps in the same section mean the plan itself needs revision — recommend plan update in your monthly report.",
     "why": "The WMP is a contractual and regulatory document. Any activity that deviates from it without an approved revision is a non-conformance — not an operational preference.",
     "how": "1. Retrieve the current site WMP and identify the key requirements: collection points, segregation scheme, contractor schedule, disposal routes.\n2. Walk the site and compare what you see to what the plan says.\n3. Note each section: does field practice match the plan?\n4. Photograph any deviation.\n——— At your desk ———\n5. Enter compliance review: plan version, date, each section assessed, conformance (Y/N), notes.\n6. Raise a Finding for each deviation.\n——— Monthly analysis ———\n7. If the same section deviates repeatedly: the plan may be outdated or unworkable. Recommend a plan revision in the monthly report — do not just keep raising the same Finding.",
     "right": "Field: WMP reviewed — Collection points ✓, segregation ✓, schedule: contractor 1 day late on special waste pickup → Finding F-005.\nDesk: Compliance review logged; F-005 assigned to contractor coordinator.\nAnalysis: Special waste collection delay appeared in 3 of 4 reviews — WMP revision recommended: change schedule to twice-weekly.",
     "wrong": "The waste plan is filed.",
     "tasks": [{"k": "rounds_submitted", "label": "Waste plan compliance reviews", "target": 2}]},

    {"code": "E13", "phase": 3, "module": "EM5", "chapter": "5.1",
     "title": "Temporary Sanitation",
     "ref": "SAEHC-S-07 + CSM 11.4.D",
     "what": "Field: Count all portable toilets on site. Verify the count meets the ratio (1 unit per 20 workers at peak). Inspect each unit: is it emptied? No overflow, no odour indicating neglect? Door lock functional? Hand sanitiser present?\nDesk: Log total worker headcount, total toilet units, ratio (units per worker), any overflow units, any missing sanitiser. Raise a Finding if ratio is below standard or any unit is overflowing.\nAnalysis: Is the ratio consistently inadequate? That means either headcount is growing or units are being removed. Track trend and recommend before a hygiene incident occurs.",
     "why": "Insufficient sanitation facilities at construction sites is a direct cause of gastrointestinal illness outbreaks. The regulatory minimum (1:20) is a floor — not a target. During peak headcount, the ratio should be checked actively.",
     "how": "1. Get today's peak headcount from the HSE log or the site supervisor.\n2. Count all portable toilets currently on site — include all locations (work areas and camp).\n3. Calculate ratio: workers ÷ units. If >20 per unit: violation.\n4. Visit each portable unit: open door — does it close and lock? Overflow? Odour?\n5. Check sanitiser: present and not empty?\n6. Photograph any overflowing unit or missing sanitiser.\n——— At your desk ———\n7. Enter: headcount, unit count, ratio, overflow units, sanitiser missing (count). Raise a Finding for ratio >20:1 or any overflow unit.\n——— Trend ———\n8. Is the ratio worsening? Are more workers arriving? Recommend additional units before the ratio is violated — not after.",
     "right": "Field: 320 workers, 18 units → ratio 17.8:1 ✓. Unit 7: overflowing, lock broken → Finding F-015. Unit 12: no sanitiser → Finding F-016.\nDesk: All logged; both findings raised and assigned to facilities contractor.\nAnalysis: Ratio has been 17:1 for 3 rounds — adequate, but monitoring for new arrivals.",
     "wrong": "There are toilets on site.",
     "tasks": [{"k": "rounds_submitted", "label": "Sanitation unit inspections", "target": 3}]},

    {"code": "E14", "phase": 3, "module": "EM5", "chapter": "5.2",
     "title": "Drinking Water — Environmental",
     "ref": "GI 151.006 + SAEHC-01",
     "what": "Field: From an environmental protection perspective: confirm water sources are physically isolated from contamination sources (fuel storage, chemical areas, sewage discharge). Test chlorine at the point closest to a contamination risk (not just the main supply). Check water supply hoses for cracks or soil contact.\nDesk: Log each water point assessed — location, distance from nearest contamination source (m), chlorine reading (ppm), hose condition (good/cracked). Raise a Finding for any water point within 10 m of a contamination source or chlorine below 0.5 ppm.\nAnalysis: Any water point consistently reading low chlorine? Either the source is being compromised or the treatment frequency is inadequate — escalate beyond a finding.",
     "why": "Construction sites have fuel, chemicals, and sewage in close proximity to water points. The environmental officer's role is to assess the risk of cross-contamination from the environment side — not just confirm chlorine ppm.",
     "how": "1. List all water supply points on site.\n2. For each: estimate the distance to the nearest potential contamination source (fuel tank, chemical store, sewage line).\n3. At the water point closest to a contamination risk: test chlorine. Record ppm.\n4. Inspect the supply hose: cracked? Lying on soil? Submerged in a puddle?\n5. Photograph any hose in contact with soil or any point within 10 m of a contamination source.\n——— At your desk ———\n6. Enter each point: location, distance to contamination source (m), chlorine (ppm), hose condition.\n7. Raise a Finding for: <10 m from contamination source, chlorine <0.5 ppm, or hose in soil contact.\n——— Trend ———\n8. Any water point consistently low chlorine? Source may be at risk — escalate to EHS and water supply team.",
     "right": "Field: 3 water points — WP-1: 45m from fuel store, chlorine 1.2 ppm, hose above ground ✓. WP-2: 8m from chemical store → Finding F-017 (proximity, High). WP-3: hose lying in puddle → Finding F-018.\nDesk: All 3 logged; F-017 and F-018 raised.\nAnalysis: WP-2 proximity finding recurring — recommend relocating water point in monthly report.",
     "wrong": "The water looks fine.",
     "tasks": [{"k": "rounds_submitted", "label": "Water source environmental checks", "target": 3}]},

    # ═══ Module 6: Documentation & Reporting ═══
    {"code": "E15", "phase": 4, "module": "EM6", "chapter": "6.1",
     "title": "Daily Environment Tour",
     "ref": "Aramco EMP + GI 430.001",
     "what": "Field: Conduct a structured daily tour covering all 5 environmental categories: waste management, soil/water protection, dust control, HAZCOM, and sanitation. The tour must cover all active work areas — not just high-risk areas. Document start and end time.\nDesk: Enter all 5 categories in the system before end of shift. Every violation must have a Finding raised same day with photo. Submit the tour record before leaving.\nAnalysis: Review your own weekly tour completion. Any category consistently skipped? That blind spot becomes a KPI gap your supervisor sees. Address the gap in the weekly report.",
     "why": "A complete daily tour = all 5 categories covered in all active areas + all entered before shift end. Skipping one category is an incomplete tour — there is no partial credit.",
     "how": "1. Plan your route: list all active areas for the day.\n2. Start with the highest-risk area (active earthworks or chemical use).\n3. Category 1 — Waste: bins covered? HAAs inspected? No overflow?\n4. Category 2 — Soil/water: dewatering discharge compliant? Oil containment in place?\n5. Category 3 — Dust: water truck spraying? Trucks covered?\n6. Category 4 — HAZCOM: visible unlabelled container? SDS accessible?\n7. Category 5 — Sanitation: ratio adequate? No overflow units?\n8. Record start and end time. Minimum 2 hours for a valid full tour.\n——— At your desk ———\n9. Enter all 5 categories. Photos for every Finding.\n10. Submit before shift end.\n——— Weekly self-review ———\n11. Check your own records: all 5 categories complete each day? Any gaps?",
     "right": "Field: Tour 07:00–09:30 (150 min) — all 5 categories covered across 4 active areas. 2 findings: F-019 (uncovered truck) and F-020 (waste point overflow). Both photographed.\nDesk: All 5 categories entered, 2 findings submitted, tour submitted by 10:00.\nAnalysis: Week review — 5/5 tours complete, all 5 categories each day. No gaps.",
     "wrong": "I checked the main areas.",
     "tasks": [{"k": "rounds_submitted", "label": "Full daily environment tours submitted", "target": 10},
               {"k": "findings_written", "label": "Environmental violations documented", "target": 5}]},

    {"code": "E16", "phase": 4, "module": "EM6", "chapter": "6.2",
     "title": "Environmental Incident Reporting",
     "ref": "GI 2.401 + Aramco ERP (SAPO → EPD)",
     "what": "Field: Know the reporting chain before an incident happens. The moment you confirm an environmental incident (spill reaching soil/water, illegal discharge, significant dust event off-site): stop the source if safe, contain, photograph, then immediately report to EHS supervisor. Do not wait to assess — report first, assess second.\nDesk: Log the incident in full: date, time, location, type, estimated quantity, source, immediate actions taken, responsible party, EHS notified at (time). Raise a Finding if root cause is a management system failure.\nAnalysis: Review the week's incident log. Were all incidents reported within the required timeframe? Any incident that was delayed in reporting? Include root cause in the weekly report.",
     "why": "Scenario: You find a chemical spill. Actions in order — 1: Stop source. 2: Contain. 3: Report to EHS (verbally, immediately). 4: Document. 5: Dispose. Any step out of order — especially reporting — is a secondary violation.",
     "how": "1. Know the reporting chain in advance: who is your direct EHS supervisor? What is their number? Who notifies SAPO if needed?\n2. When you find an incident: assess safety first — is the area safe to approach?\n3. If safe: stop the source (close valve, move drum, switch off equipment).\n4. Contain: deploy boom or sand berm around the spread.\n5. Report to EHS supervisor — verbally, immediately. Do not wait until you finish containment.\n6. Photograph: source, spread extent, containment measures.\n——— At your desk ———\n7. Enter the full incident log within 2 hours: time discovered, source, material, estimated quantity, containment actions, EHS notified at.\n8. If the incident was caused by a system failure (missing bunding, no containment plan): raise a Finding with severity Critical.\n——— Weekly review ———\n9. Were all incidents this week reported within 2 hours? Any late? Note in weekly report with root cause.",
     "right": "Field: Found chemical spill (paint thinner, ~5L) at painting station — source identified (open drum tipped), sand berm deployed around spread, EHS supervisor called immediately at 09:14. Photographed source and containment.\nDesk: Full incident logged by 10:00 — all fields complete. Finding F-021 raised (no secondary containment at painting station, severity Critical).\nAnalysis: All 2 incidents this week reported within 1 hour. F-021 root cause: no containment plan at painting area — plan requirement added to monthly report.",
     "wrong": "I'll write up the report later.",
     "tasks": [{"k": "findings_written", "label": "Environmental incidents fully documented", "target": 2}]},

    {"code": "E17", "phase": 4, "module": "EM6", "chapter": "6.3",
     "title": "Periodic Records",
     "ref": "GI 430.001 + Aramco EMP",
     "what": "Field: At the end of each week, retrieve and review: the hazardous waste log, dust control records (water truck usage or spraying logs), and the water quality check register. Are they complete — every day filled in, no blank rows?\nDesk: Enter the records audit — for each register: date range reviewed, complete (Y/N), missing days (list), responsible party for each gap. Raise a Finding for each gap or missing record.\nAnalysis: Any record consistently incomplete? That person or process needs direct follow-up — include in monthly report with a corrective action recommendation.",
     "why": "Periodic records are the audit trail. A regulator or Aramco audit will check whether records match the field reality. Gaps are automatically a non-conformance — even if the field practice was correct.",
     "how": "1. Collect the hazardous waste log — every entry for the past week present? No blank rows?\n2. Collect the dust control/water truck log — every working day recorded?\n3. Collect the water quality check register — every check point, every day?\n4. For each register: note any missing day or incomplete row.\n5. Identify the responsible person for each gap.\n——— At your desk ———\n6. Enter the records review: register name, date range, complete (Y/N), missing entries (list by date).\n7. Raise a Finding for each incomplete register.\n——— Monthly pattern ———\n8. Any register consistently incomplete? Include the responsible person and a corrective recommendation in the monthly report — do not just raise Findings repeatedly.",
     "right": "Field: 3 registers reviewed — hazardous waste log: complete ✓. Dust control log: missing Thursday → Finding F-022. Water quality register: complete ✓.\nDesk: Records review logged; F-022 assigned to dust control supervisor.\nAnalysis: Dust control log was incomplete in 3 of 4 weeks — corrective recommendation for automatic logging system included in monthly report.",
     "wrong": "The records are being kept.",
     "tasks": [{"k": "weeks_reported", "label": "Weekly records audits completed", "target": 4}]},
]
ENV_LEVEL_BY_CODE = {l["code"]: l for l in ENV_LEVELS}
ENV_LEVEL_CODES   = [l["code"] for l in ENV_LEVELS]


# ── PTW Training Routes ───────────────────────────────────────────────────────

def _ptw_progress(officer_id):
    """Return {(mod_seq, door_seq): submission} for one officer."""
    subs = PtwDoorSubmission.query.filter_by(officer_id=officer_id).all()
    return {(s.module_seq, s.door_seq): s for s in subs}

def _ptw_door_unlocked(progress, mod_seq, door_seq):
    """Door 1 always open. Door N requires door N-1 approved."""
    if door_seq == 1:
        return True
    prev = progress.get((mod_seq, door_seq - 1))
    return prev is not None and prev.status == "approved"

def _ptw_module_unlocked(progress, mod_seq):
    """Module 1 always open. Module N requires module N-1 fully done."""
    if mod_seq <= 1:
        return True
    return _ptw_module_done(progress, mod_seq - 1)

def _ptw_module_done(progress, mod_seq):
    mod = PTW_MOD_BY_SEQ.get(mod_seq)
    if not mod:
        return False
    return all(progress.get((mod_seq, d["seq"])) and
               progress[(mod_seq, d["seq"])].status == "approved"
               for d in mod["doors"] if not d.get("ref_only"))


@app.route("/ptw-training")
@login_required
def ptw_training_home():
    u = cur_user()
    if u.role != "safety_officer":
        abort(403)
    if not getattr(u, "ptw_training_active", False):
        return redirect(url_for("user_location_page"))
    progress = _ptw_progress(u.id)
    mods = []
    for m in PTW_MODULES:
        field_doors = [d for d in m["doors"] if not d.get("ref_only")]
        done = sum(1 for d in field_doors
                   if progress.get((m["seq"], d["seq"])) and
                      progress[(m["seq"], d["seq"])].status == "approved")
        mods.append({**m, "done": done, "total": len(field_doors),
                     "locked": not _ptw_module_unlocked(progress, m["seq"])})
    return render_template("ptw_training_home.html", mods=mods, officer=u)


@app.route("/ptw-training/module/<int:mod_seq>")
@login_required
def ptw_training_module(mod_seq):
    u = cur_user()
    if u.role != "safety_officer":
        abort(403)
    if not getattr(u, "ptw_training_active", False):
        return redirect(url_for("user_location_page"))
    mod = PTW_MOD_BY_SEQ.get(mod_seq) or abort(404)
    progress = _ptw_progress(u.id)
    if not _ptw_module_unlocked(progress, mod_seq):
        flash("Complete the previous module first before accessing this one.", "warning")
        return redirect(url_for("ptw_training_home"))
    doors = []
    for d in mod["doors"]:
        sub = progress.get((mod_seq, d["seq"]))
        doors.append({**d,
                      "sub": sub,
                      "unlocked": _ptw_door_unlocked(progress, mod_seq, d["seq"])})
    return render_template("ptw_training_module.html", mod=mod, doors=doors, officer=u)


@app.route("/ptw-training/module/<int:mod_seq>/door/<int:door_seq>", methods=["GET", "POST"])
@login_required
def ptw_training_door(mod_seq, door_seq):
    u = cur_user()
    if u.role != "safety_officer":
        abort(403)
    if not getattr(u, "ptw_training_active", False):
        return redirect(url_for("user_location_page"))
    mod  = PTW_MOD_BY_SEQ.get(mod_seq) or abort(404)
    door = next((d for d in mod["doors"] if d["seq"] == door_seq), None) or abort(404)
    progress = _ptw_progress(u.id)
    if not _ptw_door_unlocked(progress, mod_seq, door_seq):
        flash("Complete and get approval for the previous stage first.", "warning")
        return redirect(url_for("ptw_training_module", mod_seq=mod_seq))
    sub = progress.get((mod_seq, door_seq))

    if request.method == "POST":
        if sub and sub.status in ("pending", "approved"):
            flash("Already submitted.", "info")
            return redirect(url_for("ptw_training_door", mod_seq=mod_seq, door_seq=door_seq))
        # Reference door: auto-approve on first visit
        if door.get("ref_only"):
            sub = PtwDoorSubmission(
                officer_id=u.id, module_seq=mod_seq, door_seq=door_seq,
                answers=json.dumps([], ensure_ascii=False),
                photo_path=None, status="approved"
            )
            db.session.add(sub)
            db.session.commit()
            flash("Reference marked as reviewed.", "success")
            return redirect(url_for("ptw_training_module", mod_seq=mod_seq))
        answers = []
        for i, _ in enumerate(door["questions"], 1):
            answers.append((request.form.get(f"q{i}") or "").strip())
        photo = request.files.get("photo")
        photo_path = None
        if photo and photo.filename:
            import os as _os
            ext = _os.path.splitext(photo.filename)[1].lower()
            fname = f"ptw_{u.id}_{mod_seq}_{door_seq}_{int(datetime.utcnow().timestamp())}{ext}"
            save_dir = _os.path.join(app.root_path, "static", "hse_photos")
            _os.makedirs(save_dir, exist_ok=True)
            photo.save(_os.path.join(save_dir, fname))
            photo_path = fname
        sub = PtwDoorSubmission(
            officer_id=u.id, module_seq=mod_seq, door_seq=door_seq,
            answers=json.dumps(answers, ensure_ascii=False),
            photo_path=photo_path, status="pending"
        )
        db.session.add(sub)
        db.session.commit()
        flash("Submitted — waiting for supervisor approval.", "success")
        return redirect(url_for("ptw_training_module", mod_seq=mod_seq))

    answers = json.loads(sub.answers) if sub and sub.answers else []
    return render_template("ptw_training_door.html",
                           mod=mod, door=door, sub=sub, answers=answers, officer=u)


@app.route("/ptw-training/supervisor")
@login_required
def ptw_supervisor_review():
    u = cur_user()
    if u.role not in ("safety_supervisor", "admin", "super_admin"):
        abort(403)
    officers = _get_safety_officers(u)
    officer_ids = [o.id for o in officers]
    # Only show safety_officer trainees
    trainee_ids = [o.id for o in officers
                   if o.role == "safety_officer" and getattr(o, "ptw_training_active", False)]
    pending = (PtwDoorSubmission.query
               .filter(PtwDoorSubmission.officer_id.in_(trainee_ids),
                       PtwDoorSubmission.status == "pending")
               .order_by(PtwDoorSubmission.submitted_at).all())
    officer_map = {o.id: o for o in officers}
    rows = []
    for s in pending:
        mod  = PTW_MOD_BY_SEQ.get(s.module_seq, {})
        door = next((d for d in mod.get("doors", []) if d["seq"] == s.door_seq), {})
        rows.append({"sub": s, "officer": officer_map.get(s.officer_id),
                     "mod": mod, "door": door,
                     "answers": json.loads(s.answers) if s.answers else []})
    # Summary per trainee
    trainees = []
    for oid in trainee_ids:
        o = officer_map.get(oid)
        if not o:
            continue
        prog = _ptw_progress(oid)
        done = sum(1 for s in prog.values() if s.status == "approved")
        total = sum(len(m["doors"]) for m in PTW_MODULES)
        trainees.append({"officer": o, "done": done, "total": total,
                         "pending": sum(1 for s in prog.values() if s.status == "pending")})
    return render_template("ptw_supervisor_review.html",
                           rows=rows, trainees=trainees, now=datetime.now(RIYADH_TZ))


@app.post("/ptw-training/supervisor/review/<int:sub_id>")
@login_required
def ptw_supervisor_do_review(sub_id):
    u = cur_user()
    if u.role not in ("safety_supervisor", "admin", "super_admin"):
        abort(403)
    sub = db.session.get(PtwDoorSubmission, sub_id) or abort(404)
    action = request.form.get("action")
    note   = (request.form.get("note") or "").strip()
    if action not in ("approve", "reject"):
        abort(400)
    sub.status        = "approved" if action == "approve" else "rejected"
    sub.reviewer_id   = u.id
    sub.reviewed_at   = datetime.utcnow()
    sub.reviewer_note = note or None
    db.session.commit()
    flash(f"Stage {'approved' if action == 'approve' else 'rejected'}.", "success")
    return redirect(url_for("ptw_supervisor_review"))


@app.route("/ptw-training/reports")
@login_required
def ptw_reports_picker():
    u = cur_user()
    if u.role not in ("safety_supervisor", "admin", "super_admin"):
        abort(403)
    today = datetime.now(RIYADH_TZ).date()
    return render_template("ptw_reports_picker.html", today=today)


@app.route("/ptw-training/reports/weekly")
@login_required
def ptw_report_weekly():
    u = cur_user()
    if u.role not in ("safety_supervisor", "admin", "super_admin"):
        abort(403)
    today = datetime.now(RIYADH_TZ).date()
    try:
        ws = date.fromisoformat(request.args.get("week_start", ""))
    except (TypeError, ValueError):
        ws = today - timedelta(days=today.weekday())
    we = ws + timedelta(days=6)
    officers = _get_safety_officers(u)
    trainees = [o for o in officers
                if o.role == "safety_officer" and getattr(o, "ptw_training_active", False)]
    rows = []
    total_subs = total_approved = total_rejected = 0
    for o in trainees:
        subs = (PtwDoorSubmission.query
                .filter(PtwDoorSubmission.officer_id == o.id,
                        PtwDoorSubmission.submitted_at >= datetime.combine(ws, __import__('datetime').time.min),
                        PtwDoorSubmission.submitted_at <= datetime.combine(we, __import__('datetime').time.max))
                .all())
        approved  = sum(1 for s in subs if s.status == "approved")
        rejected  = sum(1 for s in subs if s.status == "rejected")
        pending   = sum(1 for s in subs if s.status == "pending")
        prog = _ptw_progress(o.id)
        field_total = sum(len([d for d in m["doors"] if not d.get("ref_only")]) for m in PTW_MODULES)
        all_approved = sum(1 for s in prog.values() if s.status == "approved")
        cur_mod = next((m["seq"] for m in PTW_MODULES
                        if not _ptw_module_done(prog, m["seq"])), 7)
        rows.append({"officer": o, "submitted": len(subs), "approved": approved,
                     "rejected": rejected, "pending": pending,
                     "all_approved": all_approved, "field_total": field_total,
                     "cur_mod": cur_mod})
        total_subs     += len(subs)
        total_approved += approved
        total_rejected += rejected
    return render_template("ptw_report_weekly.html",
                           rows=rows, ws=ws, we=we,
                           total_subs=total_subs, total_approved=total_approved,
                           total_rejected=total_rejected,
                           now=datetime.now(RIYADH_TZ))


@app.route("/ptw-training/reports/monthly")
@login_required
def ptw_report_monthly():
    u = cur_user()
    if u.role not in ("safety_supervisor", "admin", "super_admin"):
        abort(403)
    today = datetime.now(RIYADH_TZ).date()
    try:
        year  = int(request.args.get("year",  today.year))
        month = int(request.args.get("month", today.month))
    except (TypeError, ValueError):
        return redirect(url_for("ptw_reports_picker"))
    from calendar import monthrange, month_name as _mn
    first_day = date(year, month, 1)
    last_day  = date(year, month, monthrange(year, month)[1])
    officers = _get_safety_officers(u)
    trainees = [o for o in officers
                if o.role == "safety_officer" and getattr(o, "ptw_training_active", False)]
    rows = []
    total_subs = total_approved = 0
    for o in trainees:
        subs = (PtwDoorSubmission.query
                .filter(PtwDoorSubmission.officer_id == o.id,
                        PtwDoorSubmission.submitted_at >= datetime.combine(first_day, __import__('datetime').time.min),
                        PtwDoorSubmission.submitted_at <= datetime.combine(last_day, __import__('datetime').time.max))
                .all())
        prog = _ptw_progress(o.id)
        field_total = sum(len([d for d in m["doors"] if not d.get("ref_only")]) for m in PTW_MODULES)
        all_approved = sum(1 for s in prog.values() if s.status == "approved")
        mods_done = sum(1 for m in PTW_MODULES if _ptw_module_done(prog, m["seq"]))
        approved_this_month = sum(1 for s in subs if s.status == "approved")
        rows.append({"officer": o,
                     "submitted": len(subs),
                     "approved_month": approved_this_month,
                     "all_approved": all_approved,
                     "field_total": field_total,
                     "mods_done": mods_done,
                     "pct": int(all_approved / field_total * 100) if field_total else 0})
        total_subs     += len(subs)
        total_approved += approved_this_month
    return render_template("ptw_report_monthly.html",
                           rows=rows, year=year, month=month,
                           month_name=_mn[month], first_day=first_day, last_day=last_day,
                           total_subs=total_subs, total_approved=total_approved,
                           now=datetime.now(RIYADH_TZ))


@app.route("/ptw-training/supervisor/progress")
@login_required
def ptw_progress_report():
    u = cur_user()
    if u.role not in ("safety_supervisor", "admin", "super_admin"):
        abort(403)
    officers = _get_safety_officers(u)
    trainees = []
    for o in officers:
        if not (o.role == "safety_officer" and getattr(o, "ptw_training_active", False)):
            continue
        prog = _ptw_progress(o.id)
        field_total = sum(len([d for d in m["doors"] if not d.get("ref_only")]) for m in PTW_MODULES)
        approved    = sum(1 for s in prog.values() if s.status == "approved"
                          and not PTW_MOD_BY_SEQ.get(s.module_seq, {}).get("doors", [{}])[0].get("ref_only"))
        pending_ct  = sum(1 for s in prog.values() if s.status == "pending")
        rejected_ct = sum(1 for s in prog.values() if s.status == "rejected")
        # per-module summary
        mods_summary = []
        for m in PTW_MODULES:
            field_doors = [d for d in m["doors"] if not d.get("ref_only")]
            done = sum(1 for d in field_doors
                       if prog.get((m["seq"], d["seq"])) and
                          prog[(m["seq"], d["seq"])].status == "approved")
            last_sub = max(
                (prog[(m["seq"], d["seq"])].submitted_at for d in m["doors"]
                 if prog.get((m["seq"], d["seq"]))),
                default=None)
            mods_summary.append({"seq": m["seq"], "title": m["title"],
                                  "done": done, "total": len(field_doors),
                                  "last": last_sub})
        last_activity = max((s.submitted_at for s in prog.values()), default=None)
        trainees.append({"officer": o, "approved": approved, "field_total": field_total,
                         "pending": pending_ct, "rejected": rejected_ct,
                         "last_activity": last_activity, "mods": mods_summary})
    return render_template("ptw_progress_report.html", trainees=trainees,
                           now=datetime.now(RIYADH_TZ))


@app.route("/ptw-training/supervisor/answers/<int:officer_id>")
@login_required
def ptw_answers_report(officer_id):
    u = cur_user()
    if u.role not in ("safety_supervisor", "admin", "super_admin"):
        abort(403)
    officer = db.session.get(User, officer_id) or abort(404)
    if not (officer.role == "safety_officer" and getattr(officer, "ptw_training_active", False)):
        abort(404)
    prog = _ptw_progress(officer_id)
    sections = []
    for m in PTW_MODULES:
        doors_data = []
        for d in m["doors"]:
            if d.get("ref_only"):
                continue
            sub = prog.get((m["seq"], d["seq"]))
            if not sub:
                continue
            answers = json.loads(sub.answers) if sub.answers else []
            doors_data.append({"door": d, "sub": sub, "answers": answers})
        if doors_data:
            sections.append({"mod": m, "doors": doors_data})
    return render_template("ptw_answers_report.html", officer=officer,
                           sections=sections, now=datetime.now(RIYADH_TZ))


# ── Welfare Models ───────────────────────────────────────────────────


class WlfLevelWork(db.Model):
    """One submission per level-specific task the officer completes."""
    __tablename__ = "wlf_level_work"
    id            = db.Column(db.Integer, primary_key=True)
    officer_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id    = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    level_code    = db.Column(db.String(5),  nullable=False)   # L1 … L14
    date          = db.Column(db.Date,       nullable=False)
    unit          = db.Column(db.String(20), nullable=True)
    has_measurement = db.Column(db.Boolean,  default=False)    # true for heat-index readings
    notes         = db.Column(db.Text,       nullable=True)
    created_at    = db.Column(db.DateTime,   default=datetime.utcnow)


class WlfPhoto(db.Model):
    __tablename__ = "wlf_photo"
    id         = db.Column(db.Integer, primary_key=True)
    round_id   = db.Column(db.Integer, db.ForeignKey("wlf_round.id"), nullable=True)
    finding_id = db.Column(db.Integer, db.ForeignKey("wlf_finding.id"), nullable=True)
    item_key   = db.Column(db.String(40))
    ref_no     = db.Column(db.String(40))
    photo_path = db.Column(db.String(255), nullable=False)
    kind       = db.Column(db.String(20), default="before")   # before/after
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class WlfFinding(db.Model):
    __tablename__ = "wlf_finding"
    id           = db.Column(db.Integer, primary_key=True)
    round_id     = db.Column(db.Integer, db.ForeignKey("wlf_round.id"), nullable=True)
    officer_id   = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id   = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date         = db.Column(db.Date, nullable=False)
    unit         = db.Column(db.String(20))
    area_text    = db.Column(db.String(255))
    item_key     = db.Column(db.String(40))
    ref_no       = db.Column(db.String(40))          # Shelter 19 / WS-07
    description  = db.Column(db.Text, nullable=False)
    severity     = db.Column(db.String(20), default="Medium")
    responsible  = db.Column(db.String(160))
    due_date     = db.Column(db.Date, nullable=True)
    status       = db.Column(db.String(20), default="open")   # open/closed
    action_taken = db.Column(db.Text)
    closure_note = db.Column(db.Text)
    closed_at    = db.Column(db.DateTime, nullable=True)
    closed_by    = db.Column(db.Integer, nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)


class WlfComplaint(db.Model):
    __tablename__ = "wlf_complaint"
    id          = db.Column(db.Integer, primary_key=True)
    officer_id  = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id  = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date        = db.Column(db.Date, nullable=False)
    unit        = db.Column(db.String(20))
    kind        = db.Column(db.String(20), default="facility")  # facility/escalate
    ref_no      = db.Column(db.String(40))
    description = db.Column(db.Text, nullable=False)
    raised_by_n = db.Column(db.Integer, default=1)
    status      = db.Column(db.String(20), default="open")      # open/closed/escalated
    seen_by_sup = db.Column(db.Boolean, default=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)


class WlfFlagLog(db.Model):
    __tablename__ = "wlf_flag_log"
    id          = db.Column(db.Integer, primary_key=True)
    officer_id  = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id  = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date        = db.Column(db.Date, nullable=False)
    hour_label  = db.Column(db.String(10), nullable=False)   # "07:00"
    flag        = db.Column(db.String(10))
    index_value = db.Column(db.String(10))
    source      = db.Column(db.String(30), default="whatsapp")
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("officer_id", "date", "hour_label",
                                          name="uq_wlf_flag_odh"),)


class WlfProgress(db.Model):
    __tablename__ = "wlf_progress"
    id          = db.Column(db.Integer, primary_key=True)
    officer_id  = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id  = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    level_code  = db.Column(db.String(10), nullable=False)
    status      = db.Column(db.String(20), default="locked")  # locked/active/pending/done
    briefed_at  = db.Column(db.DateTime, nullable=True)
    completed_at= db.Column(db.DateTime, nullable=True)
    approved_by = db.Column(db.Integer, nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    deliverable = db.Column(db.String(200), nullable=True)
    notes       = db.Column(db.Text, nullable=True)
    __table_args__ = (db.UniqueConstraint("officer_id", "level_code",
                                          name="uq_wlf_prog_ol"),)


class EnvLevelWork(db.Model):
    """One submission per level-specific task the environment officer completes."""
    __tablename__ = "env_level_work"
    id              = db.Column(db.Integer, primary_key=True)
    officer_id      = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id      = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    level_code      = db.Column(db.String(5),  nullable=False)   # E1 … E17
    date            = db.Column(db.Date,       nullable=False)
    unit            = db.Column(db.String(20), nullable=True)
    has_measurement = db.Column(db.Boolean,    default=False)
    notes           = db.Column(db.Text,       nullable=True)
    created_at      = db.Column(db.DateTime,   default=datetime.utcnow)


class EnvProgress(db.Model):
    __tablename__ = "env_progress"
    id           = db.Column(db.Integer, primary_key=True)
    officer_id   = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id   = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    level_code   = db.Column(db.String(10), nullable=False)
    status       = db.Column(db.String(20), default="locked")  # locked/active/pending/done
    briefed_at   = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    approved_by  = db.Column(db.Integer, nullable=True)
    approved_at  = db.Column(db.DateTime, nullable=True)
    deliverable  = db.Column(db.String(200), nullable=True)
    notes        = db.Column(db.Text, nullable=True)
    __table_args__ = (db.UniqueConstraint("officer_id", "level_code",
                                          name="uq_env_prog_ol"),)


class OfficerTeam(db.Model):
    """Maps an officer (any role) to a safety_supervisor — set by admin."""
    __tablename__ = "officer_team"
    id            = db.Column(db.Integer, primary_key=True)
    supervisor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    officer_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id    = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("supervisor_id", "officer_id",
                                          name="uq_officer_team_so"),)


class WlfSetting(db.Model):
    """Key-value store for per-company welfare settings (e.g. season toggle)."""
    __tablename__ = "wlf_setting"
    id         = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    key        = db.Column(db.String(40), nullable=False)
    value      = db.Column(db.String(200), nullable=False)
    __table_args__ = (db.UniqueConstraint("company_id", "key",
                                          name="uq_wlf_setting_ck"),)


class WlfWeeklyReport(db.Model):
    """Auto-generated weekly snapshot shared by officer with supervisor."""
    __tablename__ = "wlf_weekly_report"
    id          = db.Column(db.Integer, primary_key=True)
    company_id  = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    officer_id  = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    week_start  = db.Column(db.Date, nullable=False)
    officer_note = db.Column(db.Text, nullable=True)
    sup_comment = db.Column(db.Text, nullable=True)
    sup_seen    = db.Column(db.Boolean, default=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("officer_id", "week_start",
                                          name="uq_wlf_wr_ow"),)


class WlfAsset(db.Model):
    """Persistent welfare asset register (water stations, shelters, camp facilities…)."""
    __tablename__ = "wlf_asset"
    id            = db.Column(db.Integer, primary_key=True)
    company_id    = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    location_code = db.Column(db.String(20), nullable=False)
    asset_type    = db.Column(db.String(30), nullable=False)
    label         = db.Column(db.String(80), nullable=False)
    qty           = db.Column(db.Integer, default=1)
    capacity      = db.Column(db.Integer, nullable=True)   # shelter pax cap etc.
    notes         = db.Column(db.Text, nullable=True)
    added_by      = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow)


class ReportFile(db.Model):
    """PDF reports uploaded by welfare or environment officers."""
    __tablename__ = "report_file"
    id          = db.Column(db.Integer, primary_key=True)
    company_id  = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    role_type   = db.Column(db.String(20), nullable=False)   # welfare / environment
    report_type = db.Column(db.String(80), nullable=False)   # free text from dropdown+input
    file_path   = db.Column(db.String(500), nullable=False)
    report_date = db.Column(db.Date, nullable=False)
    notes       = db.Column(db.Text, nullable=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    uploader    = db.relationship("User", foreign_keys=[uploaded_by])


class ReportTypeOption(db.Model):
    """Dynamic list of report type names per role (welfare/environment)."""
    __tablename__ = "report_type_option"
    id         = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    role_type  = db.Column(db.String(20), nullable=False)   # welfare / environment
    name       = db.Column(db.String(80), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("company_id", "role_type", "name",
                                          name="uq_rto_crn"),)


class EnvChecklist(db.Model):
    """Digital fill of P2-EC / P2-WMC / P2-SPC Amiral weekly checklists."""
    __tablename__ = "env_checklist"
    id             = db.Column(db.Integer, primary_key=True)
    company_id     = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    officer_id     = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    checklist_type = db.Column(db.String(20), nullable=False)   # P2-EC / P2-WMC / P2-SPC
    area           = db.Column(db.String(200))
    report_no      = db.Column(db.String(60))
    subcontractor  = db.Column(db.String(200))
    filled_date    = db.Column(db.Date, nullable=False)
    items_json     = db.Column(db.Text)   # JSON [{item, section, status, area_sub, note}]
    observations   = db.Column(db.Text)
    signatory_name  = db.Column(db.String(255), nullable=True)
    signature_data  = db.Column(db.Text, nullable=True)  # base64 PNG from canvas
    attendees_sigs  = db.Column(db.Text, nullable=True)   # JSON [{name, company, sig}]
    is_official     = db.Column(db.Boolean, default=False, nullable=False)  # locked official
    created_at     = db.Column(db.DateTime,
                               default=lambda: datetime.now(RIYADH_TZ).replace(tzinfo=None))
    officer        = db.relationship("User", foreign_keys=[officer_id])


# ── Welfare access control ───────────────────────────────────────────

def is_safety_welfare(u):
    return bool(u) and getattr(u, "role", None) == "safety_welfare"


def is_welfare_viewer(u):
    """Supervisor-side visibility. Does NOT grant HSE access anywhere."""
    if not u or not getattr(u, "is_active", False):
        return False
    return getattr(u, "role", None) in ("safety_welfare", "safety_supervisor",
                                        "safety_manager", "admin", "super_admin")


def safety_welfare_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        u = cur_user()
        if not u:
            return redirect(url_for("login"))
        if not (is_safety_welfare(u) or is_welfare_viewer(u)):
            flash("This page is for the welfare team.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return inner


def welfare_supervisor_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        u = cur_user()
        if not u:
            return redirect(url_for("login"))
        if is_safety_welfare(u) or not is_welfare_viewer(u):
            flash("Supervisor access only.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return inner


# ── Environment Officer access control ──────────────────────────────

def is_environment_officer(u):
    return bool(u) and getattr(u, "role", None) == "environment_officer"


def is_environment_viewer(u):
    if not u or not getattr(u, "is_active", False):
        return False
    return getattr(u, "role", None) in (
        "environment_officer", "safety_supervisor", "safety_manager", "admin", "super_admin")


def environment_officer_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        u = cur_user()
        if not u:
            return redirect(url_for("login"))
        if not is_environment_viewer(u):
            flash("هذه الصفحة لفريق البيئة فقط.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return inner


# ── Environment Models ────────────────────────────────────────────────

class EnvWasteCheck(db.Model):
    __tablename__ = "env_waste_check"
    id                  = db.Column(db.Integer, primary_key=True)
    officer_id          = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id          = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date                = db.Column(db.Date, nullable=False)
    segregation_ok      = db.Column(db.Boolean, default=False)
    bins_labeled        = db.Column(db.Boolean, default=False)
    hazardous_area_ok   = db.Column(db.Boolean, default=False)
    disposal_records_ok = db.Column(db.Boolean, default=False)
    bins_overflow       = db.Column(db.Boolean, default=False)
    contractor_ok       = db.Column(db.Boolean, default=False)
    notes               = db.Column(db.Text, nullable=True)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)


class EnvSoilWaterCheck(db.Model):
    __tablename__ = "env_soil_water_check"
    id               = db.Column(db.Integer, primary_key=True)
    officer_id       = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id       = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date             = db.Column(db.Date, nullable=False)
    spill_kit_ok     = db.Column(db.Boolean, default=False)
    drip_trays_ok    = db.Column(db.Boolean, default=False)
    no_soil_staining = db.Column(db.Boolean, default=False)
    drainage_clear   = db.Column(db.Boolean, default=False)
    wadi_buffer_ok   = db.Column(db.Boolean, default=False)
    notes            = db.Column(db.Text, nullable=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)


class EnvDustCheck(db.Model):
    __tablename__ = "env_dust_check"
    id                 = db.Column(db.Integer, primary_key=True)
    officer_id         = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id         = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date               = db.Column(db.Date, nullable=False)
    watering_done      = db.Column(db.Boolean, default=False)
    wind_speed_ok      = db.Column(db.Boolean, default=False)
    covered_trucks     = db.Column(db.Boolean, default=False)
    haul_road_treated  = db.Column(db.Boolean, default=False)
    complaints_count   = db.Column(db.Integer, default=0)
    notes              = db.Column(db.Text, nullable=True)
    created_at         = db.Column(db.DateTime, default=datetime.utcnow)


class EnvHazcomCheck(db.Model):
    __tablename__ = "env_hazcom_check"
    id                = db.Column(db.Integer, primary_key=True)
    officer_id        = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id        = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date              = db.Column(db.Date, nullable=False)
    location          = db.Column(db.String(120), nullable=True)
    sds_available     = db.Column(db.Boolean, default=False)
    labels_ok         = db.Column(db.Boolean, default=False)
    storage_segregated = db.Column(db.Boolean, default=False)
    spill_kit_present = db.Column(db.Boolean, default=False)
    ppe_available     = db.Column(db.Boolean, default=False)
    active_chem_work  = db.Column(db.Boolean, default=False)
    ptw_number        = db.Column(db.String(40), nullable=True)
    ptw_valid         = db.Column(db.Boolean, default=False)
    ptw_controls_met  = db.Column(db.Boolean, default=False)
    notes             = db.Column(db.Text, nullable=True)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)


class EnvSewageCheck(db.Model):
    __tablename__ = "env_sewage_check"
    id                   = db.Column(db.Integer, primary_key=True)
    officer_id           = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id           = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date                 = db.Column(db.Date, nullable=False)
    no_leaks             = db.Column(db.Boolean, default=False)
    collection_points_ok = db.Column(db.Boolean, default=False)
    disposal_records_ok  = db.Column(db.Boolean, default=False)
    ground_discoloration = db.Column(db.Boolean, default=False)
    odor_complaints      = db.Column(db.Integer, default=0)
    notes                = db.Column(db.Text, nullable=True)
    created_at           = db.Column(db.DateTime, default=datetime.utcnow)


class EnvIncident(db.Model):
    __tablename__ = "env_incident"
    id            = db.Column(db.Integer, primary_key=True)
    officer_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id    = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date          = db.Column(db.Date, nullable=False)
    incident_type = db.Column(db.String(80), nullable=False)
    location      = db.Column(db.String(120), nullable=True)
    description   = db.Column(db.Text, nullable=False)
    action_taken  = db.Column(db.Text, nullable=True)
    status        = db.Column(db.String(20), default="open")  # open/closed
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)


# ── New ENV check models (Amiral CSM 2026) ───────────────────────────

class EnvCylindersCheck(db.Model):
    __tablename__ = "env_cylinders_check"
    id                = db.Column(db.Integer, primary_key=True)
    officer_id        = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id        = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date              = db.Column(db.Date, nullable=False)
    package           = db.Column(db.String(20), nullable=True)
    location          = db.Column(db.String(120), nullable=True)
    vertical_secured  = db.Column(db.Boolean, default=False)
    empty_separated   = db.Column(db.Boolean, default=False)
    caps_on           = db.Column(db.Boolean, default=False)
    o2_separation_ok  = db.Column(db.Boolean, default=False)
    measured_distance_m = db.Column(db.Float, nullable=True)
    temp_ok           = db.Column(db.Boolean, default=False)
    temp_reading      = db.Column(db.Float, nullable=True)
    no_leaks          = db.Column(db.Boolean, default=False)
    area_ventilated   = db.Column(db.Boolean, default=False)
    no_smoking_sign   = db.Column(db.Boolean, default=False)
    notes             = db.Column(db.Text, nullable=True)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)


class EnvNormCheck(db.Model):
    __tablename__ = "env_norm_check"
    id                  = db.Column(db.Integer, primary_key=True)
    officer_id          = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id          = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date                = db.Column(db.Date, nullable=False)
    package             = db.Column(db.String(20), nullable=True)
    location            = db.Column(db.String(120), nullable=True)
    rpo_designated      = db.Column(db.Boolean, default=False)
    rpo_name            = db.Column(db.String(80), nullable=True)
    rpo_contact         = db.Column(db.String(40), nullable=True)
    epd_permit_ok       = db.Column(db.Boolean, default=False)
    norm_records_ok     = db.Column(db.Boolean, default=False)
    ndt_permit_ok       = db.Column(db.Boolean, default=False)
    dose_logs_ok        = db.Column(db.Boolean, default=False)
    norm_equip_handled  = db.Column(db.Boolean, default=False)
    no_exposed_sources  = db.Column(db.Boolean, default=False)
    radiation_levels_ok = db.Column(db.Boolean, default=False)
    ndt_active          = db.Column(db.Boolean, default=False)
    ndt_permit_no       = db.Column(db.String(40), nullable=True)
    ndt_source_type     = db.Column(db.String(40), nullable=True)
    exclusion_zone_ok   = db.Column(db.Boolean, default=False)
    notes               = db.Column(db.Text, nullable=True)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)


class EnvBlastingCheck(db.Model):
    __tablename__ = "env_blasting_check"
    id                = db.Column(db.Integer, primary_key=True)
    officer_id        = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id        = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date              = db.Column(db.Date, nullable=False)
    package           = db.Column(db.String(20), nullable=True)
    location          = db.Column(db.String(120), nullable=True)
    abrasive_type     = db.Column(db.String(80), nullable=True)
    no_silica_sand    = db.Column(db.Boolean, default=False)
    abrasive_approved = db.Column(db.Boolean, default=False)
    waste_collected   = db.Column(db.Boolean, default=False)
    no_scatter        = db.Column(db.Boolean, default=False)
    barriers_ok       = db.Column(db.Boolean, default=False)
    records_updated   = db.Column(db.Boolean, default=False)
    notes             = db.Column(db.Text, nullable=True)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)


class EnvRoadsCheck(db.Model):
    __tablename__ = "env_roads_check"
    id                  = db.Column(db.Integer, primary_key=True)
    officer_id          = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id          = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date                = db.Column(db.Date, nullable=False)
    package             = db.Column(db.String(20), nullable=True)
    location            = db.Column(db.String(120), nullable=True)
    dust_control_active = db.Column(db.Boolean, default=False)
    watering_roads      = db.Column(db.Boolean, default=False)
    trucks_covered      = db.Column(db.Boolean, default=False)
    debris_removed      = db.Column(db.Boolean, default=False)
    warnings_ok         = db.Column(db.Boolean, default=False)
    no_runoff_to_wadi   = db.Column(db.Boolean, default=False)
    notes               = db.Column(db.Text, nullable=True)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)


class EnvPressureCheck(db.Model):
    __tablename__ = "env_pressure_check"
    id                       = db.Column(db.Integer, primary_key=True)
    officer_id               = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id               = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date                     = db.Column(db.Date, nullable=False)
    package                  = db.Column(db.String(20), nullable=True)
    test_item                = db.Column(db.String(120), nullable=True)
    water_volume_m3          = db.Column(db.Float, nullable=True)
    disposal_plan_approved   = db.Column(db.Boolean, default=False)
    no_direct_soil_discharge = db.Column(db.Boolean, default=False)
    treated_before_discharge = db.Column(db.Boolean, default=False)
    settling_pond_used       = db.Column(db.Boolean, default=False)
    containment_berm_ok      = db.Column(db.Boolean, default=False)
    ph_reading               = db.Column(db.Float, nullable=True)
    chemical_additives       = db.Column(db.String(80), nullable=True)
    quality_ok               = db.Column(db.Boolean, default=False)
    notes                    = db.Column(db.Text, nullable=True)
    created_at               = db.Column(db.DateTime, default=datetime.utcnow)


class EnvJettingCheck(db.Model):
    __tablename__ = "env_jetting_check"
    id                  = db.Column(db.Integer, primary_key=True)
    officer_id          = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id          = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date                = db.Column(db.Date, nullable=False)
    package             = db.Column(db.String(20), nullable=True)
    location            = db.Column(db.String(120), nullable=True)
    pressure_bar        = db.Column(db.Integer, nullable=True)
    water_volume_m3     = db.Column(db.Float, nullable=True)
    containment_ok      = db.Column(db.Boolean, default=False)
    vacuum_truck_on_site = db.Column(db.Boolean, default=False)
    berm_around_area    = db.Column(db.Boolean, default=False)
    waste_disposed      = db.Column(db.Boolean, default=False)
    sludge_collected    = db.Column(db.Boolean, default=False)
    hazardous_checked   = db.Column(db.Boolean, default=False)
    manifest_completed  = db.Column(db.Boolean, default=False)
    notes               = db.Column(db.Text, nullable=True)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)


class EnvWeldingCheck(db.Model):
    __tablename__ = "env_welding_check"
    id                    = db.Column(db.Integer, primary_key=True)
    officer_id            = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id            = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date                  = db.Column(db.Date, nullable=False)
    package               = db.Column(db.String(20), nullable=True)
    activity_type         = db.Column(db.String(60), nullable=True)
    welding_rods_stored   = db.Column(db.Boolean, default=False)
    spent_rods_collected  = db.Column(db.Boolean, default=False)
    fume_extraction       = db.Column(db.Boolean, default=False)
    no_open_burning       = db.Column(db.Boolean, default=False)
    paint_waste_contained = db.Column(db.Boolean, default=False)
    no_paint_soil_discharge = db.Column(db.Boolean, default=False)
    msds_available        = db.Column(db.Boolean, default=False)
    hazwaste_labeled      = db.Column(db.Boolean, default=False)
    notes                 = db.Column(db.Text, nullable=True)
    created_at            = db.Column(db.DateTime, default=datetime.utcnow)


class EnvDemolitionCheck(db.Model):
    __tablename__ = "env_demolition_check"
    id                    = db.Column(db.Integer, primary_key=True)
    officer_id            = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id            = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date                  = db.Column(db.Date, nullable=False)
    package               = db.Column(db.String(20), nullable=True)
    location              = db.Column(db.String(120), nullable=True)
    asbestos_survey_done  = db.Column(db.Boolean, default=False)
    no_asbestos_found     = db.Column(db.Boolean, default=False)
    asbestos_permit_ok    = db.Column(db.Boolean, default=False)
    debris_sorted         = db.Column(db.Boolean, default=False)
    no_burning_debris     = db.Column(db.Boolean, default=False)
    transport_manifest_ok = db.Column(db.Boolean, default=False)
    dust_suppression      = db.Column(db.Boolean, default=False)
    notes                 = db.Column(db.Text, nullable=True)
    created_at            = db.Column(db.DateTime, default=datetime.utcnow)


# ── New Welfare daily check models (Amiral WSSM / SAEHC) ─────────────

class WelfareHeatCheck(db.Model):
    __tablename__ = "welfare_heat_check"
    id                      = db.Column(db.Integer, primary_key=True)
    officer_id              = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id              = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date                    = db.Column(db.Date, nullable=False)
    package                 = db.Column(db.String(20), nullable=True)
    wbgt_morning            = db.Column(db.Float, nullable=True)
    wbgt_noon               = db.Column(db.Float, nullable=True)
    max_temp_c              = db.Column(db.Float, nullable=True)
    water_available         = db.Column(db.Boolean, default=False)
    shade_within_100m       = db.Column(db.Boolean, default=False)
    rest_breaks_enforced    = db.Column(db.Boolean, default=False)
    midday_ban_enforced     = db.Column(db.Boolean, default=False)
    buddy_system_ok         = db.Column(db.Boolean, default=False)
    medic_on_site           = db.Column(db.Boolean, default=False)
    acclimatization_new     = db.Column(db.Boolean, default=False)
    heat_cases_count        = db.Column(db.Integer, default=0)
    notes                   = db.Column(db.Text, nullable=True)
    created_at              = db.Column(db.DateTime, default=datetime.utcnow)


class WelfareFirstaidCheck(db.Model):
    __tablename__ = "welfare_firstaid_check"
    id                    = db.Column(db.Integer, primary_key=True)
    officer_id            = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id            = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date                  = db.Column(db.Date, nullable=False)
    package               = db.Column(db.String(20), nullable=True)
    medic_present         = db.Column(db.Boolean, default=False)
    jhah_contact_available = db.Column(db.Boolean, default=False)
    bls_certified_workers = db.Column(db.Boolean, default=False)
    first_aid_kits_ok     = db.Column(db.Boolean, default=False)
    kits_stocked          = db.Column(db.Boolean, default=False)
    kits_accessible       = db.Column(db.Boolean, default=False)
    kit_10unit_count      = db.Column(db.Integer, default=0)
    kit_36unit_count      = db.Column(db.Integer, default=0)
    aed_available         = db.Column(db.Boolean, default=False)
    evacuation_route_marked = db.Column(db.Boolean, default=False)
    ambulance_access_ok   = db.Column(db.Boolean, default=False)
    notes                 = db.Column(db.Text, nullable=True)
    created_at            = db.Column(db.DateTime, default=datetime.utcnow)


class WelfareSanitationCheck(db.Model):
    __tablename__ = "welfare_sanitation_check"
    id                  = db.Column(db.Integer, primary_key=True)
    officer_id          = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id          = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    date                = db.Column(db.Date, nullable=False)
    package             = db.Column(db.String(20), nullable=True)
    location            = db.Column(db.String(120), nullable=True)
    worker_count        = db.Column(db.Integer, nullable=True)
    toilet_count        = db.Column(db.Integer, nullable=True)
    washing_units       = db.Column(db.Integer, nullable=True)
    toilet_ratio_met    = db.Column(db.Boolean, default=False)
    chlorine_ppm        = db.Column(db.Float, nullable=True)
    chlorine_ok         = db.Column(db.Boolean, default=False)
    toilets_clean       = db.Column(db.Boolean, default=False)
    soap_paper_available = db.Column(db.Boolean, default=False)
    waste_bins_emptied  = db.Column(db.Boolean, default=False)
    no_standing_water   = db.Column(db.Boolean, default=False)
    pest_control_ok     = db.Column(db.Boolean, default=False)
    notes               = db.Column(db.Text, nullable=True)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)


class WelfareAccommodation(db.Model):
    __tablename__ = "welfare_accommodation"
    id                       = db.Column(db.Integer, primary_key=True)
    officer_id               = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id               = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    inspect_date             = db.Column(db.Date, nullable=False)
    camp_name                = db.Column(db.String(80), nullable=False)
    capacity                 = db.Column(db.Integer, nullable=True)
    occupancy                = db.Column(db.Integer, nullable=True)
    cleanliness              = db.Column(db.Integer, nullable=True)
    facilities               = db.Column(db.Integer, nullable=True)
    safety_rating            = db.Column(db.Integer, nullable=True)
    space_per_person_ok      = db.Column(db.Boolean, default=False)
    no_triple_bunks          = db.Column(db.Boolean, default=False)
    ac_24_7_working          = db.Column(db.Boolean, default=False)
    pest_control_ok          = db.Column(db.Boolean, default=False)
    occupancy_within_capacity = db.Column(db.Boolean, default=False)
    emergency_exits_ok       = db.Column(db.Boolean, default=False)
    issues_found             = db.Column(db.Text, nullable=True)
    action_needed            = db.Column(db.Text, nullable=True)
    created_at               = db.Column(db.DateTime, default=datetime.utcnow)

class WelfareTransport(db.Model):
    __tablename__ = "welfare_transport"
    id                       = db.Column(db.Integer, primary_key=True)
    officer_id               = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company_id               = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    log_date                 = db.Column(db.Date, nullable=False)
    vehicle_id               = db.Column(db.String(30), nullable=False)
    route                    = db.Column(db.String(120), nullable=True)
    passengers               = db.Column(db.Integer, nullable=True)
    capacity                 = db.Column(db.Integer, nullable=True)
    driver_name              = db.Column(db.String(80), nullable=True)
    condition_ok             = db.Column(db.Boolean, default=True)
    ac_working               = db.Column(db.Boolean, default=False)
    seatbelts_ok             = db.Column(db.Boolean, default=False)
    capacity_not_exceeded    = db.Column(db.Boolean, default=False)
    driver_license_valid     = db.Column(db.Boolean, default=False)
    no_standing_passengers   = db.Column(db.Boolean, default=False)
    issues_found             = db.Column(db.Text, nullable=True)
    created_at               = db.Column(db.DateTime, default=datetime.utcnow)


@app.context_processor
def inject_welfare_flags():
    from flask import g as _g
    u = getattr(_g, "user", None)
    try:
        pending_q = 0
        role = getattr(u, "role", None)
        if role in ("safety_officer", "safety_supervisor", "admin", "super_admin") and u:
            try:
                q = WlfQuery.query.filter(WlfQuery.status == "open")
                if role == "safety_officer":
                    q = q.filter(WlfQuery.assignee_id == u.id)
                else:
                    _c = cid()
                    if _c:
                        q = q.filter(WlfQuery.company_id == _c)
                pending_q = q.count()
            except Exception:
                pending_q = 0
        return {"g_is_welfare": is_safety_welfare(u),
                "g_is_welfare_viewer": (is_welfare_viewer(u) and not is_safety_welfare(u)),
                "g_wlf_pending_queries": pending_q}
    except Exception:
        return {"g_is_welfare": False, "g_is_welfare_viewer": False,
                "g_wlf_pending_queries": 0}


@app.context_processor
def inject_env_flags():
    from flask import g as _g
    try:
        u = getattr(_g, "user", None)
        return {"g_is_env": is_environment_officer(u),
                "g_is_env_viewer": (is_environment_viewer(u) and not is_environment_officer(u))}
    except Exception:
        return {"g_is_env": False, "g_is_env_viewer": False}


# ── Welfare helpers ──────────────────────────────────────────────────

def _wlf_today():
    return datetime.now(RIYADH_TZ).date()


def _wlf_officers():
    """Welfare trainees in the current company."""
    q = User.query.filter(User.role == "safety_welfare", User.is_active == True)
    _c = cid()
    if _c:
        q = q.filter(User.company_id == _c)
    return q.order_by(User.name.asc()).all()


def _all_officers_scoped():
    """All 3 officer types visible to current supervisor (for report pickers).
    safety_supervisor → only their OfficerTeam assignments.
    Others → all officers in company."""
    OFFICER_ROLES = ("safety_officer", "safety_welfare", "environment_officer")
    u = g.user
    if u and u.role == "safety_supervisor":
        assigned_ids = [
            row.officer_id
            for row in OfficerTeam.query.filter_by(supervisor_id=u.id).all()
        ]
        if not assigned_ids:
            return []
        return User.query.filter(
            User.id.in_(assigned_ids),
            User.role.in_(OFFICER_ROLES),
            User.is_active == True,
        ).order_by(User.role, User.name).all()
    _c = cid()
    q = User.query.filter(User.role.in_(OFFICER_ROLES), User.is_active == True)
    if _c:
        q = q.filter(User.company_id == _c)
    return q.order_by(User.role, User.name).all()


def _wlf_officers_scoped():
    """Returns welfare officers visible to the current user.
    safety_supervisor → only officers assigned to them via OfficerTeam.
    admin / safety_manager / others → all welfare officers in company."""
    u = g.user
    if u and u.role == "safety_supervisor":
        assigned_ids = [
            row.officer_id
            for row in OfficerTeam.query.filter_by(supervisor_id=u.id).all()
        ]
        if not assigned_ids:
            return []
        q = User.query.filter(
            User.id.in_(assigned_ids),
            User.role == "safety_welfare",
            User.is_active == True,
        )
        return q.order_by(User.name.asc()).all()
    return _wlf_officers()



def _wlf_ensure_progress(uid):
    """Creates the 14 level rows on first visit (one per training chapter). L1 starts active."""
    existing = {p.level_code: p for p in WlfProgress.query.filter_by(officer_id=uid).all()}
    if len(existing) >= len(WLF_LEVEL_CODES):
        return existing
    changed = False
    for i, code in enumerate(WLF_LEVEL_CODES):
        if code not in existing:
            p = WlfProgress(officer_id=uid, company_id=cid(), level_code=code,
                            status=("active" if i == 0 else "locked"))
            db.session.add(p)
            existing[code] = p
            changed = True
    if changed:
        db.session.commit()
    return existing


def _wlf_current_level(uid):
    progs = _wlf_ensure_progress(uid)
    for code in WLF_LEVEL_CODES:
        p = progs.get(code)
        if p and p.status in ("active", "pending"):
            return p
    return progs.get(WLF_LEVEL_CODES[-1])


# ── Environment helpers ──────────────────────────────────────────────

def _env_ensure_progress(uid):
    """Creates the 17 level rows on first visit. E1 starts active."""
    existing = {p.level_code: p for p in EnvProgress.query.filter_by(officer_id=uid).all()}
    if len(existing) >= len(ENV_LEVEL_CODES):
        return existing
    changed = False
    for i, code in enumerate(ENV_LEVEL_CODES):
        if code not in existing:
            p = EnvProgress(officer_id=uid, company_id=cid(), level_code=code,
                            status=("active" if i == 0 else "locked"))
            db.session.add(p)
            existing[code] = p
            changed = True
    if changed:
        db.session.commit()
    return existing


def _env_current_level(uid):
    progs = _env_ensure_progress(uid)
    for code in ENV_LEVEL_CODES:
        p = progs.get(code)
        if p and p.status in ("active", "pending"):
            return p
    return progs.get(ENV_LEVEL_CODES[-1])


def _env_counters(uid, since=None):
    c = {}
    wq = EnvLevelWork.query.filter_by(officer_id=uid)
    if since:
        wq = wq.filter(EnvLevelWork.date >= since)
    works = wq.all()
    c["rounds_submitted"]  = len(works)
    c["rounds_measured"]   = sum(1 for w in works if w.has_measurement)
    fq = WlfFinding.query.filter_by(officer_id=uid)
    if since:
        fq = fq.filter(WlfFinding.date >= since)
    finds = fq.all()
    c["findings_written"]  = len(finds)
    c["findings_closed"]   = sum(1 for f in finds if f.status == "closed")
    wr_q = WlfWeeklyReport.query.filter_by(officer_id=uid)
    if since:
        wr_q = wr_q.filter(WlfWeeklyReport.week_start >= since)
    c["weeks_reported"]    = wr_q.count()
    return c


def _wlf_counters(uid, since=None):
    """All task counters used by level gates and KPIs."""
    c = {}
    wq = WlfLevelWork.query.filter_by(officer_id=uid)
    if since:
        wq = wq.filter(WlfLevelWork.date >= since)
    works = wq.all()
    c["rounds_submitted"] = len(works)
    c["rounds_measured"]  = sum(1 for w in works if w.has_measurement)
    c["units_covered"]    = len(set(w.unit for w in works if w.unit))
    fq = WlfFinding.query.filter_by(officer_id=uid)
    if since:
        fq = fq.filter(WlfFinding.date >= since)
    finds = fq.all()
    c["findings_written"] = len(finds)
    c["findings_closed"]  = sum(1 for f in finds if f.status == "closed")
    cq = WlfComplaint.query.filter_by(officer_id=uid)
    if since:
        cq = cq.filter(WlfComplaint.date >= since)
    c["complaints"] = cq.count()
    wr_q = WlfWeeklyReport.query.filter_by(officer_id=uid)
    if since:
        wr_q = wr_q.filter(WlfWeeklyReport.week_start >= since)
    c["weeks_reported"] = wr_q.count()
    return c


def _wlf_overdue(uid):
    today = _wlf_today()
    return WlfFinding.query.filter(
        WlfFinding.officer_id == uid,
        WlfFinding.status == "open",
        WlfFinding.due_date != None,
        WlfFinding.due_date < today).all()


def _wlf_gate_state(uid):
    """Returns (level, tasks_with_progress, blocked_reason_or_None, all_done)."""
    p = _wlf_current_level(uid)
    lvl = WLF_LEVEL_BY_CODE.get(p.level_code) if p else None
    if not lvl:
        return None, [], None, False
    counters = _wlf_counters(uid)
    tasks, all_done = [], True
    for t in lvl["tasks"]:
        have = counters.get(t["k"], 0)
        done = have >= t["target"]
        all_done = all_done and done
        tasks.append({"label": t["label"], "have": have,
                      "target": t["target"], "done": done})
    overdue = _wlf_overdue(uid)
    blocked = None
    if overdue:
        blocked = f"Clear {len(overdue)} overdue finding(s) to unlock the gate"
    return p, tasks, blocked, all_done



# ── Welfare routes: trainee ──────────────────────────────────────────

@app.route("/welfare/home")
@login_required
@safety_welfare_required
def welfare_home():
    u = cur_user()
    if not is_safety_welfare(u):
        return redirect(url_for("welfare_supervisor"))
    _c = cid()
    today = datetime.now(RIYADH_TZ).date()
    try:
        recent_obs = (HseObservation.query
                      .filter_by(officer_id=u.id, company_id=_c)
                      .order_by(HseObservation.created_at.desc())
                      .limit(10).all())
    except Exception:
        recent_obs = []
    try:
        recent_files = (ReportFile.query
                        .filter_by(uploaded_by=u.id, company_id=_c, role_type="welfare")
                        .order_by(ReportFile.uploaded_at.desc())
                        .limit(10).all())
    except Exception:
        recent_files = []
    try:
        type_options = (ReportTypeOption.query
                        .filter_by(company_id=_c, role_type="welfare")
                        .order_by(ReportTypeOption.name).all())
    except Exception:
        type_options = []
    return render_template("welfare_home.html",
                           today=today,
                           recent_obs=recent_obs,
                           recent_files=recent_files,
                           type_options=type_options)


# ── Welfare officer — observations PDF export ────────────────────────

@app.route("/welfare/observations/export")
@login_required
@safety_welfare_required
def welfare_obs_export_pdf():
    u = cur_user()
    if not is_safety_welfare(u):
        return redirect(url_for("welfare_supervisor"))
    _c = cid()
    date_str = request.args.get("date", "")
    try:
        from datetime import date as _date
        export_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else datetime.now(RIYADH_TZ).date()
    except ValueError:
        export_date = datetime.now(RIYADH_TZ).date()
    obs_list = (HseObservation.query
                .filter_by(officer_id=u.id, company_id=_c)
                .filter(HseObservation.date == export_date)
                .order_by(HseObservation.created_at).all())
    return _obs_export_pdf(u, obs_list, export_date, "Welfare")


@app.route("/env/observations/export")
@login_required
@environment_officer_required
def env_obs_export_pdf():
    u = cur_user()
    if not is_environment_officer(u):
        return redirect(url_for("welfare_supervisor"))
    _c = cid()
    date_str = request.args.get("date", "")
    try:
        export_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else datetime.now(RIYADH_TZ).date()
    except ValueError:
        export_date = datetime.now(RIYADH_TZ).date()
    obs_list = (HseObservation.query
                .filter_by(officer_id=u.id, company_id=_c)
                .filter(HseObservation.date == export_date)
                .order_by(HseObservation.created_at).all())
    return _obs_export_pdf(u, obs_list, export_date, "Environment")


def _obs_export_pdf(u, obs_list, export_date, role_label):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    import io

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet()
    bold = ParagraphStyle("bold", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=11)
    normal = ParagraphStyle("normal", parent=styles["Normal"], fontSize=9, leading=13)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, textColor=colors.grey)

    story = []

    # Header
    story.append(Paragraph(f"NSH SafeTrack — {role_label} Observations Report", bold))
    story.append(Paragraph(f"Officer: {u.name}   |   Date: {export_date.strftime('%d %B %Y')}", small))
    story.append(Spacer(1, 0.3*cm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0")))
    story.append(Spacer(1, 0.3*cm))

    if not obs_list:
        story.append(Paragraph("No observations recorded for this date.", normal))
    else:
        TYPE_LABEL = {"unsafe_act": "Unsafe Act", "unsafe_condition": "Unsafe Condition", "positive": "Positive"}
        RISK_COLOR = {"H": colors.HexColor("#b91c1c"), "M": colors.HexColor("#92400e"), "L": colors.HexColor("#166534")}
        for idx, obs in enumerate(obs_list, 1):
            story.append(Paragraph(f"#{idx}  {TYPE_LABEL.get(obs.obs_type, obs.obs_type)}"
                                   + (f"   [{obs.risk_level}]" if obs.risk_level else ""), bold))
            rows = []
            if obs.location:
                rows.append(["Location", obs.location])
            if obs.category:
                rows.append(["Category", obs.category])
            if obs.description:
                rows.append(["Description", obs.description])
            if obs.action_taken:
                rows.append(["Action Taken", obs.action_taken])
            if rows:
                tbl = Table([[Paragraph(r[0], small), Paragraph(r[1], normal)] for r in rows],
                            colWidths=[3.5*cm, None])
                tbl.setStyle(TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                ]))
                story.append(tbl)
            story.append(Spacer(1, 0.25*cm))
            if idx < len(obs_list):
                story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#f1f5f9")))
                story.append(Spacer(1, 0.15*cm))

    # Summary line
    story.append(Spacer(1, 0.3*cm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0")))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(f"Total: {len(obs_list)} observation(s)", small))

    doc.build(story)
    buf.seek(0)
    fname = f"{role_label.lower()}_obs_{export_date.isoformat()}.pdf"
    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'attachment; filename="{fname}"'
    return resp


# ── helpers ─────────────────────────────────────────────────────────

PDF_UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads", "reports")
os.makedirs(PDF_UPLOAD_DIR, exist_ok=True)

DEFAULT_WELFARE_TYPES = [
    "Camp Visit", "Daily Welfare Round", "Heat Index Check",
    "First Aid Check", "Sanitation Check", "Other",
]
DEFAULT_ENV_TYPES = [
    "Environmental Inspection", "Waste Check", "Dust Check",
    "Water/Soil Check", "Cylinders Check", "Other",
]

ENV_CHECKLISTS = {
    "P2-EC": {
        "title": "Environmental Checklist – Weekly Inspection",
        "appendix": "Appendix 4",
        "report_prefix": "P2-EC",
        "has_na": False,
        "col_good": "In good order / condition",
        "col_imm":  "To be immediately improved",
        "col_imp":  "To be improved",
        "note_col": "Product",
        "compliance_header": "In case of non-compliance, indicate:",
        "sections": [
            {
                "name": "Part 1 – General Site Observations",
                "items": [
                    "Site / Working Areas boundaries are Clearly Identified",
                    "Site access / Entry roads",
                    "Ungraded Buffer Areas",
                    "Signs, Fence, Other",
                    "General Housekeeping (visual)",
                    "Sufficient Site Trash / Garbage / Waste Containers",
                    "Condition of Waste Accumulation Areas (if segregated)",
                    "Condition of Recyclable Solid Waste",
                    "Condition of Non-Hazardous Solid Waste",
                    "Condition of Hazardous Waste Storage Area",
                    "Condition of Sewage tanks / Toilets (Leak or contamination)",
                    "Hazardous Materials / Storage Area",
                    "Properly installed erosion controls (e.g., silt fencing)",
                    "Management of Surface Water",
                ],
            },
            {
                "name": "Part 2 – General Site Observations",
                "items": [
                    "Quality of outfall from sediment basins",
                    "Properly Dewatering of Excavation",
                    "Sediment pond / basin for concrete batching plant",
                    "Hydrostatic tests water discharging",
                    "Equipment being Maintained free of leaks",
                    "Management of Materials at Laydown Area",
                    "Condition of equipment maintenance area",
                    "Management of Refuelling area",
                    "Spill kits with Trucks/Equipment",
                    "Spill kits at Stationary equipment",
                    "Fuel Tanks free of fuel drips/seepage",
                    "Fire Fighting / Response equipment",
                    "Dust Control Measures in place (spraying of water, covers etc.)",
                    "Vehicle Exhaust Emissions",
                ],
            },
        ],
    },
    "P2-WMC": {
        "title": "Waste Management Checklist – Weekly Inspection",
        "appendix": "Appendix 5",
        "report_prefix": "P2-WMC",
        "has_na": True,
        "col_good": "In good order / condition",
        "col_imm":  "To be immediately improved",
        "col_imp":  "To be improved",
        "note_col": "Anomaly / Kind of Waste(s)",
        "compliance_header": "In case of anomaly, to be indicated:",
        "sections": [
            {
                "name": "1 – Collection and Deposits",
                "items": [
                    "Wastes are disposed of in the relevant container / areas.",
                    "Containers / waste areas - clearly labeled for identification",
                    "Containers / waste areas are adequate to waste types",
                    "Containers / waste areas - adequate to vol. of waste produced.",
                    "Containers are provided by appropriate closing to prevent leaking",
                    "Containers are provided by suitable devices for safe handling, filling and emptying",
                    "Non-Hazardous waste storage does not last more than a year and its volume is not exceeding 20 m3.",
                    "Hazardous waste storage does not last more than a year and its volume is not exceeding 10 m3.",
                    "Foul smell, presence of animals (rats, birds) is noticed?",
                    "Waste piles are adequately protected from wind and rain",
                ],
            },
            {
                "name": "2 – Waste Management Documents",
                "items": [
                    '"Waste Manifest" is correctly filled in, as per local laws and COMPANY requirements',
                    "Waste area maps are available and clearly shows the different types of waste storage",
                    "Waste production is recorded on the relevant form",
                    "Waste are disposed of at approved disposal areas / facilities?",
                    "Waste collection is organized by separating different types of wastes (hazardous, not hazardous, recyclable etc.)",
                    "The waste disposal is compliant with local environmental law and without environmental risks.",
                    "CONTRACTOR and SUBCONTRACTOR personnel are trained and are aware of environmental care.",
                ],
            },
        ],
    },
    "P2-SPC": {
        "title": "Substances and Products Checklist – Weekly Inspection",
        "appendix": "Appendix 6",
        "report_prefix": "P2-SPC",
        "has_na": False,
        "col_good": "In good order / condition",
        "col_imm":  "To be immediately improved",
        "col_imp":  "To be improved",
        "note_col": "Product",
        "compliance_header": "In case of non-compliance, indicate:",
        "sections": [
            {
                "name": "1 – Storage",
                "items": [
                    "Storage facility / mixing shelter is inspected and confirmed to be in good condition.",
                    "SDS, Risk Assessment & inventory available for every substance or chemical product",
                    "Chemicals are well stored, identified for typology.",
                    "Storage takes place in appropriate containing tanks.",
                    "All containers are original, not deteriorated, sealed and labelled.",
                    "No expired products stored inside",
                    "Fuel and oil tanks are situated on specific containing tanks",
                    "Secondary containment tanks are appropriate with 110% capacity and well kept",
                    "Fire protection devices in compliance with SDS are available",
                    "Spill kits are available and inspected using dedicated checklist",
                    "Instructions about use of adsorbent materials are available.",
                    "The ground, waters, sewer systems etc. are free from spills?",
                    "Pouring devices used are appropriate or safe for users and env.",
                    "Natural / Mechanical Ventilation in place and is effective.",
                    "All containers have caps and are closed when not in use.",
                    "No half-cut chemical containers are used as paint buckets.",
                    "Storage is 'numbered' and has 'No Naked Flame', 'No Smoking', 'Authorized Person' 'Hazardous Material' notice posted and clearly visible?",
                    "No other materials including combustibles are stored inside.",
                ],
            },
        ],
    },
}


def _seed_report_types(company_id):
    for name in DEFAULT_WELFARE_TYPES:
        if not ReportTypeOption.query.filter_by(
                company_id=company_id, role_type="welfare", name=name).first():
            db.session.add(ReportTypeOption(
                company_id=company_id, role_type="welfare", name=name))
    for name in DEFAULT_ENV_TYPES:
        if not ReportTypeOption.query.filter_by(
                company_id=company_id, role_type="environment", name=name).first():
            db.session.add(ReportTypeOption(
                company_id=company_id, role_type="environment", name=name))
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


def _save_report_pdf(file, company_id):
    """Save uploaded PDF; return relative path or None on failure."""
    if not file or not file.filename:
        return None
    fname = file.filename.lower()
    if not fname.endswith(".pdf"):
        return None
    from werkzeug.utils import secure_filename
    safe = secure_filename(file.filename)
    uid = uuid.uuid4().hex[:10]
    final_name = f"rpt_{uid}_{safe}"
    subfolder = os.path.join(PDF_UPLOAD_DIR, str(company_id or "0"))
    os.makedirs(subfolder, exist_ok=True)
    file.save(os.path.join(subfolder, final_name))
    return f"{company_id or 0}/{final_name}"


# ── Welfare/Env officer — observations list & close ─────────────────

def _is_welfare_env_officer(u):
    return getattr(u, "role", None) in ("safety_welfare", "environment_officer")


def _welfare_env_or_supervisor(u):
    return getattr(u, "role", None) in (
        "safety_welfare", "environment_officer",
        "welfare_supervisor", "safety_supervisor",
        "safety_manager", "admin", "super_admin",
    )


def welfare_env_access_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        u = cur_user()
        if not u or not _welfare_env_or_supervisor(u):
            flash("Access denied.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return inner


@app.route("/welfare/observations")
@login_required
def welfare_observation_list():
    return redirect(url_for("hse_observations"))


@app.route("/welfare/observations/_old")
@login_required
@welfare_env_access_required
def welfare_observation_list_old():
    u = cur_user()
    _c = cid()
    status_filter = request.args.get("status", "open")
    page = request.args.get("page", 1, type=int)

    q = HseObservation.query.filter_by(company_id=_c).join(
        User, User.id == HseObservation.officer_id)

    if _is_welfare_env_officer(u):
        # Officers see only their own observations
        q = q.filter(HseObservation.officer_id == u.id)
    else:
        # Supervisors/admins see welfare + environment officers
        q = q.filter(User.role.in_(["safety_welfare", "environment_officer"]))

    if status_filter in ("open", "closed"):
        q = q.filter(HseObservation.status == status_filter)

    pagination = q.order_by(HseObservation.date.desc()).paginate(
        page=page, per_page=20, error_out=False)
    obs_list = pagination.items
    obs_photos = {o.id: list(o.photos) for o in obs_list}
    return render_template("welfare_observations.html",
                           obs_list=obs_list, obs_photos=obs_photos,
                           status_filter=status_filter, pagination=pagination)


@app.route("/welfare/observation/<int:obs_id>/close", methods=["GET", "POST"])
@login_required
@welfare_env_access_required
def welfare_observation_close(obs_id):
    u = cur_user()
    _c = cid()
    obs = HseObservation.query.filter_by(id=obs_id, company_id=_c).first_or_404()
    # Only the submitting officer (or a supervisor) can close
    if _is_welfare_env_officer(u) and obs.officer_id != u.id:
        abort(403)
    if obs.status == "closed":
        flash("Already closed.", "info")
        return redirect(url_for("welfare_observation_list", status="closed"))
    if request.method == "POST":
        closure_action = request.form.get("closure_action", "").strip()
        if not closure_action:
            flash("Closure note is required.", "warning")
        else:
            f = request.files.get("closure_photo")
            path = _save_hse_photo(f, "wlf_close", company_id=_c)
            if path:
                db.session.add(HseObservationPhoto(
                    observation_id=obs.id, photo_path=path,
                    photo_type="after", company_id=_c))
            obs.status = "closed"
            obs.closed_at = datetime.now(RIYADH_TZ).date()
            obs.closure_action = closure_action
            db.session.commit()
            flash("Observation closed.", "success")
            return redirect(url_for("welfare_observation_list", status="closed"))
    return render_template("welfare_observation_close.html", obs=obs)


# ── Welfare officer — observation ────────────────────────────────────

@app.route("/welfare/observation/new", methods=["GET", "POST"])
@login_required
@safety_welfare_required
def welfare_observation_new():
    u = cur_user()
    if not is_safety_welfare(u):
        return redirect(url_for("welfare_supervisor"))
    _c = cid()
    today = datetime.now(RIYADH_TZ).date()
    if request.method == "POST":
        obs_type = request.form.get("obs_type", "").strip()
        if not obs_type:
            flash("Observation type is required.", "warning")
            return redirect(url_for("welfare_observation_new"))
        obs = HseObservation(
            officer_id=u.id, date=today,
            location=request.form.get("location", "").strip(),
            obs_type=obs_type,
            category=request.form.get("category", "").strip(),
            risk_level=request.form.get("risk_level") or None,
            description=request.form.get("description", "").strip(),
            action_taken=request.form.get("action_taken", "").strip(),
            company_id=_c,
        )
        db.session.add(obs)
        db.session.flush()
        for i in range(1, 4):
            f = request.files.get(f"photo_{i}")
            path = _save_hse_photo(f, "wlf_obs", company_id=_c)
            if path:
                db.session.add(HseObservationPhoto(
                    observation_id=obs.id, photo_path=path, photo_type="before",
                    company_id=_c))
        db.session.commit()
        flash("Observation saved.", "success")
        return redirect(url_for("welfare_home"))
    return render_template("welfare_observation_new.html",
                           today=today, categories=OBS_CATEGORIES)


# ── Welfare officer — PDF report upload ──────────────────────────────

@app.route("/welfare/report/upload", methods=["GET", "POST"])
@login_required
@safety_welfare_required
def welfare_report_upload():
    u = cur_user()
    if not is_safety_welfare(u):
        return redirect(url_for("welfare_supervisor"))
    _c = cid()
    _seed_report_types(_c)
    type_options = (ReportTypeOption.query
                    .filter_by(company_id=_c, role_type="welfare")
                    .order_by(ReportTypeOption.name).all())
    if request.method == "POST":
        file = request.files.get("pdf_file")
        report_type = request.form.get("report_type", "").strip()
        new_type = request.form.get("new_type", "").strip()
        report_date = _safe_date(request.form.get("report_date")) or datetime.now(RIYADH_TZ).date()
        notes = request.form.get("notes", "").strip()

        if new_type:
            report_type = new_type
            if not ReportTypeOption.query.filter_by(
                    company_id=_c, role_type="welfare", name=new_type).first():
                db.session.add(ReportTypeOption(
                    company_id=_c, role_type="welfare", name=new_type,
                    created_by=u.id))
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()

        if not report_type:
            flash("Please select or enter a report type.", "warning")
            return redirect(url_for("welfare_report_upload"))

        path = _save_report_pdf(file, _c)
        if not path:
            flash("Please upload a valid PDF file (max 10 MB).", "warning")
            return redirect(url_for("welfare_report_upload"))

        rf = ReportFile(uploaded_by=u.id, company_id=_c, role_type="welfare",
                        report_type=report_type, file_path=path,
                        report_date=report_date, notes=notes)
        db.session.add(rf)
        db.session.commit()
        flash("Report uploaded successfully.", "success")
        return redirect(url_for("welfare_home"))

    return render_template("welfare_report_upload.html",
                           type_options=type_options,
                           today=datetime.now(RIYADH_TZ).date())


@app.route("/welfare/report/<int:rid>/view")
@login_required
@safety_welfare_required
def welfare_report_view(rid):
    _c = cid()
    rf = ReportFile.query.filter_by(id=rid, company_id=_c, role_type="welfare").first_or_404()
    u = cur_user()
    if is_safety_welfare(u) and rf.uploaded_by != u.id:
        abort(403)
    full_path = os.path.join(PDF_UPLOAD_DIR, rf.file_path)
    if not os.path.exists(full_path):
        flash("File not found.", "danger")
        return redirect(url_for("welfare_home"))
    return send_file(full_path, mimetype="application/pdf",
                     download_name=os.path.basename(rf.file_path))


@app.route("/welfare/report/<int:rid>/delete", methods=["POST"])
@login_required
@welfare_supervisor_required
def welfare_report_delete(rid):
    _c = cid()
    rf = ReportFile.query.filter_by(id=rid, company_id=_c, role_type="welfare").first_or_404()
    try:
        full_path = os.path.join(PDF_UPLOAD_DIR, rf.file_path)
        if os.path.exists(full_path):
            os.remove(full_path)
    except Exception:
        pass
    db.session.delete(rf)
    db.session.commit()
    flash("Report deleted.", "success")
    return redirect(url_for("welfare_supervisor"))


# ── Welfare supervisor — unified view ────────────────────────────────


@app.route("/welfare/supervisor")
@login_required
@welfare_supervisor_required
def welfare_supervisor():
    _c = cid()
    role_filter = request.args.get("role", "all")   # all / welfare / environment
    date_from = _safe_date(request.args.get("from"))
    date_to   = _safe_date(request.args.get("to"))

    obs_q = (HseObservation.query.filter_by(company_id=_c)
             .join(User, User.id == HseObservation.officer_id))
    if role_filter == "welfare":
        obs_q = obs_q.filter(User.role == "safety_welfare")
    elif role_filter == "environment":
        obs_q = obs_q.filter(User.role == "environment_officer")
    else:
        obs_q = obs_q.filter(User.role.in_(["safety_welfare", "environment_officer"]))
    if date_from:
        obs_q = obs_q.filter(HseObservation.date >= date_from)
    if date_to:
        obs_q = obs_q.filter(HseObservation.date <= date_to)
    observations = obs_q.order_by(HseObservation.created_at.desc()).limit(200).all()

    files_q = ReportFile.query.filter_by(company_id=_c)
    if role_filter in ("welfare", "environment"):
        files_q = files_q.filter_by(role_type=role_filter)
    if date_from:
        files_q = files_q.filter(ReportFile.report_date >= date_from)
    if date_to:
        files_q = files_q.filter(ReportFile.report_date <= date_to)
    report_files = files_q.order_by(ReportFile.uploaded_at.desc()).limit(200).all()

    cl_q = EnvChecklist.query.filter_by(company_id=_c)
    if role_filter == "welfare":
        cl_q = cl_q.filter(db.literal(False))  # checklists are env-only
    if date_from:
        cl_q = cl_q.filter(EnvChecklist.filled_date >= date_from)
    if date_to:
        cl_q = cl_q.filter(EnvChecklist.filled_date <= date_to)
    checklists = cl_q.order_by(EnvChecklist.created_at.desc()).limit(200).all()

    return render_template("welfare_supervisor.html",
                           observations=observations,
                           report_files=report_files,
                           checklists=checklists,
                           role_filter=role_filter,
                           date_from=date_from, date_to=date_to)


@app.get("/welfare/supervisor/pdf")
@login_required
@welfare_supervisor_required
def welfare_supervisor_pdf():
    """PDF summary — observations + checklists + uploaded reports for selected filters."""
    import io
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    _c = cid()
    role_filter = request.args.get("role", "all")
    date_from   = _safe_date(request.args.get("from"))
    date_to     = _safe_date(request.args.get("to"))

    # ── fetch data (same logic as welfare_supervisor view) ──────────────
    obs_q = (HseObservation.query.filter_by(company_id=_c)
             .join(User, User.id == HseObservation.officer_id))
    if role_filter == "welfare":
        obs_q = obs_q.filter(User.role == "safety_welfare")
    elif role_filter == "environment":
        obs_q = obs_q.filter(User.role == "environment_officer")
    else:
        obs_q = obs_q.filter(User.role.in_(["safety_welfare", "environment_officer"]))
    if date_from:
        obs_q = obs_q.filter(HseObservation.date >= date_from)
    if date_to:
        obs_q = obs_q.filter(HseObservation.date <= date_to)
    observations = obs_q.order_by(HseObservation.date.desc()).all()

    files_q = ReportFile.query.filter_by(company_id=_c)
    if role_filter in ("welfare", "environment"):
        files_q = files_q.filter_by(role_type=role_filter)
    if date_from:
        files_q = files_q.filter(ReportFile.report_date >= date_from)
    if date_to:
        files_q = files_q.filter(ReportFile.report_date <= date_to)
    report_files = files_q.order_by(ReportFile.report_date.desc()).all()

    cl_q = EnvChecklist.query.filter_by(company_id=_c)
    if role_filter == "welfare":
        cl_q = cl_q.filter(db.literal(False))
    if date_from:
        cl_q = cl_q.filter(EnvChecklist.filled_date >= date_from)
    if date_to:
        cl_q = cl_q.filter(EnvChecklist.filled_date <= date_to)
    checklists = cl_q.order_by(EnvChecklist.filled_date.desc()).all()

    # ── PDF setup ───────────────────────────────────────────────────────
    buf  = io.BytesIO()
    PAGE = landscape(A4)
    LM = RM = TM = BM = 12 * mm
    W = PAGE[0] - LM - RM

    doc = SimpleDocTemplate(buf, pagesize=PAGE,
                            leftMargin=LM, rightMargin=RM,
                            topMargin=TM, bottomMargin=BM)
    styles  = getSampleStyleSheet()
    _AR     = pdf_arabic_font()
    _AR_B   = "Arabic-Bold" if _AR == "Arabic" else "Helvetica-Bold"

    def _ps(name, size=8, leading=11, bold=False, color=None, align=0):
        return ParagraphStyle(name, parent=styles["Normal"],
                              fontSize=size, leading=leading,
                              fontName=(_AR_B if bold else _AR),
                              textColor=color or colors.black,
                              alignment=align)

    HDR_BG  = colors.HexColor("#1E293B")
    ALT_BG  = colors.HexColor("#F8FAFC")
    BDR     = colors.HexColor("#CBD5E1")
    SEC_BG  = colors.HexColor("#EFF6FF")
    TYPE_MAP = {"unsafe_act": "Unsafe Act", "unsafe_condition": "Unsafe Condition", "positive": "Positive"}
    RISK_COLOR = {"H": colors.HexColor("#FCA5A5"), "M": colors.HexColor("#FDE68A"), "L": colors.HexColor("#86EFAC")}

    title_s = _ps("wt", 14, 18, bold=True)
    sub_s   = _ps("ws",  9, 12, color=colors.HexColor("#6B7280"))
    sec_s   = _ps("wsc",10, 14, bold=True, color=colors.HexColor("#1E40AF"))
    cell_s  = _ps("wc",  8, 11)
    hdr_s   = _ps("wh",  8, 11, bold=True, color=colors.white)

    def hdr_row(labels):
        return [Paragraph(l, hdr_s) for l in labels]

    def tbl_style(extra=None):
        base = [
            ("BACKGROUND",    (0, 0), (-1, 0),  HDR_BG),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, ALT_BG]),
            ("GRID",          (0, 0), (-1, -1),  0.35, BDR),
            ("VALIGN",        (0, 0), (-1, -1),  "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1),  3),
            ("BOTTOMPADDING", (0, 0), (-1, -1),  3),
            ("LEFTPADDING",   (0, 0), (-1, -1),  3),
            ("RIGHTPADDING",  (0, 0), (-1, -1),  3),
            ("FONTSIZE",      (0, 0), (-1, -1),  8),
        ]
        if extra:
            base += extra
        return TableStyle(base)

    story = []

    # ── Title ────────────────────────────────────────────────────────────
    role_label = {"all": "Welfare & Environment", "welfare": "Welfare", "environment": "Environment"}.get(role_filter, "All")
    date_label = ""
    if date_from and date_to:
        date_label = f"  ·  {date_from.strftime('%d %b %Y')} — {date_to.strftime('%d %b %Y')}"
    elif date_from:
        date_label = f"  ·  From {date_from.strftime('%d %b %Y')}"
    elif date_to:
        date_label = f"  ·  Up to {date_to.strftime('%d %b %Y')}"

    story.append(Paragraph(f"{role_label} Reports", title_s))
    story.append(Paragraph(f"Generated {datetime.now(RIYADH_TZ).strftime('%d %b %Y  %H:%M')}{date_label}", sub_s))
    story.append(Spacer(1, 6 * mm))

    # ── Summary row ──────────────────────────────────────────────────────
    sum_data = [[
        Paragraph("Observations", _ps("sk", 8, 11, bold=True, color=colors.HexColor("#1D4ED8"))),
        Paragraph("Checklists",   _ps("sk2",8, 11, bold=True, color=colors.HexColor("#16A34A"))),
        Paragraph("PDF Reports",  _ps("sk3",8, 11, bold=True, color=colors.HexColor("#CA8A04"))),
    ], [
        Paragraph(str(len(observations)), _ps("sv",  16, 20, bold=True, color=colors.HexColor("#1D4ED8"), align=1)),
        Paragraph(str(len(checklists)),   _ps("sv2", 16, 20, bold=True, color=colors.HexColor("#16A34A"), align=1)),
        Paragraph(str(len(report_files)), _ps("sv3", 16, 20, bold=True, color=colors.HexColor("#CA8A04"), align=1)),
    ]]
    sum_col = [W / 3] * 3
    sum_tbl = Table(sum_data, colWidths=sum_col)
    sum_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EFF6FF")),
        ("BACKGROUND", (1, 0), (1, -1), colors.HexColor("#F0FDF4")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#FEFCE8")),
        ("ALIGN",      (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",       (0, 0), (-1, -1), 0.5, BDR),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("ROUNDEDCORNERS", [4]),
    ]))
    story.append(sum_tbl)
    story.append(Spacer(1, 7 * mm))

    # ── Section 1: Observations ──────────────────────────────────────────
    story.append(Paragraph(f"Observations  ({len(observations)})", sec_s))
    story.append(Spacer(1, 2 * mm))

    if observations:
        C_obs = [10, 22, 36, 30, 24, 18, 52, 52, 31]   # mm  (total ≈ W/mm)
        _diff = (W / mm) - sum(C_obs)
        C_obs[6] += _diff / 2
        C_obs[7] += _diff / 2
        obs_cw = [c * mm for c in C_obs]

        obs_rows = [hdr_row(["#", "Date", "Officer", "Location", "Type", "Risk",
                              "Description", "Action Taken", "Status"])]
        risk_styles = []
        for i, obs in enumerate(observations, 1):
            rval  = obs.risk_level or ""
            rcol  = RISK_COLOR.get(rval)
            if rcol:
                risk_styles.append(("BACKGROUND", (5, i), (5, i), rcol))
            obs_rows.append([
                str(i),
                obs.date.strftime("%d %b %Y"),
                Paragraph(pdf_ar(obs.officer.name if obs.officer else "—"), cell_s),
                Paragraph(pdf_ar(obs.location or "—"), cell_s),
                TYPE_MAP.get(obs.obs_type, obs.obs_type or "—"),
                rval or "—",
                Paragraph(pdf_ar(obs.description or "—"), cell_s),
                Paragraph(pdf_ar(obs.action_taken or "—"), cell_s),
                Paragraph("CLOSED" if obs.status != "open" else "OPEN",
                          _ps("st", 7, 10, bold=True,
                              color=colors.HexColor("#16A34A" if obs.status != "open" else "#DC2626"))),
            ])
        obs_tbl = Table(obs_rows, colWidths=obs_cw, repeatRows=1)
        obs_tbl.setStyle(tbl_style(risk_styles))
        story.append(obs_tbl)
    else:
        story.append(Paragraph("No observations for the selected period.", _ps("no", 8, 11, color=colors.HexColor("#94A3B8"))))

    story.append(Spacer(1, 7 * mm))

    # ── Section 2: Checklists ─────────────────────────────────────────────
    story.append(Paragraph(f"Environment Checklists  ({len(checklists)})", sec_s))
    story.append(Spacer(1, 2 * mm))

    if checklists:
        C_cl = [10, 22, 50, 40, 40, W / mm - 162]
        cl_cw = [c * mm for c in C_cl]
        cl_rows = [hdr_row(["#", "Date", "Checklist Type", "Report No.", "Officer", "Area"])]
        for i, cl in enumerate(checklists, 1):
            cl_rows.append([
                str(i),
                cl.filled_date.strftime("%d %b %Y"),
                Paragraph(pdf_ar(cl.checklist_type or "—"), cell_s),
                cl.report_no or "—",
                Paragraph(pdf_ar(cl.officer.name if cl.officer else "—"), cell_s),
                Paragraph(pdf_ar(cl.area or "—"), cell_s),
            ])
        cl_tbl = Table(cl_rows, colWidths=cl_cw, repeatRows=1)
        cl_tbl.setStyle(tbl_style())
        story.append(cl_tbl)
    else:
        story.append(Paragraph("No checklists for the selected period.", _ps("no2", 8, 11, color=colors.HexColor("#94A3B8"))))

    story.append(Spacer(1, 7 * mm))

    # ── Section 3: Uploaded PDF Reports ──────────────────────────────────
    story.append(Paragraph(f"Uploaded PDF Reports  ({len(report_files)})", sec_s))
    story.append(Spacer(1, 2 * mm))

    if report_files:
        C_rf = [10, 22, 30, 40, 40, W / mm - 142]
        rf_cw = [c * mm for c in C_rf]
        rf_rows = [hdr_row(["#", "Date", "Type", "Report Name", "Uploaded By", "Notes"])]
        for i, rf in enumerate(report_files, 1):
            rf_rows.append([
                str(i),
                rf.report_date.strftime("%d %b %Y"),
                rf.role_type.title(),
                Paragraph(pdf_ar(rf.report_type or "—"), cell_s),
                Paragraph(pdf_ar(rf.uploader.name if rf.uploader else "—"), cell_s),
                Paragraph(pdf_ar(rf.notes or "—"), cell_s),
            ])
        rf_tbl = Table(rf_rows, colWidths=rf_cw, repeatRows=1)
        rf_tbl.setStyle(tbl_style())
        story.append(rf_tbl)
    else:
        story.append(Paragraph("No uploaded reports for the selected period.", _ps("no3", 8, 11, color=colors.HexColor("#94A3B8"))))

    doc.build(story)
    buf.seek(0)
    fname = f"welfare_env_report_{date_from or 'all'}_{date_to or 'all'}.pdf"
    resp = make_response(buf.read())
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'inline; filename="{fname}"'
    return resp


@app.route("/welfare/report/<int:rid>/env-delete", methods=["POST"])
@login_required
@welfare_supervisor_required
def env_report_delete(rid):
    _c = cid()
    rf = ReportFile.query.filter_by(id=rid, company_id=_c, role_type="environment").first_or_404()
    try:
        full_path = os.path.join(PDF_UPLOAD_DIR, rf.file_path)
        if os.path.exists(full_path):
            os.remove(full_path)
    except Exception:
        pass
    db.session.delete(rf)
    db.session.commit()
    flash("Report deleted.", "success")
    return redirect(url_for("welfare_supervisor"))



# ── LMS models — must be imported before db.create_all() ─────────────────────
import models.lms  # noqa: F401  registers LMS tables with SQLAlchemy metadata

# ===================== Bootstrapping =====================
with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        app.logger.error("create_all failed: %s", e)

    try:
        seed_warning_reasons()
    except Exception as e:
        db.session.rollback()
        app.logger.warning("seed_warning_reasons skipped: %s", e)

    # ── Migration: requests.officer_user_id + employee_id nullable ──
    # ملاحظة: SQLAlchemy 1.4 القديم لا يوفّر Connection.commit/rollback،
    # لذا نستخدم engine.begin() الذي يلتزم تلقائياً عند الخروج.
    def _mysql_column_exists(table, column):
        try:
            row = db.session.execute(text(
                "SELECT COUNT(*) FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = :t AND COLUMN_NAME = :c"
            ), {"t": table, "c": column}).scalar()
            return bool(row)
        except Exception:
            return True   # عند الشك لا نحاول التعديل

    def _mysql_column_nullable(table, column):
        try:
            row = db.session.execute(text(
                "SELECT IS_NULLABLE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = :t AND COLUMN_NAME = :c"
            ), {"t": table, "c": column}).scalar()
            return (row or "YES").upper() == "YES"
        except Exception:
            return True

    _is_mysql = "mysql" in str(db.engine.url)
    if _is_mysql:
        try:
            if not _mysql_column_exists("requests", "officer_user_id"):
                with db.engine.begin() as _conn:
                    _conn.execute(text(
                        "ALTER TABLE requests "
                        "ADD COLUMN officer_user_id INT NULL, "
                        "ADD CONSTRAINT fk_requests_officer_user "
                        "FOREIGN KEY (officer_user_id) REFERENCES `user`(id)"
                    ))
                app.logger.info("migration: requests.officer_user_id added")
        except Exception as e:
            db.session.rollback()
            app.logger.warning("migration officer_user_id skipped: %s", e)

        try:
            if not _mysql_column_nullable("requests", "employee_id"):
                with db.engine.begin() as _conn:
                    _conn.execute(text(
                        "ALTER TABLE requests MODIFY COLUMN employee_id INT NULL"
                    ))
                app.logger.info("migration: requests.employee_id set nullable")
        except Exception as e:
            db.session.rollback()
            app.logger.warning("migration employee_id nullable skipped: %s", e)

        try:
            if not _mysql_column_exists("wlf_progress", "deliverable"):
                with db.engine.begin() as _conn:
                    _conn.execute(text(
                        "ALTER TABLE wlf_progress ADD COLUMN deliverable VARCHAR(200) NULL"
                    ))
                app.logger.info("migration: wlf_progress.deliverable added")
        except Exception as e:
            db.session.rollback()
            app.logger.warning("migration wlf_progress.deliverable skipped: %s", e)

        # wlf_asset: add missing columns if table existed before this version
        for _col, _ddl in [
            ("location_code", "ALTER TABLE wlf_asset ADD COLUMN location_code VARCHAR(20) NOT NULL DEFAULT ''"),
            ("asset_type",    "ALTER TABLE wlf_asset ADD COLUMN asset_type VARCHAR(30) NOT NULL DEFAULT ''"),
            ("label",         "ALTER TABLE wlf_asset ADD COLUMN label VARCHAR(80) NOT NULL DEFAULT ''"),
            ("qty",           "ALTER TABLE wlf_asset ADD COLUMN qty INT NOT NULL DEFAULT 1"),
            ("capacity",      "ALTER TABLE wlf_asset ADD COLUMN capacity INT NULL"),
            ("notes",         "ALTER TABLE wlf_asset ADD COLUMN notes TEXT NULL"),
            ("added_by",      "ALTER TABLE wlf_asset ADD COLUMN added_by INT NULL"),
            ("created_at",    "ALTER TABLE wlf_asset ADD COLUMN created_at DATETIME NULL"),
            ("updated_at",    "ALTER TABLE wlf_asset ADD COLUMN updated_at DATETIME NULL"),
        ]:
            try:
                if not _mysql_column_exists("wlf_asset", _col):
                    with db.engine.begin() as _conn:
                        _conn.execute(text(_ddl))
                    app.logger.info("migration: wlf_asset.%s added", _col)
            except Exception as e:
                db.session.rollback()
                app.logger.warning("migration wlf_asset.%s skipped: %s", _col, e)

        # wlf_weekly_report: ensure required columns exist
        for _col, _ddl in [
            ("officer_note", "ALTER TABLE wlf_weekly_report ADD COLUMN officer_note TEXT NULL"),
            ("sup_comment",  "ALTER TABLE wlf_weekly_report ADD COLUMN sup_comment TEXT NULL"),
            ("sup_seen",     "ALTER TABLE wlf_weekly_report ADD COLUMN sup_seen TINYINT(1) NOT NULL DEFAULT 0"),
        ]:
            try:
                if not _mysql_column_exists("wlf_weekly_report", _col):
                    with db.engine.begin() as _conn:
                        _conn.execute(text(_ddl))
                    app.logger.info("migration: wlf_weekly_report.%s added", _col)
            except Exception as e:
                db.session.rollback()
                app.logger.warning("migration wlf_weekly_report.%s skipped: %s", _col, e)

        # wlf_progress.notes column
        try:
            if not _mysql_column_exists("wlf_progress", "notes"):
                with db.engine.begin() as _conn:
                    _conn.execute(text(
                        "ALTER TABLE wlf_progress ADD COLUMN notes TEXT NULL"
                    ))
                app.logger.info("migration: wlf_progress.notes added")
        except Exception as e:
            db.session.rollback()
            app.logger.warning("migration wlf_progress.notes skipped: %s", e)

# على الاستضافة: Passenger يستورد الملف فقط، لذا ننادي الإنشاء هنا:
ensure_db_and_admin()

# ── Privacy Policy (public, no login required) ─────────────────
@app.route("/privacy")
def privacy_policy():
    return render_template("privacy.html")


# ═══════════════════════════════════════════════════════════════
#  Super Admin Routes  (supervisor_code = 39468, role = super_admin)
# ═══════════════════════════════════════════════════════════════

@app.route("/sa/")
@super_admin_required
def superadmin_dashboard():
    total_companies = Company.query.count()
    active_companies = Company.query.filter_by(is_active=True).count()
    pending_companies = Company.query.filter_by(is_active=False).count()
    total_users = User.query.filter(User.role != "super_admin").count()
    recent = Company.query.order_by(Company.created_at.desc()).limit(5).all()
    pending = Company.query.filter_by(is_active=False).order_by(Company.created_at.desc()).all()
    return render_template("superadmin_dashboard.html",
                           total_companies=total_companies,
                           active_companies=active_companies,
                           pending_companies=pending_companies,
                           total_users=total_users,
                           recent=recent,
                           pending=pending)


@app.route("/sa/companies")
@super_admin_required
def superadmin_companies():
    q = request.args.get("q", "").strip()
    query = Company.query
    if q:
        query = query.filter(Company.name.ilike(f"%{q}%"))
    companies = query.order_by(Company.created_at.desc()).all()
    # Attach user count
    for co in companies:
        co._user_count = User.query.filter_by(company_id=co.id).count()
    return render_template("superadmin_companies.html", companies=companies, q=q)


@app.route("/sa/companies/<int:company_id>")
@super_admin_required
def superadmin_company_detail(company_id):
    co = Company.query.get_or_404(company_id)
    users = User.query.filter_by(company_id=company_id).order_by(User.name).all()
    admin_user = next((u for u in users if u.role == "admin" and u.is_active), None) or \
                 next((u for u in users if u.is_active), None)
    return render_template("superadmin_company.html", co=co, users=users, admin_user=admin_user)


@app.post("/sa/companies/<int:company_id>/activate")
@super_admin_required
def superadmin_activate(company_id):
    co = Company.query.get_or_404(company_id)
    co.is_active = True
    db.session.commit()
    flash(f"Company '{co.name}' activated.", "success")
    return redirect(url_for("superadmin_company_detail", company_id=company_id))


@app.post("/sa/companies/<int:company_id>/deactivate")
@super_admin_required
def superadmin_deactivate(company_id):
    co = Company.query.get_or_404(company_id)
    co.is_active = False
    db.session.commit()
    flash(f"Company '{co.name}' deactivated.", "warning")
    return redirect(url_for("superadmin_company_detail", company_id=company_id))


@app.post("/sa/companies/create")
@super_admin_required
def superadmin_create_company():
    company_name = (request.form.get("company_name") or "").strip()
    admin_name   = (request.form.get("admin_name") or "").strip()
    admin_email  = (request.form.get("admin_email") or "").strip().lower()
    admin_pw     = (request.form.get("admin_password") or "").strip()

    if not all([company_name, admin_name, admin_email, admin_pw]):
        flash("All fields are required.", "danger")
        return redirect(url_for("superadmin_companies"))

    if User.query.filter_by(email=admin_email).first():
        flash("Email already in use.", "danger")
        return redirect(url_for("superadmin_companies"))

    import re, secrets as _sec
    slug = re.sub(r"[^a-z0-9]+", "-", company_name.lower()).strip("-")
    base_slug = slug
    counter = 1
    while Company.query.filter_by(slug=slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1

    co = Company(name=company_name, slug=slug, owner_email=admin_email, is_active=True)
    db.session.add(co)
    db.session.flush()

    sup_code = f"adm-{_sec.token_hex(4)}"
    while User.query.filter_by(supervisor_code=sup_code).first():
        sup_code = f"adm-{_sec.token_hex(4)}"

    u = User(supervisor_code=sup_code, name=admin_name, email=admin_email,
             role="admin", is_active=True, company_id=co.id)
    u.set_password(admin_pw)
    db.session.add(u)
    db.session.commit()

    flash(f"Company '{company_name}' created and activated.", "success")
    return redirect(url_for("superadmin_company_detail", company_id=co.id))


@app.post("/sa/companies/<int:company_id>/set-max-users")
@super_admin_required
def superadmin_set_max_users(company_id):
    co = Company.query.get_or_404(company_id)
    try:
        co.max_users = max(1, int(request.form.get("max_users", 50)))
        db.session.commit()
        flash(f"Max users updated to {co.max_users}.", "success")
    except (ValueError, TypeError):
        flash("Invalid value.", "danger")
    return redirect(url_for("superadmin_company_detail", company_id=company_id))


@app.post("/sa/companies/<int:company_id>/set-plan")
@super_admin_required
def superadmin_set_plan(company_id):
    co = Company.query.get_or_404(company_id)
    plan = request.form.get("plan", "free")
    if plan in ("free", "pro"):
        co.plan = plan
        db.session.commit()
        flash(f"Plan updated to {plan}.", "success")
    return redirect(url_for("superadmin_company_detail", company_id=company_id))


@app.route("/sa/impersonate/<int:user_id>")
@super_admin_required
def superadmin_impersonate(user_id):
    target = User.query.get_or_404(user_id)
    if target.role == "super_admin":
        flash("Cannot impersonate super admin.", "danger")
        return redirect(url_for("superadmin_dashboard"))
    session["original_user_id"] = session.get("user_id")
    session["user_id"] = target.id
    session["company_id"] = target.company_id
    session["impersonating"] = True
    flash(f"Now viewing as {target.name} ({target.role})", "info")
    return redirect(url_for("index"))


@app.route("/sa/stop-impersonate")
@login_required
def superadmin_stop_impersonate():
    if not session.get("impersonating"):
        return redirect(url_for("index"))
    original_id = session.get("original_user_id")
    session.clear()
    if original_id:
        session["user_id"] = original_id
    return redirect(url_for("superadmin_dashboard"))


# ── Super Admin API (for iOS app) ─────────────────────────────

def _sa_token_user():
    """Validates Bearer token and returns user if super_admin, else None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token_val = auth[7:]
    mt = MobileToken.query.filter_by(token=token_val).first()
    if not mt:
        return None
    u = db.session.get(User, mt.user_id)
    if not u or u.role != "super_admin":
        return None
    return u

@app.get("/api/sa/dashboard")
def api_sa_dashboard():
    if not _sa_token_user():
        return jsonify({"error": "forbidden"}), 403
    total     = Company.query.count()
    active    = Company.query.filter_by(is_active=True).count()
    pending   = Company.query.filter_by(is_active=False).count()
    users_cnt = User.query.filter(User.role != "super_admin").count()
    recent    = Company.query.order_by(Company.created_at.desc()).limit(5).all()
    pending_list = Company.query.filter_by(is_active=False).order_by(Company.created_at.desc()).all()
    def co_dict(co):
        return {
            "id": co.id, "name": co.name, "slug": co.slug,
            "plan": co.plan, "is_active": bool(co.is_active),
            "owner_email": co.owner_email,
            "user_count": User.query.filter_by(company_id=co.id).count(),
            "created_at": co.created_at.strftime("%Y-%m-%d") if co.created_at else None
        }
    return jsonify({
        "total_companies": total,
        "active_companies": active,
        "pending_companies": pending,
        "total_users": users_cnt,
        "recent": [co_dict(c) for c in recent],
        "pending": [co_dict(c) for c in pending_list]
    })

@app.get("/api/sa/companies")
def api_sa_companies():
    if not _sa_token_user():
        return jsonify({"error": "forbidden"}), 403
    q = request.args.get("q", "").strip()
    query = Company.query
    if q:
        query = query.filter(Company.name.ilike(f"%{q}%"))
    companies = query.order_by(Company.created_at.desc()).all()
    return jsonify([{
        "id": co.id, "name": co.name, "slug": co.slug,
        "plan": co.plan, "is_active": bool(co.is_active),
        "owner_email": co.owner_email,
        "user_count": User.query.filter_by(company_id=co.id).count(),
        "created_at": co.created_at.strftime("%Y-%m-%d") if co.created_at else None
    } for co in companies])

@app.get("/api/sa/companies/<int:company_id>")
def api_sa_company_detail(company_id):
    if not _sa_token_user():
        return jsonify({"error": "forbidden"}), 403
    co = Company.query.get_or_404(company_id)
    users = User.query.filter_by(company_id=company_id).order_by(User.name).all()
    return jsonify({
        "id": co.id, "name": co.name, "slug": co.slug,
        "plan": co.plan, "is_active": bool(co.is_active),
        "owner_email": co.owner_email, "max_users": co.max_users,
        "created_at": co.created_at.strftime("%Y-%m-%d") if co.created_at else None,
        "users": [{
            "id": u.id, "name": u.name, "role": u.role,
            "email": u.email, "supervisor_code": u.supervisor_code,
            "is_active": bool(u.is_active)
        } for u in users]
    })

@app.post("/api/sa/companies/<int:company_id>/activate")
def api_sa_activate(company_id):
    if not _sa_token_user():
        return jsonify({"error": "forbidden"}), 403
    co = Company.query.get_or_404(company_id)
    co.is_active = True
    db.session.commit()
    return jsonify({"ok": True})

@app.post("/api/sa/companies/<int:company_id>/deactivate")
def api_sa_deactivate(company_id):
    if not _sa_token_user():
        return jsonify({"error": "forbidden"}), 403
    co = Company.query.get_or_404(company_id)
    co.is_active = False
    db.session.commit()
    return jsonify({"ok": True})

@app.post("/api/sa/companies/create")
def api_sa_create_company():
    sa = _sa_token_user()
    if not sa:
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(force=True) or {}
    name  = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    plan  = data.get("plan", "free")
    if not name:
        return jsonify({"error": "name required"}), 400
    import re, unicodedata
    slug_base = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", name).encode("ascii","ignore").decode().lower()).strip("-") or "co"
    slug = slug_base
    i = 1
    while Company.query.filter_by(slug=slug).first():
        slug = f"{slug_base}-{i}"; i += 1
    co = Company(name=name, slug=slug, plan=plan, is_active=False, owner_email=email or None)
    db.session.add(co)
    db.session.flush()
    pwd = data.get("password") or ""
    admin_u = User(
        name=name + " Admin", supervisor_code=str(co.id) + "adm",
        role="admin", is_active=True, company_id=co.id,
        email=email or None
    )
    if pwd:
        admin_u.set_password(pwd)
    db.session.add(admin_u)
    db.session.commit()
    return jsonify({"ok": True, "company_id": co.id}), 201

@app.delete("/api/sa/companies/<int:company_id>")
def api_sa_delete_company(company_id):
    if not _sa_token_user():
        return jsonify({"error": "forbidden"}), 403
    co = db.session.get(Company, company_id)
    if not co:
        return jsonify({"error": "company not found"}), 404

    user_count = User.query.filter_by(company_id=company_id).count()
    emp_count  = Employee.query.filter_by(company_id=company_id).count()
    if user_count or emp_count:
        return jsonify({
            "error": "cannot delete company with existing data",
            "users": user_count,
            "employees": emp_count,
            "hint": "deactivate all users and employees first",
        }), 409

    db.session.delete(co)
    db.session.commit()
    return jsonify({"ok": True})


# ── Company Settings (admin only) ────────────────────────────

@app.route("/settings/company", methods=["GET", "POST"])
@admin_required
def settings_company():
    u = cur_user()
    co = db.session.get(Company, u.company_id)
    if not co:
        abort(404)
    if request.method == "POST":
        new_name = (request.form.get("name") or "").strip()
        if not new_name:
            flash("Company name cannot be empty.", "danger")
        else:
            co.name = new_name
            db.session.commit()
            flash("Company name updated.", "success")
        return redirect(url_for("settings_company"))
    return render_template("settings_company.html", co=co)


@app.route("/settings/users")
@admin_required
def settings_users():
    u = cur_user()
    users = (apply_company_filter(User.query, User)
             .filter(User.is_active == True)
             .order_by(User.role, User.name)
             .all())
    co = db.session.get(Company, u.company_id)
    return render_template("settings_users.html", users=users, co=co)


@app.post("/settings/users/<int:uid>/deactivate")
@admin_required
def settings_user_deactivate(uid):
    u = cur_user()
    if not u.company_id:
        abort(403)
    target = User.query.filter(
        User.id == uid, User.company_id == u.company_id
    ).first_or_404()
    if target.id == u.id:
        flash("You cannot deactivate yourself.", "danger")
    else:
        target.is_active = False
        db.session.commit()
        flash(f"{target.name} deactivated.", "success")
    return redirect(url_for("settings_users"))


@app.post("/settings/users/<int:uid>/activate")
@admin_required
def settings_user_activate(uid):
    u = cur_user()
    if not u.company_id:
        abort(403)
    target = User.query.filter(
        User.id == uid, User.company_id == u.company_id
    ).first_or_404()
    target.is_active = True
    db.session.commit()
    flash(f"{target.name} activated.", "success")
    return redirect(url_for("settings_users"))


# ===================== Safety Supervisor =====================

def _safety_sup_required():
    u = cur_user()
    if not u or u.role not in ("safety_supervisor", "admin", "super_admin"):
        abort(403)
    return u

def _get_safety_officers(sup_user):
    """Active safety officers visible to this supervisor.
    safety_supervisor → only officers explicitly assigned via OfficerTeam.
    admin / super_admin → all safety officers in company."""
    if sup_user.role == "safety_supervisor":
        assigned_ids = [
            row.officer_id
            for row in OfficerTeam.query.filter_by(supervisor_id=sup_user.id).all()
        ]
        if not assigned_ids:
            return []
        return User.query.filter(
            User.id.in_(assigned_ids),
            User.role == "safety_officer",
            User.is_active == True,
        ).order_by(User.name).all()
    return User.query.filter_by(role="safety_officer", is_active=True,
                                company_id=sup_user.company_id).order_by(User.name).all()

@app.route("/safety-supervisor/home")
@login_required
def safety_supervisor_home():
    u = cur_user()
    if u.role not in ("safety_supervisor", "admin", "super_admin"):
        abort(403)
    today = datetime.now(RIYADH_TZ).date()
    days_since_sunday = today.isoweekday() % 7
    week_start = today - timedelta(days=days_since_sunday)
    officers = _get_safety_officers(u)
    officer_ids = [o.id for o in officers]

    # Aggregated stats per officer (GROUP BY — avoids N+1)
    def _grp(col, date_col, extra=None):
        q = (db.session.query(col, func.count())
             .filter(col.in_(officer_ids), date_col.between(week_start, today))
             .group_by(col))
        if extra is not None:
            q = q.filter(extra)
        return dict(q.all())

    obs_map  = _grp(HseObservation.officer_id, HseObservation.date)
    tbt_map  = _grp(HseTbt.officer_id, HseTbt.date)
    nm_map   = _grp(HseNearMiss.officer_id, HseNearMiss.date)
    jso_map  = _grp(HseJsoClosure.officer_id, HseJsoClosure.date)
    bbs_map  = dict(
        db.session.query(HseBbs.officer_id, func.coalesce(func.sum(HseBbs.card_count), 0))
        .filter(HseBbs.officer_id.in_(officer_ids), HseBbs.date.between(week_start, today))
        .group_by(HseBbs.officer_id).all()
    )
    ptw_map  = dict(
        db.session.query(HsePtw.officer_id, func.count())
        .filter(HsePtw.officer_id.in_(officer_ids), HsePtw.status == "active", HsePtw.week_end >= today)
        .group_by(HsePtw.officer_id).all()
    )
    insp_map = _grp(HseInspection.officer_id, HseInspection.date)
    locs_today = {l.user_id: l for l in UserLocation.query.filter(
        UserLocation.user_id.in_(officer_ids)).all()}

    rows = []
    for o in officers:
        oid = o.id
        loc = locs_today.get(oid)
        loc_present = False
        loc_text    = None
        if loc and loc.updated_at:
            lu = (loc.updated_at.replace(tzinfo=None) if loc.updated_at.tzinfo is None
                  else loc.updated_at.astimezone(RIYADH_TZ).replace(tzinfo=None))
            loc_present = lu.date() == today
            if loc_present:
                parts = ([f"PKG{loc.pkg}"] if loc.pkg else []) + \
                        ([f"Unit {loc.unit}"] if loc.unit else []) + \
                        ([loc.area_text] if loc.area_text else [])
                loc_text = " · ".join(parts)
        obs = obs_map.get(oid, 0)
        tbt = tbt_map.get(oid, 0)
        nm  = nm_map.get(oid, 0)
        jso = jso_map.get(oid, 0)
        bbs = int(bbs_map.get(oid, 0))
        ptw = ptw_map.get(oid, 0)
        insp = insp_map.get(oid, 0)
        score = round(obs*2 + tbt*3 + ptw, 1)
        rows.append({
            "id": oid, "name": o.name, "code": o.supervisor_code,
            "checked_in": loc_present,
            "location": loc_text,
            "obs_w": obs, "tbt_w": tbt, "nm_w": nm,
            "jso_w": jso, "bbs_w": bbs, "ptw_w": ptw, "insp_w": insp,
            "total_w": obs + tbt + nm + jso,
            "score": score,
            "is_trainee": bool(getattr(o, "ptw_training_active", False)),
        })

    rows.sort(key=lambda r: r["score"], reverse=True)
    checked_in = sum(1 for r in rows if r["checked_in"])
    pending_req = Request.query.filter_by(supervisor_id=u.id, status="pending").count()
    return render_template("safety_supervisor_home.html",
                           officers=rows, today=today, week_start=week_start,
                           checked_in=checked_in, total=len(rows),
                           pending_req=pending_req)

@app.route("/safety-supervisor/officers", methods=["GET", "POST"])
@login_required
def safety_supervisor_officers():
    u = cur_user()
    if u.role not in ("safety_supervisor", "admin", "super_admin"):
        abort(403)
    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        name = (request.form.get("name") or "").strip()
        role = (request.form.get("role") or "safety_officer").strip()
        allowed_roles = {"safety_officer", "safety_welfare", "environment_officer"}
        if role not in allowed_roles:
            role = "safety_officer"
        if not code:
            flash("Officer ID is required.", "danger")
        else:
            existing = User.query.filter_by(supervisor_code=code).first()
            if existing:
                existing.role = role
                existing.company_id = u.company_id
                existing.is_active = True
                if name:
                    existing.name = name
                db.session.commit()
                flash(f"{existing.name or code} updated to {role.replace('_',' ').title()}.", "success")
            else:
                new_officer = User(supervisor_code=code, name=name,
                                   role=role, is_active=True,
                                   company_id=u.company_id)
                db.session.add(new_officer)
                db.session.commit()
                flash(f"{name or code} added as {role.replace('_',' ').title()}.", "success")
        return redirect(url_for("safety_supervisor_officers"))
    today = datetime.now(RIYADH_TZ).date()
    days_since_sunday = today.isoweekday() % 7
    week_start = today - timedelta(days=days_since_sunday)
    officers = _get_safety_officers(u)
    officer_ids = [o.id for o in officers]
    locs = {l.user_id: l for l in UserLocation.query.filter(
        UserLocation.user_id.in_(officer_ids)).all()}
    rows = []
    for o in officers:
        loc = locs.get(o.id)
        loc_today = False
        loc_text  = None
        if loc and loc.updated_at:
            lu = (loc.updated_at.replace(tzinfo=None) if loc.updated_at.tzinfo is None
                  else loc.updated_at.astimezone(RIYADH_TZ).replace(tzinfo=None))
            loc_today = lu.date() == today
            if loc_today:
                parts = ([f"PKG{loc.pkg}"] if loc.pkg else []) + \
                        ([f"Unit {loc.unit}"] if loc.unit else []) + \
                        ([loc.area_text] if loc.area_text else [])
                loc_text = " · ".join(parts)
        obs_w = HseObservation.query.filter(HseObservation.officer_id == o.id,
                                            HseObservation.date >= week_start).count()
        tbt_w = HseTbt.query.filter(HseTbt.officer_id == o.id,
                                    HseTbt.date >= week_start).count()
        nm_w  = HseNearMiss.query.filter(HseNearMiss.officer_id == o.id,
                                         HseNearMiss.date >= week_start).count()
        rows.append({"id": o.id, "name": o.name, "code": o.supervisor_code,
                     "checked_in": loc_today,
                     "location": loc_text,
                     "obs_w": obs_w, "tbt_w": tbt_w, "nm_w": nm_w,
                     "total_w": obs_w + tbt_w + nm_w})
    return render_template("safety_supervisor_officers.html",
                           officers=rows, today=today, week_start=week_start)

# ── Admin: manage safety supervisor ─────────────────────────
@app.route("/admin/safety-supervisor/assign", methods=["GET", "POST"])
@admin_required
def admin_safety_sup_assign():
    if request.method == "POST":
        action = request.form.get("action")
        sup_id = request.form.get("sup_id", type=int)
        officer_id = request.form.get("officer_id", type=int)
        if action == "add" and sup_id and officer_id:
            existing = SafetySupervisorMap.query.filter_by(
                safety_sup_id=sup_id, officer_id=officer_id).first()
            if not existing:
                db.session.add(SafetySupervisorMap(
                    safety_sup_id=sup_id, officer_id=officer_id,
                    company_id=cid()))
                db.session.commit()
                flash("Assigned.", "success")
        elif action == "remove" and sup_id and officer_id:
            SafetySupervisorMap.query.filter_by(
                safety_sup_id=sup_id, officer_id=officer_id).delete()
            db.session.commit()
            flash("Removed.", "success")
        return redirect(url_for("admin_safety_sup_assign"))
    safety_sups = User.query.filter_by(role="safety_supervisor", is_active=True,
                                        company_id=cid()).order_by(User.name).all()
    officers = User.query.filter_by(role="safety_officer", is_active=True,
                                     company_id=cid()).order_by(User.name).all()
    maps = SafetySupervisorMap.query.filter(
        SafetySupervisorMap.company_id == cid()).all()
    map_dict = {}
    for m in maps:
        map_dict.setdefault(m.safety_sup_id, []).append(m.officer_id)
    return render_template("admin_safety_sup_assign.html",
                           safety_sups=safety_sups, officers=officers,
                           map_dict=map_dict)

# ── API: safety supervisor officers list (iOS) ───────────────
@app.route("/api/safety-supervisor/officers", methods=["GET"])
def api_safety_sup_officers():
    u = get_api_user() or cur_user()
    if not u or not u.is_active:
        return jsonify(error="Unauthorized"), 401
    if u.role not in ("safety_supervisor", "admin", "super_admin"):
        return jsonify(error="Forbidden"), 403
    today = datetime.now(RIYADH_TZ).date()
    # Support ?date=YYYY-MM-DD for specific day check-in, ?days=N for stats period
    date_param = freq.args.get("date")
    days_param = int(freq.args.get("days", 7))
    try:
        target_date = datetime.strptime(date_param, "%Y-%m-%d").date() if date_param else today
    except ValueError:
        target_date = today
    stats_start = target_date - timedelta(days=max(1, days_param) - 1)

    officers = _get_safety_officers(u)
    officer_ids = [o.id for o in officers]

    # Check-in: use HseCheckin for historical dates, UserLocation for today
    if target_date == today:
        locs = {l.user_id: l for l in UserLocation.query.filter(
            UserLocation.user_id.in_(officer_ids)).all()}
    else:
        locs = None

    checkins_on_date = {c.officer_id: c for c in HseCheckin.query.filter(
        HseCheckin.officer_id.in_(officer_ids),
        HseCheckin.date == target_date
    ).all()} if target_date != today else {}

    def _grp(col, date_col):
        return dict(db.session.query(col, func.count())
                    .filter(col.in_(officer_ids), date_col.between(stats_start, target_date))
                    .group_by(col).all())

    obs_map  = _grp(HseObservation.officer_id, HseObservation.date)
    tbt_map  = _grp(HseTbt.officer_id, HseTbt.date)
    nm_map   = _grp(HseNearMiss.officer_id, HseNearMiss.date)
    jso_map  = _grp(HseJsoClosure.officer_id, HseJsoClosure.date)
    bbs_map  = dict(
        db.session.query(HseBbs.officer_id, func.coalesce(func.sum(HseBbs.card_count), 0))
        .filter(HseBbs.officer_id.in_(officer_ids), HseBbs.date.between(stats_start, target_date))
        .group_by(HseBbs.officer_id).all()
    )
    ptw_map  = dict(
        db.session.query(HsePtw.officer_id, func.count())
        .filter(HsePtw.officer_id.in_(officer_ids), HsePtw.status == "active", HsePtw.week_end >= target_date)
        .group_by(HsePtw.officer_id).all()
    )
    insp_map = _grp(HseInspection.officer_id, HseInspection.date)

    data = []
    for o in officers:
        loc_today = False
        loc_text  = None
        if target_date == today and locs:
            loc = locs.get(o.id)
            if loc and loc.updated_at:
                loc_date = (loc.updated_at.replace(tzinfo=None)
                            if loc.updated_at.tzinfo is None else
                            loc.updated_at.astimezone(RIYADH_TZ).replace(tzinfo=None))
                loc_today = loc_date.date() == today
                if loc_today:
                    parts = ([f"PKG{loc.pkg}"] if loc.pkg else []) + \
                            ([f"Unit {loc.unit}"] if loc.unit else []) + \
                            ([loc.area_text] if loc.area_text else [])
                    loc_text = " · ".join(parts)
        else:
            ci = checkins_on_date.get(o.id)
            if ci:
                loc_today = True
                loc_text  = ci.location
        obs_w  = obs_map.get(o.id, 0)
        tbt_w  = tbt_map.get(o.id, 0)
        nm_w   = nm_map.get(o.id, 0)
        jso_w  = jso_map.get(o.id, 0)
        bbs_w  = int(bbs_map.get(o.id, 0))
        ptw_w  = ptw_map.get(o.id, 0)
        insp_w = insp_map.get(o.id, 0)
        score  = round(obs_w*2 + tbt_w*3 + ptw_w, 1)
        data.append({
            "id": o.id, "name": o.name, "code": o.supervisor_code,
            "checked_in": loc_today,
            "checkin_location": loc_text,
            "obs_week": obs_w, "tbt_week": tbt_w, "nm_week": nm_w,
            "jso_week": jso_w, "bbs_week": bbs_w, "ptw_week": ptw_w, "insp_week": insp_w,
            "total_week": obs_w + tbt_w + nm_w + jso_w,
            "score": score,
        })
    data.sort(key=lambda r: r["score"], reverse=True)
    return jsonify({"officers": data, "today": today.isoformat(),
                    "target_date": target_date.isoformat(),
                    "stats_start": stats_start.isoformat(),
                    "days": days_param})


# ═══════════════════════════════════════════════════════════════════
# ██  ENVIRONMENT OFFICER ROUTES
# ═══════════════════════════════════════════════════════════════════

@app.route("/env/")
@login_required
@environment_officer_required
def env_dashboard():
    u = cur_user()
    if not is_environment_officer(u):
        return redirect(url_for("welfare_supervisor"))
    _c = cid()
    today = datetime.now(RIYADH_TZ).date()
    recent_obs = (HseObservation.query
                  .filter_by(officer_id=u.id, company_id=_c)
                  .order_by(HseObservation.created_at.desc())
                  .limit(10).all())
    recent_files = (ReportFile.query
                    .filter_by(uploaded_by=u.id, company_id=_c, role_type="environment")
                    .order_by(ReportFile.uploaded_at.desc())
                    .limit(10).all())
    recent_checklists = (EnvChecklist.query
                         .filter_by(officer_id=u.id, company_id=_c)
                         .order_by(EnvChecklist.created_at.desc())
                         .limit(10).all())
    _seed_report_types(_c)
    type_options = (ReportTypeOption.query
                    .filter_by(company_id=_c, role_type="environment")
                    .order_by(ReportTypeOption.name).all())
    return render_template("env_dashboard.html",
                           today=today,
                           recent_obs=recent_obs,
                           recent_files=recent_files,
                           recent_checklists=recent_checklists,
                           type_options=type_options)



# ── Environment officer — observation ───────────────────────────────

@app.route("/env/observation/new", methods=["GET", "POST"])
@login_required
@environment_officer_required
def env_observation_new():
    u = cur_user()
    if not is_environment_officer(u):
        return redirect(url_for("welfare_supervisor"))
    _c = cid()
    today = datetime.now(RIYADH_TZ).date()
    if request.method == "POST":
        obs_type = request.form.get("obs_type", "").strip()
        if not obs_type:
            flash("Observation type is required.", "warning")
            return redirect(url_for("env_observation_new"))
        obs = HseObservation(
            officer_id=u.id, date=today,
            location=request.form.get("location", "").strip(),
            obs_type=obs_type,
            category=request.form.get("category", "").strip(),
            risk_level=request.form.get("risk_level") or None,
            description=request.form.get("description", "").strip(),
            action_taken=request.form.get("action_taken", "").strip(),
            company_id=_c,
        )
        db.session.add(obs)
        db.session.flush()
        for i in range(1, 4):
            f = request.files.get(f"photo_{i}")
            path = _save_hse_photo(f, "env_obs", company_id=_c)
            if path:
                db.session.add(HseObservationPhoto(
                    observation_id=obs.id, photo_path=path, photo_type="before",
                    company_id=_c))
        db.session.commit()
        flash("Observation saved.", "success")
        return redirect(url_for("env_dashboard"))
    return render_template("env_observation_new.html",
                           today=today, categories=OBS_CATEGORIES)


# ── Environment officer — PDF report upload ──────────────────────────

@app.route("/env/report/upload", methods=["GET", "POST"])
@login_required
@environment_officer_required
def env_report_upload():
    u = cur_user()
    if not is_environment_officer(u):
        return redirect(url_for("welfare_supervisor"))
    _c = cid()
    _seed_report_types(_c)
    type_options = (ReportTypeOption.query
                    .filter_by(company_id=_c, role_type="environment")
                    .order_by(ReportTypeOption.name).all())
    if request.method == "POST":
        file = request.files.get("pdf_file")
        report_type = request.form.get("report_type", "").strip()
        new_type = request.form.get("new_type", "").strip()
        report_date = _safe_date(request.form.get("report_date")) or datetime.now(RIYADH_TZ).date()
        notes = request.form.get("notes", "").strip()

        if new_type:
            report_type = new_type
            if not ReportTypeOption.query.filter_by(
                    company_id=_c, role_type="environment", name=new_type).first():
                db.session.add(ReportTypeOption(
                    company_id=_c, role_type="environment", name=new_type,
                    created_by=u.id))
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()

        if not report_type:
            flash("Please select or enter a report type.", "warning")
            return redirect(url_for("env_report_upload"))

        path = _save_report_pdf(file, _c)
        if not path:
            flash("Please upload a valid PDF file.", "warning")
            return redirect(url_for("env_report_upload"))

        rf = ReportFile(uploaded_by=u.id, company_id=_c, role_type="environment",
                        report_type=report_type, file_path=path,
                        report_date=report_date, notes=notes)
        db.session.add(rf)
        db.session.commit()
        flash("Report uploaded successfully.", "success")
        return redirect(url_for("env_dashboard"))

    return render_template("env_report_upload.html",
                           type_options=type_options,
                           today=datetime.now(RIYADH_TZ).date())


@app.route("/env/report/<int:rid>/view")
@login_required
@environment_officer_required
def env_report_view(rid):
    _c = cid()
    rf = ReportFile.query.filter_by(id=rid, company_id=_c, role_type="environment").first_or_404()
    u = cur_user()
    if is_environment_officer(u) and rf.uploaded_by != u.id:
        abort(403)
    full_path = os.path.join(PDF_UPLOAD_DIR, rf.file_path)
    if not os.path.exists(full_path):
        flash("File not found.", "danger")
        return redirect(url_for("env_dashboard"))
    return send_file(full_path, mimetype="application/pdf",
                     download_name=os.path.basename(rf.file_path))


# ── Environment officer — checklists (P2-EC / P2-WMC / P2-SPC) ─────────────

@app.route("/env/checklist/new")
@login_required
@environment_officer_required
def env_checklist_new():
    return render_template("env_checklist_new.html")


@app.route("/env/checklist/fill/<ctype>", methods=["GET", "POST"])
@login_required
@environment_officer_required
def env_checklist_fill(ctype):
    if ctype not in ENV_CHECKLISTS:
        abort(404)
    cfg = ENV_CHECKLISTS[ctype]
    _c = cid()
    u = cur_user()
    today = datetime.now(RIYADH_TZ).date()

    # Build a flat ordered list of (section_name, item_text)
    flat_items = []
    for sec in cfg["sections"]:
        for item_text in sec["items"]:
            flat_items.append((sec["name"], item_text))

    if request.method == "POST":
        filled_date_str = request.form.get("filled_date") or today.isoformat()
        try:
            filled_date = date.fromisoformat(filled_date_str)
        except ValueError:
            filled_date = today

        items_data = []
        for idx, (sec_name, item_text) in enumerate(flat_items):
            status   = request.form.get(f"s_{idx}", "na" if cfg["has_na"] else "good")
            area_sub = request.form.get(f"a_{idx}", "").strip()
            note     = request.form.get(f"n_{idx}", "").strip()
            items_data.append({
                "item": item_text, "section": sec_name,
                "status": status, "area_sub": area_sub, "note": note,
            })

        # Build area string: types + detail
        area_types = request.form.getlist("area_types")
        area_other = request.form.get("area_other", "").strip()
        area_detail = request.form.get("area_detail", "").strip()
        if area_other and "Other" in area_types:
            area_types = [t if t != "Other" else f"Other: {area_other}" for t in area_types]
        area_str = "; ".join(area_types)
        if area_detail:
            area_str = f"{area_detail} | {area_str}" if area_str else area_detail

        # JSO observations
        jso_rows = []
        for i in range(4):
            jno  = request.form.get(f"jso_no_{i}", "").strip()
            jdsc = request.form.get(f"jso_desc_{i}", "").strip()
            if jno or jdsc:
                jso_rows.append({"no": jno, "desc": jdsc})
        extra_obs = request.form.get("observations", "").strip()
        obs_payload = json.dumps({"jso": jso_rows, "notes": extra_obs}, ensure_ascii=False)

        custom_no = request.form.get("report_no_custom", "").strip()
        if custom_no:
            report_no = f"{cfg['report_prefix']} #{custom_no} / {filled_date.year}"
        else:
            count = EnvChecklist.query.filter_by(
                company_id=_c, checklist_type=ctype).count() + 1
            report_no = f"{cfg['report_prefix']} #{count:03d} / {filled_date.year}"

        cl = EnvChecklist(
            company_id=_c, officer_id=u.id, checklist_type=ctype,
            area=area_str[:199],
            report_no=report_no,
            subcontractor=request.form.get("subcontractor", "").strip(),
            filled_date=filled_date,
            items_json=json.dumps(items_data, ensure_ascii=False),
            observations=obs_payload,
        )
        db.session.add(cl)
        try:
            db.session.commit()
            flash("Checklist saved.", "success")
            return redirect(url_for("env_checklist_view", clid=cl.id))
        except Exception:
            db.session.rollback()
            flash("Error saving checklist.", "danger")

    return render_template("env_checklist_fill.html",
                           cfg=cfg, ctype=ctype, flat_items=flat_items, today=today)


@app.route("/env/checklist/<int:clid>")
@login_required
@environment_officer_required
def env_checklist_view(clid):
    _c = cid()
    u = cur_user()
    cl = EnvChecklist.query.filter_by(id=clid, company_id=_c).first_or_404()
    if is_environment_officer(u) and cl.officer_id != u.id:
        abort(403)
    cfg = ENV_CHECKLISTS.get(cl.checklist_type, {})
    items = json.loads(cl.items_json or "[]")
    return render_template("env_checklist_view.html", cl=cl, cfg=cfg, items=items)


@app.route("/env/checklist/<int:clid>/sign", methods=["POST"])
@login_required
@environment_officer_required
def env_checklist_sign(clid):
    _c = cid()
    u = cur_user()
    cl = EnvChecklist.query.filter_by(id=clid, company_id=_c).first_or_404()
    if is_environment_officer(u) and cl.officer_id != u.id:
        abort(403)
    sig = (request.form.get("signature") or "").strip()
    name = (request.form.get("signatory_name") or "").strip()
    if not sig:
        flash("Signature is required.", "danger")
        return redirect(url_for("env_checklist_view", clid=clid))
    cl.signature_data = sig
    cl.signatory_name = name or None
    try:
        db.session.commit()
        flash("Signature saved.", "success")
    except Exception:
        db.session.rollback()
        flash("Error saving signature.", "danger")
    return redirect(url_for("env_checklist_view", clid=clid))


@app.route("/env/checklist/<int:clid>/mark_official", methods=["POST"])
@login_required
def env_checklist_mark_official(clid):
    u = cur_user()
    if getattr(u, "role", None) != "admin":
        abort(403)
    _c = cid()
    cl = EnvChecklist.query.filter_by(id=clid, company_id=_c).first_or_404()
    if not cl.is_official:
        cl.is_official = True
        try:
            db.session.commit()
            flash("Checklist marked as official and logos are now locked.", "success")
        except Exception:
            db.session.rollback()
            flash("Error updating checklist.", "danger")
    return redirect(url_for("env_checklist_view", clid=clid))


@app.route("/env/checklist/<int:clid>/save_attendees", methods=["POST"])
@login_required
@environment_officer_required
def env_checklist_save_attendees(clid):
    _c = cid()
    cl = EnvChecklist.query.filter_by(id=clid, company_id=_c).first_or_404()
    attendees_json = request.form.get("attendees_json", "[]").strip()
    try:
        parsed = json.loads(attendees_json)
        cl.attendees_sigs = json.dumps(parsed, ensure_ascii=False)
        db.session.commit()
        flash("Attendees signatures saved.", "success")
    except Exception:
        db.session.rollback()
        flash("Error saving attendees.", "danger")
    return redirect(url_for("env_checklist_view", clid=clid))


@app.route("/env/checklist/<int:clid>/pdf")
@login_required
@environment_officer_required
def env_checklist_pdf(clid):
    _c = cid()
    u = cur_user()
    cl = EnvChecklist.query.filter_by(id=clid, company_id=_c).first_or_404()
    if is_environment_officer(u) and cl.officer_id != u.id:
        abort(403)
    cfg = ENV_CHECKLISTS.get(cl.checklist_type, {})
    items = json.loads(cl.items_json or "[]")

    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors as rl_colors
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                     Paragraph, Spacer, HRFlowable, Image)
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    import io, os as _os

    buf = io.BytesIO()
    page = A4   # Portrait — matches DOCX
    doc = SimpleDocTemplate(buf, pagesize=page,
                            topMargin=1.2*cm, bottomMargin=1.5*cm,
                            leftMargin=1.5*cm, rightMargin=1.5*cm)
    styles = getSampleStyleSheet()
    # Exact colors from DOCX XML
    GREY_D9  = rl_colors.HexColor("#D9D9D9")   # section/header grey
    HDR_GOOD = rl_colors.HexColor("#92D050")   # Good — lime green
    HDR_IMM  = rl_colors.HexColor("#FF0000")   # Immediately — pure red
    HDR_IMP  = rl_colors.HexColor("#FFFF00")   # Improve — pure yellow
    SIG_BLUE = rl_colors.HexColor("#C6D9F1")   # Signatories title — light blue
    BLACK       = rl_colors.black
    WHITE       = rl_colors.white
    AMIRAL_GREEN = rl_colors.HexColor("#065f46")   # only for AMIRAL PROJECT logo box

    norm  = ParagraphStyle("norm",  parent=styles["Normal"], fontSize=8,  leading=10)
    sm    = ParagraphStyle("sm",    parent=styles["Normal"], fontSize=7,   leading=9)
    bold  = ParagraphStyle("bold",  parent=styles["Normal"], fontSize=8,   leading=10, fontName="Helvetica-Bold")
    boldC = ParagraphStyle("boldC", parent=styles["Normal"], fontSize=8,   leading=10, fontName="Helvetica-Bold",   alignment=TA_CENTER)
    italic= ParagraphStyle("ital",  parent=styles["Normal"], fontSize=8,   leading=10, fontName="Helvetica-Oblique")
    hdr_s = ParagraphStyle("hdr",   parent=styles["Normal"], fontSize=11,  leading=13, fontName="Helvetica-Bold")
    story = []

    # ── Section title (matches DOCX heading style) ──
    appendix  = cfg.get("appendix", "")
    title_txt = cfg.get("title", cl.checklist_type)
    story.append(Paragraph(f"<b>{appendix}   {title_txt}</b>", hdr_s))
    story.append(Spacer(1, 0.25*cm))

    static_dir   = _os.path.join(_os.path.dirname(__file__), "static", "img")
    amiral_path  = _os.path.join(static_dir, "amiral_logo.png")
    tecnimnt_path= _os.path.join(static_dir, "tecnimont_logo.png")

    # ── Parse area field: "detail_text | Type1, Type2" or plain ──
    area_raw = cl.area or ""
    if " | " in area_raw:
        area_detail, area_types = area_raw.split(" | ", 1)
    else:
        area_detail = ""
        area_types  = area_raw

    LOC_OPTIONS = ['Workshop', 'Office', 'Laydown Area', 'Camp/Accommodation', 'Other']

    # Build location checkboxes using real bordered boxes (Helvetica doesn't support ☑/☐)
    def _chk_box(checked):
        """Return a tiny bordered table cell acting as a checkbox."""
        tick_tbl = Table([["✓" if checked else ""]], colWidths=[0.3*cm], rowHeights=[0.3*cm])
        tick_tbl.setStyle(TableStyle([
            ("BOX",           (0,0), (0,0), 0.5, BLACK),
            ("ALIGN",         (0,0), (0,0), "CENTER"),
            ("VALIGN",        (0,0), (0,0), "MIDDLE"),
            ("FONTNAME",      (0,0), (0,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0), (0,0), 7),
            ("TOPPADDING",    (0,0), (0,0), 0),
            ("BOTTOMPADDING", (0,0), (0,0), 0),
            ("LEFTPADDING",   (0,0), (0,0), 0),
            ("RIGHTPADDING",  (0,0), (0,0), 0),
        ]))
        return tick_tbl

    sm_bold = ParagraphStyle("sm_bold", parent=styles["Normal"], fontSize=7,
                             fontName="Helvetica-Bold", leading=9)
    loc_rows = []
    for opt in LOC_OPTIONS:
        is_checked = opt in (area_types or "")
        loc_rows.append([_chk_box(is_checked), Paragraph(f" {opt}", sm_bold)])
    loc_inner = Table(loc_rows, colWidths=[0.36*cm, 3.8*cm])
    loc_inner.setStyle(TableStyle([
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING",   (0,0), (-1,-1), 1),
        ("RIGHTPADDING",  (0,0), (-1,-1), 2),
        ("TOPPADDING",    (0,0), (-1,-1), 1),
        ("BOTTOMPADDING", (0,0), (-1,-1), 1),
    ]))

    # Left cell of main header table: area label + area value + location checkboxes
    area_label = Paragraph("<b>Construction worksite</b><br/><i>(Specify the Area/Unit/Elevation)</i>", sm)
    area_val   = Paragraph(f"<b>{area_detail}</b>", norm) if area_detail else Paragraph("", norm)
    left_cell_data = [[area_label], [area_val], [loc_inner]]
    left_cell_tbl  = Table(left_cell_data, colWidths=["*"])
    left_cell_tbl.setStyle(TableStyle([
        ("LEFTPADDING",   (0,0), (-1,-1), 4),
        ("RIGHTPADDING",  (0,0), (-1,-1), 4),
        ("TOPPADDING",    (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
    ]))

    # Right cell: TECNIMONT logo (left) + AMIRAL logo (far right) + "AMIRAL PROJECT" text
    w = doc.width
    if cl.is_official:
        tecni_img = Image(tecnimnt_path, width=3.0*cm, height=0.85*cm) if _os.path.exists(tecnimnt_path) else Paragraph("TECNIMONT", bold)
        amirl_img = Image(amiral_path,   width=1.5*cm, height=0.85*cm) if _os.path.exists(amiral_path)  else Paragraph("AMIRAL", bold)
    else:
        tecni_img = Paragraph("", bold)
        amirl_img = Paragraph("", bold)
    right_cell_data = [[
        tecni_img, "",  amirl_img,       # spacer column pushes AMIRAL logo to far right
    ],[
        Paragraph("<b>AMIRAL<br/>PROJECT</b>",
                  ParagraphStyle("ap", parent=styles["Normal"], fontSize=11,
                                 fontName="Helvetica-Bold", alignment=TA_CENTER,
                                 textColor=BLACK)),
        "", "",
    ]]
    right_cell_tbl = Table(right_cell_data, colWidths=[3.0*cm, "*", 1.7*cm])
    right_cell_tbl.setStyle(TableStyle([
        ("VALIGN",  (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN",   (0,0), (-1,-1), "CENTER"),
        ("SPAN",    (0,1), (2,1)),        # AMIRAL PROJECT spans all 3 cols — NO border
        ("TOPPADDING",    (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("LEFTPADDING",   (0,0), (-1,-1), 3),
        ("RIGHTPADDING",  (0,0), (-1,-1), 3),
    ]))

    # Main header 2-column table (matches DOCX row 1)
    hdr_tbl = Table([[left_cell_tbl, right_cell_tbl]],
                    colWidths=[w * 0.44, w * 0.56])
    hdr_tbl.setStyle(TableStyle([
        ("BOX",     (0,0), (-1,-1), 0.75, BLACK),
        ("INNERGRID",(0,0), (-1,-1), 0.75, BLACK),
        ("VALIGN",  (0,0), (-1,-1), "TOP"),
        ("BACKGROUND", (0,0), (0,0), GREY_D9),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",   (0,0), (-1,-1), 0),
        ("RIGHTPADDING",  (0,0), (-1,-1), 0),
    ]))
    story.append(hdr_tbl)

    # Date / Report No / Subcontractor row (matches DOCX row 2)
    date_str = cl.filled_date.strftime('%d / %m / %Y')
    info2 = Table([[
        Paragraph(f"<b>Inspection Date:</b>  {date_str}", norm),
        Paragraph(f"<b>SUBCONTRACTOR(s) Inspected:</b>  {cl.subcontractor or ''}", norm),
    ],[
        Paragraph(f"<b>Inspection Report N.:</b>  {cl.report_no}", norm),
        "",
    ]], colWidths=[w * 0.44, w * 0.56])
    info2.setStyle(TableStyle([
        ("BOX",      (0,0), (-1,-1), 0.75, BLACK),
        ("INNERGRID",(0,0), (-1,-1), 0.75, BLACK),
        ("VALIGN",   (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ("RIGHTPADDING",  (0,0), (-1,-1), 6),
        ("SPAN", (1,0), (1,1)),   # subcontractor cell spans both rows on right
    ]))
    story.append(info2)
    story.append(Spacer(1, 0.3*cm))

    # ── Checklist table ──
    has_na = cfg.get("has_na", False)
    col_good = cfg.get("col_good", "In good order / condition")
    col_imm  = cfg.get("col_imm",  "To be immediately improved")
    col_imp  = cfg.get("col_imp",  "To be improved")
    comp_hdr = cfg.get("compliance_header", "In case of non-compliance, indicate:")
    note_col = cfg.get("note_col", "Note")

    TICK = "✓"

    # Helper: checkbox cell — small nested table with visible border
    def _status_cell(checked):
        t = Table([["✓" if checked else ""]], colWidths=[0.4*cm], rowHeights=[0.38*cm])
        t.setStyle(TableStyle([
            ("BOX",           (0,0), (0,0), 0.5, BLACK),
            ("ALIGN",         (0,0), (0,0), "CENTER"),
            ("VALIGN",        (0,0), (0,0), "MIDDLE"),
            ("FONTSIZE",      (0,0), (0,0), 7),
            ("TOPPADDING",    (0,0), (0,0), 0),
            ("BOTTOMPADDING", (0,0), (0,0), 0),
            ("LEFTPADDING",   (0,0), (0,0), 0),
            ("RIGHTPADDING",  (0,0), (0,0), 0),
        ]))
        return t

    # Col widths for portrait A4 (usable ~17cm)
    num_status_cols = 3 + (1 if has_na else 0)
    fixed = (1.2*cm if has_na else 0)
    status_w = 1.3*cm
    area_sub_w = 3.0*cm
    note_w = 3.2*cm
    item_w = w - fixed - num_status_cols * status_w - area_sub_w - note_w

    col_widths = [item_w]
    if has_na:
        col_widths.append(1.5*cm)
    col_widths += [status_w, status_w, status_w, area_sub_w, note_w]

    ncols = len(col_widths)

    # Smaller style for status column header text (matches DOCX — small font in colored headers)
    hdr_sm = ParagraphStyle("hdr_sm", parent=styles["Normal"], fontSize=6.5, leading=8,
                            fontName="Helvetica-Bold", alignment=TA_CENTER)
    hdr_sm_b = ParagraphStyle("hdr_sm_b", parent=styles["Normal"], fontSize=6.5, leading=8,
                              fontName="Helvetica-Bold", alignment=TA_CENTER, textColor=BLACK)

    # First section name — placed in header col 0 (matches DOCX exactly)
    first_section = items[0].get("section", "") if items else ""
    first_sec_style = ParagraphStyle("fsc", parent=styles["Normal"], fontSize=7.5,
                                     fontName="Helvetica-BoldOblique", textColor=BLACK,
                                     leading=10)

    # 2-row header
    h0 = [Paragraph(first_section, first_sec_style)]
    if has_na:
        h0.append(Paragraph("<b>N/A</b>", hdr_sm_b))
    h0 += [
        Paragraph(f"<b>{col_good}</b>", hdr_sm_b),
        Paragraph(f"<b>{col_imm}</b>",  hdr_sm_b),
        Paragraph(f"<b>{col_imp}</b>",  hdr_sm_b),
        Paragraph(f"<b>{comp_hdr}</b>", hdr_sm_b),
        "",
    ]

    h1 = [""]
    if has_na:
        h1.append("")
    h1 += [
        Paragraph(f"<b>{TICK}</b>", hdr_sm_b),
        Paragraph(f"<b>{TICK}</b>", hdr_sm_b),
        Paragraph(f"<b>{TICK}</b>", hdr_sm_b),
        Paragraph("<b>Area / SUBCON.</b>", hdr_sm_b),
        Paragraph(f"<b>{note_col}</b>",    hdr_sm_b),
    ]

    tbl_rows = [h0, h1]
    sc0 = 2 if has_na else 1   # 0-indexed col of first status col (good/imm/imp)
    comp_col_start = ncols - 2  # compliance_header spans last 2 cols

    tbl_styles = [
        # ── Item/N/A header: DOCX grey D9D9D9, black text ──
        ("BACKGROUND", (0,0), (sc0-1, 1), GREY_D9),
        ("TEXTCOLOR",  (0,0), (sc0-1, 1), BLACK),
        # ── Status column headers: exact DOCX colors, black text ──
        ("BACKGROUND", (sc0,   0), (sc0,   1), HDR_GOOD),   # #92D050 lime
        ("BACKGROUND", (sc0+1, 0), (sc0+1, 1), HDR_IMM),    # #FF0000 red
        ("BACKGROUND", (sc0+2, 0), (sc0+2, 1), HDR_IMP),    # #FFFF00 yellow
        ("TEXTCOLOR",  (sc0,   0), (sc0+2, 1), BLACK),
        # ── Compliance cols: DOCX grey D9D9D9, black text ──
        ("BACKGROUND", (comp_col_start, 0), (-1, 1), GREY_D9),
        ("TEXTCOLOR",  (comp_col_start, 0), (-1, 1), BLACK),
        # ── Common header formatting ──
        ("FONTNAME",   (0,0), (-1,1), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 7.5),
        ("ALIGN",      (0,0), (-1,-1), "CENTER"),
        ("ALIGN",      (0,0), (0,-1), "LEFT"),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("GRID",       (0,0), (-1,-1), 0.5, BLACK),
        ("TOPPADDING",    (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("LEFTPADDING",   (0,0), (-1,-1), 3),
        ("RIGHTPADDING",  (0,0), (-1,-1), 3),
        # ── Span: Item spans rows 0-1 ──
        ("SPAN", (0,0), (0,1)),
    ]
    if has_na:
        tbl_styles.append(("SPAN", (1,0), (1,1)))
    tbl_styles.append(("SPAN", (comp_col_start, 0), (ncols-1, 0)))
    for c in range(sc0, sc0+3):
        tbl_styles.append(("SPAN", (c, 0), (c, 1)))

    # Section header style: grey D9D9D9, bold italic, black text (matches DOCX)
    sec_style = ParagraphStyle("sec", parent=styles["Normal"],
                               fontSize=8, leading=10,
                               fontName="Helvetica-BoldOblique",
                               textColor=BLACK)
    ck_style  = ParagraphStyle("ck", parent=norm, alignment=TA_CENTER)

    def _add_section_header(sec_name):
        """Insert a full 2-row header repeat (matches DOCX exactly) for a new section."""
        sh0 = [Paragraph(sec_name, first_sec_style)]
        if has_na:
            sh0.append(Paragraph("<b>N/A</b>", hdr_sm_b))
        sh0 += [
            Paragraph(f"<b>{col_good}</b>", hdr_sm_b),
            Paragraph(f"<b>{col_imm}</b>",  hdr_sm_b),
            Paragraph(f"<b>{col_imp}</b>",  hdr_sm_b),
            Paragraph(f"<b>{comp_hdr}</b>", hdr_sm_b),
            "",
        ]
        sh1 = [""]
        if has_na:
            sh1.append("")
        sh1 += [
            Paragraph(f"<b>{TICK}</b>", hdr_sm_b),
            Paragraph(f"<b>{TICK}</b>", hdr_sm_b),
            Paragraph(f"<b>{TICK}</b>", hdr_sm_b),
            Paragraph("<b>Area / SUBCON.</b>", hdr_sm_b),
            Paragraph(f"<b>{note_col}</b>",    hdr_sm_b),
        ]
        r0 = len(tbl_rows)
        tbl_rows.append(sh0)
        tbl_rows.append(sh1)
        r1 = r0 + 1
        # same styles as main header
        tbl_styles.extend([
            ("BACKGROUND", (0, r0), (sc0-1, r1), GREY_D9),
            ("TEXTCOLOR",  (0, r0), (sc0-1, r1), BLACK),
            ("BACKGROUND", (sc0,   r0), (sc0,   r1), HDR_GOOD),
            ("BACKGROUND", (sc0+1, r0), (sc0+1, r1), HDR_IMM),
            ("BACKGROUND", (sc0+2, r0), (sc0+2, r1), HDR_IMP),
            ("TEXTCOLOR",  (sc0,   r0), (sc0+2, r1), BLACK),
            ("BACKGROUND", (comp_col_start, r0), (-1, r1), GREY_D9),
            ("TEXTCOLOR",  (comp_col_start, r0), (-1, r1), BLACK),
            ("ALIGN",      (0, r0), (-1, r1), "CENTER"),
            ("VALIGN",     (0, r0), (-1, r1), "MIDDLE"),
            ("FONTNAME",   (0, r0), (-1, r1), "Helvetica-Bold"),
            ("FONTSIZE",   (0, r0), (-1, r1), 6.5),
            ("TOPPADDING",    (0, r0), (-1, r1), 2),
            ("BOTTOMPADDING", (0, r0), (-1, r1), 2),
            ("SPAN", (0, r0), (0, r1)),
            ("ALIGN", (0, r0), (0, r1), "LEFT"),
            ("SPAN", (comp_col_start, r0), (ncols-1, r0)),
        ])
        for c in range(sc0, sc0+3):
            tbl_styles.append(("SPAN", (c, r0), (c, r1)))
        if has_na:
            tbl_styles.append(("SPAN", (1, r0), (1, r1)))

    current_section = None
    for idx, it in enumerate(items):
        sec = it.get("section", "")
        if sec != current_section:
            current_section = sec
            if sec != first_section:   # first section is already in main header
                _add_section_header(sec)

        st  = it.get("status", "good")
        r   = len(tbl_rows)
        row = [Paragraph(it.get("item", ""), norm)]
        if has_na:
            row.append(_status_cell(st == "na"))
        row += [
            _status_cell(st == "good"),
            _status_cell(st == "immediately"),
            _status_cell(st == "improve"),
            Paragraph(it.get("area_sub", "") or "", sm),
            Paragraph(it.get("note", "") or "", sm),
        ]
        tbl_rows.append(row)
        # All data rows: white background (DOCX has no colored row backgrounds)

    cl_tbl = Table(tbl_rows, colWidths=col_widths, repeatRows=2)
    cl_tbl.setStyle(TableStyle(tbl_styles))
    story.append(cl_tbl)
    story.append(Spacer(1, 0.5*cm))

    # ── JSO / Notes section ──
    obs_raw = cl.observations or ""
    obs_data = {}
    if obs_raw.startswith("{"):
        try:
            obs_data = json.loads(obs_raw)
        except Exception:
            obs_data = {}

    jso_rows = obs_data.get("jso", []) if obs_data else []
    extra_notes = obs_data.get("notes", "") if obs_data else (obs_raw if obs_raw and not obs_raw.startswith("{") else "")

    # Notes label — italic, matches DOCX style
    story.append(Paragraph(
        "<i><b>Notes / Observations:</b> Short Description of observation. "
        "For more details, refer to the observations in JSO using the Ob.No.</i>",
        ParagraphStyle("obs_lbl", parent=styles["Normal"], fontSize=8, leading=10,
                       fontName="Helvetica-Oblique")))
    story.append(Spacer(1, 0.15*cm))

    # JSO table — plain black borders, bold header text (no colored background)
    jso_data = [[
        Paragraph("<b>JSO\nOb.No.</b>", boldC),
        Paragraph("<b>Short Description of Observation</b>", boldC),
    ]]
    for row in jso_rows:
        jso_data.append([Paragraph(row.get("no", ""), norm),
                         Paragraph(row.get("desc", ""), norm)])
    # Match DOCX: show many empty rows (at least 20 total)
    while len(jso_data) < 21:
        jso_data.append(["", ""])

    jso_tbl = Table(jso_data, colWidths=[2.5*cm, w - 2.5*cm])
    jso_tbl.setStyle(TableStyle([
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 8),
        ("GRID",       (0,0), (-1,-1), 0.5, BLACK),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN",      (0,0), (0,-1), "CENTER"),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 4),
        ("RIGHTPADDING",  (0,0), (-1,-1), 4),
    ]))
    story.append(jso_tbl)

    if extra_notes:
        story.append(Spacer(1, 0.2*cm))
        story.append(Paragraph(f"<i>{extra_notes}</i>", norm))

    story.append(Spacer(1, 0.4*cm))

    # ── Signatories table — matches DOCX exactly ──
    # Title row: light blue (C6D9F1), centered bold
    # Column headers: grey D9D9D9, bold italic
    # Data rows: white, tall (for signatures)
    display_name = cl.signatory_name or (cl.officer.name if cl.officer else "")
    officer_name = f"{display_name} — Environment Officer" if display_name else "— Environment Officer"

    # Build signature image cell if base64 data exists
    sig_img_cell = ""
    if cl.signature_data:
        try:
            import base64, io as _io
            _hdr, _b64 = cl.signature_data.split(",", 1)
            _raw = base64.b64decode(_b64)
            from reportlab.platypus import Image as RLImage
            sig_img_cell = RLImage(_io.BytesIO(_raw), width=3.0*cm, height=0.9*cm)
        except Exception:
            sig_img_cell = ""

    # Load attendees from DB
    import base64 as _b64mod, io as _io
    att_list = []
    if cl.attendees_sigs:
        try:
            att_list = json.loads(cl.attendees_sigs)
        except Exception:
            att_list = []

    def _att_sig_img(sig_str):
        if not sig_str:
            return ""
        try:
            _, b64 = sig_str.split(",", 1)
            raw = _b64mod.b64decode(b64)
            from reportlab.platypus import Image as RLImage
            return RLImage(_io.BytesIO(raw), width=3.0*cm, height=0.9*cm)
        except Exception:
            return ""

    sigh_style = ParagraphStyle("sigh", parent=styles["Normal"], fontSize=8,
                                fontName="Helvetica-BoldOblique", alignment=TA_CENTER)
    sig_data = [
        [Paragraph("<b>Signatories of Attendees</b>",
                   ParagraphStyle("sigt", parent=styles["Normal"], fontSize=9,
                                  fontName="Helvetica-Bold", alignment=TA_CENTER)), "", ""],
        [Paragraph("<i><b>Name and Role</b></i>", sigh_style),
         Paragraph("<i><b>Company</b></i>", sigh_style),
         Paragraph("<i><b>Signature</b></i>", sigh_style)],
        [Paragraph(officer_name, norm), "", sig_img_cell],
    ]
    # Add saved attendees
    for att in att_list:
        att_name_para = Paragraph(att.get("name", ""), norm)
        att_comp_para = Paragraph(att.get("company", ""), norm)
        sig_data.append([att_name_para, att_comp_para, _att_sig_img(att.get("sig", ""))])
    # Pad to minimum 4 data rows total
    while len(sig_data) < 6:
        sig_data.append(["", "", ""])

    n_rows = len(sig_data)
    row_h = [None, None] + [1.4*cm] * (n_rows - 2)
    sig_tbl = Table(sig_data, colWidths=["*", 4.5*cm, 4.5*cm], rowHeights=row_h)
    sig_tbl.setStyle(TableStyle([
        # Title row: blue background spanning all cols
        ("SPAN",       (0,0), (2,0)),
        ("BACKGROUND", (0,0), (2,0), SIG_BLUE),
        ("FONTNAME",   (0,0), (2,0), "Helvetica-Bold"),
        ("ALIGN",      (0,0), (2,0), "CENTER"),
        # Column headers: grey
        ("BACKGROUND", (0,1), (2,1), GREY_D9),
        ("FONTNAME",   (0,1), (2,1), "Helvetica-BoldOblique"),
        ("ALIGN",      (0,1), (2,1), "CENTER"),
        # All rows
        ("FONTSIZE",   (0,0), (-1,-1), 8),
        ("GRID",       (0,0), (-1,-1), 0.5, BLACK),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0), (2,1), 3),
        ("BOTTOMPADDING", (0,0), (2,1), 3),
        ("TOPPADDING",    (0,2), (-1,-1), 3),
        ("BOTTOMPADDING", (0,2), (-1,-1), 3),
        ("LEFTPADDING",   (0,0), (-1,-1), 5),
        ("RIGHTPADDING",  (0,0), (-1,-1), 5),
    ]))
    story.append(sig_tbl)

    # NA = Not applicable note
    if has_na:
        story.append(Spacer(1, 0.2*cm))
        story.append(Paragraph("<b>NA = Not applicable</b>",
                               ParagraphStyle("na", parent=styles["Normal"], fontSize=7.5,
                                              fontName="Helvetica-Bold")))

    story.append(Spacer(1, 0.3*cm))

    doc.build(story)
    fname = f"{cl.checklist_type}_{(cl.report_no or 'checklist').replace(' ', '_').replace('/', '-')}.pdf"
    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'inline; filename="{fname}"'
    return resp


@app.route("/env/checklist/<int:clid>/delete", methods=["POST"])
@login_required
@welfare_supervisor_required
def env_checklist_delete(clid):
    _c = cid()
    cl = EnvChecklist.query.filter_by(id=clid, company_id=_c).first_or_404()
    db.session.delete(cl)
    try:
        db.session.commit()
        flash("Checklist deleted.", "success")
    except Exception:
        db.session.rollback()
        flash("Error deleting checklist.", "danger")
    return redirect(url_for("welfare_supervisor"))


# ── Service Worker & Offline Page ────────────────────────────────────────────
# sw.js must be served from the root URL so its scope covers all pages.

@app.route('/sw.js')
def service_worker_js():
    response = send_file(
        os.path.join(BASE_DIR, 'static', 'js', 'sw.js'),
        mimetype='application/javascript'
    )
    # No caching — browser must always get the latest version to detect updates
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Service-Worker-Allowed'] = '/'
    return response


@app.route('/offline')
def offline_page():
    return render_template('offline.html')


# ── Offline Sync ──────────────────────────────────────────────────────────────
# Receives a JSON array of queued records from offline.js and saves them to DB.
# Path starts with /api/ so CSRF is skipped (handled by session auth check below).

@app.route("/api/sync", methods=["POST"])
def api_offline_sync():
    u = cur_user()
    if not u:
        return jsonify(error="Unauthorized"), 401
    if u.role not in ("safety_officer", "safety_supervisor", "safety_welfare",
                      "environment_officer", "admin", "super_admin"):
        return jsonify(error="Forbidden"), 403

    items = request.get_json(silent=True) or []
    failed = []

    for item in items:
        item_id   = item.get("id", "")
        item_type = item.get("type", "")
        data      = item.get("data") or {}
        try:
            _sync_dispatch(u, item_type, data)
        except Exception as exc:
            app.logger.warning("offline sync error [%s/%s]: %s", item_type, item_id, exc)
            failed.append(item_id)

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        app.logger.error("offline sync commit failed: %s", exc)
        return jsonify(error="Database error"), 500

    return jsonify(ok=True, failed=failed)


def _sync_dispatch(u, item_type, data):
    """Route one queued item to the right save function."""
    today = datetime.now(RIYADH_TZ).date()

    if item_type == "location":
        pkg  = data.get("pkg")
        unit = str(data.get("unit", "")).strip()
        area = str(data.get("area_text", "")).strip()
        if not pkg or not unit:
            raise ValueError("location: missing pkg or unit")
        loc = UserLocation.query.filter_by(user_id=u.id).first()
        if not loc:
            loc = UserLocation(user_id=u.id, company_id=u.company_id)
            db.session.add(loc)
        loc.pkg       = int(pkg)
        loc.unit      = unit
        loc.area_text = area
        loc.updated_at = datetime.utcnow()
        if u.role == "safety_officer":
            loc_text = f"PKG{pkg} Unit{unit}" + (f" - {area}" if area else "")
            ci = HseCheckin.query.filter_by(officer_id=u.id, date=today).first()
            if ci:
                ci.location = loc_text
            else:
                db.session.add(HseCheckin(officer_id=u.id, date=today,
                                          location=loc_text, company_id=u.company_id))

    elif item_type == "observation":
        obs_date   = _safe_date(data.get("date")) or today
        obs_type   = data.get("obs_type", "").strip()
        if not obs_type:
            raise ValueError("observation: obs_type required")
        db.session.add(HseObservation(
            officer_id  = u.id,
            date        = obs_date,
            location    = data.get("location", "").strip(),
            obs_type    = obs_type,
            category    = data.get("category", "").strip() or None,
            risk_level  = data.get("risk_level", "").strip() or None,
            description = data.get("description", "").strip(),
            action_taken= data.get("action_taken", "").strip(),
            company_id  = u.company_id,
        ))

    elif item_type == "tbt":
        tbt_date = _safe_date(data.get("date")) or today
        topic    = data.get("topic", "").strip()
        if not topic:
            raise ValueError("tbt: topic required")
        sup_code = data.get("supervisor_code", "").strip()
        supervisor = None
        if sup_code:
            supervisor = User.query.filter(
                User.supervisor_code == sup_code, User.is_active == True
            ).first()
        tbt = HseTbt(
            officer_id    = u.id,
            date          = tbt_date,
            topic         = topic,
            location      = data.get("location", "").strip(),
            supervisor_id = supervisor.id if supervisor else None,
            company_id    = u.company_id,
        )
        db.session.add(tbt)
        db.session.flush()
        emp_numbers = data.get("emp_number[]") or []
        emp_names   = data.get("emp_name[]")   or []
        if isinstance(emp_numbers, str): emp_numbers = [emp_numbers]
        if isinstance(emp_names,   str): emp_names   = [emp_names]
        for num, name in zip(emp_numbers, emp_names):
            num  = str(num).strip()
            name = str(name).strip()
            if num and name:
                db.session.add(HseTbtAttendance(
                    tbt_id=tbt.id, emp_number=num,
                    emp_name=name, company_id=u.company_id,
                ))

    elif item_type == "near_miss":
        nm_date         = _safe_date(data.get("date")) or today
        location        = data.get("location", "").strip()
        description     = data.get("description", "").strip()
        immediate_cause = data.get("immediate_cause", "").strip()
        action_taken    = data.get("action_taken", "").strip()
        reported_to     = data.get("reported_to", "").strip()
        if not all([location, description, immediate_cause, action_taken, reported_to]):
            raise ValueError("near_miss: all fields required")
        db.session.add(HseNearMiss(
            officer_id      = u.id,
            date            = nm_date,
            location        = location,
            description     = description,
            immediate_cause = immediate_cause,
            action_taken    = action_taken,
            reported_to     = reported_to,
            company_id      = u.company_id,
        ))

    elif item_type == "ptw_door":
        mod_seq  = int(data.get("_mod_seq") or 0)
        door_seq = int(data.get("_door_seq") or 0)
        if not mod_seq or not door_seq:
            raise ValueError("ptw_door: missing mod_seq or door_seq")
        # Reject if already submitted and not rejected
        existing = PtwDoorSubmission.query.filter_by(
            officer_id=u.id, module_seq=mod_seq, door_seq=door_seq
        ).first()
        if existing and existing.status in ("pending", "approved"):
            return  # already submitted, skip silently
        # Collect answers q1, q2, q3, ...
        answers = []
        i = 1
        while True:
            val = data.get(f"q{i}", "").strip()
            if not val and i > 10:
                break
            if val:
                answers.append(val)
            elif i > len(answers) + 3:
                break
            i += 1
        if existing and existing.status == "rejected":
            existing.answers     = json.dumps(answers, ensure_ascii=False)
            existing.photo_path  = None
            existing.status      = "pending"
            existing.submitted_at = datetime.utcnow()
        else:
            db.session.add(PtwDoorSubmission(
                officer_id=u.id, module_seq=mod_seq, door_seq=door_seq,
                answers=json.dumps(answers, ensure_ascii=False),
                photo_path=None, status="pending",
            ))

    elif item_type == "ptw_ref_door":
        mod_seq  = int(data.get("_mod_seq") or 0)
        door_seq = int(data.get("_door_seq") or 0)
        if not mod_seq or not door_seq:
            raise ValueError("ptw_ref_door: missing mod_seq or door_seq")
        existing = PtwDoorSubmission.query.filter_by(
            officer_id=u.id, module_seq=mod_seq, door_seq=door_seq
        ).first()
        if existing:
            return  # already marked, skip
        db.session.add(PtwDoorSubmission(
            officer_id=u.id, module_seq=mod_seq, door_seq=door_seq,
            answers=json.dumps([], ensure_ascii=False),
            photo_path=None, status="approved",
        ))

    elif item_type == "env_checklist":
        ctype = data.get("_ctype", "").strip()
        if ctype not in ENV_CHECKLISTS:
            raise ValueError(f"env_checklist: unknown ctype '{ctype}'")
        cfg = ENV_CHECKLISTS[ctype]
        item_count = int(data.get("_item_count") or 0)
        if not item_count:
            raise ValueError("env_checklist: missing _item_count")

        # Reconstruct items_data from s_N / a_N / n_N fields
        items_data = []
        flat_items = []
        for sec in cfg["sections"]:
            for item_text in sec["items"]:
                flat_items.append((sec["name"], item_text))
        for idx, (sec_name, item_text) in enumerate(flat_items[:item_count]):
            status = data.get(f"s_{idx}", "na" if cfg.get("has_na") else "good")
            items_data.append({
                "item":     item_text,
                "section":  sec_name,
                "status":   status,
                "area_sub": str(data.get(f"a_{idx}", "") or "").strip(),
                "note":     str(data.get(f"n_{idx}", "") or "").strip(),
            })

        # area_types: may be single string, list, or False
        area_types_raw = data.get("area_types")
        if isinstance(area_types_raw, list):
            checked_areas = area_types_raw
        elif area_types_raw and area_types_raw is not True and area_types_raw is not False:
            checked_areas = [str(area_types_raw)]
        else:
            checked_areas = []
        area_other = str(data.get("area_other") or "").strip()
        area_detail = str(data.get("area_detail") or "").strip()
        area_parts  = checked_areas + ([area_other] if area_other else [])
        area_str    = (", ".join(area_parts) + (" — " + area_detail if area_detail else ""))[:199]

        # JSO rows
        jso_rows = []
        for i in range(4):
            jno  = str(data.get(f"jso_no_{i}") or "").strip()
            jdsc = str(data.get(f"jso_desc_{i}") or "").strip()
            if jno or jdsc:
                jso_rows.append({"no": jno, "desc": jdsc})

        filled_date_str = str(data.get("filled_date") or "").strip()
        try:
            filled_date = date.fromisoformat(filled_date_str)
        except (ValueError, TypeError):
            filled_date = datetime.now(RIYADH_TZ).date()

        count = EnvChecklist.query.filter_by(company_id=u.company_id, checklist_type=ctype).count() + 1
        report_no = f"{cfg['report_prefix']} #{count:03d} / {filled_date.year}"
        custom_no = str(data.get("report_no_custom") or "").strip()
        if custom_no:
            report_no = f"{cfg['report_prefix']} #{custom_no} / {filled_date.year}"

        obs_payload = json.dumps(
            {"jso": jso_rows, "notes": str(data.get("observations") or "").strip()},
            ensure_ascii=False)

        db.session.add(EnvChecklist(
            company_id      = u.company_id,
            officer_id      = u.id,
            checklist_type  = ctype,
            area            = area_str,
            report_no       = report_no,
            subcontractor   = str(data.get("subcontractor") or "").strip(),
            filled_date     = filled_date,
            items_json      = json.dumps(items_data, ensure_ascii=False),
            observations    = obs_payload,
        ))

    else:
        raise ValueError(f"unknown type: {item_type}")


# ══════════════════════════════════════════════════════════════════════
# TRAINEE WEEKLY REPORT GENERATOR  —  /hse/trainee-report
# ══════════════════════════════════════════════════════════════════════

TRAINEE_TEMPLATE_PATH = os.path.join(BASE_DIR, "uploads", "hse",
                                     "trainee_report_template.pptx")


@app.route("/hse/trainee-report", methods=["GET"])
@login_required
@hse_supervisor_required
def hse_trainee_report():
    template_exists = os.path.exists(TRAINEE_TEMPLATE_PATH)
    return render_template("hse_trainee_report.html",
                           template_exists=template_exists)


@app.route("/hse/trainee-report/upload-template", methods=["POST"])
@login_required
@hse_supervisor_required
def hse_trainee_report_upload_template():
    f = request.files.get("template")
    if not f or not f.filename.lower().endswith(".pptx"):
        flash("يرجى رفع ملف PPTX صحيح", "error")
        return redirect(url_for("hse_trainee_report"))
    os.makedirs(os.path.dirname(TRAINEE_TEMPLATE_PATH), exist_ok=True)
    f.save(TRAINEE_TEMPLATE_PATH)
    flash("تم رفع القالب بنجاح ✓", "success")
    return redirect(url_for("hse_trainee_report"))


@app.route("/hse/trainee-report/generate", methods=["POST"])
@login_required
@hse_supervisor_required
def hse_trainee_report_generate():
    if not os.path.exists(TRAINEE_TEMPLATE_PATH):
        flash("القالب غير موجود — يرجى رفعه أولاً", "error")
        return redirect(url_for("hse_trainee_report"))

    try:
        from trainee_report_generator import generate_report

        # Mandatory files
        hse_file  = request.files.get("hse_pdf")
        ptw_file  = request.files.get("ptw_pdf")
        csv_file  = request.files.get("progress_csv")
        obs_files = request.files.getlist("obs_pdfs")

        missing = []
        if not hse_file  or not hse_file.filename:  missing.append("HSE Weekly Report PDF")
        if not ptw_file  or not ptw_file.filename:  missing.append("PTW Weekly Report PDF")
        if not csv_file  or not csv_file.filename:  missing.append("Progress CSV")
        if not obs_files or not obs_files[0].filename: missing.append("ملفات الاوبزرفيشنز اليومية")
        if missing:
            flash("الملفات الناقصة: " + ", ".join(missing), "error")
            return redirect(url_for("hse_trainee_report"))

        obs_list = [(f.filename, f.read()) for f in obs_files if f.filename]

        pptx_bytes = generate_report(
            template_path=TRAINEE_TEMPLATE_PATH,
            csv_content=csv_file.read(),
            hse_pdf=hse_file.read(),
            ptw_pdf=ptw_file.read(),
            obs_pdfs=obs_list,
        )

        from flask import make_response
        now_str  = datetime.now(RIYADH_TZ).strftime("%Y%m%d_%H%M")
        filename = f"Trainee_Weekly_Report_{now_str}.pptx"

        resp = make_response(pptx_bytes)
        resp.headers["Content-Type"] = (
            "application/vnd.openxmlformats-officedocument"
            ".presentationml.presentation"
        )
        resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp

    except Exception as e:
        app.logger.exception("Trainee report generation failed")
        flash(f"حدث خطأ أثناء التوليد: {e}", "error")
        return redirect(url_for("hse_trainee_report"))


@app.route("/hse/trainee-report/generate-auto", methods=["POST"])
@login_required
@hse_supervisor_required
def hse_trainee_report_generate_auto():
    """Generate trainee report entirely from DB — no file uploads needed."""
    if not os.path.exists(TRAINEE_TEMPLATE_PATH):
        flash("القالب غير موجود — يرجى رفعه أولاً", "error")
        return redirect(url_for("hse_trainee_report"))

    try:
        from trainee_report_generator import generate_report_from_data
        from models.lms import (LmsEnrollment, LmsModuleProgress,
                                 LmsModule, LmsCourse)

        _c   = cid()
        now  = datetime.now(RIYADH_TZ)
        # Current ISO week: Monday–Sunday
        week_start = (now - timedelta(days=now.weekday())).date()
        week_end   = week_start + timedelta(days=6)

        # ── 1. E-Learning progress (csv_data) ────────────────────────
        # Trainees = safety_officer / safety_welfare / environment_officer
        trainees_qs = User.query.filter(
            User.company_id == _c,
            User.role.in_(["safety_officer", "safety_welfare", "environment_officer"]),
        ).order_by(User.name).all()

        STATUS_MAP = {True: "Passed", None: "In Progress", False: "—"}

        csv_trainees = []
        for u_tr in trainees_qs:
            enroll = LmsEnrollment.query.filter_by(
                officer_id=u_tr.id
            ).first()
            modules_data = []
            if enroll:
                progresses = {
                    mp.module_id: mp
                    for mp in LmsModuleProgress.query.filter_by(
                        enrollment_id=enroll.id
                    ).all()
                }
                # Get modules ordered by position
                course_modules = (LmsModule.query
                                  .filter_by(course_id=enroll.course_id)
                                  .order_by(LmsModule.seq)
                                  .limit(7).all())
                for mod in course_modules:
                    mp = progresses.get(mod.id)
                    if mp is None:
                        status, score = "—", ""
                    elif mp.passed_at:
                        status = "Passed"
                        score  = str(mp.best_score or "")
                    elif mp.attempts_used > 0 or mp.content_opened_at:
                        status, score = "In Progress", ""
                    else:
                        status, score = "—", ""
                    modules_data.append({"score": score, "status": status})
            # Pad to 7 modules
            while len(modules_data) < 7:
                modules_data.append({"score": "", "status": "—"})
            csv_trainees.append({"name": u_tr.name, "modules": modules_data})

        csv_data = {"trainees": csv_trainees}

        # ── 2. PTW Field Training (ptw_data) ─────────────────────────
        # All officers (trainee or not) who submitted PTW doors this week
        ptw_week_subs = PtwDoorSubmission.query.join(
            User, PtwDoorSubmission.officer_id == User.id
        ).filter(
            User.company_id == _c,
            PtwDoorSubmission.submitted_at >= datetime.combine(week_start, datetime.min.time()),
            PtwDoorSubmission.submitted_at <= datetime.combine(week_end,   datetime.max.time()),
        ).all()

        # Collect all officers who ever submitted (for per-module stages)
        all_ptw_subs = PtwDoorSubmission.query.join(
            User, PtwDoorSubmission.officer_id == User.id
        ).filter(User.company_id == _c).all()

        # Group by officer
        from collections import defaultdict
        officer_all   = defaultdict(list)
        officer_week  = defaultdict(list)
        officer_ids   = set()
        for sub in all_ptw_subs:
            officer_all[sub.officer_id].append(sub)
            officer_ids.add(sub.officer_id)
        for sub in ptw_week_subs:
            officer_week[sub.officer_id].append(sub)

        ptw_trainees = []
        total_approved_week = 0
        for oid in officer_ids:
            u_obj = User.query.get(oid)
            if not u_obj:
                continue
            week_subs  = officer_week[oid]
            all_subs_o = officer_all[oid]

            sub_w = len(week_subs)
            app_w = sum(1 for s in week_subs if s.status == "approved")
            rej_w = sum(1 for s in week_subs if s.status == "rejected")
            pend  = sum(1 for s in week_subs if s.status == "pending")
            total_approved_week += app_w

            total_app_all = sum(1 for s in all_subs_o if s.status == "approved")
            overall = f"{total_app_all}/35"
            pct     = int(total_app_all / 35 * 100)

            # Per-module stages (5 doors each, 7 modules)
            per_module = []
            for mi in range(1, 8):
                mod_subs = [s for s in all_subs_o if s.module_seq == mi]
                approved = sum(1 for s in mod_subs if s.status == "approved")
                total_m  = len(mod_subs)
                if approved == 5:
                    per_module.append("5/5")
                elif approved > 0 or total_m > 0:
                    per_module.append(f"{approved}/5")
                else:
                    per_module.append("—")

            # Current module = first module with incomplete approved count
            cur_mod = "Module 1"
            for mi, val in enumerate(per_module, 1):
                if val != "5/5":
                    cur_mod = f"Module {mi}"
                    break

            ptw_trainees.append({
                "name":       u_obj.name,
                "cur_module": cur_mod,
                "sub_week":   sub_w,
                "app_week":   app_w,
                "rej_week":   rej_w,
                "pending":    pend,
                "overall":    overall,
                "pct":        pct,
                "per_module": per_module,
                "active":     sub_w > 0,
            })

        ptw_data = {
            "week_label": f"{week_start.strftime('%d %b')} – {week_end.strftime('%d %b %Y')}",
            "active":     len([t for t in ptw_trainees if t["active"]]),
            "submitted":  sum(t["sub_week"] for t in ptw_trainees),
            "approved":   total_approved_week,
            "rejected":   sum(t["rej_week"] for t in ptw_trainees),
            "trainees":   ptw_trainees,
        }

        # ── 3. HSE Observations (hse_data) ───────────────────────────
        week_obs = HseObservation.query.filter(
            HseObservation.company_id == _c,
            HseObservation.date >= week_start,
            HseObservation.date <= week_end,
        ).all()

        _all_officer_ids = [u_o.id for u_o in User.query.filter_by(company_id=_c).all()]
        week_tbts = HseTbt.query.filter(
            HseTbt.officer_id.in_(_all_officer_ids),
            HseTbt.date >= week_start,
            HseTbt.date <= week_end,
        ).all()
        tbt_attend_total = sum(
            tbt.attendance.count() for tbt in week_tbts
        )

        all_officers_qs = User.query.filter_by(
            company_id=_c, role="safety_officer"
        ).all()
        officers_active_ids = {o.date for o in week_obs}

        hse_data = {
            "week_label":      f"{week_start.strftime('%d %b')} – {week_end.strftime('%d %b %Y')}",
            "total_obs":       len(week_obs),
            "high_risk":       sum(1 for o in week_obs if o.risk_level == "H"),
            "jso_closures":    HseJsoClosure.query.filter(
                                   HseJsoClosure.company_id == _c,
                                   HseJsoClosure.date >= week_start,
                                   HseJsoClosure.date <= week_end,
                               ).count(),
            "tbt_sessions":    len(week_tbts),
            "tbt_attend":      tbt_attend_total,
            "officers_active": len({o.officer_id for o in week_obs}),
            "officers_total":  len(all_officers_qs),
        }

        # ── 4. Daily obs data (obs_data) ─────────────────────────────
        from collections import defaultdict as _dd
        day_obs = _dd(list)
        for o in week_obs:
            day_obs[o.date].append(o)

        day_tbts = _dd(list)
        for tbt in week_tbts:
            day_tbts[tbt.date].append(tbt)

        obs_data = []
        for day in sorted(day_obs.keys()):
            obs_list_day = day_obs[day]
            tbts_day     = day_tbts[day]
            tbt_attend_day = sum(t.attendance.count() for t in tbts_day)

            sgl_sessions = []
            for tbt in tbts_day:
                officer_u = User.query.get(tbt.officer_id)
                sgl_sessions.append({
                    "num":      str(len(sgl_sessions) + 1),
                    "topic":    tbt.topic or "—",
                    "location": tbt.location or "—",
                    "officer":  officer_u.name if officer_u else "—",
                    "attend":   tbt.attendance.count(),
                })

            key_obs = []
            for o in obs_list_day:
                if o.description and len(o.description) > 15 and len(key_obs) < 4:
                    key_obs.append(o.description[:80])

            obs_data.append({
                "date":        day.strftime("%d %b %Y"),
                "date_label":  day.strftime("%a %d %b"),
                "total_obs":   len(obs_list_day),
                "sgl_count":   len(tbts_day),
                "sgl_attend":  tbt_attend_day,
                "high":        sum(1 for o in obs_list_day if o.risk_level == "H"),
                "medium":      sum(1 for o in obs_list_day if o.risk_level == "M"),
                "low":         sum(1 for o in obs_list_day if o.risk_level == "L"),
                "positive":    sum(1 for o in obs_list_day if o.obs_type == "positive"),
                "key_obs":     key_obs,
                "sgl_sessions": sgl_sessions,
            })

        # ── 5. Generate ───────────────────────────────────────────────
        pptx_bytes = generate_report_from_data(
            template_path=TRAINEE_TEMPLATE_PATH,
            csv_data=csv_data,
            hse_data=hse_data,
            ptw_data=ptw_data,
            obs_data=obs_data,
        )

        from flask import make_response
        now_str  = now.strftime("%Y%m%d_%H%M")
        filename = f"Trainee_Weekly_Report_{now_str}.pptx"
        resp = make_response(pptx_bytes)
        resp.headers["Content-Type"] = (
            "application/vnd.openxmlformats-officedocument"
            ".presentationml.presentation"
        )
        resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp

    except Exception as e:
        app.logger.exception("Auto trainee report generation failed")
        flash(f"حدث خطأ أثناء التوليد التلقائي: {e}", "error")
        return redirect(url_for("hse_trainee_report"))


@app.get("/api/hse/trainees")
@api_hse_supervisor_required
def api_hse_trainees():
    """Return list of trainees (safety_officer / safety_welfare / environment_officer)."""
    _c = cid()
    trainees = User.query.filter(
        User.company_id == _c,
        User.role.in_(["safety_officer", "safety_welfare", "environment_officer"]),
        User.is_active == True,
    ).order_by(User.role, User.name).all()
    return jsonify([{"id": u.id, "name": u.name, "code": u.employee_id or str(u.id),
                     "role": u.role} for u in trainees])


@app.post("/api/hse/trainee-report/generate-auto")
@api_hse_supervisor_required
def api_hse_trainee_report_generate_auto():
    """Mobile API: generate trainee weekly report from DB using build_report (no template needed)."""
    try:
        from trainee_report_builder import build_report
        from models.lms import (LmsEnrollment, LmsModuleProgress, LmsModule)
        from collections import defaultdict as _dd

        _c   = cid()
        now  = datetime.now(RIYADH_TZ)
        week_start = (now - timedelta(days=now.weekday())).date()
        week_end   = week_start + timedelta(days=6)
        week_start_dt = datetime.combine(week_start, datetime.min.time())
        week_end_dt   = datetime.combine(week_end,   datetime.max.time())

        body = request.get_json(silent=True) or {}
        selected_ids = body.get("trainee_ids") or []

        trainees_base = User.query.filter(
            User.company_id == _c,
            User.role.in_(["safety_officer", "safety_welfare", "environment_officer"]),
        ).order_by(User.name).all()
        trainees_qs = [t for t in trainees_base if t.id in selected_ids] if selected_ids else trainees_base

        # ── E-Learning + PTW per trainee ──────────────────────────────
        trainees_out = []
        for u_tr in trainees_qs:
            enroll = LmsEnrollment.query.filter_by(officer_id=u_tr.id).first()
            modules_data, new_passes = [], []
            if enroll:
                progresses = {mp.module_id: mp for mp in LmsModuleProgress.query.filter_by(enrollment_id=enroll.id).all()}
                course_modules = LmsModule.query.filter_by(course_id=enroll.course_id).order_by(LmsModule.seq).limit(7).all()
                for idx, mod in enumerate(course_modules):
                    mp = progresses.get(mod.id)
                    if mp is None:
                        status, score = "—", ""
                    elif mp.passed_at:
                        status, score = "Passed", str(mp.best_score or "")
                        if week_start_dt <= mp.passed_at <= week_end_dt:
                            new_passes.append(idx)
                    elif mp.attempts_used > 0 or mp.content_opened_at:
                        status, score = "In Progress", ""
                    else:
                        status, score = "—", ""
                    modules_data.append({"status": status, "score": score})
            while len(modules_data) < 7:
                modules_data.append({"status": "—", "score": ""})

            all_subs_tr = PtwDoorSubmission.query.filter_by(officer_id=u_tr.id).all()
            ptw_per_module = []
            ptw_stages_week = sum(1 for s in all_subs_tr if s.status == "approved"
                                  and s.submitted_at and week_start_dt <= s.submitted_at <= week_end_dt)
            for mi in range(1, 8):
                mod_subs = [s for s in all_subs_tr if s.module_seq == mi]
                approved = sum(1 for s in mod_subs if s.status == "approved")
                ptw_per_module.append("5/5" if approved == 5 else (f"{approved}/5" if approved > 0 or mod_subs else "—"))
            total_ptw_app = sum(1 for s in all_subs_tr if s.status == "approved")

            obs_tr = HseObservation.query.filter(
                HseObservation.officer_id == u_tr.id,
                HseObservation.date >= week_start,
                HseObservation.date <= week_end,
            ).all()

            trainees_out.append({
                "name": u_tr.name, "modules": modules_data, "new_passes": new_passes,
                "ptw_per_module": ptw_per_module, "ptw_overall": f"{total_ptw_app}/35",
                "ptw_stages_this_week": ptw_stages_week,
                "obs_count": len(obs_tr), "obs_high": sum(1 for o in obs_tr if o.risk_level == "H"),
            })

        # ── HSE aggregate ─────────────────────────────────────────────
        week_obs = HseObservation.query.filter(
            HseObservation.company_id == _c,
            HseObservation.date >= week_start, HseObservation.date <= week_end,
        ).all()
        _co_ids = [u.id for u in User.query.filter_by(company_id=_c).all()]
        week_tbts = HseTbt.query.filter(
            HseTbt.officer_id.in_(_co_ids),
            HseTbt.date >= week_start, HseTbt.date <= week_end,
        ).all()
        tbt_attend_total = sum(t.attendance.count() for t in week_tbts)
        jso_count = HseJsoClosure.query.filter(
            HseJsoClosure.company_id == _c,
            HseJsoClosure.date >= week_start, HseJsoClosure.date <= week_end,
        ).count()
        hse_agg = {
            "total_obs": len(week_obs), "high": sum(1 for o in week_obs if o.risk_level == "H"),
            "medium": sum(1 for o in week_obs if o.risk_level == "M"),
            "low": sum(1 for o in week_obs if o.risk_level == "L"),
            "positive": sum(1 for o in week_obs if o.obs_type == "positive"),
            "tbt_sessions": len(week_tbts), "tbt_attend": tbt_attend_total,
            "officers_active": len({o.officer_id for o in week_obs}), "jso_closures": jso_count,
        }

        # ── PTW summary ───────────────────────────────────────────────
        all_week_ptw = PtwDoorSubmission.query.join(User, PtwDoorSubmission.officer_id == User.id).filter(
            User.company_id == _c,
            PtwDoorSubmission.submitted_at >= week_start_dt,
            PtwDoorSubmission.submitted_at <= week_end_dt,
        ).all()
        ptw_summary = {
            "submitted": len(all_week_ptw),
            "approved":  sum(1 for s in all_week_ptw if s.status == "approved"),
            "rejected":  sum(1 for s in all_week_ptw if s.status == "rejected"),
            "pending":   sum(1 for s in all_week_ptw if s.status == "pending"),
        }

        # ── Daily obs breakdown ───────────────────────────────────────
        day_obs_map  = _dd(list)
        day_tbts_map = _dd(list)
        for o in week_obs: day_obs_map[o.date].append(o)
        for t in week_tbts: day_tbts_map[t.date].append(t)
        obs_by_day = []
        for day in sorted(set(list(day_obs_map.keys()) + list(day_tbts_map.keys()))):
            obs_d  = day_obs_map[day]; tbts_d = day_tbts_map[day]
            key_obs = [o.description[:80] for o in obs_d if o.description and len(o.description) > 15][:4]
            obs_by_day.append({
                "date_label": day.strftime("%a %d %b"), "total": len(obs_d),
                "high": sum(1 for o in obs_d if o.risk_level == "H"),
                "medium": sum(1 for o in obs_d if o.risk_level == "M"),
                "low": sum(1 for o in obs_d if o.risk_level == "L"),
                "positive": sum(1 for o in obs_d if o.obs_type == "positive"),
                "tbt": len(tbts_d), "tbt_attend": sum(t.attendance.count() for t in tbts_d),
                "key_obs": key_obs,
            })

        # ── TBT sessions list ─────────────────────────────────────────
        tbt_sessions_list = [{"topic": t.topic or "—",
                               "officer": (User.query.get(t.officer_id).name if User.query.get(t.officer_id) else "—"),
                               "location": t.location or "—", "attend": t.attendance.count()} for t in week_tbts]

        # ── Delta vs previous week ────────────────────────────────────
        prev_start = week_start - timedelta(days=7)
        prev_end   = week_start - timedelta(days=1)
        prev_obs   = HseObservation.query.filter(HseObservation.company_id == _c,
                        HseObservation.date >= prev_start, HseObservation.date <= prev_end).count()
        prev_tbts  = HseTbt.query.filter(HseTbt.officer_id.in_(_co_ids),
                        HseTbt.date >= prev_start, HseTbt.date <= prev_end).count()
        delta = {
            "modules_passed": sum(len(t["new_passes"]) for t in trainees_out),
            "ptw_stages":     sum(t["ptw_stages_this_week"] for t in trainees_out),
            "obs":            len(week_obs) - prev_obs,
            "tbt":            len(week_tbts) - prev_tbts,
        }

        # ── Obs detail slide ──────────────────────────────────────────
        obs_detail = []
        for o in sorted(week_obs, key=lambda x: (x.date, x.id)):
            u_o = User.query.get(o.officer_id)
            obs_detail.append({
                "date": o.date.strftime("%a %d %b"), "officer": u_o.name if u_o else "—",
                "location": o.location or "—", "risk": o.risk_level or "—",
                "obs_type": o.obs_type or "—", "description": (o.description or "")[:120],
                "action": (o.action_taken or "")[:80], "status": o.status or "open",
            })

        company_obj  = Company.query.get(_c)
        company_name = company_obj.name if company_obj else ""

        data = {
            "week_start": week_start, "week_end": week_end,
            "company_name": company_name, "generated_at": now,
            "trainees": trainees_out, "hse": hse_agg,
            "ptw_summary": ptw_summary, "obs_by_day": obs_by_day,
            "obs_detail": obs_detail, "tbt_sessions": tbt_sessions_list,
            "delta": delta,
        }

        pptx_bytes = build_report(data)
        ws_str   = week_start.strftime("%Y%m%d")
        we_str   = week_end.strftime("%Y%m%d")
        filename = f"Trainee_Report_{ws_str}_{we_str}.pptx"
        resp = make_response(pptx_bytes)
        resp.headers["Content-Type"] = ("application/vnd.openxmlformats-officedocument"
                                        ".presentationml.presentation")
        resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp
    except Exception as e:
        app.logger.exception("API trainee report generation failed")
        return jsonify({"error": str(e)}), 500


@app.route("/hse/trainee-report/filter", methods=["GET"])
@login_required
@hse_supervisor_required
def hse_trainee_report_filter():
    """Filter page: select trainees and week before generating the new report."""
    _c = cid()
    trainees_qs = User.query.filter(
        User.company_id == _c,
        User.role.in_(["safety_officer", "safety_welfare", "environment_officer"]),
    ).order_by(User.name).all()

    now        = datetime.now(RIYADH_TZ)
    week_start = (now - timedelta(days=now.weekday())).date()
    week_end   = week_start + timedelta(days=6)

    return render_template(
        "hse_trainee_report_filter.html",
        trainees=trainees_qs,
        week_start=week_start.isoformat(),
        week_end=week_end.isoformat(),
    )


@app.route("/hse/trainee-report/generate-v2", methods=["POST"])
@login_required
@hse_supervisor_required
def hse_trainee_report_generate_v2():
    """Generate the new-style report (trainee_report_builder) from DB."""
    try:
        from trainee_report_builder import build_report
        from models.lms import (LmsEnrollment, LmsModuleProgress,
                                 LmsModule, LmsCourse)

        _c = cid()
        now = datetime.now(RIYADH_TZ)

        # Week range from form
        try:
            week_start = date.fromisoformat(request.form["week_start"])
            week_end   = date.fromisoformat(request.form["week_end"])
        except (KeyError, ValueError):
            week_start = (now - timedelta(days=now.weekday())).date()
            week_end   = week_start + timedelta(days=6)

        # Selected trainee IDs (empty = all)
        selected_ids_raw = request.form.getlist("trainee_ids")
        selected_ids = [int(x) for x in selected_ids_raw if x.isdigit()]

        trainees_base = User.query.filter(
            User.company_id == _c,
            User.role.in_(["safety_officer", "safety_welfare", "environment_officer"]),
        ).order_by(User.name).all()

        if selected_ids:
            trainees_qs = [t for t in trainees_base if t.id in selected_ids]
        else:
            trainees_qs = trainees_base

        week_start_dt = datetime.combine(week_start, datetime.min.time())
        week_end_dt   = datetime.combine(week_end,   datetime.max.time())

        # ── E-Learning ────────────────────────────────────────────────
        trainees_out = []
        for u_tr in trainees_qs:
            enroll = LmsEnrollment.query.filter_by(
                officer_id=u_tr.id
            ).first()
            modules_data, new_passes = [], []
            if enroll:
                progresses = {
                    mp.module_id: mp
                    for mp in LmsModuleProgress.query.filter_by(
                        enrollment_id=enroll.id
                    ).all()
                }
                course_modules = (LmsModule.query
                                  .filter_by(course_id=enroll.course_id)
                                  .order_by(LmsModule.seq)
                                  .limit(7).all())
                for idx, mod in enumerate(course_modules):
                    mp = progresses.get(mod.id)
                    if mp is None:
                        status, score = "—", ""
                    elif mp.passed_at:
                        status = "Passed"
                        score  = str(mp.best_score or "")
                        if week_start_dt <= mp.passed_at <= week_end_dt:
                            new_passes.append(idx)
                    elif mp.attempts_used > 0 or mp.content_opened_at:
                        status, score = "In Progress", ""
                    else:
                        status, score = "—", ""
                    modules_data.append({"status": status, "score": score})
            while len(modules_data) < 7:
                modules_data.append({"status": "—", "score": ""})

            # PTW per-module stages (all-time approved)
            all_subs_tr = PtwDoorSubmission.query.filter_by(
                officer_id=u_tr.id
            ).all()
            ptw_per_module = []
            ptw_stages_week = sum(
                1 for s in all_subs_tr
                if s.status == "approved"
                and s.submitted_at
                and week_start_dt <= s.submitted_at <= week_end_dt
            )
            for mi in range(1, 8):
                mod_subs = [s for s in all_subs_tr if s.module_seq == mi]
                approved = sum(1 for s in mod_subs if s.status == "approved")
                if approved == 5:
                    ptw_per_module.append("5/5")
                elif approved > 0 or mod_subs:
                    ptw_per_module.append(f"{approved}/5")
                else:
                    ptw_per_module.append("—")

            total_ptw_app = sum(1 for s in all_subs_tr if s.status == "approved")

            # HSE obs this week
            obs_tr = HseObservation.query.filter(
                HseObservation.officer_id == u_tr.id,
                HseObservation.date >= week_start,
                HseObservation.date <= week_end,
            ).all()

            trainees_out.append({
                "name":               u_tr.name,
                "modules":            modules_data,
                "new_passes":         new_passes,
                "ptw_per_module":     ptw_per_module,
                "ptw_overall":        f"{total_ptw_app}/35",
                "ptw_stages_this_week": ptw_stages_week,
                "obs_count":          len(obs_tr),
                "obs_high":           sum(1 for o in obs_tr if o.risk_level == "H"),
            })

        # ── HSE aggregate ─────────────────────────────────────────────
        week_obs = HseObservation.query.filter(
            HseObservation.company_id == _c,
            HseObservation.date >= week_start,
            HseObservation.date <= week_end,
        ).all()
        _co_officer_ids = [u_o.id for u_o in User.query.filter_by(company_id=_c).all()]
        week_tbts = HseTbt.query.filter(
            HseTbt.officer_id.in_(_co_officer_ids),
            HseTbt.date >= week_start,
            HseTbt.date <= week_end,
        ).all()
        tbt_attend_total = sum(tbt.attendance.count() for tbt in week_tbts)
        jso_count = HseJsoClosure.query.filter(
            HseJsoClosure.company_id == _c,
            HseJsoClosure.date >= week_start,
            HseJsoClosure.date <= week_end,
        ).count()

        hse_agg = {
            "total_obs":      len(week_obs),
            "high":           sum(1 for o in week_obs if o.risk_level == "H"),
            "medium":         sum(1 for o in week_obs if o.risk_level == "M"),
            "low":            sum(1 for o in week_obs if o.risk_level == "L"),
            "positive":       sum(1 for o in week_obs if o.obs_type == "positive"),
            "tbt_sessions":   len(week_tbts),
            "tbt_attend":     tbt_attend_total,
            "officers_active": len({o.officer_id for o in week_obs}),
            "jso_closures":   jso_count,
        }

        # ── PTW summary ───────────────────────────────────────────────
        all_week_ptw = PtwDoorSubmission.query.join(
            User, PtwDoorSubmission.officer_id == User.id
        ).filter(
            User.company_id == _c,
            PtwDoorSubmission.submitted_at >= week_start_dt,
            PtwDoorSubmission.submitted_at <= week_end_dt,
        ).all()

        ptw_summary = {
            "submitted": len(all_week_ptw),
            "approved":  sum(1 for s in all_week_ptw if s.status == "approved"),
            "rejected":  sum(1 for s in all_week_ptw if s.status == "rejected"),
            "pending":   sum(1 for s in all_week_ptw if s.status == "pending"),
        }

        # ── Daily obs breakdown ───────────────────────────────────────
        from collections import defaultdict as _dd
        day_obs_map  = _dd(list)
        day_tbts_map = _dd(list)
        for o in week_obs:
            day_obs_map[o.date].append(o)
        for tbt in week_tbts:
            day_tbts_map[tbt.date].append(tbt)

        obs_by_day = []
        all_days = sorted(set(list(day_obs_map.keys()) + list(day_tbts_map.keys())))
        for day in all_days:
            obs_d  = day_obs_map[day]
            tbts_d = day_tbts_map[day]
            tbt_attend_d = sum(t.attendance.count() for t in tbts_d)
            key_obs = [o.description[:80] for o in obs_d
                       if o.description and len(o.description) > 15][:4]
            obs_by_day.append({
                "date_label": day.strftime("%a %d %b"),
                "total":      len(obs_d),
                "high":       sum(1 for o in obs_d if o.risk_level == "H"),
                "medium":     sum(1 for o in obs_d if o.risk_level == "M"),
                "low":        sum(1 for o in obs_d if o.risk_level == "L"),
                "positive":   sum(1 for o in obs_d if o.obs_type == "positive"),
                "tbt":        len(tbts_d),
                "tbt_attend": tbt_attend_d,
                "key_obs":    key_obs,
            })

        # ── TBT sessions list ─────────────────────────────────────────
        tbt_sessions_list = []
        for tbt in week_tbts:
            officer_u = User.query.get(tbt.officer_id)
            tbt_sessions_list.append({
                "topic":    tbt.topic or "—",
                "officer":  officer_u.name if officer_u else "—",
                "location": tbt.location or "—",
                "attend":   tbt.attendance.count(),
            })

        # ── Delta vs previous week ────────────────────────────────────
        new_modules_total = sum(len(t["new_passes"]) for t in trainees_out)
        new_ptw_stages    = sum(t["ptw_stages_this_week"] for t in trainees_out)

        prev_start = week_start - timedelta(days=7)
        prev_end   = week_start - timedelta(days=1)

        prev_obs_count = HseObservation.query.filter(
            HseObservation.company_id == _c,
            HseObservation.date >= prev_start,
            HseObservation.date <= prev_end,
        ).count()

        prev_tbts = HseTbt.query.filter(
            HseTbt.officer_id.in_(_co_officer_ids),
            HseTbt.date >= prev_start,
            HseTbt.date <= prev_end,
        ).count()

        delta = {
            "modules_passed": new_modules_total,
            "ptw_stages":     new_ptw_stages,
            "obs":            len(week_obs) - prev_obs_count,
            "tbt":            len(week_tbts) - prev_tbts,
        }

        company_obj = Company.query.get(_c)
        company_name = company_obj.name if company_obj else ""

        # ── Full observation list for detail slide ────────────────────
        obs_detail = []
        for o in sorted(week_obs, key=lambda x: (x.date, x.id)):
            officer_u = User.query.get(o.officer_id)
            obs_detail.append({
                "date":        o.date.strftime("%a %d %b"),
                "officer":     officer_u.name if officer_u else "—",
                "location":    o.location or "—",
                "risk":        o.risk_level or "—",
                "obs_type":    o.obs_type or "—",
                "description": (o.description or "")[:120],
                "action":      (o.action_taken or "")[:80],
                "status":      o.status or "open",
            })

        data = {
            "week_start":   week_start,
            "week_end":     week_end,
            "company_name": company_name,
            "generated_at": now,
            "trainees":     trainees_out,
            "hse":          hse_agg,
            "ptw_summary":  ptw_summary,
            "obs_by_day":   obs_by_day,
            "obs_detail":   obs_detail,
            "tbt_sessions": tbt_sessions_list,
            "delta":        delta,
        }

        pptx_bytes = build_report(data)

        from flask import make_response
        ws_str   = week_start.strftime("%Y%m%d")
        we_str   = week_end.strftime("%Y%m%d")
        filename = f"Trainee_Report_{ws_str}_{we_str}.pptx"
        resp = make_response(pptx_bytes)
        resp.headers["Content-Type"] = (
            "application/vnd.openxmlformats-officedocument"
            ".presentationml.presentation"
        )
        resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp

    except Exception as e:
        app.logger.exception("V2 trainee report generation failed")
        flash(f"حدث خطأ أثناء توليد التقرير: {e}", "error")
        return redirect(url_for("hse_trainee_report_filter"))


# ══════════════════════════════════════════════════════════════════════════════
#  API: Admin Safety Management (iOS)
# ══════════════════════════════════════════════════════════════════════════════

# ── Admin Safety Teams ────────────────────────────────────────────────────────
@app.get("/api/admin/safety-teams")
@api_admin_required
def api_admin_safety_teams_get():
    _c = api_cid()
    sups_q = User.query.filter_by(role="safety_supervisor", is_active=True)
    if _c:
        sups_q = sups_q.filter_by(company_id=_c)
    supervisors = sups_q.order_by(User.name).all()

    offs_q = User.query.filter(User.role.in_(OFFICER_ROLES), User.is_active == True)
    if _c:
        offs_q = offs_q.filter(User.company_id == _c)
    all_officers = offs_q.order_by(User.role, User.name).all()

    result = []
    assigned_ids = set()
    for sup in supervisors:
        rows = (db.session.query(OfficerTeam, User)
                .join(User, OfficerTeam.officer_id == User.id)
                .filter(OfficerTeam.supervisor_id == sup.id)
                .order_by(User.role, User.name).all())
        officers_list = []
        for team_row, officer in rows:
            assigned_ids.add(officer.id)
            officers_list.append({
                "team_id":    team_row.id,
                "officer_id": officer.id,
                "name":       officer.name or officer.supervisor_code,
                "code":       officer.supervisor_code,
                "role":       officer.role,
                "role_label": officer.role.replace("_", " ").title()
            })
        result.append({
            "sup_id":   sup.id,
            "name":     sup.name or sup.supervisor_code,
            "code":     sup.supervisor_code,
            "officers": officers_list
        })

    unassigned = [
        {"officer_id": o.id, "name": o.name or o.supervisor_code,
         "code": o.supervisor_code, "role": o.role,
         "role_label": o.role.replace("_", " ").title()}
        for o in all_officers if o.id not in assigned_ids
    ]
    all_offs_list = [
        {"officer_id": o.id, "name": o.name or o.supervisor_code,
         "code": o.supervisor_code, "role": o.role,
         "role_label": o.role.replace("_", " ").title(),
         "assigned": o.id in assigned_ids}
        for o in all_officers
    ]
    return jsonify({"supervisors": result, "unassigned": unassigned,
                    "all_officers": all_offs_list})


@app.post("/api/admin/safety-teams")
@api_admin_required
def api_admin_safety_teams_post():
    _c = api_cid()
    data   = freq.get_json(force=True) or {}
    action = data.get("action")
    if action == "assign":
        sup_id = int(data.get("sup_id") or 0)
        off_id = int(data.get("off_id") or 0)
        if sup_id and off_id:
            exists = OfficerTeam.query.filter_by(supervisor_id=sup_id, officer_id=off_id).first()
            if not exists:
                db.session.add(OfficerTeam(supervisor_id=sup_id, officer_id=off_id, company_id=_c))
                db.session.commit()
        return jsonify({"ok": True})
    elif action == "remove":
        tid = int(data.get("team_id") or 0)
        row = db.session.get(OfficerTeam, tid) if tid else None
        if row:
            db.session.delete(row)
            db.session.commit()
        return jsonify({"ok": True})
    return jsonify({"error": "Invalid action"}), 400


# ── Admin Officers Activity Report ────────────────────────────────────────────
@app.post("/api/admin/officers-report")
@api_admin_required
def api_admin_officers_report():
    _c   = api_cid()
    data = freq.get_json(force=True) or {}
    raw_from    = (data.get("date_from") or "").strip()
    raw_to      = (data.get("date_to")   or "").strip()
    officer_ids = data.get("officer_ids") or []

    try:
        date_from = parse_date(raw_from)
        date_to   = parse_date(raw_to)
    except Exception:
        return jsonify({"error": "Invalid dates"}), 400

    officers_q = User.query.filter(User.role.in_(OFFICER_ROLES), User.is_active == True)
    if _c:
        officers_q = officers_q.filter(User.company_id == _c)
    all_officers = officers_q.order_by(User.role, User.name).all()

    if not officer_ids:
        selected = [o.id for o in all_officers]
    else:
        selected = [int(x) for x in officer_ids]

    rows = []
    for o in all_officers:
        if o.id not in selected:
            continue
        if o.role == "safety_officer":
            submissions = HseCheckin.query.filter(
                HseCheckin.officer_id == o.id,
                HseCheckin.date.between(date_from, date_to)).count()
            finds = 0
            detail = f"{submissions} check-ins"
            label  = "Safety Officer"
        elif o.role == "safety_welfare":
            submissions = WlfLevelWork.query.filter(
                WlfLevelWork.officer_id == o.id,
                WlfLevelWork.date.between(date_from, date_to)).count()
            finds = WlfFinding.query.filter(
                WlfFinding.officer_id == o.id,
                WlfFinding.date.between(date_from, date_to)).count()
            detail = f"{submissions} field submissions"
            label  = "Welfare Officer"
        elif o.role == "environment_officer":
            submissions = EnvLevelWork.query.filter(
                EnvLevelWork.officer_id == o.id,
                EnvLevelWork.date.between(date_from, date_to)).count()
            finds = WlfFinding.query.filter(
                WlfFinding.officer_id == o.id,
                WlfFinding.date.between(date_from, date_to)).count()
            detail = f"{submissions} env submissions"
            label  = "Environment Officer"
        else:
            continue
        rows.append({"officer_id": o.id, "name": o.name or o.supervisor_code,
                     "code": o.supervisor_code, "role": o.role, "role_label": label,
                     "submissions": submissions, "findings": finds, "detail": detail})

    all_offs_list = [
        {"officer_id": o.id, "name": o.name or o.supervisor_code,
         "code": o.supervisor_code, "role": o.role,
         "role_label": o.role.replace("_", " ").title()}
        for o in all_officers
    ]
    return jsonify({
        "date_from":        date_from.isoformat(),
        "date_to":          date_to.isoformat(),
        "rows":             rows,
        "all_officers":     all_offs_list,
        "total_submissions": sum(r["submissions"] for r in rows),
        "total_findings":   sum(r["findings"] for r in rows)
    })


# ── Admin Safety Supervisor Assignments ───────────────────────────────────────
@app.get("/api/admin/safety-supervisor/assign")
@api_admin_required
def api_admin_safety_sup_assign_get():
    _c = api_cid()
    sups = User.query.filter_by(role="safety_supervisor", is_active=True,
                                 company_id=_c).order_by(User.name).all()
    offs = User.query.filter_by(role="safety_officer", is_active=True,
                                 company_id=_c).order_by(User.name).all()
    maps = SafetySupervisorMap.query.filter(SafetySupervisorMap.company_id == _c).all()
    map_dict = {}
    for m in maps:
        map_dict.setdefault(m.safety_sup_id, []).append(m.officer_id)

    assigned_ids = set()
    result = []
    for sup in sups:
        assigned = map_dict.get(sup.id, [])
        assigned_ids.update(assigned)
        result.append({
            "sup_id":             sup.id,
            "name":               sup.name or sup.supervisor_code,
            "code":               sup.supervisor_code,
            "assigned_officers":  [
                {"officer_id": o.id, "name": o.name or o.supervisor_code, "code": o.supervisor_code}
                for o in offs if o.id in assigned
            ]
        })

    all_offs = [
        {"officer_id": o.id, "name": o.name or o.supervisor_code,
         "code": o.supervisor_code, "assigned": o.id in assigned_ids}
        for o in offs
    ]
    return jsonify({"supervisors": result, "all_officers": all_offs,
                    "unassigned": [x for x in all_offs if not x["assigned"]]})


@app.post("/api/admin/safety-supervisor/assign")
@api_admin_required
def api_admin_safety_sup_assign_post():
    _c        = api_cid()
    data      = freq.get_json(force=True) or {}
    action    = data.get("action")
    sup_id    = int(data.get("sup_id") or 0)
    officer_id = int(data.get("officer_id") or 0)
    if action == "add" and sup_id and officer_id:
        exists = SafetySupervisorMap.query.filter_by(safety_sup_id=sup_id,
                                                      officer_id=officer_id).first()
        if not exists:
            db.session.add(SafetySupervisorMap(safety_sup_id=sup_id, officer_id=officer_id,
                                                company_id=_c))
            db.session.commit()
        return jsonify({"ok": True})
    elif action == "remove" and sup_id and officer_id:
        SafetySupervisorMap.query.filter_by(safety_sup_id=sup_id,
                                             officer_id=officer_id).delete()
        db.session.commit()
        return jsonify({"ok": True})
    return jsonify({"error": "Invalid action"}), 400


# ── Admin HSE Access ──────────────────────────────────────────────────────────
@app.route("/admin/hse-access", methods=["GET", "POST"])
@admin_required
def admin_hse_access():
    primary = User.query.filter_by(supervisor_code=HSE_SUPERVISOR_CODE, is_active=True).first()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            code = (request.form.get("code") or "").strip().upper()
            u = User.query.filter_by(supervisor_code=code, is_active=True).first()
            if u:
                if not HseSupervisorAccess.query.filter_by(user_id=u.id).first():
                    db.session.add(HseSupervisorAccess(user_id=u.id))
                    db.session.commit()
                    flash("Access granted.", "success")
                else:
                    flash("User already has access.", "info")
            else:
                flash("User not found.", "danger")
        elif action == "remove":
            uid = int(request.form.get("user_id") or 0)
            acc = HseSupervisorAccess.query.filter_by(user_id=uid).first()
            if acc:
                db.session.delete(acc)
                db.session.commit()
                flash("Access removed.", "success")
        return redirect(url_for("admin_hse_access"))
    access_rows = (db.session.query(HseSupervisorAccess, User)
                   .join(User, HseSupervisorAccess.user_id == User.id)
                   .order_by(User.name).all())
    return render_template("admin_hse_access.html", primary=primary, access_list=access_rows)


@app.get("/api/admin/hse-access")
@api_admin_required
def api_admin_hse_access_get():
    primary = User.query.filter_by(supervisor_code=HSE_SUPERVISOR_CODE, is_active=True).first()
    access_rows = (db.session.query(HseSupervisorAccess, User)
                   .join(User, HseSupervisorAccess.user_id == User.id)
                   .order_by(User.name).all())
    result = []
    if primary:
        result.append({"user_id": primary.id, "name": primary.name,
                        "code": primary.supervisor_code, "role": primary.role,
                        "granted": None, "is_primary": True})
    for acc, u in access_rows:
        if primary and u.id == primary.id:
            continue
        result.append({"user_id": u.id, "name": u.name, "code": u.supervisor_code,
                        "role": u.role,
                        "granted": acc.granted_at.isoformat() if acc.granted_at else None,
                        "is_primary": False})
    return jsonify({"access_list": result})


@app.post("/api/admin/hse-access")
@api_admin_required
def api_admin_hse_access_post():
    data   = freq.get_json(force=True) or {}
    action = data.get("action")
    if action == "add":
        code = (data.get("code") or "").strip().upper()
        u = User.query.filter_by(supervisor_code=code, is_active=True).first()
        if not u:
            return jsonify({"error": "User not found"}), 404
        if not HseSupervisorAccess.query.filter_by(user_id=u.id).first():
            db.session.add(HseSupervisorAccess(user_id=u.id))
            db.session.commit()
        return jsonify({"ok": True,
                        "user": {"user_id": u.id, "name": u.name, "code": u.supervisor_code}})
    elif action == "remove":
        uid = int(data.get("user_id") or 0)
        acc = HseSupervisorAccess.query.filter_by(user_id=uid).first()
        if acc:
            db.session.delete(acc)
            db.session.commit()
        return jsonify({"ok": True})
    return jsonify({"error": "Invalid action"}), 400


# ── API: Safety Manager Dashboard (enhanced — admin / safety_manager) ─────────
@app.get("/api/hse/safety-manager-dashboard")
@api_admin_required
def api_hse_safety_manager_dashboard():
    from calendar import monthrange
    _c          = api_cid()
    today       = datetime.now(RIYADH_TZ).date()
    period      = freq.args.get("period", "week")
    week_offset = int(freq.args.get("week_offset", 0))

    if period == "month":
        first_day  = date(today.year, today.month, 1)
        last_day   = today
        period_lbl = today.strftime("%B %Y")
        week_offset = 0
        prev_m = today.month - 1 or 12
        prev_y = today.year if today.month > 1 else today.year - 1
        prev_first = date(prev_y, prev_m, 1)
        prev_last  = date(prev_y, prev_m, monthrange(prev_y, prev_m)[1])
    else:
        dsun      = today.isoweekday() % 7
        this_sun  = today - timedelta(days=dsun)
        first_day = this_sun + timedelta(weeks=week_offset)
        last_day  = min(first_day + timedelta(days=6), today)
        period_lbl = f"{first_day.strftime('%d %b')} – {last_day.strftime('%d %b %Y')}"
        prev_first = first_day - timedelta(days=7)
        prev_last  = first_day - timedelta(days=1)

    rows      = _hse_weekly_report_data(first_day, last_day, company_id=_c)
    prev_rows = _hse_weekly_report_data(prev_first, prev_last, company_id=_c)
    prev_map  = {r["officer_id"]: r["score"] for r in prev_rows}
    for r in rows:
        prev         = prev_map.get(r["officer_id"], 0)
        r["prev_score"] = prev
        r["trend"]      = round(r["score"] - prev, 1)
    rows.sort(key=lambda r: r["score"], reverse=True)

    total_off   = len(rows)
    active_off  = sum(1 for r in rows if r["checkin_days"] > 0)
    comp_pct    = round(active_off / total_off * 100) if total_off else 0
    avg_score   = round(sum(r["score"] for r in rows) / total_off, 1) if total_off else 0
    total_obs   = sum(r["obs_total"] for r in rows)
    inactive    = [{"officer_id": r["officer_id"], "name": r["officer_name"]}
                   for r in rows if r["checkin_days"] == 0]

    # High-risk open observations
    hr_q = HseObservation.query.filter_by(status="open", risk_level="H")
    if _c:
        hr_q = hr_q.filter(HseObservation.company_id == _c)
    hr_list = [{"id": obs.id, "date": obs.date.isoformat(),
                "officer_id": obs.officer_id, "category": obs.category or "",
                "location": obs.location or "", "description": obs.description or ""}
               for obs in hr_q.order_by(HseObservation.date).limit(15)]

    # Overdue corrective actions
    ca_q = HseCorrectiveAction.query.filter(
        HseCorrectiveAction.status != "completed",
        HseCorrectiveAction.due_date < today)
    if _c:
        ca_q = ca_q.filter(HseCorrectiveAction.company_id == _c)
    ca_list = [{"id": ca.id,
                "action_required": (ca.action_required or "")[:80],
                "due_date": ca.due_date.isoformat() if ca.due_date else None}
               for ca in ca_q.order_by(HseCorrectiveAction.due_date).limit(10)]

    # 6-week team score trend
    weekly_labels, weekly_avg = [], []
    for i in range(5, -1, -1):
        dsun_i = today.isoweekday() % 7
        ws_i   = today - timedelta(days=dsun_i + i * 7)
        we_i   = ws_i + timedelta(days=4)
        wr     = _hse_weekly_report_data(ws_i, we_i, company_id=_c)
        wavg   = round(sum(x["score"] for x in wr) / len(wr), 1) if wr else 0
        weekly_labels.append(ws_i.strftime("%d %b"))
        weekly_avg.append(wavg)

    # Welfare summary
    wlf_q = User.query.filter_by(role="safety_welfare", is_active=True)
    if _c:
        wlf_q = wlf_q.filter(User.company_id == _c)
    wlf_rows = []
    for o in wlf_q.order_by(User.name).all():
        d = _wlf_monthly_card_data(o, first_day, last_day)
        wlf_rows.append({"officer_id": o.id, "officer_name": o.name,
                         "rounds": d["rounds"], "avg_score": d["avg_score"],
                         "finds_closed": d["finds_closed"], "finds_open": d["finds_open"],
                         "complaints": d["complaints"]})
    wlf_rows.sort(key=lambda r: r["rounds"], reverse=True)

    return jsonify({
        "today":            today.isoformat(),
        "period":           period,
        "period_lbl":       period_lbl,
        "first_day":        first_day.isoformat(),
        "last_day":         last_day.isoformat(),
        "week_offset":      week_offset,
        "total_officers":   total_off,
        "active_officers":  active_off,
        "inactive_officers": inactive,
        "compliance_pct":   comp_pct,
        "avg_score":        avg_score,
        "total_obs":        total_obs,
        "total_ua":         sum(r["obs_unsafe_act"]  for r in rows),
        "total_uc":         sum(r["obs_unsafe_cond"] for r in rows),
        "total_pos":        sum(r["obs_positive"]    for r in rows),
        "high_risk_open":   hr_list,
        "overdue_cas":      ca_list,
        "officers":         rows,
        "weekly_labels":    weekly_labels,
        "weekly_avg":       weekly_avg,
        "welfare_rows":     wlf_rows,
    })


# ── LMS blueprint ────────────────────────────────────────────────────────────
from models.lms import (LmsCourse, LmsModule, LmsModuleSection, LmsQuestion,
                         LmsQuestionOption, LmsEnrollment, LmsModuleProgress,
                         LmsQuizAttempt, LmsAttemptQuestion, LmsAttemptAnswer,
                         LmsAuditLog)
from blueprints.lms import lms_bp
app.register_blueprint(lms_bp)

# نقطة دخول WSGI لاسم "application"
application = app

# للتشغيل المحلي فقط
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)