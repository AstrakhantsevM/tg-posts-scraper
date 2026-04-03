from pathlib import Path
from typing import Optional

# Pydantic — это «золотой стандарт» для работы с данными в Python.
# Field позволяет настраивать правила для каждого поля (например, другое имя в .env).
# SecretStr скрывает пароли в логах (вместо 'qwerty' выведет '**********').
from pydantic import Field, SecretStr, field_validator

# BaseSettings автоматически ищет переменные в .env или в системе и подставляет их в класс.
from pydantic_settings import BaseSettings, SettingsConfigDict

# ОПРЕДЕЛЯЕМ ПУТЬ К ПРОЕКТУ
# __file__ — это путь к этому текущему файлу.
# .resolve().parent.parent — «поднимаемся» на две папки вверх, чтобы найти корень проекта.
BASE_DIR = Path(__file__).resolve().parent.parent


class BaseConfig(BaseSettings):
    """
    БАЗОВЫЙ КЛАСС КОНФИГУРАЦИИ.
    Мы выносим настройки .env сюда, чтобы не дублировать их в каждом классе.
    Все классы ниже будут наследоваться от этого класса.
    """
    model_config = SettingsConfigDict(
        # Указываем, где лежит файл с секретами (корень проекта + ".env")
        env_file=BASE_DIR / ".env",
        # Кодировка (обязательно utf-8 для корректной работы на всех ОС)
        env_file_encoding='utf-8',
        # Если в .env есть лишние переменные, которых нет в коде, Pydantic их просто проигнорирует
        extra='ignore'
    )

class APISettings(BaseConfig):
    """
    Группа настроек для внешних сервисов (AI модели).
    Мы выносим их в отдельный подкласс, чтобы не сваливать всё в кучу.
    """
    # alias="M1KEY" говорит: «В коде я хочу называть это mistral_key,
    # но в файле .env ищи переменную с именем M1KEY».
    mistral_key: SecretStr = Field(alias="M1KEY")

    openrouter_key: SecretStr = Field(alias="O1KEY")

    groq_main: SecretStr = Field(alias="G1KEY")

    # Optional и default=None означают, что если этого ключа нет в .env,
    # программа НЕ упадет с ошибкой, а просто запишет туда None.
    groq_reserve: Optional[SecretStr] = Field(default=None, alias="G2KEY")


class TelegramSettings(BaseConfig):
    """Настройки для связи с Telegram API"""
    # Pydantic сам попробует превратить строку из .env в число (int).
    # Если там будет написано "привет", он выдаст ошибку валидации.
    api_id: int = Field(alias="TG_API_ID")
    api_hash: str = Field(alias="TG_API_HASH")
    # Дефолтная версия
    session_path: str = "sessions/default_session"

class AppSettings(BaseConfig):
    """
    ГЛАВНЫЙ КЛАСС.
    Он объединяет все мелкие группы настроек в единую структуру.
    """

    # ОБЩИЕ НАСТРОЙКИ
    # Если в .env нет DEBUG, по умолчанию будет False
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ПУТИ К ПАПКАМ
    # Path позволяет писать settings.PROMPTS_DIR / "my_file.txt"
    # и это будет работать и на Windows, и на Mac.
    PROMPTS_DIR: Path = BASE_DIR / "prompts"
    MODULES_DIR: Path = BASE_DIR / "modules"

    # ВЛОЖЕННЫЕ КЛАССЫ
    # Теперь мы можем обращаться к ним через точку: settings.api.mistral_key
    api: APISettings = APISettings()
    tg: TelegramSettings = TelegramSettings()

    # ВАЛИДАТОР (Проверка на адекватность)
    @field_validator("LOG_LEVEL")
    @classmethod
    def check_log_level(cls, value: str) -> str:
        """Проверяем, что уровень логов один из стандартных, а не случайный текст"""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if value.upper() not in valid_levels:
            # Если уровень логов странный — сразу сообщаем об этом
            raise ValueError(f"Недопустимый уровень логирования: {value}")
        return value.upper()


# СОЗДАЕМ ОБЪЕКТ
# В этот момент Pydantic идет в .env, читает всё, проверяет типы и создает объект.
# Если что-то не так (например, вместо API_ID — буквы), программа упадет СРАЗУ,
# не дожидаясь, пока ошибка вылезет в середине работы.
settings = AppSettings()