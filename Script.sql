

CREATE TABLE IF NOT EXISTS users (
    id BIGINT NOT NULL AUTO_INCREMENT,
    username VARCHAR(50) NOT NULL,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(100) NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    last_login_at DATETIME NULL,

    PRIMARY KEY (id),
    UNIQUE KEY uq_users_username (username),
    UNIQUE KEY uq_users_email (email),
    KEY idx_users_active (is_active)
) ENGINE=InnoDB;



CREATE TABLE IF NOT EXISTS stocks (
    id BIGINT  NOT NULL AUTO_INCREMENT,
    symbol VARCHAR(20) NOT NULL,
    name VARCHAR(255) NOT NULL,
    exchange VARCHAR(10) NOT NULL,
    industry VARCHAR(150) NULL,
    company_type VARCHAR(50) NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    listed_date DATE NULL,
    website VARCHAR(255) NULL,
    description TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_stocks_symbol (symbol),
    KEY idx_stocks_exchange (exchange),
    KEY idx_stocks_industry (industry),
    KEY idx_stocks_status (status)
) ENGINE=InnoDB;



CREATE TABLE IF NOT EXISTS stock_prices (
    id BIGINT  NOT NULL AUTO_INCREMENT,
    stock_id BIGINT  NOT NULL,
    trading_date DATE NOT NULL,
    open_price DECIMAL(18,4) NULL,
    high_price DECIMAL(18,4) NULL,
    low_price DECIMAL(18,4) NULL,
    close_price DECIMAL(18,4) NOT NULL,
    adjusted_close DECIMAL(18,4) NULL,
    volume BIGINT  NOT NULL DEFAULT 0,
    value_traded DECIMAL(22,2) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_stock_prices_stock_date
        (stock_id, trading_date),
    KEY idx_stock_prices_date (trading_date),

    CONSTRAINT fk_stock_prices_stock
        FOREIGN KEY (stock_id) REFERENCES stocks(id)
        ON DELETE CASCADE
) ENGINE=InnoDB;

-- =====================================================
-- 4. STOCK_FINANCIALS - Chỉ số tài chính theo kỳ
-- =====================================================

CREATE TABLE IF NOT EXISTS stock_financials (
    id BIGINT NOT NULL AUTO_INCREMENT,
    stock_id BIGINT NOT NULL,
    period_end DATE NOT NULL,
    period_type VARCHAR(10) NOT NULL,
    revenue DECIMAL(22,2) NULL,
    operating_profit DECIMAL(22,2) NULL,
    net_profit DECIMAL(22,2) NULL,
    equity DECIMAL(22,2) NULL,
    total_assets DECIMAL(22,2) NULL,
    total_debt DECIMAL(22,2) NULL,
    eps DECIMAL(18,4) NULL,
    roe DECIMAL(10,4) NULL,
    roa DECIMAL(10,4) NULL,
    roic DECIMAL(10,4) NULL,
    source VARCHAR(50) NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_stock_financials_period
        (stock_id, period_end, period_type),
    KEY idx_stock_financials_period (period_end),

    CONSTRAINT fk_stock_financials_stock
        FOREIGN KEY (stock_id) REFERENCES stocks(id)
        ON DELETE CASCADE
) ENGINE=InnoDB;

-- =====================================================
-- 5. STOCK_VALUATIONS - Chỉ số định giá
-- =====================================================

CREATE TABLE IF NOT EXISTS stock_valuations (
    id BIGINT  NOT NULL AUTO_INCREMENT,
    stock_id BIGINT  NOT NULL,
    valuation_date DATE NOT NULL,
    market_cap DECIMAL(22,2) NULL,
    pe DECIMAL(12,4) NULL,
    pb DECIMAL(12,4) NULL,
    ps DECIMAL(12,4) NULL,
    source VARCHAR(50) NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_stock_valuations_date
        (stock_id, valuation_date),
    KEY idx_stock_valuations_date (valuation_date),

    CONSTRAINT fk_stock_valuations_stock
        FOREIGN KEY (stock_id) REFERENCES stocks(id)
        ON DELETE CASCADE
) ENGINE=InnoDB;

-- =====================================================
-- 6. SYNC_LOGS - Nhật ký đồng bộ dữ liệu
-- =====================================================

CREATE TABLE IF NOT EXISTS sync_logs (
    id BIGINT  NOT NULL AUTO_INCREMENT,
    task_name VARCHAR(100) NOT NULL,
    symbol VARCHAR(20) NULL,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'running',
    records_affected INT NOT NULL DEFAULT 0,
    error_message TEXT NULL,

    PRIMARY KEY (id),
    KEY idx_sync_logs_task (task_name),
    KEY idx_sync_logs_status (status),
    KEY idx_sync_logs_started (started_at)
) ENGINE=InnoDB;


CREATE TABLE stockmate.watchlists (
    id BIGINT NOT NULL AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    stock_id BIGINT NOT NULL,
    note VARCHAR(255) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_watchlist_user_stock (user_id, stock_id),
    KEY idx_watchlists_stock_id (stock_id),

    CONSTRAINT fk_watchlist_user
        FOREIGN KEY (user_id)
        REFERENCES stockmate.users(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_watchlist_stock
        FOREIGN KEY (stock_id)
        REFERENCES stockmate.stocks(id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;



CREATE TABLE stock_financial_reports (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    stock_id BIGINT NOT NULL,

    fiscal_year SMALLINT NOT NULL,
    fiscal_quarter TINYINT NULL,

    revenue DECIMAL(20, 2) NULL,
    gross_profit DECIMAL(20, 2) NULL,
    operating_profit DECIMAL(20, 2) NULL,
    net_profit DECIMAL(20, 2) NULL,

    eps DECIMAL(18, 4) NULL,
    total_assets DECIMAL(20, 2) NULL,
    total_liabilities DECIMAL(20, 2) NULL,
    equity DECIMAL(20, 2) NULL,

    source VARCHAR(50) NULL,
    report_date DATE NULL,
    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uq_stock_period (
        stock_id, fiscal_year, fiscal_quarter
    ),

    KEY idx_fiscal_period (
        fiscal_year, fiscal_quarter
    )
);


ALTER TABLE stock_valuations
ADD COLUMN period_type VARCHAR(10) NOT NULL DEFAULT 'quarter',
ADD COLUMN fiscal_quarter TINYINT(4) DEFAULT NULL,
ADD COLUMN fiscal_quarter_txt VARCHAR(100) DEFAULT NULL;

ALTER TABLE stocks
    ADD COLUMN reference_price DECIMAL(15, 2) NULL,
    ADD COLUMN current_price DECIMAL(15, 2) NULL,
    ADD COLUMN change_percent DECIMAL(10, 4) NULL;

ALTER TABLE stocks
    ADD COLUMN icb_code1 VARCHAR(10) NULL,
    ADD COLUMN icb_name1 VARCHAR(150) NULL,
    ADD COLUMN icb_code2 VARCHAR(10) NULL,
    ADD COLUMN icb_name2 VARCHAR(150) NULL,
    ADD COLUMN icb_code3 VARCHAR(10) NULL,
    ADD COLUMN icb_name3 VARCHAR(150) NULL,
    ADD COLUMN icb_code4 VARCHAR(10) NULL,
    ADD COLUMN icb_name4 VARCHAR(150) NULL;
