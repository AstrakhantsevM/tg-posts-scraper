import time
import logging
from typing import List, Optional
from openai import OpenAI  # OpenRouter использует стандартный клиент OpenAI

# Настройка логирования
logger = logging.getLogger(__name__)

class OpenRouterAgent:
    """
    Агент для работы с OpenRouter.
    Поддерживает любые модели (включая бесплатные :free).
    """

    def __init__(
            self,
            api_key: str,
            # По умолчанию ставим одну из лучших бесплатных моделей
            model: str = "meta-llama/llama-3.1-8b-instruct:free",
            timeout: int = 60,  # Для OpenRouter лучше ставить 60+, т.к. бесплатные модели бывают в очереди
            max_retries: int = 3
    ):
        """
        Инициализация агента.
        """
        # Указываем специальный base_url для OpenRouter
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    def process(self, prompt: str, data: List[str], system_instruction: Optional[str] = None) -> str:
        """
        Метод отправки запроса, полностью совместимый с логикой вашего GroqAgent.
        """

        # Формируем входные данные
        formatted_input = "\n---\n".join(data)
        user_content = f"{prompt}\n\nПОСТЫ ДЛЯ АНАЛИЗА:\n{formatted_input}"

        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})

        messages.append({"role": "user", "content": user_content})

        attempts = 0
        while attempts < self.max_retries:
            try:
                # В OpenRouter можно передавать доп. заголовки для рейтинга вашего приложения
                response = self.client.chat.completions.create(
                    extra_headers={
                        "HTTP-Referer": "https://localhost", # Для OpenRouter (необязательно)
                        "X-Title": "News Summarizer",        # Для OpenRouter (необязательно)
                    },
                    messages=messages,
                    model=self.model,
                    timeout=self.timeout
                )

                # Извлекаем результат
                return response.choices[0].message.content.strip()

            except Exception as e:
                attempts += 1
                logger.warning(f"Attempt {attempts} failed for OpenRouter model {self.model}: {e}")

                if attempts < self.max_retries:
                    # Экспоненциальная задержка
                    time.sleep(2 ** attempts)
                else:
                    logger.error(f"All {self.max_retries} attempts failed on OpenRouter.")
                    raise RuntimeError(f"OpenRouterAgent exhausted all retries. Last error: {e}")

        raise RuntimeError(f"OpenRouterAgent had an unexpected error")