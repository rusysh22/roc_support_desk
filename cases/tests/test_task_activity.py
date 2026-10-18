import json
import shutil
import subprocess
from html.parser import HTMLParser
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from pathlib import Path

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.context_processors import my_tickets_badge
from core.models import User
from cases.activity import annotate_activity, mark_activity_read, unread_cases, visible_cases
from cases.models import CaseActivityRead, CaseAuditLog, CaseCategory, CaseRecord, Message, SavedCaseFilter
from cases import views, views_chat
from cases.forms import validate_staff_reply_attachment


class TaskActivityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def user(name, role):
            return User.objects.create_user(username=name, login_username=name, email=f'{name}@example.com', role_access=role, is_staff=role != User.RoleAccess.PORTALUSER)
        cls.staff = user('desk', User.RoleAccess.SUPPORTDESK)
        cls.other_staff = user('desk2', User.RoleAccess.SUPPORTDESK)
        cls.engineer = user('engineer', User.RoleAccess.ENGINEER)
        cls.portal = user('requester', User.RoleAccess.PORTALUSER)
        cls.follower = user('follower', User.RoleAccess.PORTALUSER)
        cls.category = CaseCategory.objects.create(name='Support', slug='support', prefix_code='IT')
        cls.confidential = CaseCategory.objects.create(name='Private', slug='private', prefix_code='PR', is_confidential=True)

    def setUp(self):
        self.factory = RequestFactory()

    def request(self, path, user=None, data=None, method='get'):
        request = getattr(self.factory, method)(path, data or {})
        request.user = user or self.staff
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        return request

    def case(self, **kwargs):
        defaults = dict(category=self.category, subject='Network issue', problem_description='Cannot connect', requester_email=self.portal.email)
        defaults.update(kwargs)
        return CaseRecord.objects.create(**defaults)

    def portal_list_context(self, data=None):
        with patch('cases.views_chat.render', return_value=HttpResponse()) as render:
            views_chat.my_tickets(self.request(reverse('chat:my_tickets'), self.portal, data=data))
            return render.call_args.args[2]

    def test_portal_status_groups_cover_entire_current_lifecycle(self):
        expected = {
            'action': {'PendingUser', 'RevisionRequired', 'Resolved'},
            'progress': {'New', 'Assigned', 'InProgress', 'PendingExternal'},
            'approval': {'PendingApproval'},
            'closed': {'Closed'},
            'inactive': {'Rejected', 'Cancelled'},
        }
        self.assertEqual(set().union(*expected.values()), set(CaseRecord.Status.values))
        for status in CaseRecord.Status.values:
            self.case(status=status)
        self.case(requester_email='someone-else@example.com')
        for group, statuses in {'': set(CaseRecord.Status.values), **expected}.items():
            with self.subTest(group=group):
                context = self.portal_list_context({'view': group})
                self.assertEqual({item['case'].status for item in context['tickets']}, statuses)
                counts = {tab['key']: tab['count'] for tab in context['ticket_tabs']}
                self.assertEqual(counts[''], 11)
                self.assertEqual(sum(count for key, count in counts.items() if key), counts[''])
                self.assertEqual(counts['action'], 3)
                self.assertEqual(counts['progress'], 4)
                self.assertEqual(counts['approval'], 1)
                self.assertEqual(counts['closed'], 1)
                self.assertEqual(counts['inactive'], 2)
                self.assertEqual(dict(context['status_choices'])['PendingUser'], 'Needs Your Reply')

    def test_portal_filter_counts_and_tab_links_preserve_search_and_ownership(self):
        query = 'Power & network +'
        for status in ('New', 'PendingUser', 'Closed'):
            self.case(subject=query, status=status)
        self.case(subject='Different search')
        for status in ('New', 'PendingApproval'):
            followed = self.case(subject=query, status=status, requester_email='other@example.com')
            followed.followers.add(self.portal)
        context = self.portal_list_context({'q': query, 'role': 'following', 'sort': 'oldest', 'view': 'approval', 'page': 9})
        self.assertEqual(context['page_obj'].paginator.count, 1)
        self.assertTrue(context['tickets'][0]['is_follower'])
        self.assertEqual(context['count_role_all'], 5)
        self.assertEqual(context['count_mine'], 3)
        self.assertEqual(context['count_following'], 2)
        counts = {tab['key']: tab['count'] for tab in context['ticket_tabs']}
        self.assertEqual(counts[''], 2)
        self.assertEqual(counts['progress'], 1)
        self.assertEqual(counts['approval'], 1)
        for tab in context['ticket_tabs']:
            parameters = parse_qs(urlparse(tab['url']).query)
            self.assertEqual(parameters['q'], [query])
            self.assertEqual(parameters['role'], ['following'])
            self.assertEqual(parameters['sort'], ['oldest'])
            self.assertNotIn('page', parameters)
            self.assertNotIn('status', parameters)

    def test_portal_exact_status_supersedes_group_and_invalid_filters_reset(self):
        closed = self.case(status='Closed')
        self.case(status='PendingUser')
        context = self.portal_list_context({'status': 'Closed', 'view': 'action'})
        self.assertEqual([item['case'].pk for item in context['tickets']], [closed.pk])
        self.assertEqual(context['view_filter'], '')
        context = self.portal_list_context({'status': 'unknown', 'view': 'unknown', 'sort': 'unknown', 'role': 'unknown'})
        self.assertEqual(context['page_obj'].paginator.count, 2)
        self.assertEqual(context['view_filter'], '')
        self.assertEqual(context['status_filter'], '')
        self.assertEqual(context['role_filter'], '')
        self.assertEqual(context['sort_by'], 'updated')

    def test_portal_pagination_keeps_group_search_ownership_and_sort(self):
        from core.list_pagination import LIST_PAGE_SIZE
        query = 'Power & network +'
        for _ in range(LIST_PAGE_SIZE + 1):  # one more than a page, so page links render
            self.case(subject=query, status='Closed')
        data = {'q': query, 'view': 'closed', 'role': 'mine', 'sort': 'oldest'}
        response = views_chat.my_tickets(self.request(reverse('chat:my_tickets'), self.portal, data=data))

        class Parser(HTMLParser):
            links = []

            def handle_starttag(parser, tag, attrs):
                attrs = dict(attrs)
                href = attrs.get('href', '')
                if tag == 'a' and href.startswith('?') and 'page=' in href:
                    parser.links.append(href)

        parser = Parser()
        parser.feed(response.content.decode())
        self.assertTrue(parser.links)
        for href in parser.links:
            parameters = parse_qs(urlparse(href).query)
            for key, value in data.items():
                self.assertEqual(parameters[key], [value])

    def test_opening_ticket_clears_only_current_users_marker(self):
        case = self.case()
        mark_activity_read(case, self.staff)
        self.assertFalse(unread_cases(self.staff).filter(pk=case.pk).exists())
        self.assertTrue(unread_cases(self.other_staff).filter(pk=case.pk).exists())

    def test_portal_follower_read_position_is_independent(self):
        case = self.case()
        case.followers.add(self.follower)
        mark_activity_read(case, self.portal)
        self.assertFalse(unread_cases(self.portal).exists())
        self.assertTrue(unread_cases(self.follower).exists())

    def test_late_read_receipt_cannot_move_read_position_backwards(self):
        case = self.case()
        at = timezone.now()
        mark_activity_read(case, self.staff, at)
        mark_activity_read(case, self.staff, at - timedelta(hours=1))
        self.assertEqual(CaseActivityRead.objects.get(case=case, user=self.staff).last_read_at, at)

    def test_reply_without_updating_ticket_becomes_latest_activity(self):
        old = self.case(subject='Old ticket with new reply')
        new = self.case(subject='New ticket')
        mark_activity_read(old, self.staff)
        Message.objects.create(case=old, body='New portal reply', direction='IN', channel='Web')
        request = self.request('/desk/cases/')
        cases, _, field, _ = views._filter_case_queryset(request, CaseRecord.objects.all())
        self.assertEqual(field, 'last_activity')
        self.assertEqual(list(cases.values_list('pk', flat=True)), [old.pk, new.pk])
        self.assertTrue(cases.get(pk=old.pk).has_unread_activity)

    def test_assignment_audit_makes_ticket_unread_for_assignee(self):
        case = self.case(assigned_to=self.engineer)
        mark_activity_read(case, self.engineer)
        CaseAuditLog.objects.create(case=case, action='Updated', field_name='assigned_to', new_value=str(self.engineer.pk), created_by=self.staff)
        self.assertTrue(unread_cases(self.engineer).filter(pk=case.pk).exists())

    def test_engineer_notifications_are_scoped_and_confidential_is_hidden(self):
        assigned = self.case(assigned_to=self.engineer)
        self.case()
        self.case(assigned_to=self.engineer, category=self.confidential)
        self.assertEqual(list(unread_cases(self.engineer).values_list('pk', flat=True)), [assigned.pk])

    def test_portal_notifications_do_not_expose_other_users_tickets(self):
        own = self.case(subject='Own ticket')
        self.case(subject='Secret other ticket', requester_email='other@example.com')
        response = views.notification_bell(self.request('/desk/notifications/', self.portal))
        html = response.content.decode()
        self.assertIn(own.case_number, html)
        self.assertNotIn('Secret other ticket', html)
        self.assertIn(reverse('chat:room', args=[own.pk]), html)
        self.assertIn('my-tickets-unread-badge', html)

    def test_notifications_older_than_24_hours_are_kept_until_opened(self):
        case = self.case()
        CaseRecord.objects.filter(pk=case.pk).update(created_at=timezone.now() - timedelta(days=3), updated_at=timezone.now() - timedelta(days=3))
        self.assertEqual(unread_cases(self.staff).count(), 1)

    def test_portal_progress_and_closed_updates_count_without_chat(self):
        case = self.case()
        mark_activity_read(case, self.portal)
        CaseRecord.objects.filter(pk=case.pk).update(status=CaseRecord.Status.CLOSED, updated_at=timezone.now())
        response = views_chat.my_tickets_status(self.request('/portal/chat/my-tickets/status/', self.portal))
        self.assertEqual(json.loads(response.content)['tickets'][0]['unread'], 1)
        self.assertEqual(my_tickets_badge(self.request('/', self.portal))['my_tickets_unread_count'], 1)

    def test_empty_email_portal_user_sees_only_followed_tickets(self):
        user = User.objects.create_user(username='noemail', login_username='noemail', role_access=User.RoleAccess.PORTALUSER)
        self.case(requester_email='')
        self.assertFalse(visible_cases(user).exists())

    def test_filters_and_order_match_table_kanban_calendar_and_polling(self):
        chosen = self.case(subject='Matched', status=CaseRecord.Status.IN_PROGRESS, assigned_to=self.engineer, tags='network')
        self.case(subject='Wrong status', tags='network')
        self.case(subject='Wrong tag', status=CaseRecord.Status.IN_PROGRESS, assigned_to=self.engineer)
        query = {'status': 'InProgress', 'assigned_to': str(self.engineer.pk), 'tags': 'network', 'read_status': 'unread', 'confidential': '', 'sla': ''}
        request = self.request('/desk/cases/', data=query)
        cases, _, _, _ = views._filter_case_queryset(request, CaseRecord.objects.all())
        self.assertEqual(list(cases.values_list('pk', flat=True)), [chosen.pk])
        for view, path in [(views.case_kanban, '/desk/cases/kanban/'), (views.case_kanban_partial, '/desk/cases/partials/kanban/'), (views.case_calendar, '/desk/cases/calendar/')]:
            with patch('cases.views.render', return_value=HttpResponse()) as render:
                view(self.request(path, data=query))
                context = render.call_args.args[2]
                if 'kanban_data' in context:
                    actual = [case.pk for col in context['kanban_data'] for case in col['cases']]
                else:
                    actual = [event['id'] for event in json.loads(context['events_json'])]
                    actual = [str(pk) for pk in actual]
                self.assertEqual(actual, [chosen.pk] if 'kanban_data' in context else [str(chosen.pk)])

    def test_live_filter_url_is_saved_for_submit_return(self):
        request = self.request('/desk/cases/partials/results/', data={'tags': 'network', 'status': 'Assigned'})
        with patch('cases.views.render', return_value=HttpResponse()):
            response = views.case_list_results_partial(request)
        self.assertEqual(request.session['task_management_url'], response['HX-Push-Url'])
        self.assertIn('tags=network', response['HX-Push-Url'])

    def test_submit_returns_to_original_filtered_view(self):
        case = self.case()
        back = '/desk/cases/kanban/?tags=network&status=New'
        data = {'status': case.status, 'impact': case.impact, 'severity': case.severity, 'case_type': case.case_type, 'quick_notes': 'Investigating', 'back_url': back}
        response = views.case_update_rca(self.request(f'/desk/cases/{case.pk}/rca/', data=data, method='post'), case.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['HX-Redirect'], back)

    def test_saved_filters_render_in_every_view(self):
        self.case()
        SavedCaseFilter.objects.create(user=self.staff, name='Network favorite', query_string='tags=network')
        for view, path in [(views.case_list, '/desk/cases/'), (views.case_kanban, '/desk/cases/kanban/'), (views.case_calendar, '/desk/cases/calendar/')]:
            response = view(self.request(path, data={'tags': 'network'}))
            html = response.content.decode()
            self.assertIn('Network favorite', html)
            self.assertIn(f'{path}?tags=network', html)
            self.assertIn('data-task-view', html)

    def test_personal_unread_pill_is_rendered_on_ticket_number(self):
        case = self.case()
        cases = annotate_activity(CaseRecord.objects.filter(pk=case.pk), self.staff)
        html = render_to_string('partials/case_table_rows.html', {'cases': cases})
        self.assertIn('jk-table-link jk-ticket-unread', html)

    def test_sidebar_repeated_navigation_and_portal_bell_are_enabled(self):
        html = render_to_string('base.html', {'user': self.portal, 'user_modules': ['my_tickets'], 'licensed_features': set()}, request=self.request('/', self.portal))
        self.assertIn('hx-boost="false"', html)
        self.assertIn('hx-get="' + reverse('chat:notifications') + '"', html)
        self.assertNotIn('hx-get="' + reverse('desk:notifications') + '"', html)
        self.assertNotIn('hx-on::before-request="window.__sidebarNavInFlight = true"', html)

    @patch('licensing.models.LicenseRecord.get_effective_status', return_value='active')
    @override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['testserver'])
    def test_portal_bell_polling_does_not_redirect_between_menu_pages(self, _license_status):
        own = self.case(subject='Portal owned update')
        self.case(subject='Private update for someone else', requester_email='other@example.com')
        self.client.force_login(self.portal)

        class BellParser(HTMLParser):
            url = None

            def handle_starttag(parser, tag, attrs):
                attrs = dict(attrs)
                if '/notifications/' in attrs.get('hx-get', ''):
                    parser.url = attrs['hx-get']

        for page_url in ('/', reverse('chat:my_tickets'), reverse('chat:my_approvals'), '/kb/'):
            with self.subTest(page=page_url):
                page = self.client.get(page_url)
                self.assertEqual(page.status_code, 200)
                parser = BellParser()
                parser.feed(page.content.decode())
                self.assertIsNotNone(parser.url)
                for _ in range(3):
                    response = self.client.get(parser.url, HTTP_HX_REQUEST='true', HTTP_HX_CURRENT_URL=f'http://testserver{page_url}')
                    self.assertEqual(response.status_code, 200)
                    self.assertNotIn('HX-Redirect', response.headers)
                    self.assertNotIn('Location', response.headers)
                    self.assertContains(response, 'notif-metadata')
                    self.assertContains(response, own.case_number)
                    self.assertNotContains(response, 'Private update for someone else')
        self.assertFalse(CaseActivityRead.objects.filter(case=own, user=self.portal).exists())

    @patch('licensing.models.LicenseRecord.get_effective_status', return_value='active')
    @override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['testserver'])
    def test_shared_bell_keeps_authentication_role_scope_and_desk_restriction(self, _license_status):
        own = self.case(subject='Followed update', assigned_to=self.engineer)
        own.followers.add(self.follower)
        other = self.case(subject='Other requester update', requester_email='other@example.com')
        for user in (self.portal, self.follower, self.engineer, self.staff):
            with self.subTest(role=user.role_access):
                self.client.force_login(user)
                response = self.client.get(reverse('chat:notifications'), HTTP_HX_REQUEST='true')
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('HX-Redirect', response.headers)
                self.assertContains(response, own.case_number)
                if user == self.staff:
                    self.assertContains(response, other.case_number)
                    self.assertContains(response, reverse('desk:case_detail', args=[own.pk]))
                else:
                    self.assertNotContains(response, other.case_number)
                if user in (self.portal, self.follower):
                    self.assertContains(response, reverse('chat:room', args=[own.pk]))

        self.client.force_login(self.portal)
        blocked = self.client.get(reverse('desk:case_list'), HTTP_HX_REQUEST='true')
        # portal users who hit a desk page are sent to their own tickets
        self.assertEqual(blocked['HX-Redirect'], reverse('chat:my_tickets'))
        self.client.logout()
        anonymous = self.client.get(reverse('chat:notifications'), HTTP_HX_REQUEST='true')
        self.assertIn(reverse('login'), anonymous['HX-Redirect'])
        self.assertNotContains(anonymous, own.case_number)

    def test_ticket_navigation_does_not_inherit_list_fragment_boosting(self):
        first = self.case()
        second = self.case()
        request = self.request('/desk/cases/', data={'tags': 'network'})
        request.GET = request.GET.copy()
        request.GET.pop('tags')
        context = views._case_list_results_context(request)
        context['cases'] = Paginator(context['cases'].object_list, 1).get_page(1)

        class NavigationParser(HTMLParser):
            void_tags = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

            def __init__(parser):
                super().__init__()
                parser.stack = []
                parser.links = []

            def handle_starttag(parser, tag, attrs):
                attrs = dict(attrs)
                inherited = dict(parser.stack[-1][1]) if parser.stack else {}
                inherited.update({key: value for key, value in attrs.items() if key.startswith('hx-')})
                if tag == 'a':
                    parser.links.append((attrs.get('href', ''), inherited))
                if tag not in parser.void_tags:
                    parser.stack.append((tag, inherited))

            def handle_endtag(parser, tag):
                for index in range(len(parser.stack) - 1, -1, -1):
                    if parser.stack[index][0] == tag:
                        del parser.stack[index:]
                        break

        detail_urls = {reverse('desk:case_detail', args=[case.pk]) for case in (first, second)}
        for template in ('admin/case_list.html', 'partials/case_results.html'):
            with self.subTest(template=template):
                parser = NavigationParser()
                parser.feed(render_to_string(template, context, request=request))
                tickets = [hx for href, hx in parser.links if href in detail_urls]
                sorting = [hx for href, hx in parser.links if href.startswith('?') and 'sort=' in href]
                pagination = [hx for href, hx in parser.links if href.startswith('?') and 'page=' in href]
                self.assertTrue(tickets)
                self.assertTrue(sorting)
                self.assertTrue(pagination)
                for hx in tickets:
                    self.assertNotEqual(hx.get('hx-boost'), 'true')
                    self.assertNotIn('hx-select', hx)
                    self.assertNotIn('hx-target', hx)
                for hx in sorting + pagination:
                    self.assertEqual(hx.get('hx-boost'), 'true')
                    self.assertEqual(hx.get('hx-select'), '#case-results')
                    self.assertEqual(hx.get('hx-target'), '#case-results')

    def test_portal_list_renders_progress_unread_and_activity_snapshot(self):
        case = self.case()
        response = views_chat.my_tickets(self.request('/portal/chat/my-tickets/', self.portal))
        html = response.content.decode()
        self.assertIn('ticket-activity-snapshot', html)
        self.assertIn('jk-ticket-unread', html)
        self.assertIn(case.case_number, html)

    def test_chat_search_controls_search_messages_instead_of_switching_tickets(self):
        case = self.case()
        Message.objects.create(case=case, direction='OUT', channel='Web', body='Searchable reply', sender_staff=self.staff)
        Message.objects.create(case=case, direction='OUT', channel='Web', body='Retracted reply', is_deleted=True)
        html = views_chat.chat_room(self.request(reverse('chat:room', args=[case.pk]), self.portal), case.pk).content.decode()
        self.assertIn('id="chat-message-search-input"', html)
        self.assertIn('Search messages in this ticket', html)
        self.assertIn('js/chat-message-search.js', html)
        self.assertIn('data-search-deleted="true"', html)
        self.assertNotIn('ticket-switcher-input', html)
        self.assertNotIn('initTicketSearchSuggest(', html)

    def test_chat_message_search_scoping_navigation_and_live_updates(self):
        if not shutil.which('node'):
            self.skipTest('Node is needed to validate chat search interactions')
        program = r'''
const assert = require('node:assert/strict');
const api = require(process.argv[1]);
class Element {
  constructor(tag = 'div') {
    this.tag = tag; this.dataset = {}; this.children = []; this.events = {}; this.attributes = {};
    this.hidden = false; this.value = ''; this.textContent = '';
    const classes = new Set();
    this.classList = { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c) };
  }
  addEventListener(name, fn) { this.events[name] = fn; }
  setAttribute(name, value) { this.attributes[name] = value; }
  append(...children) { this.children.push(...children); }
  replaceChildren() { this.children = []; }
  focus() { this.focused = true; }
  querySelectorAll(selector) { return selector === 'button' ? this.children.filter(c => c.tag === 'button') : []; }
}
function message(id, text, deleted = false) {
  const element = new Element();
  element.dataset = { msgId: id, searchText: text, searchDeleted: String(deleted), searchSender: 'Support', searchTime: '12:30' };
  return element;
}
const older = message('older', 'VPN is down');
const latest = message('latest', 'The VPN has been fixed');
const deleted = message('deleted', 'VPN secret', true);
const outsideTicket = message('outside', 'VPN from another ticket');
let messages = [older, deleted, latest];
const ids = {};
for (const id of ['chat-search-toggle','chat-message-search','chat-message-search-input','chat-message-search-results','chat-message-search-status','chat-message-search-prev','chat-message-search-next','chat-message-search-close','chat-scroll']) ids[id] = new Element();
ids['chat-message-search'].hidden = true;
const thread = ids['chat-scroll'];
thread.querySelectorAll = selector => {
  if (selector === '.chat-search-active') return messages.filter(m => m.classList.contains('chat-search-active'));
  assert.equal(selector, '#chat-history [data-msg-id], #chat-live [data-msg-id]');
  return messages;
};
global.document = { getElementById: id => ids[id], createElement: tag => new Element(tag) };
const jumps = [];
global.window = { chatJumpToMessage: id => jumps.push(id) };
global.setTimeout = fn => { fn(); return 1; };
global.clearTimeout = () => {};
let mutation;
global.MutationObserver = class { constructor(fn) { mutation = fn; } observe(target, options) { assert.equal(target, thread); assert.deepEqual(options.attributeFilter, ['data-search-deleted']); } };
const controller = api.init();
ids['chat-search-toggle'].events.click();
assert.equal(ids['chat-message-search'].hidden, false);
const input = ids['chat-message-search-input'];
input.value = ' vpn ';
input.events.input();
assert.equal(ids['chat-message-search-results'].children.length, 2);
assert.deepEqual(api.findMatches(api.collectMessages(thread), 'VPN').map(m => m.id), ['latest','older']);
assert.equal(ids['chat-message-search-status'].textContent, '2 matching messages');
assert(!api.collectMessages(thread).some(m => m.id === outsideTicket.dataset.msgId));
ids['chat-message-search-results'].children[0].events.click();
assert.equal(jumps.at(-1), 'latest');
assert(latest.classList.contains('chat-search-active'));
ids['chat-message-search-next'].events.click();
assert.equal(jumps.at(-1), 'older');
ids['chat-message-search-prev'].events.click();
assert.equal(jumps.at(-1), 'latest');
messages.push(message('incoming', 'New VPN update'));
mutation();
assert.equal(ids['chat-message-search-results'].children.length, 3);
assert.equal(jumps.at(-1), 'latest'); // Incoming updates do not move the scroll position.
latest.dataset.searchDeleted = 'true';
mutation();
assert.equal(ids['chat-message-search-results'].children.length, 2);
assert(!latest.classList.contains('chat-search-active'));
input.value = '<img src=x onerror=alert(1)>';
messages.push(message('literal', input.value));
input.events.input();
assert.equal(ids['chat-message-search-results'].children.length, 1);
assert.equal(ids['chat-message-search-results'].children[0].children.find(c => c.tag === 'mark').textContent, input.value);
input.value = 'not present'; input.events.input();
assert.equal(ids['chat-message-search-status'].textContent, 'No messages found in this ticket.');
assert.equal(ids['chat-message-search-next'].disabled, true);
ids['chat-message-search'].events.keydown({ key: 'Escape', preventDefault() {}, target: input });
assert.equal(ids['chat-message-search'].hidden, true);
assert.equal(input.value, '');
assert.equal(ids['chat-search-toggle'].attributes['aria-expanded'], 'false');
console.log('Chat message search scope, safe snippets, navigation, deletion and live updates passed');
'''
        script = Path(__file__).resolve().parents[2] / 'static/js/chat-message-search.js'
        result = subprocess.run(['node', '-e', program, str(script)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_portal_snapshot_counts_replies_in_one_query(self):
        case = self.case()
        mark_activity_read(case, self.portal)
        for _ in range(2):
            Message.objects.create(case=case, direction='OUT', channel='Web', body='Update', sender_staff=self.staff)
        with self.assertNumQueries(1):
            from cases.activity import portal_activity_snapshot
            snapshot = portal_activity_snapshot(self.portal)
        self.assertEqual(snapshot[0]['unread'], 2)

    def test_supported_video_passes_real_mime_validation(self):
        uploaded = SimpleUploadedFile('clip.mp4', b'fake video for mocked MIME detector', content_type='video/mp4')
        with patch('cases.forms.magic.from_buffer', return_value='video/mp4'):
            self.assertEqual(validate_staff_reply_attachment(uploaded), 'video/mp4')
        self.assertEqual(uploaded.tell(), 0)

    def test_invalid_attachment_does_not_send_or_clear_draft(self):
        case = self.case()
        uploaded = SimpleUploadedFile('photo.png', b'not an image', content_type='image/png')
        request = self.request(f'/desk/cases/{case.pk}/reply/', data={'body': 'Keep this draft', 'attachments': uploaded}, method='post')
        with patch('cases.forms.magic.from_buffer', return_value='application/x-executable'):
            response = views.case_send_reply(request, case.pk)
        self.assertEqual(response.status_code, 400)
        self.assertIn('show-toast', response['HX-Trigger'])
        self.assertFalse(case.messages.exists())

    def test_key_handlers_and_attachment_preview_lifecycle(self):
        if not shutil.which('node'):
            self.skipTest('Node is needed to validate the chat interaction handlers')
        case = self.case()
        html = views.case_detail(self.request(f'/desk/cases/{case.pk}/'), case.pk).content.decode()

        class Parser(HTMLParser):
            component = None
            enter = None
            def handle_starttag(parser, tag, attrs):
                attrs = dict(attrs)
                if attrs.get('id') == 'reply-form':
                    parser.component = attrs['x-data']
                if tag == 'textarea' and attrs.get('x-ref') == 'replyInput':
                    parser.enter = attrs['@keydown.enter']

        parser = Parser()
        parser.feed(html)
        program = r'''
const assert = require('node:assert/strict');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const state = new Function('return (' + input.component + ')')();
let revocations = [];
global.URL = { createObjectURL: file => 'blob:' + file.name, revokeObjectURL: url => revocations.push(url) };
global.DataTransfer = class { constructor() { this.files = []; this.items = { add: file => this.files.push(file) }; } };
state.$refs = { fileInput: {} };
state.addFiles([{ name: 'image.png', type: 'image/png' }, { name: 'clip.mp4', type: 'video/mp4' }]);
assert.deepEqual(state.filePreviews.map(p => p.url), ['blob:image.png', 'blob:clip.mp4']);
state.removeFileAt(0);
assert.deepEqual(state.fileNames, ['clip.mp4']);
assert.deepEqual(revocations, ['blob:image.png']);
state.clearFiles();
assert.deepEqual(revocations, ['blob:image.png', 'blob:clip.mp4']);
assert.equal(state.$refs.fileInput.files.length, 0);
const handler = new Function('$event', '$el', 'Event', 'with(this) {' + input.enter + '}');
let submits = 0;
for (const modifier of ['shiftKey', 'ctrlKey', 'metaKey']) {
    const textarea = { value: 'hello world', selectionStart: 5, selectionEnd: 6,
        setRangeText(text, start, end) { this.value = this.value.slice(0, start) + text + this.value.slice(end); this.selectionStart = this.selectionEnd = start + text.length; },
        dispatchEvent() { state.replyText = this.value; }, closest() { return { requestSubmit() { submits++; } }; } };
    const event = { [modifier]: true, preventDefault() { this.prevented = true; } };
    handler.call(state, event, textarea, class {});
    assert.equal(textarea.value, 'hello\nworld');
    assert.equal(textarea.selectionStart, 6);
    assert.equal(state.replyText, 'hello\nworld');
    assert.equal(event.prevented, true);
}
assert.equal(submits, 0);
const textarea = { closest() { return { requestSubmit() { submits++; } }; } };
handler.call(state, { preventDefault() {} }, textarea, class {});
assert.equal(submits, 1);
handler.call(state, { isComposing: true, preventDefault() { throw Error('IME was interrupted'); } }, textarea, class {});
assert.equal(submits, 1);
console.log('Chat shortcuts and media preview lifecycle passed');
'''
        result = subprocess.run(['node', '-e', program], input=json.dumps({'component': parser.component, 'enter': parser.enter}), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rendered_page_scripts_have_valid_javascript(self):
        if not shutil.which('node'):
            self.skipTest('Node is needed to check rendered JavaScript')
        case = self.case()

        class Parser(HTMLParser):
            def __init__(parser):
                super().__init__()
                parser.scripts = []
                parser.current = None
                parser.components = []
            def handle_starttag(parser, tag, attrs):
                attrs = dict(attrs)
                if tag == 'script' and not attrs.get('src') and attrs.get('type', '').lower() != 'application/json':
                    parser.current = ''
                if 'x-data' in attrs:
                    parser.components.append('return (' + attrs['x-data'] + ');')
            def handle_data(parser, data):
                if parser.current is not None:
                    parser.current += data
            def handle_endtag(parser, tag):
                if tag == 'script' and parser.current is not None:
                    parser.scripts.append(parser.current)
                    parser.current = None

        snippets = []
        for view, path, user, args in [
            (views.case_list, '/desk/cases/', self.staff, []),
            (views.case_kanban, '/desk/cases/kanban/', self.staff, []),
            (views.case_calendar, '/desk/cases/calendar/', self.staff, []),
            (views.case_detail, f'/desk/cases/{case.pk}/', self.staff, [case.pk]),
            (views_chat.my_tickets, '/portal/chat/my-tickets/', self.portal, []),
            (views_chat.chat_room, f'/portal/chat/{case.pk}/', self.portal, [case.pk]),
        ]:
            parser = Parser()
            parser.feed(view(self.request(path, user), *args).content.decode())
            snippets.extend(parser.scripts + parser.components)
        program = "const snippets = JSON.parse(require('node:fs').readFileSync(0,'utf8')); snippets.forEach((code, i) => { try { new Function(code); } catch(e) { throw new Error('Snippet ' + i + ': ' + e.message); } }); console.log(snippets.length + ' JavaScript snippets compiled');"
        result = subprocess.run(['node', '-e', program], input=json.dumps(snippets), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
