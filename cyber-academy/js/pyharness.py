"""
فاحص تمارين Python (Harness).
يُستخدم في مكانين بنفس الكود:
  1) داخل المتصفح (Pyodide) لتشغيل كود المتعلم وفحصه.
  2) في أداة التحقق على المطوّر (CPython) للتأكد أن حل المرجع لكل تمرين ينجح.

الواجهة الوحيدة: run_json(payload_json) -> result_json
payload:
  mode   : "run" (تشغيل فقط) | "tests" (تشغيل وفحص)
  code   : كود المتعلم
  stdin  : قائمة مدخلات لـ input()
  files  : {اسم: محتوى} ملفات تدريبية تُنشأ في مجلد مؤقت
  tests  : [{label, stdin, files, expect_stdout | (call + expect), hidden}]
  checks : [{regex, must (افتراضي True), msg}] فحوصات على نص الكود
"""
import builtins
import contextlib
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import traceback

MAX_OUT = 20000          # أقصى حجم للناتج (حرف)
SOURCE_NAME = "main.py"  # اسم ملف الكود في رسائل الأخطاء
BLOCKED = {"js", "pyodide", "pyodide_js", "micropip", "_pyodide", "pyodide_http", "pyodide_ffi"}


class OutputLimit(Exception):
    """الناتج تجاوز الحد المسموح (غالباً حلقة تطبع بلا نهاية)."""


class _Out(io.StringIO):
    def write(self, s):
        if self.tell() + len(s) > MAX_OUT:
            raise OutputLimit("الناتج طويل جداً")
        return super().write(s)


def _err_info(exc, code):
    """معلومات الخطأ: نوعه ورسالته ورقم السطر في كود المتعلم."""
    line = getattr(exc, "lineno", None) if isinstance(exc, SyntaxError) else None
    if line is None:
        for fr in traceback.extract_tb(exc.__traceback__):
            if fr.filename == SOURCE_NAME:
                line = fr.lineno
    lines = code.splitlines()
    text = lines[line - 1].strip() if line and 0 < line <= len(lines) else ""
    msg = getattr(exc, "msg", None) if isinstance(exc, SyntaxError) else None
    return {"type": type(exc).__name__, "msg": msg or str(exc), "line": line, "text": text}


def _execute(code, stdin=None, files=None, expr=None, echo=True):
    """ينفّذ الكود في فضاء أسماء جديد ويرجع الناتج، وإن وُجد expr يقيّمه بعد التنفيذ.
    echo=True: نعرض المدخل بعد النص التوضيحي كأنه كُتب في الطرفية (للتشغيل). وفي الفحص echo=False عشان الناتج يكون نظيفاً."""
    out = _Out()
    queue = list(stdin or [])
    result = {"stdout": "", "error": None, "value": None, "has_value": False}

    def fake_input(prompt=""):
        print(prompt, end="")
        if not queue:
            raise EOFError("لا توجد مدخلات كافية لـ input()")
        value = str(queue.pop(0))
        if echo:
            print(value)
        return value

    real_import = builtins.__import__

    def guarded_import(name, *a, **k):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"الوحدة {name} غير مسموحة في هذي البيئة")
        return real_import(name, *a, **k)

    env = dict(builtins.__dict__)
    env["input"] = fake_input
    env["__import__"] = guarded_import
    ns = {"__name__": "__main__", "__builtins__": env}

    old_cwd = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="pyex_")
    try:
        os.chdir(tmp)
        sys.path.insert(0, tmp)  # لتعمل import لملفات التمرين (مثل helpers.py)
        before = set(sys.modules)
        for name, content in (files or {}).items():
            safe = os.path.basename(name)
            with open(safe, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
        try:
            compiled = compile(code, SOURCE_NAME, "exec")
        except SyntaxError as e:
            result["error"] = _err_info(e, code)
            return result
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                exec(compiled, ns)
                if expr is not None:
                    result["value"] = eval(compile(expr, "<test>", "eval"), ns)
                    result["has_value"] = True
        except BaseException as e:  # noqa: BLE001 — نعرض أي خطأ للمتعلم
            if isinstance(e, KeyboardInterrupt):
                raise
            result["error"] = _err_info(e, code)
    finally:
        os.chdir(old_cwd)
        if tmp in sys.path:
            sys.path.remove(tmp)
        for m in set(sys.modules) - before:  # ننظف الوحدات اللي استوردها كود المتعلم
            if getattr(sys.modules[m], "__file__", None) and str(sys.modules[m].__file__).startswith(tmp):
                del sys.modules[m]
        shutil.rmtree(tmp, ignore_errors=True)
        result["stdout"] = out.getvalue()
    return result


def _norm_out(s):
    return "\n".join(line.rstrip() for line in (s or "").rstrip("\n").split("\n"))


def _jsonable(v):
    if isinstance(v, (tuple, list)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (set, frozenset)):
        return sorted((_jsonable(x) for x in v), key=repr)
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return repr(v)


def _equal(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    return a == b


def _short(v, n=200):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "…"


def _run_tests(code, tests, checks, files):
    report = {"results": [], "checks": [], "error": None}
    for c in checks or []:
        found = re.search(c["regex"], code) is not None
        must = c.get("must", True)
        report["checks"].append({"ok": found == must, "msg": c.get("msg", "")})
    if not tests:  # تمرين بدون اختبارات: يكفي أن يشتغل الكود بدون أخطاء
        r = _execute(code, None, files, echo=False)
        report["results"].append({"label": "الكود يشتغل بدون أخطاء", "hidden": False, "ok": r["error"] is None, "error": r["error"], "got": None})
    for i, t in enumerate(tests or []):
        merged = dict(files or {})
        merged.update(t.get("files") or {})
        label = t.get("label") or f"اختبار {i + 1}"
        base = {"label": label, "hidden": bool(t.get("hidden"))}
        if "call" in t:
            r = _execute(code, t.get("stdin"), merged, expr=t["call"], echo=False)
            if r["error"]:
                report["results"].append({**base, "ok": False, "error": r["error"], "got": None})
                continue
            got = _jsonable(r["value"])
            exp = t.get("expect")
            report["results"].append({**base, "ok": _equal(got, exp), "got": _short(got), "expected": _short(exp), "call": t["call"]})
        else:
            r = _execute(code, t.get("stdin"), merged, echo=False)
            exp = t.get("expect_stdout", "")
            ok = r["error"] is None and _norm_out(r["stdout"]) == _norm_out(exp)
            report["results"].append({**base, "ok": ok, "got": _short(_norm_out(r["stdout"])), "expected": _short(_norm_out(exp)),
                                      "error": r["error"], "stdin": t.get("stdin")})
    report["passed"] = all(x["ok"] for x in report["results"]) and all(x["ok"] for x in report["checks"])
    return report


def run_json(payload_json):
    p = json.loads(payload_json)
    if p.get("mode") == "tests":
        res = _run_tests(p.get("code", ""), p.get("tests"), p.get("checks"), p.get("files"))
    else:
        r = _execute(p.get("code", ""), p.get("stdin"), p.get("files"))
        res = {"stdout": r["stdout"], "error": r["error"]}
    return json.dumps(res, ensure_ascii=False)
