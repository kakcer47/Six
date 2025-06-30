"""
Утилиты мониторинга и администрирования для Telegram Bot
Дополнительные endpoints и функции для продакшн среды
"""

import asyncio
import logging
import os
import sys
import psutil
import asyncpg
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from aiohttp import web
import json

logger = logging.getLogger(__name__)

class MonitoringService:
    """Сервис мониторинга системы"""
    
    def __init__(self, db_pool: asyncpg.Pool, redis_client=None):
        self.db_pool = db_pool
        self.redis_client = redis_client
        self.start_time = datetime.now()
    
    async def get_system_metrics(self) -> Dict[str, Any]:
        """Получить системные метрики"""
        try:
            # CPU и память
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Информация о процессе
            process = psutil.Process()
            process_memory = process.memory_info()
            
            return {
                "system": {
                    "cpu_percent": cpu_percent,
                    "memory": {
                        "total": memory.total,
                        "available": memory.available,
                        "used": memory.used,
                        "percent": memory.percent
                    },
                    "disk": {
                        "total": disk.total,
                        "used": disk.used,
                        "free": disk.free,
                        "percent": (disk.used / disk.total) * 100
                    }
                },
                "process": {
                    "pid": process.pid,
                    "memory_rss": process_memory.rss,
                    "memory_vms": process_memory.vms,
                    "memory_percent": process.memory_percent(),
                    "cpu_percent": process.cpu_percent(),
                    "threads": process.num_threads(),
                    "uptime": str(datetime.now() - self.start_time)
                }
            }
        except Exception as e:
            logger.error(f"Ошибка получения системных метрик: {e}")
            return {}
    
    async def get_database_metrics(self) -> Dict[str, Any]:
        """Получить метрики базы данных"""
        try:
            async with self.db_pool.acquire() as conn:
                # Основная статистика
                stats_query = """
                    SELECT 
                        (SELECT COUNT(*) FROM user_ads) as total_ads,
                        (SELECT COUNT(DISTINCT user_id) FROM user_ads) as total_users,
                        (SELECT COUNT(*) FROM banned_users) as banned_users,
                        (SELECT COUNT(*) FROM user_ads WHERE created_at >= CURRENT_DATE) as today_ads,
                        (SELECT COUNT(DISTINCT user_id) FROM user_ads WHERE created_at >= CURRENT_DATE) as today_users
                """
                
                stats = await conn.fetchrow(stats_query)
                
                # Статистика по темам
                topics_query = """
                    SELECT topic_name, COUNT(*) as count 
                    FROM user_ads 
                    GROUP BY topic_name 
                    ORDER BY count DESC
                """
                topics = await conn.fetch(topics_query)
                
                # Активность за последние дни
                activity_query = """
                    SELECT 
                        DATE(created_at) as date,
                        COUNT(*) as ads,
                        COUNT(DISTINCT user_id) as users
                    FROM user_ads 
                    WHERE created_at >= CURRENT_DATE - INTERVAL '7 days'
                    GROUP BY DATE(created_at)
                    ORDER BY date DESC
                """
                activity = await conn.fetch(activity_query)
                
                # Информация о подключениях к БД
                connections_query = """
                    SELECT 
                        COUNT(*) as total_connections,
                        COUNT(*) FILTER (WHERE state = 'active') as active_connections,
                        COUNT(*) FILTER (WHERE state = 'idle') as idle_connections
                    FROM pg_stat_activity 
                    WHERE datname = current_database()
                """
                connections = await conn.fetchrow(connections_query)
                
                return {
                    "statistics": dict(stats),
                    "topics": [dict(row) for row in topics],
                    "activity": [dict(row) for row in activity],
                    "connections": dict(connections)
                }
        except Exception as e:
            logger.error(f"Ошибка получения метрик БД: {e}")
            return {}
    
    async def get_redis_metrics(self) -> Dict[str, Any]:
        """Получить метрики Redis"""
        if not self.redis_client:
            return {"status": "disabled"}
        
        try:
            info = await self.redis_client.info()
            return {
                "status": "connected",
                "version": info.get("redis_version"),
                "uptime": info.get("uptime_in_seconds"),
                "connected_clients": info.get("connected_clients"),
                "used_memory": info.get("used_memory"),
                "used_memory_human": info.get("used_memory_human"),
                "keyspace": info.get("db0", {})
            }
        except Exception as e:
            logger.error(f"Ошибка получения метрик Redis: {e}")
            return {"status": "error", "error": str(e)}

class AdminEndpoints:
    """Административные endpoints"""
    
    def __init__(self, monitoring: MonitoringService, db_pool: asyncpg.Pool):
        self.monitoring = monitoring
        self.db_pool = db_pool
    
    async def health_detailed(self, request: web.Request) -> web.Response:
        """Детальная проверка здоровья"""
        try:
            # Проверяем все компоненты
            checks = {}
            
            # Database check
            try:
                async with self.db_pool.acquire() as conn:
                    await conn.fetchval("SELECT 1")
                checks["database"] = {"status": "ok", "latency_ms": 0}
            except Exception as e:
                checks["database"] = {"status": "error", "error": str(e)}
            
            # Redis check
            if self.monitoring.redis_client:
                try:
                    await self.monitoring.redis_client.ping()
                    checks["redis"] = {"status": "ok"}
                except Exception as e:
                    checks["redis"] = {"status": "error", "error": str(e)}
            else:
                checks["redis"] = {"status": "disabled"}
            
            # System check
            try:
                memory = psutil.virtual_memory()
                disk = psutil.disk_usage('/')
                
                checks["system"] = {
                    "status": "ok" if memory.percent < 90 and disk.percent < 90 else "warning",
                    "memory_percent": memory.percent,
                    "disk_percent": disk.percent
                }
            except Exception as e:
                checks["system"] = {"status": "error", "error": str(e)}
            
            # Общий статус
            overall_status = "ok"
            if any(check.get("status") == "error" for check in checks.values()):
                overall_status = "error"
            elif any(check.get("status") == "warning" for check in checks.values()):
                overall_status = "warning"
            
            return web.json_response({
                "status": overall_status,
                "timestamp": datetime.now().isoformat(),
                "uptime": str(datetime.now() - self.monitoring.start_time),
                "checks": checks
            })
            
        except Exception as e:
            return web.json_response({
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }, status=500)
    
    async def metrics_detailed(self, request: web.Request) -> web.Response:
        """Детальные метрики системы"""
        try:
            system_metrics = await self.monitoring.get_system_metrics()
            db_metrics = await self.monitoring.get_database_metrics()
            redis_metrics = await self.monitoring.get_redis_metrics()
            
            return web.json_response({
                "timestamp": datetime.now().isoformat(),
                "system": system_metrics,
                "database": db_metrics,
                "redis": redis_metrics
            })
            
        except Exception as e:
            return web.json_response({
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }, status=500)
    
    async def admin_stats(self, request: web.Request) -> web.Response:
        """Административная статистика"""
        try:
            async with self.db_pool.acquire() as conn:
                # Топ пользователей по количеству объявлений
                top_users_query = """
                    SELECT 
                        user_id,
                        COUNT(*) as ads_count,
                        MIN(created_at) as first_ad,
                        MAX(created_at) as last_ad
                    FROM user_ads 
                    WHERE user_id NOT IN (SELECT user_id FROM banned_users)
                    GROUP BY user_id 
                    ORDER BY ads_count DESC 
                    LIMIT 10
                """
                top_users = await conn.fetch(top_users_query)
                
                # Статистика модерации
                moderation_query = """
                    SELECT 
                        action_type,
                        COUNT(*) as count,
                        MAX(created_at) as last_action
                    FROM moderation_logs 
                    WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'
                    GROUP BY action_type
                """
                moderation_stats = await conn.fetch(moderation_query)
                
                # Рост пользователей по дням
                growth_query = """
                    SELECT 
                        DATE(created_at) as date,
                        COUNT(*) as new_ads,
                        COUNT(DISTINCT user_id) as new_users
                    FROM user_ads 
                    WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'
                    GROUP BY DATE(created_at)
                    ORDER BY date DESC
                """
                growth_stats = await conn.fetch(growth_query)
                
                return web.json_response({
                    "top_users": [dict(row) for row in top_users],
                    "moderation": [dict(row) for row in moderation_stats], 
                    "growth": [dict(row) for row in growth_stats],
                    "generated_at": datetime.now().isoformat()
                })
                
        except Exception as e:
            return web.json_response({
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }, status=500)
    
    async def cleanup_database(self, request: web.Request) -> web.Response:
        """Очистка базы данных"""
        try:
            # Получаем параметры
            days_to_keep = int(request.query.get('days', 90))
            dry_run = request.query.get('dry_run', 'false').lower() == 'true'
            
            async with self.db_pool.acquire() as conn:
                if dry_run:
                    # Показываем что будет удалено
                    old_logs = await conn.fetchval(
                        "SELECT COUNT(*) FROM moderation_logs WHERE created_at < $1",
                        datetime.now() - timedelta(days=days_to_keep)
                    )
                    old_stats = await conn.fetchval(
                        "SELECT COUNT(*) FROM bot_stats WHERE date < $1",
                        datetime.now().date() - timedelta(days=365)
                    )
                    
                    return web.json_response({
                        "dry_run": True,
                        "would_delete": {
                            "moderation_logs": old_logs,
                            "old_stats": old_stats
                        }
                    })
                else:
                    # Выполняем очистку
                    await conn.execute("SELECT cleanup_old_data($1)", days_to_keep)
                    
                    return web.json_response({
                        "success": True,
                        "message": f"Очистка выполнена, сохранены данные за последние {days_to_keep} дней"
                    })
                    
        except Exception as e:
            return web.json_response({
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }, status=500)

def setup_monitoring_routes(app: web.Application, monitoring: MonitoringService, db_pool: asyncpg.Pool):
    """Настройка маршрутов мониторинга"""
    admin = AdminEndpoints(monitoring, db_pool)
    
    # Основные endpoints
    app.router.add_get('/health/detailed', admin.health_detailed)
    app.router.add_get('/metrics/detailed', admin.metrics_detailed)
    app.router.add_get('/admin/stats', admin.admin_stats)
    app.router.add_post('/admin/cleanup', admin.cleanup_database)

# Утилита для выполнения административных команд из командной строки
async def cli_admin(command: str, *args):
    """CLI для административных задач"""
    
    # Подключение к БД
    db_pool = await asyncpg.create_pool(os.getenv("DATABASE_URL"))
    
    try:
        if command == "stats":
            # Показать статистику
            async with db_pool.acquire() as conn:
                result = await conn.fetchrow("""
                    SELECT 
                        COUNT(*) as total_ads,
                        COUNT(DISTINCT user_id) as total_users,
                        COUNT(*) FILTER (WHERE created_at >= CURRENT_DATE) as today_ads
                    FROM user_ads
                """)
                print(f"📊 Статистика бота:")
                print(f"   Всего объявлений: {result['total_ads']}")
                print(f"   Всего пользователей: {result['total_users']}")
                print(f"   Объявлений сегодня: {result['today_ads']}")
        
        elif command == "cleanup":
            # Очистка данных
            days = int(args[0]) if args else 90
            async with db_pool.acquire() as conn:
                await conn.execute("SELECT cleanup_old_data($1)", days)
                print(f"✅ Очистка выполнена (сохранены данные за {days} дней)")
        
        elif command == "backup":
            # Создание бэкапа (требует pg_dump)
            backup_file = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql"
            os.system(f"pg_dump {os.getenv('DATABASE_URL')} > {backup_file}")
            print(f"💾 Бэкап создан: {backup_file}")
        
        elif command == "update-stats":
            # Обновление статистики
            async with db_pool.acquire() as conn:
                await conn.execute("SELECT update_daily_stats()")
                print("📈 Статистика обновлена")
        
        else:
            print("❌ Неизвестная команда")
            print("Доступные команды:")
            print("  stats - показать статистику")
            print("  cleanup [days] - очистить старые данные")  
            print("  backup - создать бэкап")
            print("  update-stats - обновить статистику")
    
    finally:
        await db_pool.close()

if __name__ == "__main__":
    # Запуск CLI команд
    if len(sys.argv) > 1:
        asyncio.run(cli_admin(*sys.argv[1:]))
    else:
        print("Использование: python monitoring.py <command> [args]")
        print("Команды: stats, cleanup, backup, update-stats")
