"""
TelegramScraper — асинхронный парсер постов из Telegram-каналов.

Ключевые отличия от старой версии:
  - Полностью асинхронный (async/await) — вписывается в пайплайн
  - Принимает список каналов и обходит их параллельно
  - Возвращает структурированные объекты Post вместо кортежей
  - Единая точка входа scrape_region() для использования в main.py
  - Graceful shutdown через context manager (async with)
  - Подробное логирование с статистикой по каждому каналу
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import List, Optional

from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
    FloodWaitError,
)
from telethon.tl.types import Message

logger = logging.getLogger(__name__)


@dataclass
class Post:
    """
    Структурированный пост из Telegram.
    Используем dataclass вместо кортежа — читаемо и расширяемо.
    """
    channel: str  # @username канала
    date: datetime  # дата публикации (UTC)
    text: str  # текст или подпись
    message_id: int  # ID сообщения (для дедупликации)
    views: Optional[int] = None  # просмотры (если доступны)

    def to_plain_text(self) -> str:
        """Возвращает строку для передачи в PipelineManager.data."""
        return f"[{self.channel} | {self.date.strftime('%Y-%m-%d')}]\n{self.text}"


@dataclass
class ScrapeResult:
    """Итог парсинга одного канала — данные + статистика."""
    channel: str
    posts: List[Post] = field(default_factory=list)
    error: Optional[str] = None  # сообщение об ошибке если канал недоступен

    @property
    def success(self) -> bool:
        return self.error is None


class TelegramScraper:
    """
    Асинхронный парсер Telegram-каналов.

    Использование (через context manager):

        async with TelegramScraper(session_path, api_id, api_hash) as scraper:
            posts = await scraper.scrape_region(
                channels=["@channel1", "@channel2"],
                stop_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                limit_per_channel=100,
            )
    """

    def __init__(self, session_path: str, api_id: int, api_hash: str):
        """
        :param session_path: Путь к файлу .session (без расширения).
        :param api_id:       Telegram API ID.
        :param api_hash:     Telegram API Hash.
        """
        self._client = TelegramClient(session_path, api_id, api_hash)

    # ──────────────────────────────────────────────────────────────────────────
    # CONTEXT MANAGER — гарантирует корректное подключение и отключение
    # ──────────────────────────────────────────────────────────────────────────

    async def __aenter__(self) -> "TelegramScraper":
        """Подключаемся при входе в async with."""
        await self._client.start()
        logger.info("[SCRAPER] Подключение к Telegram установлено.")
        return self

    async def __aexit__(self, *_) -> None:
        """Отключаемся при выходе из async with — даже если было исключение."""
        await self._client.disconnect()
        logger.info("[SCRAPER] Соединение с Telegram закрыто.")

    # ──────────────────────────────────────────────────────────────────────────
    # ПУБЛИЧНЫЙ API
    # ──────────────────────────────────────────────────────────────────────────

    async def scrape_region(
            self,
            channels: List[str],
            stop_date: datetime,
            limit_per_channel: int = 999,
    ) -> List[str]:
        """
        Парсит все каналы региона параллельно и возвращает плоский список
        текстов постов, готовых для передачи в PipelineManager.

        :param channels:           Список @username каналов.
        :param stop_date:          Не парсить посты старше этой даты.
        :param limit_per_channel:  Максимум постов с одного канала.
        :return:                   Плоский список строк для pipeline.run(data=...).
        """
        # Нормализуем дату — telethon требует timezone-aware datetime
        stop_date = self._ensure_utc(stop_date)

        logger.info(
            f"[SCRAPER] Начинаю парсинг {len(channels)} каналов. "
            f"Стоп-дата: {stop_date.date()}, лимит: {limit_per_channel}/канал."
        )

        # Запускаем парсинг всех каналов параллельно
        tasks = [
            self._scrape_channel(
                channel=ch,
                stop_date=stop_date,
                limit=limit_per_channel,
            )
            for ch in channels
        ]
        results: List[ScrapeResult] = await asyncio.gather(*tasks)

        # Логируем итоги по каждому каналу
        total_posts = 0
        for r in results:
            if r.success:
                logger.info(f"[SCRAPER]  ✅ {r.channel}: {len(r.posts)} постов")
                total_posts += len(r.posts)
            else:
                logger.warning(f"[SCRAPER]  ❌ {r.channel}: {r.error}")

        logger.info(f"[SCRAPER] Итого собрано постов: {total_posts}")

        # Собираем плоский список строк, сортируя по дате (новые первыми)
        all_posts = sorted(
            [post for r in results if r.success for post in r.posts],
            key=lambda p: p.date,
            reverse=True,
        )
        return [post.to_plain_text() for post in all_posts]

    # ──────────────────────────────────────────────────────────────────────────
    # ВНУТРЕННИЕ МЕТОДЫ
    # ──────────────────────────────────────────────────────────────────────────

    async def _scrape_channel(
            self,
            channel: str,
            stop_date: datetime,
            limit: int,
    ) -> ScrapeResult:
        """
        Парсит один канал с двойным уровнем защиты.
        Внешний try ловит ошибки доступа к каналу.
        Внутренний try ловит ошибки парсинга конкретных (битых) сообщений.
        """
        result = ScrapeResult(channel=channel)

        try:
            # 1. Инициализируем итератор сообщений
            # Мы не используем "async for", чтобы иметь контроль над каждой итерацией
            it = self._client.iter_messages(channel)

            while True:
                try:
                    # 2. Пытаемся получить следующее сообщение (тут может вылететь Constructor ID)
                    message = await it.__anext__()

                    # 3. Проверка даты (Telethon возвращает сообщения от новых к старым)
                    if message.date < stop_date:
                        logger.debug(f"[SCRAPER] {channel}: достигнута стоп-дата {stop_date}")
                        break

                    # 4. Извлекаем текст (обычный или подпись к фото/видео)
                    text = message.text or getattr(message, "caption", "") or ""

                    # Пропускаем пустые объекты (сервисные сообщения, стикеры без текста)
                    if not text.strip():
                        continue

                    # 5. Добавляем успешно прочитанный пост в результат
                    result.posts.append(Post(
                        channel=channel,
                        date=message.date,
                        text=text.strip(),
                        message_id=message.id,
                        views=getattr(message, "views", None),
                    ))

                    # 6. Проверка лимита количества постов
                    if len(result.posts) >= limit:
                        logger.debug(f"[SCRAPER] {channel}: достигнут лимит {limit} постов.")
                        break

                except StopAsyncIteration:
                    # Сообщения в канале закончились штатно
                    break

                except Exception as e:
                    # КЛЮЧЕВАЯ ПРАВКА: если конкретное сообщение вызвало ошибку
                    # (например, "Could not find a matching Constructor ID"),
                    # мы логируем её и ПРОДОЛЖАЕМ цикл, не бросая весь канал.
                    logger.warning(f"[SCRAPER] Пропущено битое сообщение в {channel}: {e}")
                    continue

        # Внешние ошибки (проблемы с самим каналом или доступом)
        except ChannelPrivateError:
            result.error = "Канал приватный или аккаунт не подписан"
        except (UsernameInvalidError, UsernameNotOccupiedError):
            result.error = "Канал не найден (неверный username)"
        except FloodWaitError as e:
            # Если Telegram наложил временное ограничение (Flood), ждем и повторяем
            logger.warning(f"[SCRAPER] FloodWait {e.seconds}s для {channel}. Ожидаем...")
            await asyncio.sleep(e.seconds)
            return await self._scrape_channel(channel, stop_date, limit)
        except Exception as e:
            # Критическая ошибка, которую не удалось обработать внутри цикла
            result.error = f"Критическая ошибка канала: {e}"

        return result

    def _ensure_utc(self, dt):
        """
        Приводит входящую дату к формату datetime с часовым поясом UTC.
        Работает и с date, и с datetime.
        """
        if dt is None:
            return None

        # Если пришла просто дата (date), превращаем её в datetime (начало дня)
        if isinstance(dt, date) and not isinstance(dt, datetime):
            dt = datetime.combine(dt, datetime.min.time())

        # Теперь, когда у нас точно datetime, проверяем часовой пояс
        if dt.tzinfo is None:
            # Если пояса нет, считаем, что это UTC
            return dt.replace(tzinfo=timezone.utc)

        # Если пояс есть, просто конвертируем в UTC
        return dt.astimezone(timezone.utc)