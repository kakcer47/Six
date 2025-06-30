-- ===========================================
-- Упрощенная инициализация БД для Telegram Bot
-- Только основные таблицы без мониторинга
-- ===========================================

-- Включаем расширения PostgreSQL
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

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
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Таблица заблокированных пользователей
CREATE TABLE IF NOT EXISTS banned_users (
    user_id BIGINT PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Таблица лимитов пользователей
CREATE TABLE IF NOT EXISTS user_limits (
    user_id BIGINT PRIMARY KEY,
    ad_limit INTEGER NOT NULL DEFAULT 4,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
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

-- ===========================================
-- ЗАВЕРШЕНИЕ
-- ===========================================

-- Информационное сообщение
DO $
BEGIN
    RAISE NOTICE '✅ Упрощенная база данных Telegram Bot инициализирована!';
    RAISE NOTICE 'ℹ️  Создано таблиц: 3 (user_ads, banned_users, user_limits)';
    RAISE NOTICE 'ℹ️  Создано индексов: 5';
    RAISE NOTICE 'ℹ️  Готово к работе!';
END
$;
