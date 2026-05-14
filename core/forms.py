"""
Form state helpers — Phase 1 of UX improvements.

Usage in views:
    errors = form_errors(
        ('name', bool(name), 'Name is required.'),
        ('cidr', validate_cidr(cidr), 'Invalid CIDR.'),
    )
    if errors:
        return render_template('...', errors=errors, form_values=request.form)

Usage in templates:
    <input name="name" class="form-control {% if errors.name %}is-invalid{% endif %}"
           value="{{ form_values.name or entity.name or '' }}">
    {% if errors.name %}<div class="invalid-feedback">{{ errors.name }}</div>{% endif %}
"""


def form_errors(*triples) -> dict:
    """
    Build a per-field error dict from validation triples.

    Each triple is (field_name, is_valid_bool, error_message).
    Returns {} when all pass; stops at first failure per field.
    """
    errors = {}
    for field, ok, message in triples:
        if not ok and field not in errors:
            errors[field] = message
    return errors
