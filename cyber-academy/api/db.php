<?php
// اتصال قاعدة البيانات (PDO) + إنشاء/ترقية الجداول تلقائياً.
declare(strict_types=1);

const SCHEMA_VERSION = '2';

function db(): PDO {
    static $pdo = null;
    if ($pdo) return $pdo;
    $cfg = require __DIR__ . '/config.php';
    $pdo = new PDO($cfg['dsn'], $cfg['user'] ?? null, $cfg['pass'] ?? null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);
    ensure_schema($pdo);
    return $pdo;
}

function ensure_schema(PDO $pdo): void {
    $sqlite = $pdo->getAttribute(PDO::ATTR_DRIVER_NAME) === 'sqlite';
    $id = $sqlite ? 'INTEGER PRIMARY KEY AUTOINCREMENT' : 'INT UNSIGNED AUTO_INCREMENT PRIMARY KEY';
    $opts = $sqlite ? '' : ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4';

    $pdo->exec("CREATE TABLE IF NOT EXISTS ca_settings (
        k VARCHAR(40) NOT NULL PRIMARY KEY,
        v VARCHAR(200) NOT NULL
    )$opts");

    // إذا الجداول بالنسخة الحالية ما نكرر الفحص في كل طلب
    $st = $pdo->prepare("SELECT v FROM ca_settings WHERE k = 'schema_v'");
    $st->execute();
    if ($st->fetchColumn() === SCHEMA_VERSION) return;

    $pdo->exec("CREATE TABLE IF NOT EXISTS ca_users (
        id $id,
        username VARCHAR(30) NOT NULL UNIQUE,
        pass_hash VARCHAR(255) NOT NULL,
        created_at DATETIME NOT NULL
    )$opts");
    $pdo->exec("CREATE TABLE IF NOT EXISTS ca_progress (
        user_id INT UNSIGNED NOT NULL PRIMARY KEY,
        data TEXT NOT NULL,
        updated_at DATETIME NOT NULL
    )$opts");
    $pdo->exec("CREATE TABLE IF NOT EXISTS ca_login_attempts (
        id $id,
        ip VARCHAR(45) NOT NULL,
        username VARCHAR(30) NOT NULL,
        at DATETIME NOT NULL
    )$opts");
    $pdo->exec("CREATE TABLE IF NOT EXISTS ca_audit (
        id $id,
        admin_id INT NOT NULL,
        user_id INT NULL,
        action VARCHAR(40) NOT NULL,
        detail VARCHAR(200) NULL,
        at DATETIME NOT NULL
    )$opts");

    // ترقية جدول المستخدمين (الحسابات الموجودة تبقى كما هي وتصير متدربين)
    $cols = [
        'role'         => "VARCHAR(10) NOT NULL DEFAULT 'trainee'",
        'status'       => "VARCHAR(10) NOT NULL DEFAULT 'active'",
        'full_name'    => 'VARCHAR(60) NULL',
        'max_level'    => 'INT NULL',
        'free_access'  => 'INT NOT NULL DEFAULT 0',
        'reset_epoch'  => 'INT NOT NULL DEFAULT 0',
        'last_seen'    => 'DATETIME NULL',
        'stopped_note' => 'VARCHAR(200) NULL',
    ];
    foreach ($cols as $name => $ddl) {
        try {
            $pdo->query("SELECT $name FROM ca_users LIMIT 1");
        } catch (Throwable) {
            $pdo->exec("ALTER TABLE ca_users ADD COLUMN $name $ddl");
        }
    }

    $up = $pdo->prepare("UPDATE ca_settings SET v = ? WHERE k = 'schema_v'");
    $up->execute([SCHEMA_VERSION]);
    if ($up->rowCount() === 0) {
        $pdo->prepare("INSERT INTO ca_settings (k, v) VALUES ('schema_v', ?)")->execute([SCHEMA_VERSION]);
    }
}
