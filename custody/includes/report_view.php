<?php
/** جسم التقرير (HTML) يُستخدم للعرض على الشاشة ولملف PDF. $img: دالة تعيد مصدر الصورة لمعرّف مرفق. */
function report_body(array $d, callable $img): string
{
    ob_start();
    ?>
<section class="report">
  <h2 class="center">تقرير العهدة</h2>
  <p class="center"><?= e($d['custodyName']) ?> — من <?= e($d['from']) ?> إلى <?= e($d['to']) ?></p>

  <table class="summary">
    <tr><th>رصيد مرحّل قبل الفترة</th><td class="num"><?= money($d['opening']) ?></td></tr>
    <tr><th>المستلم خلال الفترة</th><td class="num"><?= money($d['recTotal']) ?></td></tr>
    <tr><th>المصروفات (بدون السلف)</th><td class="num"><?= money($d['spent']) ?></td></tr>
    <tr class="total"><th>رصيد العهدة بنهاية الفترة</th><td class="num"><?= money($d['closing']) ?></td></tr>
    <tr class="adv"><th>إجمالي السلف (منفصلة)</th><td class="num"><?= money($d['adv']) ?></td></tr>
  </table>

  <h3>المبالغ المستلمة</h3>
  <table>
    <tr><th>التاريخ</th><th>العهدة</th><th>ملاحظة</th><th>المبلغ</th></tr>
    <?php foreach ($d['receipts'] as $r): ?>
      <tr><td><?= e($r['received_date']) ?></td><td><?= e($r['custody_name']) ?></td><td><?= e($r['note']) ?></td><td class="num"><?= money($r['amount']) ?></td></tr>
    <?php endforeach; if (!$d['receipts']): ?><tr><td colspan="4" class="muted">لا يوجد</td></tr><?php endif; ?>
    <tr class="total"><td colspan="3">الإجمالي</td><td class="num"><?= money($d['recTotal']) ?></td></tr>
  </table>

  <?php
  $ordered = array_merge(array_diff(array_keys(CATEGORIES), [ADVANCE]), [ADVANCE]);
  foreach ($ordered as $key):
      if (empty($d['groups'][$key])) continue;
      $sub = array_sum(array_column($d['groups'][$key], 'amount'));
  ?>
  <h3 class="<?= $key === ADVANCE ? 'adv-h' : '' ?>"><?= e(CATEGORIES[$key]) ?><?= $key === ADVANCE ? ' (منفصلة عن المصروفات)' : '' ?></h3>
  <table>
    <tr><th>التاريخ</th><th>التفصيل</th><th>البيان</th><th>المبلغ</th><th>التوقيع</th><?php if ($d['withImages']): ?><th>الفاتورة</th><?php endif; ?></tr>
    <?php foreach ($d['groups'][$key] as $x): ?>
      <tr>
        <td><?= e($x['expense_date']) ?></td>
        <td><?= e(category_label($x['category'], $x['sub_type'], $x['sub_note'])) ?></td>
        <td><?= e($x['description']) ?></td>
        <td class="num"><?= money($x['amount']) ?></td>
        <td><?= $x['signature_id'] ? '<img class="sig" src="' . e($img((int)$x['signature_id'])) . '" alt="">' : '' ?></td>
        <?php if ($d['withImages']): ?>
          <td><?= $x['invoice_id'] ? '<img class="inv" loading="lazy" src="' . e($img((int)$x['invoice_id'])) . '" alt="">' : '-' ?></td>
        <?php endif; ?>
      </tr>
    <?php endforeach; ?>
    <tr class="total"><td colspan="3">إجمالي <?= e(CATEGORIES[$key]) ?></td><td class="num"><?= money($sub) ?></td><td colspan="<?= $d['withImages'] ? 2 : 1 ?>"></td></tr>
  </table>
  <?php endforeach; if (!$d['expenses']): ?><p class="muted">لا توجد مصروفات في هذه الفترة.</p><?php endif; ?>
</section><?php
    return ob_get_clean();
}
