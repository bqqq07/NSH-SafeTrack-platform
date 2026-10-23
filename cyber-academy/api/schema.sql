-- للمرجع فقط: الجداول تنشأ وتترقّى تلقائياً أول طلب (api/db.php). الحسابات القديمة تبقى وتصير متدربين.
CREATE TABLE IF NOT EXISTS ca_users (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(30) NOT NULL UNIQUE,
  pass_hash VARCHAR(255) NOT NULL,
  created_at DATETIME NOT NULL,
  role VARCHAR(10) NOT NULL DEFAULT 'trainee',      -- trainee | admin
  status VARCHAR(10) NOT NULL DEFAULT 'active',     -- active | suspended
  full_name VARCHAR(60) NULL,
  max_level INT NULL,                               -- سقف أقصى مستوى (NULL = بدون حد)
  free_access INT NOT NULL DEFAULT 0,               -- 1 = إلغاء القفل المتسلسل
  reset_epoch INT NOT NULL DEFAULT 0,               -- يزيد عند تصفير المشرف للتقدم
  last_seen DATETIME NULL,
  stopped_note VARCHAR(200) NULL                    -- رسالة الإيقاف للمتدرب
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS ca_progress (
  user_id INT UNSIGNED NOT NULL PRIMARY KEY,
  data TEXT NOT NULL,
  updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS ca_login_attempts (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  ip VARCHAR(45) NOT NULL,
  username VARCHAR(30) NOT NULL,
  at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS ca_settings (k VARCHAR(40) NOT NULL PRIMARY KEY, v VARCHAR(200) NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS ca_audit (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  admin_id INT NOT NULL, user_id INT NULL, action VARCHAR(40) NOT NULL, detail VARCHAR(200) NULL, at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
