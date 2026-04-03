"""
ResultSaver — сохраняет на диск два типа данных для каждого региона:
  1. Сырые посты (raw)    — то что спарсилось из Telegram
  2. Результат анализа (result) — ответ LLM после пайплайна

Структура на диске:
  data/
    weekly_summary/
      2026-04-02/
        Москва/
          raw.json       ← сырые посты
          result.json    ← ответ LLM
        Татарстан/
          raw.json
          result.json
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from configs.preset_schema import PresetConfig

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent


class ResultSaver:

    def __init__(self, preset: PresetConfig):
        """
        :param preset: Пресет — имя, дата, формат берутся отсюда.
        """
        self.preset = preset
        # Фиксируем дату один раз — все файлы одного запуска имеют одну дату
        self._run_date = datetime.now().strftime("%Y-%m-%d")
        # Корневая папка для всего запуска: results/{preset}/{date}/
        self._run_dir = (
            PROJECT_ROOT
            / "data"
            / preset.name
            / self._run_date
        )

    # ──────────────────────────────────────────────────────────────────────────
    # ПУБЛИЧНЫЙ API
    # ──────────────────────────────────────────────────────────────────────────

    def save_raw_posts(self, region: str, posts: List[str]) -> None:
        """
        Сохраняет сырые посты региона сразу после парсинга.
        Вызывается внутри цикла по регионам — ДО запуска пайплайна.

        :param region: Название региона.
        :param posts:  Список текстов постов.
        """
        if not posts:
            logger.warning(f"[SAVER] {region}: нет постов для сохранения.")
            return

        filepath = self._region_dir(region) / "raw.json"
        self._write_json(
            filepath=filepath,
            data={
                "meta": self._meta(region),
                "total_posts": len(posts),
                "posts": posts,
            }
        )
        logger.info(f"[SAVER] {region} → raw.json ({len(posts)} постов)")

    def save_result(self, region: str, result: Optional[str]) -> None:
        """
        Сохраняет результат анализа LLM для региона.
        Вызывается после pipeline.run().

        :param region: Название региона.
        :param result: Строка-ответ от LLM (или None при ошибке).
        """
        if result is None:
            logger.warning(f"[SAVER] {region}: результат пустой, пропускаем.")
            return

        filepath = self._region_dir(region) / "result.json"
        self._write_json(
            filepath=filepath,
            data={
                "meta": self._meta(region),
                "result": self._try_parse_json(result),
            }
        )
        logger.info(f"[SAVER] {region} → result.json")

    def save_all(
            self,
            report: Dict[str, Optional[str]],
            raw_posts: Dict[str, List[str]]
    ) -> None:
        """
        Удобный метод для сохранения всего отчёта разом —
        если нужно сохранить всё в конце, а не по ходу цикла.

        :param report:    {регион: ответ_LLM}
        :param raw_posts: {регион: [посты]}
        """
        for region in set(list(report) + list(raw_posts)):
            if region in raw_posts:
                self.save_raw_posts(region, raw_posts[region])
            if region in report:
                self.save_result(region, report[region])

    # ──────────────────────────────────────────────────────────────────────────
    # ВНУТРЕННИЕ МЕТОДЫ
    # ──────────────────────────────────────────────────────────────────────────

    def _region_dir(self, region: str) -> Path:
        """
        Возвращает папку для конкретного региона и создаёт её если нет.
        results/{preset}/{date}/{регион}/
        """
        path = self._run_dir / region.replace(" ", "_")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _meta(self, region: str) -> dict:
        """Стандартный блок метаданных, который идёт в каждый файл."""
        return {
            "preset":     self.preset.name,
            "region":     region,
            "date":       self._run_date,
            "prompt":     self.preset.prompt_file,
            "saved_at":   datetime.now().isoformat(timespec="seconds"),
        }

    @staticmethod
    def _write_json(filepath: Path, data: dict) -> None:
        """Пишет dict в JSON-файл с отступами."""
        filepath.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    @staticmethod
    def _try_parse_json(text: str):
        """
        Если LLM вернул валидный JSON-строку — распаковываем в dict.
        Иначе возвращаем как обычный текст.
        Это защищает от строки внутри строки в итоговом файле.
        """
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text