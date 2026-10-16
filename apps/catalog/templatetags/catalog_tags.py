from urllib.parse import urlencode
from django import template
from django.utils.safestring import mark_safe
from apps.catalog.currency import format_inr

register = template.Library()

@register.filter(name='inr')
def inr_filter(value):
    """Formats a price as Indian Rupees with exactly 2 decimal places: ₹1,000.00"""
    return mark_safe(format_inr(value, symbol=True))

@register.filter(name='format_inr')
def format_inr_filter(value):
    """Alias for inr filter."""
    return mark_safe(format_inr(value, symbol=True))

@register.filter(name='inr_num')
def inr_num_filter(value):
    """Formats numeric part with Indian grouping and exactly 2 decimal places without ₹ symbol: 1,000.00"""
    return mark_safe(format_inr(value, symbol=False))

@register.simple_tag(takes_context=True)
def query_transform(context, **kwargs):
    """
    Returns an updated, clean query string based on current request.GET.
    Handles adding, updating, or removing parameters without malformed syntax.
    """
    request = context.get('request')
    if not request:
        return ''
    
    query = request.GET.copy()
    for key, value in kwargs.items():
        if value is None or value == '':
            query.pop(key, None)
        else:
            query[key] = str(value)
            
    # Reset pagination when search/filter criteria change
    if any(k in kwargs for k in ['category', 'stock', 'sort', 'q']):
        if 'page' not in kwargs:
            query.pop('page', None)

    encoded = query.urlencode()
    return mark_safe(f"?{encoded}") if encoded else ""
