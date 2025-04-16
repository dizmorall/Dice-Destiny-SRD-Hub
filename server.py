import os
import secrets
from datetime import datetime
from PIL import Image
from flask import Flask, render_template, request, flash, redirect, url_for, abort, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import StringField, PasswordField, BooleanField, SubmitField, TextAreaField, FileField
from wtforms.validators import DataRequired, Length, EqualTo, ValidationError, Optional, Regexp
from flask_wtf.file import FileAllowed
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from config import Config
from datetime import datetime, timezone

# --- 1. Инициализация приложения и расширений ---
app = Flask(__name__, instance_relative_config=True)
app.config.from_object(Config)

@app.context_processor
def inject_now():
    """Делает объект datetime доступным во всех шаблонах как 'now'."""
    # Используем рекомендованный способ получения текущего времени UTC
    return {'now': datetime.now(timezone.utc)}

# Убедимся, что папка instance существует
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

# --- 2. Модели Базы Данных (SQLAlchemy) ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    avatar_filename = db.Column(db.String(128), default='default.png')
    registered_on = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    description = db.Column(db.Text, nullable=True) # <-- ДОБАВЛЕНО ОПИСАНИЕ

    posts = db.relationship('HomebrewPost', backref='author', lazy='dynamic')
    comments = db.relationship('Comment', backref='author', lazy='dynamic')

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
    replies = db.relationship('Comment', backref=db.backref('parent', remote_side=[id]), lazy='dynamic')

    def __repr__(self):
        return f'<Comment {self.id} by User {self.user_id}>'

class HomebrewPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    comments = db.relationship('Comment', backref='post', lazy='dynamic',
                               foreign_keys=[Comment.post_id], cascade="all, delete-orphan")

    def __repr__(self):
        return f'<HomebrewPost {self.title}>'

# --- 3. Flask-Login user_loader ---
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- 4. Формы (Flask-WTF) ---

# --- Валидаторы (ОБНОВЛЕН validate_username_unique) ---
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
    title = StringField('Заголовок', validators=[DataRequired(), Length(min=5, max=150)])
    content = TextAreaField('Содержание', validators=[DataRequired(), Length(min=10)])
    submit = SubmitField('Опубликовать')

# ОБНОВЛЕНА ProfileUpdateForm
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
        self._editing_user = user_to_edit # Сохраняем пользователя для валидации

# ДОБАВЛЕНА ChangePasswordForm
class ChangePasswordForm(FlaskForm):
    old_password = PasswordField('Текущий пароль', validators=[DataRequired()])
    new_password = PasswordField('Новый пароль', validators=[DataRequired(), Length(min=6)])
    confirm_new_password = PasswordField('Подтвердите новый пароль', validators=[
        DataRequired(), EqualTo('new_password', message='Новые пароли должны совпадать.')
        ])
    submit_password = SubmitField('Сменить пароль') # Другое имя для submit

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
SRD_CLASSES = { # Данные SRD
    'fighter': {'name': 'Воин', 'icon': 'fighter.png', 'description': 'Мастер боя...', 'slug': 'fighter'},
    'cleric': {'name': 'Жрец', 'icon': 'cleric.png', 'description': 'Представитель богов...', 'slug': 'cleric'},
    'rogue': {'name': 'Плут', 'icon': 'rogue.png', 'description': 'Мастер скрытности...', 'slug': 'rogue'},
    'wizard': {'name': 'Волшебник', 'icon': 'wizard.png', 'description': 'Повелитель магии...', 'slug': 'wizard'},
    # ...
}
SRD_SPECIES = { # Данные SRD
    'human': {'name': 'Человек', 'description': 'Самая распространенная раса...', 'slug': 'human'},
    'elf': {'name': 'Эльф', 'description': 'Долгоживущие обитатели лесов...', 'slug': 'elf'},
    'dwarf': {'name': 'Дварф', 'description': 'Стойкие горные жители...', 'slug': 'dwarf'},
    'halfling': {'name': 'Полурослик', 'description': 'Маленькие и удачливые...', 'slug': 'halfling'},
     # ...
}

# --- 6. Маршруты (Views) ---

# --- Основные маршруты ---
@app.route('/')
@app.route('/index')
def index():
    return render_template('index.html', title='Dice & Destiny SRD Hub')

@app.route('/hi.html')
def hi():
    return render_template('hi.html', title='Wellcum')

# --- Маршруты аутентификации ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    # ... (без изменений)
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
    return render_template('classes.html', title='Классы', classes=SRD_CLASSES.values())

@app.route('/srd/class/<string:class_slug>', methods=['GET', 'POST'])
def class_detail(class_slug):
    # ... (без изменений, обрабатывает комментарии)
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
    return render_template('species.html', title='Виды (Расы)', species=SRD_SPECIES.values())

@app.route('/srd/species/<string:species_slug>', methods=['GET', 'POST'])
def species_detail(species_slug):
    # ... (без изменений, обрабатывает комментарии)
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
     return render_template('generic_srd_page.html', title='Персонаж', page_content="Информация о создании и развитии персонажа...")
@app.route('/srd/equipment')
def equipment():
     return render_template('generic_srd_page.html', title='Экипировка', page_content="Описание оружия, брони, снаряжения...")
@app.route('/srd/gameplay')
def gameplay():
     return render_template('generic_srd_page.html', title='Игровой процесс', page_content="Правила проверок, отдыха, путешествий...")
@app.route('/srd/combat')
def combat():
     return render_template('generic_srd_page.html', title='Сражение', page_content="Порядок хода, действия в бою, атаки...")
@app.route('/srd/spells')
def spells_info():
     return render_template('generic_srd_page.html', title='Заклинания (SRD)', page_content="Общие правила магии, список заклинаний SRD...")
@app.route('/srd/monsters')
def monsters_info():
     return render_template('generic_srd_page.html', title='Чудовища (Общее)', page_content="Характеристики монстров, типы, среда обитания...")

# --- Маршруты Homebrew ---
@app.route('/homebrew')
@login_required
def homebrew_index():
    # ... (без изменений)
    page = request.args.get('page', 1, type=int)
    posts = HomebrewPost.query.order_by(HomebrewPost.timestamp.desc()).paginate(
        page=page, per_page=10, error_out=False
    )
    return render_template('homebrew_index.html', title='Homebrew Форум', posts=posts)

@app.route('/homebrew/post/<int:post_id>', methods=['GET', 'POST'])
@login_required
def post_detail(post_id):
    # ... (без изменений, обрабатывает комментарии)
    post = HomebrewPost.query.get_or_404(post_id)
    comment_form = CommentForm(); reply_form = ReplyForm()
    if request.method == 'POST':
        parent_id = request.form.get('parent_id'); form_to_validate = reply_form if parent_id else comment_form
        if form_to_validate.validate_on_submit():
            parent_comment = None
            if parent_id:
                 parent_comment = Comment.query.get(int(parent_id))
                 if not parent_comment or parent_comment.post_id != post_id:
                      flash('Не удалось найти родительский комментарий.', 'danger'); return redirect(url_for('post_detail', post_id=post_id))
            comment = Comment(content=form_to_validate.content.data, author=current_user, post_id=post.id, parent_comment_id=parent_id if parent_id else None)
            db.session.add(comment); db.session.commit()
            flash('Комментарий добавлен.' if not parent_id else 'Ответ добавлен.', 'success')
            return redirect(url_for('post_detail', post_id=post_id))
        else: flash('Ошибка в форме комментария/ответа.', 'danger')
    comments = get_comments_with_replies(Comment.post_id == post_id)
    return render_template('post_detail.html', title=post.title, post=post, comments=comments, comment_form=comment_form, reply_form=reply_form)

@app.route('/homebrew/new_post', methods=['GET', 'POST'])
@login_required
def create_post():
    # ... (без изменений)
    form = HomebrewPostForm()
    if form.validate_on_submit():
        post = HomebrewPost(title=form.title.data, content=form.content.data, author=current_user)
        db.session.add(post); db.session.commit()
        flash('Ваш пост успешно создан!', 'success')
        return redirect(url_for('post_detail', post_id=post.id))
    return render_template('create_post.html', title='Создать пост', form=form)

# --- Маршрут профиля (ОБНОВЛЕН ПОЛНОСТЬЮ) ---
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    form = ProfileUpdateForm(current_user) # Передаем пользователя для валидации
    password_form = ChangePasswordForm()

    profile_update_submitted = form.submit.data and request.form.get('submit') == form.submit.label.text
    password_change_submitted = password_form.submit_password.data and request.form.get('submit_password') == password_form.submit_password.label.text

    # --- Обработка формы обновления профиля ---
    if profile_update_submitted and form.validate_on_submit():
        should_redirect = True # Флаг для редиректа
        try:
            new_username = form.username.data.lower()
            username_changed = new_username != current_user.username
            if username_changed:
                # Доп. проверка уникальности (хотя валидатор должен покрывать)
                existing_user = User.query.filter(User.username == new_username).first()
                if existing_user:
                     flash('Это имя пользователя уже занято.', 'danger')
                     should_redirect = False # Не редиректим, показываем ошибку
                else:
                     current_user.username = new_username

            if should_redirect: # Обновляем остальное только если имя пользователя ОК
                current_user.description = form.description.data
                if form.avatar.data:
                    picture_file = save_picture(form.avatar.data)
                    if picture_file:
                        current_user.avatar_filename = picture_file
                    else: # Ошибка сохранения аватара
                         should_redirect = False # Не редиректим, т.к. была ошибка

            if should_redirect: # Коммитим только если все ОК
                db.session.commit()
                flash('Ваш профиль успешно обновлен!', 'success')
                return redirect(url_for('profile'))

        except Exception as e:
            db.session.rollback()
            print(f"Error updating profile: {e}")
            flash('Произошла ошибка при обновлении профиля.', 'danger')
            should_redirect = False # Ошибка - не редиректим

    # --- Обработка формы смены пароля ---
    elif password_change_submitted and password_form.validate_on_submit():
        if current_user.check_password(password_form.old_password.data):
            current_user.set_password(password_form.new_password.data)
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
            flash('Неверный текущий пароль.', 'danger') # Доп. сообщение

    # --- Логика для GET запроса или если валидация форм не прошла ---
    # Предзаполняем форму профиля текущими данными ТОЛЬКО для GET
    # или если была отправлена форма профиля и она не прошла валидацию
    if request.method == 'GET' or (profile_update_submitted and not form.validate()):
         form.username.data = current_user.username
         form.description.data = current_user.description
    # Форму пароля никогда не предзаполняем

    avatar_url = current_user.get_avatar()
    user_posts = current_user.posts.order_by(HomebrewPost.timestamp.desc()).limit(10).all()
    return render_template('profile.html', title='Профиль', user=current_user,
                           avatar_url=avatar_url, form=form, password_form=password_form,
                           user_posts=user_posts)

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
# ВАЖНО: Если таблица user уже существует, добавление поля 'description'
# не произойдет автоматически через create_all(). Нужно либо удалить
# старый файл БД (instance/app.db), либо использовать Flask-Migrate.
with app.app_context():
    db.create_all()
    # ... (код для добавления начальных данных, если нужно)

# --- 9. Запуск приложения (для локальной разработки) ---
if __name__ == '__main__':
    app.run(debug=True)