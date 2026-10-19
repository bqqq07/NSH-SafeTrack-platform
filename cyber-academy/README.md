# أكاديمية الأمن السيبراني (Cyber Academy)

منصة تعلّم الأمن السيبراني من الصفر بالعربي، جوال أولاً (RTL + PWA).
ملفات ثابتة فقط (HTML/CSS/JS)، بدون Build وبدون سيرفر، فتشتغل على أي استضافة (مثل Namecheap).

## التشغيل محلياً
    cd cyber-academy && python3 -m http.server 8000   # ثم افتح http://localhost:8000

## الرفع
انسخ محتوى المجلد كامل إلى `public_html` (أو مجلد فرعي). تحتاج HTTPS عشان الـ PWA والـ Service Worker.

## هيكل المحتوى
- `content/curriculum.json`: المستويات والوحدات (`ready: true` لما يجهز الدرس).
- `content/glossary.json`: قاموس المصطلحات (English + نطق + عربي).
- `content/lessons/<id>.json`: الدرس كشرائح (`intro`, `text`, `terms`, `practice`, `quiz`, `summary`).
- في النصوص: `[[term-id]]` يصير مصطلح قابل للضغط، و`**نص**` خط عريض.

التقدم محفوظ في `localStorage` بدون حساب.
