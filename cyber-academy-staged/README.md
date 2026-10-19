# محتوى جاهز لكن غير منشور (المستويات 6 إلى 9)

دروس المستويات 6 (أمن الويب) و7 (الدفاع) و8 (الاختراق الأخلاقي) و9 (الحوكمة) مكتوبة ومختبرة، لكنها خارج مجلد الموقع عشان ما تنرفع قبل ما نجرّب المستويات 0 إلى 5.

## لنشرها لاحقاً (من مجلد المشروع)
    git mv cyber-academy-staged/lessons/*.json cyber-academy/content/lessons/
    cd cyber-academy && python3 tools/validate_content.py \
      --merge ../cyber-academy-staged/glossary/g6.json --merge ../cyber-academy-staged/glossary/g7.json \
      --merge ../cyber-academy-staged/glossary/g8.json --merge ../cyber-academy-staged/glossary/g9.json
الأداة تدمج المصطلحات، وتفعّل الدروس (`ready`)، وتولّد فهارس المختبر والمراجعة.
