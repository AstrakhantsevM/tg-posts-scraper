"""
PresetLoader — модуль для загрузки конфигураций, текстов промптов и справочников регионов.
Отвечает только за чтение файлов и первичную валидацию структуры.
"""
import json
import logging
from pathlib import Path
from typing import List, Literal, Union, Dict
from configs.preset_schema import PresetConfig

# Настройка логгера для отслеживания процесса загрузки
logger = logging.getLogger(__name__)

# Определение путей относительно текущего файла для переносимости кода
PROJECT_ROOT = Path(__file__).parent.parent
PRESETS_DIR = PROJECT_ROOT / "configs" / "presets"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
REGIONS_FILE = PROJECT_ROOT / "data" / "russian_regions.json"


class PresetLoader:
    """
    Класс-утилита для работы с файловой системой и загрузки данных в модель PresetConfig.
    Все методы статические, так как класс не хранит внутреннее состояние.
    """

    @staticmethod
    def load(preset_name: str) -> PresetConfig:
        """
        Читает JSON-файл пресета и превращает его в объект PresetConfig.

        :param preset_name: Имя файла в папке /configs/presets/ (без расширения .json).
        :return: Валидированный объект PresetConfig.
        :raises FileNotFoundError: Если файл пресета отсутствует.
        :raises pydantic.ValidationError: Если данные в JSON не соответствуют схеме.
        """
        path = PRESETS_DIR / f"{preset_name}.json"

        if not path.exists():
            # Помогаем пользователю, выводя список доступных пресетов при ошибке
            available = [f.stem for f in PRESETS_DIR.glob("*.json")]
            raise FileNotFoundError(
                f"Пресет '{preset_name}' не найден по пути {path}. "
                f"Доступные варианты: {available}"
            )

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        # Создаем экземпляр PresetConfig.
        # Pydantic автоматически преобразует строку 'stop_date' в объект date.
        preset = PresetConfig(**data)

        logger.info(f"[PRESET] Успешно загружен: «{preset.name}» (Описание: {preset.description})")
        return preset

    @staticmethod
    def load_prompt(prompt_file: str) -> str:
        """
        Считывает содержимое текстового файла с инструкциями для LLM.

        :param prompt_file: Имя файла в папке /prompts/ (с расширением, например 'base_v1.txt').
        :return: Очищенный от лишних пробелов текст проンプта.
        """
        path = PROMPTS_DIR / prompt_file

        if not path.exists():
            available = [f.name for f in PROMPTS_DIR.glob("*.txt")]
            raise FileNotFoundError(
                f"Файл промпта '{prompt_file}' не найден. Доступные: {available}"
            )

        return path.read_text(encoding="utf-8").strip()

    @staticmethod
    def load_regions(names: Union[List[str], Literal["all"]]) -> Dict[str, List[str]]:
        """
        Загружает справочник регионов и возвращает список каналов ТЕЛЕГРАМ.
        """
        with open(REGIONS_FILE, encoding="utf-8") as f:
            raw_data = json.load(f)

        # Пересобираем данные во внутренний словарь для удобного поиска
        # Из [{name: ..., social_media: {telegram: ...}}] -> {name: [telegram]}
        registry = {}
        for item in raw_data:
            region_name = item.get("name")
            # Безопасно достаем telegram, проваливаясь во вложенный словарь
            tg_channel = item.get("social_media", {}).get("telegram")

            if region_name and tg_channel:
                # Оборачиваем в список [tg_channel], так как логика скрапера
                # обычно ожидает список (один регион — много каналов)
                registry[region_name] = [tg_channel]

        # Определяем, какие регионы нам нужны
        if names == "all":
            selected_names = list(registry.keys())
        else:
            selected_names = names

        # Проверка на наличие запрошенных регионов в базе
        unknown = [n for n in selected_names if n not in registry]
        if unknown:
            available_list = ", ".join(list(registry.keys())[:5])  # Показываем первые 5 для примера
            raise ValueError(
                f"Неизвестные регионы: {unknown}. \n"
                f"Доступные в JSON (всего {len(registry)}): {available_list}..."
            )

        # Возвращаем { "Белгородская область": ["@minsoctrud"], ... }
        return {name: registry[name] for name in selected_names}