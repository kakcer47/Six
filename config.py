"""
Конфигурация для разных сред разработки
Поддерживает development, staging, production
"""

import os
from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum
import logging

class Environment(Enum):
    """Окружения приложения"""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TESTING = "testing"

@dataclass
class DatabaseConfig:
    """Конфигурация базы данных"""
    url: str
    min_size: int = 2
    max_size: int = 10
    command_timeout: int = 30
    
    @property
    def pool_kwargs(self) -> Dict[str, Any]:
        return {
            'min_size': self.min_size,
            'max_size': self.max_size,
            'command_timeout': self.command_timeout
        }

@dataclass
class RedisConfig:
    """Конфигурация Redis"""
    url: str
    max_connections: int = 20
    retry_on_timeout: bool = True
    decode_responses: bool = True
    
    @property
    def connection_kwargs(self) -> Dict[str, Any]:
        return {
            'max_connections': self.max_connections,
            'retry_on_timeout': self.retry_on_timeout,
            'decode_responses': self.decode_responses
        }

@dataclass
class TelegramConfig:
    """Конфигурация Telegram"""
    bot_token: str
    target_chat_id: int
    moderation_chat_id: Optional[int] = None
    group_link: str = "https://t.me/your_group"
    example_url: str = "https://example.com"
    
    def validate(self):
        """Валидация конфигурации Telegram"""
        if not self.bot_token:
            raise ValueError("BOT_TOKEN не может быть пустым")
        if not self.target_chat_id:
            raise ValueError("TARGET_CHAT_ID не может быть пустым")

@dataclass
class WebhookConfig:
    """Конфигурация webhook"""
    host: str
    path: str = "/webhook"
    port: int = 8080
    
    @property
    def url(self) -> str:
        return f"{self.host}{self.path}"
    
    @property
    def listen_address(self) -> str:
        return f"0.0.0.0:{self.port}"

@dataclass
class SecurityConfig:
    """Конфигурация безопасности"""
    rate_limit_window: int = 60
    rate_limit_max_requests: int = 10
    default_ad_limit: int = 4
    max_ad_limit: int = 50
    max_message_length: int = 4000
    
    # Валидация сообщений
    allow_urls: bool = False
    allow_usernames: bool = False
    allow_hashtags: bool = False
    allow_domains: bool = False

@dataclass
class LoggingConfig:
    """Конфигурация логирования"""
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file_path: Optional[str] = None
    max_bytes: int = 10 * 1024 * 1024  # 10MB
    backup_count: int = 5
    json_format: bool = False

class Config:
    """Главный класс конфигурации"""
    
    def __init__(self, environment: Optional[Environment] = None):
        self.environment = environment or self._detect_environment()
        
        # Основные конфигурации
        self.database = self._get_database_config()
        self.redis = self._get_redis_config()
        self.telegram = self._get_telegram_config()
        self.webhook = self._get_webhook_config()
        self.security = self._get_security_config()
        self.logging = self._get_logging_config()
        
        # Валидация
        self.validate()
    
    def _detect_environment(self) -> Environment:
        """Автоматическое определение окружения"""
        env_str = os.getenv("ENVIRONMENT", "development").lower()
        
        try:
            return Environment(env_str)
        except ValueError:
            # Определяем по другим признакам
            if os.getenv("PYTEST_CURRENT_TEST"):
                return Environment.TESTING
            elif "render.com" in os.getenv("WEBHOOK_HOST", ""):
                return Environment.PRODUCTION
            elif "staging" in os.getenv("WEBHOOK_HOST", ""):
                return Environment.STAGING
            else:
                return Environment.DEVELOPMENT
    
    def _get_database_config(self) -> DatabaseConfig:
        """Конфигурация базы данных для текущего окружения"""
        url = os.getenv("DATABASE_URL")
        if not url:
            raise ValueError("DATABASE_URL не установлен")
        
        if self.environment == Environment.PRODUCTION:
            return DatabaseConfig(
                url=url,
                min_size=3,
                max_size=15,
                command_timeout=60
            )
        elif self.environment == Environment.STAGING:
            return DatabaseConfig(
                url=url,
                min_size=2,
                max_size=8,
                command_timeout=45
            )
        elif self.environment == Environment.TESTING:
            return DatabaseConfig(
                url=url,
                min_size=1,
                max_size=3,
                command_timeout=10
            )
        else:  # Development
            return DatabaseConfig(
                url=url,
                min_size=1,
                max_size=5,
                command_timeout=30
            )
    
    def _get_redis_config(self) -> Optional[RedisConfig]:
        """Конфигурация Redis"""
        url = os.getenv("REDIS_URL")
        if not url:
            return None
        
        if self.environment == Environment.PRODUCTION:
            return RedisConfig(
                url=url,
                max_connections=50,
                retry_on_timeout=True
            )
        else:
            return RedisConfig(
                url=url,
                max_connections=20,
                retry_on_timeout=True
            )
    
    def _get_telegram_config(self) -> TelegramConfig:
        """Конфигурация Telegram"""
        config = TelegramConfig(
            bot_token=os.getenv("BOT_TOKEN", ""),
            target_chat_id=int(os.getenv("TARGET_CHAT_ID", "0")),
            moderation_chat_id=int(os.getenv("MODERATION_CHAT_ID", "0")) or None,
            group_link=os.getenv("GROUP_LINK", "https://t.me/your_group"),
            example_url=os.getenv("EXAMPLE_URL", "https://example.com")
        )
        
        config.validate()
        return config
    
    def _get_webhook_config(self) -> WebhookConfig:
        """Конфигурация webhook"""
        host = os.getenv("WEBHOOK_HOST", "http://localhost:8080")
        port = int(os.getenv("PORT", "8080"))
        
        return WebhookConfig(
            host=host,
            port=port
        )
    
    def _get_security_config(self) -> SecurityConfig:
        """Конфигурация безопасности"""
        if self.environment == Environment.PRODUCTION:
            return SecurityConfig(
                rate_limit_window=60,
                rate_limit_max_requests=5,  # Более строгий лимит
                default_ad_limit=4,
                max_message_length=3000,    # Короче в проде
            )
        elif self.environment == Environment.DEVELOPMENT:
            return SecurityConfig(
                rate_limit_window=60,
                rate_limit_max_requests=20,  # Мягче для разработки
                default_ad_limit=10,
                max_message_length=5000,
            )
        else:
            return SecurityConfig()  # Значения по умолчанию
    
    def _get_logging_config(self) -> LoggingConfig:
        """Конфигурация логирования"""
        if self.environment == Environment.PRODUCTION:
            return LoggingConfig(
                level="INFO",
                json_format=True,
                file_path="/app/logs/bot.log"
            )
        elif self.environment == Environment.DEVELOPMENT:
            return LoggingConfig(
                level="DEBUG",
                json_format=False
            )
        elif self.environment == Environment.TESTING:
            return LoggingConfig(
                level="WARNING",
                json_format=False
            )
        else:
            return LoggingConfig()
    
    def validate(self):
        """Валидация всей конфигурации"""
        self.telegram.validate()
        
        # Дополнительные проверки
        if self.environment == Environment.PRODUCTION:
            if not self.webhook.host.startswith("https://"):
                raise ValueError("Production должен использовать HTTPS")
            
            if self.security.rate_limit_max_requests > 10:
                raise ValueError("Rate limit в production не должен превышать 10")
    
    @property
    def is_development(self) -> bool:
        return self.environment == Environment.DEVELOPMENT
    
    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION
    
    @property
    def is_staging(self) -> bool:
        return self.environment == Environment.STAGING
    
    @property
    def is_testing(self) -> bool:
        return self.environment == Environment.TESTING
    
    def setup_logging(self):
        """Настройка логирования"""
        handlers = []
        
        if self.logging.json_format:
            import json_logging
            json_logging.init_non_web(enable_json=True)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(getattr(logging, self.logging.level))
        formatter = logging.Formatter(self.logging.format)
        console_handler.setFormatter(formatter)
        handlers.append(console_handler)
        
        # File handler (если указан путь)
        if self.logging.file_path:
            from logging.handlers import RotatingFileHandler
            os.makedirs(os.path.dirname(self.logging.file_path), exist_ok=True)
            
            file_handler = RotatingFileHandler(
                self.logging.file_path,
                maxBytes=self.logging.max_bytes,
                backupCount=self.logging.backup_count
            )
            file_handler.setLevel(getattr(logging, self.logging.level))
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
        
        # Настройка root logger
        logging.basicConfig(
            level=getattr(logging, self.logging.level),
            format=self.logging.format,
            handlers=handlers
        )
        
        # Настройка уровней для внешних библиотек
        if self.environment == Environment.PRODUCTION:
            logging.getLogger('aiogram').setLevel(logging.WARNING)
            logging.getLogger('aiohttp').setLevel(logging.WARNING)
            logging.getLogger('asyncpg').setLevel(logging.WARNING)
        elif self.environment == Environment.DEVELOPMENT:
            logging.getLogger('aiogram').setLevel(logging.INFO)
            logging.getLogger('aiohttp').setLevel(logging.INFO)
    
    def get_topic_config(self) -> Dict[str, Dict[str, Any]]:
        """Конфигурация тем (можно переопределить в наследниках)"""
        return {
            "topic_1": {"name": "💼 Работа", "id": 27},
            "topic_2": {"name": "🏠 Недвижимость", "id": 28},
            "topic_3": {"name": "🚗 Авто", "id": 29},
            "topic_4": {"name": "🛍️ Товары", "id": 30},
            "topic_5": {"name": "💡 Услуги", "id": 31},
            "topic_6": {"name": "📚 Обучение", "id": 32},
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """Преобразование конфигурации в словарь"""
        return {
            "environment": self.environment.value,
            "database": {
                "url": "***HIDDEN***",
                "min_size": self.database.min_size,
                "max_size": self.database.max_size,
                "command_timeout": self.database.command_timeout
            },
            "redis": {
                "enabled": self.redis is not None,
                "max_connections": self.redis.max_connections if self.redis else None
            },
            "telegram": {
                "target_chat_id": self.telegram.target_chat_id,
                "moderation_chat_id": self.telegram.moderation_chat_id,
                "group_link": self.telegram.group_link
            },
            "webhook": {
                "host": self.webhook.host,
                "port": self.webhook.port
            },
            "security": {
                "rate_limit_window": self.security.rate_limit_window,
                "rate_limit_max_requests": self.security.rate_limit_max_requests,
                "default_ad_limit": self.security.default_ad_limit
            }
        }

# Специализированные конфигурации для разных сред

class DevelopmentConfig(Config):
    """Конфигурация для разработки"""
    
    def __init__(self):
        super().__init__(Environment.DEVELOPMENT)
    
    def get_topic_config(self) -> Dict[str, Dict[str, Any]]:
        # В разработке можем использовать тестовые темы
        return {
            "topic_1": {"name": "🧪 Тест Работа", "id": 27},
            "topic_2": {"name": "🧪 Тест Недвижимость", "id": 28},
            "topic_3": {"name": "🧪 Тест Авто", "id": 29},
        }

class ProductionConfig(Config):
    """Конфигурация для продакшна"""
    
    def __init__(self):
        super().__init__(Environment.PRODUCTION)
    
    def validate(self):
        super().validate()
        
        # Дополнительные проверки для продакшна
        if not self.telegram.moderation_chat_id:
            raise ValueError("MODERATION_CHAT_ID обязателен в продакшне")

class TestingConfig(Config):
    """Конфигурация для тестирования"""
    
    def __init__(self):
        super().__init__(Environment.TESTING)
        
        # Переопределяем некоторые значения для тестов
        self.telegram.bot_token = "test_token"
        self.telegram.target_chat_id = -1001234567890
        self.security.rate_limit_max_requests = 1000  # Без лимитов в тестах

# Фабрика конфигураций
def get_config(environment: Optional[str] = None) -> Config:
    """Получить конфигурацию для указанного окружения"""
    if environment:
        env = Environment(environment.lower())
    else:
        env = Environment(os.getenv("ENVIRONMENT", "development").lower())
    
    if env == Environment.DEVELOPMENT:
        return DevelopmentConfig()
    elif env == Environment.PRODUCTION:
        return ProductionConfig()
    elif env == Environment.STAGING:
        return Config(Environment.STAGING)
    elif env == Environment.TESTING:
        return TestingConfig()
    else:
        return Config(env)

# Глобальная конфигурация (ленивая инициализация)
_config: Optional[Config] = None

def get_current_config() -> Config:
    """Получить текущую конфигурацию (singleton)"""
    global _config
    if _config is None:
        _config = get_config()
    return _config

def set_config(config: Config):
    """Установить глобальную конфигурацию (для тестов)"""
    global _config
    _config = config

# Пример использования
if __name__ == "__main__":
    # Тестируем конфигурацию
    config = get_config()
    print(f"Environment: {config.environment.value}")
    print(f"Config: {config.to_dict()}")
    
    # Настраиваем логирование
    config.setup_logging()
    
    logger = logging.getLogger(__name__)
    logger.info(f"Конфигурация загружена для окружения: {config.environment.value}")
