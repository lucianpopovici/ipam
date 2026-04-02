"""
Application factory — registers blueprints and shared filters.
"""
from flask import Flask
from ipam   import ipam_bp
from ne     import ne_bp
from hw     import hw_bp
from vmware import vmware_bp

app = Flask(__name__)
app.config['SECRET_KEY'] = 'change-me-in-production'

@app.template_filter('format_num')
def format_num(value):
    """
    Format value as an integer
    """
    try:
        return f'{int(value):,}'
    except (ValueError, TypeError):
        return value



app.register_blueprint(ipam_bp)
app.register_blueprint(ne_bp)
app.register_blueprint(hw_bp)
app.register_blueprint(vmware_bp)

if __name__ == '__main__':
    app.run(host='0.0.0.0',debug=True)
