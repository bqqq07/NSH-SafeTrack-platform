<?php
require __DIR__ . '/includes/bootstrap.php';
require __DIR__ . '/includes/layout.php';
$me = require_admin();
$pdo = db();

function admin_count(PDO $pdo): int
{
    return (int)$pdo->query("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1")->fetchColumn();
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    csrf_verify();
    $action = $_POST['action'] ?? '';
    $id = (int)($_POST['id'] ?? 0);
    $st = $pdo->prepare('SELECT * FROM users WHERE id = ?');
    $st->execute([$id]);
    $target = $st->fetch();

    if ($action === 'add') {
        $username = trim($_POST['username'] ?? '');
        $full = mb_substr(trim($_POST['full_name'] ?? ''), 0, 100);
        $pass = $_POST['password'] ?? '';
        $role = ($_POST['role'] ?? '') === 'admin' ? 'admin' : 'user';
        $dup = $pdo->prepare('SELECT 1 FROM users WHERE username = ?');
        $dup->execute([$username]);
        if (!preg_match('/^[A-Za-z0-9_.-]{3,50}$/', $username) || $full === '' || strlen($pass) < 8) {
            flash('اسم المستخدم (إنجليزي 3+ خانات)، الاسم، وكلمة مرور 8 خانات على الأقل', 'err');
        } elseif ($dup->fetchColumn()) {
            flash('اسم المستخدم موجود مسبقاً', 'err');
        } else {
            $pdo->prepare('INSERT INTO users (username, full_name, password_hash, role) VALUES (?,?,?,?)')
                ->execute([$username, $full, password_hash($pass, PASSWORD_DEFAULT), $role]);
            flash('تمت إضافة المستخدم');
        }
    } elseif ($target) {
        $isSelf = (int)$target['id'] === $me['id'];
        if ($action === 'reset') {
            if (strlen($_POST['password'] ?? '') < 8) {
                flash('كلمة المرور 8 خانات على الأقل', 'err');
            } else {
                $pdo->prepare('UPDATE users SET password_hash = ? WHERE id = ?')
                    ->execute([password_hash($_POST['password'], PASSWORD_DEFAULT), $id]);
                flash('تم تغيير كلمة المرور');
            }
        } elseif ($action === 'toggle' || $action === 'role') {
            // لا يُسمح بإيقاف أو إنزال آخر مدير (ولا نفسك)
            $demotes = $action === 'toggle' ? $target['is_active'] : $target['role'] === 'admin';
            if ($target['role'] === 'admin' && $demotes && ($isSelf || admin_count($pdo) <= 1)) {
                flash('لا يمكن إيقاف/إنزال نفسك أو آخر مدير', 'err');
            } elseif ($action === 'toggle') {
                $pdo->prepare('UPDATE users SET is_active = 1 - is_active WHERE id = ?')->execute([$id]);
                flash('تم التحديث');
            } else {
                $pdo->prepare("UPDATE users SET role = IF(role = 'admin','user','admin') WHERE id = ?")->execute([$id]);
                flash('تم تغيير الصلاحية');
            }
        }
    }
    redirect('users.php');
}

$users = $pdo->query('SELECT * FROM users ORDER BY id')->fetchAll();
render_header('المستخدمون');
?>
<h1>المستخدمون</h1>
<p class="muted">المدير: كل الصلاحيات (حذف، عُهد، مستخدمون). المستخدم العادي: يسجّل الاستلام والصرف ويعدّل ما سجّله هو ويطلع التقارير.</p>
<form method="post" class="card grid">
  <?= csrf_field() ?><input type="hidden" name="action" value="add">
  <label>الاسم <input name="full_name" required maxlength="100"></label>
  <label>اسم المستخدم <input name="username" required dir="ltr" autocomplete="off"></label>
  <label>كلمة المرور <input type="password" name="password" required minlength="8" dir="ltr" autocomplete="new-password"></label>
  <label>الصلاحية <select name="role"><option value="user">مستخدم</option><option value="admin">مدير</option></select></label>
  <button class="btn primary">إضافة مستخدم</button>
</form>

<div class="tablewrap"><table>
  <tr><th>الاسم</th><th>المستخدم</th><th>الصلاحية</th><th>الحالة</th><th>إجراءات</th></tr>
  <?php foreach ($users as $u): ?>
  <tr>
    <td><?= e($u['full_name']) ?></td><td dir="ltr"><?= e($u['username']) ?></td>
    <td><?= $u['role'] === 'admin' ? 'مدير' : 'مستخدم' ?></td>
    <td><?= $u['is_active'] ? 'فعّال' : 'موقوف' ?></td>
    <td class="row">
      <form method="post"><?= csrf_field() ?><input type="hidden" name="id" value="<?= (int)$u['id'] ?>">
        <input type="hidden" name="action" value="role"><button class="btn small"><?= $u['role'] === 'admin' ? 'إنزال لمستخدم' : 'ترقية لمدير' ?></button></form>
      <form method="post"><?= csrf_field() ?><input type="hidden" name="id" value="<?= (int)$u['id'] ?>">
        <input type="hidden" name="action" value="toggle"><button class="btn small"><?= $u['is_active'] ? 'إيقاف' : 'تفعيل' ?></button></form>
      <form method="post" class="inline"><?= csrf_field() ?><input type="hidden" name="id" value="<?= (int)$u['id'] ?>">
        <input type="hidden" name="action" value="reset">
        <input type="password" name="password" placeholder="كلمة مرور جديدة" minlength="8" required dir="ltr" autocomplete="new-password">
        <button class="btn small">تغيير</button></form>
    </td>
  </tr>
  <?php endforeach; ?>
</table></div>
<?php render_footer([]);
