<?php
require __DIR__ . '/includes/bootstrap.php';
require __DIR__ . '/includes/layout.php';

if (!empty($_SESSION['uid'])) {
    redirect('index.php');
}
$err = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    csrf_verify();
    if (($_SESSION['lock_until'] ?? 0) > time()) {
        $err = 'محاولات كثيرة، انتظر بضع دقائق.';
    } else {
        ensure_schema();
        $st = db()->prepare('SELECT * FROM users WHERE username = ? AND is_active = 1');
        $st->execute([trim($_POST['username'] ?? '')]);
        $u = $st->fetch();
        if ($u && password_verify($_POST['password'] ?? '', $u['password_hash'])) {
            session_regenerate_id(true);
            $_SESSION['uid'] = (int)$u['id'];
            $_SESSION['fails'] = 0;
            redirect('index.php');
        }
        $_SESSION['fails'] = ($_SESSION['fails'] ?? 0) + 1;
        if ($_SESSION['fails'] >= 5) {
            $_SESSION['lock_until'] = time() + 300;
            $_SESSION['fails'] = 0;
        }
        sleep(1);
        $err = 'اسم المستخدم أو كلمة المرور غير صحيحة.';
    }
}
render_header('تسجيل الدخول', false);
?>
<div class="card narrow">
  <h1><?= e($config['app_name']) ?></h1>
  <?php if ($err): ?><div class="alert err"><?= e($err) ?></div><?php endif; ?>
  <form method="post">
    <?= csrf_field() ?>
    <label>اسم المستخدم <input name="username" required dir="ltr" autocomplete="username" autofocus></label>
    <label>كلمة المرور <input type="password" name="password" required dir="ltr" autocomplete="current-password"></label>
    <button class="btn primary">دخول</button>
  </form>
</div>
<?php render_footer([]);
