"""
modules/result_scanner.py
─────────────────────────
Слой данных мониторинга СМИ.

Содержит:
  · ScanStatus      — перечисление статусов региона
  · RegionResult    — датакласс одного результата
  · RawJsonParser   — извлекает канал/посты/дату из raw.json
  · ResultJsonParser— читает и нормализует result.json
  · AnswerCleaner   — убирает мусор из ответов ИИ
"""

import json
import re
import logging
from collections import Counter
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  МОДЕЛИ ДАННЫХ
# ══════════════════════════════════════════════════════════════════════════════

class ScanStatus(Enum):
    FOUND  = auto()   # есть значимые упоминания
    EMPTY  = auto()   # упоминаний нет
    FAILED = auto()   # ошибка обработки / файл отсутствует


@dataclass
class RegionResult:
    """Результат обработки одного региона."""
    region:      str
    status:      ScanStatus
    channel:     str = "—"
    answer:      str = ""
    error:       str = ""
    posts_count: int = 0
    scan_date:   str = ""


# ══════════════════════════════════════════════════════════════════════════════
#  ПАРСЕРЫ
# ══════════════════════════════════════════════════════════════════════════════

class RawJsonParser:
    """
    Читает raw.json и извлекает:
      · channel      — самый частый @handle из заголовков постов
      · posts_count  — количество постов (из meta или len(posts))
      · scan_date    — дата сканирования из meta
    """

    # Паттерн заголовка поста:  [@channel_name | 2026-04-02]
    _POST_HEADER = re.compile(r"\[@([^\s|]+)\s*\|")

    @classmethod
    def parse(cls, raw_file: Path, posts_scan_limit: int = 200) -> dict:
        """
        Возвращает dict с ключами: channel, posts_count, scan_date.
        При любой ошибке возвращает безопасные дефолты (не бросает исключений).
        """
        defaults = {"channel": "Неизвестный канал", "posts_count": 0, "scan_date": ""}

        if not raw_file.exists():
            log.debug("raw.json не найден: %s", raw_file)
            return defaults

        try:
            with raw_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            log.warning("Ошибка чтения %s: %s", raw_file, exc)
            return defaults

        result = dict(defaults)

        # meta
        meta = data.get("meta", {})
        result["scan_date"]   = meta.get("date", "")
        result["posts_count"] = data.get("total_posts", 0)

        # определяем канал по заголовкам постов
        posts = data.get("posts", [])
        if isinstance(posts, list) and posts:
            sample = " ".join(posts[:posts_scan_limit])
            handles = cls._POST_HEADER.findall(sample)
            if handles:
                most_common, _ = Counter(handles).most_common(1)[0]
                result["channel"] = f"@{most_common}"
            if not result["posts_count"]:
                result["posts_count"] = len(posts)

        return result


class ResultJsonParser:
    """Читает result.json и нормализует ответ ИИ."""

    # Ответы, которые считаем «нет упоминаний»
    _NEGATIVE_ANSWERS = frozenset({
        "нет", "no", "не найдено", "не обнаружено", "отсутствует",
        "упоминания отсутствуют", "не упоминается",
    })

    @staticmethod
    def parse(result_file: Path) -> Optional[str]:
        """
        Возвращает текстовый ответ или None при ошибке/отсутствии файла.
        """
        if not result_file.exists():
            log.debug("result.json не найден: %s", result_file)
            return None
        try:
            with result_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            answer = data if isinstance(data, str) else data.get("result")
            return str(answer).strip() if answer is not None else None
        except Exception as exc:
            log.warning("Ошибка парсинга %s: %s", result_file, exc)
            return None

    @classmethod
    def is_negative(cls, answer: str) -> bool:
        """True, если ответ означает «упоминаний нет»."""
        normalized = answer.strip().lower().rstrip(".!…")
        return normalized in cls._NEGATIVE_ANSWERS


# ══════════════════════════════════════════════════════════════════════════════
#  ОЧИСТИТЕЛЬ ОТВЕТОВ
# ══════════════════════════════════════════════════════════════════════════════

class AnswerCleaner:
    """
    Убирает из текста ответа ИИ типичный мусор:
      · строки-«нет» в виде маркированного списка  (- Нет. / • нет / и т.п.)
      · «голые» маркеры без текста                 (просто «- »)
      · markdown-разметку                          (**bold**, *italic*)
      · серии пустых строк                         (схлопываем в одну)
      · дублирующиеся абзацы                       (ИИ иногда повторяет блок)
    """

    _JUNK_LINE = re.compile(
        r"^\s*"
        r"(?:[-–—•*]\s*)?"
        r"(?:нет|no|не найдено|не обнаружено|отсутствует|упоминания отсутствуют)"
        r"[\s.!…]*$",
        re.IGNORECASE,
    )
    _BARE_BULLET  = re.compile(r"^\s*[-–—•]\s*$")
    _MD_BOLD      = re.compile(r"\*{1,2}(.*?)\*{1,2}")

    @classmethod
    def clean(cls, text: str) -> str:
        # 1. Снимаем markdown
        text = cls._MD_BOLD.sub(r"\1", text)

        # 2. Фильтруем строки
        cleaned: list[str] = []
        for line in text.splitlines():
            if cls._JUNK_LINE.match(line):
                continue
            if cls._BARE_BULLET.match(line):
                continue
            cleaned.append(line)

        # 3. Схлопываем серии пустых строк
        result_lines: list[str] = []
        prev_blank = False
        for line in cleaned:
            is_blank = not line.strip()
            if is_blank and prev_blank:
                continue
            result_lines.append(line)
            prev_blank = is_blank

        # 4. Дедупликация абзацев
        paragraphs = "\n".join(result_lines).split("\n\n")
        seen: list[str] = []
        for para in paragraphs:
            key = re.sub(r"\s+", " ", para.strip())
            if key and key not in seen:
                seen.append(key)

        return "\n\n".join(seen).strip()
