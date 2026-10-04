from django import template

from transporte.models import soles

register = template.Library()


@register.filter
def dinero(centimos):
    return soles(centimos or 0)
