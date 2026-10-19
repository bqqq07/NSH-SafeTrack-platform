<?php
// اتصال قاعدة البيانات (PDO) + إنشاء الجداول تلقائياً أول مرة.
declare(strict_types=1);

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
}
