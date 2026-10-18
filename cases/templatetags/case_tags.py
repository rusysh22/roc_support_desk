import json

from django import template

from cases.models import PriorityMatrixRule

register = template.Library()


@register.simple_tag
def priority_matrix_json():
    return json.dumps(PriorityMatrixRule.as_lookup_dict())
