<?php
// تشغيل لمرة واحدة: ينشئ الجداول وأول مستخدم. احذف هذا الملف بعد الانتهاء.
require __DIR__ . '/includes/bootstrap.php';
require __DIR__ . '/includes/layout.php';

$pdo = db();
foreach (array_filter(array_map('trim', explode(";\n", file_get_contents(__DIR__ . '/schema.sql')))) as $sql) {
    $pdo->exec($sql);
}
if ((int)$pdo->query('SELECT COUNT(*) FROM users')->fetchColumn() > 0) {
    http_response_code(403);
    exit('تم الإعداد مسبقاً. احذف ملف setup.php من السيرفر.');
}

$err = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    csrf_verify();
    $username = trim($_POST['username'] ?? '');
    $full = trim($_POST['full_name'] ?? '');
    $pass = $_POST['password'] ?? '';
    $cname = trim($_POST['custody'] ?? '') ?: 'العهدة الرئيسية';
    if (!preg_match('/^[A-Za-z0-9_.-]{3,50}$/', $username) || $full === '' || strlen($pass) < 8) {
        $err = 'اسم المستخدم (حروف إنجليزية/أرقام 3+) والاسم مطلوبان، وكلمة المرور 8 خانات على الأقل.';
    } else {
        $pdo->prepare('INSERT INTO users (username, full_name, password_hash) VALUES (?,?,?)')
            ->execute([$username, $full, password_hash($pass, PASSWORD_DEFAULT)]);
        $pdo->prepare('INSERT INTO custodies (name) VALUES (?)')->execute([$cname]);
        flash('تم الإعداد. سجّل دخولك، ولا تنسَ حذف setup.php من السيرفر.');
        redirect('login.php');
    }
}
render_header('إعداد النظام', false);
?>
<div class="card narrow">
  <h1>إعداد النظام</h1>
  <?php if ($err): ?><div class="alert err"><?= e($err) ?></div><?php endif; ?>
  <form method="post">
    <?= csrf_field() ?>
    <label>اسم العهدة <input name="custody" placeholder="العهدة الرئيسية"></label>
    <label>اسمك <input name="full_name" required></label>
    <label>اسم المستخدم <input name="username" required dir="ltr" autocomplete="username"></label>
    <label>كلمة المرور <input type="password" name="password" required minlength="8" dir="ltr" autocomplete="new-password"></label>
    <button class="btn primary">إنشاء</button>
  </form>
</div>
<?php render_footer([]);
