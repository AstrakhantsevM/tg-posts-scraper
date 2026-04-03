import asyncio
import logging
from typing import Any, List, Optional
from modules.inference_pool import InferencePool
from modules.data_batcher import DataBatcher

logger = logging.getLogger(__name__)

# Максимальная глубина рекурсии для защиты от бесконечного цикла
MAX_REDUCE_DEPTH = 10

class PipelineManager:
    """
    Оркестратор пайплайна обработки данных (Паттерн Map-Reduce).

    Реализует по-настоящему иерархическое (рекурсивное) саммари:

    ┌─────────────────────────────────────────────────────────────────┐
    │  Входные данные (N постов)                                      │
    │       │                                                         │
    │  ┌────▼────┐  ┌─────────┐  ┌─────────┐                          │
    │  │ Батч 1  │  │ Батч 2  │  │ Батч 3  │  ← MAP: параллельно      │
    │  └────┬────┘  └────┬────┘  └────┬────┘                          │
    │       │            │            │                               │
    │  ┌────▼────┐  ┌────▼────┐  ┌───▼─────┐                          │
    │  │ Ответ 1 │  │ Ответ 2 │  │ Ответ 3 │                          │
    │  └────┬────┘  └────┬────┘  └───┬─────┘                          │
    │       └────────────┴───────────┘                                │
    │                    │                                            │
    │             ┌──────▼──────┐                                     │
    │             │  Слишком    │  Если суммарный объём               │
    │             │  большой?   │  ответов превышает лимит →          │
    │             └──────┬──────┘  снова батчим и редьюсим!           │
    │                    │                                            │
    │          ┌─────────┴─────────┐                                  │
    │     ┌────▼────┐         ┌────▼────┐  ← REDUCE уровень 1         │
    │     │ Мини-   │         │ Мини-   │                             │
    │     │ саммари │         │ саммари │                             │
    │     └────┬────┘         └────┬────┘                             │
    │          └─────────┬─────────┘                                  │
    │                    │                                            │
    │             ┌──────▼──────┐  ← REDUCE уровень 2                 │
    │             │   ИТОГОВЫЙ  │                                     │
    │             │    ОТВЕТ    │                                     │
    │             └─────────────┘                                     │
    └─────────────────────────────────────────────────────────────────┘

    DataBatcher используется на каждом уровне рекурсии, что гарантирует,
    что ни один запрос к LLM никогда не превысит лимит символов.
    """

    def __init__(
            self,
            agents: List[Any],
            max_concurrent_tasks: int = 2,
            max_retries: int = 3,
            max_chars_per_batch: int = 10_000
    ):
        """
        Инициализация конвейера обработки.

        :param agents:               Список инициализированных агентов для пула.
        :param max_concurrent_tasks: Лимит одновременных задач к API.
        :param max_retries:          Количество попыток при сетевой/API ошибке.
        :param max_chars_per_batch:  Максимальное количество символов в одном батче.
                                     Применяется как к исходным данным (MAP),
                                     так и к промежуточным ответам (каждый REDUCE-уровень).
        """
        # Пул инференса — управляет параллельными запросами к LLM через семафор
        self.pool = InferencePool(
            agents=agents,
            max_concurrent_tasks=max_concurrent_tasks,
            max_retries=max_retries
        )

        # Батчер — нарезает списки строк на группы, не превышающие лимит символов.
        # Один экземпляр переиспользуется на всех уровнях иерархии.
        self.batcher = DataBatcher(max_chars_per_batch=max_chars_per_batch)

        logger.info(
            f"PipelineManager инициализирован. "
            f"Лимит батча: {max_chars_per_batch} символов, "
            f"параллельность: {max_concurrent_tasks}, "
            f"макс. глубина редьюса: {MAX_REDUCE_DEPTH}."
        )

    # ──────────────────────────────────────────────────────────────────────────
    # ПУБЛИЧНЫЙ API
    # ──────────────────────────────────────────────────────────────────────────

    async def run(self, prompt: str, data: List[str]) -> Optional[str]:
        """
        Главный метод запуска полного пайплайна Map → Reduce*.

        Алгоритм:
          1. [MAP]    Нарезать исходные данные на батчи и обработать параллельно.
          2. [REDUCE] Рекурсивно сводить промежуточные ответы до единого результата.
                      Каждый уровень редьюса сам батчится, если объём слишком велик.

        :param prompt: Исходный вопрос/задание пользователя.
        :param data:   Список сырых текстов для обработки.
        :return:       Итоговая строка-ответ или None при полном сбое.
        """
        logger.info(f"[RUN] Старт обработки {len(data)} элементов.")

        # ── ШАГ 1: MAP ────────────────────────────────────────────────────────
        # Получаем список первичных ответов — по одному на каждый батч
        map_results = await self._map_phase(prompt=prompt, data=data)

        # Если ни один батч не обработался — возвращаем None
        if not map_results:
            logger.error("[RUN] MAP-фаза не вернула ни одного результата.")
            return None

        logger.info(f"[RUN] MAP завершён, получено {len(map_results)} промежуточных ответов.")

        # ── ШАГ 2: REDUCE (рекурсивный) ───────────────────────────────────────
        # Передаём промежуточные ответы в рекурсивный редьюсер.
        # Он сам разберётся, нужна ли ему ещё одна итерация.
        final_result = await self._hierarchical_reduce(
            original_prompt=prompt,
            intermediate_results=map_results,
            depth=0
        )

        logger.info("[RUN] Пайплайн успешно завершён.")
        return final_result

    # ──────────────────────────────────────────────────────────────────────────
    # ВНУТРЕННИЕ МЕТОДЫ
    # ──────────────────────────────────────────────────────────────────────────

    async def _map_phase(self, prompt: str, data: List[str]) -> List[str]:
        """
        Фаза MAP: параллельная первичная обработка исходных данных.

        Алгоритм:
          1. Нарезать `data` на батчи по лимиту символов.
          2. Для каждого батча создать задачу pool.execute().
          3. Запустить все задачи параллельно (asyncio.gather).
          4. Отфильтровать ошибки и пустые ответы.

        :param prompt: Промпт пользователя (одинаковый для всех батчей).
        :param data:   Исходный список строк для обработки.
        :return:       Список строк-ответов (только успешные).
        """
        # Нарезаем входной список на батчи, не превышающие max_chars_per_batch
        batches = self.batcher.create_batches(data)
        logger.info(f"[MAP] Данные нарезаны на {len(batches)} батчей.")

        if not batches:
            logger.warning("[MAP] Батчей не создано — исходные данные пусты.")
            return []

        # Формируем список корутин для параллельного запуска
        tasks = [
            self.pool.execute(prompt=prompt, data=batch)
            for batch in batches
        ]

        # Запускаем параллельно. return_exceptions=True — ошибка в одной задаче
        # не обрушивает остальные; ошибки придут как объекты Exception в списке.
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Фильтруем: пропускаем исключения и None-ответы
        valid: List[str] = []
        for idx, result in enumerate(raw_results):
            if isinstance(result, Exception):
                # Логируем, но не прерываем — остальные батчи продолжат работу
                logger.error(f"[MAP] Батч #{idx + 1} завершился с ошибкой: {result}")
            elif result is not None:
                valid.append(result)
            else:
                logger.warning(f"[MAP] Батч #{idx + 1} вернул пустой ответ (None).")

        logger.info(f"[MAP] Успешно обработано {len(valid)} из {len(batches)} батчей.")
        return valid

    async def _hierarchical_reduce(
            self,
            original_prompt: str,
            intermediate_results: List[str],
            depth: int
    ) -> Optional[str]:
        """
        Рекурсивная фаза REDUCE: сворачивает список промежуточных ответов в один.

        Ключевая идея: перед отправкой в LLM промежуточные ответы сами
        прогоняются через DataBatcher. Если они не влезают в один батч —
        каждый батч сжимается отдельно, а результаты снова поступают
        в эту же функцию (следующий уровень рекурсии).

        Дерево вызовов (пример с 9 ответами, лимит = 3):
          depth=0: [A,B,C,D,E,F,G,H,I] → батчи [A,B,C], [D,E,F], [G,H,I]
                    → reduce(ABC), reduce(DEF), reduce(GHI) → [R1, R2, R3]
          depth=1: [R1, R2, R3] → один батч → финальный ответ ✓

        :param original_prompt:      Исходный промпт пользователя (для контекста в reduce-промпте).
        :param intermediate_results: Список строк, которые нужно свести в одну.
        :param depth:                Текущая глубина рекурсии (защита от бесконечного цикла).
        :return:                     Единая строка-результат или None при полном сбое.
        """
        # ── Граничные условия ─────────────────────────────────────────────────

        # База рекурсии: единственный элемент — он и есть итог
        if len(intermediate_results) == 1:
            logger.info(f"[REDUCE][depth={depth}] Остался 1 элемент — возвращаем напрямую.")
            return intermediate_results[0]

        # Защита от бесконечной рекурсии при непредвиденных ситуациях
        if depth >= MAX_REDUCE_DEPTH:
            logger.critical(
                f"[REDUCE] Достигнута максимальная глубина рекурсии ({MAX_REDUCE_DEPTH}). "
                f"Принудительное слияние {len(intermediate_results)} оставшихся ответов в один запрос."
            )
            # Аварийный выход: объединяем всё что есть в один запрос без батчинга
            return await self._execute_reduce_call(
                original_prompt=original_prompt,
                results_to_merge=intermediate_results,
                depth=depth
            )

        logger.info(
            f"[REDUCE][depth={depth}] Начало свёртки {len(intermediate_results)} элементов."
        )

        # ── Батчинг промежуточных результатов ────────────────────────────────
        # Прогоняем промежуточные ответы через тот же батчер.
        # Это и есть ключевое место: если суммарный объём ответов велик —
        # они будут разбиты на несколько групп для параллельного редьюса.
        reduce_batches = self.batcher.create_batches(intermediate_results)

        logger.info(
            f"[REDUCE][depth={depth}] Промежуточные ответы нарезаны "
            f"на {len(reduce_batches)} батч(ей)."
        )

        if len(reduce_batches) == 1:
            # ── Всё влезло в один батч — делаем один финальный вызов ──────────
            logger.info(f"[REDUCE][depth={depth}] Один батч — финальный вызов LLM.")
            return await self._execute_reduce_call(
                original_prompt=original_prompt,
                results_to_merge=intermediate_results,
                depth=depth
            )

        # ── Несколько батчей — параллельный редьюс на текущем уровне ─────────
        logger.info(
            f"[REDUCE][depth={depth}] {len(reduce_batches)} батчей — "
            f"запускаем параллельный reduce для каждой группы."
        )

        # Создаём задачи: каждый батч промежуточных ответов сжимается отдельно
        group_tasks = [
            self._execute_reduce_call(
                original_prompt=original_prompt,
                results_to_merge=batch_of_results,
                depth=depth
            )
            for batch_of_results in reduce_batches
        ]

        # Параллельно сжимаем все группы
        group_raw = await asyncio.gather(*group_tasks, return_exceptions=True)

        # Фильтруем ошибки в результатах групповых вызовов
        group_valid: List[str] = []
        for idx, res in enumerate(group_raw):
            if isinstance(res, Exception):
                logger.error(
                    f"[REDUCE][depth={depth}] Группа #{idx + 1} упала с ошибкой: {res}"
                )
            elif res is not None:
                group_valid.append(res)
            else:
                logger.warning(
                    f"[REDUCE][depth={depth}] Группа #{idx + 1} вернула пустой ответ."
                )

        if not group_valid:
            logger.error(f"[REDUCE][depth={depth}] Все группы завершились с ошибкой.")
            return None

        logger.info(
            f"[REDUCE][depth={depth}] Получено {len(group_valid)} групповых ответов. "
            f"Переходим на следующий уровень рекурсии."
        )

        # ── Рекурсивный вызов на уровень ниже ────────────────────────────────
        # Групповые ответы сами могут быть велики → снова через батчер
        return await self._hierarchical_reduce(
            original_prompt=original_prompt,
            intermediate_results=group_valid,
            depth=depth + 1  # увеличиваем счётчик глубины
        )

    async def _execute_reduce_call(
            self,
            original_prompt: str,
            results_to_merge: List[str],
            depth: int
    ) -> Optional[str]:
        """
        Атомарный вызов LLM для слияния группы промежуточных ответов.

        Формирует специальный reduce-промпт, который:
          - Напоминает модели исходный вопрос пользователя.
          - Передаёт пронумерованные промежуточные ответы.
          - Просит составить единый структурированный итог.

        :param original_prompt:   Исходное задание пользователя (для контекста).
        :param results_to_merge:  Список строк-ответов, которые нужно слить в один.
        :param depth:             Глубина рекурсии (используется только для логов).
        :return:                  Строка-результат от LLM или None при ошибке.
        """
        logger.debug(
            f"[REDUCE][depth={depth}] _execute_reduce_call: "
            f"сливаем {len(results_to_merge)} ответов."
        )

        # Формируем промпт для reduce-агента динамически,
        # вставляя исходный вопрос и пронумерованные части
        reduce_prompt = (
            f"Ты — ИИ-агрегатор. Твоя задача — объединить несколько промежуточных ответов "
            f"в один связный итог.\n\n"
            f"ИСХОДНЫЙ ВОПРОС ПОЛЬЗОВАТЕЛЯ:\n«{original_prompt}»\n\n"
            f"ПРОМЕЖУТОЧНЫЕ ОТВЕТЫ ДЛЯ ОБЪЕДИНЕНИЯ:\n"
            # Каждый ответ нумеруется для наглядности
            + "\n\n".join(
                f"--- Ответ {i + 1} ---\n{result}"
                for i, result in enumerate(results_to_merge)
            )
            + "\n\nПРАВИЛА:\n"
            f"1. Удали дублирующуюся информацию.\n"
            f"2. Сохрани все уникальные факты и выводы.\n"
            f"3. Составь единый структурированный ответ на исходный вопрос.\n"
            f"4. Не добавляй информацию, которой нет в промежуточных ответах."
        )

        # Передаём сформированный промпт в пул — он сам выберет свободного агента
        result = await self.pool.execute(prompt=reduce_prompt, data=results_to_merge)

        if result is None:
            logger.error(
                f"[REDUCE][depth={depth}] _execute_reduce_call вернул None "
                f"(пул исчерпал попытки)."
            )

        return result