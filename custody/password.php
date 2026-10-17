<?php
require __DIR__ . '/includes/bootstrap.php';
require __DIR__ . '/includes/layout.php';
$me = require_login();

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    csrf_verify();
    $st = db()->prepare('SELECT password_hash FROM users WHERE id = ?');
    $st->execute([$me['id']]);
    $new = $_POST['new'] ?? '';
    if (!password_verify($_POST['old'] ?? '', (string)$st->fetchColumn())) {
        flash('كلمة المرور الحالية غير صحيحة', 'err');
    } elseif (strlen($new) < 8 || $new !== ($_POST['confirm'] ?? '')) {
        flash('كلمة المرور الجديدة 8 خانات على الأقل ويجب أن تتطابق مع التأكيد', 'err');
    } else {
        db()->prepare('UPDATE users SET password_hash = ? WHERE id = ?')
            ->execute([password_hash($new, PASSWORD_DEFAULT), $me['id']]);
        flash('تم تغيير كلمة المرور');
    }
    redirect('password.php');
}
render_header('حسابي');
?>
<h1>حسابي — <?= e($me['name']) ?></h1>
<form method="post" class="card narrow" style="margin-inline:0">
  <?= csrf_field() ?>
  <label>كلمة المرور الحالية <input type="password" name="old" required dir="ltr" autocomplete="current-password"></label>
  <label>كلمة المرور الجديدة <input type="password" name="new" required minlength="8" dir="ltr" autocomplete="new-password"></label>
  <label>تأكيد الجديدة <input type="password" name="confirm" required minlength="8" dir="ltr" autocomplete="new-password"></label>
  <button class="btn primary">تغيير</button>
</form>
<?php render_footer([]);
