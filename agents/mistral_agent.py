import time
import logging
from typing import List, Optional
from mistralai.client import Mistral

# Настройка логирования
logger = logging.getLogger(__name__)


class MistralAgent:
    """
    Универсальный агент для взаимодействия с моделями Mistral AI.
    Поддерживает автоматические повторы при ошибках и гибкую настройку параметров.
    """

    def __init__(
            self,
            api_key: str,
            model: str = "mistral-small-latest",
            timeout: int = 60,
            max_retries: int = 3,
            temperature: float = 0.1
    ):
        """
        Инициализация агента.

        :param api_key: Токен доступа Mistral API.
        :param model: Имя модели (например, mistral-large-latest или open-mistral-nemo).
        :param timeout: Время ожидания ответа в секундах.
        :param max_retries: Количество попыток при сбоях.
        :param temperature: Степень случайности ответа (0.1 для строгой аналитики).
        """
        self.client = Mistral(api_key=api_key)
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.temperature = temperature

    def process(self, prompt: str, data: List[str], system_instruction: Optional[str] = None) -> str:
        """
        Метод для обработки списка постов через Mistral AI.

        :param prompt: Основная инструкция (промпт) из файла.
        :param data: Список текстов (постов) для анализа.
        :param system_instruction: Системная роль или глобальная установка.
        :return: Очищенный текст ответа или описание ошибки.
        """

        # Формируем контент. Используем разделитель для структурирования входных данных.
        formatted_input = "\n---\n".join(data)
        user_content = f"{prompt}\n\nПОСТЫ ДЛЯ АНАЛИЗА:\n{formatted_input}"

        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})

        messages.append({"role": "user", "content": user_content})

        attempts = 0
        while attempts < self.max_retries:
            try:
                # Отправка запроса через официальный SDK
                response = self.client.chat.complete(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    # В SDK Mistral таймаут часто управляется на уровне клиента или вызова
                )

                # Возвращаем содержимое первого варианта ответа
                return response.choices[0].message.content.strip()

            except Exception as e:
                attempts += 1
                logger.warning(f"Attempt {attempts} failed for Mistral ({self.model}): {e}")

                if attempts < self.max_retries:
                    # Экспоненциальное ожидание между попытками
                    time.sleep(2 ** attempts)
                else:
                    logger.error(f"Mistral API error after {self.max_retries} attempts.")
                    raise RuntimeError(f"Mistral exhausted all retries. Last error: {e}")

        raise RuntimeError(f"Mistral had an unexpected error")