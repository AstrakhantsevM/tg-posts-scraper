import os
import asyncio
import logging
from itertools import islice

# Импортируем наши классы
from agents.openrouter_agent import OpenRouterAgent
from agents.mistral_agent import MistralAgent
from agents.groq_agent import GroqAgent
from modules.pipeline import PipelineManager
from modules.result_saver import ResultSaver
from modules.telegram_scraper import TelegramScraper

# Инициализируем настройки
from configs.settings import settings
from modules.preset_loader import PresetLoader

# Базовая настройка логирования, чтобы видеть красивые логи в консоли
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


async def main():

    # 0. Инициализируем сценарий
    preset = PresetLoader.load('honor_guardian_scan_2026')
    prompt = PresetLoader.load_prompt(preset.prompt_file)

    # Получаем словарь { "Регион": ["@канал1", "@канал2"] }
    regions_data = PresetLoader.load_regions(preset.regions)

    # 1. Инициализируем агентов (например, 1 Mistral и 1 Groq)
    mistral_key = settings.api.mistral_key.get_secret_value()
    openrouter_key = settings.api.openrouter_key.get_secret_value()
    groq_key_1 = settings.api.groq_main.get_secret_value()
    groq_key_2 = settings.api.groq_reserve.get_secret_value()

    agents_list = [
        # --- СЛОЙ 1: ГРЕЙДЕРЫ (Максимальная скорость, Groq Key 1) ---
        # Эти модели первыми принимают удар. Они выдают результат почти мгновенно.
        GroqAgent(api_key=groq_key_1, model="llama-3.1-8b-instant", max_retries=1),

        # --- СЛОЙ 2: ДУБЛЕРЫ (Обход лимитов, Groq Key 2) ---
        # Если на первом ключе кончились токены в минуту (TPM), пробуем второй ключ.
        GroqAgent(api_key=groq_key_2, model="llama-3.1-8b-instant", max_retries=1),

        # --- СЛОЙ 3: СТАБИЛЬНЫЙ РЕЗЕРВ (Mistral) ---
        # Если Groq «захлебнулся», Mistral — самый надежный бесплатный эндпоинт.
        # Он работает медленнее, но почти никогда не выдает 404.
        MistralAgent(api_key=mistral_key, max_retries=2),

        # --- СЛОЙ 4: КАЧЕСТВЕННЫЙ БЕСПЛАТНЫЙ OPENROUTER ---
        # Здесь модели умнее, но могут быть очереди или 404 ошибки.
        #OpenRouterAgent(api_key=openrouter_key, model="google/gemini-flash-1.5-8b:free", max_retries=1),
        #OpenRouterAgent(api_key=openrouter_key, model="qwen/qwen-2.5-7b-instruct:free", max_retries=1),
        #OpenRouterAgent(api_key=openrouter_key, model="stepfun/step-3.5-flash:free", max_retries=1),

        # --- СЛОЙ 5: ЭКСПЕРИМЕНТАЛЬНЫЙ OPENROUTER (Высокое качество, риск 404) ---
        # Эти модели — топ по интеллекту, но часто перегружены.
        # Пробуем по одному разу: если эндпоинт занят, сразу летим дальше.
        #OpenRouterAgent(api_key=openrouter_key, model="qwen/qwen3.6-plus-preview:free", max_retries=1),
        #OpenRouterAgent(api_key=openrouter_key, model="z-ai/glm-4.5-air:free", max_retries=1),
        #OpenRouterAgent(api_key=openrouter_key, model="nvidia/nemotron-3-super-120b-a12b:free", max_retries=1),
        #OpenRouterAgent(api_key=openrouter_key, model="arcee-ai/trinity-large-preview:free", max_retries=1),

        # --- СЛОЙ 6: ПОТЕНЦИАЛЬНЫЕ GROQ (Низкие лимиты, высокое качество) ---
        # У Llama-4 и Safeguard на бесплатном тире очень мало запросов (RPM).
        # Чередуем ключи, чтобы выжать максимум из квот.
        GroqAgent(api_key=groq_key_1, model="meta-llama/llama-4-scout-17b-16e-instruct", max_retries=1),
        GroqAgent(api_key=groq_key_2, model="meta-llama/llama-4-scout-17b-16e-instruct", max_retries=1),

        GroqAgent(api_key=groq_key_1, model="openai/gpt-oss-safeguard-20b", max_retries=1),
        GroqAgent(api_key=groq_key_2, model="openai/gpt-oss-safeguard-20b", max_retries=1),

        # --- СЛОЙ 7: ГЛОБАЛЬНЫЙ ФОЛБЕК (Последний рубеж) ---
        # Если вообще ничего не сработало, отдаем на откуп автоматике OpenRouter.
        # Она сама найдет любую живую "затычку", чтобы батч не потерялся.
        #OpenRouterAgent(api_key=openrouter_key, model="openrouter/free", max_retries=1),

    ]

    # 2. Инициализация менеджера (данные берем из пресета!)
    pipeline = PipelineManager(
        agents=agents_list,
        max_concurrent_tasks=preset.pipeline.max_concurrent_tasks,
        max_chars_per_batch=preset.pipeline.max_chars_per_batch,
        max_retries=len(agents_list) + 1
    )

    saver = ResultSaver(preset)

    # =====================================================
    # 3. Собираем посты
    # =====================================================

    # Словарь для хранения постов в оперативной памяти (для текущего прогона)
    all_regions_posts = {}

    async with TelegramScraper(
            session_path=settings.tg.session_path,
            api_id=settings.tg.api_id,
            api_hash=settings.tg.api_hash,
    ) as scraper:

        # 3. Цикл обработки
        for region_name, channels in regions_data.items():
            logging.info(f"Начинаю сбор данных для региона: {region_name}")

            posts = await scraper.scrape_region(
                channels=channels,
                stop_date=preset.scraper.stop_date
            )

            if not posts:
                logging.warning(f"Нет новых постов для региона {region_name}")
                continue

            # Сохраняем сырые посты сразу
            saver.save_raw_posts(region_name, posts)

            # Сохраняем в память для следующего этапа
            all_regions_posts[region_name] = posts

        logging.info(f"✅ Сбор завершен. Всего регионов с данными: {len(all_regions_posts)}")

        # =====================================================
        # 4. Обрабатываем их ИИ
        # =====================================================

        final_report = {}

        for region_name, posts in all_regions_posts.items():
            logging.info(f"--- [AI АНАЛИЗ] Регион: {region_name} ({len(posts)} постов) ---")

            # Запускаем анализ
            summary = await pipeline.run(prompt=prompt, data=posts)

            # ДОБАВЛЕНА ПРОВЕРКА: Если вернулся None, значит пайплайн упал.
            if summary is None:
                logging.critical(
                    f"❌ [ОШИБКА ИИ] Пайплайн вернул None для {region_name}. Пропускаем сохранение, чтобы не записать null.")
                break  # Выходим из цикла регионов

            final_report[region_name] = summary

            # Сразу сохраняем готовый отчет по региону
            saver.save_result(region_name, summary)
            logging.info(f"✅ Результат для {region_name} сохранен.")

        logging.info("🎉 Обработка всех регионов завершена!")


if __name__ == "__main__":
    asyncio.run(main())