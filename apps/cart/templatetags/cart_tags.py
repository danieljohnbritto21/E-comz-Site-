from django import template

register = template.Library()

@register.filter(name='get_item')
def get_item(dictionary, key):
    """Safely retrieves a key from dictionary, returning 0 if not found."""
    if isinstance(dictionary, dict):
        return dictionary.get(key, 0)
    return 0
