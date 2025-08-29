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
ADMIN_IDS = [8096476392]

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

# Дополнительные логи для действий пользователей
user_logger = logging.getLogger('user_actions')
user_logger.setLevel(logging.INFO)
user_handler = logging.FileHandler('user_actions.log')
user_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
user_logger.addHandler(user_handler)

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
                search_all_ukraine BOOLEAN DEFAULT FALSE,
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
            CREATE INDEX IF NOT EXISTS idx_users_location ON users(current_lat, current_lon);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_users_search ON users(search_lat, search_lon);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_likes_from_user ON likes(from_user);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_likes_to_user ON likes(to_user);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_matches_users ON matches(user1, user2);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_viewed_profiles ON viewed_profiles(viewer_user);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_complaints_against ON complaints(against_user);
            """
        ]
        
        try:
            with Database.get_connection() as conn:
                with conn.cursor() as cur:
                    for command in commands:
                        cur.execute(command)
                    
                    # Создаем индексы
                    for index in index_commands:
                        cur.execute(index)
                        
                conn.commit()
            logger.info("База данных инициализирована")
        except Exception as e:
            logger.error(f"Ошибка инициализации БД: {e}")
    
    @staticmethod
    def execute_query(query: str, params: tuple = (), fetch: str = None):
        """Выполнение SQL запроса с безопасными параметрами"""
        try:
            with Database.get_connection() as conn:
                with conn.cursor() as cur:
                    # Логирование SQL запросов для отладки
                    logger.debug(f"SQL Query: {query}")
                    logger.debug(f"Params: {params}")
                    
                    cur.execute(query, params)
                    if fetch == "one":
                        return cur.fetchone()
                    elif fetch == "all":
                        return cur.fetchall()
                    return None
        except Exception as e:
            logger.error(f"Ошибка выполнения запроса: {e}")
            logger.error(f"Query: {query}")
            logger.error(f"Params: {params}")
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
            try:
                last_change = user['last_name_change']
                if isinstance(last_change, str):
                    last_change = datetime.fromisoformat(last_change)
                
                if datetime.now() - last_change < timedelta(days=30):
                    return False, "Имя можно менять только раз в месяц"
            except (ValueError, AttributeError):
                pass
        
        return True, ""
    
    @staticmethod
    def can_change_age(user_id: int) -> Tuple[bool, str]:
        user = UserManager.get_user(user_id)
        if not user:
            return False, "Пользователь не найден"
        
        # Проверка 24 часа
        if user['last_age_change']:
            try:
                last_change = user['last_age_change']
                if isinstance(last_change, str):
                    last_change = datetime.fromisoformat(last_change)
                
                if datetime.now() - last_change < timedelta(hours=24):
                    return False, "Возраст можно менять не чаще раза в сутки"
            except (ValueError, AttributeError):
                pass
        
        # Проверка 3 раза в месяц
        if user.get('age_changes', 0) >= 3:
            # Сброс счетчика если прошел месяц с последнего изменения
            if user['last_age_change']:
                try:
                    last_change = user['last_age_change']
                    if isinstance(last_change, str):
                        last_change = datetime.fromisoformat(last_change)
                    
                    if datetime.now() - last_change >= timedelta(days=30):
                        Database.execute_query(
                            "UPDATE users SET age_changes = 0 WHERE user_id = %s",
                            (user_id,)
                        )
                    else:
                        return False, "Возраст можно менять максимум 3 раза в месяц"
                except (ValueError, AttributeError):
                    pass
        
        return True, ""
    
    @staticmethod
    def can_change_location(user_id: int) -> Tuple[bool, str]:
        user = UserManager.get_user(user_id)
        if not user:
            return False, "Пользователь не найден"
        
        today = datetime.now().date()
        
        # Сброс дневного счетчика
        if user['last_location_change']:
            try:
                last_change = user['last_location_change']
                if isinstance(last_change, str):
                    last_change = datetime.fromisoformat(last_change).date()
                elif hasattr(last_change, 'date'):
                    last_change = last_change.date()
                else:
                    last_change = last_change
                
                if last_change != today:
                    Database.execute_query(
                        "UPDATE users SET location_changes_today = 0 WHERE user_id = %s",
                        (user_id,)
                    )
                    user['location_changes_today'] = 0
            except (ValueError, AttributeError):
                pass
        
        # Проверка дневного лимита
        if user.get('location_changes_today', 0) >= 5:
            return False, "Локацию можно менять максимум 5 раз в день"
        
        # Проверка месячного лимита
        if user.get('location_changes_month', 0) >= 15:
            if user['last_location_change']:
                try:
                    last_change = user['last_location_change']
                    if isinstance(last_change, str):
                        last_change = datetime.fromisoformat(last_change)
                    
                    if datetime.now() - last_change < timedelta(days=30):
                        return False, "Локацию можно менять максимум 15 раз в месяц"
                    else:
                        Database.execute_query(
                            "UPDATE users SET location_changes_month = 0 WHERE user_id = %s",
                            (user_id,)
                        )
                except (ValueError, AttributeError):
                    pass
        
        return True, ""
    
    @staticmethod
    def update_user_field(user_id: int, field: str, value, increment_changes: bool = False):
        """Безопасное обновление полей пользователя"""
        # Белый список разрешенных полей для безопасности
        allowed_fields = [
            'name', 'age', 'gender', 'current_city', 'search_city', 'search_radius',
            'search_all_ukraine', 'dating_goal', 'bio', 'current_lat', 'current_lon',
            'search_lat', 'search_lon', 'is_active', 'is_banned', 'last_active'
        ]
        
        if field not in allowed_fields:
            logger.error(f"Попытка обновить недопустимое поле: {field}")
            return False
        
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
        
        result = Database.execute_query(query, tuple(params))
        
        # Логирование действия пользователя
        user_logger.info(f"User {user_id} updated field {field} to {value}")
        
        return result is not None

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
        
        # Фильтр по поиску
        if user.get('search_all_ukraine', False) or (user['search_city'] and user['search_city'].lower() == 'вся украина'):
            # Поиск по всей Украине - без дополнительных фильтров
            pass
        else:
            # Ищем тех, кто находится в городе поиска пользователя
            # ИЛИ тех, кто ищет в текущем городе пользователя
            # Дополнительно проверяем расстояние если есть координаты
            if user.get('search_lat') and user.get('search_lon'):
                # Используем формулу расстояния между координатами (примерное расстояние)
                query += """
                AND (
                    (u.current_city ILIKE %s)
                    OR (u.search_city ILIKE %s)
                    OR (
                        u.current_lat IS NOT NULL AND u.current_lon IS NOT NULL
                        AND (
                            6371 * acos(
                                cos(radians(%s)) * cos(radians(u.current_lat)) *
                                cos(radians(u.current_lon) - radians(%s)) +
                                sin(radians(%s)) * sin(radians(u.current_lat))
                            )
                        ) <= %s
                    )
                )
                """
                params.extend([
                    f"%{user['search_city']}%", f"%{user['current_city']}%",
                    user['search_lat'], user['search_lon'], user['search_lat'],
                    user.get('search_radius', 50)
                ])
            else:
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
        # Проверяем, не подавал ли пользователь уже жалобу на этого же человека
        existing = Database.execute_query(
            "SELECT COUNT(*) as count FROM complaints WHERE from_user = %s AND against_user = %s",
            (from_user, against_user), "one"
        )
        
        if existing and existing['count'] > 0:
            return False  # Уже есть жалоба от этого пользователя
        
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
        
        # Проверяем количество уникальных жалоб от разных пользователей
        complaint_count = Database.execute_query(
            """SELECT COUNT(DISTINCT from_user) as count FROM complaints 
               WHERE against_user = %s AND status = 'pending'""",
            (against_user,), "one"
        )
        
        # Логируем жалобу
        user_logger.info(f"Complaint filed: user {from_user} complained about user {against_user} for {reason}")
        
        # Автоматическая блокировка после 5 жалоб от разных пользователей
        if complaint_count and complaint_count['count'] >= 5:
            Database.execute_query(
                "UPDATE users SET is_banned = TRUE WHERE user_id = %s",
                (against_user,)
            )
            user_logger.warning(f"User {against_user} automatically banned after {complaint_count['count']} complaints")
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
    await query.answer()
    
    gender = "male" if query.data == "gender_male" else "female"
    context.user_data['gender'] = gender
    
    await query.edit_message_text("В каком городе вы находитесь сейчас? (напишите название)")
    return CURRENT_CITY

# Регистрация - текущий город
async def get_current_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = update.message.text.strip()
    if len(city) < 2:
        await update.message.reply_text("Введите корректное название города:")
        return CURRENT_CITY
    
    context.user_data['current_city'] = city
    # Здесь можно добавить геокодинг для получения координат
    
    await update.message.reply_text("В каком городе вы хотите искать знакомства? (или напишите 'вся украина')")
    return SEARCH_CITY

# Регистрация - город поиска
async def get_search_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = update.message.text.strip()
    if len(city) < 2:
        await update.message.reply_text("Введите корректное название города:")
        return SEARCH_CITY
    
    context.user_data['search_city'] = city
    
    if city.lower() == 'вся украина':
        context.user_data['search_radius'] = 0
        context.user_data['search_all_ukraine'] = True
        
        # Переходим сразу к целям знакомства
        keyboard = []
        for key, value in DATING_GOALS.items():
            keyboard.append([InlineKeyboardButton(value, callback_data=f"goal_{key}")])
        
        await update.message.reply_text(
            "Выберите цель знакомства:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return DATING_GOAL
    else:
        context.user_data['search_all_ukraine'] = False
        keyboard = [
            [InlineKeyboardButton("10 км", callback_data="radius_10")],
            [InlineKeyboardButton("25 км", callback_data="radius_25")],
            [InlineKeyboardButton("50 км", callback_data="radius_50")],
            [InlineKeyboardButton("100 км", callback_data="radius_100")]
        ]
        
        await update.message.reply_text(
            "Выберите радиус поиска:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return SEARCH_RADIUS

# Регистрация - радиус поиска
async def get_search_radius(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    radius = int(query.data.split("_")[1])
    context.user_data['search_radius'] = radius
    
    keyboard = []
    for key, value in DATING_GOALS.items():
        keyboard.append([InlineKeyboardButton(value, callback_data=f"goal_{key}")])
    
    await query.edit_message_text(
        "Выберите цель знакомства:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return DATING_GOAL

# Регистрация - цель знакомства
async def get_dating_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    goal = query.data.split("_")[1]
    context.user_data['dating_goal'] = goal
    
    await query.edit_message_text("Напишите немного о себе (до 500 символов):")
    return BIO

# Регистрация - био
async def get_bio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bio = update.message.text.strip()[:500]
    if len(bio) < 10:
        await update.message.reply_text("Описание должно быть минимум 10 символов. Расскажите о себе подробнее:")
        return BIO
    
    context.user_data['bio'] = bio
    await update.message.reply_text(
        "Теперь загрузите ваши фотографии (от 1 до 5 фото). Отправьте первое фото:"
    )
    context.user_data['photos'] = []
    return PHOTO

# Регистрация - фотографии
async def get_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Пожалуйста, отправьте фотографию:")
        return PHOTO
    
    photo_id = update.message.photo[-1].file_id
    photos = context.user_data.get('photos', [])
    photos.append(photo_id)
    context.user_data['photos'] = photos
    
    if len(photos) < 5:
        keyboard = [
            [InlineKeyboardButton("✅ Закончить", callback_data="finish_photos")]
        ]
        await update.message.reply_text(
            f"Фото {len(photos)}/5 загружено. Загрузите еще одно фото или нажмите 'Закончить':",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return PHOTO
    else:
        return await finish_registration(update, context)

# Завершение добавления фото
async def finish_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    return await finish_registration(update, context)

# Завершение регистрации
async def finish_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = {
        'user_id': update.effective_user.id,
        'username': update.effective_user.username,
        **context.user_data
    }
    
    if UserManager.create_user(user_data):
        # Сохраняем фотографии
        for i, photo_id in enumerate(context.user_data.get('photos', [])):
            UserManager.add_photo(user_data['user_id'], photo_id, i == 0)  # Первое фото - главное
        
        message_text = "Профиль успешно создан! Добро пожаловать в бот знакомств."
        
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(
                message_text,
                reply_markup=create_main_menu()
            )
        else:
            await update.message.reply_text(
                message_text,
                reply_markup=create_main_menu()
            )
    else:
        await update.message.reply_text("Ошибка создания профиля. Попробуйте еще раз с /start")
    
    return ConversationHandler.END

# Просмотр анкет
async def browse_profiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
    else:
        user_id = update.effective_user.id
    
    if UserManager.is_user_banned(user_id):
        text = "Ваш аккаунт заблокирован."
        if query:
            await query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return
    
    candidates = MatchManager.find_candidates(user_id)
    
    if not candidates:
        text = "Анкеты закончились! Попробуйте позже или измените параметры поиска."
        keyboard = create_main_menu()
        if query:
            await query.edit_message_text(text, reply_markup=keyboard)
        else:
            await update.message.reply_text(text, reply_markup=keyboard)
        return
    
    candidate = candidates[0]
    text = format_profile_text(candidate)
    keyboard = create_browse_keyboard(candidate['user_id'])
    
    # Получаем фотографии пользователя
    photos = UserManager.get_user_photos(candidate['user_id'])
    
    if query:
        await query.message.delete()
    
    if photos:
        try:
            if len(photos) == 1:
                # Одно фото
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=photos[0]['photo_id'],
                    caption=text,
                    reply_markup=keyboard
                )
            else:
                # Несколько фото - отправляем медиа-группу
                media = []
                for i, photo in enumerate(photos[:5]):  # Максимум 5 фото в группе
                    if i == 0:
                        media.append(InputMediaPhoto(photo['photo_id'], caption=text))
                    else:
                        media.append(InputMediaPhoto(photo['photo_id']))
                
                await context.bot.send_media_group(chat_id=user_id, media=media)
                await context.bot.send_message(user_id, "Выберите действие:", reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Ошибка отправки фото: {e}")
            await context.bot.send_message(user_id, text, reply_markup=keyboard)
    else:
        await context.bot.send_message(user_id, text, reply_markup=keyboard)

# Обработка лайка
async def handle_like(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    target_id = int(query.data.split('_')[1])
    
    # Отмечаем как просмотренный
    MatchManager.mark_viewed(user_id, target_id)
    
    # Ставим лайк
    is_match = MatchManager.add_like(user_id, target_id)
    
    if is_match:
        target_user = UserManager.get_user(target_id)
        current_user = UserManager.get_user(user_id)
        
        # Уведомляем о матче
        await query.message.delete()
        await context.bot.send_message(
            user_id,
            f"🎉 Взаимная симпатия с {target_user['name']}!\n\n"
            f"Контакт: @{target_user['username'] or 'скрыт'}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👀 Смотреть дальше", callback_data="browse")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]
            ])
        )
        
        # Уведомляем второго пользователя
        try:
            await context.bot.send_message(
                target_id,
                f"🎉 Взаимная симпатия с {current_user['name']}!\n\n"
                f"Контакт: @{current_user['username'] or 'скрыт'}"
            )
        except:
            pass
    else:
        await query.message.delete()
        await context.bot.send_message(
            user_id,
            "❤️ Лайк отправлен!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👀 Смотреть дальше", callback_data="browse")]
            ])
        )

# Обработка пропуска
async def handle_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    target_id = int(query.data.split('_')[1])
    
    # Отмечаем как просмотренный
    MatchManager.mark_viewed(user_id, target_id)
    
    # Показываем следующую анкету
    await query.message.delete()
    await browse_profiles(update, context)

# Показ матчей
async def show_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    matches = MatchManager.get_matches(query.from_user.id)
    
    if not matches:
        await query.edit_message_text(
            "У вас пока нет матчей. Ставьте больше лайков!",
            reply_markup=create_main_menu()
        )
        return
    
    text = "❤️ Ваши матчи:\n\n"
    for match in matches[:10]:
        username = f"@{match['username']}" if match['username'] else "контакт скрыт"
        text += f"• {match['name']}, {match['age']} — {username}\n"
    
    await query.edit_message_text(text, reply_markup=create_main_menu())

# Показ профиля
async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = UserManager.get_user(query.from_user.id)
    if not user:
        await query.edit_message_text("Профиль не найден")
        return
    
    text = format_profile_text(user)
    await query.edit_message_text(text, reply_markup=create_main_menu())

# Главное меню
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Главное меню:",
        reply_markup=create_main_menu()
    )

# Жалоба на пользователя
async def handle_complaint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    target_id = int(query.data.split('_')[1])
    user_id = query.from_user.id
    
    can_complain, message = ComplaintManager.can_file_complaint(user_id)
    if not can_complain:
        await query.answer(message, show_alert=True)
        return
    
    keyboard = [
        [InlineKeyboardButton("🔞 Неподходящий контент", callback_data=f"complain_inappropriate_{target_id}")],
        [InlineKeyboardButton("🤖 Бот/фейк", callback_data=f"complain_fake_{target_id}")],
        [InlineKeyboardButton("😠 Оскорбления", callback_data=f"complain_abuse_{target_id}")],
        [InlineKeyboardButton("💰 Спам/реклама", callback_data=f"complain_spam_{target_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="browse")]
    ]
    
    await query.edit_message_text(
        "Выберите причину жалобы:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# Обработка жалобы
async def process_complaint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data_parts = query.data.split('_')
    reason = data_parts[1]
    target_id = int(data_parts[2])
    user_id = query.from_user.id
    
    reason_text = {
        'inappropriate': 'Неподходящий контент',
        'fake': 'Бот/фейк аккаунт',
        'abuse': 'Оскорбления',
        'spam': 'Спам/реклама'
    }.get(reason, reason)
    
    was_banned = ComplaintManager.file_complaint(user_id, target_id, reason_text)
    
    # Уведомляем админов
    target_user = UserManager.get_user(target_id)
    complainer = UserManager.get_user(user_id)
    
    admin_message = f"🚨 ЖАЛОБА\n\n"
    admin_message += f"От: {complainer['name']} (@{complainer['username']}, ID: {user_id})\n"
    admin_message += f"На: {target_user['name']} (@{target_user['username']}, ID: {target_id})\n"
    admin_message += f"Причина: {reason_text}\n"
    if was_banned:
        admin_message += f"⚠️ ПОЛЬЗОВАТЕЛЬ АВТОМАТИЧЕСКИ ЗАБЛОКИРОВАН (много жалоб)"
    
    keyboard = [
        [InlineKeyboardButton("Связаться", url=f"tg://user?id={target_id}")],
        [InlineKeyboardButton("Заблокировать", callback_data=f"admin_ban_{target_id}")]
    ]
    
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                admin_message,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except:
            pass
    
    result_text = "✅ Жалоба отправлена администрации."
    if was_banned:
        result_text += "\n⚠️ Пользователь заблокирован автоматически."
    
    await query.edit_message_text(
        result_text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👀 Смотреть дальше", callback_data="browse")]
        ])
    )

# Админские команды
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    # Получаем статистику
    total_users = Database.execute_query("SELECT COUNT(*) as count FROM users", fetch="one")
    active_users = Database.execute_query("SELECT COUNT(*) as count FROM users WHERE is_active = TRUE AND is_banned = FALSE", fetch="one")
    total_matches = Database.execute_query("SELECT COUNT(*) as count FROM matches", fetch="one")
    pending_complaints = Database.execute_query("SELECT COUNT(*) as count FROM complaints WHERE status = 'pending'", fetch="one")
    
    text = f"""📊 СТАТИСТИКА БОТА

👥 Всего пользователей: {total_users['count'] if total_users else 0}
✅ Активных: {active_users['count'] if active_users else 0}
❤️ Всего матчей: {total_matches['count'] if total_matches else 0}
⚠️ Жалоб на рассмотрении: {pending_complaints['count'] if pending_complaints else 0}"""
    
    await update.message.reply_text(text)

async def admin_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    if len(context.args) != 1:
        await update.message.reply_text("Использование: /ban <user_id>")
        return
    
    try:
        user_id = int(context.args[0])
        Database.execute_query("UPDATE users SET is_banned = TRUE WHERE user_id = %s", (user_id,))
        await update.message.reply_text(f"✅ Пользователь {user_id} заблокирован")
    except ValueError:
        await update.message.reply_text("Неверный ID пользователя")

async def admin_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    if len(context.args) != 1:
        await update.message.reply_text("Использование: /unban <user_id>")
        return
    
    try:
        user_id = int(context.args[0])
        Database.execute_query("UPDATE users SET is_banned = FALSE WHERE user_id = %s", (user_id,))
        await update.message.reply_text(f"✅ Пользователь {user_id} разблокирован")
    except ValueError:
        await update.message.reply_text("Неверный ID пользователя")

async def admin_complaints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    complaints = Database.execute_query(
        """SELECT c.*, u1.name as complainant_name, u2.name as target_name 
           FROM complaints c
           JOIN users u1 ON c.from_user = u1.user_id
           JOIN users u2 ON c.against_user = u2.user_id
           WHERE c.status = 'pending'
           ORDER BY c.created_at DESC LIMIT 10""",
        fetch="all"
    )
    
    if not complaints:
        await update.message.reply_text("Нет жалоб для рассмотрения")
        return
    
    text = "🚨 ЖАЛОБЫ НА РАССМОТРЕНИИ:\n\n"
    for complaint in complaints:
        text += f"ID: {complaint['id']}\n"
        text += f"От: {complaint['complainant_name']} (ID: {complaint['from_user']})\n"
        text += f"На: {complaint['target_name']} (ID: {complaint['against_user']})\n"
        text += f"Причина: {complaint['reason']}\n"
        text += f"Дата: {complaint['created_at']}\n\n"
    
    await update.message.reply_text(text)

# Отмена операций
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Операция отменена.")
    return ConversationHandler.END

def main():
    """Запуск бота"""
    # Инициализация базы данных
    Database.init_database()
    
    # Создание приложения
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Обработчик регистрации
    registration_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states={
            CAPTCHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_captcha)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_age)],
            GENDER: [CallbackQueryHandler(get_gender, pattern=r"^gender_")],
            CURRENT_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_current_city)],
            SEARCH_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_search_city)],
            SEARCH_RADIUS: [CallbackQueryHandler(get_search_radius, pattern=r"^radius_")],
            DATING_GOAL: [CallbackQueryHandler(get_dating_goal, pattern=r"^goal_")],
            BIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_bio)],
            PHOTO: [
                MessageHandler(filters.PHOTO, get_photo),
                CallbackQueryHandler(finish_photos, pattern="finish_photos")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    
    # Добавляем обработчики
    application.add_handler(registration_handler)
    application.add_handler(CallbackQueryHandler(browse_profiles, pattern="browse"))
    application.add_handler(CallbackQueryHandler(handle_like, pattern=r"^like_\d+"))
    application.add_handler(CallbackQueryHandler(handle_skip, pattern=r"^skip_\d+"))
    application.add_handler(CallbackQueryHandler(show_matches, pattern="matches"))
    application.add_handler(CallbackQueryHandler(show_profile, pattern="profile"))
    application.add_handler(CallbackQueryHandler(main_menu, pattern="main_menu"))
    application.add_handler(CallbackQueryHandler(handle_complaint, pattern=r"^complaint_\d+"))
    application.add_handler(CallbackQueryHandler(process_complaint, pattern=r"^complain_"))
    
    # Админские команды
    application.add_handler(CommandHandler("stats", admin_stats))
    application.add_handler(CommandHandler("ban", admin_ban))
    application.add_handler(CommandHandler("unban", admin_unban))
    application.add_handler(CommandHandler("complaints", admin_complaints))
    
    # Команды
    application.add_handler(CommandHandler("browse", browse_profiles))
    application.add_handler(CommandHandler("matches", show_matches))
    application.add_handler(CommandHandler("profile", show_profile))
    
    logger.info("Бот запускается...")
    
    # Запуск polling
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()