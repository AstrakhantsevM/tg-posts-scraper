"""
Схема пресета. Только данные, никакой логики.
Обновлено: удален posts_limit, добавлена дата отсечки (stop_date).
"""
from typing import List, Literal, Union, Optional
from datetime import date
from pydantic import BaseModel, Field


class PipelineConfig(BaseModel):
    """
    Настройки конвейера обработки.
    Контролируют нагрузку на систему и API.
    """
    # Сколько задач выполняем одновременно (параллелизм)
    max_concurrent_tasks: int = Field(default=2, ge=1, le=10)

    # Размер чанка текста для обработки (важно для лимитов контекстного окна LLM)
    max_chars_per_batch: int = Field(default=10_000, ge=500)

    # Сколько раз пытаемся перезапустить упавшую задачу
    max_retries: int = Field(default=3, ge=1)


class ScraperConfig(BaseModel):
    """
    Настройки сбора данных.
    Теперь без лимита постов, фокус на временных рамках.
    """
    # Дата, до которой нужно собирать посты (включительно).
    # Если None, можно собирать за всё время (зависит от логики парсера).
    stop_date: Optional[date] = Field(
        default=None,
        description="Дата отсечки: парсер собирает посты не старее этой даты"
    )

    # Опционально оставляем глубину в днях как альтернативу,
    # если stop_date не задана вручную
    days_back: int = Field(default=7, ge=1)


class OutputConfig(BaseModel):
    """
    Настройки экспорта.
    """
    # Нужно ли записывать данные в файл
    save_to_file: bool = True

    # Путь сохранения. Переменные в скобках подставляются кодом.
    filename_template: str = "results/{preset_name}/{date}/{region}.json"


class PresetConfig(BaseModel):
    """
    Итоговая конфигурация пресета.
    Служит единственным источником правды для запуска задачи.
    """
    # Название задачи (например, 'competitor_analysis')
    name: str

    # Описание для логов или UI
    description: str = ""

    # География поиска: список кодов регионов или 'all' для глобального поиска
    regions: Union[List[str], Literal["all"]]

    # Имя файла инструкции в папке /prompts/ (например, 'analyze_v1.txt')
    prompt_file: str

    # Вложенные объекты конфигурации
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    scraper: ScraperConfig = Field(default_factory=ScraperConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)