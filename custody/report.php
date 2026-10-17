<?php
require __DIR__ . '/includes/bootstrap.php';
require __DIR__ . '/includes/layout.php';
require_login();
$pdo = db();

$custodies = get_custodies(false);
$cid = (int)($_GET['custody'] ?? 0);
$cat = $_GET['category'] ?? '';
$from = valid_date($_GET['from'] ?? '') ? $_GET['from'] : '';
$to = valid_date($_GET['to'] ?? '') ? $_GET['to'] : '';
if ($from && !$to) $to = $from;            // يوم محدد
if ($to && !$from) $from = $to;
$withImages = !empty($_GET['images']);
$generated = $from !== '';

$custodyName = 'كل العُهد';
foreach ($custodies as $c) if ((int)$c['id'] === $cid) $custodyName = $c['name'];

$receipts = $expenses = [];
if ($generated) {
    $ra = [$from, $to]; $rw = 'r.received_date BETWEEN ? AND ?';
    $ea = [$from, $to]; $ew = 'e.expense_date BETWEEN ? AND ?';
    if ($cid) { $rw .= ' AND r.custody_id = ?'; $ra[] = $cid; $ew .= ' AND e.custody_id = ?'; $ea[] = $cid; }
    if (isset(CATEGORIES[$cat])) { $ew .= ' AND e.category = ?'; $ea[] = $cat; }

    $st = $pdo->prepare("SELECT r.*, c.name AS custody_name FROM receipts r JOIN custodies c ON c.id=r.custody_id
                         WHERE $rw ORDER BY r.received_date, r.id");
    $st->execute($ra);
    $receipts = $st->fetchAll();

    $st = $pdo->prepare("SELECT e.*, c.name AS custody_name FROM expenses e JOIN custodies c ON c.id=e.custody_id
                         WHERE $ew ORDER BY e.expense_date, e.id");
    $st->execute($ea);
    $expenses = $st->fetchAll();
}

// ملخص الفترة + الرصيد المرحّل (الرصيد قبل بداية الفترة)
$prev = $generated ? custody_totals($cid, null, date('Y-m-d', strtotime($from . ' -1 day'))) : null;
$per = $generated ? custody_totals($cid, $from, $to) : ['received' => 0, 'spent' => 0, 'advances' => 0];
$recTotal = $per['received'];   // الملخص دائماً للفترة كاملة حتى لو فُلتر البند في التفاصيل
$spent = $per['spent'];
$adv = $per['advances'];
$groups = [];
foreach ($expenses as $x) {
    $groups[$x['category']][] = $x;
}
$opening = $prev['balance'] ?? 0;
$closing = $opening + $recTotal - $spent;

if ($generated && ($_GET['export'] ?? '') === 'csv') {
    header('Content-Type: text/csv; charset=utf-8');
    header('Content-Disposition: attachment; filename="custody_' . $from . '_' . $to . '.csv"');
    echo "\xEF\xBB\xBF";
    $o = fopen('php://output', 'w');
    fputcsv($o, ['النوع', 'التاريخ', 'العهدة', 'البند', 'البيان', 'المبلغ']);
    foreach ($receipts as $r) fputcsv($o, ['استلام', $r['received_date'], $r['custody_name'], '', $r['note'], $r['amount']]);
    foreach ($expenses as $x) fputcsv($o, [$x['category'] === ADVANCE ? 'سلفة' : 'صرف', $x['expense_date'], $x['custody_name'],
        category_label($x['category'], $x['sub_type'], $x['sub_note']), $x['description'], $x['amount']]);
    exit;
}

render_header('التقارير');
?>
<h1 class="no-print">التقارير</h1>
<form method="get" class="card grid no-print">
  <label>العهدة <?= custody_select($custodies, $cid, true) ?></label>
  <label>البند <?= category_select($cat, true) ?></label>
  <label>من تاريخ <input type="date" name="from" value="<?= e($from) ?>" required></label>
  <label>إلى تاريخ <input type="date" name="to" value="<?= e($to) ?>"></label>
  <label class="chk"><input type="checkbox" name="images" value="1" <?= $withImages ? 'checked' : '' ?>> إرفاق صور الفواتير (يزيد حجم التقرير)</label>
  <button class="btn primary">عرض التقرير</button>
  <p class="muted full">لتقرير يوم واحد اترك "إلى تاريخ" فارغاً أو اجعله نفس "من تاريخ".</p>
</form>

<?php if ($generated): ?>
<div class="row no-print">
  <button class="btn" onclick="window.print()">طباعة / حفظ PDF</button>
  <a class="btn" href="?<?= e(http_build_query(array_merge($_GET, ['export' => 'csv']))) ?>">تصدير Excel (CSV)</a>
</div>

<section class="report">
  <h2 class="center">تقرير العهدة</h2>
  <p class="center"><?= e($custodyName) ?> — من <?= e($from) ?> إلى <?= e($to) ?></p>

  <table class="summary">
    <tr><th>رصيد مرحّل قبل الفترة</th><td class="num"><?= money($opening) ?></td></tr>
    <tr><th>المستلم خلال الفترة</th><td class="num"><?= money($recTotal) ?></td></tr>
    <tr><th>المصروفات (بدون السلف)</th><td class="num"><?= money($spent) ?></td></tr>
    <tr class="total"><th>رصيد العهدة بنهاية الفترة</th><td class="num"><?= money($closing) ?></td></tr>
    <tr class="adv"><th>إجمالي السلف (منفصلة)</th><td class="num"><?= money($adv) ?></td></tr>
  </table>

  <h3>المبالغ المستلمة</h3>
  <table>
    <tr><th>التاريخ</th><th>العهدة</th><th>ملاحظة</th><th>المبلغ</th></tr>
    <?php foreach ($receipts as $r): ?>
      <tr><td><?= e($r['received_date']) ?></td><td><?= e($r['custody_name']) ?></td><td><?= e($r['note']) ?></td><td class="num"><?= money($r['amount']) ?></td></tr>
    <?php endforeach; if (!$receipts): ?><tr><td colspan="4" class="muted">لا يوجد</td></tr><?php endif; ?>
    <tr class="total"><td colspan="3">الإجمالي</td><td class="num"><?= money($recTotal) ?></td></tr>
  </table>

  <?php
  $ordered = array_merge(array_diff(array_keys(CATEGORIES), [ADVANCE]), [ADVANCE]);
  foreach ($ordered as $key):
      if (empty($groups[$key])) continue;
      $sub = array_sum(array_column($groups[$key], 'amount'));
  ?>
  <h3 class="<?= $key === ADVANCE ? 'adv-h' : '' ?>"><?= e(CATEGORIES[$key]) ?><?= $key === ADVANCE ? ' (منفصلة عن المصروفات)' : '' ?></h3>
  <table>
    <tr><th>التاريخ</th><th>التفصيل</th><th>البيان</th><th>المبلغ</th><th>التوقيع</th><?php if ($withImages): ?><th>الفاتورة</th><?php endif; ?></tr>
    <?php foreach ($groups[$key] as $x): ?>
      <tr>
        <td><?= e($x['expense_date']) ?></td>
        <td><?= e(category_label($x['category'], $x['sub_type'], $x['sub_note'])) ?></td>
        <td><?= e($x['description']) ?></td>
        <td class="num"><?= money($x['amount']) ?></td>
        <td><?= $x['signature_id'] ? '<img class="sig" src="file.php?id=' . (int)$x['signature_id'] . '" alt="">' : '' ?></td>
        <?php if ($withImages): ?>
          <td><?= $x['invoice_id'] ? '<img class="inv" loading="lazy" src="file.php?id=' . (int)$x['invoice_id'] . '" alt="">' : '-' ?></td>
        <?php endif; ?>
      </tr>
    <?php endforeach; ?>
    <tr class="total"><td colspan="3">إجمالي <?= e(CATEGORIES[$key]) ?></td><td class="num"><?= money($sub) ?></td><td colspan="<?= $withImages ? 2 : 1 ?>"></td></tr>
  </table>
  <?php endforeach; if (!$expenses): ?><p class="muted">لا توجد مصروفات في هذه الفترة.</p><?php endif; ?>
</section>
<?php endif; ?>
<?php render_footer([]);
