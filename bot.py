import os
import logging
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
import random
import string
import asyncio
import re
import json
from dotenv import load_dotenv

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, 
    ReplyKeyboardRemove, InputMediaPhoto
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, ConversationHandler
)

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_IDS = [123456789]  # Замените на свой Telegram ID

if not TELEGRAM_TOKEN or not DATABASE_URL:
    raise ValueError("TELEGRAM_TOKEN и DATABASE_URL должны быть установлены в .env файле")

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Состояния для регистрации
(NAME, AGE, GENDER, CURRENT_CITY, SEARCH_CITY, SEARCH_RADIUS, 
 DATING_GOAL, BIO, PHOTO, CAPTCHA) = range(10)

# Состояния для редактирования
(EDIT_NAME, EDIT_AGE, EDIT_BIO, EDIT_PHOTOS, EDIT_CURRENT_CITY, 
 EDIT_SEARCH_CITY, EDIT_SEARCH_RADIUS, EDIT_DATING_GOAL) = range(100, 108)

# Константы
DATING_GOALS = {
    'relationship': 'Отношения',
    'friendship': 'Дружба', 
    'online_chat': 'Общение онлайн',
    'short_romance': 'Короткий роман',
    'gaming': 'Совместный гейминг'
}

GENDERS = {
    'male': 'Мужчина',
    'female': 'Женщина'
}

class Database:
    """Класс для работы с базой данных"""
    
    @staticmethod
    def get_connection():
        return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    
    @staticmethod
    def init_database():
        """Инициализация всех таблиц"""
        commands = [
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                name TEXT NOT NULL,
                age INTEGER NOT NULL,
                gender TEXT NOT NULL,
                current_city TEXT NOT NULL,
                current_lat FLOAT,
                current_lon FLOAT,
                search_city TEXT NOT NULL,
                search_lat FLOAT,
                search_lon FLOAT,
                search_radius INTEGER DEFAULT 50,
                dating_goal TEXT NOT NULL,
                bio TEXT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                is_banned BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                name_changes INTEGER DEFAULT 0,
                last_name_change TIMESTAMP,
                age_changes INTEGER DEFAULT 0,
                last_age_change TIMESTAMP,
                location_changes_today INTEGER DEFAULT 0,
                location_changes_month INTEGER DEFAULT 0,
                last_location_change TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS user_photos (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                photo_id TEXT NOT NULL,
                is_main BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS likes (
                from_user BIGINT NOT NULL,
                to_user BIGINT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (from_user, to_user)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS matches (
                user1 BIGINT NOT NULL,
                user2 BIGINT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user1, user2)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS viewed_profiles (
                viewer_user BIGINT NOT NULL,
                viewed_user BIGINT NOT NULL,
                first_view TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                can_view_again TIMESTAMP,
                view_count INTEGER DEFAULT 1,
                PRIMARY KEY (viewer_user, viewed_user)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS complaints (
                id SERIAL PRIMARY KEY,
                from_user BIGINT NOT NULL,
                against_user BIGINT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP,
                resolved_by BIGINT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS captcha_attempts (
                user_id BIGINT PRIMARY KEY,
                attempts INTEGER DEFAULT 0,
                last_attempt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_verified BOOLEAN DEFAULT FALSE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS daily_limits (
                user_id BIGINT PRIMARY KEY,
                complaints_today INTEGER DEFAULT 0,
                last_complaint_date DATE DEFAULT CURRENT_DATE
            )
            """
        ]
        
        try:
            with Database.get_connection() as conn:
                with conn.cursor() as cur:
                    for command in commands:
                        cur.execute(command)
                conn.commit()
            logger.info("База данных инициализирована")
        except Exception as e:
            logger.error(f"Ошибка инициализации БД: {e}")
    
    @staticmethod
    def execute_query(query: str, params: tuple = (), fetch: str = None):
        """Выполнение SQL запроса"""
        try:
            with Database.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    if fetch == "one":
                        return cur.fetchone()
                    elif fetch == "all":
                        return cur.fetchall()
                    return None
        except Exception as e:
            logger.error(f"Ошибка выполнения запроса: {e}")
            return None

class UserManager:
    """Управление пользователями"""
    
    @staticmethod
    def user_exists(user_id: int) -> bool:
        result = Database.execute_query(
            "SELECT 1 FROM users WHERE user_id = %s", 
            (user_id,), "one"
        )
        return bool(result)
    
    @staticmethod
    def is_user_banned(user_id: int) -> bool:
        result = Database.execute_query(
            "SELECT is_banned FROM users WHERE user_id = %s", 
            (user_id,), "one"
        )
        return bool(result and result['is_banned'])
    
    @staticmethod
    def create_user(user_data: dict) -> bool:
        query = """
        INSERT INTO users (user_id, username, name, age, gender, current_city, 
                          current_lat, current_lon, search_city, search_lat, search_lon,
                          search_radius, dating_goal, bio)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            user_data['user_id'], user_data.get('username'), user_data['name'],
            user_data['age'], user_data['gender'], user_data['current_city'],
            user_data.get('current_lat'), user_data.get('current_lon'),
            user_data['search_city'], user_data.get('search_lat'), user_data.get('search_lon'),
            user_data.get('search_radius', 50), user_data['dating_goal'], user_data['bio']
        )
        
        result = Database.execute_query(query, params)
        return result is not None
    
    @staticmethod
    def get_user(user_id: int):
        return Database.execute_query(
            "SELECT * FROM users WHERE user_id = %s", 
            (user_id,), "one"
        )
    
    @staticmethod
    def add_photo(user_id: int, photo_id: str, is_main: bool = False):
        # Если это главное фото, сбрасываем у остальных
        if is_main:
            Database.execute_query(
                "UPDATE user_photos SET is_main = FALSE WHERE user_id = %s",
                (user_id,)
            )
        
        Database.execute_query(
            "INSERT INTO user_photos (user_id, photo_id, is_main) VALUES (%s, %s, %s)",
            (user_id, photo_id, is_main)
        )
    
    @staticmethod
    def get_user_photos(user_id: int):
        return Database.execute_query(
            "SELECT photo_id, is_main FROM user_photos WHERE user_id = %s ORDER BY is_main DESC, created_at",
            (user_id,), "all"
        )
    
    @staticmethod
    def can_change_name(user_id: int) -> Tuple[bool, str]:
        user = UserManager.get_user(user_id)
        if not user:
            return False, "Пользователь не найден"
        
        if user['last_name_change']:
            last_change = datetime.fromisoformat(str(user['last_name_change']))
            if datetime.now() - last_change < timedelta(days=30):
                return False, "Имя можно менять только раз в месяц"
        
        return True, ""
    
    @staticmethod
    def can_change_age(user_id: int) -> Tuple[bool, str]:
        user = UserManager.get_user(user_id)
        if not user:
            return False, "Пользователь не найден"
        
        # Проверка 24 часа
        if user['last_age_change']:
            last_change = datetime.fromisoformat(str(user['last_age_change']))
            if datetime.now() - last_change < timedelta(hours=24):
                return False, "Возраст можно менять не чаще раза в сутки"
        
        # Проверка 3 раза в месяц
        if user['age_changes'] >= 3:
            # Сброс счетчика если прошел месяц с последнего изменения
            if user['last_age_change']:
                last_change = datetime.fromisoformat(str(user['last_age_change']))
                if datetime.now() - last_change >= timedelta(days=30):
                    Database.execute_query(
                        "UPDATE users SET age_changes = 0 WHERE user_id = %s",
                        (user_id,)
                    )
                else:
                    return False, "Возраст можно менять максимум 3 раза в месяц"
        
        return True, ""
    
    @staticmethod
    def can_change_location(user_id: int) -> Tuple[bool, str]:
        user = UserManager.get_user(user_id)
        if not user:
            return False, "Пользователь не найден"
        
        today = datetime.now().date()
        
        # Сброс дневного счетчика
        if user['last_location_change']:
            last_change = datetime.fromisoformat(str(user['last_location_change'])).date()
            if last_change != today:
                Database.execute_query(
                    "UPDATE users SET location_changes_today = 0 WHERE user_id = %s",
                    (user_id,)
                )
                user['location_changes_today'] = 0
        
        # Проверка дневного лимита
        if user['location_changes_today'] >= 5:
            return False, "Локацию можно менять максимум 5 раз в день"
        
        # Проверка месячного лимита
        if user['location_changes_month'] >= 15:
            if user['last_location_change']:
                last_change = datetime.fromisoformat(str(user['last_location_change']))
                if datetime.now() - last_change < timedelta(days=30):
                    return False, "Локацию можно менять максимум 15 раз в месяц"
                else:
                    Database.execute_query(
                        "UPDATE users SET location_changes_month = 0 WHERE user_id = %s",
                        (user_id,)
                    )
        
        return True, ""
    
    @staticmethod
    def update_user_field(user_id: int, field: str, value, increment_changes: bool = False):
        query = f"UPDATE users SET {field} = %s"
        params = [value]
        
        if increment_changes:
            if field in ['name']:
                query += ", name_changes = name_changes + 1, last_name_change = CURRENT_TIMESTAMP"
            elif field in ['age']:
                query += ", age_changes = age_changes + 1, last_age_change = CURRENT_TIMESTAMP"
            elif field in ['current_city', 'search_city', 'current_lat', 'current_lon', 'search_lat', 'search_lon']:
                query += ", location_changes_today = location_changes_today + 1, location_changes_month = location_changes_month + 1, last_location_change = CURRENT_TIMESTAMP"
        
        query += " WHERE user_id = %s"
        params.append(user_id)
        
        Database.execute_query(query, tuple(params))

class MatchManager:
    """Управление лайками и матчами"""
    
    @staticmethod
    def add_like(from_user: int, to_user: int) -> bool:
        # Добавляем лайк
        Database.execute_query(
            "INSERT INTO likes (from_user, to_user) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (from_user, to_user)
        )
        
        # Проверяем взаимность
        mutual = Database.execute_query(
            "SELECT 1 FROM likes WHERE from_user = %s AND to_user = %s",
            (to_user, from_user), "one"
        )
        
        if mutual:
            # Создаем матч
            user1, user2 = sorted([from_user, to_user])
            Database.execute_query(
                "INSERT INTO matches (user1, user2) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (user1, user2)
            )
            return True
        
        return False
    
    @staticmethod
    def mark_viewed(viewer: int, viewed: int):
        # Получаем текущую запись о просмотре
        current = Database.execute_query(
            "SELECT * FROM viewed_profiles WHERE viewer_user = %s AND viewed_user = %s",
            (viewer, viewed), "one"
        )
        
        now = datetime.now()
        
        if current:
            # Обновляем количество просмотров
            new_count = current['view_count'] + 1
            
            # Определяем когда можно показать снова
            if new_count == 1:  # Первый просмотр
                next_view = now + timedelta(weeks=1)
            elif new_count == 2:  # Второй просмотр
                next_view = now + timedelta(days=30)
            else:  # Третий и далее
                next_view = now + timedelta(days=180)
            
            Database.execute_query(
                "UPDATE viewed_profiles SET view_count = %s, can_view_again = %s WHERE viewer_user = %s AND viewed_user = %s",
                (new_count, next_view, viewer, viewed)
            )
        else:
            # Первый просмотр
            next_view = now + timedelta(weeks=1)
            Database.execute_query(
                "INSERT INTO viewed_profiles (viewer_user, viewed_user, can_view_again) VALUES (%s, %s, %s)",
                (viewer, viewed, next_view)
            )
    
    @staticmethod
    def get_matches(user_id: int):
        return Database.execute_query(
            """SELECT u.*, 
                      CASE WHEN m.user1 = %s THEN m.user2 ELSE m.user1 END as match_user_id,
                      m.created_at as match_date
               FROM matches m
               JOIN users u ON u.user_id = CASE WHEN m.user1 = %s THEN m.user2 ELSE m.user1 END
               WHERE (m.user1 = %s OR m.user2 = %s) AND u.is_active = TRUE AND u.is_banned = FALSE
               ORDER BY m.created_at DESC""",
            (user_id, user_id, user_id, user_id), "all"
        )
    
    @staticmethod
    def find_candidates(user_id: int):
        user = UserManager.get_user(user_id)
        if not user:
            return []
        
        # Базовый запрос
        query = """
        SELECT DISTINCT u.* FROM users u
        WHERE u.user_id != %s 
        AND u.is_active = TRUE 
        AND u.is_banned = FALSE
        AND u.user_id NOT IN (
            SELECT to_user FROM likes WHERE from_user = %s
        )
        AND (
            u.user_id NOT IN (
                SELECT viewed_user FROM viewed_profiles 
                WHERE viewer_user = %s AND can_view_again > CURRENT_TIMESTAMP
            )
        )
        """
        
        params = [user_id, user_id, user_id]
        
        # Фильтр по поиску "Вся Украина"
        if user['search_city'].lower() != 'вся украина':
            # Ищем тех, кто находится в городе поиска пользователя
            # ИЛИ тех, кто ищет в текущем городе пользователя
            query += """
            AND (
                (u.current_city ILIKE %s)
                OR (u.search_city ILIKE %s)
            )
            """
            params.extend([f"%{user['search_city']}%", f"%{user['current_city']}%"])
        
        query += " ORDER BY RANDOM() LIMIT 1"
        
        return Database.execute_query(query, tuple(params), "all")

class ComplaintManager:
    """Управление жалобами"""
    
    @staticmethod
    def can_file_complaint(user_id: int) -> Tuple[bool, str]:
        # Проверяем дневной лимит
        today = datetime.now().date()
        
        limits = Database.execute_query(
            "SELECT * FROM daily_limits WHERE user_id = %s",
            (user_id,), "one"
        )
        
        if limits:
            if limits['last_complaint_date'] == today:
                if limits['complaints_today'] >= 5:
                    return False, "Вы можете подавать максимум 5 жалоб в день"
            else:
                # Сбрасываем счетчик
                Database.execute_query(
                    "UPDATE daily_limits SET complaints_today = 0, last_complaint_date = %s WHERE user_id = %s",
                    (today, user_id)
                )
        else:
            # Создаем запись
            Database.execute_query(
                "INSERT INTO daily_limits (user_id, complaints_today, last_complaint_date) VALUES (%s, 0, %s)",
                (user_id, today)
            )
        
        return True, ""
    
    @staticmethod
    def file_complaint(from_user: int, against_user: int, reason: str):
        # Добавляем жалобу
        Database.execute_query(
            "INSERT INTO complaints (from_user, against_user, reason) VALUES (%s, %s, %s)",
            (from_user, against_user, reason)
        )
        
        # Обновляем счетчик
        Database.execute_query(
            "UPDATE daily_limits SET complaints_today = complaints_today + 1 WHERE user_id = %s",
            (from_user,)
        )
        
        # Проверяем количество жалоб на пользователя
        complaint_count = Database.execute_query(
            "SELECT COUNT(*) as count FROM complaints WHERE against_user = %s AND status = 'pending'",
            (against_user,), "one"
        )
        
        # Автоматическая блокировка после 10 жалоб
        if complaint_count and complaint_count['count'] >= 10:
            Database.execute_query(
                "UPDATE users SET is_banned = TRUE WHERE user_id = %s",
                (against_user,)
            )
            return True  # Пользователь заблокирован
        
        return False

class CaptchaManager:
    """Управление капчей"""
    
    @staticmethod
    def generate_captcha() -> Tuple[str, str]:
        """Генерирует простую математическую капчу"""
        num1 = random.randint(1, 20)
        num2 = random.randint(1, 20)
        operation = random.choice(['+', '-'])
        
        if operation == '+':
            answer = str(num1 + num2)
            question = f"{num1} + {num2} = ?"
        else:
            if num1 < num2:
                num1, num2 = num2, num1
            answer = str(num1 - num2)
            question = f"{num1} - {num2} = ?"
        
        return question, answer
    
    @staticmethod
    def is_verified(user_id: int) -> bool:
        result = Database.execute_query(
            "SELECT is_verified FROM captcha_attempts WHERE user_id = %s",
            (user_id,), "one"
        )
        return bool(result and result['is_verified'])
    
    @staticmethod
    def increment_attempts(user_id: int) -> int:
        # Получаем текущие попытки
        current = Database.execute_query(
            "SELECT attempts FROM captcha_attempts WHERE user_id = %s",
            (user_id,), "one"
        )
        
        if current:
            new_attempts = current['attempts'] + 1
            Database.execute_query(
                "UPDATE captcha_attempts SET attempts = %s, last_attempt = CURRENT_TIMESTAMP WHERE user_id = %s",
                (new_attempts, user_id)
            )
        else:
            new_attempts = 1
            Database.execute_query(
                "INSERT INTO captcha_attempts (user_id, attempts) VALUES (%s, %s)",
                (user_id, new_attempts)
            )
        
        return new_attempts
    
    @staticmethod
    def verify_user(user_id: int):
        Database.execute_query(
            "INSERT INTO captcha_attempts (user_id, is_verified) VALUES (%s, TRUE) ON CONFLICT (user_id) DO UPDATE SET is_verified = TRUE",
            (user_id,)
        )

# Утилиты
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def create_main_menu():
    keyboard = [
        [InlineKeyboardButton("👀 Смотреть анкеты", callback_data="browse")],
        [InlineKeyboardButton("❤️ Мои матчи", callback_data="matches")],
        [InlineKeyboardButton("👤 Мой профиль", callback_data="profile")],
        [InlineKeyboardButton("✏️ Редактировать", callback_data="edit_menu")],
        [InlineKeyboardButton("🚫 Пожаловаться", callback_data="complaint_menu")],
        [InlineKeyboardButton("🗑 Удалить профиль", callback_data="delete_profile")]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_browse_keyboard(target_id: int):
    keyboard = [
        [
            InlineKeyboardButton("❤️", callback_data=f"like_{target_id}"),
            InlineKeyboardButton("👎", callback_data=f"skip_{target_id}")
        ],
        [InlineKeyboardButton("🚫 Пожаловаться", callback_data=f"complaint_{target_id}")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def format_profile_text(user_data) -> str:
    photos = UserManager.get_user_photos(user_data['user_id'])
    photo_count = len(photos) if photos else 0
    
    search_location = "🌍 Вся Украина" if user_data['search_city'].lower() == 'вся украина' else f"📍 {user_data['search_city']} ({user_data['search_radius']} км)"
    
    return f"""👤 {user_data['name']}, {user_data['age']} лет
🚻 {GENDERS.get(user_data['gender'], user_data['gender'])}
📍 {user_data['current_city']}
🔍 Ищет: {search_location}
💕 Цель: {DATING_GOALS.get(user_data['dating_goal'], user_data['dating_goal'])}
📸 Фото: {photo_count}

📝 О себе:
{user_data['bio']}"""

# Команда /start
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if UserManager.is_user_banned(user_id):
        await update.message.reply_text("❌ Ваш аккаунт заблокирован.")
        return ConversationHandler.END
    
    if UserManager.user_exists(user_id):
        await update.message.reply_text(
            "Добро пожаловать обратно! Выберите действие:",
            reply_markup=create_main_menu()
        )
        return ConversationHandler.END
    
    # Проверка капчи
    if not CaptchaManager.is_verified(user_id):
        question, answer = CaptchaManager.generate_captcha()
        context.user_data['captcha_answer'] = answer
        
        await update.message.reply_text(
            f"🤖 Подтвердите, что вы человек.\n\nРешите пример: {question}",
            reply_markup=ReplyKeyboardRemove()
        )
        return CAPTCHA
    
    await update.message.reply_text(
        "Привет! Давайте создадим вашу анкету для знакомств.\n\nКак вас зовут?",
        reply_markup=ReplyKeyboardRemove()
    )
    return NAME

# Обработка капчи
async def handle_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_answer = update.message.text.strip()
    correct_answer = context.user_data.get('captcha_answer', '')
    user_id = update.effective_user.id
    
    if user_answer == correct_answer:
        CaptchaManager.verify_user(user_id)
        await update.message.reply_text(
            "✅ Отлично! Теперь давайте создадим вашу анкету.\n\nКак вас зовут?"
        )
        return NAME
    else:
        attempts = CaptchaManager.increment_attempts(user_id)
        
        if attempts >= 3:
            await update.message.reply_text(
                "❌ Слишком много неверных попыток. Попробуйте позже командой /start"
            )
            return ConversationHandler.END
        
        question, answer = CaptchaManager.generate_captcha()
        context.user_data['captcha_answer'] = answer
        
        await update.message.reply_text(
            f"❌ Неверно. Попытка {attempts}/3\n\nПопробуйте еще раз: {question}"
        )
        return CAPTCHA

# Регистрация - имя
async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if len(name) < 2 or len(name) > 50:
        await update.message.reply_text("Имя должно быть от 2 до 50 символов. Попробуйте еще раз:")
        return NAME
    
    context.user_data['name'] = name
    await update.message.reply_text("Сколько вам лет? (от 16 до 100)")
    return AGE

# Регистрация - возраст
async def get_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        age = int(update.message.text)
        if 16 <= age <= 100:
            context.user_data['age'] = age
            
            keyboard = [
                [InlineKeyboardButton("👨 Мужчина", callback_data="gender_male")],
                [InlineKeyboardButton("👩 Женщина", callback_data="gender_female")]
            ]
            
            await update.message.reply_text(
                "Выберите ваш пол:", 
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return GENDER
        else:
            await update.message.reply_text("Возраст должен быть от 16 до 100 лет:")
            return AGE
    except ValueError:
        await update.message.reply_text("Введите возраст числом:")
        return AGE

# Регистрация - пол
async def get_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await