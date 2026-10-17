<?php
function render_header(string $title, bool $nav = true): void
{
    global $config;
    $page = basename($_SERVER['SCRIPT_NAME']);
    $links = [
        'index.php'        => 'الرئيسية',
        'receipts.php'     => 'الاستلام',
        'expense_form.php' => '+ صرف جديد',
        'expenses.php'     => 'المصروفات',
        'report.php'       => 'التقارير',
        'custodies.php'    => 'العُهد',
        'users.php'        => 'المستخدمون',
        'password.php'     => 'حسابي',
    ];
    $me = $GLOBALS['me'] ?? null;
    if ($me && !$me['admin']) {
        unset($links['custodies.php'], $links['users.php']);
    }
    ?><!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title><?= e($title) ?> - <?= e($config['app_name']) ?></title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<?php if ($nav): ?>
<header class="top no-print">
  <div class="brand"><?= e($config['app_name']) ?><?php if ($me): ?> <small class="muted">— <?= e($me['name']) ?></small><?php endif; ?></div>
  <nav>
    <?php foreach ($links as $href => $label): ?>
      <a href="<?= $href ?>" class="<?= $page === $href ? 'active' : '' ?>"><?= e($label) ?></a>
    <?php endforeach; ?>
    <a href="logout.php" class="logout">خروج</a>
  </nav>
</header>
<?php endif; ?>
<main class="wrap">
<?php if ($f = flash()): ?>
  <div class="alert <?= e($f['type']) ?> no-print"><?= e($f['msg']) ?></div>
<?php endif;
}

function render_footer(array $scripts = ['assets/app.js']): void
{
    foreach ($scripts as $s) {
        echo '<script src="' . e($s) . '"></script>' . "\n";
    }
    echo "</main>\n</body>\n</html>";
}

function custody_select(array $custodies, int $selected, bool $withAll = false, string $name = 'custody'): string
{
    $h = '<select name="' . e($name) . '">';
    if ($withAll) {
        $h .= '<option value="0">كل العُهد</option>';
    }
    foreach ($custodies as $c) {
        $h .= '<option value="' . (int)$c['id'] . '"' . ((int)$c['id'] === $selected ? ' selected' : '') . '>'
            . e($c['name']) . '</option>';
    }
    return $h . '</select>';
}

function category_select(string $selected, bool $withAll = false, string $name = 'category'): string
{
    $h = '<select name="' . e($name) . '" id="category">';
    if ($withAll) {
        $h .= '<option value="">كل البنود</option>';
    }
    foreach (CATEGORIES as $k => $v) {
        $h .= '<option value="' . e($k) . '"' . ($k === $selected ? ' selected' : '') . '>' . e($v) . '</option>';
    }
    return $h . '</select>';
}
