# models/daily.py
from extensions import db

class DailyEvaluation(db.Model):
    __tablename__ = "daily_evaluation"

    id = db.Column(db.Integer, primary_key=True)
    employee_id   = db.Column(db.Integer, nullable=False)
    supervisor_id = db.Column(db.Integer)
    eval_date     = db.Column(db.Date, nullable=False)

    targets_score = db.Column(db.Float)
    perf_score    = db.Column(db.Float)
    total_score   = db.Column(db.Float)
    overall_band  = db.Column(db.String(32))
    notes         = db.Column(db.Text)

    c_punctuality    = db.Column(db.Float)
    c_quality        = db.Column(db.Float)
    c_productivity   = db.Column(db.Float)
    c_communication  = db.Column(db.Float)
    c_problemsolving = db.Column(db.Float)
    c_compliance     = db.Column(db.Float)

    __table_args__ = (db.UniqueConstraint('employee_id','eval_date', name='uq_daily_emp_date'),)
