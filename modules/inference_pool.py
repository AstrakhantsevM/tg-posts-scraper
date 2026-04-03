import asyncio
import inspect
import logging
import uuid

from typing import List, Any, Optional

# Настраиваем логгер для модуля. Он поможет отслеживать, как запросы
# распределяются между агентами, где возникают ошибки и когда агенты выбывают.
logger = logging.getLogger(__name__)

class InferencePool:
    """
    Пул инференса (InferencePool) для управления запросами к ИИ-моделям.

    Главные задачи класса:
    1. Балансировка нагрузки (Round Robin) — поочередно отправляет запросы разным агентам.
    2. Контроль конкурентности (Semaphore) — не дает запустить больше N запросов одновременно.
    3. Отказоустойчивость (Retries) — автоматически повторяет запрос при падении API.
    4. Универсальность — умеет работать как с синхронными (def), так и с асинхронными (async def) агентами.
    5. Отбраковка (Circuit Breaker) — автоматически исключает агента из ротации, если он сломался окончательно.
    """

    def __init__(
            self,
            agents: List[Any],
            max_concurrent_tasks: int = 2,
            max_retries: int = 3,
            retry_delay: float = 15.0,
            max_agent_errors: int = 2
    ):
        """
        Инициализация пула.

        :param agents: Список объектов-агентов (MistralAgent, GroqAgent и т.д.).
                       У каждого агента ОБЯЗАТЕЛЬНО должен быть метод `process(prompt, data)`.
        :param max_concurrent_tasks: Максимальное количество одновременно выполняемых запросов.
        :param max_retries: Сколько раз пытаться выполнить запрос при ошибках API.
        :param retry_delay: Время ожидания (в секундах) перед повторной попыткой.
        :param max_agent_errors: Сколько раз ПОДРЯД агенту разрешено упасть, прежде чем
                                 он будет навсегда выкинут из пула (consequent_break).
        """
        # Базовая валидация входных данных
        if not agents:
            raise ValueError("Список агентов не может быть пустым. Передайте хотя бы одного агента.")

        # Проверяем, что у всех переданных агентов реализован нужный интерфейс (метод process)
        for i, agent in enumerate(agents):
            if not hasattr(agent, "process") or not callable(getattr(agent, "process")):
                raise TypeError(f"Агент на позиции {i} ({agent.__class__.__name__}) не имеет метода 'process'.")

        # Сохраняем настройки
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.max_agent_errors = max_agent_errors

        # Динамический список активных агентов. Мы ушли от `itertools.cycle`,
        # так как нам нужно уметь удалять из списка "мертвых" агентов на лету.
        self._active_agents = list(agents)

        # Словарь для отслеживания ошибок ПОДРЯД для каждого агента.
        # Используем id(agent) как ключ на случай, если в пуле несколько экземпляров одного класса.
        self._error_counts = {id(agent): 0 for agent in agents}

        # Семафор ограничивает количество корутин, которые могут одновременно
        # выполнять блок кода. Если лимит исчерпан, новые задачи будут ждать в очереди.
        self._semaphore = asyncio.Semaphore(max_concurrent_tasks)

        # Асинхронная блокировка (Lock) нужна, чтобы разные параллельные таски
        # не попытались одновременно изменить список `_active_agents` (состояние гонки).
        self._lock = asyncio.Lock()

        logger.info(
            f"🚀 InferencePool успешно инициализирован. "
            f"Агентов в пуле: {len(self._active_agents)} | "
            f"Лимит одновременных задач: {max_concurrent_tasks} | "
            f"Макс. попыток на батч: {self.max_retries} | "
            f"Лимит ошибок агента до исключения: {self.max_agent_errors}"
        )

    async def _get_next_agent(self):
        """
        Безопасно извлекает следующего агента по принципу ручного Round Robin.
        Берет первого агента из очереди и ставит его в конец списка.
        """
        async with self._lock:
            if not self._active_agents:
                # Если список пуст (все агенты сгорели), возвращаем None
                return None

            # Извлекаем первого агента в списке
            agent = self._active_agents.pop(0)
            # Сразу же возвращаем его в конец списка, имитируя бесконечный цикл
            self._active_agents.append(agent)
            return agent

    async def _remove_agent(self, agent: Any, agent_name: str):
        """
        Исключает навсегда агента из ротации, если он превысил лимит ошибок.
        """
        async with self._lock:
            # Проверяем, что агент еще в списке (чтобы другая таска не удалила его секундой раньше)
            if agent in self._active_agents:
                self._active_agents.remove(agent)
                logger.error(
                    f"💀 [CIRCUIT BREAKER] Агент {agent_name} исключен из пула! "
                    f"Он превысил лимит в {self.max_agent_errors} ошибок подряд. "
                    f"Осталось агентов в ротации: {len(self._active_agents)}"
                )

    async def execute(self, prompt: str, data: Optional[List[str]] = None) -> Optional[str]:
        """
        Выполняет запрос к ИИ-агенту с балансировкой нагрузки, автоматической
        ротацией (переключением на резерв) и отбраковкой нерабочих агентов.
        """
        task_id = str(uuid.uuid4())[:6]  # Генерируем короткий ID для конкретного батча (например: "a1b2c3")
        last_exception = None

        # Цикл повторных попыток для обработки конкретного батча данных.
        for attempt in range(1, self.max_retries + 1):

            # 1. ПОЛУЧЕНИЕ АГЕНТА И ПРОВЕРКА НА FAST FAIL
            current_agent = await self._get_next_agent()

            # Если `_get_next_agent` вернул None, значит все агенты были исключены из пула.
            # Прерываемся досрочно, нет смысла ждать retry_delay и делать новые попытки.
            if current_agent is None:
                logger.critical("🚨 В пуле не осталось живых агентов! Остановка обработки батча.")
                break

            # Пытаемся вытащить название модели из атрибутов агента.
            # (Обычно в библиотеках это хранится в .model, .model_name или .name)
            model_info = getattr(current_agent, "model", None) or getattr(current_agent, "model_name", None)

            # Если нашли имя модели, добавляем его в лог, иначе добавляем уникальный ID из памяти
            if model_info:
                agent_name = f"{current_agent.__class__.__name__}[{model_info}]"
            else:
                agent_name = f"{current_agent.__class__.__name__}[id:{id(current_agent) % 1000}]"

            agent_id = id(current_agent)

            # 2. Входим в семафор.
            # Ограничивает количество одновременных запросов к API.
            async with self._semaphore:
                logger.info(
                    f"[{task_id}] 🔄 [Попытка {attempt}/{self.max_retries}] "
                    f"Направляем запрос агенту: {agent_name}"
                )

                try:
                    process_method = current_agent.process

                    # 3. МАГИЯ АСИНХРОННОСТИ (Синхронные vs Асинхронные агенты)
                    if inspect.iscoroutinefunction(process_method):
                        result = await process_method(prompt=prompt, data=data)
                    else:
                        result = await asyncio.to_thread(process_method, prompt=prompt, data=data)

                    # 4. УСПЕХ И СБРОС СЧЕТЧИКА
                    # Если дошли сюда, значит модель отработала штатно.
                    # Важно: обнуляем счетчик ошибок именно для этого агента!
                    # Лимит `max_agent_errors` считается только для ошибок ПОДРЯД.
                    self._error_counts[agent_id] = 0
                    logger.debug(f"✅ Агент {agent_name} успешно обработал батч.")
                    return result

                except Exception as e:
                    # 5. ОБРАБОТКА ПАДЕНИЯ И ШТРАФНЫЕ БАЛЛЫ
                    last_exception = e

                    # Увеличиваем счетчик ошибок конкретного агента
                    self._error_counts[agent_id] += 1

                    logger.warning(
                        f"[{task_id}] ⚠️ Ошибка у агента {agent_name} "
                        f"(Сбой {self._error_counts[agent_id]} из {self.max_agent_errors} допустимых подряд): "
                        f"{str(e)[:200]}..."
                    )

                    # 6. ПРОВЕРКА НА ИСКЛЮЧЕНИЕ (CONSEQUENT BREAK)
                    if self._error_counts[agent_id] >= self.max_agent_errors:
                        # ДОБАВЛЕНО: передаем agent_name сюда
                        await self._remove_agent(current_agent, agent_name)

            # --- Конец блока with self._semaphore ---
            # Семафор освобожден! Другие батчи могут начать работу, пока мы спим.

            # 7. ПОДГОТОВКА К СЛЕДУЮЩЕЙ ПОПЫТКЕ
            if attempt < self.max_retries:
                # В следующей итерации цикла `_get_next_agent()` выдаст уже ДРУГОГО агента
                logger.info(f"⏳ Переключение... Ждем {self.retry_delay} сек. перед передачей задачи резервному агенту.")
                await asyncio.sleep(self.retry_delay)

        # 8. ФАТАЛЬНЫЙ СБОЙ БАТЧА
        # Сюда попадаем, если исчерпали `max_retries` или если `current_agent is None`
        # Этот батч должен уйти в Dead Letter Queue (DLQ), чтобы мы не потеряли данные.
        logger.error(
            f"❌ Полный отказ пайплайна. Все {self.max_retries} попытки исчерпаны, "
            f"либо в пуле кончились агенты. Последняя ошибка: {last_exception}"
        )
        return None