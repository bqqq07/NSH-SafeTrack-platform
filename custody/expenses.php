<?php
require __DIR__ . '/includes/bootstrap.php';
require __DIR__ . '/includes/layout.php';
$user = require_login();
$pdo = db();

if ($_SERVER['REQUEST_METHOD'] === 'POST' && ($_POST['action'] ?? '') === 'delete') {
    csrf_verify();
    if (!$user['admin']) { http_response_code(403); exit('الحذف للمدير فقط.'); }
    $id = (int)($_POST['id'] ?? 0);
    $st = $pdo->prepare('SELECT signature_id, invoice_id FROM expenses WHERE id = ?');
    $st->execute([$id]);
    if ($row = $st->fetch()) {
        $pdo->prepare('DELETE FROM expenses WHERE id = ?')->execute([$id]);
        delete_attachment($row['signature_id'] ? (int)$row['signature_id'] : null);
        delete_attachment($row['invoice_id'] ? (int)$row['invoice_id'] : null);
        flash('تم حذف الصرف');
    }
    redirect('expenses.php');
}

$custodies = get_custodies(false);
$cid = (int)($_GET['custody'] ?? 0);
$cat = $_GET['category'] ?? '';
$from = valid_date($_GET['from'] ?? '') ? $_GET['from'] : '';
$to = valid_date($_GET['to'] ?? '') ? $_GET['to'] : '';

$where = []; $args = [];
if ($cid) { $where[] = 'e.custody_id = ?'; $args[] = $cid; }
if (isset(CATEGORIES[$cat])) { $where[] = 'e.category = ?'; $args[] = $cat; }
if ($from) { $where[] = 'e.expense_date >= ?'; $args[] = $from; }
if ($to) { $where[] = 'e.expense_date <= ?'; $args[] = $to; }
$st = $pdo->prepare(
    'SELECT e.*, c.name AS custody_name, u.full_name AS by_name FROM expenses e JOIN custodies c ON c.id = e.custody_id LEFT JOIN users u ON u.id = e.created_by'
    . ($where ? ' WHERE ' . implode(' AND ', $where) : '')
    . ' ORDER BY e.expense_date DESC, e.id DESC LIMIT 300'
);
$st->execute($args);
$list = $st->fetchAll();

render_header('المصروفات');
?>
<h1>المصروفات</h1>
<form method="get" class="card grid">
  <label>العهدة <?= custody_select($custodies, $cid, true) ?></label>
  <label>البند <?= category_select($cat, true) ?></label>
  <label>من <input type="date" name="from" value="<?= e($from) ?>"></label>
  <label>إلى <input type="date" name="to" value="<?= e($to) ?>"></label>
  <button class="btn primary">تصفية</button>
</form>

<div class="tablewrap cards"><table>
  <tr><th>التاريخ</th><th>العهدة</th><th>البند</th><th>البيان</th><th>المبلغ</th><th>فاتورة</th><th>سجّله</th><th></th></tr>
  <?php foreach ($list as $r): ?>
  <tr class="<?= $r['category'] === ADVANCE ? 'adv' : '' ?>">
    <td data-label="التاريخ"><?= e($r['expense_date']) ?></td><td data-label="العهدة"><?= e($r['custody_name']) ?></td>
    <td data-label="البند"><?= category_chip($r['category'], $r['sub_type'], $r['sub_note']) ?></td>
    <td data-label="البيان"><?= e($r['description']) ?></td><td class="num" data-label="المبلغ"><?= money($r['amount']) ?></td>
    <td data-label="فاتورة"><?= $r['invoice_id'] ? '<a target="_blank" href="file.php?id=' . (int)$r['invoice_id'] . '">عرض</a>' : '-' ?></td>
    <td data-label="سجّله"><?= e($r['by_name']) ?></td>
    <td class="row act">
      <?php if ($user['admin'] || (int)$r['created_by'] === $user['id']): ?>
      <a class="btn small" href="expense_form.php?id=<?= (int)$r['id'] ?>">تعديل</a>
      <?php endif; if ($user['admin']): ?>
      <form method="post" data-confirm="حذف هذا الصرف نهائياً؟"><?= csrf_field() ?>
        <input type="hidden" name="action" value="delete"><input type="hidden" name="id" value="<?= (int)$r['id'] ?>">
        <button class="btn small danger">حذف</button></form>
      <?php endif; ?>
    </td>
  </tr>
  <?php endforeach; if (!$list): ?><tr><td colspan="8" class="muted">لا توجد نتائج</td></tr><?php endif; ?>
</table></div>
<?php render_footer();
