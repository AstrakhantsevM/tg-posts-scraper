"""
=============================================================================
Скрипт: Возобновление и точечная переобработка ИИ (Resume AI Inference)
=============================================================================

Описание:
Этот скрипт используется для "допрогона" (повторной обработки) сохраненных
данных без повторного обращения к Telegram API. Он полезен в случаях, когда:
- Часть регионов не обработалась из-за сбоев сети/лимитов API (отсутствует result.json).
- Нужно перепроверить регионы, где ИИ что-то "нашел" (ответ отличный от "Нет").
- Вы изменили промпт или состав агентов и хотите перегенерировать только
  содержательные ответы, не тратя время и деньги на "пустые" регионы.

Алгоритм работы:
1. Сканирует целевую директорию (например, `data/preset_name/YYYY-MM-DD`).
2. Проходит по всем вложенным папкам регионов.
3. Читает `raw.json` (сохраненные сырые посты из Telegram).
4. Проверяет `result.json`. Если файла нет, либо текст ответа не равен "Нет"
   (без учета регистра и точек) — регион добавляется в очередь на переобработку.
5. Запускает асинхронный PipelineManager только для выбранных регионов.
6. Напрямую перезаписывает (или создает) `result.json` с новыми результатами от LLM.
=============================================================================
"""

import sys
import json
import asyncio
import logging
from pathlib import Path

# Добавляем корневую папку в sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from agents.openrouter_agent import OpenRouterAgent
from agents.mistral_agent import MistralAgent
from agents.groq_agent import GroqAgent

from modules.preset_loader import PresetLoader
from modules.pipeline import PipelineManager
from configs.settings import settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


async def main():
    # =====================================================
    # 0. Определяем целевую папку и пресет
    # =====================================================

    target_dir_path = "data/honor_guardian_scan_2026/2026-04-02"
    target_dir = BASE_DIR / target_dir_path

    if not target_dir.exists() or not target_dir.is_dir():
        logging.error(f"❌ Целевая папка не найдена: {target_dir}")
        return

    # Извлекаем имя пресета (сценарий) из названия родительской папки
    preset_name = target_dir.parent.name
    logging.info(f"⚙️ Определен пресет (сценарий): {preset_name}")

    preset = PresetLoader.load(preset_name)
    prompt = PresetLoader.load_prompt(preset.prompt_file)

    # 1. Инициализируем агентов
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
        OpenRouterAgent(api_key=openrouter_key, model="google/gemini-flash-1.5-8b:free", max_retries=1),
        OpenRouterAgent(api_key=openrouter_key, model="qwen/qwen-2.5-7b-instruct:free", max_retries=1),
        OpenRouterAgent(api_key=openrouter_key, model="stepfun/step-3.5-flash:free", max_retries=1),

        # --- СЛОЙ 5: ЭКСПЕРИМЕНТАЛЬНЫЙ OPENROUTER (Высокое качество, риск 404) ---
        # Эти модели — топ по интеллекту, но часто перегружены.
        # Пробуем по одному разу: если эндпоинт занят, сразу летим дальше.
        OpenRouterAgent(api_key=openrouter_key, model="qwen/qwen3.6-plus-preview:free", max_retries=1),
        OpenRouterAgent(api_key=openrouter_key, model="z-ai/glm-4.5-air:free", max_retries=1),
        OpenRouterAgent(api_key=openrouter_key, model="nvidia/nemotron-3-super-120b-a12b:free", max_retries=1),
        OpenRouterAgent(api_key=openrouter_key, model="arcee-ai/trinity-large-preview:free", max_retries=1),

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
        OpenRouterAgent(api_key=openrouter_key, model="openrouter/free", max_retries=1),

    ]

    pipeline = PipelineManager(
        agents=agents_list,
        max_concurrent_tasks=preset.pipeline.max_concurrent_tasks,
        max_chars_per_batch=preset.pipeline.max_chars_per_batch,
        max_retries=len(agents_list) + 1
    )

    # =====================================================
    # 2. Поиск регионов (папок) для переобработки
    # =====================================================

    regions_to_reprocess = {}

    # Итерируемся только по папкам внутри директории с датой
    for region_dir in target_dir.iterdir():
        if not region_dir.is_dir():
            continue

        region_name = region_dir.name

        # Определяем пути к файлам внутри папки региона
        raw_file = region_dir / "raw.json"
        result_file = region_dir / "result.json"

        # Если нет сырых данных, нам нечего обрабатывать
        if not raw_file.exists():
            continue

        # Читаем сырые посты
        with open(raw_file, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        # raw_data может быть списком строк, либо словарем, где посты под ключом "posts"
        # Подстраиваемся под оба варианта
        posts = raw_data if isinstance(raw_data, list) else raw_data.get("posts", [])

        if not posts:
            continue

        needs_reprocessing = False

        # Если файла result.json вообще нет — ИИ не отработал
        if not result_file.exists():
            needs_reprocessing = True
            logging.info(f"🔄 [{region_name}]: Отсутствует файл result.json. В очередь.")
        else:
            # Читаем существующий результат
            with open(result_file, "r", encoding="utf-8") as f:
                result_data = json.load(f)

            # Достаем сам текст ответа ИИ
            # Если result.json - это просто строка, берем ее, если словарь - берем по ключу
            ai_result = result_data if isinstance(result_data, str) else result_data.get("result", "")

            if not ai_result:
                needs_reprocessing = True
                logging.info(f"🔄 [{region_name}]: Файл result.json есть, но ответ пуст. В очередь.")
            else:
                clean_result = str(ai_result).strip().lower().rstrip('.')
                if clean_result != "нет":
                    needs_reprocessing = True
                    logging.info(f"🔄 [{region_name}]: Ответ '{clean_result[:20]}...'. В очередь.")

        # Если регион нуждается в переобработке, сохраняем посты и путь, куда писать ответ
        if needs_reprocessing:
            regions_to_reprocess[region_name] = {
                "posts": posts,
                "result_file_path": result_file
            }

    if not regions_to_reprocess:
        logging.info("✅ Дополнительная обработка не требуется.")
        return

    logging.info(f"🚀 Начинаем переобработку {len(regions_to_reprocess)} регионов...")

    # =====================================================
    # 3. Повторный прогон ИИ и прямое сохранение
    # =====================================================

    for region_name, region_info in regions_to_reprocess.items():
        posts = region_info["posts"]
        result_file_path = region_info["result_file_path"]

        logging.info(f"--- [AI ПЕРЕОБРАБОТКА] Регион: {region_name} ({len(posts)} постов) ---")

        # Запускаем анализ
        summary = await pipeline.run(prompt=prompt, data=posts)

        # ДОБАВЛЕНА ПРОВЕРКА: Если вернулся None, значит пайплайн упал.
        if summary is None:
            logging.critical(
                f"❌ [ОШИБКА ИИ] Пайплайн вернул None для {region_name}. Пропускаем сохранение, чтобы не записать null.")
            break  # Выходим из цикла регионов

        # Сохраняем результат в result.json
        # Сохраняем как словарь, чтобы было удобнее расширять потом
        output_data = {
            "result": summary
        }

        with open(result_file_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=4)

        logging.info(f"✅ Новый результат для {region_name} успешно сохранен в result.json")

    logging.info("🎉 Переобработка успешно завершена!")


if __name__ == "__main__":
    asyncio.run(main())