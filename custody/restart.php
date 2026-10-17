<?php
// إعادة تشغيل: يمسح كاش PHP (OPcache) ويحدّث tmp/restart.txt (يستخدمه Passenger إن كان مفعّلاً)
require __DIR__ . '/includes/bootstrap.php';
require_admin();
$done = [];
if (function_exists('opcache_reset') && @opcache_reset()) {
    $done[] = 'تم مسح كاش PHP';
}
$dir = __DIR__ . '/tmp';
if (!is_dir($dir)) {
    @mkdir($dir, 0755, true);
}
if (@touch($dir . '/restart.txt')) {
    $done[] = 'تم تحديث restart.txt';
}
flash($done ? 'تمت إعادة التشغيل: ' . implode('، ', $done) : 'لا يوجد كاش لمسحه — التعديلات تظهر مباشرة', 'ok');
redirect('index.php');
