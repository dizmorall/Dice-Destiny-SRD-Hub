import os
import secrets
import json
import re
from datetime import datetime
from markupsafe import Markup
import markdown
from PIL import Image
from flask import Flask, render_template, request, flash, redirect, url_for, abort, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import StringField, PasswordField, BooleanField, SubmitField, TextAreaField, FileField, SelectField
from wtforms.validators import DataRequired, Length, EqualTo, ValidationError, Optional, Regexp
from flask_wtf.file import FileAllowed
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from config import Config
from datetime import datetime, timezone
import click

# --- 1. Инициализация приложения и расширений ---
app = Flask(__name__, instance_relative_config=True)
app.config.from_object(Config)

@app.template_filter('markdown')
def markdown_to_html_filter(text):
    if text:
        return Markup(markdown.markdown(text, extensions=['fenced_code', 'nl2br']))
    return ''

@app.context_processor
def inject_now():
    return {'now': datetime.now(timezone.utc)}

try:
    os.makedirs(app.instance_path)
except OSError:
    pass

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = "Пожалуйста, войдите, чтобы получить доступ к этой странице."
login_manager.login_message_category = "info"

# --- Словари-маппинги ---
SCHOOL_NAMES = {
    "abj": "Ограждение", "abjuration": "Ограждение",
    "con": "Сотворение", "conjuration": "Сотворение",
    "div": "Прорицание", "divination": "Прорицание",
    "enc": "Очарование", "enchantment": "Очарование",
    "evo": "Воплощение", "evocation": "Воплощение",
    "ill": "Иллюзия", "illusion": "Иллюзия",
    "nec": "Некромантия", "necromancy": "Некромантия",
    "trs": "Преобразование", "transmutation": "Преобразование"
}

CLASS_NAMES = {
    "bard": "Бард", "cleric": "Жрец", "druid": "Друид",
    "paladin": "Паладин", "ranger": "Следопыт", "sorcerer": "Чародей",
    "warlock": "Колдун", "wizard": "Волшебник", "artificer": "Изобретатель"
}

HOMEBREW_TOPICS = {
    'classes': 'Новые классы и архетипы',
    'species': 'Новые расы и подрасы',
    'spells': 'Новые заклинания',
    'items': 'Магические предметы',
    'monsters': 'Монстры и существа',
    'adventures': 'Приключения и модули',
    'rules': 'Правила и механики (хомрулы)',
    'settings': 'Миры и сеттинги',
    'discussion': 'Общее обсуждение и оффтоп'
}

# --- УНИВЕРСАЛЬНАЯ ФУНКЦИЯ СОРТИРОВКИ УРОВНЕЙ ---
def spell_level_sort_key(item):
    level_to_process = None
    if isinstance(item, dict):
        level_to_process = item.get('level_raw', 99)
    elif isinstance(item, (int, str)):
        level_to_process = item
    else:
        return 99

    level_str = str(level_to_process).lower()
    if level_str == 'cantrip' or level_str == '0':
        return 0
    try:
        return int(level_str)
    except ValueError:
        return 99

# --- Функция для загрузки JSON ---
def load_srd_data(filename):
    filepath = os.path.join(app.root_path, 'data', filename)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"WARNING: SRD data file not found: {filepath}")
        flash(f"Внимание: Файл данных SRD '{filename}' не найден.", 'warning')
        return []
    except json.JSONDecodeError:
        print(f"ERROR: Failed to decode JSON from file: {filepath}")
        flash(f"Ошибка: Неверный формат данных в файле '{filename}'.", 'danger')
        return []

# --- Загрузка и ПРЕДВАРИТЕЛЬНАЯ ОБРАБОТКА данных SRD ---
SRD_SPELLS_LIST_RAW = load_srd_data('spells.json')
SRD_SPELLS_LIST = [] #
SRD_SPELLS_DICT = {} 

for spell_raw in SRD_SPELLS_LIST_RAW:
    spell_name_original = spell_raw.get('name') # Исходное название, может быть с англ. в скобках
    slug = None

    if spell_name_original:
        name_for_slug = spell_name_original
        # Убираем текст в квадратных скобках для генерации слага
        if '[' in name_for_slug and ']' in name_for_slug:
             start_bracket = name_for_slug.find('[')
             end_bracket = name_for_slug.find(']')
             name_for_slug = name_for_slug[:start_bracket].strip() + name_for_slug[end_bracket+1:].strip()

        # Очищаем от лишних символов для слага
        cleaned_name_for_slug = re.sub(r'[^\w\s-]', '', name_for_slug.strip()) # Оставляем буквы, цифры, пробелы, дефисы
        slug = cleaned_name_for_slug.lower().replace(' ', '-')
        # Дополнительно убираем множественные дефисы, если образовались
        slug = re.sub(r'-+', '-', slug).strip('-')


    if not slug or not spell_name_original:
        print(f"WARNING: Пропущено заклинание из-за отсутствия имени или невозможности создать слаг: {spell_name_original}")
        continue

    # Извлекаем английское название из скобок, если есть
    en_name = None
    if '[' in spell_name_original and ']' in spell_name_original:
        en_name_match = re.search(r'\[(.*?)\]', spell_name_original)
        if en_name_match:
            en_name = en_name_match.group(1).strip()

    system_data = spell_raw.get('system', {}) # Получаем объект system один раз

    level_raw = system_data.get('level')
    level_display = 'Заговор' # По умолчанию для уровня 0 или 'cantrip'
    if level_raw is not None and str(level_raw).lower() != 'cantrip' and str(level_raw) != '0':
        level_display = f"{level_raw} круг"


    school_raw = system_data.get('school', '').lower()
    school_display = SCHOOL_NAMES.get(school_raw, school_raw.capitalize() if school_raw else '?')


    activation_data = system_data.get('activation', {})
    activation_type_raw = activation_data.get('type', '').lower() # Приводим к нижнему регистру для надежности
    activation_cost = activation_data.get('cost')
    activation_condition = activation_data.get('condition') # Получаем условие, если есть
    casting_time_display = '?' # Значение по умолчанию

    if activation_type_raw:
        if activation_type_raw == 'reaction':
            activation_type_display = 'реакция'
            if activation_condition: # Если есть условие для реакции
                activation_type_display += f" ({activation_condition})"
        elif activation_type_raw == 'action':
            activation_type_display = '1 действие' # Обычно "1 действие"
        elif activation_type_raw == 'bonus':
            activation_type_display = '1 бонусное действие' # Обычно "1 бонусное действие"
        elif activation_type_raw == 'minute':
            activation_type_display = f"{activation_cost or '1'} минута/минут" # Указываем количество минут
        elif activation_type_raw == 'hour':
            activation_type_display = f"{activation_cost or '1'} час/часов"
        else: # Если тип не опознан, просто капитализируем
            activation_type_display = activation_type_raw.capitalize()

        casting_time_display = activation_type_display

        if activation_cost and activation_type_raw not in ['reaction', 'action', 'bonus', 'minute', 'hour']:
            casting_time_display += f" ({activation_cost})"
        elif activation_type_raw == 'reaction' and not activation_condition and activation_cost:
            # Если для реакции есть cost, но нет condition (маловероятно, но на всякий случай)
            casting_time_display += f" ({activation_cost})"

    range_data = system_data.get('range', {})
    range_value = range_data.get('value')
    range_units_raw = range_data.get('units', '')
    range_display = '?'
    if range_units_raw:
        if range_units_raw.lower() == 'self': range_display = "На себя"
        elif range_units_raw.lower() == 'touch': range_display = "Касание"
        elif range_units_raw.lower() == 'ft' and range_value is not None: range_display = f"{range_value} фт."
        elif range_units_raw.lower() == 'any': # Часто для "Self (X-foot Y)"
            target_data = system_data.get('target', {})
            target_type = target_data.get('type')
            target_val = target_data.get('value')
            target_units = target_data.get('units', '').replace('ft', 'фт.')
            if target_type and target_val and target_units:
                range_display = f"На себя ({target_val} {target_units} {target_type})"
            elif not range_value : # Если 'any' и нет value, но есть type 'self'
                if system_data.get('target', {}).get('type', '') == 'self':
                    range_display = "На себя"
        elif range_value is not None: # Общий случай, если есть value и units
            range_display = f"{range_value} {range_units_raw.replace('ft', 'фт.')}"
        elif range_units_raw: # Если есть только units, но не "self" или "touch"
            range_display = range_units_raw.capitalize()


    components_data = system_data.get('components', {})
    components_raw_str = components_data.get('raw')
    components_display_parts = []
    if components_data.get('vocal'): components_display_parts.append("В")
    if components_data.get('somatic'): components_display_parts.append("С")
    if components_data.get('material'): components_display_parts.append("М")

    components_display_str = ", ".join(components_display_parts)

    # Материалы: берем из system.materials.value ИЛИ из system.components.materials_needed
    materials_value_str = system_data.get('materials', {}).get('value')
    materials_needed_list = components_data.get('materials_needed', [])

    actual_materials_str = None
    if materials_value_str:
        actual_materials_str = materials_value_str
    elif materials_needed_list:
        actual_materials_str = ", ".join(materials_needed_list)

    if components_data.get('material') and actual_materials_str:
        # Добавляем материалы в скобках, только если компонент М указан
        components_display_str += f" ({actual_materials_str})"
    elif not components_display_str and not actual_materials_str and components_raw_str:
        # Если V, S, M не определены, но есть raw строка, используем ее
        components_display_str = components_raw_str
    elif not components_display_str:
        components_display_str = "?"


    duration_data = system_data.get('duration', {})
    duration_value = duration_data.get('value')
    duration_units_raw = duration_data.get('units', '')
    duration_display = '?'
    if duration_units_raw:
        duration_units_display = duration_units_raw.replace('inst', 'Мгновенная').replace('minute', 'минута/минут').replace('hour', 'час/часов').replace('round', 'раунд/раундов').replace('day','день/дней').replace('special','Особая')
        if duration_value:
            duration_display = f"{duration_value} {duration_units_display}"
        else: # Если нет value, но есть units (например, "inst")
            duration_display = duration_units_display


    concentration = components_data.get('concentration', False)


    higher_levels_data = system_data.get('description', {}).get('higher_levels', None) # Может быть строкой или списком
    higher_level_list = []
    if isinstance(higher_levels_data, str) and higher_levels_data:
        higher_level_list.append(higher_levels_data)
    elif isinstance(higher_levels_data, list):
        higher_level_list = [item for item in higher_levels_data if item]


    spell_processed = {
        'name': spell_name_original,
        'en_name': en_name,
        'slug': slug,
        'system': system_data,
        'level_raw': level_raw,
        'level_display': level_display,
        'school_raw': school_raw,
        'school_display': school_display,
        'casting_time_display': casting_time_display,
        'range_display': range_display,
        'components_display': components_display_str, # Используем новую строку
        'duration_display': duration_display,
        'concentration': concentration,
        'ritual': system_data.get('ritual', False), # Из system, а не components
        'classes_raw': spell_raw.get('classes', []),
        'classes_display': [CLASS_NAMES.get(cls.lower(), cls.capitalize()) for cls in spell_raw.get('classes', [])],
        'description': system_data.get('description', {}).get('value', ''),
        'higher_level_list': higher_level_list,
        'tags': spell_raw.get('tags', []),
        'type': spell_raw.get('type', '?')
    }


    SRD_SPELLS_LIST.append(spell_processed)
    SRD_SPELLS_DICT[slug] = spell_processed

ALL_HOMEBREW_CATEGORIES_FOR_FILTER = list(HOMEBREW_TOPICS.items())

ALL_SPELL_CLASSES = sorted(list(set(cls for spell in SRD_SPELLS_LIST for cls in spell.get('classes_raw', []))))
ALL_SPELL_LEVELS = sorted(list(set(spell.get('level_raw', 99) for spell in SRD_SPELLS_LIST)), key=spell_level_sort_key)
ALL_SPELL_SCHOOLS = sorted(list(set(spell.get('school_raw') for spell in SRD_SPELLS_LIST if spell.get('school_raw'))))

# --- 2. Модели Базы Данных (SQLAlchemy) ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    avatar_filename = db.Column(db.String(128), default='default.png')
    registered_on = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    role = db.Column(db.String(20), default='user', nullable=False)
    description = db.Column(db.Text, nullable=True)

    posts = db.relationship('HomebrewPost', backref='author', lazy='dynamic',
                            cascade="all, delete-orphan")
    comments = db.relationship('Comment', backref='author', lazy='dynamic',
                               cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def get_avatar(self):
        return url_for('static', filename=f'avatars/{self.avatar_filename}')

    def __repr__(self):
        return f'<User {self.username}>'

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('homebrew_post.id'), nullable=True)
    srd_page_type = db.Column(db.String(50), nullable=True) 
    srd_page_slug = db.Column(db.String(100), nullable=True) 
    parent_comment_id = db.Column(db.Integer, db.ForeignKey('comment.id'), nullable=True)
    replies = db.relationship(
        'Comment', 
        backref=db.backref('parent_comment_obj', remote_side=[id]), 
                                                                
        lazy='dynamic',
        cascade="all, delete-orphan" 
    )

    def __repr__(self):
        return f'<Comment {self.id} by User {self.user_id}>'

class HomebrewPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category = db.Column(db.String(50), nullable=False, index=True)

    comments = db.relationship('Comment',
                               backref='post', 
                               lazy='dynamic',
                               cascade="all, delete-orphan",
                               foreign_keys=[Comment.post_id]) 

    def __repr__(self):
        return f'<HomebrewPost {self.title}>'

# --- 3. Flask-Login user_loader ---
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- 4. Формы (Flask-WTF) ---

# --- Валидаторы ---
def validate_username_chars(form, field):
    if not field.data.isalnum():
        raise ValidationError('Имя пользователя может содержать только буквы латинского алфавита и цифры.')

def validate_username_unique(form, field):
    """Проверяет уникальность username при регистрации или смене."""
    editing_user = getattr(form, '_editing_user', None)
    user = User.query.filter(User.username.ilike(field.data)).first()
    if user:
        if not editing_user or user.id != editing_user.id:
            raise ValidationError('Это имя пользователя уже занято.')

# --- Формы ---
class LoginForm(FlaskForm):
    username = StringField('Логин', validators=[DataRequired(), Length(min=3, max=64)])
    password = PasswordField('Пароль', validators=[DataRequired()])
    remember_me = BooleanField('Запомнить меня')
    submit = SubmitField('Войти')

class RegistrationForm(FlaskForm):
    username = StringField('Логин', validators=[
        DataRequired(), Length(min=3, max=64),
        validate_username_chars,
        validate_username_unique
        ])
    password = PasswordField('Пароль', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Подтвердите Пароль', validators=[
        DataRequired(), EqualTo('password', message='Пароли должны совпадать.')
        ])
    submit = SubmitField('Зарегистрироваться')

class CommentForm(FlaskForm):
    content = TextAreaField('Комментарий', validators=[DataRequired(), Length(min=1, max=5000)])
    submit = SubmitField('Отправить')

class ReplyForm(FlaskForm):
    content = TextAreaField('Ответ', validators=[DataRequired(), Length(min=1, max=5000)])
    submit = SubmitField('Ответить')

class HomebrewPostForm(FlaskForm):
    title = StringField('Заголовок поста', validators=[DataRequired(), Length(min=5, max=150)])
    category = SelectField('Тема (Категория)', choices=[(key, value) for key, value in HOMEBREW_TOPICS.items()],
                           validators=[DataRequired(message="Пожалуйста, выберите тему.")])
    content = TextAreaField('Содержание поста', validators=[DataRequired(), Length(min=20)])
    submit = SubmitField('Опубликовать пост')


class ProfileUpdateForm(FlaskForm):
    username = StringField('Имя пользователя', validators=[
        DataRequired(), Length(min=3, max=64),
        validate_username_chars,
        validate_username_unique
        ])
    description = TextAreaField('О себе (макс. 500 симв.)', validators=[
        Optional(), Length(max=500)
        ])
    avatar = FileField('Сменить Аватар (png, jpg, gif)', validators=[
        Optional(),
        FileAllowed(app.config['ALLOWED_EXTENSIONS'], 'Разрешены только изображения!')
    ])
    submit = SubmitField('Сохранить изменения профиля')

    def __init__(self, user_to_edit, *args, **kwargs):
        super(ProfileUpdateForm, self).__init__(*args, **kwargs)
        self._editing_user = user_to_edit 

class ChangePasswordForm(FlaskForm):
    old_password = PasswordField('Текущий пароль', validators=[DataRequired()])
    new_password = PasswordField('Новый пароль', validators=[DataRequired(), Length(min=6)])
    confirm_new_password = PasswordField('Подтвердите новый пароль', validators=[
        DataRequired(), EqualTo('new_password', message='Новые пароли должны совпадать.')
        ])
    submit_password = SubmitField('Сменить пароль') 

# --- 5. Helper Функции ---

def save_picture(form_picture):
    """Сохраняет аватарку, удаляет старую (если не дефолтная) и возвращает имя файла."""
    random_hex = secrets.token_hex(8)
    _, f_ext = os.path.splitext(form_picture.filename)
    picture_fn = random_hex + f_ext.lower()
    picture_path = os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], picture_fn)

    output_dir = os.path.dirname(picture_path)
    os.makedirs(output_dir, exist_ok=True)

    try:
        output_size = (125, 125)
        img = Image.open(form_picture)
        img.thumbnail(output_size)
        img.save(picture_path)

        if current_user.avatar_filename != 'default.png':
            old_picture_path = os.path.join(app.root_path, app.config['UPLOAD_FOLDER'], current_user.avatar_filename)
            if os.path.exists(old_picture_path):
                try:
                    os.remove(old_picture_path)
                except Exception as e:
                    print(f"Error deleting old avatar '{current_user.avatar_filename}': {e}")

        return picture_fn
    except Exception as e:
         print(f"Error saving avatar: {e}")
         flash('Ошибка при сохранении аватара.', 'danger')
         return None

def get_comments_with_replies(query_filter):
    """Общая функция для получения комментариев и их ответов."""
    top_level_comments = Comment.query.filter(
        query_filter, Comment.parent_comment_id.is_(None)
    ).order_by(Comment.timestamp.asc()).all()

    comment_ids = [c.id for c in top_level_comments]
    replies_dict = {}
    if comment_ids:
        replies = Comment.query.filter(
            Comment.parent_comment_id.in_(comment_ids)
        ).order_by(Comment.timestamp.asc()).all()
        for reply in replies:
            if reply.parent_comment_id not in replies_dict:
                replies_dict[reply.parent_comment_id] = []
            replies_dict[reply.parent_comment_id].append(reply)

    for comment in top_level_comments:
        comment.loaded_replies = replies_dict.get(comment.id, [])

    return top_level_comments

# --- Данные SRD ---
SRD_CLASSES = {
    'cleric': {
        'name': 'Жрец',
        'en_name': 'Cleric',
        'icon': 'images/classes/cleric_icon.png',
        'description': 'Жрецы — это посредники между миром смертных и далёкими планами богов. Столь же разнообразные, как и боги, которым они служат, жрецы стремятся олицетворять труды своих божеств. Жрец — это не обычный священник, а наделённый божественной магией индивид.',
        'slug': 'cleric',
        'hit_dice': '1к8 за каждый уровень жреца',
        'hp_at_first_level': '8 + ваш модификатор Телосложения',
        'hp_at_higher_levels': '1к8 (или 5) + ваш модификатор Телосложения (минимум 1) за каждый уровень жреца после первого',
        'proficiencies': {
            'armor': 'Лёгкие и средние доспехи, щиты',
            'weapons': 'Всё простое оружие',
            'tools': 'Нет',
            'saving_throws': 'Мудрость, Харизма',
            'skills': 'Выберите два навыка из следующих: История, Проницательность, Медицина, Убеждение и Религия'
        },
        'equipment': [
            '(а) кистень или (б) боевой молот (если владеете)',
            '(а) чешуйчатый доспех, (б) кожаный доспех или (в) кольчуга (если владеете)',
            '(а) лёгкий арбалет и 20 болтов или (б) любое простое оружие',
            '(а) набор священника или (б) набор исследователя подземелий',
            'Щит и священный символ'
        ],
        'features_table': { # SRD включает только Домен Жизни
            1: ["Использование заклинаний", "Божественный домен (Жизнь)", "Дополнительное владение (Тяжелые доспехи)", "Ученик жизни"],
            2: ["Божественный канал (1/отдых)", "Божественный канал: Изгнание нежити", "Божественный канал: Сохранение жизни"],
            3: ["— (Заклинания 2-го круга)"],
            4: ["Увеличение характеристик"],
            5: ["Уничтожение нежити (ПО 1/2)", "— (Заклинания 3-го круга)"],
            6: ["Божественный канал (2/отдых)", "Благословенный целитель (Домен Жизни)"],
            7: ["— (Заклинания 4-го круга)"],
            8: ["Увеличение характеристик", "Уничтожение нежити (ПО 1)", "Божественный удар (Домен Жизни, 1к8)"],
            9: ["— (Заклинания 5-го круга)"],
           10: ["Божественное вмешательство"],
           # ... SRD описывает прогрессию и далее, но умения становятся менее детализированы
           11: ["Уничтожение нежити (ПО 2)", "— (Заклинания 6-го круга)"],
           14: ["Уничтожение нежити (ПО 3)", "Божественный удар (Домен Жизни, 2к8)"],
           17: ["Уничтожение нежити (ПО 4)", "— (Заклинания 9-го круга)"],
           18: ["Божественный канал (3/отдых)"],
           20: ["Увеличение характеристик", "Божественное вмешательство (улучшение)"]
        },
        'detailed_features': {
            'spellcasting': "Как проводник божественной силы, вы можете творить заклинания жреца. См. главу 10 SRD для общих правил использования заклинаний и список заклинаний жреца в SRD.",
            'divine_domain': "Выберите один домен, связанный с вашим божеством. В SRD представлен только Домен Жизни.",
            'life_domain_note': "Домен Жизни фокусируется на яркой позитивной энергии жизни, одной из фундаментальных сил вселенной. Боги, предоставляющие этот домен: Гелиод, Лира, Сильванус.",
            'bonus_proficiency': "(Домен Жизни) Вы получаете владение тяжелыми доспехами.",
            'disciple_of_life': "(Домен Жизни) Ваши заклинания лечения становятся более эффективными. Каждый раз, когда вы используете заклинание 1-го круга или выше для восстановления хитов существу, оно восстанавливает цели дополнительные хиты, равные 2 + круг заклинания.",
            'channel_divinity': "На 2-м уровне вы получаете возможность направлять божественную энергию непосредственно от вашего божества, используя эту энергию для разжигания магических эффектов. Вы начинаете с двумя такими эффектами: Изгнание нежити и эффектом, определяемым вашим доменом (Сохранение жизни для Домена Жизни). Вы можете использовать Божественный канал один раз. Вы должны закончить короткий или продолжительный отдых, чтобы использовать Божественный канал снова. Количество использований увеличивается на 6-м и 18-м уровнях.",
            'turn_undead': "Действием вы демонстрируете свой священный символ и произносите молитву, изгоняющую нежить. Вся нежить, которая может видеть или слышать вас в пределах 30 футов, должна совершить спасбросок Мудрости. Если существо провалило спасбросок, оно становится изгнанным на 1 минуту или пока не получит урон.",
            'preserve_life': "(Домен Жизни) Действием вы демонстрируете свой священный символ и вызываете целительную энергию, которая может восстановить количество хитов, равное вашему уровню жреца, умноженному на пять. Выберите любое количество существ в пределах 30 футов от вас и распределите эти хиты между ними. Это умение не может поднять хиты существа выше половины от его максимума. Вы не можете использовать это умение на нежити и конструктах.",
            'ability_score_improvement': "При достижении 4-го, 8-го, 12-го, 16-го и 19-го уровней вы можете повысить значение одной из ваших характеристик на 2 или двух характеристик на 1. Как обычно, значение характеристики при этом не должно превысить 20.",
            'destroy_undead': "Начиная с 5-го уровня, когда нежить проваливает спасбросок от вашего умения Изгнание нежити, она немедленно уничтожается, если её показатель опасности (ПО) не превышает определённого значения, как указано в таблице.",
            'blessed_healer': "(Домен Жизни, 6 ур.) Исцеляющая энергия, текущая через вас, может исцелять и вас. Когда вы используете заклинание 1-го круга или выше для восстановления хитов существу, отличному от вас, вы восстанавливаете хиты в размере 2 + круг заклинания.",
            'divine_strike': "(Домен Жизни, 8 ур.) Вы получаете способность наполнять удары своим оружием божественной энергией. Один раз в каждый ваш ход, когда вы попадаете по существу атакой оружием, вы можете причинить цели дополнительный урон излучением 1к8. Когда вы достигаете 14-го уровня, дополнительный урон увеличивается до 2к8.",
            'divine_intervention': "Начиная с 10-го уровня, вы можете воззвать к вашему божеству с просьбой о помощи, когда она вам особенно нужна. Мольба о помощи требует от вас действия. Опишите помощь, которую вы ищете, и бросьте к100. Если результат будет меньше или равен вашему уровню жреца, ваше божество вмешается. Мастер определяет природу этого вмешательства; эффект любого заклинания жреца или заклинания домена будет уместен. Если божество вмешивается, вы не можете использовать это умение в течение следующих 7 дней.",
            # Supreme Healing (17 ур.) и улучшенное Вмешательство (20 ур.) для Life Domain не описаны в SRD как фичи, но есть в таблице.
        }
    },
    'fighter': {
        'name': 'Воин',
        'en_name': 'Fighter',
        'icon': 'images/classes/fighter_icon.png',
        'description': 'Воины имеют много общего: непревзойдённое мастерство владения оружием и доспехами, а также доскональное знание боевых искусств. Воины хорошо знакомы со смертью, либо неся её сами, либо глядя ей в лицо и презрительно усмехаясь.',
        'slug': 'fighter',
        'hit_dice': '1к10 за каждый уровень воина',
        'hp_at_first_level': '10 + ваш модификатор Телосложения',
        'hp_at_higher_levels': '1к10 (или 6) + ваш модификатор Телосложения (минимум 1) за каждый уровень воина после первого',
        'proficiencies': {
            'armor': 'Все доспехи, щиты',
            'weapons': 'Простое и воинское оружие',
            'tools': 'Нет',
            'saving_throws': 'Сила, Телосложение',
            'skills': 'Выберите два навыка из следующих: Акробатика, Атлетика, Восприятие, Выживание, Запугивание, История, Проницательность'
        },
        'equipment': [
            '(а) кольчуга или (б) кожаный доспех, длинный лук и 20 стрел',
            '(а) воинское оружие и щит или (б) два воинских оружия',
            '(а) лёгкий арбалет и 20 болтов или (б) два ручных топора',
            '(а) набор исследователя подземелий или (б) набор путешественника'
        ],
        'features_table': { # SRD включает только архетип Чемпион
            1: ["Боевой стиль", "Второе дыхание"],
            2: ["Всплеск действий (1 использование)"],
            3: ["Воинский архетип (Чемпион)", "Улучшенный критический удар"],
            4: ["Увеличение характеристик"],
            5: ["Дополнительная атака (1)"],
            6: ["Увеличение характеристик"],
            7: ["Невероятный атлет (Чемпион)"],
            8: ["Увеличение характеристик"],
            9: ["Непоколебимость (1 использование)"],
           10: ["Дополнительный боевой стиль (Чемпион)"],
           # ... SRD детализирует фичи Чемпиона и дальше
           11: ["Дополнительная атака (2)"],
           12: ["Увеличение характеристик"],
           13: ["Непоколебимость (2 использования)"],
           14: ["Увеличение характеристик"],
           15: ["Превосходный критический удар (Чемпион)"],
           17: ["Всплеск действий (2 использования)", "Непоколебимость (3 использования)"],
           18: ["Выживший (Чемпион)"],
           20: ["Дополнительная атака (3)"]
        },
        'detailed_features': {
            'fighting_style': "Вы принимаете определённый боевой стиль, который становится вашей специализацией. Выберите один из следующих вариантов. Вы не можете выбрать один и тот же стиль дважды, даже если позже у вас появится возможность выбрать новый стиль.\n"
                             "• <strong>Стрельба (Archery):</strong> Вы получаете бонус +2 к броскам атаки дальнобойным оружием.\n"
                             "• <strong>Оборона (Defense):</strong> Пока вы носите доспехи, вы получаете бонус +1 к КД.\n"
                             "• <strong>Дуэлянт (Dueling):</strong> Когда вы держите рукопашное оружие в одной руке и не держите другого оружия, вы получаете бонус +2 к броскам урона этим оружием.\n"
                             "• <strong>Сражение большим оружием (Great Weapon Fighting):</strong> Когда вы выбрасываете 1 или 2 на кости урона при атаке рукопашным оружием, которое вы держите двумя руками, вы можете перебросить эту кость и должны использовать новый результат, даже если он снова равен 1 или 2. Оружие должно иметь свойство «двуручное» или «универсальное», чтобы вы получили это преимущество.\n"
                             "• <strong>Защита (Protection):</strong> Когда существо, которое вы можете видеть, атакует цель, отличную от вас, находящуюся в пределах 5 футов от вас, вы можете реакцией создать помеху броску атаки этого существа. Вы должны при этом держать щит.\n"
                             "• <strong>Сражение двумя оружиями (Two-Weapon Fighting):</strong> Когда вы сражаетесь двумя оружиями, вы можете добавить модификатор характеристики к урону второй атаки.",
            'second_wind': "У вас есть небольшой источник выносливости, который вы можете использовать, чтобы защитить себя. В свой ход бонусным действием вы можете восстановить хиты в размере 1к10 + ваш уровень воина. Использовав это умение, вы должны завершить короткий или продолжительный отдых, прежде чем сможете использовать его снова.",
            'action_surge': "Начиная со 2-го уровня, вы можете заставить себя выйти за пределы обычных возможностей на мгновение. В свой ход вы можете совершить одно дополнительное действие. Использовав это умение, вы должны завершить короткий или продолжительный отдых, прежде чем сможете использовать его снова. Начиная с 17-го уровня, вы можете использовать это умение дважды между периодами отдыха, но только один раз за ход.",
            'martial_archetype': "На 3-м уровне вы выбираете архетип, который отражает ваш подход к бою. В SRD доступен только архетип Чемпион.",
            'champion_note': "Архетип Чемпион фокусируется на развитии грубой физической силы, доведённой до смертоносного совершенства. Те, кто выбирает этот архетип, сочетают строгие тренировки с превосходной атлетикой, чтобы наносить сокрушительные удары.",
            'improved_critical': "(Чемпион) Начиная с 3-го уровня, ваши атаки оружием совершают критическое попадание при выпадении «19» или «20» на к20.",
            'ability_score_improvement': "При достижении 4-го, 6-го, 8-го, 12-го, 14-го, 16-го и 19-го уровней вы можете повысить значение одной из ваших характеристик на 2 или двух характеристик на 1. Как обычно, значение характеристики при этом не должно превысить 20.",
            'extra_attack': "Начиная с 5-го уровня, если вы в свой ход совершаете действие Атака, вы можете совершить две атаки вместо одной. Количество атак увеличивается до трёх на 11-м уровне этого класса и до четырёх на 20-м уровне.",
            'remarkable_athlete': "(Чемпион, 7 ур.) Начиная с 7-го уровня, вы можете добавлять половину вашего бонуса мастерства (округлённую в большую сторону) ко всем проверкам Силы, Ловкости или Телосложения, куда вы ещё не добавили свой бонус мастерства. Кроме того, когда вы совершаете прыжок в длину с разбега, преодолённое расстояние увеличивается на количество футов, равное вашему модификатору Силы.",
            'indomitable': "(9 ур.) Начиная с 9-го уровня, вы можете перебросить спасбросок, который вы провалили. Если вы это сделаете, вы должны использовать новый результат. Вы не можете использовать это умение снова, пока не завершите продолжительный отдых. Вы можете использовать это умение дважды между продолжительными периодами отдыха, начиная с 13-го уровня, и трижды, начиная с 17-го уровня.",
            'additional_fighting_style': "(Чемпион, 10 ур.) На 10-м уровне вы можете выбрать второй Боевой стиль.",
            'superior_critical': "(Чемпион, 15 ур.) Начиная с 15-го уровня, ваши атаки оружием совершают критическое попадание при выпадении «18», «19» или «20» на к20.",
            'survivor': "(Чемпион, 18 ур.) На 18-м уровне вы обретаете вершину стойкости в бою. В начале каждого вашего хода вы восстанавливаете количество хитов, равное 5 + ваш модификатор Телосложения, если у вас осталось не больше половины хитов. Вы не получите этого преимущества, если у вас 0 хитов.",
        }
    },
    'rogue': {
        'name': 'Плут',
        'en_name': 'Rogue',
        'icon': 'images/classes/rogue_icon.png',
        'description': 'Плуты полагаются на умения, скрытность и уязвимые места врагов, чтобы добиться своего. У них достаточно ловкости и хитрости, чтобы незаметно прокрасться куда угодно, но плуты так же хорошо себя чувствуют и в обществе, становясь обманщиками, переговорщиками, шпионами и даже дипломатами.',
        'slug': 'rogue',
        'hit_dice': '1к8 за каждый уровень плута',
        'hp_at_first_level': '8 + ваш модификатор Телосложения',
        'hp_at_higher_levels': '1к8 (или 5) + ваш модификатор Телосложения (минимум 1) за каждый уровень плута после первого',
        'proficiencies': {
            'armor': 'Лёгкие доспехи',
            'weapons': 'Простое оружие, ручные арбалеты, короткие мечи, рапиры, длинные мечи',
            'tools': 'Воровские инструменты',
            'saving_throws': 'Ловкость, Интеллект',
            'skills': 'Выберите четыре навыка из следующих: Акробатика, Атлетика, Обман, Проницательность, Запугивание, Расследование, Восприятие, Выступление, Убеждение, Ловкость рук и Скрытность'
        },
        'equipment': [
            '(а) рапира или (б) короткий меч',
            '(а) короткий лук и колчан с 20 стрелами или (б) короткий меч',
            '(а) набор взломщика, (б) набор исследователя подземелий или (в) набор путешественника',
            'Кожаный доспех, два кинжала и воровские инструменты'
        ],
        'features_table': { # SRD включает только архетип Вор
             1: ["Компетентность", "Скрытая атака (1к6)", "Воровской жаргон"],
             2: ["Хитрое действие"],
             3: ["Плутовской архетип (Вор)", "Быстрые руки", "Работа на втором этаже", "Скрытая атака (2к6)"],
             4: ["Увеличение характеристик"],
             5: ["Сверхъестественное уклонение", "Скрытая атака (3к6)"],
             6: ["Компетентность"],
             7: ["Увёртливость", "Скрытая атака (4к6)"],
             8: ["Увеличение характеристик"],
             9: ["Мастерство вора (Вор)", "Скрытая атака (5к6)"],
            10: ["Увеличение характеристик"],
            11: ["Надёжный талант", "Скрытая атака (6к6)"],
            # ... SRD детализирует фичи и дальше ...
            13: ["Мастер невидимости (Вор)", "Скрытая атака (7к6)"],
            15: ["Скользкий ум", "Скрытая атака (8к6)"],
            17: ["Вор в законе (Вор)", "Скрытая атака (9к6)"],
            18: ["Неуловимость"],
            20: ["Удар удачи", "Скрытая атака (10к6)"]
        },
        'detailed_features': {
            'expertise': "На 1-м уровне выберите два ваших навыка, которыми вы владеете, или одно владение навыком и владение воровскими инструментами. Ваш бонус мастерства удваивается для всех проверок характеристик, которые вы совершаете, используя любое из выбранных владений. На 6-м уровне вы можете выбрать ещё два ваших владения (навыками или воровскими инструментами) и получить для них это же преимущество.",
            'sneak_attack': "Начиная с 1-го уровня, вы знаете, как точно ударить и использовать distrаction врага. Один раз в ход вы можете причинить цели, по которой вы попали атакой, совершённой с преимуществом к броску атаки, пока вы используете фехтовальное или дальнобойное оружие, дополнительный урон 1к6. Вам не нужно преимущество на бросок атаки, если другой враг цели находится в пределах 5 футов от неё, этот враг не недееспособен, и у вас нет помехи на бросок атаки. Количество дополнительного урона увеличивается с вашим уровнем в этом классе, как показано в колонке «Скрытая атака» таблицы.",
            'thieves_cant': "Во время вашего обучения вы изучили воровской жаргон — тайную смесь диалекта, арго и шифра, которая позволяет вам передавать скрытые сообщения во время обычного разговора. Только другое существо, знающее воровской жаргон, поймёт такое сообщение. Это занимает в четыре раза больше времени, чем передача той же идеи обычным образом. В дополнение, вы понимаете набор тайных знаков и символов, используемый для передачи коротких и простых сообщений.",
            'cunning_action': "Начиная со 2-го уровня, ваша сообразительность и ловкость позволяют вам двигаться и действовать быстро. Вы можете совершать бонусное действие в каждый свой ход в бою. Это действие может быть использовано только для Засады, Отхода или Рывка.",
            'roguish_archetype': "На 3-м уровне вы выбираете архетип, которому вы будете подражать в своих плутовских умениях. В SRD доступен только архетип Вор.",
            'thief_note': "Вы отточили свои навыки до тонкостей воровства. Взломщики, грабители, карманники и прочие преступники обычно следуют этому архетипу, но также и плуты, предпочитающие думать о себе как о профессиональных искателях сокровищ, исследователях и сыщиках.",
            'fast_hands': "(Вор) Начиная с 3-го уровня, вы можете использовать бонусное действие, предоставляемое вашим Хитрым действием, чтобы совершить проверку Ловкости (Ловкость рук), использовать воровские инструменты для обезвреживания ловушки или вскрытия замка, или совершить действие Использование предмета.",
            'second_story_work': "(Вор) Начиная с 3-го уровня, вы получаете способность лазать быстрее обычного; лазание больше не стоит вам дополнительного движения. Кроме того, когда вы совершаете прыжок с разбега, преодолённое расстояние увеличивается на количество футов, равное вашему модификатору Ловкости.",
            'ability_score_improvement': "При достижении 4-го, 8-го, 10-го, 12-го, 16-го и 19-го уровней вы можете повысить значение одной из ваших характеристик на 2 или двух характеристик на 1. Как обычно, значение характеристики при этом не должно превысить 20.",
            'uncanny_dodge': "Начиная с 5-го уровня, если атакующий, которого вы можете видеть, попадает по вам атакой, вы можете реакцией уменьшить вдвое урон, причиняемый вам этой атакой.",
            'evasion': "(7 ур.) Начиная с 7-го уровня, вы можете с непревзойдённой ловкостью увернуться от направленных на вас эффектов, таких как дыхание синего дракона или заклинание огненный шар. Когда вы подвергаетесь эффекту, позволяющему совершить спасбросок Ловкости, чтобы получить только половину урона, вы вместо этого не получаете урона при успешном спасброске и получаете только половину урона при проваленном.",
            'supreme_sneak': "(Вор, 9 ур.) Начиная с 9-го уровня, вы получаете преимущество на проверки Ловкости (Скрытность), если вы в свой ход переместились не более чем на половину вашей скорости.",
            'reliable_talent': "(11 ур.) К 11-му уровню вы отточили выбранные навыки почти до совершенства. Каждый раз, когда вы совершаете проверку характеристики, позволяющую добавить бонус мастерства, вы можете считать результат броска к20 равным 9 или менее как 10.",
            'use_magic_device': "(Вор, 13 ур.) К 13-му уровню вы изучили достаточно много о работе магии, чтобы вы могли импровизировать при использовании магических предметов, для которых вы не предназначались. Вы игнорируете все классовые, расовые и уровневые требования при использовании магических предметов.",
            'blindsense': "(14 ур.) Начиная с 14-го уровня, если вы можете слышать, вы знаете о местонахождении всех скрытых и невидимых существ в пределах 10 футов от вас.",
            'slippery_mind': "(15 ур.) К 15-му уровню вы приобрели большую силу разума. Вы получаете владение спасбросками Мудрости.",
            'thief_reflexes': "(Вор, 17 ур.) Когда вы достигаете 17-го уровня, вы становитесь невероятно проворным. Вы можете совершать два хода во время первого раунда любого боя. Вы совершаете первый ход согласно вашей инициативе и второй ход при значении вашей инициативы минус 10. Вы не можете использовать это умение, если вы захвачены врасплох.",
            'elusive': "(18 ур.) Начиная с 18-го уровня, вы настолько ловки, что атакующие редко могут взять над вами верх. Ни один бросок атаки не имеет преимущества против вас, пока вы не недееспособны.",
            'stroke_of_luck': "(20 ур.) На 20-м уровне вы обретаете сверхъестественную удачу, которая может спасти вас в самый нужный момент. Если ваша атака промахивается по цели в пределах досягаемости, вы можете изменить результат броска на попадание. В качестве альтернативы, если вы провалили проверку характеристики, вы можете считать результат броска к20 равным 20. Использовав это умение, вы должны завершить короткий или продолжительный отдых, прежде чем сможете использовать его снова.",
        }
    },
    'wizard': {
        'name': 'Волшебник',
        'en_name': 'Wizard',
        'icon': 'images/classes/wizard_icon.png',
        'description': 'Волшебники — величайшие пользователи магии, определяемые как тонкими, так и эффектными заклинаниями, которые они творят. Опираясь на едва уловимое переплетение магии, пронизывающее космос, волшебники творят заклинания взрывного огня, вспышек молний, тонкого обмана и грубого контроля над разумом.',
        'slug': 'wizard',
        'hit_dice': '1к6 за каждый уровень волшебника',
        'hp_at_first_level': '6 + ваш модификатор Телосложения',
        'hp_at_higher_levels': '1к6 (или 4) + ваш модификатор Телосложения (минимум 1) за каждый уровень волшебника после первого',
        'proficiencies': {
            'armor': 'Нет',
            'weapons': 'Кинжалы, дротики, пращи, боевые посохи, лёгкие арбалеты',
            'tools': 'Нет',
            'saving_throws': 'Интеллект, Мудрость',
            'skills': 'Выберите два навыка из следующих: Магия, История, Проницательность, Расследование, Медицина и Религия'
        },
        'equipment': [
            '(а) боевой посох или (б) кинжал',
            '(а) мешочек с компонентами или (б) магическая фокусировка',
            '(а) набор учёного или (б) набор исследователя подземелий',
            'Книга заклинаний'
        ],
        'features_table': { # SRD включает только Школу Воплощения
            1: ["Использование заклинаний", "Магическое восстановление"],
            2: ["Традиция магии (Воплощение)", "Последователь школы Воплощения", "Создание заклинаний"],
            3: ["— (Заклинания 2-го круга)"],
            4: ["Увеличение характеристик"],
            5: ["— (Заклинания 3-го круга)"],
            6: ["Мощное воплощение (Воплощение)"],
            7: ["— (Заклинания 4-го круга)"],
            8: ["Увеличение характеристик"],
            9: ["— (Заклинания 5-го круга)"],
           10: ["Сфокусированное воплощение (Воплощение)"], # Empowered Evocation в оригинале
           14: ["Перегрузка (Воплощение)"], # Overchannel в оригинале
           18: ["Мастерство заклинаний"],
           20: ["Фирменные заклинания"]
        },
        'detailed_features': {
            'spellcasting': "Как студент тайной магии, у вас есть книга заклинаний, содержащая заклинания, которые показывают первые проблески вашей истинной силы. См. главу 10 SRD для общих правил использования заклинаний и список заклинаний волшебника в SRD.",
            'arcane_recovery': "Вы научились частично восстанавливать магическую энергию во время короткого отдыха. Один раз в день, когда вы заканчиваете короткий отдых, вы можете выбрать израсходованные ячейки заклинаний для восстановления. Ячейки могут иметь суммарный уровень, не превышающий половину вашего уровня волшебника (округлённую в большую сторону), и ни одна из ячеек не может быть 6-го уровня или выше.",
            'arcane_tradition': "Когда вы достигаете 2-го уровня, вы выбираете традицию магии, формируя свою практику через одну из восьми школ. В SRD представлена только Школа Воплощения.",
            'school_of_evocation_note': "Вы фокусируете своё обучение на магии, которая создаёт мощные стихийные эффекты, такие как жгучий холод, обжигающий огонь, раскатистый гром, разряды молнии и едкая кислота. Некоторые заклинатели находят применение этой магии в военном деле, другие же используют свои впечатляющие силы для защиты слабых или для собственных нужд.",
            'evocation_savant': "(Воплощение) Начиная со 2-го уровня, золото и время, которые вы должны потратить на копирование заклинания школы Воплощения в свою книгу заклинаний, уменьшаются вдвое.",
            'sculpt_spells': "(Воплощение) Начиная со 2-го уровня, вы можете создавать островки относительной безопасности в эффектах ваших заклинаний школы Воплощения. Когда вы используете заклинание школы Воплощения, которое затрагивает других существ, которых вы можете видеть, вы можете выбрать количество этих существ, равное 1 + круг заклинания. Выбранные существа автоматически преуспевают в спасбросках от этого заклинания, и они не получают урона, если обычно получили бы половину урона при успешном спасброске.",
            'ability_score_improvement': "При достижении 4-го, 8-го, 12-го, 16-го и 19-го уровней вы можете повысить значение одной из ваших характеристик на 2 или двух характеристик на 1. Как обычно, значение характеристики при этом не должно превысить 20.",
            'potent_cantrip': "(Воплощение, 6 ур.) Начиная с 6-го уровня, ваши вредоносные заговоры воздействуют на цель, даже если та избегает худшего эффекта. Когда существо преуспевает в спасброске от вашего заговора, оно всё равно получает половину урона от заговора (если таковой имеется), но не получает никаких дополнительных эффектов от заговора.",
            'empowered_evocation': "(Воплощение, 10 ур.) Начиная с 10-го уровня, вы можете добавлять ваш модификатор Интеллекта к одному броску урона любого заклинания школы Воплощения, которое вы используете.",
            'overchannel': "(Воплощение, 14 ур.) Начиная с 14-го уровня, вы можете увеличить силу ваших более простых заклинаний. Когда вы используете заклинание волшебника 5-го круга или ниже, причиняющее урон, вы можете причинить максимальный урон этим заклинанием. Первый раз, когда вы так делаете после продолжительного отдыха, вы не испытываете никаких негативных эффектов. Если вы используете это умение снова до завершения продолжительного отдыха, вы получаете урон некротической энергией 2к12 за каждый круг этого заклинания сразу после его использования. Каждый следующий раз, когда вы используете это умение до завершения продолжительного отдыха, урон некротической энергией за круг заклинания увеличивается на 1к12. Этот урон игнорирует сопротивление и иммунитет.",
            'spell_mastery': "(18 ур.) На 18-м уровне вы достигаете такого мастерства в определённых заклинаниях, что можете использовать их по желанию. Выберите по одному заклинанию волшебника 1-го и 2-го круга из вашей книги заклинаний. Вы можете использовать эти заклинания на их минимальном уровне без траты ячеек заклинаний, если они у вас подготовлены. Если вы хотите использовать их на более высоком уровне, вы должны потратить ячейку заклинаний как обычно. Потратив 8 часов на изучение, вы можете поменять одно или оба заклинания, выбранных этим умением, на другие заклинания тех же кругов.",
            'signature_spells': "(20 ур.) Когда вы достигаете 20-го уровня, вы получаете мастерство над двумя мощными заклинаниями и можете использовать их с небольшой затратой сил. Выберите два заклинания волшебника 3-го круга из вашей книги заклинаний в качестве ваших фирменных заклинаний. Они всегда считаются подготовленными и не учитываются в общем количестве подготовленных заклинаний. Вы можете один раз использовать каждое из них на 3-м круге без траты ячейки заклинаний. Использовав одно из них таким образом, вы должны закончить короткий или продолжительный отдых, прежде чем сможете использовать его снова таким же образом. Если вы хотите использовать эти заклинания на более высоком уровне, вы должны потратить ячейку заклинаний как обычно.",
        }
    }
}
    # Остальные классы (Bard, Barbarian, Druid, Monk, Paladin, Ranger, Sorcerer, Warlock)
    # НЕ входят в SRD 5.1. Artificer тем более.
SRD_SPECIES = { # Данные SRD Видов/Рас
    'human': {
        'name': 'Человек',
        'en_name': 'Human',
        'icon': 'images/species/human_icon.png', 
        'description': 'Люди — самая распространённая раса в мире. Они невероятно разнообразны, имеют множество культур и взглядов. Их адаптивность и амбиции позволили им основать великие империи и проникать во все уголки мира.',
        'slug': 'human',
        'traits': { # Черты расы
            'ability_score_increase': 'Увеличьте все ваши значения характеристик на 1.',
            'age': 'Люди достигают совершеннолетия примерно в 18 лет и живут меньше века.',
            'alignment': 'Люди не имеют склонности к какому-либо мировоззрению.',
            'size': 'Рост людей колеблется от 5 до 6 футов (от 1,5 до 1,8 метра), а вес от 125 до 250 фунтов (от 55 до 115 килограммов). Ваш размер — Средний.',
            'speed': 'Ваша базовая скорость ходьбы составляет 30 футов.'
        }
    },
    'elf': {
        'name': 'Эльф',
        'en_name': 'Elf',
        'icon': 'images/species/elf_icon.png',
        'description': 'Эльфы — магический народ неземной грации, живущий в мире, но не совсем от него. Они обитают в местах мистической красоты посреди древних лесов, в сияющих серебром бастионах, скрученных туманом, в мирных деревушках из переплетённых ветвей или в кристальных городах под бледным морем.',
        'slug': 'elf',
         'traits': {
            'ability_score_increase': 'Увеличьте значение вашей Ловкости на 2.',
            'age': 'Эльфы достигают физического совершеннолетия примерно в том же возрасте, что и люди, но полностью зрелыми считаются в 100 лет и могут жить до 750 лет.',
            'alignment': 'Эльфы любят свободу, разнообразие и самовыражение, поэтому они чаще добрые, чем злые. Они склонны к Хаосу, ценя личную свободу и самовыражение выше правил.',
            'size': 'Эльфы имеют рост от 5 до 6 футов (от 1,5 до 1,8 метра) и довольно худощавы. Ваш размер — Средний.',
            'speed': 'Ваша базовая скорость ходьбы составляет 30 футов.',
            'darkvision': 'У вас превосходное зрение в темноте и при тусклом свете. В пределах 60 футов вы можете видеть при тусклом освещении как при ярком, а при полной темноте как при тусклом. В темноте вы различаете только оттенки серого.',
            'keen_senses': 'Вы владеете навыком Восприятие.',
            'fey_ancestry': 'Вы совершаете с преимуществом спасброски от очарования, и вас невозможно магически усыпить.',
            'trance': 'Эльфы не спят. Вместо этого они входят в состояние глубокой медитации, называемое трансом, занимающее 4 часа в день. В трансе эльф получает преимущества продолжительного отдыха, и это состояние похоже на лёгкий сон.'
        },
        'subraces': { # SRD включает только Высших эльфов
            'high_elf': {
                'name': 'Высший эльф',
                'traits': {
                    'ability_score_increase': 'Увеличьте значение вашего Интеллекта на 1.',
                    'elf_weapon_training': 'Вы владеете длинным мечом, коротким мечом, коротким луком и длинным луком.',
                    'cantrip': 'Вы знаете один заговор на ваш выбор из списка заклинаний волшебника. Интеллект — ваша базовая характеристика для его использования.',
                    'extra_language': 'Вы можете говорить, читать и писать на одном дополнительном языке на ваш выбор.'
                }
            }
        }
    },
    'dwarf': {
        'name': 'Дварф',
        'en_name': 'Dwarf',
        'icon': 'images/species/dwarf_icon.png', 
        'description': 'Дварфы — стойкий и выносливый народ, известный как умелые воины, мастера-шахтёры и великолепные ремесленники, живущие глубоко под землёй в сияющих городах-крепостях.',
        'slug': 'dwarf',
        'traits': {
            'ability_score_increase': 'Увеличьте значение вашего Телосложения на 2.',
            'age': 'Дварфы взрослеют с той же скоростью, что и люди, но считаются молодыми до 50 лет. В среднем они живут 350 лет.',
            'alignment': 'Дварфы чаще Законопослушные, верящие в блага хорошо организованного общества.',
            'size': 'Дварфы ростом чуть более 4 футов (1,2 метра) и очень массивны. Ваш размер — Средний.',
            'speed': 'Ваша базовая скорость ходьбы составляет 25 футов. Ваша скорость не уменьшается, если вы носите тяжёлые доспехи.',
            'darkvision': 'У вас превосходное зрение в темноте и при тусклом свете. В пределах 60 футов вы можете видеть при тусклом освещении как при ярком, а при полной темноте как при тусклом. В темноте вы различаете только оттенки серого.',
            'dwarven_resilience': 'Вы совершаете с преимуществом спасброски от яда, и вы получаете сопротивление урону ядом.',
            'dwarven_combat_training': 'Вы владеете боевым топором, ручным топором, лёгким молотом и боевым молотом.',
            'tool_proficiency': 'Вы получаете владение одним набором инструментов ремесленника на ваш выбор: инструменты кузнеца, инструменты каменщика или инструменты ювелира.',
            'stonecunning': 'Каждый раз, когда вы совершаете проверку Интеллекта (История), связанную с происхождением работ из камня, вы считаетесь владеющим навыком История и добавляете удвоенный бонус мастерства к проверке.'
        },
         'subraces': { # SRD включает только Горных дварфов
            'mountain_dwarf': {
                'name': 'Горный дварф',
                'traits': {
                    'ability_score_increase': 'Увеличьте значение вашей Силы на 2.',
                    'dwarven_armor_training': 'Вы владеете лёгкими и средними доспехами.'
                }
            }
        }
    },
    'halfling': {
        'name': 'Полурослик',
        'en_name': 'Halfling',
        'icon': 'images/species/halfling_icon.png',
        'description': 'Полурослики — дружелюбный и весёлый народ, который предпочитает мирную жизнь вдали от суеты. Они любят уютные дома, хорошую еду и приятные компании.',
        'slug': 'halfling',
        'traits': {
            'ability_score_increase': 'Увеличьте значение вашей Ловкости на 2.',
            'age': 'Полурослики достигают совершеннолетия в возрасте 20 лет и живут до середины второго столетия.',
            'alignment': 'Полурослики обычно добрые и склонны к порядку. Они ценят комфорт и традиции, но легко приспосабливаются.',
            'size': 'Полурослики ростом около 3 футов (0,9 метра) и весят около 40 фунтов (18 килограммов). Ваш размер — Маленький.',
            'speed': 'Ваша базовая скорость ходьбы составляет 25 футов.',
            'lucky': 'Когда вы выбрасываете 1 на к20 для броска атаки, проверки характеристики или спасброска, вы можете перебросить кость и должны использовать новый результат.',
            'brave': 'Вы совершаете с преимуществом спасброски от испуга.',
            'halfling_nimbleness': 'Вы можете перемещаться через пространство любого существа, чей размер хотя бы на одну категорию больше вашего.',
        },
        'subraces': { # SRD включает только Легконогих полуросликов
            'lightfoot_halfling': {
                'name': 'Легконогий полурослик',
                'traits': {
                    'ability_score_increase': 'Увеличьте значение вашей Харизмы на 1.',
                    'naturally_stealthy': 'Вы можете попытаться спрятаться, даже когда находитесь за существом, чей размер хотя бы на одну категорию больше вашего.'
                }
            }
        }
    }
}

# --- Данные Чудовищ SRD ---
SRD_MONSTERS = {
    'goblin': {
        'name': 'Гоблин',
        'en_name': 'Goblin',
        'slug': 'goblin',
        'icon': 'images/monsters/goblin_icon.png',
        'size_type_alignment': 'Маленький гуманоид (гоблиноид), нейтрально-злой',
        'armor_class': '15 (кожаный доспех, щит)',
        'hit_points': '7 (2к6)',
        'speed': '30 фт.',
        'stats': { # Характеристики
            'STR': '8 (-1)', 'DEX': '14 (+2)', 'CON': '10 (+0)',
            'INT': '10 (+0)', 'WIS': '8 (-1)', 'CHA': '8 (-1)'
        },
        'skills': 'Скрытность +6',
        'senses': 'Тёмное зрение 60 фт., Пассивное Восприятие 9',
        'languages': 'Гоблинский, Общий',
        'challenge': '1/4 (50 опыта)',
        'abilities': [ # Умения
            {
                'name': 'Пронырливый побег (Nimble Escape)',
                'desc': 'Гоблин может совершать действия Отход или Засада бонусным действием в каждый свой ход.'
            }
        ],
        'actions': [ # Действия
            {
                'name': 'Скимитар (Scimitar)',
                'desc': 'Рукопашная атака оружием: +4 к попаданию, досягаемость 5 фт., одна цель. Попадание: 5 (1к6 + 2) рубящего урона.'
            },
            {
                'name': 'Короткий лук (Shortbow)',
                'desc': 'Дальнобойная атака оружием: +4 к попаданию, дистанция 80/320 фт., одна цель. Попадание: 5 (1к6 + 2) колющего урона.'
            }
        ],
        'description': 'Гоблины — это маленькие, злобные и хитрые существа, которые часто собираются в большие группы, чтобы терроризировать более цивилизованные народы. Они трусливы поодиночке, но наглы и жестоки в стае.'
    },
    'skeleton': {
        'name': 'Скелет',
        'en_name': 'Skeleton',
        'slug': 'skeleton',
        'icon': 'images/monsters/skeleton_icon.png',
        'size_type_alignment': 'Средний нежить, законопослушно-злой',
        'armor_class': '13 (обрывки доспехов)',
        'hit_points': '13 (2к8 + 4)',
        'speed': '30 фт.',
        'stats': {
            'STR': '10 (+0)', 'DEX': '14 (+2)', 'CON': '15 (+2)',
            'INT': '6 (-2)', 'WIS': '8 (-1)', 'CHA': '5 (-3)'
        },
        'damage_vulnerabilities': 'Дробящий',
        'damage_immunities': 'Яд',
        'condition_immunities': 'Истощение, Отравление',
        'senses': 'Тёмное зрение 60 фт., Пассивное Восприятие 9',
        'languages': 'Понимает все языки, которые знал при жизни, но не может говорить',
        'challenge': '1/4 (50 опыта)',
        'abilities': [], # У скелета в SRD нет особых умений
        'actions': [
            {
                'name': 'Короткий меч (Shortsword)',
                'desc': 'Рукопашная атака оружием: +4 к попаданию, досягаемость 5 фт., одна цель. Попадание: 5 (1к6 + 2) колющего урона.'
            },
            {
                'name': 'Короткий лук (Shortbow)',
                'desc': 'Дальнобойная атака оружием: +4 к попаданию, дистанция 80/320 фт., одна цель. Попадание: 5 (1к6 + 2) колющего урона.'
            }
        ],
        'description': 'Анимированные магией кости давно умерших существ, скелеты часто служат стражами или безмозглыми воинами в армиях некромантов.'
    },
    'wolf': {
        'name': 'Волк',
        'en_name': 'Wolf',
        'slug': 'wolf',
        'icon': 'images/monsters/wolf_icon.png',
        'size_type_alignment': 'Средний зверь, без мировоззрения',
        'armor_class': '13 (природный доспех)',
        'hit_points': '11 (2к8 + 2)',
        'speed': '40 фт.',
        'stats': {
            'STR': '12 (+1)', 'DEX': '15 (+2)', 'CON': '12 (+1)',
            'INT': '3 (-4)', 'WIS': '12 (+1)', 'CHA': '6 (-2)'
        },
        'skills': 'Восприятие +3, Скрытность +4',
        'senses': 'Пассивное Восприятие 13',
        'languages': '—',
        'challenge': '1/4 (50 опыта)',
        'abilities': [
            {
                'name': 'Острый слух и нюх (Keen Hearing and Smell)',
                'desc': 'Волк совершает с преимуществом проверки Мудрости (Восприятие), основанные на слухе или нюхе.'
            },
            {
                'name': 'Тактика стаи (Pack Tactics)',
                'desc': 'Волк совершает с преимуществом бросок атаки по существу, если как минимум один союзник волка находится в пределах 5 футов от этого существа, и этот союзник не недееспособен.'
            }
        ],
        'actions': [
            {
                'name': 'Укус (Bite)',
                'desc': 'Рукопашная атака оружием: +4 к попаданию, досягаемость 5 фт., одна цель. Попадание: 7 (2к4 + 2) колющего урона. Если цель — существо, она должна преуспеть в спасброске Силы Сл 11, иначе будет сбита с ног.'
            }
        ],
        'description': 'Волки — хищники, охотящиеся стаями. Они умны, быстры и обладают острыми чувствами, что делает их опасными противниками в дикой местности.'
    }
}

def challenge_rating_sort_key(cr_str):
    if "/" in cr_str:
        try:
            num, den = map(int, cr_str.split('/'))
            return num / den
        except ValueError:
            return 999 
    try:
        return float(cr_str) 
    except ValueError:
        return 999

ALL_MONSTER_CHALLENGES = sorted(list(set(
    monster_data.get('challenge', 'Не указан') for monster_data in SRD_MONSTERS.values()
)), key=challenge_rating_sort_key)


# Уникальные типы существ (извлекаем из 'size_type_alignment')
def extract_monster_type(size_type_alignment_str):
    parts = size_type_alignment_str.split(',')
    if len(parts) > 0:
        # Берем первую часть до запятой, где обычно размер и тип
        # Пример: "Маленький гуманоид (гоблиноид)" или "Средний зверь"
        type_and_size_part = parts[0].strip()
        words = type_and_size_part.split()

        known_sizes = ["крошечный", "маленький", "средний", "большой", "огромный", "исполинский",
                       "tiny", "small", "medium", "large", "huge", "gargantuan"]

        actual_type_words = []
        for word in words:
            if word.lower().replace('ё', 'е') not in known_sizes:
                if '(' in word:
                    actual_type_words.append(word.split('(')[0].strip())
                    break
                else:
                    actual_type_words.append(word.strip())
            elif actual_type_words and word.startswith('('):
                 break


        if actual_type_words:
            return " ".join(actual_type_words).capitalize()
    return None

ALL_MONSTER_TYPES = sorted(list(set(
    extract_monster_type(monster_data.get('size_type_alignment', ''))
    for monster_data in SRD_MONSTERS.values()
    if extract_monster_type(monster_data.get('size_type_alignment', '')) # Убираем None значения
)))

# Уникальные мировоззрения (извлекаем из 'size_type_alignment')
def extract_monster_alignment(size_type_alignment_str):
    parts = size_type_alignment_str.split(',')
    if len(parts) > 1: 
        return parts[-1].strip().capitalize() 
    return None

ALL_MONSTER_ALIGNMENTS = sorted(list(set(
    extract_monster_alignment(monster_data.get('size_type_alignment', ''))
    for monster_data in SRD_MONSTERS.values()
    if extract_monster_alignment(monster_data.get('size_type_alignment', ''))
)))

# Уникальные размеры (извлекаем из 'size_type_alignment')
def extract_monster_size(size_type_alignment_str):
    parts = size_type_alignment_str.split(',')
    if len(parts) > 0:
        type_part = parts[0].strip() # "Маленький гуманоид (гоблиноид)"
        size_words = type_part.split()
        if len(size_words) > 0:
            return size_words[0].strip().capitalize()
    return None

ALL_MONSTER_SIZES = sorted(list(set(
    extract_monster_size(monster_data.get('size_type_alignment', ''))
    for monster_data in SRD_MONSTERS.values()
    if extract_monster_size(monster_data.get('size_type_alignment', ''))
)))

SRD_STATIC_CONTENT = {
    'character_info': """
    <h2>Основы создания персонажа</h2>
    <p>Создание персонажа — это первый шаг в вашем приключении D&D. Это одновременно увлекательный процесс, позволяющий воплотить в жизнь вашу уникальную концепцию героя, и важный этап для понимания правил игры.</p>
    <p>В процессе создания вы определите следующие аспекты вашего персонажа:</p>
    <ul>
        <li><strong>Раса:</strong> Определяет базовые черты, умения и иногда размер или скорость.</li>
        <li><strong>Класс:</strong> Определяет основные способности, владения и стиль игры.</li>
        <li><strong>Значения характеристик:</strong> Определяют врожденные способности персонажа (Сила, Ловкость, Телосложение, Интеллект, Мудрость, Харизма).</li>
        <li><strong>Навыки и владения:</strong> Определяют, в чём ваш персонаж особенно хорош.</li>
        <li><strong>Снаряжение:</strong> Определяет оружие, доспехи и предметы, которыми он начинает игру.</li>
        <li><strong>Предыстория:</strong> Дает персонажу контекст в игровом мире и определяет дополнительные владения и умения.</li>
        <li><strong>Мировоззрение и божество:</strong> Определяют моральный и этический компас персонажа.</li>
    </ul>

    <h3>Значения характеристик</h3>
    <p>Есть шесть основных характеристик, каждая из которых определяет определенные способности существа:</p>
    <ul>
        <li><strong>Сила (Strength):</strong> Измеряет физическую силу и мощь.</li>
        <li><strong>Ловкость (Dexterity):</strong> Измеряет проворство, рефлексы и равновесие.</li>
        <li><strong>Телосложение (Constitution):</strong> Измеряет живучесть и запас жизненных сил.</li>
        <li><strong>Интеллект (Intelligence):</strong> Измеряет сообразительность, память и аналитические способности.</li>
        <li><strong>Мудрость (Wisdom):</strong> Измеряет восприятие, интуицию и здравый смысл.</li>
        <li><strong>Харизма (Charisma):</strong> Измеряет способность влиять на других, уверенность в себе и привлекательность.</li>
    </ul>
    <p>Вы определяете значения этих характеристик для своего персонажа во время его создания. Обычно это делается либо броском костей, либо использованием стандартного набора значений, либо системой покупки очков.</p>

    <h3>Модификаторы характеристик</h3>
    <p>Каждая характеристика имеет модификатор, который используется для большинства проверок, спасбросков и бросков атаки. Модификатор определяется по формуле: <code>(значение характеристики - 10) / 2</code>, округленное в меньшую сторону.</p>
    <table>
        <thead>
            <tr>
                <th>Значение</th>
                <th>Модификатор</th>
            </tr>
        </thead>
        <tbody>
            <tr><td>1</td><td>-5</td></tr>
            <tr><td>2–3</td><td>-4</td></tr>
            <tr><td>4–5</td><td>-3</td></tr>
            <tr><td>6–7</td><td>-2</td></tr>
            <tr><td>8–9</td><td>-1</td></tr>
            <tr><td>10–11</td><td>+0</td></tr>
            <tr><td>12–13</td><td>+1</td></tr>
            <tr><td>14–15</td><td>+2</td></tr>
            <tr><td>16–17</td><td>+3</td></tr>
            <tr><td>18–19</td><td>+4</td></tr>
            <tr><td>20–21</td><td>+5</td></tr>
            <tr><td>22–23</td><td>+6</td></tr>
            <tr><td>24–25</td><td>+7</td></tr>
            <tr><td>26–27</td><td>+8</td></tr>
            <tr><td>28–29</td><td>+9</td></tr>
            <tr><td>30</td><td>+10</td></tr>
        </tbody>
    </table>

    <h2>Проверка характеристик</h2>
    <p>Проверка характеристики измеряет попытку персонажа или монстра преодолеть препятствие. В тех ситуациях, когда инициатива и броски атаки не применимы, Мастер определяет, какая характеристика и какое владение навыком наиболее уместны. Затем игрок бросает к20, добавляет модификатор соответствующей характеристики и бонус мастерства (если персонаж владеет соответствующим навыком), и сравнивает общий результат со Сложностью (СЛ) действия.</p>

    <h3>Навыки</h3>
    <p>Каждая характеристика охватывает спектр способностей, включая навыки, которыми может владеть персонаж.</p>
    <table>
        <thead>
            <tr>
                <th>Характеристика</th>
                <th>Связанные навыки (примеры)</th>
            </tr>
        </thead>
        <tbody>
            <tr><td><strong>Сила (Strength)</strong></td><td>Атлетика</td></tr>
            <tr><td><strong>Ловкость (Dexterity)</strong></td><td>Акробатика, Ловкость рук, Скрытность</td></tr>
            <tr><td><strong>Телосложение (Constitution)</strong></td><td>—</td></tr>
            <tr><td><strong>Интеллект (Intelligence)</strong></td><td>Анализ, История, Магия, Природа, Религия</td></tr>
            <tr><td><strong>Мудрость (Wisdom)</strong></td><td>Уход за животными, Проницательность, Медицина, Внимательность, Выживание</td></tr>
            <tr><td><strong>Харизма (Charisma)</strong></td><td>Обман, Запугивание, Выступление, Убеждение</td></tr>
        </tbody>
    </table>
    """,
    'equipment': """
    <h2>Снаряжение</h2>
    <p>Рыцарь в сияющих доспехах, облаченный в кольчугу, держащий сияющий щит и направляющий свой меч на красного дракона, — это знаковый образ искателя приключений. Снаряжение, которое носит персонаж, определяет его способности к выживанию и нападению.</p>

    <h3>Доспехи и Щиты</h3>
    <p>Доспехи делятся на три категории: лёгкие, средние и тяжёлые. Щиты — отдельный вид снаряжения.</p>
    <p>Ношение доспехов, которыми вы не владеете, накладывает помеху на все проверки характеристик, броски атаки и спасброски, основанные на Силе или Ловкости, а также накладывает помеху на броски для использования заклинаний. Вы не можете использовать заклинания, если вы не владеете доспехом, который носите.</p>

    <h4>Таблица Доспехов</h4>
    <table>
        <thead>
            <tr>
                <th>Доспех</th>
                <th>Категория</th>
                <th>Базовый КД</th>
                <th>Бонус Ловкости</th>
                <th>Скрытность</th>
                <th>Сила</th>
                <th>Вес</th>
                <th>Цена</th>
            </tr>
        </thead>
        <tbody>
            <tr><td>Стёганый</td><td>Лёгкий</td><td>11</td><td>+ модификатор Ловкости</td><td>Помеха</td><td>—</td><td>8 фнт.</td><td>5 зм</td></tr>
            <tr><td>Кожаный</td><td>Лёгкий</td><td>11</td><td>+ модификатор Ловкости</td><td>—</td><td>—</td><td>10 фнт.</td><td>10 зм</td></tr>
            <tr><td>Клепаная кожа</td><td>Лёгкий</td><td>12</td><td>+ модификатор Ловкости</td><td>—</td><td>—</td><td>13 фнт.</td><td>45 зм</td></tr>
            <tr><td>Чешуйчатый доспех</td><td>Средний</td><td>14</td><td>+ модификатор Ловкости (макс. 2)</td><td>Помеха</td><td>—</td><td>45 фнт.</td><td>50 зм</td></tr>
            <tr><td>Полулаты</td><td>Средний</td><td>15</td><td>+ модификатор Ловкости (макс. 2)</td><td>Помеха</td><td>—</td><td>40 фнт.</td><td>750 зм</td></tr>
            <tr><td>Кольчуга</td><td>Тяжёлый</td><td>16</td><td>—</td><td>Помеха</td><td>Сил 13</td><td>55 фнт.</td><td>50 зм</td></tr>
            <tr><td>Латы</td><td>Тяжёлый</td><td>18</td><td>—</td><td>Помеха</td><td>Сил 15</td><td>65 фнт.</td><td>1500 зм</td></tr>
            <tr><td>Щит</td><td>—</td><td>+2</td><td>—</td><td>—</td><td>—</td><td>6 фнт.</td><td>10 зм</td></tr>
        </tbody>
    </table>
    <p><small>Примечание: Таблица содержит примеры доспехов из SRD.</small></p>

    <h3>Оружие</h3>
    <p>Оружие делятся на две категории: простое и воинское. Большинство людей могут использовать простое оружие с мастерством. Воинское оружие требует более специализированной подготовки.</p>
    <p>Оружие также имеет свойства, которые влияют на его использование. Например, «<strong>Фехтовальное</strong>» (Finesse) позволяет использовать модификатор Ловкости вместо Силы, а «<strong>Тяжёлое</strong>» (Heavy) делает оружие неподходящим для существ Маленького размера.</p>

     <h4>Таблица Оружия</h4>
    <table>
        <thead>
            <tr>
                <th>Оружие</th>
                <th>Тип</th>
                <th>Урон</th>
                <th>Свойства</th>
                <th>Вес</th>
                <th>Цена</th>
            </tr>
        </thead>
        <tbody>
            <!-- Простое рукопашное -->
            <tr><td>Булава</td><td>Простое рукопашное</td><td>1к6 дробящий</td><td>—</td><td>4 фнт.</td><td>5 зм</td></tr>
            <tr><td>Кинжал</td><td>Простое рукопашное</td><td>1к4 колющий</td><td>Фехтовальное, Лёгкое, Метательное (дис 20/60)</td><td>1 фнт.</td><td>2 зм</td></tr>
            <tr><td>Боевой посох</td><td>Простое рукопашное</td><td>1к6 дробящий</td><td>Двуручное, Универсальное (1к8)</td><td>4 фнт.</td><td>2 зм</td></tr>
            <!-- Простое дальнобойное -->
            <tr><td>Лёгкий арбалет</td><td>Простое дальнобойное</td><td>1к8 колющий</td><td>Боеприпас (дис 80/320), Двуручное, Перезарядка</td><td>5 фнт.</td><td>25 зм</td></tr>
             <tr><td>Дротик</td><td>Простое дальнобойное</td><td>1к4 колющий</td><td>Фехтовальное, Метательное (дис 20/60)</td><td>1/4 фнт.</td><td>5 ср</td></tr>
            <!-- Воинское рукопашное -->
            <tr><td>Боевой топор</td><td>Воинское рукопашное</td><td>1к8 рубящий</td><td>Универсальное (1к10)</td><td>4 фнт.</td><td>10 зм</td></tr>
            <tr><td>Длинный меч</td><td>Воинское рукопашное</td><td>1к8 рубящий</td><td>Универсальное (1к10)</td><td>3 фнт.</td><td>15 зм</td></tr>
             <tr><td>Рапира</td><td>Воинское рукопашное</td><td>1к8 колющий</td><td>Фехтовальное</td><td>2 фнт.</td><td>25 зм</td></tr>
             <tr><td>Скимитар</td><td>Воинское рукопашное</td><td>1к6 рубящий</td><td>Фехтовальное, Лёгкое</td><td>3 фнт.</td><td>25 зм</td></tr>
            <!-- Воинское дальнобойное -->
            <tr><td>Длинный лук</td><td>Воинское дальнобойное</td><td>1к8 колющий</td><td>Боеприпас (дис 150/600), Двуручное, Тяжёлое</td><td>2 фнт.</td><td>50 зм</td></tr>
             <tr><td>Ручной арбалет</td><td>Воинское дальнобойное</td><td>1к6 колющий</td><td>Боеприпас (дис 30/120), Лёгкое, Перезарядка</td><td>3 фнт.</td><td>75 зм</td></tr>
        </tbody>
    </table>

    <h3>Торговые товары и прочее снаряжение</h3>
    <p>Помимо оружия и доспехов, искателям приключений требуется различное снаряжение...</p>
     <p>В SRD также перечислены различные торговые товары (Trade Goods) и услуги (Services), но для краткости они здесь не приводятся.</p>
    """,
    'gameplay': """
    <h2>Игровой процесс</h2>
    <p>Приключение в D&D — это путешествие в мир фантазии, где герои исследуют, сражаются и решают головоломки. Правила игры обеспечивают структуру для определения результатов действий персонажей.</p>

    <h3>Проверки характеристик</h3>
    <p>Когда ваш персонаж пытается сделать что-то, результат чего не очевиден, Мастер игры просит совершить проверку характеристики. Это может быть проверка без владения навыком или с владением навыком.</p>
    <ul>
        <li><strong>Проверка без навыка:</strong> <code>к20 + модификатор характеристики</code> против СЛ.</li>
        <li><strong>Проверка с навыком:</strong> <code>к20 + модификатор характеристики + бонус мастерства (если есть владение)</code> против СЛ.</li>
    </ul>
    <p>Мастер определяет Сложность (СЛ) задачи:</p>
    <table>
        <thead>
            <tr>
                <th>Сложность</th>
                <th>СЛ</th>
            </tr>
        </thead>
        <tbody>
            <tr><td>Очень лёгкая</td><td>5</td></tr>
            <tr><td>Лёгкая</td><td>10</td></tr>
            <tr><td>Средняя</td><td>15</td></tr>
            <tr><td>Сложная</td><td>20</td></tr>
            <tr><td>Очень сложная</td><td>25</td></tr>
            <tr><td>Почти невыполнимая</td><td>30</td></tr>
        </tbody>
    </table>

    <h3>Преимущество и Помеха</h3>
    <p>Иногда особая способность или ситуация дают вам <strong>преимущество</strong> (advantage) или накладывают <strong>помеху</strong> (disadvantage) на проверку характеристики, бросок атаки или спасбросок.</p>
    <ul>
        <li><strong>Преимущество:</strong> Вы бросаете два к20 и используете больший результат.</li>
        <li><strong>Помеха:</strong> Вы бросаете два к20 и используете меньший результат.</li>
    </ul>
    <p>Если несколько ситуаций одновременно дают преимущество и накладывают помеху на один и тот же бросок, то они просто компенсируют друг друга, и вы бросаете один к20 без преимущества или помехи. Вы не можете получить преимущество или помеху на одном и том же броске одновременно.</p>

    <h3>Время</h3>
    <p>В D&D время делится на раунды (6 секунд в бою), минуты (10 раундов), часы и дни, в зависимости от контекста игры.</p>

    <h3>Передвижение</h3>
    <p>В бою персонажи используют своё движение в свой ход. Скорость персонажа определяет, как далеко он может переместиться за один ход. Передвижение может быть по суше, лазанье, плавание или полёт.</p>

    <h3>Взаимодействие с объектами</h3>
    <p>Персонажи могут взаимодействовать с объектами как часть своего движения или действия.</p>

    <h3>Отдых</h3>
    <p>Персонажи могут совершать короткий (минимум 1 час) или продолжительный (минимум 8 часов) отдых, чтобы восстановить силы, хиты и ячейки заклинаний.</p>
    """,
    'combat': """
    <h2>Сражение</h2>
    <p>Бой в D&D может быть быстрым и захватывающим. Эта секция описывает правила, которые регулируют схватки.</p>

    <h3>Порядок хода: Инициатива</h3>
    <p>В начале боя все участники совершают проверку Ловкости на инициативу. Порядок ходов определяется результатом этой проверки, от наибольшего к наименьшему.</p>

    <h3>Ход в бою</h3>
    <p>В свой ход существо может совершить <strong>действие</strong> и <strong>переместиться</strong> на расстояние, не превышающее его скорость.</p>
    <p>Наиболее распространённые действия в бою:</p>
    <ul>
        <li><strong>Атака</strong> (Attack)</li>
        <li><strong>Использование заклинания</strong> (Cast a Spell)</li>
        <li><strong>Рывок</strong> (Dash)</li>
        <li><strong>Отход</strong> (Disengage)</li>
        <li><strong>Уклонение</strong> (Dodge)</li>
        <li><strong>Помощь</strong> (Help)</li>
        <li><strong>Скрыть себя</strong> (Hide)</li>
        <li><strong>Подготовка</strong> (Ready)</li>
        <li><strong>Поиск</strong> (Search)</li>
        <li><strong>Использование предмета</strong> (Use an Object)</li>
        <li><strong>Использование особого умения</strong> (Use a Special Ability)</li>
    </ul>
    <p>Некоторые действия, такие как "Свободное взаимодействие" (Free Interaction) или "Бонусное действие" (Bonus Action) (если умение или заклинание это позволяют), также могут быть совершены.</p>

    <h3>Атака</h3>
    <p>Когда вы совершаете действие Атака, вы можете совершить одну атаку ближнего или дальнего боя. Некоторые умения или эффекты могут давать вам дополнительные атаки.</p>
    <p><strong>Бросок атаки:</strong> <code>к20 + модификатор характеристики + бонус мастерства (если владеете оружием)</code></p>
    <p>Результат сравнивается с Классом Доспеха (КД) цели. Если результат равен или больше КД цели, атака попадает.</p>
    <p><strong>Критическое попадание:</strong> Если на к20 выпало 20, это критическое попадание. Вы бросаете дополнительные кости урона для атаки.</p>

    <h3>Урон и лечение</h3>
    <p>Когда атака попадает, вы бросаете кости урона оружия и добавляете соответствующие модификаторы. Урон уменьшает количество хитов цели.</p>
    <p>Существа могут восстанавливать хиты с помощью лечения (заклинаний, умений, зелий) или во время отдыха.</p>

    <h3>Особые ситуации в бою</h3>
    <p>Правила также описывают такие ситуации, как:</p>
    <ul>
        <li><strong>Бой двумя оружиями</strong></li>
        <li><strong>Захват</strong></li>
        <li><strong>Скрытые атакующие и цели</strong></li>
        <li><strong>Бой под водой</strong></li>
        <li>И многое другое.</li>
    </ul>
    """,
}

# --- 6. Маршруты (Views) ---

# --- Основные маршруты ---
@app.route('/')
@app.route('/index')
def index():
    return render_template('index.html', title='Dice & Destiny SRD Hub')

# --- Маршруты аутентификации ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter(User.username.ilike(form.username.data)).first()
        if user and user.check_password(form.password.data):
            login_user(user, remember=form.remember_me.data)
            flash(f'Добро пожаловать, {user.username}!', 'success')
            next_page = request.args.get('next')
            if next_page and next_page.startswith('/') and not next_page.startswith('//'):
                 return redirect(next_page)
            else:
                 return redirect(url_for('index'))
        else:
            flash('Неверный логин или пароль.', 'danger')
    return render_template('login.html', title='Вход', form=form)

@app.route('/register', methods=['GET', 'POST'])
def register():
    # ... (без изменений)
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(username=form.username.data.lower())
        user.set_password(form.password.data)
        db.session.add(user)
        try:
            db.session.commit()
            flash('Регистрация прошла успешно! Теперь вы можете войти.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            print(f"Error during registration commit: {e}")
            if 'UNIQUE constraint failed: user.username' in str(e).lower():
                 form.username.errors.append('Это имя пользователя уже занято.')
            else:
                 flash('Произошла ошибка при регистрации. Попробуйте позже.', 'danger')
    return render_template('register.html', title='Регистрация', form=form)


@app.route('/logout')
@login_required
def logout_page():
    return render_template('logout.html', title='Выход')

@app.route('/logout_action', methods=['POST'])
@login_required
def logout_action():
    logout_user()
    return {'message': 'Logged out successfully'}, 200

# --- Маршруты SRD ---
@app.route('/srd/classes')
def list_classes():
     return render_template('classes.html', title='Классы', classes=SRD_CLASSES)

@app.route('/srd/class/<string:class_slug>', methods=['GET', 'POST'])
def class_detail(class_slug):
    srd_item = SRD_CLASSES.get(class_slug)
    if not srd_item: abort(404)
    comment_form = CommentForm(); reply_form = ReplyForm(); page_type = 'class'
    if request.method == 'POST':
        if not current_user.is_authenticated:
             flash('Нужно войти, чтобы комментировать.', 'warning'); return redirect(url_for('login', next=request.url))
        parent_id = request.form.get('parent_id'); form_to_validate = reply_form if parent_id else comment_form
        if form_to_validate.validate_on_submit():
            parent_comment = None
            if parent_id:
                 parent_comment = Comment.query.get(int(parent_id))
                 if not parent_comment or parent_comment.srd_page_type != page_type or parent_comment.srd_page_slug != class_slug:
                      flash('Не удалось найти родительский комментарий.', 'danger'); return redirect(url_for('class_detail', class_slug=class_slug))
            comment = Comment(content=form_to_validate.content.data, author=current_user, srd_page_type=page_type, srd_page_slug=class_slug, parent_comment_id=parent_id if parent_id else None)
            db.session.add(comment); db.session.commit()
            flash('Комментарий добавлен.' if not parent_id else 'Ответ добавлен.', 'success')
            return redirect(url_for('class_detail', class_slug=class_slug))
        else: flash('Ошибка в форме комментария/ответа.', 'danger')
    comments = get_comments_with_replies((Comment.srd_page_type == page_type) & (Comment.srd_page_slug == class_slug))
    return render_template('class_detail.html', title=srd_item['name'], srd_item=srd_item, comments=comments, comment_form=comment_form, reply_form=reply_form, page_type=page_type, page_slug=class_slug)

@app.route('/srd/species')
def list_species():
    return render_template('species.html', title='Виды (Расы)', species=SRD_SPECIES)

@app.route('/srd/species/<string:species_slug>', methods=['GET', 'POST'])
def species_detail(species_slug):
    srd_item = SRD_SPECIES.get(species_slug)
    if not srd_item: abort(404)
    comment_form = CommentForm(); reply_form = ReplyForm(); page_type = 'species'
    if request.method == 'POST':
        if not current_user.is_authenticated:
             flash('Нужно войти, чтобы комментировать.', 'warning'); return redirect(url_for('login', next=request.url))
        parent_id = request.form.get('parent_id'); form_to_validate = reply_form if parent_id else comment_form
        if form_to_validate.validate_on_submit():
            parent_comment = None
            if parent_id:
                 parent_comment = Comment.query.get(int(parent_id))
                 if not parent_comment or parent_comment.srd_page_type != page_type or parent_comment.srd_page_slug != species_slug:
                      flash('Не удалось найти родительский комментарий.', 'danger'); return redirect(url_for('species_detail', species_slug=species_slug))
            comment = Comment(content=form_to_validate.content.data, author=current_user, srd_page_type=page_type, srd_page_slug=species_slug, parent_comment_id=parent_id if parent_id else None)
            db.session.add(comment); db.session.commit()
            flash('Комментарий добавлен.' if not parent_id else 'Ответ добавлен.', 'success')
            return redirect(url_for('species_detail', species_slug=species_slug))
        else: flash('Ошибка в форме комментария/ответа.', 'danger')
    comments = get_comments_with_replies((Comment.srd_page_type == page_type) & (Comment.srd_page_slug == species_slug))
    return render_template('species_detail.html', title=srd_item['name'], srd_item=srd_item, comments=comments, comment_form=comment_form, reply_form=reply_form, page_type=page_type, page_slug=species_slug)


# Другие статичные SRD страницы
@app.route('/srd/character')
def character_info():
     # Получаем HTML контент из словаря
     content = SRD_STATIC_CONTENT.get('character_info', '<p>Контент для этой страницы пока недоступен.</p>')
     # Передаем его в универсальный шаблон
     return render_template('generic_srd_page.html', title='Персонаж', page_content=content)

@app.route('/srd/equipment')
def equipment():
     content = SRD_STATIC_CONTENT.get('equipment', '<p>Контент для этой страницы пока недоступен.</p>')
     return render_template('generic_srd_page.html', title='Экипировка', page_content=content)

@app.route('/srd/gameplay')
def gameplay():
     content = SRD_STATIC_CONTENT.get('gameplay', '<p>Контент для этой страницы пока недоступен.</p>')
     return render_template('generic_srd_page.html', title='Игровой процесс', page_content=content)

@app.route('/srd/combat')
def combat():
     content = SRD_STATIC_CONTENT.get('combat', '<p>Контент для этой страницы пока недоступен.</p>')
     return render_template('generic_srd_page.html', title='Сражение', page_content=content)

@app.route('/srd/spells')
def spells_list():
    search_query = request.args.get('search', '').strip()
    filter_class = request.args.get('class', '')
    filter_level = request.args.get('level', '')
    filter_school = request.args.get('school', '')

    filtered_spells = SRD_SPELLS_LIST # Работаем уже с обработанным списком

    if search_query:
        search_query_lower = search_query.lower()
        filtered_spells = [
            spell for spell in filtered_spells
            # Ищем в русском названии
            if search_query_lower in spell.get('name', '').lower()
        ]

    if filter_class:
        filtered_spells = [
            spell for spell in filtered_spells
            # Ищем в списке raw классов
            if filter_class.lower() in [cls.lower() for cls in spell.get('classes_raw', [])]
        ]

    if filter_level:
        filtered_spells = [
            spell for spell in filtered_spells
            # Сравниваем raw уровень (число) с фильтром (строка), приводим к строке
            if str(spell.get('level_raw', 99)).lower() == filter_level.lower()
        ]

    if filter_school:
         filtered_spells = [
             spell for spell in filtered_spells
             # Сравниваем raw школу
             if spell.get('school_raw', '').lower() == filter_school.lower()
         ]

    # Сортируем ОТФИЛЬТРОВАННЫЙ список по уровню (используем универсальную функцию)
    sorted_spells = sorted(filtered_spells, key=spell_level_sort_key)

    return render_template(
        'spells_list.html',
        title='Заклинания (SRD 5.1)',
        spells=sorted_spells,

        search_query=search_query,
        filter_class=filter_class,
        filter_level=filter_level,
        filter_school=filter_school,

        all_classes=ALL_SPELL_CLASSES,
        all_levels=ALL_SPELL_LEVELS,
        all_schools=ALL_SPELL_SCHOOLS,

        class_names_map=CLASS_NAMES,
        school_names_map=SCHOOL_NAMES
    )

# Маршрут для деталей заклинания
@app.route('/srd/spell/<string:spell_slug>', methods=['GET', 'POST'])
def spell_detail(spell_slug):
    spell = SRD_SPELLS_DICT.get(spell_slug)
    if not spell:
        abort(404)

    comment_form = CommentForm()
    reply_form = ReplyForm()
    page_type = 'spell'
    page_slug = spell.get('slug') # Используем наш сгенерированный слаг из объекта spell

    if request.method == 'POST':
        if not current_user.is_authenticated:
             flash('Нужно войти, чтобы комментировать.', 'warning')
             return redirect(url_for('login', next=request.url))

        is_new_comment = request.form.get('submit_comment') == 'true'
        is_reply = request.form.get('submit_reply') == 'true'
        parent_id = request.form.get('parent_id')

        form_to_validate = None
        if is_new_comment: form_to_validate = comment_form
        elif is_reply and parent_id: form_to_validate = reply_form

        if form_to_validate and form_to_validate.validate_on_submit():
            parent_comment_obj = None
            if parent_id and is_reply:
                 parent_comment_obj = Comment.query.get(int(parent_id))
                 if not parent_comment_obj or parent_comment_obj.srd_page_type != page_type or parent_comment_obj.srd_page_slug != page_slug:
                      flash('Ошибка: неверный родительский комментарий.', 'danger')
                      return redirect(url_for('spell_detail', spell_slug=page_slug))

            comment = Comment(
                content=form_to_validate.content.data,
                author=current_user,
                srd_page_type=page_type,
                srd_page_slug=page_slug,
                parent_comment_id=int(parent_id) if parent_id and is_reply else None
            )
            db.session.add(comment)
            db.session.commit()
            flash('Комментарий добавлен.' if is_new_comment else 'Ответ добавлен.', 'success')

            redirect_url = url_for('spell_detail', spell_slug=page_slug)
            if comment.parent_comment_id: redirect_url += f'#comment-{comment.parent_comment_id}'
            else: redirect_url += f'#comment-{comment.id}'
            return redirect(redirect_url)

        elif request.method == 'POST':
             flash('Ошибка в форме комментария/ответа. Проверьте введенные данные.', 'danger')

    comments = get_comments_with_replies(
        (Comment.srd_page_type == page_type) & (Comment.srd_page_slug == page_slug)
    )

    return render_template(
        'spell_detail.html',
        title=spell.get('name', 'Заклинание'), # Используем русское название
        spell=spell,
        comments=comments,
        comment_form=comment_form,
        reply_form=reply_form,
        page_type=page_type,
        page_slug=page_slug
    )

@app.route('/srd/monsters')
def monsters_list():
    # Получаем параметры фильтров из URL
    filter_cr = request.args.get('cr', '')
    filter_type = request.args.get('type', '')
    filter_alignment = request.args.get('alignment', '')
    filter_size = request.args.get('size', '')

    # Начинаем с полного списка чудовищ (значений словаря)
    filtered_monsters_list = list(SRD_MONSTERS.values()) # Преобразуем в список для фильтрации

    # Применяем фильтры
    if filter_cr:
        filtered_monsters_list = [
            monster for monster in filtered_monsters_list
            if monster.get('challenge', '').strip().lower() == filter_cr.lower()
        ]

    if filter_type:
        filtered_monsters_list = [
            monster for monster in filtered_monsters_list
            if extract_monster_type(monster.get('size_type_alignment', '')).lower() == filter_type.lower()
        ]

    if filter_alignment:
        filtered_monsters_list = [
            monster for monster in filtered_monsters_list
            if extract_monster_alignment(monster.get('size_type_alignment', '')).lower() == filter_alignment.lower()
        ]

    if filter_size:
        filtered_monsters_list = [
            monster for monster in filtered_monsters_list
            if extract_monster_size(monster.get('size_type_alignment', '')).lower() == filter_size.lower()
        ]

    # Сортируем отфильтрованный список по ПО
    sorted_monsters = sorted(filtered_monsters_list, key=lambda m: challenge_rating_sort_key(m.get('challenge', '999')))


    return render_template(
        'monsters_list.html',
        title='Чудовища (SRD 5.1)',
        monsters=sorted_monsters,

        # Текущие значения фильтров для формы
        filter_cr=filter_cr,
        filter_type=filter_type,
        filter_alignment=filter_alignment,
        filter_size=filter_size,

        # Опции для выпадающих списков
        all_challenges=ALL_MONSTER_CHALLENGES,
        all_types=ALL_MONSTER_TYPES,
        all_alignments=ALL_MONSTER_ALIGNMENTS,
        all_sizes=ALL_MONSTER_SIZES
    )

@app.route('/srd/monster/<string:monster_slug>', methods=['GET', 'POST'])
def monster_detail(monster_slug):
    monster = SRD_MONSTERS.get(monster_slug)
    if not monster:
        abort(404)

    comment_form = CommentForm()
    reply_form = ReplyForm()
    page_type = 'monster'
    page_slug = monster_slug

    # --- Обработка POST для комментариев ---
    if request.method == 'POST':
        if not current_user.is_authenticated:
             flash('Нужно войти, чтобы комментировать.', 'warning')
             return redirect(url_for('login', next=request.url))

        is_new_comment = request.form.get('submit_comment') == 'true'
        is_reply = request.form.get('submit_reply') == 'true'
        parent_id = request.form.get('parent_id')

        form_to_validate = None
        if is_new_comment: form_to_validate = comment_form
        elif is_reply and parent_id: form_to_validate = reply_form

        if form_to_validate and form_to_validate.validate_on_submit():
            parent_comment_obj = None
            if parent_id and is_reply:
                 parent_comment_obj = Comment.query.get(int(parent_id))
                 if not parent_comment_obj or parent_comment_obj.srd_page_type != page_type or parent_comment_obj.srd_page_slug != page_slug:
                      flash('Ошибка: неверный родительский комментарий.', 'danger')
                      return redirect(url_for('monster_detail', monster_slug=page_slug))

            comment = Comment(
                content=form_to_validate.content.data,
                author=current_user,
                srd_page_type=page_type,
                srd_page_slug=page_slug,
                parent_comment_id=int(parent_id) if parent_id and is_reply else None
            )
            db.session.add(comment)
            db.session.commit()
            flash('Комментарий добавлен.' if is_new_comment else 'Ответ добавлен.', 'success')

            redirect_url = url_for('monster_detail', monster_slug=page_slug)
            if comment.parent_comment_id: redirect_url += f'#comment-{comment.parent_comment_id}'
            else: redirect_url += f'#comment-{comment.id}'
            return redirect(redirect_url)
        elif request.method == 'POST':
             flash('Ошибка в форме комментария/ответа. Проверьте введенные данные.', 'danger')

    comments = get_comments_with_replies(
        (Comment.srd_page_type == page_type) & (Comment.srd_page_slug == page_slug)
    )

    return render_template(
        'monster_detail.html',
        title=monster.get('name', 'Чудовище'),
        monster=monster, 
        comments=comments,
        comment_form=comment_form,
        reply_form=reply_form,
        page_type=page_type,
        page_slug=page_slug
    )

@app.route('/comment/<int:comment_id>/delete', methods=['POST'])
@login_required
def delete_comment(comment_id):
    comment_to_delete = Comment.query.get_or_404(comment_id)

    # Проверка прав: автор ИЛИ модератор/админ
    can_delete = (comment_to_delete.author == current_user or 
                  (current_user.is_authenticated and current_user.role in ['admin', 'moderator']))

    if not can_delete:
        flash('У вас нет прав для удаления этого комментария.', 'danger')
        return redirect(request.referrer or url_for('index'))

    redirect_back_url = request.referrer or url_for('index') 
    parent_anchor_id = None 

    if comment_to_delete.post_id: 
        if comment_to_delete.parent_comment_id:
            parent_anchor_id = comment_to_delete.parent_comment_id
        redirect_back_url = url_for('post_detail', post_id=comment_to_delete.post_id)
    elif comment_to_delete.srd_page_type and comment_to_delete.srd_page_slug: # Комментарий к SRD
        if comment_to_delete.parent_comment_id:
            parent_anchor_id = comment_to_delete.parent_comment_id
        
        endpoint_name = f"{comment_to_delete.srd_page_type}_detail"
        slug_param_name = f"{comment_to_delete.srd_page_type}_slug"
        try:
            redirect_back_url = url_for(endpoint_name, **{slug_param_name: comment_to_delete.srd_page_slug})
        except Exception as e:
            print(f"BuildError for SRD comment redirect: {e}")
            redirect_back_url = url_for('index') # На крайний случай

    try:
        if parent_anchor_id:
            redirect_back_url += f'#comment-{parent_anchor_id}'
        else:
             redirect_back_url += '#comments'


        db.session.delete(comment_to_delete)
        db.session.commit()
        flash('Комментарий успешно удален.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Ошибка при удалении комментария.', 'danger')
        print(f"Error deleting comment: {e}")

    return redirect(redirect_back_url)


# --- Маршруты Homebrew ---
@app.route('/homebrew/', methods=['GET'])
@login_required
def homebrew_index():
    page = request.args.get('page', 1, type=int)
    selected_category_key = request.args.get('category', '')

    query = HomebrewPost.query 

    if selected_category_key:
        query = query.filter_by(category=selected_category_key)

    posts_pagination = query.order_by(HomebrewPost.timestamp.desc()).paginate(
        page=page, per_page=10, error_out=False
    )

    community_rules = """
    <h4>Правила Сообщества Homebrew Раздела:</h4>
    <ol>
        <li><strong>Уважение:</strong> Относитесь с уважением ко всем участникам. Конструктивная критика приветствуется, оскорбления и нападки запрещены.</li>
        <li><strong>Авторское право:</strong> Публикуйте только свои материалы или те, на которые у вас есть право публикации. Указывайте источники, если используете чужие идеи.</li>
        <li><strong>Релевантность:</strong> Старайтесь публиковать материалы, соответствующие тематике D&D 5e и выбранной категории.</li>
        <li><strong>Оформление:</strong> Старайтесь делать посты читаемыми и понятными. Используйте форматирование, если это необходимо.</li>
        <li><strong>Без спама:</strong> Не публикуйте рекламу или нерелевантный контент.</li>
        <li><strong>Модерация:</strong> Администрация и модераторы оставляют за собой право удалять посты и комментарии, нарушающие правила, а также блокировать пользователей за систематические нарушения.</li>
    </ol>
    """

    return render_template(
        'homebrew_index.html',
        title='Homebrew Форум',
        posts_pagination=posts_pagination,
        all_categories=ALL_HOMEBREW_CATEGORIES_FOR_FILTER,
        selected_category=selected_category_key,
        community_rules=Markup(community_rules),
        HOMEBREW_TOPICS=HOMEBREW_TOPICS
    )

@app.route('/homebrew/post/<int:post_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_homebrew_post(post_id):
    post_to_edit = HomebrewPost.query.get_or_404(post_id)
    if post_to_edit.author != current_user:
        flash('Вы можете редактировать только свои посты.', 'danger')
        return redirect(url_for('post_detail', post_id=post_id))

    form = HomebrewPostForm(obj=post_to_edit)
    if form.validate_on_submit():
        post_to_edit.title = form.title.data
        post_to_edit.content = form.content.data
        post_to_edit.category = form.category.data
        post_to_edit.timestamp = datetime.utcnow()
        try:
            db.session.commit()
            flash('Пост успешно обновлен!', 'success')
            return redirect(url_for('post_detail', post_id=post_to_edit.id))
        except Exception as e:
            db.session.rollback()
            flash('Ошибка при обновлении поста.', 'danger')
            print(f"Error editing post: {e}")
            
    return render_template('create_post.html', title=f'Редактировать: {post_to_edit.title}', form=form, post_id=post_id)

@app.route('/homebrew/post/<int:post_id>', methods=['GET', 'POST'])
@login_required
def post_detail(post_id):
    post = HomebrewPost.query.get_or_404(post_id)
    comment_form = CommentForm() 
    reply_form = ReplyForm()  

    # --- Обработка POST-запросов ---
    if request.method == 'POST':
        if not current_user.is_authenticated:
            flash('Пожалуйста, войдите, чтобы оставить комментарий.', 'warning')
            return redirect(url_for('login', next=request.url)) 

        is_new_comment_submission = request.form.get('submit_comment') == 'true'
        is_reply_submission = request.form.get('submit_reply') == 'true'
        parent_comment_id_str = request.form.get('parent_id') 

        form_to_validate = None
        if is_new_comment_submission:
            form_to_validate = comment_form
        elif is_reply_submission and parent_comment_id_str:
            form_to_validate = reply_form
        
        if form_to_validate and form_to_validate.validate_on_submit():
            new_db_comment = Comment(
                content=form_to_validate.content.data,
                author=current_user,
                post_id=post.id
            )

            if is_reply_submission and parent_comment_id_str:
                parent_comment = Comment.query.get(int(parent_comment_id_str))
                if not parent_comment or parent_comment.post_id != post.id:
                    flash('Ошибка: Неверный родительский комментарий.', 'danger')
                    return redirect(url_for('post_detail', post_id=post.id))
                new_db_comment.parent_comment_id = int(parent_comment_id_str)

            db.session.add(new_db_comment)
            db.session.commit()
            flash('Комментарий успешно добавлен.' if is_new_comment_submission else 'Ответ успешно добавлен.', 'success')

            redirect_url = url_for('post_detail', post_id=post.id)
            if new_db_comment.parent_comment_id: # Если это был ответ
                redirect_url += f'#comment-{new_db_comment.parent_comment_id}' # Якорь на родительский коммент
            else: 
                redirect_url += f'#comment-{new_db_comment.id}' # Якорь на сам новый коммент
            return redirect(redirect_url)
        else:
            if form_to_validate:
                flash('Ошибка в форме комментария/ответа. Пожалуйста, проверьте введенные данные.', 'danger')

    # --- Логика для GET-запроса или после неудачного POST ---
    comments = get_comments_with_replies(Comment.post_id == post.id)

    return render_template(
        'post_detail.html',
        title=post.title,
        post=post,                        
        comments=comments,               
        comment_form=comment_form,         
        reply_form=reply_form,           
        HOMEBREW_TOPICS=HOMEBREW_TOPICS     
    )

@app.route('/homebrew/new_post', methods=['GET', 'POST'])
@login_required
def create_post():
    form = HomebrewPostForm() 
    if form.validate_on_submit():
        post = HomebrewPost(
            title=form.title.data,
            content=form.content.data,
            category=form.category.data, 
            author=current_user 
        )
        db.session.add(post)
        db.session.commit()
        flash('Ваш Homebrew пост успешно создан!', 'success')
        return redirect(url_for('post_detail', post_id=post.id)) # Редирект на созданный пост
    return render_template('create_post.html', title='Создать Homebrew пост', form=form)

@app.route('/homebrew/post/<int:post_id>/delete', methods=['GET','POST']) 
def delete_homebrew_post(post_id):
    post_to_delete = HomebrewPost.query.get_or_404(post_id)
    
    can_delete = (post_to_delete.author == current_user or 
                  (current_user.is_authenticated and current_user.role in ['admin', 'moderator']))

    if not can_delete:
        flash('У вас нет прав для удаления этого поста.', 'danger')
        return redirect(url_for('post_detail', post_id=post_id))

    if request.method == 'POST' or request.method == 'GET':
        try:
            db.session.delete(post_to_delete)
            db.session.commit()
            flash('Пост успешно удален.', 'success')
            return redirect(url_for('homebrew_index'))
        except Exception as e:
            db.session.rollback()
            flash('Ошибка при удалении поста.', 'danger')
            print(f"Error deleting post: {e}")
            return redirect(url_for('post_detail', post_id=post_id))
    
    return redirect(url_for('post_detail', post_id=post_id))

# --- Маршрут профиля ---
@app.route('/profile/', methods=['GET', 'POST'])
@app.route('/profile/<string:username_to_view>', methods=['GET', 'POST'])
@login_required
def profile(username_to_view=None):
    user_to_display = None
    is_own_profile = False

    if username_to_view:
        # Ищем пользователя по имени, нечувствительно к регистру
        user_to_display = User.query.filter(User.username.ilike(username_to_view)).first_or_404()
        # Проверяем, является ли просматриваемый профиль профилем текущего пользователя
        if current_user.is_authenticated and user_to_display.id == current_user.id:
            is_own_profile = True
    elif current_user.is_authenticated: # Если username_to_view не указан, показываем профиль текущего пользователя
        user_to_display = current_user
        is_own_profile = True
    else:
        # Если пользователь не аутентифицирован и не указано имя для просмотра, перенаправляем на вход
        flash("Пожалуйста, войдите, чтобы посмотреть свой профиль, или укажите имя пользователя для просмотра.", "info")
        return redirect(url_for('login'))

    # Инициализируем формы как None, они нужны только для своего профиля
    form = None
    password_form = None

    if is_own_profile:
        form = ProfileUpdateForm(user_to_display)
        password_form = ChangePasswordForm()

        profile_update_submitted = request.form.get('submit') == form.submit.label.text if request.method == 'POST' and form.submit else False
        password_change_submitted = request.form.get('submit_password') == password_form.submit_password.label.text if request.method == 'POST' and password_form.submit_password else False


        if profile_update_submitted and form.validate_on_submit():
            try:
                new_username_data = form.username.data.lower()
                # Проверяем, изменилось ли имя и не занято ли оно другим пользователем
                if new_username_data != user_to_display.username:
                    existing_user = User.query.filter(User.username == new_username_data).first()
                    if existing_user:
                        flash('Это имя пользователя уже занято.', 'danger')
                        # Остаемся на странице, чтобы показать ошибку (нужно передать все переменные снова)
                        user_posts = user_to_display.posts.order_by(HomebrewPost.timestamp.desc()).limit(10).all()
                        return render_template('profile.html', title=f'Профиль: {user_to_display.username}',
                                               user=user_to_display, is_own_profile=is_own_profile,
                                               avatar_url=user_to_display.get_avatar(),
                                               form=form, password_form=password_form, user_posts=user_posts)
                    user_to_display.username = new_username_data

                user_to_display.description = form.description.data
                if form.avatar.data:
                    picture_file = save_picture(form.avatar.data)
                    if picture_file:
                        user_to_display.avatar_filename = picture_file
                
                db.session.commit()
                flash('Ваш профиль успешно обновлен!', 'success')
                return redirect(url_for('profile', username_to_view=user_to_display.username))
            except Exception as e:
                db.session.rollback()
                print(f"Error updating profile: {e}")
                flash('Произошла ошибка при обновлении профиля.', 'danger')

        elif password_change_submitted and password_form.validate_on_submit():
            if user_to_display.check_password(password_form.old_password.data):
                user_to_display.set_password(password_form.new_password.data)
                try:
                    db.session.commit()
                    flash('Пароль успешно изменен!', 'success')
                    return redirect(url_for('profile'))
                except Exception as e:
                    db.session.rollback()
                    print(f"Error changing password: {e}")
                    flash('Произошла ошибка при смене пароля.', 'danger')
            else:
                password_form.old_password.errors.append('Неверный текущий пароль.')

        if request.method == 'GET' and is_own_profile:
            if form:
                form.username.data = user_to_display.username
                form.description.data = user_to_display.description


    user_posts = user_to_display.posts.order_by(HomebrewPost.timestamp.desc()).limit(10).all()
    avatar_url = user_to_display.get_avatar()

    return render_template(
        'profile.html',
        title=f'Профиль: {user_to_display.username}',
        user=user_to_display,
        is_own_profile=is_own_profile,
        avatar_url=avatar_url,
        form=form,
        password_form=password_form,
        user_posts=user_posts
    )

# --- Маршрут для удаления пользователя (только для админов) ---
@app.route('/admin/user/<int:user_id>/delete', methods=['POST']) # Строго POST для безопасности
@login_required
def delete_user_by_admin(user_id):
    # Проверяем, является ли текущий пользователь админом
    if not current_user.is_authenticated or current_user.role != 'admin':
        flash('Доступ запрещен. Только администраторы могут удалять пользователей.', 'danger')
        return redirect(url_for('index'))

    user_to_delete = User.query.get_or_404(user_id)

    # Запрещаем админу удалять свой собственный аккаунт через эту функцию
    if user_to_delete.id == current_user.id:
        flash('Вы не можете удалить свой собственный аккаунт этим способом.', 'warning')
        return redirect(url_for('profile', username_to_view=user_to_delete.username))

    try:
        username_deleted = user_to_delete.username
        db.session.delete(user_to_delete)
        db.session.commit()
        flash(f'Пользователь {username_deleted} и весь его контент были успешно удалены.', 'success')
        return redirect(url_for('index'))
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка при удалении пользователя: {e}', 'danger')
        print(f"Error deleting user {user_id}: {e}")
        return redirect(url_for('profile', username_to_view=user_to_delete.username))

# --- 7. Обработчики ошибок ---
@app.errorhandler(404)
def not_found_error(error):
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    print(f"Server Error: {error}")
    return render_template('errors/500.html'), 500

@app.errorhandler(403)
def forbidden_error(error):
    return render_template('errors/403.html'), 403

@app.errorhandler(401) # Обработка ошибки @login_required
def unauthorized_error(error):
    flash("Для доступа к этой странице необходимо войти.", "warning")
    return redirect(url_for('login', next=request.path))


# --- 8. Создание таблиц БД (если их нет) ---
with app.app_context():
    db.create_all()
@app.cli.command("set-role")
@click.argument("username")
@click.argument("role")
def set_role_command(username, role):
    """Назначает роль пользователю.
    Роли: user, moderator, admin.
    Пример: flask set-role someuser admin
    """
    user = User.query.filter_by(username=username).first()
    if not user:
        click.echo(f"Ошибка: Пользователь '{username}' не найден.")
        return

    allowed_roles = ['user', 'moderator', 'admin']
    if role not in allowed_roles:
        click.echo(f"Ошибка: Недопустимая роль '{role}'. Доступные роли: {', '.join(allowed_roles)}.")
        return

    user.role = role
    try:
        db.session.commit()
        click.echo(f"Пользователю '{username}' успешно назначена роль '{role}'.")
    except Exception as e:
        db.session.rollback()
        click.echo(f"Ошибка при назначении роли: {e}")

# --- 9. Запуск приложения (для локальной разработки) ---
if __name__ == '__main__':
    app.run(debug=True)