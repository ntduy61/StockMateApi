-- stockmate.stocks definition

CREATE TABLE `stocks` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `symbol` varchar(20) NOT NULL,
  `name` varchar(255) NOT NULL,
  `exchange` varchar(10) NOT NULL,
  `industry` varchar(150) DEFAULT NULL,
  `company_type` varchar(50) DEFAULT NULL,
  `status` varchar(20) NOT NULL DEFAULT 'active',
  `listed_date` date DEFAULT NULL,
  `website` varchar(255) DEFAULT NULL,
  `description` text DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  `reference_price` decimal(15,2) DEFAULT NULL,
  `current_price` decimal(15,2) DEFAULT NULL,
  `change_percent` decimal(10,4) DEFAULT NULL,
  `icb_code1` varchar(10) DEFAULT NULL,
  `icb_name1` varchar(150) DEFAULT NULL,
  `icb_code2` varchar(10) DEFAULT NULL,
  `icb_name2` varchar(150) DEFAULT NULL,
  `icb_code3` varchar(10) DEFAULT NULL,
  `icb_name3` varchar(150) DEFAULT NULL,
  `icb_code4` varchar(10) DEFAULT NULL,
  `icb_name4` varchar(150) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_stocks_symbol` (`symbol`),
  KEY `idx_stocks_exchange` (`exchange`),
  KEY `idx_stocks_industry` (`industry`),
  KEY `idx_stocks_status` (`status`)
) ENGINE=InnoDB AUTO_INCREMENT=2804 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- stockmate.sync_logs definition

CREATE TABLE `sync_logs` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `task_name` varchar(100) NOT NULL,
  `symbol` varchar(20) DEFAULT NULL,
  `started_at` datetime NOT NULL DEFAULT current_timestamp(),
  `finished_at` datetime DEFAULT NULL,
  `status` varchar(20) NOT NULL DEFAULT 'running',
  `records_affected` int(11) NOT NULL DEFAULT 0,
  `error_message` text DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_sync_logs_task` (`task_name`),
  KEY `idx_sync_logs_status` (`status`),
  KEY `idx_sync_logs_started` (`started_at`)
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- stockmate.users definition

CREATE TABLE `users` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `username` varchar(50) NOT NULL,
  `email` VARCHAR(191) NOT NULL,
  `password_hash` varchar(255) NOT NULL,
  `full_name` varchar(100) DEFAULT NULL,
  `role` varchar(20) NOT NULL DEFAULT 'user',
  `is_active` tinyint(1) NOT NULL DEFAULT 1,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  `last_login_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_users_username` (`username`),
  UNIQUE KEY `uq_users_email` (`email`),
  KEY `idx_users_active` (`is_active`)
) ENGINE=InnoDB AUTO_INCREMENT=1 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- stockmate.stock_financials definition

CREATE TABLE `stock_financials` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `stock_id` bigint(20) NOT NULL,
  `period_end` date NOT NULL,
  `period_type` varchar(10) NOT NULL,
  `fiscal_quarter` tinyint(4) DEFAULT NULL,
  `fiscal_quarter_txt` varchar(100) DEFAULT NULL,
  `revenue` decimal(22,2) DEFAULT NULL,
  `operating_profit` decimal(22,2) DEFAULT NULL,
  `net_profit` decimal(22,2) DEFAULT NULL,
  `equity` decimal(22,2) DEFAULT NULL,
  `total_assets` decimal(22,2) DEFAULT NULL,
  `total_debt` decimal(22,2) DEFAULT NULL,
  `eps` decimal(18,4) DEFAULT NULL,
  `roe` decimal(10,4) DEFAULT NULL,
  `roa` decimal(10,4) DEFAULT NULL,
  `roic` decimal(10,4) DEFAULT NULL,
  `source` varchar(50) DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_stock_financials_period` (`stock_id`,`period_end`,`period_type`),
  KEY `idx_stock_financials_period` (`period_end`),
  CONSTRAINT `fk_stock_financials_stock` FOREIGN KEY (`stock_id`) REFERENCES `stocks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=718 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- stockmate.stock_prices definition

CREATE TABLE `stock_prices` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `stock_id` bigint(20) NOT NULL,
  `trading_date` date NOT NULL,
  `open_price` decimal(18,4) DEFAULT NULL,
  `high_price` decimal(18,4) DEFAULT NULL,
  `low_price` decimal(18,4) DEFAULT NULL,
  `close_price` decimal(18,4) NOT NULL,
  `adjusted_close` decimal(18,4) DEFAULT NULL,
  `volume` bigint(20) NOT NULL DEFAULT 0,
  `value_traded` decimal(22,2) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_stock_prices_stock_date` (`stock_id`,`trading_date`),
  KEY `idx_stock_prices_date` (`trading_date`),
  CONSTRAINT `fk_stock_prices_stock` FOREIGN KEY (`stock_id`) REFERENCES `stocks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=197 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- stockmate.stock_valuations definition

CREATE TABLE `stock_valuations` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `stock_id` bigint(20) NOT NULL,
  `valuation_date` date NOT NULL,
  `period_type` varchar(10) NOT NULL DEFAULT 'quarter',
  `fiscal_quarter` tinyint(4) DEFAULT NULL,
  `fiscal_quarter_txt` varchar(100) DEFAULT NULL,
  `market_cap` decimal(22,2) DEFAULT NULL,
  `pe` decimal(12,4) DEFAULT NULL,
  `pb` decimal(12,4) DEFAULT NULL,
  `ps` decimal(12,4) DEFAULT NULL,
  `source` varchar(50) DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_stock_valuations_date` (`stock_id`,`valuation_date`),
  KEY `idx_stock_valuations_date` (`valuation_date`),
  CONSTRAINT `fk_stock_valuations_stock` FOREIGN KEY (`stock_id`) REFERENCES `stocks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=718 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- stockmate.watchlists definition

CREATE TABLE `watchlists` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `user_id` bigint(20) NOT NULL,
  `stock_id` bigint(20) NOT NULL,
  `note` varchar(255) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_watchlist_user_stock` (`user_id`,`stock_id`),
  KEY `idx_watchlists_stock_id` (`stock_id`),
  CONSTRAINT `fk_watchlist_stock` FOREIGN KEY (`stock_id`) REFERENCES `stocks` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_watchlist_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=1 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;