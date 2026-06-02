"""
IPAM application factory — registers blueprints and shared filters.
"""
import os
from flask import Flask, redirect, url_for, request, g
from flask_login import LoginManager, current_user
from flask_smorest import Api
from ipam import ipam_bp, get_project, all_projects
from ne import ne_bp
from hw import hw_bp
from vmware import vmware_bp
from auth import auth_bp, load_user, create_default_admin, AnonymousUser
from api_v1 import api_v1_bp
from customer import customer_bp
from documents import documents_bp
from checks import checks_bp
from services import services_bp
from vrf import vrf_bp
from dns import dns_bp
from dhcp import dhcp_bp
from topology import topology_bp

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-for-local-use-only')

# Flask-Smorest configuration
app.config["API_TITLE"] = "Redis IPAM API"
app.config["API_VERSION"] = "v1"
app.config["OPENAPI_VERSION"] = "3.0.3"
app.config["OPENAPI_URL_PREFIX"] = "/"
app.config["OPENAPI_SWAGGER_UI_PATH"] = "/swagger-ui"
app.config["OPENAPI_SWAGGER_UI_URL"] = "https://cdn.jsdelivr.net/npm/swagger-ui-dist/"

api = Api(app)

login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.anonymous_user = AnonymousUser
login_manager.init_app(app)
login_manager.user_loader(load_user)

@app.context_processor
def inject_nav_helpers():
    """Inject navigation helpers into template context."""
    def is_active(*endpoints):
        ep = request.endpoint or ''
        return ep in endpoints or any(ep.startswith(p) for p in endpoints if p.endswith('.'))
    return {'is_active': is_active}

@app.before_request
def attach_project_context():
    """Attach current project and all projects to Flask global 'g' context."""
    pid = (request.view_args or {}).get('pid')
    g.current_project = get_project(pid) if pid else None
    g.all_projects = sorted(all_projects(), key=lambda p: p['name']) if g.current_project else []

@app.after_request
def add_security_headers(response):
    """Adds security headers to every response."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response

@app.before_request
def require_login():
    """Require authentication for non-public endpoints."""
    if app.config.get('TESTING'):
        return None
    if request.endpoint and (request.endpoint in ('auth.login', 'static') or request.endpoint.startswith('api_v1.')):
        return None
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    return None

@app.template_filter('format_num')
def format_num(value):
    """
    Formats a numeric value as an integer with thousands separators.
    """
    try:
        return f'{int(value):,}'
    except (ValueError, TypeError):
        return value


app.register_blueprint(ipam_bp)
app.register_blueprint(ne_bp)
app.register_blueprint(hw_bp)
app.register_blueprint(vmware_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(customer_bp)
app.register_blueprint(documents_bp)
app.register_blueprint(checks_bp)
app.register_blueprint(services_bp)
app.register_blueprint(vrf_bp)
app.register_blueprint(dns_bp)
app.register_blueprint(dhcp_bp)
app.register_blueprint(topology_bp)
api.register_blueprint(api_v1_bp)

with app.app_context():
    create_default_admin()

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)
