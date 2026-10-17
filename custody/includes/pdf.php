<?php
require_once __DIR__ . '/../vendor/autoload.php';

function attachment_data_uri(int $id): string
{
    $st = db()->prepare('SELECT mime, data FROM attachments WHERE id = ?');
    $st->execute([$id]);
    $r = $st->fetch();
    return $r ? 'data:' . $r['mime'] . ';base64,' . base64_encode($r['data']) : '';
}

/** يولّد PDF عربي (A4) من جسم التقرير ويرسله للمتصفح. */
function send_report_pdf(array $d, string $filename): never
{
    global $config;
    $tmp = __DIR__ . '/../tmp';
    if (!is_dir($tmp)) {
        @mkdir($tmp, 0755, true);
        @file_put_contents($tmp . '/.htaccess', "Require all denied\n");
    }
    $mpdf = new \Mpdf\Mpdf([
        'mode' => 'utf-8', 'format' => 'A4', 'tempDir' => $tmp,
        'default_font' => 'xbriyaz', 'default_font_size' => 11,
        'margin_left' => 12, 'margin_right' => 12, 'margin_top' => 14, 'margin_bottom' => 16,
    ]);
    $mpdf->SetDirectionality('rtl');
    $mpdf->autoScriptToLang = true;
    $mpdf->autoLangToFont = true;
    $mpdf->SetTitle('تقرير العهدة');
    $mpdf->SetHTMLFooter('<div style="text-align:center;font-size:9pt;color:#555">' . htmlspecialchars($config['app_name'])
        . ' — صفحة {PAGENO} من {nb}</div>');

    $css = 'body{direction:rtl;font-family:xbriyaz}
      h2{text-align:center;font-size:18pt;margin:0}h3{font-size:13pt;margin:14px 0 4px}
      p.center{text-align:center;margin:2px 0 10px}
      table{width:100%;border-collapse:collapse;margin-bottom:6px}
      th,td{border:1px solid #888;padding:4px 6px;text-align:right;font-size:10pt}
      th{background-color:#e8eef0}
      td.num{text-align:left;white-space:nowrap}
      table.summary{width:70%;margin:0 auto 10px}
      tr.total td,tr.total th{background-color:#f0f0f0;font-weight:bold}
      tr.adv td,tr.adv th{background-color:#fff1d6}
      h3.adv-h{background-color:#fff1d6;padding:3px 6px}
      .muted{color:#666}
      img.sig{height:26px}img.inv{width:200px}';
    $mpdf->WriteHTML($css, \Mpdf\HTMLParserMode::HEADER_CSS);
    $mpdf->WriteHTML(report_body($d, 'attachment_data_uri'), \Mpdf\HTMLParserMode::HTML_BODY);
    $mpdf->Output($filename, \Mpdf\Output\Destination::DOWNLOAD);
    exit;
}
