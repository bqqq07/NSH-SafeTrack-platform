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
# فهارس مولَّدة: المختبرات (تمارين تفاعلية) وربط المصطلحات بالدروس
LAB = {'terminal', 'passlab', 'phish', 'order', 'match', 'cipher', 'webvuln', 'scenario', 'ctf', 'firewall'}
labs, term_lessons = {}, {}
for f in sorted(glob.glob('content/lessons/*.json'), key=lambda x: [int(p) if p.isdigit() else p for p in re.split(r'(\d+)', x)]):
    d = json.load(open(f)); lid = os.path.basename(f)[:-5]
    for t in set(re.findall(r'\[\[([a-z0-9-]+)\]\]', open(f).read())):
        term_lessons.setdefault(t, []).append(lid)
    for s in d['slides']:
        for t in s.get('ids', []): term_lessons.setdefault(t, [])
        if s.get('ids'):
            for t in s['ids']:
                if lid not in term_lessons[t]: term_lessons[t].append(lid)
    for i, s in enumerate(d['slides']):
        if s['type'] in LAB:
            labs.setdefault(s['type'], []).append({'id': lid, 'slide': i, 'title': s.get('title') or d['title_ar'], 'lesson': d['title_ar']})
json.dump(labs, open('content/labs.json', 'w'), ensure_ascii=False, indent=1)
json.dump(term_lessons, open('content/term_lessons.json', 'w'), ensure_ascii=False)
n = len(glob.glob('content/lessons/*.json'))
ready = sum(m['ready'] for l in cur['levels'] for m in l['modules'])
total = sum(len(l['modules']) for l in cur['levels'])
print(f'lessons={n} terms={len(glossary)} ready={ready}/{total}')
print('errors:', errs if errs else 'none')
sys.exit(1 if errs else 0)
