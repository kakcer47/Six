"""
Система миграций базы данных для Telegram Bot
Позволяет версионировать и обновлять схему БД в продакшне
"""

import asyncio
import asyncpg
import os
import sys
from datetime import datetime
from typing import List, Dict, Any, Optional
import logging
from pathlib import Path
import hashlib

logger = logging.getLogger(__name__)

class Migration:
    """Базовый класс миграции"""
    
    def __init__(self, version: str, description: str):
        self.version = version
        self.description = description
        self.timestamp = datetime.now()
    
    async def up(self, connection: asyncpg.Connection):
        """Применить миграцию"""
        raise NotImplementedError("Метод up должен быть реализован")
    
    async def down(self, connection: asyncpg.Connection):
        """Откатить миграцию"""
        raise NotImplementedError("Метод down должен быть реализован")
    
    def get_checksum(self) -> str:
        """Получить контрольную сумму миграции"""
        content = f"{self.version}{self.description}"
        return hashlib.md5(content.encode()).hexdigest()

class MigrationManager:
    """Менеджер миграций"""
    
    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        self.migrations: List[Migration] = []
    
    def add_migration(self, migration: Migration):
        """Добавить миграцию"""
        self.migrations.append(migration)
    
    async def init_migration_table(self, connection: asyncpg.Connection):
        """Инициализация таблицы миграций"""
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(255) PRIMARY KEY,
                description TEXT NOT NULL,
                checksum VARCHAR(32) NOT NULL,
                applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)
    
    async def get_applied_migrations(self, connection: asyncpg.Connection) -> List[str]:
        """Получить список примененных миграций"""
        rows = await connection.fetch(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
        return [row['version'] for row in rows]
    
    async def apply_migration(self, connection: asyncpg.Connection, migration: Migration):
        """Применить одну миграцию"""
        logger.info(f"Применение миграции {migration.version}: {migration.description}")
        
        try:
            # Применяем миграцию в транзакции
            async with connection.transaction():
                await migration.up(connection)
                
                # Записываем в таблицу миграций
                await connection.execute(
                    """INSERT INTO schema_migrations (version, description, checksum) 
                       VALUES ($1, $2, $3)""",
                    migration.version, migration.description, migration.get_checksum()
                )
            
            logger.info(f"✅ Миграция {migration.version} успешно применена")
            
        except Exception as e:
            logger.error(f"❌ Ошибка применения миграции {migration.version}: {e}")
            raise
    
    async def rollback_migration(self, connection: asyncpg.Connection, migration: Migration):
        """Откатить миграцию"""
        logger.info(f"Откат миграции {migration.version}: {migration.description}")
        
        try:
            async with connection.transaction():
                await migration.down(connection)
                
                # Удаляем из таблицы миграций
                await connection.execute(
                    "DELETE FROM schema_migrations WHERE version = $1",
                    migration.version
                )
            
            logger.info(f"✅ Миграция {migration.version} успешно откачена")
            
        except Exception as e:
            logger.error(f"❌ Ошибка отката миграции {migration.version}: {e}")
            raise
    
    async def migrate(self):
        """Применить все неприменные миграции"""
        connection = await asyncpg.connect(self.connection_string)
        
        try:
            await self.init_migration_table(connection)
            applied = await self.get_applied_migrations(connection)
            
            # Сортируем миграции по версии
            self.migrations.sort(key=lambda m: m.version)
            
            for migration in self.migrations:
                if migration.version not in applied:
                    await self.apply_migration(connection, migration)
                else:
                    logger.debug(f"Миграция {migration.version} уже применена")
            
            logger.info("🎉 Все миграции успешно применены")
            
        finally:
            await connection.close()
    
    async def rollback(self, target_version: Optional[str] = None):
        """Откатить миграции до указанной версии"""
        connection = await asyncpg.connect(self.connection_string)
        
        try:
            applied = await self.get_applied_migrations(connection)
            
            # Сортируем в обратном порядке для отката
            self.migrations.sort(key=lambda m: m.version, reverse=True)
            
            for migration in self.migrations:
                if migration.version in applied:
                    await self.rollback_migration(connection, migration)
                    
                    if target_version and migration.version == target_version:
                        break
            
            logger.info("🎉 Откат миграций завершен")
            
        finally:
            await connection.close()
    
    async def status(self):
        """Показать статус миграций"""
        connection = await asyncpg.connect(self.connection_string)
        
        try:
            await self.init_migration_table(connection)
            applied = await self.get_applied_migrations(connection)
            
            print("📊 Статус миграций:")
            print("=" * 80)
            
            self.migrations.sort(key=lambda m: m.version)
            
            for migration in self.migrations:
                status = "✅ APPLIED" if migration.version in applied else "⏳ PENDING"
                print(f"{migration.version:15} | {status:10} | {migration.description}")
            
            print("=" * 80)
            print(f"Всего миграций: {len(self.migrations)}")
            print(f"Применено: {len(applied)}")
            print(f"Ожидает: {len(self.migrations) - len(applied)}")
            
        finally:
            await connection.close()

# ==================== КОНКРЕТНЫЕ МИГРАЦИИ ====================

class Migration001_InitialSchema(Migration):
    """Создание начальной схемы"""
    
    def __init__(self):
        super().__init__("001", "Создание начальной схемы базы данных")
    
    async def up(self, connection: asyncpg.Connection):
        # Включаем расширения
        await connection.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
        
        # Таблица объявлений
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS user_ads (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL UNIQUE,
                message_url TEXT NOT NULL,
                topic_name TEXT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица заблокированных пользователей
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id BIGINT PRIMARY KEY,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица лимитов
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS user_limits (
                user_id BIGINT PRIMARY KEY,
                ad_limit INTEGER NOT NULL DEFAULT 4,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Основные индексы
        await connection.execute("CREATE INDEX IF NOT EXISTS idx_user_ads_user_id ON user_ads(user_id)")
        await connection.execute("CREATE INDEX IF NOT EXISTS idx_user_ads_message_id ON user_ads(message_id)")
        await connection.execute("CREATE INDEX IF NOT EXISTS idx_user_ads_created_at ON user_ads(created_at DESC)")
    
    async def down(self, connection: asyncpg.Connection):
        await connection.execute("DROP TABLE IF EXISTS user_limits")
        await connection.execute("DROP TABLE IF EXISTS banned_users") 
        await connection.execute("DROP TABLE IF EXISTS user_ads")

class Migration002_AddModerationLogs(Migration):
    """Добавление логов модерации"""
    
    def __init__(self):
        super().__init__("002", "Добавление таблицы логов модерации")
    
    async def up(self, connection: asyncpg.Connection):
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS moderation_logs (
                id SERIAL PRIMARY KEY,
                target_user_id BIGINT NOT NULL,
                moderator_id BIGINT NOT NULL,
                action_type VARCHAR(50) NOT NULL,
                action_details JSONB,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Индексы для быстрого поиска
        await connection.execute("CREATE INDEX IF NOT EXISTS idx_moderation_logs_target_user ON moderation_logs(target_user_id)")
        await connection.execute("CREATE INDEX IF NOT EXISTS idx_moderation_logs_action_type ON moderation_logs(action_type)")
        await connection.execute("CREATE INDEX IF NOT EXISTS idx_moderation_logs_created_at ON moderation_logs(created_at DESC)")
    
    async def down(self, connection: asyncpg.Connection):
        await connection.execute("DROP TABLE IF EXISTS moderation_logs")

class Migration003_AddStatistics(Migration):
    """Добавление таблицы статистики"""
    
    def __init__(self):
        super().__init__("003", "Добавление таблицы статистики бота")
    
    async def up(self, connection: asyncpg.Connection):
        await connection.execute("""
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
            )
        """)
        
        await connection.execute("CREATE INDEX IF NOT EXISTS idx_bot_stats_date ON bot_stats(date DESC)")
        
        # Создаем функцию обновления статистики
        await connection.execute("""
            CREATE OR REPLACE FUNCTION update_daily_stats()
            RETURNS VOID AS $$
            DECLARE
                today DATE := CURRENT_DATE;
                total_users_count INTEGER;
                total_ads_count INTEGER;
                new_users_count INTEGER;
                new_ads_count INTEGER;
                banned_users_count INTEGER;
            BEGIN
                SELECT COUNT(DISTINCT user_id) INTO total_users_count FROM user_ads;
                SELECT COUNT(*) INTO total_ads_count FROM user_ads;
                SELECT COUNT(DISTINCT user_id) INTO new_users_count 
                    FROM user_ads WHERE DATE(created_at) = today;
                SELECT COUNT(*) INTO new_ads_count 
                    FROM user_ads WHERE DATE(created_at) = today;
                SELECT COUNT(*) INTO banned_users_count FROM banned_users;
                
                INSERT INTO bot_stats (
                    date, total_users, total_ads, new_users, 
                    new_ads, banned_users
                ) VALUES (
                    today, total_users_count, total_ads_count, new_users_count,
                    new_ads_count, banned_users_count
                ) ON CONFLICT (date) DO UPDATE SET
                    total_users = EXCLUDED.total_users,
                    total_ads = EXCLUDED.total_ads,
                    new_users = EXCLUDED.new_users,
                    new_ads = EXCLUDED.new_ads,
                    banned_users = EXCLUDED.banned_users;
            END;
            $$ LANGUAGE plpgsql;
        """)
    
    async def down(self, connection: asyncpg.Connection):
        await connection.execute("DROP FUNCTION IF EXISTS update_daily_stats()")
        await connection.execute("DROP TABLE IF EXISTS bot_stats")

class Migration004_AddUpdatedAtColumns(Migration):
    """Добавление колонок updated_at"""
    
    def __init__(self):
        super().__init__("004", "Добавление updated_at и триггеров автообновления")
    
    async def up(self, connection: asyncpg.Connection):
        # Добавляем колонку updated_at к user_ads
        await connection.execute("""
            ALTER TABLE user_ads 
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        """)
        
        # Функция для автообновления updated_at
        await connection.execute("""
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = CURRENT_TIMESTAMP;
                RETURN NEW;
            END;
            $$ language 'plpgsql';
        """)
        
        # Триггер для user_ads
        await connection.execute("""
            DROP TRIGGER IF EXISTS update_user_ads_updated_at ON user_ads;
            CREATE TRIGGER update_user_ads_updated_at 
                BEFORE UPDATE ON user_ads 
                FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
        """)
        
        # Триггер для user_limits
        await connection.execute("""
            DROP TRIGGER IF EXISTS update_user_limits_updated_at ON user_limits;
            CREATE TRIGGER update_user_limits_updated_at 
                BEFORE UPDATE ON user_limits 
                FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
        """)
    
    async def down(self, connection: asyncpg.Connection):
        await connection.execute("DROP TRIGGER IF EXISTS update_user_ads_updated_at ON user_ads")
        await connection.execute("DROP TRIGGER IF EXISTS update_user_limits_updated_at ON user_limits")
        await connection.execute("DROP FUNCTION IF EXISTS update_updated_at_column()")
        await connection.execute("ALTER TABLE user_ads DROP COLUMN IF EXISTS updated_at")

class Migration005_AddCleanupFunction(Migration):
    """Добавление функции очистки старых данных"""
    
    def __init__(self):
        super().__init__("005", "Добавление функции очистки устаревших данных")
    
    async def up(self, connection: asyncpg.Connection):
        await connection.execute("""
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
        """)
    
    async def down(self, connection: asyncpg.Connection):
        await connection.execute("DROP FUNCTION IF EXISTS cleanup_old_data(INTEGER)")

# ==================== ИНИЦИАЛИЗАЦИЯ МИГРАЦИЙ ====================

def create_migration_manager(connection_string: str) -> MigrationManager:
    """Создать менеджер миграций со всеми миграциями"""
    manager = MigrationManager(connection_string)
    
    # Добавляем все миграции в порядке версий
    manager.add_migration(Migration001_InitialSchema())
    manager.add_migration(Migration002_AddModerationLogs())
    manager.add_migration(Migration003_AddStatistics())
    manager.add_migration(Migration004_AddUpdatedAtColumns())
    manager.add_migration(Migration005_AddCleanupFunction())
    
    return manager

# ==================== CLI ИНТЕРФЕЙС ====================

async def main():
    """CLI интерфейс для управления миграциями"""
    if len(sys.argv) < 2:
        print("Использование: python migrations.py <command> [args]")
        print("Команды:")
        print("  migrate    - Применить все миграции")
        print("  rollback   - Откатить последнюю миграцию")
        print("  rollback <version> - Откатить до указанной версии")
        print("  status     - Показать статус миграций")
        return
    
    command = sys.argv[1]
    database_url = os.getenv("DATABASE_URL")
    
    if not database_url:
        print("❌ Переменная DATABASE_URL не установлена")
        return
    
    manager = create_migration_manager(database_url)
    
    try:
        if command == "migrate":
            await manager.migrate()
        elif command == "rollback":
            target_version = sys.argv[2] if len(sys.argv) > 2 else None
            await manager.rollback(target_version)
        elif command == "status":
            await manager.status()
        else:
            print(f"❌ Неизвестная команда: {command}")
    
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
