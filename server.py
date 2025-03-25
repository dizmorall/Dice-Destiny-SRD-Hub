from flask import Flask, request, render_template, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_cors import CORS
from flask_session import Session

from functools import wraps
import json
from datetime import datetime

app = Flask(__name__)
CORS(app)  

@app.route('/')
def home():
    return render_template('hi.html')

@app.route('/hi.html')
def hi():
    return render_template('hi.html')
    
@app.route('/register.html')
def register_page():
    return render_template('register.html')

@app.route('/login.html')
def login_page():
    return render_template('login.html')

@app.route('/logout')
def logoutpage():
    return render_template('logout.html')

# Configurations
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_TYPE'] = 'filesystem'
app.secret_key = 'supersecretkey'

# Initialize extensions
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
Session(app)

# Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    login = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.Integer, nullable=False)  # 1 for Dungeon Master (admin), 0 for user

# Utility function to check admin access
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 1:
            return jsonify({'error': 'Unauthorized'}), 403
        return f(*args, **kwargs)
    return decorated_function

# Routes
@app.route('/register', methods=['POST'])
def register():
    data = request.json
    login = data.get('login')
    password = data.get('password')
    role = data.get('role', 0)

    if not login or not password:
        return jsonify({'error': 'Login and password are required'}), 400

    if User.query.filter_by(login=login).first():
        return jsonify({'error': 'User already exists'}), 400

    hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
    new_user = User(login=login, password=hashed_password, role=role)
    db.session.add(new_user)
    db.session.commit()

    print(f"[{datetime.now()}] Регистрация: {login}")
    return jsonify({'message': 'User registered successfully'}), 201

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    login = data.get('login')
    password = data.get('password')

    user = User.query.filter_by(login=login).first()
    if user and bcrypt.check_password_hash(user.password, password):
        session['user_id'] = user.id
        session['role'] = user.role
        print(f"[{datetime.now()}] Вход: {login}")
        return jsonify({'message': 'Login successful', 'role': user.role}), 200

    return jsonify({'error': 'Invalid login or password'}), 401

@app.route('/logout', methods=['POST'])
def logout():
    print(f"[{datetime.now()}] Выход пользователя ID: {session.get('user_id')}")
    session.clear()
    return jsonify({'message': 'Logout successful'}), 200

@app.route('/users', methods=['GET', 'POST', 'DELETE'])
@admin_required
def manage_users():
    if request.method == 'GET':
        # Получение списка всех пользователей
        users = User.query.all()
        return jsonify([{
            'id': u.id,
            'login': u.login,
            'role': u.role
        } for u in users])

    if request.method == 'POST':
        # Добавление нового пользователя
        data = request.json
        login = data.get('login')
        password = data.get('password')
        role = data.get('role', 0)  # По умолчанию роль - пользователь (0)

        if not login or not password:
            return jsonify({'error': 'Login and password are required'}), 400

        if User.query.filter_by(login=login).first():
            return jsonify({'error': 'User already exists'}), 400

        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        new_user = User(login=login, password=hashed_password, role=role)
        db.session.add(new_user)
        db.session.commit()
        return jsonify({'message': 'User added successfully'}), 201

    if request.method == 'DELETE':
        # Удаление пользователя по ID
        user_id = request.args.get('id')
        if not user_id:
            return jsonify({'error': 'User ID is required'}), 400
        
        user = User.query.get(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404

        db.session.delete(user)
        db.session.commit()
        return jsonify({'message': 'User deleted successfully'}), 200

# Run Server
if __name__ == '__main__':
    with app.app_context():
        db.create_all()  # Создание всех таблиц
    app.run(debug=True)



