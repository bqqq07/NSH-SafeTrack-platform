-- HSE Phase 2 Migration
-- Run this in phpMyAdmin on database zappimiw_nsh

CREATE TABLE IF NOT EXISTS `hse_ptw` (
  `id`             INT NOT NULL AUTO_INCREMENT,
  `officer_id`     INT NOT NULL,
  `company_id`     INT NULL,
  `permit_number`  VARCHAR(50) NOT NULL,
  `permit_type`    VARCHAR(50) NOT NULL,
  `description`    TEXT,
  `location`       VARCHAR(255),
  `week_start`     DATE NOT NULL,
  `week_end`       DATE NOT NULL,
  `status`         ENUM('active','suspended','closed') NOT NULL DEFAULT 'active',
  `attached_to_id` INT NULL,
  `created_at`     DATETIME DEFAULT NOW(),
  PRIMARY KEY (`id`),
  KEY `idx_ptw_officer` (`officer_id`),
  KEY `idx_ptw_company` (`company_id`),
  CONSTRAINT `fk_ptw_officer` FOREIGN KEY (`officer_id`) REFERENCES `user` (`id`),
  CONSTRAINT `fk_ptw_company` FOREIGN KEY (`company_id`) REFERENCES `company` (`id`),
  CONSTRAINT `fk_ptw_parent`  FOREIGN KEY (`attached_to_id`) REFERENCES `hse_ptw` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE IF NOT EXISTS `hse_manpower` (
  `id`          INT NOT NULL AUTO_INCREMENT,
  `officer_id`  INT NOT NULL,
  `company_id`  INT NULL,
  `date`        DATE NOT NULL,
  `location`    VARCHAR(255),
  `total_count` INT NOT NULL,
  `breakdown`   TEXT,
  `notes`       TEXT,
  `created_at`  DATETIME DEFAULT NOW(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_hse_manpower_od` (`officer_id`, `date`),
  CONSTRAINT `fk_mp_officer` FOREIGN KEY (`officer_id`) REFERENCES `user` (`id`),
  CONSTRAINT `fk_mp_company` FOREIGN KEY (`company_id`) REFERENCES `company` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE IF NOT EXISTS `hse_corrective_action` (
  `id`               INT NOT NULL AUTO_INCREMENT,
  `observation_id`   INT NOT NULL,
  `company_id`       INT NULL,
  `assigned_to`      VARCHAR(255),
  `due_date`         DATE NOT NULL,
  `action_required`  TEXT NOT NULL,
  `status`           ENUM('open','in_progress','completed') NOT NULL DEFAULT 'open',
  `completed_at`     DATE NULL,
  `completion_notes` TEXT,
  `created_by`       INT NOT NULL,
  `created_at`       DATETIME DEFAULT NOW(),
  PRIMARY KEY (`id`),
  KEY `idx_ca_obs`  (`observation_id`),
  KEY `idx_ca_co`   (`company_id`),
  CONSTRAINT `fk_ca_obs`    FOREIGN KEY (`observation_id`) REFERENCES `hse_observation` (`id`),
  CONSTRAINT `fk_ca_co`     FOREIGN KEY (`company_id`)     REFERENCES `company` (`id`),
  CONSTRAINT `fk_ca_creator` FOREIGN KEY (`created_by`)    REFERENCES `user` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE IF NOT EXISTS `hse_inspection` (
  `id`            INT NOT NULL AUTO_INCREMENT,
  `officer_id`    INT NOT NULL,
  `company_id`    INT NULL,
  `date`          DATE NOT NULL,
  `location`      VARCHAR(255),
  `checklist`     TEXT NOT NULL,
  `overall_score` FLOAT,
  `notes`         TEXT,
  `created_at`    DATETIME DEFAULT NOW(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_hse_inspection_od` (`officer_id`, `date`),
  CONSTRAINT `fk_insp_officer` FOREIGN KEY (`officer_id`) REFERENCES `user` (`id`),
  CONSTRAINT `fk_insp_company` FOREIGN KEY (`company_id`) REFERENCES `company` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
