from django import template
from django.conf import settings

from django_tables2.utils import AttributeDict

register = template.Library()


@register.simple_tag
def render_attrs(attrs, **kwargs):
    """
    render attrs.
    """
    ret = AttributeDict(kwargs)

    if attrs is not None:
        ret.update(attrs)

    return ret.as_html()
