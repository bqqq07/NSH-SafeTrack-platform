<?php
require __DIR__ . '/includes/bootstrap.php';
require __DIR__ . '/includes/layout.php';
require __DIR__ . '/includes/report_view.php';
require_login();
$pdo = db();

$custodies = get_custodies(false);
$cid = (int)($_GET['custody'] ?? 0);
$cat = $_GET['category'] ?? '';
$from = valid_date($_GET['from'] ?? '') ? $_GET['from'] : '';
$to = valid_date($_GET['to'] ?? '') ? $_GET['to'] : '';
if ($from && !$to) $to = $from;            // يوم محدد
if ($to && !$from) $from = $to;
$withImages = !empty($_GET['images']);
$generated = $from !== '';

$custodyName = 'كل العُهد';
foreach ($custodies as $c) if ((int)$c['id'] === $cid) $custodyName = $c['name'];

$receipts = $expenses = [];
if ($generated) {
    $ra = [$from, $to]; $rw = 'r.received_date BETWEEN ? AND ?';
    $ea = [$from, $to]; $ew = 'e.expense_date BETWEEN ? AND ?';
    if ($cid) { $rw .= ' AND r.custody_id = ?'; $ra[] = $cid; $ew .= ' AND e.custody_id = ?'; $ea[] = $cid; }
    if (isset(CATEGORIES[$cat])) { $ew .= ' AND e.category = ?'; $ea[] = $cat; }

    $st = $pdo->prepare("SELECT r.*, c.name AS custody_name FROM receipts r JOIN custodies c ON c.id=r.custody_id
                         WHERE $rw ORDER BY r.received_date, r.id");
    $st->execute($ra);
    $receipts = $st->fetchAll();

    $st = $pdo->prepare("SELECT e.*, c.name AS custody_name FROM expenses e JOIN custodies c ON c.id=e.custody_id
                         WHERE $ew ORDER BY e.expense_date, e.id");
    $st->execute($ea);
    $expenses = $st->fetchAll();
}

// ملخص الفترة + الرصيد المرحّل (الرصيد قبل بداية الفترة)
$prev = $generated ? custody_totals($cid, null, date('Y-m-d', strtotime($from . ' -1 day'))) : null;
$per = $generated ? custody_totals($cid, $from, $to) : ['received' => 0, 'spent' => 0, 'advances' => 0];
$recTotal = $per['received'];   // الملخص دائماً للفترة كاملة حتى لو فُلتر البند في التفاصيل
$spent = $per['spent'];
$adv = $per['advances'];
$groups = [];
foreach ($expenses as $x) {
    $groups[$x['category']][] = $x;
}
$opening = $prev['balance'] ?? 0;
$closing = $opening + $recTotal - $spent;
$data = [
    'custodyName' => $custodyName, 'from' => $from, 'to' => $to, 'opening' => $opening, 'recTotal' => $recTotal,
    'spent' => $spent, 'adv' => $adv, 'closing' => $closing, 'receipts' => $receipts, 'expenses' => $expenses,
    'groups' => $groups, 'withImages' => $withImages,
];

if ($generated && ($_GET['export'] ?? '') === 'pdf') {
    require __DIR__ . '/includes/pdf.php';
    send_report_pdf($data, 'custody_' . $from . '_' . $to . '.pdf');
}

if ($generated && ($_GET['export'] ?? '') === 'csv') {
    header('Content-Type: text/csv; charset=utf-8');
    header('Content-Disposition: attachment; filename="custody_' . $from . '_' . $to . '.csv"');
    echo "\xEF\xBB\xBF";
    $o = fopen('php://output', 'w');
    fputcsv($o, ['النوع', 'التاريخ', 'العهدة', 'البند', 'البيان', 'المبلغ']);
    foreach ($receipts as $r) fputcsv($o, ['استلام', $r['received_date'], $r['custody_name'], '', $r['note'], $r['amount']]);
    foreach ($expenses as $x) fputcsv($o, [$x['category'] === ADVANCE ? 'سلفة' : 'صرف', $x['expense_date'], $x['custody_name'],
        category_label($x['category'], $x['sub_type'], $x['sub_note']), $x['description'], $x['amount']]);
    exit;
}

render_header('التقارير');
?>
<h1 class="no-print">التقارير</h1>
<form method="get" class="card grid no-print">
  <label>العهدة <?= custody_select($custodies, $cid, true) ?></label>
  <label>البند <?= category_select($cat, true) ?></label>
  <label>من تاريخ <input type="date" name="from" value="<?= e($from) ?>" required></label>
  <label>إلى تاريخ <input type="date" name="to" value="<?= e($to) ?>"></label>
  <label class="chk"><input type="checkbox" name="images" value="1" <?= $withImages ? 'checked' : '' ?>> إرفاق صور الفواتير (يزيد حجم التقرير)</label>
  <button class="btn primary">عرض التقرير</button>
  <p class="muted full">لتقرير يوم واحد اترك "إلى تاريخ" فارغاً أو اجعله نفس "من تاريخ".</p>
</form>

<?php if ($generated): ?>
<div class="row no-print">
  <a class="btn primary" href="?<?= e(http_build_query(array_merge($_GET, ['export' => 'pdf']))) ?>">تحميل PDF</a>
  <button class="btn" onclick="window.print()">طباعة</button>
  <a class="btn" href="?<?= e(http_build_query(array_merge($_GET, ['export' => 'csv']))) ?>">تصدير Excel (CSV)</a>
</div>

<?= report_body($data, fn($id, $thumb = false) => 'file.php?id=' . $id . ($thumb ? '&thumb=1' : '')) ?>
<?php endif; ?>
<?php render_footer([]);
