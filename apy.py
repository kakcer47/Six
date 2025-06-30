"""
API endpoints и документация для Telegram Bot
Административные и мониторинговые endpoints
"""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from aiohttp import web, hdrs
from aiohttp.web_middlewares import middleware
import asyncpg
import logging
from pydantic import BaseModel, Field, ValidationError
import jwt
from functools import wraps

logger = logging.getLogger(__name__)

# ==================== МОДЕЛИ API ====================

class HealthResponse(BaseModel):
    """Модель ответа health check"""
    status: str = Field(..., description="Статус сервиса")
    timestamp: str = Field(..., description="Время проверки")
    uptime: str = Field(..., description="Время работы")
    version: str = Field(default="1.0.0", description="Версия приложения")
    environment: str = Field(..., description="Окружение")

class MetricsResponse(BaseModel):
    """Модель ответа метрик"""
    total_ads: int = Field(..., description="Общее количество объявлений")
    total_users: int = Field(..., description="Общее количество пользователей") 
    banned_users: int = Field(..., description="Количество заблокированных пользователей")
    today_ads: int = Field(..., description="Объявлений сегодня")
    today_users: int = Field(..., description="Новых пользователей сегодня")
    cache_status: str = Field(..., description="Статус кэша")
    database_status: str = Field(..., description="Статус базы данных")

class UserStatsRequest(BaseModel):
    """Запрос статистики пользователя"""
    user_id: int = Field(..., description="ID пользователя")

class UserStatsResponse(BaseModel):
    """Ответ со статистикой пользователя"""
    user_id: int
    ads_count: int
    ad_limit: int
    is_banned: bool
    first_ad_date: Optional[str]
    last_ad_date: Optional[str]

class BanUserRequest(BaseModel):
    """Запрос на блокировку пользователя"""
    user_id: int = Field(..., description="ID пользователя")
    reason: Optional[str] = Field(None, description="Причина блокировки")
    delete_ads: bool = Field(False, description="Удалить все объявления")

class SetLimitRequest(BaseModel):
    """Запрос на установку лимита"""
    user_id: int = Field(..., description="ID пользователя")
    limit: int = Field(..., ge=0, le=50, description="Новый лимит")

class TopicStatsResponse(BaseModel):
    """Статистика по темам"""
    topic_name: str
    ads_count: int
    users_count: int
    last_ad_date: Optional[str]

class SystemStatsResponse(BaseModel):
    """Системная статистика"""
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    active_connections: int
    uptime_seconds: int

# ==================== MIDDLEWARE ====================

@middleware
async def cors_middleware(request: web.Request, handler):
    """CORS middleware"""
    response = await handler(request)
    
    # Добавляем CORS заголовки только для API endpoints
    if request.path.startswith('/api/'):
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    
    return response

@middleware
async def error_middleware(request: web.Request, handler):
    """Middleware для обработки ошибок"""
    try:
        return await handler(request)
    except ValidationError as e:
        return web.json_response({
            'error': 'Validation Error',
            'details': e.errors()
        }, status=400)
    except ValueError as e:
        return web.json_response({
            'error': 'Value Error',
            'message': str(e)
        }, status=400)
    except Exception as e:
        logger.error(f"API Error: {e}", exc_info=True)
        return web.json_response({
            'error': 'Internal Server Error',
            'message': str(e) if request.app.get('debug') else 'An error occurred'
        }, status=500)

@middleware
async def auth_middleware(request: web.Request, handler):
    """Middleware для проверки аутентификации админских endpoint'ов"""
    # Проверяем только админские endpoint'ы
    if request.path.startswith('/api/admin/'):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return web.json_response({
                'error': 'Authorization required'
            }, status=401)
        
        token = auth_header[7:]  # Убираем 'Bearer '
        
        # Простая проверка токена (в продакшне используйте JWT)
        admin_token = request.app.get('admin_token')
        if admin_token and token != admin_token:
            return web.json_response({
                'error': 'Invalid token'
            }, status=403)
    
    return await handler(request)

# ==================== API ENDPOINTS ====================

class BotAPI:
    """Класс с API endpoints"""
    
    def __init__(self, db_pool: asyncpg.Pool, redis_client=None, config=None):
        self.db_pool = db_pool
        self.redis_client = redis_client
        self.config = config
        self.start_time = datetime.now()
    
    async def health(self, request: web.Request) -> web.Response:
        """
        GET /health
        Проверка здоровья сервиса
        """
        try:
            # Проверяем базу данных
            async with self.db_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            
            db_status = "ok"
        except Exception as e:
            db_status = f"error: {e}"
        
        # Проверяем Redis
        if self.redis_client:
            try:
                await self.redis_client.ping()
                cache_status = "ok"
            except Exception as e:
                cache_status = f"error: {e}"
        else:
            cache_status = "disabled"
        
        uptime = datetime.now() - self.start_time
        
        response_data = HealthResponse(
            status="ok" if db_status == "ok" else "degraded",
            timestamp=datetime.now().isoformat(),
            uptime=str(uptime),
            environment=getattr(self.config, 'environment', 'unknown').value if self.config else 'unknown'
        )
        
        return web.json_response(response_data.dict())
    
    async def metrics(self, request: web.Request) -> web.Response:
        """
        GET /metrics
        Основные метрики сервиса
        """
        try:
            async with self.db_pool.acquire() as conn:
                # Основная статистика
                stats = await conn.fetchrow("""
                    SELECT 
                        (SELECT COUNT(*) FROM user_ads) as total_ads,
                        (SELECT COUNT(DISTINCT user_id) FROM user_ads) as total_users,
                        (SELECT COUNT(*) FROM banned_users) as banned_users,
                        (SELECT COUNT(*) FROM user_ads WHERE created_at >= CURRENT_DATE) as today_ads,
                        (SELECT COUNT(DISTINCT user_id) FROM user_ads WHERE created_at >= CURRENT_DATE) as today_users
                """)
                
                response_data = MetricsResponse(
                    total_ads=stats['total_ads'] or 0,
                    total_users=stats['total_users'] or 0,
                    banned_users=stats['banned_users'] or 0,
                    today_ads=stats['today_ads'] or 0,
                    today_users=stats['today_users'] or 0,
                    cache_status="ok" if self.redis_client else "disabled",
                    database_status="ok"
                )
                
                return web.json_response(response_data.dict())
        
        except Exception as e:
            logger.error(f"Error getting metrics: {e}")
            return web.json_response({
                'error': str(e)
            }, status=500)
    
    async def get_user_stats(self, request: web.Request) -> web.Response:
        """
        GET /api/users/{user_id}/stats
        Статистика конкретного пользователя
        """
        try:
            user_id = int(request.match_info['user_id'])
            
            async with self.db_pool.acquire() as conn:
                # Статистика пользователя
                user_stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(*) as ads_count,
                        MIN(created_at) as first_ad_date,
                        MAX(created_at) as last_ad_date
                    FROM user_ads 
                    WHERE user_id = $1
                """, user_id)
                
                # Лимит пользователя
                limit_row = await conn.fetchrow(
                    "SELECT ad_limit FROM user_limits WHERE user_id = $1", user_id
                )
                ad_limit = limit_row['ad_limit'] if limit_row else 4
                
                # Проверка бана
                ban_row = await conn.fetchrow(
                    "SELECT 1 FROM banned_users WHERE user_id = $1", user_id
                )
                is_banned = ban_row is not None
                
                response_data = UserStatsResponse(
                    user_id=user_id,
                    ads_count=user_stats['ads_count'] or 0,
                    ad_limit=ad_limit,
                    is_banned=is_banned,
                    first_ad_date=user_stats['first_ad_date'].isoformat() if user_stats['first_ad_date'] else None,
                    last_ad_date=user_stats['last_ad_date'].isoformat() if user_stats['last_ad_date'] else None
                )
                
                return web.json_response(response_data.dict())
        
        except ValueError:
            return web.json_response({'error': 'Invalid user_id'}, status=400)
        except Exception as e:
            logger.error(f"Error getting user stats: {e}")
            return web.json_response({'error': str(e)}, status=500)
    
    async def get_topic_stats(self, request: web.Request) -> web.Response:
        """
        GET /api/stats/topics
        Статистика по темам объявлений
        """
        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT 
                        topic_name,
                        COUNT(*) as ads_count,
                        COUNT(DISTINCT user_id) as users_count,
                        MAX(created_at) as last_ad_date
                    FROM user_ads 
                    GROUP BY topic_name 
                    ORDER BY ads_count DESC
                """)
                
                stats = []
                for row in rows:
                    stats.append(TopicStatsResponse(
                        topic_name=row['topic_name'],
                        ads_count=row['ads_count'],
                        users_count=row['users_count'],
                        last_ad_date=row['last_ad_date'].isoformat() if row['last_ad_date'] else None
                    ).dict())
                
                return web.json_response(stats)
        
        except Exception as e:
            logger.error(f"Error getting topic stats: {e}")
            return web.json_response({'error': str(e)}, status=500)
    
    async def ban_user(self, request: web.Request) -> web.Response:
        """
        POST /api/admin/users/ban
        Заблокировать пользователя
        """
        try:
            data = await request.json()
            ban_request = BanUserRequest(**data)
            
            async with self.db_pool.acquire() as conn:
                async with conn.transaction():
                    # Проверяем, не забанен ли уже
                    existing_ban = await conn.fetchrow(
                        "SELECT 1 FROM banned_users WHERE user_id = $1", ban_request.user_id
                    )
                    
                    if existing_ban:
                        return web.json_response({
                            'error': 'User already banned'
                        }, status=400)
                    
                    # Баним пользователя
                    await conn.execute(
                        "INSERT INTO banned_users (user_id) VALUES ($1)",
                        ban_request.user_id
                    )
                    
                    deleted_count = 0
                    
                    # Удаляем объявления если нужно
                    if ban_request.delete_ads:
                        ads = await conn.fetch(
                            "SELECT message_id FROM user_ads WHERE user_id = $1",
                            ban_request.user_id
                        )
                        
                        # Здесь должно быть удаление из Telegram
                        # Но в API мы только помечаем в БД
                        
                        await conn.execute(
                            "DELETE FROM user_ads WHERE user_id = $1",
                            ban_request.user_id
                        )
                        
                        deleted_count = len(ads)
                    
                    # Логируем действие
                    await conn.execute("""
                        INSERT INTO moderation_logs 
                        (target_user_id, moderator_id, action_type, action_details) 
                        VALUES ($1, $2, $3, $4)
                    """, ban_request.user_id, 0, 'ban', json.dumps({
                        'reason': ban_request.reason,
                        'delete_ads': ban_request.delete_ads,
                        'deleted_count': deleted_count
                    }))
            
            return web.json_response({
                'success': True,
                'message': f'User {ban_request.user_id} banned',
                'deleted_ads': deleted_count
            })
        
        except Exception as e:
            logger.error(f"Error banning user: {e}")
            return web.json_response({'error': str(e)}, status=500)
    
    async def unban_user(self, request: web.Request) -> web.Response:
        """
        POST /api/admin/users/unban
        Разблокировать пользователя
        """
        try:
            data = await request.json()
            user_id = data.get('user_id')
            
            if not user_id:
                return web.json_response({'error': 'user_id required'}, status=400)
            
            async with self.db_pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM banned_users WHERE user_id = $1", user_id
                )
                
                if result == "DELETE 0":
                    return web.json_response({
                        'error': 'User was not banned'
                    }, status=400)
                
                # Логируем действие
                await conn.execute("""
                    INSERT INTO moderation_logs 
                    (target_user_id, moderator_id, action_type, action_details) 
                    VALUES ($1, $2, $3, $4)
                """, user_id, 0, 'unban', json.dumps({}))
            
            return web.json_response({
                'success': True,
                'message': f'User {user_id} unbanned'
            })
        
        except Exception as e:
            logger.error(f"Error unbanning user: {e}")
            return web.json_response({'error': str(e)}, status=500)
    
    async def set_user_limit(self, request: web.Request) -> web.Response:
        """
        POST /api/admin/users/limit
        Установить лимит объявлений для пользователя
        """
        try:
            data = await request.json()
            limit_request = SetLimitRequest(**data)
            
            async with self.db_pool.acquire() as conn:
                # Получаем старый лимит
                old_limit_row = await conn.fetchrow(
                    "SELECT ad_limit FROM user_limits WHERE user_id = $1", 
                    limit_request.user_id
                )
                old_limit = old_limit_row['ad_limit'] if old_limit_row else 4
                
                # Устанавливаем новый лимит
                await conn.execute("""
                    INSERT INTO user_limits (user_id, ad_limit) 
                    VALUES ($1, $2) 
                    ON CONFLICT (user_id) 
                    DO UPDATE SET ad_limit = $2, updated_at = CURRENT_TIMESTAMP
                """, limit_request.user_id, limit_request.limit)
                
                # Логируем действие
                await conn.execute("""
                    INSERT INTO moderation_logs 
                    (target_user_id, moderator_id, action_type, action_details) 
                    VALUES ($1, $2, $3, $4)
                """, limit_request.user_id, 0, 'set_limit', json.dumps({
                    'old_limit': old_limit,
                    'new_limit': limit_request.limit
                }))
            
            return web.json_response({
                'success': True,
                'message': f'Limit for user {limit_request.user_id} set to {limit_request.limit}',
                'old_limit': old_limit,
                'new_limit': limit_request.limit
            })
        
        except Exception as e:
            logger.error(f"Error setting user limit: {e}")
            return web.json_response({'error': str(e)}, status=500)
    
    async def get_system_stats(self, request: web.Request) -> web.Response:
        """
        GET /api/admin/system/stats
        Системная статистика
        """
        try:
            import psutil
            
            # CPU и память
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Подключения к БД
            async with self.db_pool.acquire() as conn:
                connections = await conn.fetchrow("""
                    SELECT COUNT(*) as active_connections
                    FROM pg_stat_activity 
                    WHERE datname = current_database() AND state = 'active'
                """)
            
            uptime = datetime.now() - self.start_time
            
            response_data = SystemStatsResponse(
                cpu_percent=cpu_percent,
                memory_percent=memory.percent,
                disk_percent=(disk.used / disk.total) * 100,
                active_connections=connections['active_connections'],
                uptime_seconds=int(uptime.total_seconds())
            )
            
            return web.json_response(response_data.dict())
        
        except Exception as e:
            logger.error(f"Error getting system stats: {e}")
            return web.json_response({'error': str(e)}, status=500)
    
    async def cleanup_data(self, request: web.Request) -> web.Response:
        """
        POST /api/admin/cleanup
        Очистка старых данных
        """
        try:
            data = await request.json()
            days_to_keep = data.get('days_to_keep', 90)
            dry_run = data.get('dry_run', False)
            
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
                        'dry_run': True,
                        'would_delete': {
                            'moderation_logs': old_logs,
                            'old_stats': old_stats
                        }
                    })
                else:
                    # Выполняем очистку
                    await conn.execute("SELECT cleanup_old_data($1)", days_to_keep)
                    
                    return web.json_response({
                        'success': True,
                        'message': f'Cleanup completed, kept last {days_to_keep} days'
                    })
        
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            return web.json_response({'error': str(e)}, status=500)

# ==================== SWAGGER/OpenAPI ДОКУМЕНТАЦИЯ ====================

async def api_docs(request: web.Request) -> web.Response:
    """
    GET /api/docs
    OpenAPI документация
    """
    
    openapi_spec = {
        "openapi": "3.0.0",
        "info": {
            "title": "Telegram Bot API",
            "version": "1.0.0",
            "description": "API для управления Telegram ботом объявлений"
        },
        "servers": [
            {"url": "/api", "description": "API сервер"}
        ],
        "paths": {
            "/health": {
                "get": {
                    "summary": "Проверка здоровья сервиса",
                    "responses": {
                        "200": {
                            "description": "Статус сервиса",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/HealthResponse"}
                                }
                            }
                        }
                    }
                }
            },
            "/metrics": {
                "get": {
                    "summary": "Метрики сервиса",
                    "responses": {
                        "200": {
                            "description": "Метрики",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/MetricsResponse"}
                                }
                            }
                        }
                    }
                }
            },
            "/users/{user_id}/stats": {
                "get": {
                    "summary": "Статистика пользователя",
                    "parameters": [
                        {
                            "name": "user_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"}
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Статистика пользователя",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/UserStatsResponse"}
                                }
                            }
                        }
                    }
                }
            },
            "/admin/users/ban": {
                "post": {
                    "summary": "Заблокировать пользователя",
                    "security": [{"bearerAuth": []}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/BanUserRequest"}
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Пользователь заблокирован"},
                        "401": {"description": "Не авторизован"},
                        "403": {"description": "Доступ запрещен"}
                    }
                }
            }
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer"
                }
            },
            "schemas": {
                "HealthResponse": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "timestamp": {"type": "string"},
                        "uptime": {"type": "string"},
                        "version": {"type": "string"},
                        "environment": {"type": "string"}
                    }
                },
                "MetricsResponse": {
                    "type": "object",
                    "properties": {
                        "total_ads": {"type": "integer"},
                        "total_users": {"type": "integer"},
                        "banned_users": {"type": "integer"},
                        "today_ads": {"type": "integer"},
                        "today_users": {"type": "integer"},
                        "cache_status": {"type": "string"},
                        "database_status": {"type": "string"}
                    }
                },
                "UserStatsResponse": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "integer"},
                        "ads_count": {"type": "integer"},
                        "ad_limit": {"type": "integer"},
                        "is_banned": {"type": "boolean"},
                        "first_ad_date": {"type": "string", "nullable": True},
                        "last_ad_date": {"type": "string", "nullable": True}
                    }
                },
                "BanUserRequest": {
                    "type": "object",
                    "required": ["user_id"],
                    "properties": {
                        "user_id": {"type": "integer"},
                        "reason": {"type": "string", "nullable": True},
                        "delete_ads": {"type": "boolean", "default": False}
                    }
                }
            }
        }
    }
    
    return web.json_response(openapi_spec)

def setup_api_routes(app: web.Application, api: BotAPI):
    """Настройка API маршрутов"""
    
    # Добавляем middleware
    app.middlewares.append(cors_middleware)
    app.middlewares.append(error_middleware)
    app.middlewares.append(auth_middleware)
    
    # Публичные endpoints
    app.router.add_get('/health', api.health)
    app.router.add_get('/metrics', api.metrics)
    app.router.add_get('/api/docs', api_docs)
    
    # API endpoints
    app.router.add_get('/api/users/{user_id}/stats', api.get_user_stats)
    app.router.add_get('/api/stats/topics', api.get_topic_stats)
    
    # Админские endpoints (требуют авторизации)
    app.router.add_post('/api/admin/users/ban', api.ban_user)
    app.router.add_post('/api/admin/users/unban', api.unban_user)
    app.router.add_post('/api/admin/users/limit', api.set_user_limit)
    app.router.add_get('/api/admin/system/stats', api.get_system_stats)
    app.router.add_post('/api/admin/cleanup', api.cleanup_data)
    
    # Swagger UI (простая версия)
    async def swagger_ui(request):
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>API Documentation</title>
            <link rel="stylesheet" type="text/css" href="https://unpkg.com/swagger-ui-dist@3.52.5/swagger-ui.css" />
        </head>
        <body>
            <div id="swagger-ui"></div>
            <script src="https://unpkg.com/swagger-ui-dist@3.52.5/swagger-ui-bundle.js"></script>
            <script>
                SwaggerUIBundle({
                    url: '/api/docs',
                    dom_id: '#swagger-ui',
                    presets: [
                        SwaggerUIBundle.presets.apis,
                        SwaggerUIBundle.presets.standalone
                    ]
                });
            </script>
        </body>
        </html>
        """
        return web.Response(text=html, content_type='text/html')
    
    app.router.add_get('/docs', swagger_ui)
