<?php
require __DIR__ . '/includes/bootstrap.php';
require __DIR__ . '/includes/layout.php';
require_login();
$pdo = db();

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    csrf_verify();
    $action = $_POST['action'] ?? '';
    $name = mb_substr(trim($_POST['name'] ?? ''), 0, 120);
    $id = (int)($_POST['id'] ?? 0);
    if ($action === 'add' && $name !== '') {
        $pdo->prepare('INSERT INTO custodies (name) VALUES (?)')->execute([$name]);
        flash('تمت إضافة العهدة');
    } elseif ($action === 'rename' && $name !== '' && $id) {
        $pdo->prepare('UPDATE custodies SET name = ? WHERE id = ?')->execute([$name, $id]);
        flash('تم التعديل');
    } elseif ($action === 'toggle' && $id) {
        $pdo->prepare('UPDATE custodies SET is_active = 1 - is_active WHERE id = ?')->execute([$id]);
        flash('تم التحديث');
    }
    redirect('custodies.php');
}
$list = get_custodies(false);
render_header('العُهد');
?>
<h1>العُهد</h1>
<form method="post" class="card row">
  <?= csrf_field() ?><input type="hidden" name="action" value="add">
  <input name="name" placeholder="اسم عهدة جديدة (مثلاً: عهدة المشروع / اسم المستلم)" required maxlength="120">
  <button class="btn primary">إضافة</button>
</form>
<div class="tablewrap"><table>
  <tr><th>الاسم</th><th>الحالة</th><th></th></tr>
  <?php foreach ($list as $c): ?>
  <tr>
    <td><form method="post" class="row"><?= csrf_field() ?>
      <input type="hidden" name="action" value="rename"><input type="hidden" name="id" value="<?= (int)$c['id'] ?>">
      <input name="name" value="<?= e($c['name']) ?>" maxlength="120"><button class="btn small">حفظ الاسم</button></form></td>
    <td><?= $c['is_active'] ? 'فعّالة' : 'موقوفة' ?></td>
    <td><form method="post"><?= csrf_field() ?>
      <input type="hidden" name="action" value="toggle"><input type="hidden" name="id" value="<?= (int)$c['id'] ?>">
      <button class="btn small"><?= $c['is_active'] ? 'إيقاف' : 'تفعيل' ?></button></form></td>
  </tr>
  <?php endforeach; ?>
</table></div>
<?php render_footer([]);
