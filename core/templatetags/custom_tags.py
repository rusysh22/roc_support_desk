from django import template

register = template.Library()


@register.filter(name='get_item')
def get_item(dictionary, key):
    """
    Template filter to allow dict key lookup by variable.
    Usage: {{ mydict|get_item:item.name }}
    """
    if isinstance(dictionary, dict):
        return dictionary.get(str(key))
    return None


@register.filter(name='idle_time')
def idle_time(value):
    """
    Returns human-readable elapsed time since the given datetime.
    E.g. '2d 5h', '45m', '3h'
    """
    if not value:
        return ""
    from django.utils import timezone
    now = timezone.now()
    delta = now - value
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "0m"
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"


@register.filter(name='idle_level')
def idle_level(value):
    """
    Returns urgency level based on elapsed time: 'danger' (>2d), 'warning' (>12h), 'info'.
    """
    if not value:
        return ""
    from django.utils import timezone
    delta = timezone.now() - value
    hours = delta.total_seconds() / 3600
    if hours >= 48:
        return "danger"
    elif hours >= 12:
        return "warning"
    return "info"


@register.tag('split_by_page_break')
def split_by_page_break(parser, token):
    """
    Splits a queryset of FormField objects into a list of lists (pages),
    splitting on fields with field_type == 'page_break'.

    Usage:  {% split_by_page_break fields as pages %}
    """
    bits = token.split_contents()
    if len(bits) != 4 or bits[2] != 'as':
        raise template.TemplateSyntaxError(
            "Usage: {% split_by_page_break <fields_var> as <pages_var> %}"
        )
    return SplitByPageBreakNode(bits[1], bits[3])


class SplitByPageBreakNode(template.Node):
    def __init__(self, fields_var, pages_var):
        self.fields_var = template.Variable(fields_var)
        self.pages_var = pages_var

    def render(self, context):
        fields = self.fields_var.resolve(context)
        pages = [[]]
        for field in fields:
            if field.field_type == 'page_break':
                # Start a new page; include the page_break field itself
                # so templates can reference it if needed
                pages.append([])
            else:
                pages[-1].append(field)
        # Remove any empty trailing pages
        pages = [p for p in pages if p]
        if not pages:
            pages = [[]]
        context[self.pages_var] = pages
        return ''


@register.tag('has_page_break')
def has_page_break(parser, token):
    """
    Check if any field in the queryset has field_type 'page_break'.
    Usage: {% has_page_break fields as is_multipage %}
    """
    bits = token.split_contents()
    if len(bits) != 4 or bits[2] != 'as':
        raise template.TemplateSyntaxError(
            "Usage: {% has_page_break <fields_var> as <result_var> %}"
        )
    return HasPageBreakNode(bits[1], bits[3])


class HasPageBreakNode(template.Node):
    def __init__(self, fields_var, result_var):
        self.fields_var = template.Variable(fields_var)
        self.result_var = result_var

    def render(self, context):
        fields = self.fields_var.resolve(context)
        context[self.result_var] = any(
            f.field_type == 'page_break' for f in fields
        )
        return ''
