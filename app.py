"""
Application factory — registers blueprints and shared filters.
"""
from flask import Flask

app = Flask(__name__)
app.config['SECRET_KEY'] = 'change-me-in-production'

@app.template_filter('format_num')
def format_num(value):
    try:
        return f'{int(value):,}'
    except (ValueError, TypeError):
        return value

from ipam import ipam_bp
from ne   import ne_bp
from hw   import hw_bp

app.register_blueprint(ipam_bp)
app.register_blueprint(ne_bp)
app.register_blueprint(hw_bp)

if __name__ == '__main__':
    app.run(host='192.168.56.107',debug=True)
