<?php
require __DIR__ . '/includes/bootstrap.php';
require __DIR__ . '/includes/layout.php';
require_login();

$custodies = get_custodies();
$cid = (int)($_GET['custody'] ?? 0);
$t = custody_totals($cid);

$where = $cid ? 'WHERE e.custody_id = ' . $cid : '';
$recent = db()->query(
    "SELECT e.*, c.name AS custody_name FROM expenses e JOIN custodies c ON c.id = e.custody_id
     $where ORDER BY e.expense_date DESC, e.id DESC LIMIT 10"
)->fetchAll();

render_header('الرئيسية');
?>
<div class="row between">
  <h1>الرئيسية</h1>
  <?php if (count($custodies) > 1): ?>
  <form method="get" class="inline"><?= custody_select($custodies, $cid, true) ?>
    <button class="btn">عرض</button></form>
  <?php endif; ?>
</div>

<div class="stats">
  <div class="stat in"><span>إجمالي المستلم</span><b><?= money($t['received']) ?></b></div>
  <div class="stat out"><span>إجمالي المصروف</span><b><?= money($t['spent']) ?></b></div>
  <div class="stat <?= $t['balance'] < 0 ? 'neg' : 'pos' ?>"><span>رصيد العهدة</span><b><?= money($t['balance']) ?></b></div>
  <div class="stat sep"><span>السلف (منفصلة)</span><b><?= money($t['advances']) ?></b></div>
</div>

<div class="actions">
  <a class="btn primary" href="expense_form.php">+ تسجيل صرف</a>
  <a class="btn" href="receipts.php">+ استلام مبلغ</a>
  <a class="btn" href="report.php">تقرير</a>
</div>

<h2>آخر العمليات</h2>
<div class="tablewrap cards"><table>
  <tr><th>التاريخ</th><th>البند</th><th>البيان</th><th>المبلغ</th></tr>
  <?php foreach ($recent as $r): ?>
  <tr class="<?= $r['category'] === ADVANCE ? 'adv' : '' ?>">
    <td data-label="التاريخ"><?= e($r['expense_date']) ?></td>
    <td data-label="البند"><?= category_chip($r['category'], $r['sub_type'], $r['sub_note']) ?></td>
    <td data-label="البيان"><?= e($r['description']) ?></td>
    <td class="num" data-label="المبلغ"><?= money($r['amount']) ?></td>
  </tr>
  <?php endforeach; if (!$recent): ?><tr><td colspan="4" class="muted">لا توجد عمليات بعد</td></tr><?php endif; ?>
</table></div>
<?php render_footer([]);
