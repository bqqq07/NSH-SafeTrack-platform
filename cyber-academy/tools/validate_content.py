#!/usr/bin/env python3
"""يفحص المحتوى: مراجع المصطلحات [[id]]، أنواع الشرائح، فهارس الإجابات، وحقول التمارين.
ويحدّث ready في curriculum.json حسب وجود ملف الدرس.
الاستخدام (من مجلد cyber-academy):  python3 tools/validate_content.py [--merge ملف_مصطلحات.json ...]"""
import glob, json, os, re, sys

args = sys.argv[1:]
glossary = json.load(open('content/glossary.json'))
while '--merge' in args:
    i = args.index('--merge')
    glossary.update(json.load(open(args[i + 1])))
    del args[i:i + 2]
json.dump(glossary, open('content/glossary.json', 'w'), ensure_ascii=False, indent=1)

cur = json.load(open('content/curriculum.json'))
for l in cur['levels']:
    for m in l['modules']:
        m['ready'] = os.path.exists(f"content/lessons/{m['id']}.json")
json.dump(cur, open('content/curriculum.json', 'w'), ensure_ascii=False, indent=1)

TYPES = {'intro', 'text', 'terms', 'practice', 'quiz', 'summary', 'terminal', 'passlab', 'phish',
         'order', 'match', 'cipher', 'webvuln', 'scenario', 'ctf', 'firewall'}
errs = []
for f in sorted(glob.glob('content/lessons/*.json')):
    try:
        d = json.load(open(f))
    except Exception as e:
        errs.append((f, 'bad json', str(e))); continue
    txt = open(f).read()
    for t in re.findall(r'\[\[([a-z0-9-]+)\]\]', txt):
        if t not in glossary: errs.append((f, 'missing term', t))
    sl = d['slides']
    if sl[0]['type'] != 'intro' or sl[-1]['type'] != 'summary': errs.append((f, 'must start with intro and end with summary'))
    for i, s in enumerate(sl):
        ty = s['type']
        if ty not in TYPES: errs.append((f, i, 'unknown type', ty)); continue
        for t in s.get('ids', []):
            if t not in glossary: errs.append((f, i, 'terms slide missing', t))
        for q in s.get('questions', []):
            if not 0 <= q['answer'] < len(q['options']) or len(q['options']) < 2 or not q.get('why'):
                errs.append((f, i, 'bad question', q['q']))
        for tk in s.get('tasks', []):
            if not tk.get('accept') or 'why' not in tk: errs.append((f, i, 'bad task'))
        if ty == 'phish' and not any('bad' in p for p in s['parts']): errs.append((f, i, 'phish without bad part'))
        if ty == 'order' and len(s['items']) < 3: errs.append((f, i, 'order too short'))
        if ty == 'match' and len(s['pairs']) < 3: errs.append((f, i, 'match too short'))
        if ty == 'firewall' and not all('allow' in p and p.get('why') for p in s['packets']): errs.append((f, i, 'bad packet'))
        if ty == 'scenario':
            for nid, n in s['nodes'].items():
                for c in n.get('choices', []):
                    if c['next'] not in s['nodes']: errs.append((f, i, 'scenario bad next', nid))
            if s['start'] not in s['nodes']: errs.append((f, i, 'scenario bad start'))
        if ty == 'ctf' and not all(c.get('flag') for c in s['challenges']): errs.append((f, i, 'ctf no flag'))
n = len(glob.glob('content/lessons/*.json'))
ready = sum(m['ready'] for l in cur['levels'] for m in l['modules'])
total = sum(len(l['modules']) for l in cur['levels'])
print(f'lessons={n} terms={len(glossary)} ready={ready}/{total}')
print('errors:', errs if errs else 'none')
sys.exit(1 if errs else 0)
