<?php
declare(strict_types=1);

$configFile = __DIR__ . '/../config.php';
if (!is_file($configFile)) {
    http_response_code(500);
    exit('ملف config.php غير موجود. انسخ config.sample.php إلى config.php وعدّل بيانات قاعدة البيانات.');
}
$config = require $configFile;
date_default_timezone_set($config['timezone'] ?? 'Asia/Riyadh');

$isHttps = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off')
    || ($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https';
session_set_cookie_params([
    'lifetime' => 0, 'path' => '/', 'secure' => $isHttps,
    'httponly' => true, 'samesite' => 'Lax',
]);
session_start();

header('X-Frame-Options: SAMEORIGIN');
header('X-Content-Type-Options: nosniff');
header('Referrer-Policy: same-origin');

/* ---------- البنود ---------- */

const CATEGORIES = [
    'advance'        => 'سلفة',
    'purchases'      => 'مشتريات ونواقص',
    'meals'          => 'وجبات',
    'maintenance'    => 'صيانة وإصلاحات',
    'transport'      => 'مواصلات',
    'other'          => 'أخرى (تذكر)',
    'warehouses'     => 'مخازن',
    'rent'           => 'إيجار',
    'shipping'       => 'شحن بضائع',
    'daily_services' => 'يومية خدمات',
];
const NOTE_CATEGORIES = ['other', 'rent']; // بنود فيها حقل تفاصيل (إلزامي في 'أخرى' فقط)
const ADVANCE = 'advance'; // السلفة مفصولة عن باقي المصروفات والرصيد

/* ---------- أدوات عامة ---------- */

function db(): PDO
{
    static $pdo = null;
    global $config;
    if ($pdo === null) {
        try {
            $pdo = new PDO(
                "mysql:host={$config['db_host']};dbname={$config['db_name']};charset=utf8mb4",
                $config['db_user'], $config['db_pass'],
                [
                    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
                    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
                    PDO::ATTR_EMULATE_PREPARES => false,
                ]
            );
        } catch (PDOException $e) {
            http_response_code(500);
            exit('تعذر الاتصال بقاعدة البيانات. تأكد من بيانات config.php');
        }
    }
    return $pdo;
}

function e($v): string
{
    return htmlspecialchars((string)$v, ENT_QUOTES, 'UTF-8');
}

function money($v): string
{
    return number_format((float)$v, 2);
}

function redirect(string $url): never
{
    header('Location: ' . $url);
    exit;
}

function normalize_digits(string $s): string
{
    return strtr(trim($s), [
        '٠' => '0', '١' => '1', '٢' => '2', '٣' => '3', '٤' => '4',
        '٥' => '5', '٦' => '6', '٧' => '7', '٨' => '8', '٩' => '9',
        '٫' => '.', '٬' => '', ',' => '',
    ]);
}

function parse_amount(string $s): ?string
{
    $s = normalize_digits($s);
    if (!preg_match('/^\d{1,10}(\.\d{1,2})?$/', $s) || (float)$s <= 0) {
        return null;
    }
    return $s;
}

function valid_date(string $s): bool
{
    $d = DateTime::createFromFormat('Y-m-d', $s);
    return $d && $d->format('Y-m-d') === $s;
}

function category_label(string $cat, ?string $subType, ?string $subNote): string
{
    $label = CATEGORIES[$cat] ?? $cat;
    if (in_array($cat, NOTE_CATEGORIES, true) && $subNote) {
        $label .= ': ' . $subNote;
    }
    return $label;
}

function category_chip(string $cat, ?string $subType, ?string $subNote): string
{
    return '<span class="chip c-' . e($cat) . '">' . e(category_label($cat, $subType, $subNote)) . '</span>';
}

/* ---------- الجلسة و CSRF ---------- */

function flash(?string $msg = null, string $type = 'ok'): ?array
{
    if ($msg !== null) {
        $_SESSION['flash'] = ['msg' => $msg, 'type' => $type];
        return null;
    }
    $f = $_SESSION['flash'] ?? null;
    unset($_SESSION['flash']);
    return $f;
}

function csrf_token(): string
{
    if (empty($_SESSION['csrf'])) {
        $_SESSION['csrf'] = bin2hex(random_bytes(32));
    }
    return $_SESSION['csrf'];
}

function csrf_field(): string
{
    return '<input type="hidden" name="csrf" value="' . e(csrf_token()) . '">';
}

function csrf_verify(): void
{
    $sent = $_POST['csrf'] ?? '';
    if (!is_string($sent) || !hash_equals(csrf_token(), $sent)) {
        http_response_code(400);
        exit('انتهت صلاحية الجلسة، ارجع للصفحة السابقة وأعد المحاولة.');
    }
}

/** ترقية قواعد البيانات القديمة (قبل إضافة المستخدمين) تلقائياً. */
function ensure_schema(): void
{
    $cols = db()->query("SHOW COLUMNS FROM users")->fetchAll(PDO::FETCH_COLUMN);
    if (!in_array('role', $cols, true)) {
        db()->exec("ALTER TABLE users ADD role ENUM('admin','user') NOT NULL DEFAULT 'user',
                    ADD is_active TINYINT(1) NOT NULL DEFAULT 1");
        db()->exec("UPDATE users SET role = 'admin'"); // المستخدمون الحاليون كانوا مديرين
    }
    // مخازن بند مستقل، وإيجار بند مستقل (تحويل البيانات القديمة)
    db()->exec("UPDATE expenses SET category = 'warehouses', sub_type = NULL
                WHERE (category = 'rent' AND sub_type = 'warehouse') OR category = 'rent_warehouse'");
    db()->exec("UPDATE expenses SET sub_note = 'سكن', sub_type = NULL
                WHERE (category = 'rent' AND sub_type = 'housing') OR category = 'rent_housing'");
    db()->exec("UPDATE expenses SET category = 'rent', sub_type = NULL WHERE category IN ('rent_housing','rent_other')");
    db()->exec("UPDATE expenses SET sub_type = NULL WHERE category = 'rent'");
}

/** المستخدم الحالي يُقرأ من القاعدة في كل طلب، فيسري الإيقاف وتغيير الصلاحية فوراً. */
function require_login(): array
{
    if (empty($_SESSION['uid'])) {
        redirect('login.php');
    }
    $st = db()->prepare('SELECT id, full_name, role, is_active FROM users WHERE id = ?');
    $st->execute([(int)$_SESSION['uid']]);
    $u = $st->fetch();
    if (!$u || !$u['is_active']) {
        $_SESSION = [];
        redirect('login.php');
    }
    return $GLOBALS['me'] = ['id' => (int)$u['id'], 'name' => $u['full_name'], 'role' => $u['role'], 'admin' => $u['role'] === 'admin'];
}

function require_admin(): array
{
    $u = require_login();
    if (!$u['admin']) {
        http_response_code(403);
        exit('هذه الصفحة للمدير فقط.');
    }
    return $u;
}

/* ---------- العُهد ---------- */

function get_custodies(bool $onlyActive = true): array
{
    $sql = 'SELECT * FROM custodies' . ($onlyActive ? ' WHERE is_active = 1' : '') . ' ORDER BY id';
    return db()->query($sql)->fetchAll();
}

/** ملخص الأرصدة. الرصيد = المستلم - المصروفات (بدون السلف)، والسلف تُعرض منفصلة. */
function custody_totals(int $custodyId = 0, ?string $from = null, ?string $to = null): array
{
    $where = [];
    $args = [];
    if ($custodyId) { $where[] = 'custody_id = ?'; $args[] = $custodyId; }
    $rWhere = $where; $rArgs = $args;
    $eWhere = $where; $eArgs = $args;
    if ($from) { $rWhere[] = 'received_date >= ?'; $rArgs[] = $from; $eWhere[] = 'expense_date >= ?'; $eArgs[] = $from; }
    if ($to)   { $rWhere[] = 'received_date <= ?'; $rArgs[] = $to;   $eWhere[] = 'expense_date <= ?'; $eArgs[] = $to; }

    $st = db()->prepare('SELECT COALESCE(SUM(amount),0) FROM receipts' . ($rWhere ? ' WHERE ' . implode(' AND ', $rWhere) : ''));
    $st->execute($rArgs);
    $received = (float)$st->fetchColumn();

    $st = db()->prepare(
        'SELECT COALESCE(SUM(CASE WHEN category <> ? THEN amount END),0) AS spent,
                COALESCE(SUM(CASE WHEN category = ? THEN amount END),0) AS advances
         FROM expenses' . ($eWhere ? ' WHERE ' . implode(' AND ', $eWhere) : '')
    );
    $st->execute(array_merge([ADVANCE, ADVANCE], $eArgs));
    $row = $st->fetch();

    return [
        'received' => $received,
        'spent'    => (float)$row['spent'],
        'advances' => (float)$row['advances'],
        'balance'  => $received - (float)$row['spent'],
    ];
}

/* ---------- المرفقات (الصور تُخزَّن في قاعدة البيانات) ---------- */

const MAX_IMAGE_SIDE = 1280;
const JPEG_QUALITY = 70;

/** يعيد ترميز الصورة (تصغير + ضغط) ويضمن أنها صورة حقيقية. */
function compress_image(string $raw, string $outType = 'jpeg', int $maxSide = MAX_IMAGE_SIDE): ?array
{
    if (!function_exists('imagecreatefromstring')) {
        return null;
    }
    $info = @getimagesizefromstring($raw);
    if (!$info || !in_array($info[2], [IMAGETYPE_JPEG, IMAGETYPE_PNG, IMAGETYPE_WEBP, IMAGETYPE_GIF], true)) {
        return null;
    }
    $img = @imagecreatefromstring($raw);
    if (!$img) {
        return null;
    }
    // تصحيح اتجاه صور الجوال
    if ($info[2] === IMAGETYPE_JPEG && function_exists('exif_read_data')) {
        $exif = @exif_read_data('data://image/jpeg;base64,' . base64_encode($raw));
        $rot = [3 => 180, 6 => -90, 8 => 90][$exif['Orientation'] ?? 0] ?? 0;
        if ($rot) {
            $img = imagerotate($img, $rot, 0) ?: $img;
        }
    }
    $w = imagesx($img);
    $h = imagesy($img);
    $scale = min(1, $maxSide / max($w, $h));
    $nw = max(1, (int)round($w * $scale));
    $nh = max(1, (int)round($h * $scale));
    $out = imagecreatetruecolor($nw, $nh);
    imagefill($out, 0, 0, imagecolorallocate($out, 255, 255, 255));
    imagecopyresampled($out, $img, 0, 0, 0, 0, $nw, $nh, $w, $h);

    ob_start();
    if ($outType === 'png') {
        imagepng($out, null, 9);
        $mime = 'image/png';
    } else {
        imageinterlace($out, true);
        imagejpeg($out, null, JPEG_QUALITY);
        $mime = 'image/jpeg';
    }
    $data = ob_get_clean();
    return ['mime' => $mime, 'data' => $data];
}

function process_invoice_upload(array $file): array
{
    if ($file['error'] === UPLOAD_ERR_NO_FILE) {
        return ['ok' => true, 'file' => null];
    }
    if ($file['error'] !== UPLOAD_ERR_OK || !is_uploaded_file($file['tmp_name'])) {
        return ['ok' => false, 'err' => 'تعذر رفع صورة الفاتورة (قد يكون الحجم كبيراً).'];
    }
    $res = compress_image((string)file_get_contents($file['tmp_name']));
    return $res ? ['ok' => true, 'file' => $res]
                : ['ok' => false, 'err' => 'ملف الفاتورة يجب أن يكون صورة (JPG / PNG / WEBP).'];
}

function process_signature(string $dataUrl): ?array
{
    if (!preg_match('#^data:image/png;base64,([A-Za-z0-9+/=]+)$#', $dataUrl, $m) || strlen($dataUrl) > 400000) {
        return null;
    }
    return compress_image(base64_decode($m[1], true) ?: '', 'png', 700);
}

function save_attachment(string $kind, array $img): int
{
    $st = db()->prepare('INSERT INTO attachments (kind, mime, data, size) VALUES (?,?,?,?)');
    $st->bindValue(1, $kind);
    $st->bindValue(2, $img['mime']);
    $st->bindValue(3, $img['data'], PDO::PARAM_LOB);
    $st->bindValue(4, strlen($img['data']), PDO::PARAM_INT);
    $st->execute();
    return (int)db()->lastInsertId();
}

function delete_attachment(?int $id): void
{
    if ($id) {
        db()->prepare('DELETE FROM attachments WHERE id = ?')->execute([$id]);
    }
}
