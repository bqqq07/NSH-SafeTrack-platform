"""يتحقق من تمارين Python بتشغيلها فعلياً بـ CPython عبر نفس الفاحص (js/pyharness.py) المستخدم في المتصفح.
- code     : حل المرجع لازم ينجح كل الاختبارات، والكود الأولي لازم يفشل، وأي حلول خاطئة معروفة (wrong) لازم تفشل.
- predict  : الخيار الصحيح لازم يطابق الناتج الحقيقي للكود، وبقية الخيارات تختلف عنه.
- parsons  : الأسطر مرتبة لازم تشتغل بدون أخطاء (وتطابق expect_stdout إن وُجد).
- fillcode : بعد تعبئة الإجابات الصحيحة لازم يشتغل الكود (ويطابق expect_stdout إن وُجد).
يُستدعى من validate_content.py، أو مباشرة: python3 tools/validate_python.py [lesson-id ...]"""
import glob, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, '..', 'js', 'pyharness.py')
DRIVER = "import sys; ns = {}; exec(compile(open(sys.argv[1], encoding='utf-8').read(), 'pyharness', 'exec'), ns); print(ns['run_json'](sys.stdin.read()))"
PY_TYPES = {'code', 'predict', 'parsons', 'fillcode'}


def harness(payload, timeout=8):
    p = subprocess.run([sys.executable, '-I', '-c', DRIVER, HARNESS], input=json.dumps(payload), capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr[-400:])
    return json.loads(p.stdout)


def norm(s):
    return '\n'.join(l.rstrip() for l in (s or '').rstrip('\n').split('\n'))


def check_slide(s):
    t, errs = s['type'], []
    if t == 'code':
        for k in ('title', 'instructions', 'solution', 'hints'):
            if not s.get(k):
                errs.append(f'code: missing {k}')
        if errs:
            return errs
        if not s.get('tests') and not s.get('checks'):
            errs.append('code: needs tests or checks')
        run = lambda code: harness({'mode': 'tests', 'code': code, 'tests': s.get('tests'), 'checks': s.get('checks'), 'files': s.get('files')})
        r = run(s['solution'])
        if not r['passed']:
            bad = [x for x in r['results'] if not x['ok']] + [c for c in r['checks'] if not c['ok']]
            errs.append(f"code: reference solution FAILS: {json.dumps(bad, ensure_ascii=False)[:300]}")
        try:
            starter_passes = run(s.get('starter', ''))['passed']
        except subprocess.TimeoutExpired:  # الكود الأولي حلقة بلا نهاية (تمرين إصلاح): يُعتبر فاشلاً وهذا المطلوب
            starter_passes = False
        if starter_passes and not s.get('allow_starter_pass'):
            errs.append('code: starter already passes (exercise is trivial)')
        for w in s.get('wrong', []):
            if run(w)['passed']:
                errs.append(f'code: known-wrong solution passes: {w[:60]!r}')
    elif t == 'predict':
        opts = s['options']
        if len(set(opts)) != len(opts):
            errs.append('predict: duplicate options')
        r = harness({'mode': 'run', 'code': s['code'], 'stdin': s.get('stdin')})
        if r['error']:
            errs.append(f"predict: code raises {r['error']['type']}: {r['error']['msg']}")
        else:
            actual = norm(r['stdout'])
            if norm(opts[s['answer']]) != actual:
                errs.append(f"predict: marked answer {opts[s['answer']]!r} != real output {actual!r}")
            for i, o in enumerate(opts):
                if i != s['answer'] and norm(o) == actual:
                    errs.append(f'predict: wrong option {i} equals the real output')
    elif t == 'parsons':
        for order in [s['lines']] + s.get('alt', []):
            r = harness({'mode': 'run', 'code': '\n'.join(order) + '\n', 'stdin': s.get('stdin')})
            if r['error']:
                errs.append(f"parsons: solution raises {r['error']['type']}: {r['error']['msg']}")
            elif 'expect_stdout' in s and norm(r['stdout']) != norm(s['expect_stdout']):
                errs.append(f"parsons: output {norm(r['stdout'])!r} != expected")
    elif t == 'fillcode':
        if s['code'].count('____') != len(s['blanks']):
            errs.append('fillcode: blank count mismatch')
        else:
            parts = s['code'].split('____')
            answers = [b['answer'][0] if isinstance(b['answer'], list) else b['answer'] for b in s['blanks']]
            code = ''.join(p + (answers[i] if i < len(answers) else '') for i, p in enumerate(parts))
            r = harness({'mode': 'run', 'code': code, 'stdin': s.get('stdin')})
            if r['error']:
                errs.append(f"fillcode: filled code raises {r['error']['type']}: {r['error']['msg']}")
            elif 'expect_stdout' in s and norm(r['stdout']) != norm(s['expect_stdout']):
                errs.append(f"fillcode: output {norm(r['stdout'])!r} != expected {norm(s['expect_stdout'])!r}")
    return errs


def check_lessons(only=None):
    errors, n = [], 0
    for f in sorted(glob.glob(os.path.join(HERE, '..', 'content', 'lessons', 'm*.json'))):
        lid = os.path.basename(f)[:-5]
        if only and lid not in only:
            continue
        for i, s in enumerate(json.load(open(f, encoding='utf-8'))['slides']):
            if s['type'] in PY_TYPES:
                n += 1
                try:
                    errors += [(lid, i, e) for e in check_slide(s)]
                except Exception as e:  # noqa: BLE001
                    errors.append((lid, i, f'validator crashed: {e}'))
    return n, errors


if __name__ == '__main__':
    n, errs = check_lessons(set(sys.argv[1:]) or None)
    print(f'python exercises verified: {n}')
    print('python errors:', errs if errs else 'none')
    sys.exit(1 if errs else 0)
