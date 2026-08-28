import asyncio
import logging
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

# ⚠️ ВСТАВЬ СЮДА НОВЫЙ ТОКЕН, ЕСЛИ СТАРЫЙ БЫЛ ОПУБЛИКОВАН В ИНТЕРНЕТЕ
BOT_TOKEN = "8612019409:AAFjbqMTXPA2e6ZOH1v4ffW0T5mbiF65Zn4"

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.DEBUG,
    filename="bot.log"
)
logger = logging.getLogger(__name__)

# Хранилища данных
captcha_answers = {}
captcha_timers = {}
rules_timers = {}

def generate_math_captcha():
    """Генерирует пример и 4 варианта ответа."""
    a = random.randint(1, 10)
    b = random.randint(1, 10)
    op = random.choice(['+', '-'])
    
    if op == '+':
        correct = a + b
        problem = f"{a} + {b}"
    else:
        if a < b: a, b = b, a
        correct = a - b
        problem = f"{a} − {b}"
    
    options = {correct}
    while len(options) < 4:
        wrong = correct + random.randint(-5, 5)
        if wrong >= 0: options.add(wrong)
    
    options_list = list(options)
    random.shuffle(options_list)
    return problem, correct, options_list

async def safe_kick(bot, chat_id, user_id, reason=""):
    """Кик через бан + разбан."""
    try:
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
        logger.info(f"✅ Кик пользователя {user_id} (причина: {reason})")
        return True
    except Exception as e:
        logger.error(f"❌ Не удалось кикнуть {user_id}: {e}")
        return False

async def captcha_timeout(context, chat_id, user_id, msg_id):
    """Таймер капчи: 5 минут."""
    try:
        await asyncio.sleep(300)
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status in ("member", "restricted"):
            await safe_kick(context.bot, chat_id, user_id, "не решил капчу")
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id,
                    text="⏰ Время вышло! Вы не решили пример и были удалены."
                )
            except Exception: pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Ошибка таймера капчи: {e}")
    finally:
        captcha_answers.pop((chat_id, user_id), None)
        captcha_timers.pop((chat_id, user_id), None)

async def rules_timeout(context, chat_id, user_id, name):
    """Таймер правил: 12 часов."""
    try:
        await asyncio.sleep(43200)
        await safe_kick(context.bot, chat_id, user_id, "не принял правила")
        logger.warning(f"⚠️ Кик {name} за непринятие правил")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Ошибка таймера правил: {e}")
    finally:
        rules_timers.pop((chat_id, user_id), None)

async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.new_chat_members:
        return

    chat_id = update.message.chat_id
    logger.info(f"🆕 Новый участник в чате {chat_id}")

    for user in update.message.new_chat_members:
        if user.id == context.bot.id:
            continue
        
        logger.info(f"🤖 Начинаем проверку для {user.first_name} ({user.id})")

        # --- ШАГ 1: ОТПРАВКА КАПЧИ ---
        problem, correct, options = generate_math_captcha()
        captcha_answers[(chat_id, user.id)] = correct

        keyboard = []
        row = []
        for opt in options:
            row.append(InlineKeyboardButton(str(opt), callback_data=f"cap_{user.id}_{opt}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row: keyboard.append(row)

        try:
            msg = await update.message.reply_text(
                f"Привет, {user.mention_html()}! 👋\n\n"
                f"Чтобы подтвердить, что вы живой человек, решите пример:\n\n"
                f"🧮 <b>{problem} = ?</b>\n\n"
                f"⏰ У вас 5 минут.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            
            old_task = captcha_timers.get((chat_id, user.id))
            if old_task and not old_task.done(): old_task.cancel()
            
            task = asyncio.create_task(captcha_timeout(context, chat_id, user.id, msg.message_id))
            captcha_timers[(chat_id, user.id)] = task
            logger.info(f"✅ Капча отправлена для {user.id}")

        except Exception as e:
            logger.critical(f"❌ БОТ НЕ МОЖЕТ ОТПРАВИТЬ СООБЩЕНИЕ В ЧАТ {chat_id}. Ошибка: {e}")
            return

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    clicker_id = query.from_user.id

    # --- ОБРАБОТКА КАПЧИ ---
    if data.startswith("cap_"):
        # ✅ ИСПРАВЛЕНИЕ ОШИБКИ ЗДЕСЬ: берем элементы по индексу
        parts = data.split("_")
        if len(parts) < 3:
            await query.answer("Ошибка формата", show_alert=True)
            return
            
        target_id = int(parts)
        answer = int(parts)

        if clicker_id != target_id:
            await query.answer("Это не для вас!", show_alert=True)
            return

        correct = captcha_answers.get((chat_id, target_id))
        if correct is None:
            await query.answer("Время истекло.", show_alert=True)
            return

        if answer == correct:
            try: await query.message.delete()
            except Exception: pass

            logger.info(f"✅ Пользователь {target_id} решил пример. Отправляем правила.")

            # --- ШАГ 2: ОТПРАВКА ПРАВИЛ (ТВОЙ ТЕКСТ) ---
            rules_text = (
                "Здравствуйте, Вы собираетесь вступить в группу родителей района Митино, "
                "воспитывающих детей с ОВЗ, инвалидностью, молодых и взрослых инвалидов "
                "\"Особое Митино\", входящую в АНО \"МИР ОДИН НА ВСЕХ\".\n\n"
                "➡️ Вступая в группу, Вы подтверждаете, что воспитываете ребенка с ОВЗ или инвалидностью, "
                "молодого или взрослого инвалида.\n"
                "➡️ Вступая в группу, Вы подтверждаете свое согласие с требованием группы о заполнении "
                "анкеты участника, https://forms.yandex.ru/u/6a3d367702848f966f66bea6, и обязуетесь "
                "заполнить ее в течение трех рабочих дней с даты вступления в группу. Вы согласны с тем, "
                "что в случае, если анкета не будет заполнена, админы вправе удалить Вас из группы.\n"
                "➡️ Вступая в группу, Вы подтверждаете, что прочитали правила и согласны их выполнять.\n\n"
                "Правила группы:\n\n"
                "✅ Мы уважительно относимся ко всем участникам чата. В коммуникации придерживаемся принципов "
                "ненасильственного общения (нет манипуляциям, обесцениванию и другим видам психологического насилия).\n"
                "✅ Не оцениваем друг друга публично, не оскорбляем. Если хочется дать корректирующую обратную связь, "
                "лучше сделать это лично (и бережно!).\n"
                "✅ Общаясь друг с другом, мы помним, что у каждого из нас своя беда, она не может быть больше или меньше "
                "беды остальных участников чата, поэтому мы бережем нервы друг друга.\n"
                "✅ В спорах не переходим на личности, оценивающие комментарии, аргументированно отстаиваем свое мнение.\n"
                "✅ Мы переходим в личную переписку, как только обсуждение перестало быть релевантным широкому кругу родителей.\n"
                "✅ Базово мы считаем, что то, что мы пишем в чат, не выходит за его пределы без согласия автора.\n\n"
                "В группе запрещается:\n"
                "❌ размещать ссылки на сторонние сообщества/чаты/сайты без согласования с админами.\n"
                "❌ рассылать в личку участникам рекламу чего бы то ни было и кого бы то ни было.\n"
                "❌ Использовать нецензурную лексику.\n"
                "❌ Грубо оскорблять оппонента, обесценивать его достижения, его жизнь и его действия.\n"
                "❌ Продажа товаров запрещена, кроме соответственной подгруппы.\n\n"
                "❗️ Если обсуждение всё-таки вышло за рамки правил и стало слишком горячим и активным, модератор может "
                "поставить чат на паузу (например, на час), чтобы все остыли и успели прочитать накопившиеся сообщения.\n"
                "❗️ Если участник чата грубо нарушил правила, первый раз получает предупреждение, если второй раз - немой режим "
                "на сутки, с третьего раза - удаление из группы.\n\n"
                "Надеемся, правила помогут сохранять здесь комфортную атмосферу. Пожалуйста, прежде чем написать, сверяйтесь с ними.\n\n"
                "⏰ У вас есть 12 часов, чтобы подтвердить, что вы ознакомлены и согласны с правилами. "
                "Если вы не нажмёте кнопку, вы будете удалены из группы."
            )
            
            keyboard = [
                [
                    InlineKeyboardButton("✅ Я принимаю правила", callback_data=f"acc_{target_id}"),
                    InlineKeyboardButton("❌ Я отказываюсь", callback_data=f"dec_{target_id}")
                ]
            ]
            
            try:
                await context.bot.send_message(
                    chat_id=chat_id, 
                    text=rules_text, 
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception as e:
                logger.error(f"Не удалось отправить правила: {e}")
                return

            captcha_answers.pop((chat_id, target_id), None)
            captcha_timers.pop((chat_id, target_id), None)

            old_rule_task = rules_timers.get((chat_id, target_id))
            if old_rule_task and not old_rule_task.done(): old_rule_task.cancel()
            
            task = asyncio.create_task(rules_timeout(context, chat_id, target_id, query.from_user.first_name))
            rules_timers[(chat_id, target_id)] = task

        else:
            await query.edit_message_text("❌ Неверный ответ. Вы удалены.")
            await safe_kick(context.bot, chat_id, target_id, "ошибка в капче")
            captcha_answers.pop((chat_id, target_id), None)
            captcha_timers.pop((chat_id, target_id), None)

    # --- ОБРАБОТКА ПРАВИЛ ---
    elif data.startswith("acc_"):
        parts = data.split("_")
        if len(parts) < 2: return
        target_id = int(parts)
        
        if clicker_id != target_id:
            await query.answer("Не ваша кнопка", show_alert=True)
            return
        
        try: await query.message.delete()
        except Exception: pass
        
        await query.message.reply_text(f"✅ Добро пожаловать, {query.from_user.first_name}!")
        rules_timers.pop((chat_id, target_id), None)

    elif data.startswith("dec_"):
        parts = data.split("_")
        if len(parts) < 2: return
        target_id = int(parts)
        
        if clicker_id != target_id:
            await query.answer("Не ваша кнопка", show_alert=True)
            return

        try: await query.message.delete()
        except Exception: pass
        
        await safe_kick(context.bot, chat_id, target_id, "отказ от правил")
        await query.message.reply_text("🚫 Вы исключены из группы.")
        rules_timers.pop((chat_id, target_id), None)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот работает. Новые участники проходят капчу.")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_member))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(CommandHandler("start", start_cmd))
    
    logger.info("🚀 Бот запущен и ждет новых участников...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
