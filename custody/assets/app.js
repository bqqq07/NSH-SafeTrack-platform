(function () {
  // تأكيد الحذف
  document.querySelectorAll('form[data-confirm]').forEach(function (f) {
    f.addEventListener('submit', function (e) {
      if (!confirm(f.dataset.confirm)) e.preventDefault();
    });
  });

  var form = document.getElementById('expenseForm');
  if (!form) return;

  // إظهار الحقول حسب البند
  var cat = document.getElementById('category');
  var noteBox = document.getElementById('noteBox');
  var noteLabel = document.getElementById('noteLabel');
  var needsNote = { other: 'اذكر نوع المصروف (أخرى)', rent: 'نوع الإيجار (اختياري: سكن، مكتب...)' };
  function syncCategory() {
    noteBox.hidden = !(cat.value in needsNote);
    if (cat.value in needsNote) noteLabel.textContent = needsNote[cat.value];
  }
  cat.addEventListener('change', syncCategory);
  syncCategory();

  // لوحة التوقيع (إصبع أو ماوس)
  var canvas = document.getElementById('sig');
  var ctx = canvas.getContext('2d');
  var input = document.getElementById('signature');
  var drawing = false, dirty = false;
  ctx.lineWidth = 3; ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.strokeStyle = '#000';
  function pos(e) {
    var r = canvas.getBoundingClientRect();
    return { x: (e.clientX - r.left) * canvas.width / r.width, y: (e.clientY - r.top) * canvas.height / r.height };
  }
  canvas.addEventListener('pointerdown', function (e) {
    drawing = true; canvas.setPointerCapture(e.pointerId);
    var p = pos(e); ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(p.x + 0.1, p.y + 0.1); ctx.stroke();
    dirty = true; e.preventDefault();
  });
  canvas.addEventListener('pointermove', function (e) {
    if (!drawing) return;
    var p = pos(e); ctx.lineTo(p.x, p.y); ctx.stroke(); e.preventDefault();
  });
  ['pointerup', 'pointercancel'].forEach(function (ev) {
    canvas.addEventListener(ev, function () { drawing = false; });
  });
  document.getElementById('sigClear').addEventListener('click', function () {
    ctx.clearRect(0, 0, canvas.width, canvas.height); dirty = false; input.value = '';
  });

  // ضغط صورة الفاتورة قبل الرفع (يصغّر الحجم ويخفف التقرير)
  var fileInput = document.getElementById('invoice');
  fileInput.addEventListener('change', function () {
    var file = fileInput.files[0];
    if (!file || !file.type.startsWith('image/') || !window.DataTransfer) return;
    var img = new Image();
    var url = URL.createObjectURL(file);
    img.onload = function () {
      var max = 1280, s = Math.min(1, max / Math.max(img.width, img.height));
      var c = document.createElement('canvas');
      c.width = Math.round(img.width * s); c.height = Math.round(img.height * s);
      var cx = c.getContext('2d'); cx.fillStyle = '#fff'; cx.fillRect(0, 0, c.width, c.height);
      cx.drawImage(img, 0, 0, c.width, c.height);
      c.toBlob(function (blob) {
        URL.revokeObjectURL(url);
        if (!blob || blob.size >= file.size) return;
        var dt = new DataTransfer();
        dt.items.add(new File([blob], 'invoice.jpg', { type: 'image/jpeg' }));
        fileInput.files = dt.files;
      }, 'image/jpeg', 0.7);
    };
    img.src = url;
  });

  form.addEventListener('submit', function (e) {
    if (dirty) input.value = canvas.toDataURL('image/png');
    document.getElementById('saveBtn').disabled = true;
  });
})();
