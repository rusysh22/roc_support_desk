"""Category dropdowns: the whole tree is offered level by level; filters on a group cover everything below it."""
from django.template import Context, Template
from django.test import TestCase

from cases.category_tree import CategoryTreeSelect, category_rows
from cases.models import CaseCategory


class CategoryCascadeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.apps = CaseCategory.objects.create(name="Apps", slug="apps", prefix_code="AP")
        cls.erp = CaseCategory.objects.create(name="ERP", slug="erp", prefix_code="ER", parent=cls.apps)
        cls.gl = CaseCategory.objects.create(name="Ledger", slug="ledger", prefix_code="LG", parent=cls.erp)
        cls.mail = CaseCategory.objects.create(name="Mail", slug="mail", prefix_code="ML", parent=cls.apps)

    def test_rows_mark_groups_and_final_categories(self):
        leaf = {r["slug"]: r["leaf"] for r in category_rows()}
        self.assertEqual(leaf, {"apps": False, "erp": False, "ledger": True, "mail": True})

    def test_widget_lists_every_level_with_its_parent(self):
        html = CategoryTreeSelect().render("category", str(self.gl.pk))
        self.assertIn("data-category-cascade", html)
        for cat in (self.apps, self.erp, self.gl, self.mail):
            self.assertIn(f'data-key="{cat.pk}"', html)
        self.assertRegex(html, rf'value="{self.gl.pk}"[^>]*selected|selected[^>]*value="{self.gl.pk}"')

    def test_template_tag_marks_the_selected_slug(self):
        out = Template('{% load category_tree %}{% category_options "slug" sel %}').render(Context({"sel": "erp"}))
        self.assertIn('value="erp" selected', out)
        self.assertIn('data-leaf="1">Ledger', out)

    def test_filter_on_a_group_includes_all_below_it(self):
        self.assertEqual(set(CaseCategory.subtree_ids("apps")), {self.apps.pk, self.erp.pk, self.gl.pk, self.mail.pk})
        self.assertEqual(set(CaseCategory.subtree_ids("erp")), {self.erp.pk, self.gl.pk})
        self.assertEqual(CaseCategory.subtree_ids("ledger"), [self.gl.pk])
        self.assertEqual(CaseCategory.subtree_ids("nope"), [])
