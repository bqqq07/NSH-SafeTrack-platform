<?php
// يعرض صور الفواتير والتواقيع المخزّنة في قاعدة البيانات (للمسجلين فقط)
require __DIR__ . '/includes/bootstrap.php';
require_login();
$st = db()->prepare('SELECT mime, data FROM attachments WHERE id = ?');
$st->execute([(int)($_GET['id'] ?? 0)]);
$row = $st->fetch();
if (!$row) {
    http_response_code(404);
    exit;
}
if (!empty($_GET['thumb']) && ($small = compress_image($row['data'], 'jpeg', 400))) {
    $row = $small; // مصغّرة للتقارير؛ الأصل يُفتح بدون thumb
}
header('Content-Type: ' . $row['mime']);
header('Cache-Control: private, max-age=86400');
echo $row['data'];
