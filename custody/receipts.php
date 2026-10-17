<?php
require __DIR__ . '/includes/bootstrap.php';
require __DIR__ . '/includes/layout.php';
$user = require_login();
$pdo = db();
$custodies = get_custodies();

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    csrf_verify();
    if (($_POST['action'] ?? '') === 'delete') {
        $pdo->prepare('DELETE FROM receipts WHERE id = ?')->execute([(int)($_POST['id'] ?? 0)]);
        flash('تم حذف الاستلام');
    } else {
        $amount = parse_amount($_POST['amount'] ?? '');
        $date = $_POST['received_date'] ?? '';
        $cid = (int)($_POST['custody'] ?? 0);
        $ok = $pdo->prepare('SELECT 1 FROM custodies WHERE id = ?');
        $ok->execute([$cid]);
        if ($amount === null || !valid_date($date) || !$ok->fetchColumn()) {
            flash('تأكد من المبلغ والتاريخ والعهدة', 'err');
        } else {
            $pdo->prepare('INSERT INTO receipts (custody_id, amount, received_date, note, created_by) VALUES (?,?,?,?,?)')
                ->execute([$cid, $amount, $date, mb_substr(trim($_POST['note'] ?? ''), 0, 255) ?: null, $user['id']]);
            flash('تم تسجيل الاستلام');
        }
    }
    redirect('receipts.php');
}

$list = $pdo->query(
    'SELECT r.*, c.name AS custody_name FROM receipts r JOIN custodies c ON c.id = r.custody_id
     ORDER BY r.received_date DESC, r.id DESC LIMIT 200'
)->fetchAll();

render_header('الاستلام');
?>
<h1>استلام مبالغ العهدة</h1>
<form method="post" class="card grid">
  <?= csrf_field() ?>
  <label>العهدة <?= custody_select($custodies, (int)($custodies[0]['id'] ?? 0)) ?></label>
  <label>المبلغ <input name="amount" inputmode="decimal" required></label>
  <label>تاريخ الاستلام <input type="date" name="received_date" value="<?= date('Y-m-d') ?>" required></label>
  <label>ملاحظة (اختياري) <input name="note" maxlength="255"></label>
  <button class="btn primary">حفظ</button>
</form>

<div class="tablewrap"><table>
  <tr><th>التاريخ</th><th>العهدة</th><th>المبلغ</th><th>ملاحظة</th><th></th></tr>
  <?php foreach ($list as $r): ?>
  <tr>
    <td><?= e($r['received_date']) ?></td><td><?= e($r['custody_name']) ?></td>
    <td class="num"><?= money($r['amount']) ?></td><td><?= e($r['note']) ?></td>
    <td><form method="post" data-confirm="حذف هذا الاستلام؟"><?= csrf_field() ?>
      <input type="hidden" name="action" value="delete"><input type="hidden" name="id" value="<?= (int)$r['id'] ?>">
      <button class="btn small danger">حذف</button></form></td>
  </tr>
  <?php endforeach; if (!$list): ?><tr><td colspan="5" class="muted">لا توجد مبالغ مستلمة بعد</td></tr><?php endif; ?>
</table></div>
<?php render_footer();
