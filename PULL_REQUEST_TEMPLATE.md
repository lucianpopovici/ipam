## Summary
Adds pylint configuration and fixes code quality issues to ensure proper component linking and best practices.

### Changes
- **app.py**: Fixed import spacing, added docstrings, improved formatting
- **db.py**: Added module docstring, proper formatting
- **.pylintrc**: Added comprehensive pylint configuration with sensible defaults for Flask/Redis projects

### Key Improvements
- ✅ Fixed import alignment (proper PEP 8 spacing)
- ✅ Added missing docstrings to functions
- ✅ Set max line length to 120 chars (Flask-friendly)
- ✅ Disabled overly strict rules for web frameworks
- ✅ Configured pylint for redis and flask extensions

### Related Issues
Closes #2, #3, #4 (part of component linking review)

### Testing
Run locally:
```bash
pylint $(git ls-files '*.py')
make fast
```