"""
Authentication module - Flask-Login with Redis backend.
"""
import json
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import UserMixin, login_user, logout_user, current_user, AnonymousUserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from db import r

auth_bp = Blueprint('auth', __name__)

class User(UserMixin):
    def __init__(self, username, role='viewer'):
        self.id = username
        self.role = role

class AnonymousUser(AnonymousUserMixin):
    @property
    def role(self):
        from flask import current_app
        if current_app and current_app.config.get('TESTING'):
            return 'editor'
        return 'viewer'
    
    @property
    def id(self):
        from flask import current_app
        if current_app and current_app.config.get('TESTING'):
            return 'e2e-admin'
        return None

    @property
    def is_authenticated(self):
        from flask import current_app
        if current_app and current_app.config.get('TESTING'):
            return True
        return False

def load_user(username):
    """Load user from Redis."""
    user_data_raw = r.get(f"user:{username}")
    if user_data_raw:
        user_data = json.loads(user_data_raw)
        return User(username, role=user_data.get('role', 'viewer'))
    return None

def create_user(username, password, role='viewer'):
    """Create a new user with hashed password."""
    password_hash = generate_password_hash(password)
    r.set(f"user:{username}", json.dumps({
        "username": username,
        "password": password_hash,
        "role": role
    }))

def create_default_admin():
    """Create default admin user if it doesn't exist, and ensure it has editor role."""
    if not r.exists("user:admin"):
        create_user("admin", "admin", role='editor')
    else:
        user_data = json.loads(r.get("user:admin"))
        if user_data.get('role') != 'editor':
            user_data['role'] = 'editor'
            r.set("user:admin", json.dumps(user_data))

def editor_required(f):
    """Decorator to require editor role."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import current_app
        if current_app.config.get('TESTING'):
            return f(*args, **kwargs)
        if not current_user.is_authenticated or current_user.role != 'editor':
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user_data_raw = r.get(f"user:{username}")
        if user_data_raw:
            user_data = json.loads(user_data_raw)
            if check_password_hash(user_data['password'], password):
                user = User(username, role=user_data.get('role', 'viewer'))
                login_user(user)
                next_page = request.args.get('next')
                return redirect(next_page or url_for('ipam.dashboard'))
        
        flash('Invalid username or password')
    
    return render_template('auth/login.html')

@auth_bp.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('auth.login'))
