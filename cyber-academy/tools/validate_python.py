"""يتحقق من تمارين Python بتشغيلها فعلياً بـ CPython عبر نفس الفاحص (js/pyharness.py) المستخدم في المتصفح.
- code     : حل المرجع لازم ينجح كل الاختبارات، والكود الأولي لازم يفشل، وأي حلول خاطئة معروفة (wrong) لازم تفشل.
- predict  : الخيار الصحيح لازم يطابق الناتج الحقيقي للكود، وبقية الخيارات تختلف عنه.
- parsons  : الأسطر مرتبة لازم تشتغل بدون أخطاء (وتطابق expect_stdout إن وُجد).
- fillcode : بعد تعبئة الإجابات الصحيحة لازم يشتغل الكود (ويطابق expect_stdout إن وُجد).
يُستدعى من validate_content.py، أو مباشرة: python3 tools/validate_python.py [lesson-id ...]"""
import glob, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, '..', 'js', 'pyharness.py')
DRIVER = "import sys; ns = {}; exec(compile(open(sys.argv[1], encoding='utf-8').read(), 'pyharness', 'exec'), ns); print(ns['run_json'](sys.stdin.read()))"
PY_TYPES = {'code', 'predict', 'parsons', 'fillcode'}


def harness(payload, timeout=8):
    p = subprocess.run([sys.executable, '-I', '-c', DRIVER, HARNESS], input=json.dumps(payload), capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr[-400:])
    return json.loads(p.stdout)


def run_lang(lang, code, files=None, stdin=None):
    """تشغيل كود بلغات غير Python (bash/git) أو مكتبات خارجية (pandas) للتحقق من نواتج الدروس الملحقة.
    lang=text: لا يُشغَّل (تعليمي فقط). يرجع نفس شكل نتيجة harness: {stdout, error}."""
    if lang in (None, 'python'):
        return harness({'mode': 'run', 'code': code, 'stdin': stdin, 'files': files})
    tmp = tempfile.mkdtemp(prefix='ex_')
    try:
        for name, content in (files or {}).items():
            path = os.path.join(tmp, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8', newline='') as fh:
                fh.write(content)
        env = {'PATH': os.environ['PATH'], 'HOME': tmp, 'LC_ALL': 'C.UTF-8', 'GIT_CONFIG_NOSYSTEM': '1',
               'GIT_AUTHOR_NAME': 'Sara', 'GIT_AUTHOR_EMAIL': 'sara@example.com', 'GIT_COMMITTER_NAME': 'Sara', 'GIT_COMMITTER_EMAIL': 'sara@example.com',
               'GIT_AUTHOR_DATE': '2025-01-05T10:00:00', 'GIT_COMMITTER_DATE': '2025-01-05T10:00:00', 'GIT_CONFIG_COUNT': '1',
               'GIT_CONFIG_KEY_0': 'init.defaultBranch', 'GIT_CONFIG_VALUE_0': 'main'}
        if lang in ('bash', 'git'):
            cmd = ['bash', '-c', ('set -e\n' if lang == 'git' else '') + code]
        elif lang == 'pandas':
            env['PYTHONPATH'] = os.pathsep.join(sys.path)
            cmd = [sys.executable, '-c', code]
        else:
            raise ValueError('unknown lang ' + lang)
        p = subprocess.run(cmd, cwd=tmp, env=env, input=stdin and '\n'.join(stdin) + '\n', capture_output=True, text=True, timeout=20)
        if p.returncode:
            return {'stdout': p.stdout, 'error': {'type': 'ExitCode', 'msg': f'{p.returncode}: {p.stderr[-300:]}'}}
        return {'stdout': p.stdout, 'error': None}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def norm(s):
    return '\n'.join(l.rstrip() for l in (s or '').rstrip('\n').split('\n'))


def check_slide(s):
    t, errs = s['type'], []
    lang = s.get('lang')
    if t == 'predict' and lang == 'text':
        if len(set(s['options'])) != len(s['options']):
            errs.append('predict: duplicate options')
        return errs
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
        r = run_lang(lang, s['code'], s.get('files'), s.get('stdin'))
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
            r = run_lang(lang, '\n'.join(order) + '\n', s.get('files'), s.get('stdin'))
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
            r = run_lang(lang, code, s.get('files'), s.get('stdin'))
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
