import re
import json
import logging
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler

# === КОНФИГ ===
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7595431774:AAGqVaashXulX08PEpgZHsn7LysPrV6rul0")
SOURCE_CHAT_ID = -1003469691743
PRED_CHANNEL = -1003755814676

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

HISTORY_FILE = os.path.join(DATA_DIR, "simple_history.json")
TWO_CARDS_PRED_FILE = os.path.join(DATA_DIR, "active_predы_two_cards.json")
STATS_TWO_CARDS_FILE = os.path.join(DATA_DIR, "stats_two_cards.json")
THREE_CARDS_PRED_FILE = os.path.join(DATA_DIR, "active_predы_three_cards.json")
STATS_THREE_CARDS_FILE = os.path.join(DATA_DIR, "stats_three_cards.json")

TOTAL_GAMES = 1440
CHECK_RANGE = 4

LOG_FILE = os.path.join(DATA_DIR, "simple_predictor.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# === УТИЛИТЫ ===
def load_json(file: str, default=None):
    try:
        with open(file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(file: str, data):
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_ranks(cards_str: str) -> List[str]:
    """Извлекает только ранги карт (без мастей и эмодзи)"""
    cleaned = re.sub(r'[🔰✅🟩]', '', cards_str)
    ranks = re.findall(r'([A-Z\d]+)\s*[♣♦♥♠]', cleaned)
    return ranks


def parse_game(text: str) -> Optional[Dict]:
    # Убираем префикс экспорта Telegram
    text = re.sub(r'^\[\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}\]\s*[^:]+:\s*', '', text)

    # Обновленный паттерн: галочка может стоять и перед игроком, и перед банкиром
    pattern = r'#N(\d+)\.\s*(?:✅|🔰)?\s*(\d+)\s*\(([^)]+)\)\s*(?:✅|🔰)?\s*(\d+)\s*\(([^)]+)\)'
    match = re.search(pattern, text)

    if match:
        raw_id = int(match.group(1))
        player_str = match.group(3)
        banker_str = match.group(5)

        player_ranks = extract_ranks(player_str)
        banker_ranks = extract_ranks(banker_str)

        if player_ranks and banker_ranks:
            return {
                "raw_id": raw_id,
                "player_ranks": player_ranks,
                "banker_ranks": banker_ranks,
                "player_count": len(player_ranks),
                "banker_count": len(banker_ranks),
                "hour": datetime.now().hour,
                "timestamp": datetime.now().isoformat(),
                "text": text
            }
        else:
            logger.warning(f"Не удалось извлечь карты для игры #{raw_id}: player='{player_str}', banker='{banker_str}'")
    else:
        match_id = re.search(r'#N(\d+)', text)
        if match_id:
            raw_id = int(match_id.group(1))
            logger.warning(f"Не удалось разобрать игру #{raw_id}: {text[:50]}...")

    return None


def has_six(ranks: List[str]) -> bool:
    return '6' in ranks


def has_seven(ranks: List[str]) -> bool:
    return '7' in ranks


# === ОБРАБОТКА СООБЩЕНИЙ ===
async def handle_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = None
    for candidate in [update.message, update.edited_message, update.channel_post, update.edited_channel_post]:
        if candidate and candidate.text:
            msg = candidate
            break
    if not msg:
        return

    game = parse_game(msg.text)
    if not game:
        return

    raw_id = game["raw_id"]
    logger.info(f"📥 Игра #{raw_id}: игрок={game['player_count']} карт {game['player_ranks']}")

    history = load_json(HISTORY_FILE, [])
    history = [g for g in history if g.get("raw_id") != raw_id]
    history.append(game)
    save_json(HISTORY_FILE, history[-300:])

    # === Проверка активного прогноза: 2 карты (после 6) ===
    two_pred = load_json(TWO_CARDS_PRED_FILE, {})
    if two_pred and "target_raw" in two_pred:
        offset = raw_id - two_pred["target_raw"]
        if 0 <= offset < CHECK_RANGE:
            is_success = (game["player_count"] == 2)
            stats = load_json(STATS_TWO_CARDS_FILE, {"success": 0, "fail": 0})
            try:
                if is_success:
                    emoji = ["0️⃣", "1️⃣", "2️⃣", "3️⃣"][offset]
                    await context.bot.edit_message_text(
                        chat_id=PRED_CHANNEL,
                        message_id=two_pred["msg_id"],
                        text=f"✅ Игра №{two_pred['target']}\n"
                             f"6️⃣ Игрок 2 карты\n"
                             f"🎯 *ДА* → ✅{emoji}"
                    )
                    stats["success"] = stats.get("success", 0) + 1
                    two_pred = {}
                    save_json(TWO_CARDS_PRED_FILE, {})
                elif offset == CHECK_RANGE - 1:
                    await context.bot.edit_message_text(
                        chat_id=PRED_CHANNEL,
                        message_id=two_pred["msg_id"],
                        text=f"❌ Игра №{two_pred['target']}\n"
                             f"6️⃣ Игрок 2 карты\n"
                             f"🎯 *ДА* 💥 Не зашёл"
                    )
                    stats["fail"] = stats.get("fail", 0) + 1
                    two_pred = {}
                    save_json(TWO_CARDS_PRED_FILE, {})
                save_json(STATS_TWO_CARDS_FILE, stats)
            except Exception as e:
                logger.error(f"Ошибка проверки two_cards (после 6): {e}")

    # === Проверка активного прогноза: 3 карты (после 7) ===
    three_pred = load_json(THREE_CARDS_PRED_FILE, {})
    if three_pred and "target_raw" in three_pred:
        offset = raw_id - three_pred["target_raw"]
        if 0 <= offset < CHECK_RANGE:
            is_success = (game["player_count"] >= 3)
            stats = load_json(STATS_THREE_CARDS_FILE, {"success": 0, "fail": 0})
            try:
                if is_success:
                    emoji = ["0️⃣", "1️⃣", "2️⃣", "3️⃣"][offset]
                    await context.bot.edit_message_text(
                        chat_id=PRED_CHANNEL,
                        message_id=three_pred["msg_id"],
                        text=f"✅ Игра №{three_pred['target']}\n"
                             f"7️⃣ Игрок 3 карты\n"
                             f"🎯 *ДА* → ✅{emoji}"
                    )
                    stats["success"] = stats.get("success", 0) + 1
                    three_pred = {}
                    save_json(THREE_CARDS_PRED_FILE, {})
                elif offset == CHECK_RANGE - 1:
                    await context.bot.edit_message_text(
                        chat_id=PRED_CHANNEL,
                        message_id=three_pred["msg_id"],
                        text=f"❌ Игра №{three_pred['target']}\n"
                             f"7️⃣ Игрок 3 карты\n"
                             f"🎯 *ДА* 💥 Не зашёл"
                    )
                    stats["fail"] = stats.get("fail", 0) + 1
                    three_pred = {}
                    save_json(THREE_CARDS_PRED_FILE, {})
                save_json(STATS_THREE_CARDS_FILE, stats)
            except Exception as e:
                logger.error(f"Ошибка проверки three_cards (после 7): {e}")

    # === Создание новых прогнозов ===
    normalized = (raw_id - 1) % TOTAL_GAMES + 1
    if normalized == TOTAL_GAMES:
        return

    target_raw = raw_id + 1
    target_norm = (target_raw - 1) % TOTAL_GAMES + 2

    two_pred_current = load_json(TWO_CARDS_PRED_FILE, {})
    if not two_pred_current:
        if has_six(game["player_ranks"]):
            try:
                pred_text = (
                    f"🔥 Игра №{target_norm}\n"
                    f"6️⃣ Игрок 2 карты\n"
                    f"🎯 *ДА*\n"
                    f"⏳ Ожидание..."
                )
                sent = await context.bot.send_message(chat_id=PRED_CHANNEL, text=pred_text)
                save_json(TWO_CARDS_PRED_FILE, {
                    "target_raw": target_raw,
                    "target": target_norm,
                    "msg_id": sent.message_id
                })
                logger.info(f"📤 Прогноз '2 карты после 6' на игру #{target_norm}")
            except Exception as e:
                logger.error(f"Ошибка отправки two_cards (после 6): {e}")

    three_pred_current = load_json(THREE_CARDS_PRED_FILE, {})
    if not three_pred_current:
        if has_seven(game["player_ranks"]):
            try:
                pred_text = (
                    f"🔥 Игра №{target_norm}\n"
                    f"7️⃣ Игрок 3 карты\n"
                    f"🎯 *ДА*\n"
                    f"⏳ Ожидание..."
                )
                sent = await context.bot.send_message(chat_id=PRED_CHANNEL, text=pred_text)
                save_json(THREE_CARDS_PRED_FILE, {
                    "target_raw": target_raw,
                    "target": target_norm,
                    "msg_id": sent.message_id
                })
                logger.info(f"📤 Прогноз '3 карты после 7' на игру #{target_norm}")
            except Exception as e:
                logger.error(f"Ошибка отправки three_cards (после 7): {e}")


# === КОМАНДА /stats ===
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != PRED_CHANNEL:
        return

    stats2 = load_json(STATS_TWO_CARDS_FILE, {"success": 0, "fail": 0})
    stats3 = load_json(STATS_THREE_CARDS_FILE, {"success": 0, "fail": 0})

    total2 = stats2["success"] + stats2["fail"]
    total3 = stats3["success"] + stats3["fail"]

    rate2 = (stats2["success"] / total2 * 100) if total2 > 0 else 0
    rate3 = (stats3["success"] / total3 * 100) if total3 > 0 else 0

    msg = (
        "📊 *Статистика прогнозов*\n\n"
        f"🔹 *После 6 → 2 карты*\n"
        f"Успехов: {stats2['success']}, Провалов: {stats2['fail']}\n"
        f"Всего: {total2}, Успешность: *{rate2:.1f}%*\n\n"
        f"🔹 *После 7 → 3 карты*\n"
        f"Успехов: {stats3['success']}, Провалов: {stats3['fail']}\n"
        f"Всего: {total3}, Успешность: *{rate3:.1f}%*"
    )
    await update.message.reply_text(msg)


# === ЗАПУСК ===
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.Chat(SOURCE_CHAT_ID) & filters.TEXT, handle_update))
    app.add_handler(CommandHandler("stats", stats_command))

    logger.info("✅ Бот запущен: прогнозы 'после 6 → 2 карты' и 'после 7 → 3 карты'")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
