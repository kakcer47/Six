-- ===========================================
-- Инициализация базы данных для Telegram Bot
-- Создание таблиц, индексов и триггеров
-- ===========================================

-- Включаем расширения PostgreSQL
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

-- ===========================================
-- ОСНОВНЫЕ ТАБЛИЦЫ
-- ===========================================

-- Таблица объявлений пользователей
CREATE TABLE IF NOT EXISTS user_ads (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL UNIQUE,
    message_url TEXT NOT NULL,
    topic_name TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Таблица заблокированных пользователей
CREATE TABLE IF NOT EXISTS banned_users (
    user_id BIGINT PRIMARY KEY,
    reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    banned_by BIGINT
);

-- Таблица лимитов пользователей
CREATE TABLE IF NOT EXISTS user_limits (
    user_id BIGINT PRIMARY KEY,
    ad_limit INTEGER NOT NULL DEFAULT 4,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_by BIGINT
);

-- Таблица логов модерации
CREATE TABLE IF NOT EXISTS moderation_logs (
    id SERIAL PRIMARY KEY,
    target_user_id BIGINT NOT NULL,
    moderator_id BIGINT NOT NULL,
    action_type VARCHAR(50) NOT NULL, -- 'ban', 'unban', 'delete_ad', 'set_limit'
    action_details JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Таблица статистики
CREATE TABLE IF NOT EXISTS bot_stats (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL DEFAULT CURRENT_DATE,
    total_users INTEGER DEFAULT 0,
    total_ads INTEGER DEFAULT 0,
    new_users INTEGER DEFAULT 0,
    new_ads INTEGER DEFAULT 0,
    deleted_ads INTEGER DEFAULT 0,
    banned_users INTEGER DEFAULT 0,
    UNIQUE(date)
);

-- ===========================================
-- ИНДЕКСЫ ДЛЯ ОПТИМИЗАЦИИ
-- ===========================================

-- Основные индексы для user_ads
CREATE INDEX IF NOT EXISTS idx_user_ads_user_id ON user_ads(user_id);
CREATE INDEX IF NOT EXISTS idx_user_ads_message_id ON user_ads(message_id);
CREATE INDEX IF NOT EXISTS idx_user_ads_created_at ON user_ads(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_ads_topic ON user_ads(topic_name);

-- Составной индекс для быстрого подсчета объявлений пользователя
CREATE INDEX IF NOT EXISTS idx_user_ads_user_count ON user_ads(user_id, created_at DESC);

-- Индексы для модерации
CREATE INDEX IF NOT EXISTS idx_moderation_logs_target_user ON moderation_logs(target_user_id);
CREATE INDEX IF NOT EXISTS idx_moderation_logs_moderator ON moderation_logs(moderator_id);
CREATE INDEX IF NOT EXISTS idx_moderation_logs_action ON moderation_logs(action_type);
CREATE INDEX IF NOT EXISTS idx_moderation_logs_created_at ON moderation_logs(created_at DESC);

-- Индекс для статистики
CREATE INDEX IF NOT EXISTS idx_bot_stats_date ON bot_stats(date DESC);

-- ===========================================
-- ТРИГГЕРЫ ДЛЯ АВТОМАТИЧЕСКОГО ОБНОВЛЕНИЯ
-- ===========================================

-- Функция для обновления updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Триггеры для автоматического обновления timestamps
DROP TRIGGER IF EXISTS update_user_ads_updated_at ON user_ads;
CREATE TRIGGER update_user_ads_updated_at 
    BEFORE UPDATE ON user_ads 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_user_limits_updated_at ON user_limits;
CREATE TRIGGER update_user_limits_updated_at 
    BEFORE UPDATE ON user_limits 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ===========================================
-- ФУНКЦИИ ДЛЯ СТАТИСТИКИ
-- ===========================================

-- Функция для обновления ежедневной статистики
CREATE OR REPLACE FUNCTION update_daily_stats()
RETURNS VOID AS $$
DECLARE
    today DATE := CURRENT_DATE;
    total_users_count INTEGER;
    total_ads_count INTEGER;
    new_users_count INTEGER;
    new_ads_count INTEGER;
    deleted_ads_count INTEGER;
    banned_users_count INTEGER;
BEGIN
    -- Подсчитываем общие метрики
    SELECT COUNT(DISTINCT user_id) INTO total_users_count FROM user_ads;
    SELECT COUNT(*) INTO total_ads_count FROM user_ads;
    SELECT COUNT(DISTINCT user_id) INTO new_users_count 
        FROM user_ads WHERE DATE(created_at) = today;
    SELECT COUNT(*) INTO new_ads_count 
        FROM user_ads WHERE DATE(created_at) = today;
    SELECT COUNT(*) INTO banned_users_count FROM banned_users;
    
    -- Считаем удаленные объявления из логов модерации
    SELECT COUNT(*) INTO deleted_ads_count 
        FROM moderation_logs 
        WHERE action_type = 'delete_ad' AND DATE(created_at) = today;
    
    -- Обновляем или вставляем статистику
    INSERT INTO bot_stats (
        date, total_users, total_ads, new_users, 
        new_ads, deleted_ads, banned_users
    ) VALUES (
        today, total_users_count, total_ads_count, new_users_count,
        new_ads_count, deleted_ads_count, banned_users_count
    ) ON CONFLICT (date) DO UPDATE SET
        total_users = EXCLUDED.total_users,
        total_ads = EXCLUDED.total_ads,
        new_users = EXCLUDED.new_users,
        new_ads = EXCLUDED.new_ads,
        deleted_ads = EXCLUDED.deleted_ads,
        banned_users = EXCLUDED.banned_users;
END;
$$ LANGUAGE plpgsql;

-- ===========================================
-- ПРОЦЕДУРЫ ДЛЯ ОЧИСТКИ ДАННЫХ
-- ===========================================

-- Процедура очистки старых записей (для экономии места)
CREATE OR REPLACE FUNCTION cleanup_old_data(days_to_keep INTEGER DEFAULT 90)
RETURNS VOID AS $$
BEGIN
    -- Удаляем старые логи модерации
    DELETE FROM moderation_logs 
    WHERE created_at < CURRENT_TIMESTAMP - INTERVAL '1 day' * days_to_keep;
    
    -- Удаляем старую статистику (оставляем последний год)
    DELETE FROM bot_stats 
    WHERE date < CURRENT_DATE - INTERVAL '365 days';
    
    -- Обновляем статистику таблиц
    ANALYZE user_ads;
    ANALYZE banned_users;
    ANALYZE user_limits;
    ANALYZE moderation_logs;
    ANALYZE bot_stats;
END;
$$ LANGUAGE plpgsql;

-- ===========================================
-- ПРЕДСТАВЛЕНИЯ ДЛЯ АНАЛИТИКИ
-- ===========================================

-- Представление активных пользователей
CREATE OR REPLACE VIEW active_users AS
SELECT 
    user_id,
    COUNT(*) as ads_count,
    MAX(created_at) as last_ad_date,
    MIN(created_at) as first_ad_date
FROM user_ads 
WHERE user_id NOT IN (SELECT user_id FROM banned_users)
GROUP BY user_id;

-- Представление статистики по темам
CREATE OR REPLACE VIEW topic_stats AS
SELECT 
    topic_name,
    COUNT(*) as total_ads,
    COUNT(DISTINCT user_id) as unique_users,
    DATE_TRUNC('month', created_at) as month
FROM user_ads
GROUP BY topic_name, DATE_TRUNC('month', created_at)
ORDER BY month DESC, total_ads DESC;

-- ===========================================
-- НАЧАЛЬНЫЕ ДАННЫЕ
-- ===========================================

-- Создаем первую запись статистики
INSERT INTO bot_stats (date) VALUES (CURRENT_DATE) ON CONFLICT (date) DO NOTHING;

-- ===========================================
-- ПРАВА ДОСТУПА
-- ===========================================

-- Создаем роль для приложения (если нужно)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'telegram_bot') THEN
        CREATE ROLE telegram_bot LOGIN PASSWORD 'secure_password_here';
    END IF;
END
$$;

-- Предоставляем необходимые права
GRANT USAGE ON SCHEMA public TO telegram_bot;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO telegram_bot;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO telegram_bot;

-- ===========================================
-- ЗАВЕРШЕНИЕ
-- ===========================================

-- Обновляем статистику
SELECT update_daily_stats();

-- Информационное сообщение
DO $$
BEGIN
    RAISE NOTICE '✅ База данных Telegram Bot успешно инициализирована!';
    RAISE NOTICE 'ℹ️  Создано таблиц: 5';
    RAISE NOTICE 'ℹ️  Создано индексов: 10+';
    RAISE NOTICE 'ℹ️  Создано функций: 3';
    RAISE NOTICE 'ℹ️  Создано представлений: 2';
END
$$;
