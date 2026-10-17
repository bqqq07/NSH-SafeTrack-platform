<?php
require __DIR__ . '/includes/bootstrap.php';
require __DIR__ . '/includes/layout.php';
$user = require_login();
$pdo = db();

$id = (int)($_GET['id'] ?? 0);
$exp = null;
if ($id) {
    $st = $pdo->prepare('SELECT * FROM expenses WHERE id = ?');
    $st->execute([$id]);
    $exp = $st->fetch();
    if (!$exp) { http_response_code(404); exit('غير موجود'); }
    if (!$user['admin'] && (int)$exp['created_by'] !== $user['id']) { http_response_code(403); exit('لا يمكنك تعديل صرف سجّله غيرك.'); }
}
$custodies = get_custodies();
$f = $exp ?: [
    'custody_id' => $custodies[0]['id'] ?? 0, 'category' => 'purchases', 'sub_type' => '', 'sub_note' => '',
    'amount' => '', 'expense_date' => date('Y-m-d'), 'description' => '', 'signature_id' => null, 'invoice_id' => null,
];
$errors = [];

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    csrf_verify();
    $f['custody_id'] = (int)($_POST['custody'] ?? 0);
    $f['category'] = $_POST['category'] ?? '';
    $f['sub_note'] = mb_substr(trim($_POST['sub_note'] ?? ''), 0, 255);
    $f['expense_date'] = $_POST['expense_date'] ?? '';
    $f['description'] = mb_substr(trim($_POST['description'] ?? ''), 0, 500);
    $amount = parse_amount($_POST['amount'] ?? '');
    $f['amount'] = $_POST['amount'] ?? '';

    $c = $pdo->prepare('SELECT 1 FROM custodies WHERE id = ?');
    $c->execute([$f['custody_id']]);
    if (!$c->fetchColumn()) $errors[] = 'اختر العهدة';
    if (!isset(CATEGORIES[$f['category']])) $errors[] = 'اختر البند';
    if ($amount === null) $errors[] = 'المبلغ غير صحيح';
    if (!valid_date($f['expense_date'])) $errors[] = 'التاريخ غير صحيح';
    if ($f['description'] === '') $errors[] = 'البيان مطلوب';

    $f['sub_type'] = null;
    if (in_array($f['category'], NOTE_CATEGORIES, true)) {
        if ($f['category'] === 'other' && $f['sub_note'] === '') $errors[] = 'اذكر تفاصيل البند (أخرى)';
    } else {
        $f['sub_note'] = null;
    }

    $sig = null;
    if (!empty($_POST['signature'])) {
        $sig = process_signature((string)$_POST['signature']);
        if (!$sig) $errors[] = 'التوقيع غير صالح، أعد التوقيع';
    } elseif (!$exp || !$exp['signature_id']) {
        $errors[] = 'التوقيع مطلوب';
    }

    $inv = process_invoice_upload($_FILES['invoice'] ?? ['error' => UPLOAD_ERR_NO_FILE]);
    if (!$inv['ok']) $errors[] = $inv['err'];

    if (!$errors) {
        $pdo->beginTransaction();
        try {
            $sigId = $exp['signature_id'] ?? null;
            $invId = $exp['invoice_id'] ?? null;
            $drop = [];
            if ($sig) { if ($sigId) $drop[] = $sigId; $sigId = save_attachment('signature', $sig); }
            if ($inv['file']) { if ($invId) $drop[] = $invId; $invId = save_attachment('invoice', $inv['file']); }
            elseif ($exp && !empty($_POST['remove_invoice']) && $invId) { $drop[] = $invId; $invId = null; }

            $args = [$f['custody_id'], $f['category'], $f['sub_type'] ?: null, $f['sub_note'] ?: null,
                     $amount, $f['expense_date'], $f['description'], $sigId, $invId];
            if ($exp) {
                $pdo->prepare('UPDATE expenses SET custody_id=?, category=?, sub_type=?, sub_note=?, amount=?,
                               expense_date=?, description=?, signature_id=?, invoice_id=? WHERE id=?')
                    ->execute(array_merge($args, [$id]));
            } else {
                $pdo->prepare('INSERT INTO expenses (custody_id, category, sub_type, sub_note, amount, expense_date,
                               description, signature_id, invoice_id, created_by) VALUES (?,?,?,?,?,?,?,?,?,?)')
                    ->execute(array_merge($args, [$user['id']]));
            }
            foreach ($drop as $d) delete_attachment($d);
            $pdo->commit();
            flash($exp ? 'تم تعديل الصرف' : 'تم تسجيل الصرف');
            redirect($exp ? 'expenses.php' : 'expense_form.php');
        } catch (Throwable $ex) {
            $pdo->rollBack();
            $errors[] = 'حدث خطأ أثناء الحفظ، حاول مرة أخرى.';
        }
    }
}

render_header($exp ? 'تعديل صرف' : 'صرف جديد');
?>
<h1><?= $exp ? 'تعديل صرف' : 'تسجيل صرف' ?></h1>
<?php foreach ($errors as $er): ?><div class="alert err"><?= e($er) ?></div><?php endforeach; ?>
<?php if (!$custodies): ?><div class="alert err">لا توجد عهدة فعّالة. أضف عهدة من صفحة "العُهد".</div><?php endif; ?>

<form method="post" enctype="multipart/form-data" class="card grid" id="expenseForm">
  <?= csrf_field() ?>
  <label>العهدة <?= custody_select($custodies, (int)$f['custody_id']) ?></label>
  <label>البند <?= category_select((string)$f['category']) ?></label>

  <label id="noteBox" hidden><span id="noteLabel">اذكر التفاصيل</span>
    <input name="sub_note" value="<?= e($f['sub_note'] ?? '') ?>" maxlength="255">
  </label>

  <label>المبلغ <input name="amount" inputmode="decimal" value="<?= e($f['amount']) ?>" required></label>
  <label>التاريخ <input type="date" name="expense_date" value="<?= e($f['expense_date']) ?>" required></label>
  <label class="full">البيان <input name="description" value="<?= e($f['description']) ?>" maxlength="500" required></label>

  <div class="full">
    <span class="lbl">التوقيع (ارسم بإصبعك)<?= ($exp && $exp['signature_id']) ? ' — يوجد توقيع محفوظ، وقّع من جديد فقط للاستبدال' : '' ?></span>
    <canvas id="sig" width="600" height="200"></canvas>
    <input type="hidden" name="signature" id="signature">
    <button type="button" class="btn small" id="sigClear">مسح التوقيع</button>
    <?php if ($exp && $exp['signature_id']): ?><img class="thumb" src="file.php?id=<?= (int)$exp['signature_id'] ?>" alt="التوقيع الحالي"><?php endif; ?>
  </div>

  <div class="full">
    <span class="lbl">صورة الفاتورة (اختياري — تُضغط تلقائياً)</span>
    <input type="file" name="invoice" id="invoice" accept="image/*">
    <?php if ($exp && $exp['invoice_id']): ?>
      <div><a href="file.php?id=<?= (int)$exp['invoice_id'] ?>" target="_blank">عرض الفاتورة الحالية</a>
      <label class="chk"><input type="checkbox" name="remove_invoice" value="1"> حذف الفاتورة</label></div>
    <?php endif; ?>
  </div>

  <div class="full row">
    <button class="btn primary" id="saveBtn">حفظ</button>
    <?php if ($exp): ?><a class="btn" href="expenses.php">إلغاء</a><?php endif; ?>
  </div>
</form>
<?php render_footer();
