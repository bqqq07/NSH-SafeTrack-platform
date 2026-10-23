<?php
// واجهة الحسابات والمزامنة ولوحة المشرف. كل الطلبات: POST/GET على index.php?a=<action> وترجع JSON.
//   عامة:    public / setup_admin / register / login / logout / me / progress
//   مشرف:    admin_list / admin_user / admin_update / admin_password / admin_reset
//            admin_create / admin_delete / admin_settings / admin_bulk
declare(strict_types=1);
require __DIR__ . '/db.php';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');

function out(array $data, int $code = 200): never {
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

$https = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off');
session_name('ca_sid');
session_set_cookie_params(['lifetime' => 60 * 60 * 24 * 60, 'path' => '/', 'secure' => $https, 'httponly' => true, 'samesite' => 'Lax']);
session_start();

$method = $_SERVER['REQUEST_METHOD'];
$action = $_GET['a'] ?? '';

// حماية CSRF: الطلبات المغيّرة لازم تجي من جافاسكربت موقعنا (هيدر مخصص + JSON)
if ($method === 'POST') {
    $ct = $_SERVER['CONTENT_TYPE'] ?? '';
    if (($_SERVER['HTTP_X_CA'] ?? '') !== '1' || stripos($ct, 'application/json') !== 0) out(['error' => 'طلب غير مسموح'], 403);
}
$body = $method === 'POST' ? (json_decode(file_get_contents('php://input') ?: '[]', true) ?: []) : [];

/* ---------------- أدوات مساعدة ---------------- */

const USER_COLS = 'id, username, role, status, full_name, max_level, free_access, reset_epoch, last_seen, stopped_note, created_at';

function now(): string { return gmdate('Y-m-d H:i:s'); }
function client_ip(): string { return substr($_SERVER['REMOTE_ADDR'] ?? '', 0, 45); }

function current_user(): ?array {
    if (empty($_SESSION['uid'])) return null;
    $st = db()->prepare('SELECT ' . USER_COLS . ' FROM ca_users WHERE id = ?');
    $st->execute([$_SESSION['uid']]);
    return $st->fetch() ?: null;
}
function require_user(): array {
    return current_user() ?? out(['error' => 'سجّل دخولك أول'], 401);
}
function require_admin(): array {
    $u = require_user();
    if ($u['role'] !== 'admin' || $u['status'] !== 'active') out(['error' => 'هذه الصفحة للمشرف فقط'], 403);
    return $u;
}

// ما يراه التطبيق عن المستخدم (بدون أي بيانات حساسة)
function public_user(array $u): array {
    return [
        'username' => $u['username'], 'name' => $u['full_name'], 'role' => $u['role'], 'status' => $u['status'],
        'note' => $u['stopped_note'], 'max_level' => $u['max_level'] === null ? null : (int)$u['max_level'],
        'free_access' => (int)$u['free_access'] === 1, 'epoch' => (int)$u['reset_epoch'],
    ];
}

function touch_seen(int $uid): void {
    db()->prepare('UPDATE ca_users SET last_seen = ? WHERE id = ?')->execute([now(), $uid]);
}

function setting(string $k, string $default): string {
    $st = db()->prepare('SELECT v FROM ca_settings WHERE k = ?');
    $st->execute([$k]);
    $v = $st->fetchColumn();
    return $v === false ? $default : (string)$v;
}
function set_setting(string $k, string $v): void {
    $pdo = db();
    $st = $pdo->prepare('SELECT 1 FROM ca_settings WHERE k = ?');
    $st->execute([$k]);
    if ($st->fetch()) $pdo->prepare('UPDATE ca_settings SET v = ? WHERE k = ?')->execute([$v, $k]);
    else $pdo->prepare('INSERT INTO ca_settings (k, v) VALUES (?, ?)')->execute([$k, $v]);
}

function audit(int $admin, ?int $user, string $action, string $detail = ''): void {
    db()->prepare('INSERT INTO ca_audit (admin_id, user_id, action, detail, at) VALUES (?, ?, ?, ?, ?)')
        ->execute([$admin, $user, $action, mb_substr($detail, 0, 200), now()]);
}

function throttle(string $tag, int $limit): void {
    $pdo = db();
    $ip = client_ip();
    $pdo->prepare('DELETE FROM ca_login_attempts WHERE at < ?')->execute([gmdate('Y-m-d H:i:s', time() - 900)]);
    $c = $pdo->prepare('SELECT COUNT(*) FROM ca_login_attempts WHERE username = ? AND ip = ?');
    $c->execute([$tag, $ip]);
    if ((int)$c->fetchColumn() >= $limit) out(['error' => 'محاولات كثيرة، جرّب بعد قليل'], 429);
}
function note_attempt(string $tag): void {
    db()->prepare('INSERT INTO ca_login_attempts (ip, username, at) VALUES (?, ?, ?)')->execute([client_ip(), $tag, now()]);
}

function valid_username(string $u): void {
    if (!preg_match('/^[a-z0-9_]{3,30}$/', $u)) out(['error' => 'اسم المستخدم: 3 إلى 30 حرف إنجليزي صغير أو أرقام أو _'], 422);
}
function valid_password(string $p, int $min = 8): void {
    if (strlen($p) < $min || strlen($p) > 128) out(['error' => "كلمة المرور لازم تكون $min أحرف على الأقل"], 422);
}
function clean_name(mixed $n): ?string {
    $n = trim((string)($n ?? ''));
    return $n === '' ? null : mb_substr($n, 0, 60);
}

// ملخص تقدم المتدرب من بياناته المحفوظة
function curriculum_total(): int {
    static $n = null;
    if ($n !== null) return $n;
    $n = 0;
    $f = __DIR__ . '/../content/curriculum.json';
    if (is_file($f)) {
        foreach ((json_decode(file_get_contents($f), true)['levels'] ?? []) as $l)
            foreach ($l['modules'] as $m) if (!empty($m['ready'])) $n++;
    }
    return $n;
}
function lesson_level(string $id): ?int {
    return preg_match('/^m(\d+)\.\d+$/', $id, $m) ? (int)$m[1] : null;
}
function progress_summary(?array $data): array {
    $done = 0; $xp = 0; $scores = []; $levels = []; $current = null;
    foreach (($data['lessons'] ?? []) as $id => $l) {
        if (!is_array($l)) continue;
        if (!empty($l['done'])) {
            $done++; $xp += (int)($l['xp'] ?? 0); $scores[] = (int)($l['score'] ?? 0);
            $lv = lesson_level((string)$id);
            if ($lv !== null) $levels[$lv] = ($levels[$lv] ?? 0) + 1;
        } elseif ((int)($l['slide'] ?? 0) > 0) $current = (string)$id;
    }
    return [
        'done' => $done, 'xp' => $xp, 'avg_score' => $scores ? (int)round(array_sum($scores) / count($scores)) : null,
        'levels' => (object)$levels, 'in_progress' => $current,
        'streak' => (int)($data['streak'] ?? 0), 'last_day' => $data['lastDay'] ?? null,
    ];
}
function load_progress(int $uid): ?array {
    $st = db()->prepare('SELECT data FROM ca_progress WHERE user_id = ?');
    $st->execute([$uid]);
    $d = $st->fetchColumn();
    return $d ? json_decode($d, true) : null;
}
function save_progress(int $uid, array $data): void {
    $pdo = db();
    $json = json_encode($data, JSON_UNESCAPED_UNICODE);
    $st = $pdo->prepare('SELECT 1 FROM ca_progress WHERE user_id = ?');
    $st->execute([$uid]);
    if ($st->fetch()) $pdo->prepare('UPDATE ca_progress SET data = ?, updated_at = ? WHERE user_id = ?')->execute([$json, now(), $uid]);
    else $pdo->prepare('INSERT INTO ca_progress (user_id, data, updated_at) VALUES (?, ?, ?)')->execute([$uid, $json, now()]);
}

function find_user(int $id): array {
    $st = db()->prepare('SELECT ' . USER_COLS . ' FROM ca_users WHERE id = ?');
    $st->execute([$id]);
    return $st->fetch() ?: out(['error' => 'المستخدم غير موجود'], 404);
}
function admin_count(): int {
    return (int)db()->query("SELECT COUNT(*) FROM ca_users WHERE role = 'admin' AND status = 'active'")->fetchColumn();
}
function row_with_summary(array $u): array {
    return [
        'id' => (int)$u['id'], 'username' => $u['username'], 'name' => $u['full_name'], 'role' => $u['role'],
        'status' => $u['status'], 'note' => $u['stopped_note'],
        'max_level' => $u['max_level'] === null ? null : (int)$u['max_level'], 'free_access' => (int)$u['free_access'] === 1,
        'created_at' => $u['created_at'], 'last_seen' => $u['last_seen'],
    ] + progress_summary(load_progress((int)$u['id']));
}

/* ---------------- الإجراءات ---------------- */

try {
    switch ($action) {
        // معلومات عامة قبل تسجيل الدخول
        case 'public': {
            $cfg = require __DIR__ . '/config.php';
            $hasAdmin = (int)db()->query("SELECT COUNT(*) FROM ca_users WHERE role = 'admin'")->fetchColumn() > 0;
            out(['registration_open' => setting('registration_open', '1') === '1', 'setup_needed' => !$hasAdmin && !empty($cfg['setup_key'])]);
        }

        // إنشاء أول حساب مشرف: مرة وحدة، بمفتاح سري من ملف الإعدادات على السيرفر
        case 'setup_admin': {
            if ($method !== 'POST') out(['error' => 'method'], 405);
            $cfg = require __DIR__ . '/config.php';
            $pdo = db();
            if ((int)$pdo->query("SELECT COUNT(*) FROM ca_users WHERE role = 'admin'")->fetchColumn() > 0) out(['error' => 'يوجد مشرف مسبقاً'], 409);
            if (empty($cfg['setup_key'])) out(['error' => 'الإعداد غير مفعّل: أضف setup_key في config.php'], 403);
            throttle('#setup', 5);
            if (!hash_equals((string)$cfg['setup_key'], (string)($body['setup_key'] ?? ''))) {
                note_attempt('#setup');
                out(['error' => 'مفتاح الإعداد غير صحيح'], 403);
            }
            $u = strtolower(trim((string)($body['username'] ?? '')));
            $p = (string)($body['password'] ?? '');
            valid_username($u);
            valid_password($p, 12);
            $st = $pdo->prepare('SELECT 1 FROM ca_users WHERE username = ?');
            $st->execute([$u]);
            if ($st->fetch()) out(['error' => 'اسم المستخدم محجوز'], 409);
            $pdo->prepare("INSERT INTO ca_users (username, pass_hash, created_at, role) VALUES (?, ?, ?, 'admin')")
                ->execute([$u, password_hash($p, PASSWORD_DEFAULT), now()]);
            $uid = (int)$pdo->lastInsertId();
            audit($uid, $uid, 'setup_admin');
            session_regenerate_id(true);
            $_SESSION['uid'] = $uid;
            out(['user' => public_user(current_user())], 201);
        }

        case 'register': {
            if ($method !== 'POST') out(['error' => 'method'], 405);
            if (setting('registration_open', '1') !== '1') out(['error' => 'التسجيل مغلق، تواصل مع المشرف لإنشاء حسابك'], 403);
            $u = strtolower(trim((string)($body['username'] ?? '')));
            $p = (string)($body['password'] ?? '');
            valid_username($u);
            valid_password($p);
            $pdo = db();
            throttle('#reg', 5); // حد للتسجيل من نفس العنوان: 5 كل 15 دقيقة
            $st = $pdo->prepare('SELECT 1 FROM ca_users WHERE username = ?');
            $st->execute([$u]);
            if ($st->fetch()) out(['error' => 'اسم المستخدم محجوز، جرّب غيره'], 409);
            $pdo->prepare('INSERT INTO ca_users (username, pass_hash, created_at) VALUES (?, ?, ?)')
                ->execute([$u, password_hash($p, PASSWORD_DEFAULT), now()]);
            note_attempt('#reg');
            session_regenerate_id(true);
            $_SESSION['uid'] = (int)$pdo->lastInsertId();
            out(['user' => public_user(current_user())], 201);
        }

        case 'login': {
            if ($method !== 'POST') out(['error' => 'method'], 405);
            $u = strtolower(trim((string)($body['username'] ?? '')));
            $p = (string)($body['password'] ?? '');
            $ip = client_ip();
            $pdo = db();
            // تحديد المحاولات الفاشلة (ضد تخمين الباسورد) خلال آخر 15 دقيقة
            $pdo->prepare('DELETE FROM ca_login_attempts WHERE at < ?')->execute([gmdate('Y-m-d H:i:s', time() - 900)]);
            $c = $pdo->prepare("SELECT COUNT(*) FROM ca_login_attempts WHERE (username = ? OR ip = ?) AND username NOT LIKE '#%'");
            $c->execute([$u, $ip]);
            if ((int)$c->fetchColumn() >= 10) out(['error' => 'محاولات كثيرة، انتظر 15 دقيقة وجرّب'], 429);
            $st = $pdo->prepare('SELECT id, pass_hash FROM ca_users WHERE username = ?');
            $st->execute([$u]);
            $row = $st->fetch();
            // نتحقق دايماً (حتى لو المستخدم غير موجود) عشان ما يتسرّب وجود الاسم من زمن الرد
            $hash = $row['pass_hash'] ?? '$2y$10$usesomesillystringforsaltuQ1iM6e6x4yB7q8v0mV7QWj7e6r9a';
            if (!$row || !password_verify($p, $hash)) {
                note_attempt($u);
                out(['error' => 'اسم المستخدم أو كلمة المرور غير صحيحة'], 401);
            }
            if (password_needs_rehash($row['pass_hash'], PASSWORD_DEFAULT)) {
                $pdo->prepare('UPDATE ca_users SET pass_hash = ? WHERE id = ?')->execute([password_hash($p, PASSWORD_DEFAULT), $row['id']]);
            }
            session_regenerate_id(true);
            $_SESSION['uid'] = (int)$row['id'];
            touch_seen((int)$row['id']);
            out(['user' => public_user(current_user())]);
        }

        case 'logout': {
            if ($method !== 'POST') out(['error' => 'method'], 405);
            $_SESSION = [];
            session_destroy();
            out(['ok' => true]);
        }

        case 'me': {
            $user = current_user();
            if ($user) touch_seen((int)$user['id']);
            out(['user' => $user ? public_user($user) : null]);
        }

        case 'progress': {
            $user = require_user();
            $uid = (int)$user['id'];
            if ($method === 'GET') out(['progress' => load_progress($uid)]);
            // المتدرب الموقوف ما يقدر يحفظ تقدماً جديداً
            if ($user['status'] !== 'active') out(['error' => 'تدريبك موقوف', 'suspended' => true], 403);
            $data = $body['progress'] ?? null;
            if (!is_array($data) || strlen((string)json_encode($data)) > 65536) out(['error' => 'بيانات غير صالحة'], 422);
            // إذا المشرف حدد سقفاً للمستوى نحذف أي درس فوقه من التقدم المحفوظ
            if ($user['max_level'] !== null && isset($data['lessons']) && is_array($data['lessons'])) {
                foreach (array_keys($data['lessons']) as $id) {
                    $lv = lesson_level((string)$id);
                    if ($lv !== null && $lv > (int)$user['max_level']) unset($data['lessons'][$id]);
                }
            }
            save_progress($uid, $data);
            touch_seen($uid);
            out(['ok' => true]);
        }

        /* ===== لوحة المشرف ===== */

        case 'admin_list': {
            require_admin();
            $rows = db()->query('SELECT ' . USER_COLS . ' FROM ca_users ORDER BY id')->fetchAll();
            $users = array_map('row_with_summary', $rows);
            out(['users' => $users, 'total_lessons' => curriculum_total(), 'registration_open' => setting('registration_open', '1') === '1']);
        }

        case 'admin_user': {
            require_admin();
            $u = find_user((int)($_GET['id'] ?? 0));
            $data = load_progress((int)$u['id']);
            $st = db()->prepare('SELECT a.action, a.detail, a.at, m.username AS admin FROM ca_audit a LEFT JOIN ca_users m ON m.id = a.admin_id WHERE a.user_id = ? ORDER BY a.id DESC LIMIT 20');
            $st->execute([$u['id']]);
            out(['user' => row_with_summary($u), 'lessons' => (object)($data['lessons'] ?? []), 'review_count' => count($data['review'] ?? []),
                 'total_lessons' => curriculum_total(), 'audit' => $st->fetchAll()]);
        }

        case 'admin_update': {
            $admin = require_admin();
            if ($method !== 'POST') out(['error' => 'method'], 405);
            $u = find_user((int)($body['id'] ?? 0));
            $self = (int)$u['id'] === (int)$admin['id'];
            $sets = []; $vals = []; $log = [];
            if (array_key_exists('status', $body)) {
                $s = (string)$body['status'];
                if (!in_array($s, ['active', 'suspended'], true)) out(['error' => 'حالة غير صالحة'], 422);
                if ($self && $s !== 'active') out(['error' => 'ما تقدر توقف حسابك أنت'], 422);
                $sets[] = 'status = ?'; $vals[] = $s; $log[] = "status=$s";
                if ($s === 'active') { $sets[] = 'stopped_note = NULL'; }
            }
            if (array_key_exists('note', $body)) {
                $n = trim((string)$body['note']);
                $sets[] = 'stopped_note = ?'; $vals[] = $n === '' ? null : mb_substr($n, 0, 200);
            }
            if (array_key_exists('max_level', $body)) {
                $m = $body['max_level'];
                if ($m !== null && (!is_int($m) || $m < 0 || $m > 20)) out(['error' => 'مستوى غير صالح'], 422);
                $sets[] = 'max_level = ?'; $vals[] = $m; $log[] = 'max_level=' . ($m === null ? 'none' : $m);
            }
            if (array_key_exists('free_access', $body)) {
                $sets[] = 'free_access = ?'; $vals[] = $body['free_access'] ? 1 : 0; $log[] = 'free_access=' . ($body['free_access'] ? 1 : 0);
            }
            if (array_key_exists('full_name', $body)) { $sets[] = 'full_name = ?'; $vals[] = clean_name($body['full_name']); }
            if (array_key_exists('role', $body)) {
                $r = (string)$body['role'];
                if (!in_array($r, ['trainee', 'admin'], true)) out(['error' => 'دور غير صالح'], 422);
                if ($r !== 'admin' && $u['role'] === 'admin' && ($self || admin_count() <= 1)) out(['error' => 'لازم يبقى مشرف واحد على الأقل'], 422);
                $sets[] = 'role = ?'; $vals[] = $r; $log[] = "role=$r";
            }
            if (!$sets) out(['error' => 'لا يوجد تغيير'], 422);
            $vals[] = $u['id'];
            db()->prepare('UPDATE ca_users SET ' . implode(', ', $sets) . ' WHERE id = ?')->execute($vals);
            audit((int)$admin['id'], (int)$u['id'], 'update', implode(' ', $log));
            out(['user' => row_with_summary(find_user((int)$u['id']))]);
        }

        case 'admin_password': {
            $admin = require_admin();
            if ($method !== 'POST') out(['error' => 'method'], 405);
            $u = find_user((int)($body['id'] ?? 0));
            $p = (string)($body['password'] ?? '');
            valid_password($p, $u['role'] === 'admin' ? 12 : 8);
            db()->prepare('UPDATE ca_users SET pass_hash = ? WHERE id = ?')->execute([password_hash($p, PASSWORD_DEFAULT), $u['id']]);
            audit((int)$admin['id'], (int)$u['id'], 'password_reset');
            out(['ok' => true]);
        }

        // تصفير التقدم: كامل، أو مستوى واحد. نرفع epoch عشان جهاز المتدرب ينسخ حالة السيرفر ولا يرجّع القديم.
        case 'admin_reset': {
            $admin = require_admin();
            if ($method !== 'POST') out(['error' => 'method'], 405);
            $u = find_user((int)($body['id'] ?? 0));
            $level = $body['level'] ?? null;
            if ($level !== null && (!is_int($level) || $level < 0 || $level > 20)) out(['error' => 'مستوى غير صالح'], 422);
            $data = load_progress((int)$u['id']) ?? [];
            if ($level === null) {
                $data = ['streak' => 0, 'lastDay' => null, 'lessons' => new stdClass(), 'review' => new stdClass()];
            } else {
                foreach (array_keys($data['lessons'] ?? []) as $id) if (lesson_level((string)$id) === $level) unset($data['lessons'][$id]);
                if (empty($data['lessons'])) $data['lessons'] = new stdClass();
            }
            save_progress((int)$u['id'], json_decode(json_encode($data), true));
            db()->prepare('UPDATE ca_users SET reset_epoch = reset_epoch + 1 WHERE id = ?')->execute([$u['id']]);
            audit((int)$admin['id'], (int)$u['id'], 'reset', $level === null ? 'all' : "level $level");
            out(['user' => row_with_summary(find_user((int)$u['id']))]);
        }

        case 'admin_create': {
            $admin = require_admin();
            if ($method !== 'POST') out(['error' => 'method'], 405);
            $u = strtolower(trim((string)($body['username'] ?? '')));
            $p = (string)($body['password'] ?? '');
            valid_username($u);
            valid_password($p);
            $pdo = db();
            $st = $pdo->prepare('SELECT 1 FROM ca_users WHERE username = ?');
            $st->execute([$u]);
            if ($st->fetch()) out(['error' => 'اسم المستخدم محجوز'], 409);
            $pdo->prepare('INSERT INTO ca_users (username, pass_hash, created_at, full_name) VALUES (?, ?, ?, ?)')
                ->execute([$u, password_hash($p, PASSWORD_DEFAULT), now(), clean_name($body['full_name'] ?? null)]);
            $id = (int)$pdo->lastInsertId();
            audit((int)$admin['id'], $id, 'create', $u);
            out(['user' => row_with_summary(find_user($id))], 201);
        }

        case 'admin_delete': {
            $admin = require_admin();
            if ($method !== 'POST') out(['error' => 'method'], 405);
            $u = find_user((int)($body['id'] ?? 0));
            if ((int)$u['id'] === (int)$admin['id']) out(['error' => 'ما تقدر تحذف حسابك أنت'], 422);
            if ($u['role'] === 'admin' && admin_count() <= 1) out(['error' => 'لازم يبقى مشرف واحد على الأقل'], 422);
            db()->prepare('DELETE FROM ca_progress WHERE user_id = ?')->execute([$u['id']]);
            db()->prepare('DELETE FROM ca_users WHERE id = ?')->execute([$u['id']]);
            audit((int)$admin['id'], null, 'delete', $u['username']);
            out(['ok' => true]);
        }

        case 'admin_settings': {
            $admin = require_admin();
            if ($method !== 'POST') out(['error' => 'method'], 405);
            if (array_key_exists('registration_open', $body)) {
                set_setting('registration_open', $body['registration_open'] ? '1' : '0');
                audit((int)$admin['id'], null, 'registration', $body['registration_open'] ? 'open' : 'closed');
            }
            out(['registration_open' => setting('registration_open', '1') === '1']);
        }

        // إيقاف/استئناف عدة متدربين مرة وحدة (أو كل المتدربين)
        case 'admin_bulk': {
            $admin = require_admin();
            if ($method !== 'POST') out(['error' => 'method'], 405);
            $s = (string)($body['status'] ?? '');
            if (!in_array($s, ['active', 'suspended'], true)) out(['error' => 'حالة غير صالحة'], 422);
            $note = trim((string)($body['note'] ?? ''));
            $pdo = db();
            $ids = $body['ids'] ?? 'all';
            if ($ids === 'all') {
                $targets = $pdo->query("SELECT id FROM ca_users WHERE role = 'trainee'")->fetchAll(PDO::FETCH_COLUMN);
            } else {
                $targets = array_values(array_filter(array_map('intval', (array)$ids), fn($i) => $i > 0 && $i !== (int)$admin['id']));
            }
            $n = 0;
            $up = $pdo->prepare("UPDATE ca_users SET status = ?, stopped_note = ? WHERE id = ? AND role = 'trainee'");
            foreach ($targets as $t) { $up->execute([$s, $s === 'suspended' && $note !== '' ? mb_substr($note, 0, 200) : null, $t]); $n += $up->rowCount(); }
            audit((int)$admin['id'], null, 'bulk', "$s x$n");
            out(['changed' => $n]);
        }

        default:
            out(['error' => 'not found'], 404);
    }
} catch (Throwable $e) {
    error_log('cyber-academy api: ' . $e->getMessage());
    out(['error' => 'خطأ في السيرفر'], 500);
}
