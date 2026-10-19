<?php
// واجهة الحسابات والمزامنة. كل الطلبات: POST/GET على index.php?a=<action> وترجع JSON.
//   register / login / logout / me / progress (GET يجيب، POST يحفظ)
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

function current_user(): ?array {
    if (empty($_SESSION['uid'])) return null;
    $st = db()->prepare('SELECT id, username FROM ca_users WHERE id = ?');
    $st->execute([$_SESSION['uid']]);
    return $st->fetch() ?: null;
}
function require_user(): array {
    return current_user() ?? out(['error' => 'سجّل دخولك أول'], 401);
}
function now(): string { return gmdate('Y-m-d H:i:s'); }

try {
    switch ($action) {
        case 'register': {
            if ($method !== 'POST') out(['error' => 'method'], 405);
            $u = strtolower(trim((string)($body['username'] ?? '')));
            $p = (string)($body['password'] ?? '');
            if (!preg_match('/^[a-z0-9_]{3,30}$/', $u)) out(['error' => 'اسم المستخدم: 3 إلى 30 حرف إنجليزي صغير أو أرقام أو _'], 422);
            if (strlen($p) < 8 || strlen($p) > 128) out(['error' => 'كلمة المرور لازم تكون 8 أحرف على الأقل'], 422);
            $pdo = db();
            $st = $pdo->prepare('SELECT 1 FROM ca_users WHERE username = ?');
            $st->execute([$u]);
            if ($st->fetch()) out(['error' => 'اسم المستخدم محجوز، جرّب غيره'], 409);
            $pdo->prepare('INSERT INTO ca_users (username, pass_hash, created_at) VALUES (?, ?, ?)')
                ->execute([$u, password_hash($p, PASSWORD_DEFAULT), now()]);
            session_regenerate_id(true);
            $_SESSION['uid'] = (int)$pdo->lastInsertId();
            out(['user' => ['username' => $u]], 201);
        }

        case 'login': {
            if ($method !== 'POST') out(['error' => 'method'], 405);
            $u = strtolower(trim((string)($body['username'] ?? '')));
            $p = (string)($body['password'] ?? '');
            $ip = substr($_SERVER['REMOTE_ADDR'] ?? '', 0, 45);
            $pdo = db();
            // تحديد المحاولات الفاشلة (ضد تخمين الباسورد) خلال آخر 15 دقيقة
            $since = gmdate('Y-m-d H:i:s', time() - 900);
            $pdo->prepare('DELETE FROM ca_login_attempts WHERE at < ?')->execute([$since]);
            $c = $pdo->prepare('SELECT COUNT(*) FROM ca_login_attempts WHERE username = ? OR ip = ?');
            $c->execute([$u, $ip]);
            if ((int)$c->fetchColumn() >= 10) out(['error' => 'محاولات كثيرة، انتظر 15 دقيقة وجرّب'], 429);
            $st = $pdo->prepare('SELECT id, pass_hash FROM ca_users WHERE username = ?');
            $st->execute([$u]);
            $row = $st->fetch();
            // نتحقق دايماً (حتى لو المستخدم غير موجود) عشان ما يتسرّب وجود الاسم من زمن الرد
            $hash = $row['pass_hash'] ?? '$2y$10$usesomesillystringforsaltuQ1iM6e6x4yB7q8v0mV7QWj7e6r9a';
            if (!$row || !password_verify($p, $hash)) {
                $pdo->prepare('INSERT INTO ca_login_attempts (ip, username, at) VALUES (?, ?, ?)')->execute([$ip, $u, now()]);
                out(['error' => 'اسم المستخدم أو كلمة المرور غير صحيحة'], 401);
            }
            if (password_needs_rehash($row['pass_hash'], PASSWORD_DEFAULT)) {
                $pdo->prepare('UPDATE ca_users SET pass_hash = ? WHERE id = ?')->execute([password_hash($p, PASSWORD_DEFAULT), $row['id']]);
            }
            session_regenerate_id(true);
            $_SESSION['uid'] = (int)$row['id'];
            out(['user' => ['username' => $u]]);
        }

        case 'logout': {
            if ($method !== 'POST') out(['error' => 'method'], 405);
            $_SESSION = [];
            session_destroy();
            out(['ok' => true]);
        }

        case 'me': {
            $user = current_user();
            out(['user' => $user ? ['username' => $user['username']] : null]);
        }

        case 'progress': {
            $user = require_user();
            $pdo = db();
            if ($method === 'GET') {
                $st = $pdo->prepare('SELECT data FROM ca_progress WHERE user_id = ?');
                $st->execute([$user['id']]);
                $d = $st->fetchColumn();
                out(['progress' => $d ? json_decode($d, true) : null]);
            }
            $data = $body['progress'] ?? null;
            $json = json_encode($data, JSON_UNESCAPED_UNICODE);
            if (!is_array($data) || strlen($json) > 65536) out(['error' => 'بيانات غير صالحة'], 422);
            $st = $pdo->prepare('SELECT 1 FROM ca_progress WHERE user_id = ?');
            $st->execute([$user['id']]);
            if ($st->fetch()) {
                $pdo->prepare('UPDATE ca_progress SET data = ?, updated_at = ? WHERE user_id = ?')->execute([$json, now(), $user['id']]);
            } else {
                $pdo->prepare('INSERT INTO ca_progress (user_id, data, updated_at) VALUES (?, ?, ?)')->execute([$user['id'], $json, now()]);
            }
            out(['ok' => true]);
        }

        default:
            out(['error' => 'not found'], 404);
    }
} catch (Throwable $e) {
    error_log('cyber-academy api: ' . $e->getMessage());
    out(['error' => 'خطأ في السيرفر'], 500);
}
