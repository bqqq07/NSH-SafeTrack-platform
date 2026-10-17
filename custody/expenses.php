<?php
require __DIR__ . '/includes/bootstrap.php';
require __DIR__ . '/includes/layout.php';
require_login();
$pdo = db();

if ($_SERVER['REQUEST_METHOD'] === 'POST' && ($_POST['action'] ?? '') === 'delete') {
    csrf_verify();
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
    'SELECT e.*, c.name AS custody_name FROM expenses e JOIN custodies c ON c.id = e.custody_id'
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

<div class="tablewrap"><table>
  <tr><th>التاريخ</th><th>العهدة</th><th>البند</th><th>البيان</th><th>المبلغ</th><th>فاتورة</th><th></th></tr>
  <?php foreach ($list as $r): ?>
  <tr class="<?= $r['category'] === ADVANCE ? 'adv' : '' ?>">
    <td><?= e($r['expense_date']) ?></td><td><?= e($r['custody_name']) ?></td>
    <td><?= e(category_label($r['category'], $r['sub_type'], $r['sub_note'])) ?></td>
    <td><?= e($r['description']) ?></td><td class="num"><?= money($r['amount']) ?></td>
    <td><?= $r['invoice_id'] ? '<a target="_blank" href="file.php?id=' . (int)$r['invoice_id'] . '">عرض</a>' : '-' ?></td>
    <td class="row">
      <a class="btn small" href="expense_form.php?id=<?= (int)$r['id'] ?>">تعديل</a>
      <form method="post" data-confirm="حذف هذا الصرف نهائياً؟"><?= csrf_field() ?>
        <input type="hidden" name="action" value="delete"><input type="hidden" name="id" value="<?= (int)$r['id'] ?>">
        <button class="btn small danger">حذف</button></form>
    </td>
  </tr>
  <?php endforeach; if (!$list): ?><tr><td colspan="7" class="muted">لا توجد نتائج</td></tr><?php endif; ?>
</table></div>
<?php render_footer();
