from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters, ConversationHandler, JobQueue
)
from telegram import BotCommand
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    JobQueue
)
import pytz
from datetime import time
import logging
import os
from dotenv import load_dotenv
from gspread.exceptions import APIError
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta, time
import pytz
import requests
import logging
import telegram.error
ADMIN_IDS = [552553015]  # Замените на реальные Telegram user_id админов
ADMIN_ID = [552553015]

logging.basicConfig(level=logging.INFO)

# Авторизация Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/spreadsheets",
         "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_file("tg-bot-457119-fb7b4775dfe6.json", scopes=scope)
client = gspread.authorize(creds)
sheet = client.open("Bot").sheet1
settings_sheet = client.open("Bot").worksheet("Settings")
staking_sheet = client.open("Bot").worksheet("Staking")

SELECT_CURRENCY, INPUT_AMOUNT, WAITING_PHOTO = range(3)
STAKING_PERIODS = {1: 60, 3: 80, 6: 100, 12: 120}
REFERRAL_PERCENTS = {
    'deposit': [0.05, 0.02, 0.01],  # 5%, 2%, 1% за пополнение
    'staking': [0.05, 0.02, 0.01]    # 5%, 2%, 1% за стейкинг
}


def append_transaction(sheet, transaction_data: dict):
    """Безопасно добавляет транзакцию в таблицу"""
    try:
        # Подготавливаем данные в правильном порядке столбцов
        row_data = [
            transaction_data.get('user_id', ''),
            transaction_data.get('balance', 0),
            transaction_data.get('currency', 'STB'),
            transaction_data.get('amount', 0),
            transaction_data.get('timestamp', datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            transaction_data.get('status', ''),
            transaction_data.get('Address/Photo', ''),
            transaction_data.get('username', ''),
            transaction_data.get('tx_type', '')
        ]

        # Добавляем строку в таблицу
        sheet.append_row(row_data)
        return True
    except Exception as e:
        logging.error(f"Ошибка добавления транзакции: {e}")
        return False

def get_stb_rate():
    try:
        return float(settings_sheet.acell("J1").value)
    except:
        return 1.0


def get_balance(user_id):
    """Альтернативный вариант, если все статусы в одном столбце"""
    records = sheet.get_all_records()
    balance = 0.0

    for row in records:
        if str(row.get('user_id')) != str(user_id):
            continue

        try:
            amount = float(str(row.get('amount', '0')).replace(',', '.'))
            status = str(row.get('status', ''))

            if status in ['Подтверждено', 'Ежедневный доход по стейкингу', 'Завершение стейкинга','Списание за стейкинг', 'Заявка на вывод', 'Реферальный бонус 1 уровня', 'Реферальный бонус 2 уровня','Реферальный бонус 3 уровня']:
                balance += amount

        except ValueError as e:
            logging.error(f"Ошибка в строке: {row}")
            continue

    return round(max(0, balance), 2)


def get_user_history(user_id):
    records = sheet.get_all_records()
    history = []
    for row in records:
        if str(row.get('user_id')) == str(user_id):
            time = row.get('timestamp', '—')
            currency = row.get('currency', '-')
            amount = row.get('amount', '-')
            status = row.get('status', '-')
            history.append(f"{time}: {amount} {currency} ({status})")
    return history


async def process_stakes(context: ContextTypes.DEFAULT_TYPE):
    """Ежедневное начисление daily_profit и обработка завершенных стейкингов"""
    try:
        tz = pytz.timezone("Europe/Kyiv")
        current_datetime = datetime.now(tz)
        current_date = current_datetime.date()

        records = staking_sheet.get_all_records()

        for idx, row in enumerate(records, start=2):
            if row.get('status') != 'Активен':
                continue

            try:
                user_id = row['user_id']
                username = row.get('username', '')
                amount = float(row['amount'])
                daily_profit = float(row.get('daily_profit', 0))
                earned = float(row.get('earned', 0))

                # Парсим дату окончания с временной зоной
                end_date = datetime.strptime(row['end_date'], "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
                end_date_date = end_date.date()  # Дата без времени для сравнения

                if current_date >= end_date_date:
                    # Закрываем стейкинг
                    staking_sheet.update_cell(idx, 8, 'Завершен')

                    # Возвращаем основную сумму + начисленный доход
                    total_to_return = amount + earned
                    new_balance = round(get_balance(user_id) + total_to_return, 2)

                    sheet.append_row([
                        user_id,
                        new_balance,
                        'STB',
                        total_to_return,
                        current_datetime.strftime("%Y-%m-%d %H:%M:%S"),
                        'Завершение стейкинга',
                        '',
                        username
                    ])

                    try:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=f"🎉 Стейкинг завершен!\n"
                                 f"• Возвращено: {amount} STB\n"
                                 f"• Начислено: {earned:.6f} STB\n"
                                 f"• Новый баланс: {new_balance} STB"
                        )
                    except Exception as e:
                        logging.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

                else:
                    # Ежедневное начисление
                    new_earned = round(earned + daily_profit, 6)
                    staking_sheet.update_cell(idx, 9, new_earned)

                    sheet.append_row([
                        user_id,
                        round(get_balance(user_id) + daily_profit, 2),
                        'STB',
                        daily_profit,
                        current_datetime.strftime("%Y-%m-%d %H:%M:%S"),
                        'Ежедневный доход по стейкингу',
                        '',
                        username
                    ])

                    try:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=f"➕ Начислено {daily_profit:.6f} STB\n"
                                 f"Текущий доход: {new_earned:.6f} STB\n"
                                 f"Баланс: {round(get_balance(user_id), 2)} STB"
                        )
                    except Exception as e:
                        logging.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

            except Exception as e:
                logging.error(f"Ошибка обработки стейкинга в строке {idx}: {e}")
                continue

    except Exception as e:
        logging.error(f"Критическая ошибка в process_stakes: {e}", exc_info=True)


async def input_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "NoUsername"
    text = update.message.text

    # Обработка вывода средств
    if context.user_data.get("awaiting_address"):
        if not text:
            await update.message.reply_text("Пожалуйста, укажите адрес для вывода")
            return INPUT_AMOUNT

        address = text
        amount = context.user_data.get("withdraw_amount")

        if amount is None:
            await update.message.reply_text("Ошибка: сумма для вывода не найдена")
            return ConversationHandler.END

        try:
            amount = float(amount)
            balance = get_balance(user_id)

            if amount > balance:
                await update.message.reply_text("Недостаточно средств для вывода")
                return INPUT_AMOUNT

            # Записываем транзакцию
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sheet.append_row([
                user_id,
                round(balance - amount, 2),
                "STB",
                -amount,
                timestamp,
                "Заявка на вывод",
                address,
                username
            ])

            # Очищаем временные данные
            context.user_data.pop("awaiting_address", None)
            context.user_data.pop("withdraw_amount", None)

            # Отправляем подтверждение
            keyboard = [[InlineKeyboardButton("🏠 На главную", callback_data='home')]]
            await update.message.reply_text(
                f"✅ Заявка на вывод {amount} STB отправлена\n"
                f"📭 Адрес: {address}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            return ConversationHandler.END
        except ValueError:
            await update.message.reply_text("Ошибка: неверная сумма")
            return INPUT_AMOUNT

    # Обработка запроса на вывод
    if context.user_data.get("awaiting_withdraw"):
        if not text.isdigit():
            await update.message.reply_text("Введите целое число без дробей для вывода.")
            return INPUT_AMOUNT

        try:
            amount = float(text)
            balance = get_balance(user_id)

            if amount > balance:
                await update.message.reply_text("Недостаточно средств для вывода.")
                return INPUT_AMOUNT

            context.user_data["withdraw_amount"] = amount
            context.user_data["awaiting_address"] = True
            context.user_data.pop("awaiting_withdraw", None)

            await update.message.reply_text("Пожалуйста, отправьте адрес для получения средств.")
            return INPUT_AMOUNT
        except ValueError:
            await update.message.reply_text("Ошибка: введите корректную сумму")
            return INPUT_AMOUNT

    # Обработка стейкинга
    if 'stake_period' in context.user_data:
        if not text.isdigit():
            await update.message.reply_text("Пожалуйста, введите целое число без дробной части.")
            return INPUT_AMOUNT

        try:
            amount = float(text)
            balance = get_balance(user_id)

            if amount > balance:
                await update.message.reply_text("Недостаточно средств для стейкинга.")
                return INPUT_AMOUNT

            period = context.user_data['stake_period']
            percent = STAKING_PERIODS[period]
            year_profit = round(amount * (percent / 100), 2)
            daily_profit = round(year_profit / 365, 6)
            total_profit = round((period * 30) * daily_profit, 2)

            context.user_data['stake_confirm'] = {
                'amount': amount,
                'period': period,
                'percent': percent,
                'daily_profit': daily_profit,
                'total_profit': total_profit
            }

            keyboard = [
                [InlineKeyboardButton("✅ Подтвердить", callback_data='confirm_stake')],
                [InlineKeyboardButton("❌ Отменить", callback_data='cancel_stake')]
            ]

            await update.message.reply_text(
                f"Вы выбрали стейкинг на {period} мес.\n"
                f"Сумма: {amount} STB\n"
                f"Годовой %: {percent}%\n"
                f"Доход в день: {daily_profit} STB\n"
                f"Общий доход: {total_profit} STB\n\n"
                f"Подтвердите действие:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            context.user_data.pop('stake_period', None)
            return INPUT_AMOUNT
        except ValueError:
            await update.message.reply_text("Ошибка: введите корректную сумму")
            return INPUT_AMOUNT

    # Обработка пополнения баланса
    if not text.isdigit():
        await update.message.reply_text("Пожалуйста, введите целое число без дробной части.")
        return INPUT_AMOUNT

    try:
        amount = float(text)
        currency = context.user_data.get('currency', 'STB')

        if currency == 'USDT':
            stb_rate = get_stb_rate()
            converted = round(amount * stb_rate, 2)
        else:
            converted = amount

        context.user_data['amount'] = amount
        context.user_data['converted_amount'] = converted
        context.user_data['raw_amount'] = amount

        await update.message.reply_text(f"`136582964520750`", parse_mode='Markdown')
        await update.message.reply_text(
            f"Вы выбрали пополнение в {currency} на сумму {amount}. "
            f"Это будет зачтено как ~{converted} STB. "
            f"Пожалуйста, отправьте скриншот/фото подтверждения оплаты."
        )

        return WAITING_PHOTO
    except ValueError:
        await update.message.reply_text("Ошибка: введите корректную сумму")
        return INPUT_AMOUNT


def save_transaction(user_id, username, currency, amount, timestamp, status, photo_url, raw_amount=None):
    balance = round(get_balance(user_id) + amount, 2)
    sheet.append_row([
        user_id, balance, "STB", round(amount, 2), timestamp, status, photo_url, username
    ])

GROUP_ID = "-1002259609574"
async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # 1. Получаем данные из сообщения
        if not update.message or not update.message.photo:
            await update.message.reply_text("❌ Фото не найдено. Пожалуйста, отправьте фото подтверждения.")
            return

        photo = update.message.photo[-1]  # Берем самое качественное фото
        user = update.message.from_user
        amount = context.user_data.get('converted_amount')

        if not amount:
            await update.message.reply_text("❌ Ошибка: сумма не найдена. Начните процесс заново.")
            return

        # 2. Подготавливаем данные для записи
        username = user.username or f"id{user.id}"
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 3. Записываем в Google Sheets
        try:
            row_data = [
                user.id,
                get_balance(user.id),
                context.user_data['currency'],
                amount,
                current_time,
                "На рассмотрении",
                f"https://t.me/{username}" if user.username else "",
                username,
                context.user_data.get('raw_amount', amount)
            ]
            sheet.append_row(row_data)
            row_count = len(sheet.get_all_values())
        except Exception as e:
            logging.error(f"Ошибка записи в Google Sheets: {e}")
            await update.message.reply_text("❌ Ошибка сервера. Попробуйте позже.")
            return

        # 4. Отправляем администратору (с обработкой ошибок чата)
        try:
            ADMIN_ID = 552553015  # ЗАМЕНИТЕ на реальный ID

            # Текстовое сообщение с кнопками

            admin_msg = await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"📨 Новая заявка #{row_count}\n"
                     f"👤 Пользователь: @{username} (ID: {user.id})\n"
                     f"💰 Сумма: {amount} STB\n"
                     f"💱 Валюта: {context.user_data['currency']}\n"
                     f"⏰ Время: {current_time}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_{row_count}"),
                     InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{row_count}")]
                ])
            )

            # Фото отдельным сообщением
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=photo.file_id,
                caption=f"Фото подтверждения для заявки #{row_count}",
                reply_to_message_id=admin_msg.message_id
            )

            await update.message.reply_text("✅ Ваша заявка принята и отправлена на модерацию")
            # Отправляем заявку в группу
            group_message = await context.bot.send_message(
                chat_id=GROUP_ID,
                text=f"📨 Новая заявка #{row_count}\n"
                     f"👤 Пользователь: @{username} (ID: {user.id})\n"
                     f"💰 Сумма: {amount} STB\n"
                     f"💱 Валюта: {context.user_data['currency']}\n"
                     f"⏰ Время: {current_time}",

            )

            # Отправляем фото в группу
            await context.bot.send_photo(
                chat_id=GROUP_ID,
                photo=photo.file_id,
                caption=f"Подтверждение оплаты для заявки #{row_count}",
                reply_to_message_id=group_message.message_id
            )

        except telegram.error.BadRequest as e:
            logging.error(f"Ошибка отправки администратору: {e}")
            await update.message.reply_text("⚠️ Администратор недоступен. Попробуйте позже.")
        except Exception as e:
            logging.error(f"Неизвестная ошибка: {e}")
            await update.message.reply_text("❌ Системная ошибка. Сообщите администратору.")

    except Exception as e:
        logging.critical(f"Критическая ошибка в receive_photo: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Произошла ошибка при обработке заявки")


def save_referral(user_id, username, referrer_id, referrer_username):
    try:
        referral_sheet = client.open("Bot").worksheet("Referrals")
        existing = referral_sheet.get_all_records()

        # Проверяем, есть ли уже пользователь
        for row in existing:
            if str(row['user_id']) == str(user_id):
                return False

        # Сохраняем нового реферала
        referral_sheet.append_row([
            user_id,
            username,
            referrer_id,
            referrer_username,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ])
        return True
    except Exception as e:
        logging.error(f"Ошибка при сохранении реферала: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data.clear()

    user = update.effective_user
    username = user.username or f"id{user.id}"  # Используем 'id12345' если username нет

    if update.message and update.message.text.startswith("/start ref"):
        referrer_id = update.message.text.split("ref")[-1]
        save_referral(user.id, username, referrer_id, "")


    keyboard = [
        [
            InlineKeyboardButton("💰 Баланс", callback_data='balance'),
            InlineKeyboardButton("ℹ️ О нас", callback_data='about')
        ],
        [
            InlineKeyboardButton("👥 Реф Ссылка", callback_data='ref'),
            InlineKeyboardButton("🔗 Ссылки", callback_data='links')
        ],
        [
            InlineKeyboardButton("📜 История", callback_data='history')
        ]

    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    message = update.message or update.callback_query.message
    user_id = message.from_user.id
    username = message.from_user.username or "NoUsername"

    if update.message and update.message.text.startswith("/start ref"):
        referrer_id = update.message.text.split("ref")[-1]
        try:
            referrer_user = await context.bot.get_chat(referrer_id)
            referrer_username = referrer_user.username or "NoUsername"
            save_referral(update.effective_user.id,
                         update.effective_user.username or "NoUsername",
                         referrer_id,
                         referrer_username)
        except Exception as e:
            logging.error(f"Ошибка обработки реферала: {e}")

    await message.reply_text("Добро пожаловать! Выберите опцию:", reply_markup=reply_markup)


async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == 'balance':
        try:
            balance = get_balance(user_id)
            frozen = 0
            active_stakes = []

            # Сначала собираем все данные
            for stake in staking_sheet.get_all_records():
                if str(stake.get('user_id')) == str(user_id) and stake.get('status') == 'Активен':
                    frozen += float(stake['amount'])
                    active_stakes.append(
                        f"• {stake['amount']} STB ({stake['period']} мес.) → {stake['daily_profit']:.6f} STB/день"
                    )

            # Затем формируем сообщение и клавиатуру
            keyboard = [
                [
                    InlineKeyboardButton("➕ Пополнить баланс", callback_data='top_up'),
                    InlineKeyboardButton("📥 Стейкинг", callback_data='staking')
                ],
                [
                    InlineKeyboardButton("💸 Вывод средств", callback_data='withdraw'),
                    InlineKeyboardButton("🔙 Назад", callback_data='home')
                ]
            ]

            message = (
                f"💰 Текущий баланс: **{balance:.2f} STB**\n"
                f"❄️ Заморожено в стейкинге: **{frozen:.2f} STB**\n\n"
            )
            message += "📊 Активные стейки:\n" + "\n".join(active_stakes) if active_stakes else "🔹 Нет активных стейков"

            await query.edit_message_text(text=message, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

        except Exception as e:
            logging.error(f"Ошибка в балансе: {e}")
            await query.edit_message_text("⚠️ Ошибка при загрузке данных. Попробуйте позже.")

    elif query.data == 'top_up':

        keyboard = [
            [InlineKeyboardButton("USDT", callback_data='usdt'), InlineKeyboardButton("BST", callback_data='bst')],
            [InlineKeyboardButton("🔙 Назад", callback_data='home')]]
        await query.edit_message_text("Выберите валюту для пополнения:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data in ['usdt', 'bst']:

        context.user_data['currency'] = query.data.upper()
        if context.user_data['currency'] == 'USDT':
            rate = get_stb_rate()
            context.user_data['stb_rate'] = rate
            await query.message.delete()
            await context.bot.send_message(chat_id=query.from_user.id, text=f"Текущий курс STB: 1 USDT = {rate:.2f} STB Введите сумму пополнения: ")
        else:
            await query.message.delete()
            await context.bot.send_message(chat_id=query.from_user.id, text="Введите сумму пополнения:")


    elif query.data == 'withdraw':

        balance = get_balance(user_id)
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='balance')]]
        if balance < 1:
            await query.edit_message_text("У вас недостаточно средств для вывода.", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            context.user_data['awaiting_withdraw'] = True
            await query.message.delete()
            await context.bot.send_message(chat_id=user_id, text="Введите сумму для вывода в STB:")

    elif query.data == 'staking':
        keyboard = [
            [InlineKeyboardButton("📊 Мои стейки", callback_data='my_stakes')],
            [InlineKeyboardButton("1 месяц (60%)", callback_data='stake_1')],
            [InlineKeyboardButton("3 месяца (80%)", callback_data='stake_3')],
            [InlineKeyboardButton("6 месяцев (100%)", callback_data='stake_6')],
            [InlineKeyboardButton("12 месяцев (120%)", callback_data='stake_12')],
            [InlineKeyboardButton("🔙 Назад", callback_data='balance')]
        ]
        await query.edit_message_text("Выберите срок стейкинга:", reply_markup=InlineKeyboardMarkup(keyboard))


    elif query.data.startswith("stake_"):
        period = int(query.data.split("_")[1])
        context.user_data['stake_period'] = period
        await context.bot.send_message(chat_id=query.from_user.id,text="Введите сумму для стейкинга:")


        # переход в уже существующее состояние

    elif query.data == 'my_stakes':
        try:
            # Получаем все записи о стейкингах
            all_stakes = staking_sheet.get_all_records()

            # Фильтруем только активные стейкинги текущего пользователя
            user_stakes = [
                stake for stake in all_stakes
                if str(stake.get('user_id', '')) == str(user_id)
                   and stake.get('status', '').lower() == 'активен'
            ]

            # Если стейкингов нет
            if not user_stakes:
                await query.edit_message_text(
                    "У вас нет активных стейкингов.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Назад", callback_data='staking')]
                    ])
                )
                return

            # Формируем сообщение со списком стейкингов
            message_lines = []
            for stake in user_stakes:
                try:
                    amount = stake.get('amount', '0')
                    period = stake.get('period', '?')
                    end_date_str = stake.get('end_date', '')
                    daily_profit = stake.get('daily_profit', '0')
                    earned = stake.get('earned', '0')

                    # Проверяем и парсим дату окончания
                    if end_date_str:
                        try:
                            end_date = datetime.strptime(end_date_str, "%Y-%m-%d %H:%M:%S")
                            days_left = (end_date - datetime.now()).days
                            date_info = f"Осталось дней: {days_left}"
                        except ValueError:
                            date_info = "Дата окончания: неверный формат"
                    else:
                        date_info = "Дата окончания: не указана"

                    message_lines.append(
                        f"• {amount} STB на {period} мес.\n"
                        f"  Доход в день: {daily_profit} STB\n"
                        f"  Начислено: {earned} STB\n"
                        f"  {date_info}\n"
                    )
                except Exception as e:
                    logging.error(f"Ошибка обработки стейкинга: {e}")
                    continue

            # Отправляем сообщение пользователю
            await query.edit_message_text(
                "📊 Ваши активные стейкинги:\n\n" + "\n".join(message_lines),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data='staking')]
                ])
            )

        except Exception as e:
            logging.error(f"Ошибка в my_stakes: {e}")
            await query.edit_message_text(
                "⚠️ Произошла ошибка при загрузке данных о стейкингах",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data='staking')]
                ])
            )

    elif query.data == 'confirm_stake':
        data = context.user_data.get('stake_confirm')
        if not data:
            await query.edit_message_text("Нет данных для подтверждения стейкинга.")
            return ConversationHandler.END

        user_id = query.from_user.id
        username = query.from_user.username or "NoUsername"
        amount = data['amount']
        period = data['period']
        percent = data['percent']
        daily_profit = data['daily_profit']

        kyiv_tz = pytz.timezone("Europe/Kyiv")
        start_date = datetime.now(kyiv_tz)
        end_date = start_date + timedelta(days=period * 30)


        staking_sheet.append_row([
            user_id,
            username,
            amount,
            period,
            percent,
            start_date.strftime("%Y-%m-%d %H:%M:%S"),
            end_date.strftime("%Y-%m-%d %H:%M:%S"),
            "Активен",
            0,  # earned
            daily_profit
        ])
        current_balance = get_balance(user_id)
        sheet.append_row([
            user_id,
            round(current_balance - amount, 2),  # Новый баланс
            "STB",
            -amount,  # Отрицательная сумма для списания
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Списание за стейкинг",
            "",
            username
        ])

        await process_referral_bonuses(context, user_id, amount, 'staking')
        await query.edit_message_text(f"✅ Стейкинг активирован на {period} мес.\n"f"Сумма: {amount} STB\n"f"Годовой %: {percent}%\n"f"Доход в день: {daily_profit} STB\n"f"До: {end_date.strftime('%d.%m.%Y')}"
        )
        return ConversationHandler.END

    elif query.data == 'cancel_stake':
        await query.edit_message_text("Стейкинг отменён.")
        context.user_data.pop('stake_confirm', None)
        return ConversationHandler.END

    elif query.data == 'home':
            await start(update, context)

    elif query.data == 'about':
        keyboard = [
            [InlineKeyboardButton("🌐 Наш сайт", url='https://t.me/+pZNJWmKkq5tmNDdk')],
            [InlineKeyboardButton("🔙 Назад", callback_data='home')]
        ]
        await query.edit_message_text("Мы компания, предоставляющая ...", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == 'ref':
        await show_referral_info(update, context)

    elif query.data == 'ref_stats':
        await show_referral_stats(update, context)
    elif query.data == 'refresh_ref_stats':
        await show_referral_stats(update, context)

    elif query.data == 'links':
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='home')]]
            await query.edit_message_text("Полезные ссылки: ...", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == 'history':
            history = get_user_history(user_id)
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='home')]]
            if not history:
                await query.edit_message_text("История транзакций пуста.", reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await query.edit_message_text(
                    "Ваша история пополнений:\n\n" + "\n".join(history[-10:]),
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
    elif query.data == 'admin_users':
        records = sheet.get_all_records()
        users = set()
        for row in records:
            user_id = row.get('user_id')
            if user_id:
                users.add(user_id)
        await query.edit_message_text(
            f"Всего пользователей: {len(users)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='home')]])
        )

    elif query.data == 'admin_requests':
        records = sheet.get_all_records()
        pending = [(i + 2, row) for i, row in enumerate(records) if row['status'] == 'Ожидает подтверждения']
        if not pending:
                await query.edit_message_text("Нет заявок на пополнение.")
        else:
            messages = []
            keyboard = []
            for idx, row in pending[-5:]:
                user = row.get('username', '-')
                amount = row.get('amount', '-')
                currency = row.get('currency', '-')
                date = row.get('timestamp', '-')
                text = f"{user} — {amount} {currency} ({date})"
                messages.append(text)
                keyboard.append([
                    InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_{idx}"),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{idx}")
                ])
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='home')])
            await query.edit_message_text("Последние заявки на пополнение:\n\n" + "\n\n".join(messages),reply_markup=InlineKeyboardMarkup(keyboard))


    elif query.data.startswith("approve_"):
        row_index = int(query.data.split("_")[1])
        sheet.update_cell(row_index, 6, "Подтверждено")
        user_id_cell = sheet.cell(row_index, 1).value
        try:
            user_id = int(user_id_cell)
            await context.bot.send_message(chat_id=user_id, text="Ваша заявка на пополнение подтверждена ✅")
        except:
            logging.warning(f"Не удалось отправить сообщение пользователю {user_id}")
        await query.edit_message_text("Заявка подтверждена ✅")

    elif query.data.startswith("reject_"):
        row_index = int(query.data.split("_")[1])
        sheet.update_cell(row_index, 6, "Отклонено")
        user_id_cell = sheet.cell(row_index, 1).value
        try:
            user_id = int(user_id_cell)
            await context.bot.send_message(chat_id=user_id, text="Ваша заявка на пополнение была отклонена ❌")
        except:
            logging.warning(f"Не удалось отправить сообщение пользователю {user_id}")
        await query.edit_message_text("Заявка отклонена ❌")


ADMIN_IDS = [552553015]  # Замените на реальные Telegram user_id админов


async def handle_admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # Обязательно для обработки callback

    try:
        action, row_index = query.data.split('_')
        row_index = int(row_index)

        # Определяем статус
        status = "Заявка подтверждена ✅" if action == "approve" else "Заявка отклонена ❌"
        sheet_status = "Подтверждено" if action == "approve" else "Отклонено"

        # Обновляем статус в Google Sheets
        sheet.update_cell(row_index, 6, sheet_status)

        # Получаем текущий текст сообщения
        original_text = query.message.text

        # Редактируем сообщение - сохраняем текст и убираем кнопки
        await query.edit_message_text(
            text=original_text,  # Оригинальный текст без изменений
            reply_markup=None  # Удаляем кнопки
        )

        # Отправляем статус ОТДЕЛЬНЫМ сообщением как ответ
        await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=status,
            reply_to_message_id=query.message.message_id
        )

        # Уведомляем пользователя
        try:
            user_id = int(sheet.cell(row_index, 1).value)
            await context.bot.send_message(
                chat_id=user_id,
                text=f"Статус вашей заявки #{row_index}:\n{status}"
            )
        except Exception as e:
            logging.error(f"Ошибка уведомления пользователя: {e}")

    except Exception as e:
        logging.error(f"Ошибка обработки кнопки: {e}")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
            await context.bot.send_message(
                chat_id=query.message.chat.id,
                text="⚠️ Ошибка обработки",
                reply_to_message_id=query.message.message_id
            )
        except:
            pass


async def get_referral_link(bot, user_id: int) -> str:
    """Генерирует реферальную ссылку для пользователя"""
    try:
        # Получаем информацию о боте
        bot_info = await bot.get_me()
        if not hasattr(bot_info, 'username') or not bot_info.username:
            logging.error("У бота не установлен username")
            return "Ошибка: бот не имеет username"

        return f"https://t.me/{bot_info.username}?start=ref{user_id}"
    except Exception as e:
        logging.error(f"Ошибка при генерации реферальной ссылки: {e}")
        return "Ошибка генерации ссылки"


def save_referral_bonus(bonus_sheet, data: dict):
    """Записывает реферальный бонус в таблицу"""
    try:
        # Подготовка данных в правильном порядке столбцов
        row_data = [
            data.get('user_id', ''),  # Кто получил бонус
            data.get('referrer_id', ''),  # От кого пришел бонус
            data.get('level', 1),  # Уровень (1, 2, 3)
            data.get('amount', 0),  # Сумма бонуса
            data.get('currency', 'STB'),  # Валюта
            data.get('timestamp', datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            data.get('action_type', ''),  # Тип операции (deposit/stacking)
            data.get('referred_username', '')  # Имя приглашенного
        ]

        bonus_sheet.append_row(row_data)
        return True
    except Exception as e:
        logging.error(f"Ошибка записи реферального бонуса: {e}")
        return False


async def show_referral_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        message = update.message or update.callback_query.message

        # Получаем данные
        referral_sheet = client.open("Bot").worksheet("Referrals")
        bonus_sheet = client.open("Bot").worksheet("ReferralBonuses")

        # Собираем статистику
        stats = {
            'total': 0,
            'by_level': {1: {'count': 0, 'bonus': 0, 'users': []},
                         2: {'count': 0, 'bonus': 0, 'users': []},
                         3: {'count': 0, 'bonus': 0, 'users': []}},
            'total_bonus': 0
        }

        # 1. Считаем рефералов по уровням
        all_referrals = referral_sheet.get_all_records()

        # Прямые рефералы (1 уровень)
        level1 = [r for r in all_referrals if str(r['referrer_id']) == str(user_id)]
        stats['by_level'][1]['count'] = len(level1)
        stats['by_level'][1]['users'] = [r['username'] for r in level1 if 'username' in r][:5]  # первые 5

        # Рефералы 2 уровня
        level2 = []
        for ref in level1:
            level2 += [r for r in all_referrals if str(r['referrer_id']) == str(ref['user_id'])]
        stats['by_level'][2]['count'] = len(level2)
        stats['by_level'][2]['users'] = [r['username'] for r in level2 if 'username' in r][:5]

        # Рефералы 3 уровня
        level3 = []
        for ref in level2:
            level3 += [r for r in all_referrals if str(r['referrer_id']) == str(ref['user_id'])]
        stats['by_level'][3]['count'] = len(level3)
        stats['by_level'][3]['users'] = [r['username'] for r in level3 if 'username' in r][:5]

        stats['total'] = sum(level['count'] for level in stats['by_level'].values())

        # 2. Считаем бонусы
        all_bonuses = bonus_sheet.get_all_records()
        for bonus in all_bonuses:
            if str(bonus['user_id']) == str(user_id):
                level = int(bonus.get('level', 1))
                if level in stats['by_level']:
                    stats['by_level'][level]['bonus'] += float(bonus.get('amount', 0))
                    stats['total_bonus'] += float(bonus.get('amount', 0))

        # 3. Формируем сообщение
        msg = "📊 *Ваша реферальная статистика*\n\n"
        msg += f"👥 Всего рефералов: *{stats['total']}*\n"
        msg += f"💰 Всего заработано: *{round(stats['total_bonus'], 2)} STB*\n\n"

        for level in [1, 2, 3]:
            percent = [5, 2, 1][level - 1]
            msg += (
                f"*{level} уровень* ({percent}%):\n"
                f"• Рефералов: {stats['by_level'][level]['count']}\n"
                f"• Заработано: {round(stats['by_level'][level]['bonus'], 2)} STB\n"
            )

            if stats['by_level'][level]['users']:
                msg += f"• Последние: @{' @'.join(stats['by_level'][level]['users'])}\n"

            msg += "\n"

        # 4. Добавляем кнопки
        keyboard = [
            [InlineKeyboardButton("🔄 Обновить", callback_data='ref_stats'),
             InlineKeyboardButton("🔙 Назад", callback_data='ref')]
        ]

        await message.edit_text(
            text=msg,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    except Exception as e:
        logging.error(f"Ошибка в show_referral_stats: {e}")
        await message.reply_text(
            "⚠️ Произошла ошибка при загрузке статистики",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Назад", callback_data='ref')]]
            )
        )


async def show_referral_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        message = update.message or update.callback_query.message

        # Генерируем ссылку
        bot_username = (await context.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start=ref{user.id}"

        # Получаем базовую статистику
        try:
            referral_sheet = client.open("Bot").worksheet("Referrals")
            all_refs = referral_sheet.get_all_records()
            total_refs = len([r for r in all_refs if str(r['referrer_id']) == str(user.id)])
        except:
            total_refs = 0

        # Формируем сообщение
        msg = (
            "👥 *Реферальная программа*\n\n"
            f"🔗 Ваша ссылка:\n`{ref_link}`\n\n"
            "💸 *Бонусы:*\n"
            "• 1 уровень: 5%\n"
            "• 2 уровень: 2%\n"
            "• 3 уровень: 1%\n\n"
            f"📊 Всего приглашено: *{total_refs}*"
        )

        # Создаем кнопки
        keyboard = [
            [InlineKeyboardButton("📤 Поделиться",
                                  url=f"https://t.me/share/url?url={ref_link}&text=Присоединяйся%20к%20криптопроекту!")],
            [InlineKeyboardButton("📊 Подробная статистика", callback_data='ref_stats')],
            [InlineKeyboardButton("🏠 На главную", callback_data='home')]
        ]

        if update.callback_query:
            await update.callback_query.edit_message_text(
                text=msg,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                text=msg,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )

    except Exception as e:
        logging.error(f"Ошибка в show_referral_info: {e}")
        await update.message.reply_text(
            "⚠️ Произошла ошибка. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 На главную", callback_data='home')]]
            )
        )


async def get_basic_ref_stats(user_id):
    try:
        referral_sheet = client.open("Bot").worksheet("Referrals")
        transactions_sheet = client.open("Bot").worksheet("Transactions")

        # Считаем общее количество рефералов
        all_refs = referral_sheet.get_all_records()
        total_refs = len([r for r in all_refs if str(r['referrer_id']) == str(user_id)])

        # Считаем заработанные бонусы
        all_trans = transactions_sheet.get_all_records()
        earned = sum(
            float(t['amount']) for t in all_trans
            if str(t['user_id']) == str(user_id)
            and 'реферальный бонус' in t.get('status', '').lower()
        )

        return {
            'total': total_refs,
            'earned': round(earned, 2)
        }
    except Exception as e:
        logging.error(f"Ошибка получения статистики: {e}")
        return {'total': 0, 'earned': 0}



async def ref_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    referral_sheet = client.open("Bot").worksheet("Referrals")

    # Получаем всех рефералов пользователя
    all_referrals = referral_sheet.get_all_records()
    user_referrals = [r for r in all_referrals if str(r['referrer_id']) == str(user_id)]

    # Группируем по уровням
    levels = {1: [], 2: [], 3: []}
    for ref in user_referrals:
        # Определяем уровень реферала
        # ... логика определения уровня ...
        pass

    # Формируем сообщение
    message = "📊 Ваша реферальная статистика:\n\n"
    for level, refs in levels.items():
        message += f"🔹 {level} уровень: {len(refs)} чел.\n"

    await update.message.reply_text(message)


async def process_referral_bonuses(context: ContextTypes.DEFAULT_TYPE, referred_user_id: int, amount: float,
                                   action_type: str):
    try:
        # Получаем все необходимые таблицы
        referral_sheet = client.open("Bot").worksheet("Referrals")
        bonus_sheet = client.open("Bot").worksheet("ReferralBonuses")
        transaction_sheet = client.open("Bot").worksheet("Transactions")

        # Получаем информацию о приглашенном пользователе
        all_referrals = referral_sheet.get_all_records()
        referred_user = next((r for r in all_referrals if str(r['user_id']) == str(referred_user_id)), None)
        referred_username = referred_user.get('username',
                                              f"id{referred_user_id}") if referred_user else f"id{referred_user_id}"

        # Собираем цепочку рефереров
        referrers = []
        current_user = str(referred_user_id)

        for level in range(1, 4):
            referrer = next((r for r in all_referrals if str(r['user_id']) == current_user), None)
            if not referrer:
                break

            referrers.append((
                level,
                referrer['referrer_id'],
                referrer.get('username', f"id{referrer['referrer_id']}")
            ))
            current_user = str(referrer['referrer_id'])

        # Начисляем бонусы каждому рефереру
        for level, referrer_id, referrer_username in referrers:
            percent = [0.05, 0.02, 0.01][level - 1]
            bonus = round(amount * percent, 2)

            if bonus <= 0:
                continue

            # Обновляем баланс реферера
            new_balance = round(get_balance(referrer_id) + bonus, 2)

            # 1. Записываем бонус в таблицу ReferralBonuses
            if not save_referral_bonus(bonus_sheet, {
                'user_id': referrer_id,
                'referrer_id': referred_user_id,
                'level': level,
                'amount': bonus,
                'action_type': action_type,
                'referred_username': referred_username
            }):
                logging.error(f"Не удалось записать бонус для {referrer_id}")
                continue

            # 2. Записываем транзакцию в основную таблицу
            if not append_transaction(transaction_sheet, {
                'user_id': referrer_id,
                'balance': new_balance,
                'currency': 'STB',
                'amount': bonus,
                'status': f'Реферальный бонус {level} уровня',
                'Address/Photo': f'От пользователя {referred_username}',
                'username': referrer_username,
                'tx_type': f'{action_type}_referral'
            }):
                logging.error(f"Не удалось записать транзакцию для {referrer_id}")
                continue

            # Уведомляем реферера
            try:
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=f"💸 Вам начислен реферальный бонус {level} уровня!\n"
                         f"• Сумма: {bonus} STB\n"
                         f"• Тип: {action_type}\n"
                         f"• От пользователя: @{referred_username}"
                )
            except Exception as e:
                logging.error(f"Не удалось уведомить реферера {referrer_id}: {e}")

    except Exception as e:
        logging.error(f"Ошибка в process_referral_bonuses: {e}")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("У вас нет доступа к админ-панели.")
        return

    keyboard = [
        [InlineKeyboardButton("📥 Все заявки", callback_data='admin_requests')],
        [InlineKeyboardButton("👤 Пользователи", callback_data='admin_users')],
        [InlineKeyboardButton("🏠 Назад", callback_data='home')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Добро пожаловать в админ-панель:", reply_markup=reply_markup)


import asyncio
import pytz

async def daily_staking_task():
    while True:
        now = datetime.now(pytz.timezone("Europe/Kyiv"))
        next_run = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())

        records = staking_sheet.get_all_records()
        for i, row in enumerate(records, start=2):
            if row.get("status") != "Активен":
                continue

            end_date = datetime.strptime(row["end_date"], "%Y-%m-%d")
            if datetime.now() >= end_date:
                staking_sheet.update_cell(i, 8, "Завершен")
                profit = float(row["earned"])
                user_id = row["user_id"]
                username = row.get("username", "")
                sheet.append_row([user_id, round(get_balance(user_id) + profit, 2), "STB", round(profit, 2), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Начисление дохода","",username])
                continue

                earned = float(row.get("earned", 0)) + float(row.get("daily_profit", 0))
                staking_sheet.update_cell(i, 9, round(earned, 2))
                # Вложите это в цикл, проверяющий активные стейки:
                # после строки, где обновляется earned
                new_balance = get_balance(user_id) + daily_profit

            user_row_num = next((idx + 1 for idx,rec in enumerate(sheet.get_all_records()) if rec['user_id'] == user_id), None)

            if user_row_num:
                sheet.update_cell(user_row_num, 3, new_balance)
    # обновление баланса в Google Sheets

            # отправка уведомления пользователю
        try:
            await context.bot.send_message(chat_id=user_id, text=f"Вам начислено {daily_profit} STB за стейкинг.")
        except:
                logging.warning(f"Не удалось отправить сообщение пользователю {user_id}")
load_dotenv()

# Ваши обработчики должны быть определены перед использованием
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Операция отменена.")
    return ConversationHandler.END

async def post_init(application: Application):
    """Функция, выполняемая после инициализации бота"""
    logging.info("Бот успешно инициализирован")

async def post_shutdown(application: Application):
    """Функция, выполняемая перед выключением бота"""
    logging.info("Завершение работы бота")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """📚 Support:Если у вас возникли проблемы с ботом пожалуйста напишите нашему тех. Админу: @123 """
    await update.message.reply_text(help_text)

async def post_init(application):
    """Установка команд меню"""
    commands = [
        BotCommand("start", "Главное меню"),
        BotCommand("help", "Помощь")
    ]
    await application.bot.set_my_commands(commands)

async def refresh_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принудительное обновление команд (/setcommands)"""
    await set_commands(context.bot)
    await update.message.reply_text("✅ Команды меню обновлены!")

async def set_commands(bot):
    await bot.set_my_commands([
        BotCommand("start", "Перезапустить бота"),
        BotCommand("help", "Справка по использованию"),
        BotCommand("setcommands", "Обновить меню команд (админ)")
    ])

def main():
    try:
        # Инициализация бота
        app = ApplicationBuilder() \
            .token("7675737327:AAHdhojQrVLlpgwRCanmcBwRduimo7pBBsY") \
            .post_init(post_init) \
            .post_shutdown(post_shutdown) \
            .build()

        # Инициализация JobQueue для периодических задач
        jq = app.job_queue

        # Настройка ежедневной задачи для обработки стейкингов
        if jq:
            jq.run_daily(
                callback=process_stakes,
                time=time(hour=00, minute=50, tzinfo=pytz.timezone("Europe/Kyiv")),
                name="daily_staking_processing"
            )

        # Настройка ConversationHandler
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("start", start),
                CallbackQueryHandler(handle_buttons),
                MessageHandler(filters.TEXT & ~filters.COMMAND, input_amount)
            ],
            states={
                INPUT_AMOUNT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, input_amount),
                    CallbackQueryHandler(handle_buttons)
                ],
                WAITING_PHOTO: [
                    MessageHandler(filters.PHOTO, receive_photo)
                ],
            },
            fallbacks=[
                CommandHandler("cancel", cancel),
                CommandHandler("start", start)
            ],
            allow_reentry=True
        )

        # Регистрация обработчиков в правильном порядке
        app.post_init(set_commands)
        app.add_handler(conv_handler)
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("setcommands", refresh_commands))
        app.add_handler(CommandHandler("admin", admin_panel))
        app.add_handler(CallbackQueryHandler(handle_buttons))
        app.add_handler(CallbackQueryHandler(
            handle_admin_buttons,
            pattern=r"^(approve|reject)_\d+$"
        ))
        app.add_handler(CommandHandler("help", help_command))

        # Обработчик ошибок
        app.add_error_handler(error_handler)

        # Запуск бота
        logging.info("Starting bot...")
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            close_loop=False
        )

    except Exception as e:
        logging.critical(f"Bot crashed: {e}", exc_info=True)
        raise


async def post_init(app: Application):
    """Выполняется после инициализации бота"""
    logging.info("Bot initialized successfully")


async def post_shutdown(app: Application):
    """Выполняется перед выключением бота"""
    logging.info("Bot shutdown completed")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Глобальный обработчик ошибок"""
    logging.error("Exception while handling update:", exc_info=context.error)

    if update and isinstance(update, Update):
        if update.callback_query:
            await update.callback_query.answer("⚠️ Произошла ошибка", show_alert=False)
        elif update.message:
            await update.message.reply_text("⚠️ Произошла ошибка при обработке запроса")


if __name__ == "__main__":
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler("bot.log"),
            logging.StreamHandler()
        ]
    )

    try:
        # Для работы в асинхронных средах
        import nest_asyncio

        nest_asyncio.apply()

        main()
    except Exception as e:
        logging.critical(f"Failed to start bot: {e}", exc_info=True)

















