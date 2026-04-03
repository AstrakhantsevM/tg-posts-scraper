import time
import logging
from typing import List, Optional
from groq import Groq

# Настройка логирования для отслеживания запросов в консоли PyCharm
logger = logging.getLogger(__name__)

class GroqAgent:
    """
    Универсальный агент для взаимодействия с моделями через API Groq.
    Реализует логику повторных попыток (retries) и управление контекстом.
    """

    def __init__(
            self,
            api_key: str,
            model: str = "llama-3.1-8b-instant",
            timeout: int = 30,
            max_retries: int = 3
    ):
        """
        Инициализация агента. 
        Все параметры передаются извне, никакого хардкода внутри класса.
        """
        self.client = Groq(api_key=api_key)
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    def process(self, prompt: str, data: List[str], system_instruction: Optional[str] = None) -> str:
        """
        Основной метод отправки запроса.

        :param prompt: Инструкция для ИИ (берется из файла в папке prompts)
        :param data: Список постов для анализа
        :param system_instruction: Опциональная системная установка (role)
        :return: Ответ от ИИ или сообщение об ошибке
        """

        # Формируем тело сообщения. 
        # Разделяем посты четким сепаратором для лучшего понимания моделью.
        formatted_input = "\n---\n".join(data)
        user_content = f"{prompt}\n\nПОСТЫ ДЛЯ АНАЛИЗА:\n{formatted_input}"

        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})

        messages.append({"role": "user", "content": user_content})

        attempts = 0
        while attempts < self.max_retries:
            try:
                # Выполняем запрос к API
                response = self.client.chat.completions.create(
                    messages=messages,
                    model=self.model,
                    timeout=self.timeout
                )

                # Извлекаем результат
                return response.choices[0].message.content.strip()

            except Exception as e:
                attempts += 1
                logger.warning(f"Attempt {attempts} failed for model {self.model}: {e}")

                if attempts < self.max_retries:
                    # Экспоненциальная задержка: чем больше ошибок, тем дольше ждем
                    time.sleep(2 ** attempts)
                else:
                    logger.error(f"All {self.max_retries} attempts failed.")
                    raise RuntimeError(f"Grok exhausted all retries. Last error: {e}")

        raise RuntimeError(f"Grok had an unexpected error")