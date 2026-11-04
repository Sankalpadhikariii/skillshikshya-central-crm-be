from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase
import csv, io, codecs
from apps.course.models import Course
from apps.lead.models import Lead

User = get_user_model()


class PrimaryCourseNestedTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.course = Course.objects.create(name='Python Advanced', full_amount=30000)
        self.lead = Lead.objects.create(
            full_name='Test Lead', phone='9800000055', primary_course=self.course,
        )

    def test_detail_returns_nested_primary_course(self):
        response = self.client.get(f'/api/leads/{self.lead.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        pc = response.data['primary_course']
        self.assertEqual(pc, {'id': self.course.id, 'name': 'Python Advanced', 'price': '30000.00'})
        self.assertNotIn('primary_course_name', response.data)

    def test_null_primary_course(self):
        lead = Lead.objects.create(full_name='No Course', phone='9800000056')
        response = self.client.get(f'/api/leads/{lead.id}/')
        self.assertIsNone(response.data['primary_course'])

    def test_patch_accepts_nested_object(self):
        other = Course.objects.create(name='Flutter', full_amount=25000)
        response = self.client.patch(
            f'/api/leads/{self.lead.id}/',
            {'primary_course': {'id': other.id}}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['primary_course']['id'], other.id)

    def test_patch_still_accepts_bare_int(self):
        bare = Course.objects.create(name='DevOps', full_amount=None)
        response = self.client.patch(
            f'/api/leads/{self.lead.id}/',
            {'primary_course': bare.id}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['primary_course'], {
            'id': bare.id, 'name': 'DevOps', 'price': None,
        })

    def test_patch_invalid_course_rejected(self):
        response = self.client.patch(
            f'/api/leads/{self.lead.id}/',
            {'primary_course': 99999}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

class LeadSourceDeletionTests(APITestCase):
    def setUp(self):
        from apps.lead.models import LeadSource
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.protected = LeadSource.objects.create(
            name='Protected Source', is_protected=True,
        )
        self.used = LeadSource.objects.create(name='Used Source')
        Lead.objects.create(full_name='Someone', phone='1', source=self.used)
        self.free = LeadSource.objects.create(name='Free Source')

    def test_protected_source_cannot_be_deleted(self):
        response = self.client.delete(f'/api/lead-sources/{self.protected.id}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('protected', response.data['detail'])
        self.assertTrue(
            __import__('apps.lead.models', fromlist=['LeadSource'])
            .LeadSource.objects.filter(pk=self.protected.id).exists()
        )

    def test_used_source_cannot_be_deleted(self):
        response = self.client.delete(f'/api/lead-sources/{self.used.id}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('used in leads', response.data['detail'])

    def test_unused_unprotected_source_deletes(self):
        response = self.client.delete(f'/api/lead-sources/{self.free.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_is_protected_toggle_via_patch(self):
        response = self.client.patch(
            f'/api/lead-sources/{self.free.id}/',
            {'is_protected': True}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_protected'])
        # now deletion must be blocked
        blocked = self.client.delete(f'/api/lead-sources/{self.free.id}/')
        self.assertEqual(blocked.status_code, status.HTTP_400_BAD_REQUEST)

    def test_model_level_delete_raises_protected_error(self):
        from django.db import transaction
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.protected.delete()
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                self.used.delete()


class GlobalCallListTests(APITestCase):
    def setUp(self):
        from datetime import timedelta
        from apps.branch.models import Branch
        from django.utils import timezone as tz
        from apps.lead.models import LeadCall

        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.counselor = User.objects.create_user(
            email='c9@test.com', name='Counselor Nine', password='pass',
            is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.branch = Branch.objects.create(name='Biratnagar', code='BRT')
        self.lead = Lead.objects.create(full_name='Call Person', phone='9811111111')
        from apps.student.models import Student
        self.student = Student.objects.create(
            full_name='Call Person', phone='9811111111', lead=self.lead,
        )
        now = tz.now()
        self.recent_call = LeadCall.objects.create(
            lead=self.lead, call_timestamp=now, direction='outbound',
            caller='Counselor Nine', phone_number='9811111111',
            duration_seconds=270, outcome='interested',
            created_by=self.counselor, branch=self.branch,
        )
        self.old_call = LeadCall.objects.create(
            lead=self.lead, call_timestamp=now - timedelta(days=10),
            direction='inbound', caller='Someone Else',
            phone_number='9822222222', duration_seconds=60,
            created_by=self.admin, branch=self.branch,
        )

    def test_list_returns_all_calls_newest_first(self):
        response = self.client.get('/api/calls/')
        self.assertEqual(response.status_code, 200)
        ids = [r['id'] for r in response.data['results']]
        self.assertEqual(ids, [self.recent_call.id, self.old_call.id])
        row = response.data['results'][0]
        self.assertEqual(row['lead_name'], 'Call Person')
        self.assertTrue(row['lead_reference_number'].startswith('LD-'))

    def test_filter_direction(self):
        response = self.client.get('/api/calls/?direction=inbound')
        self.assertEqual([r['id'] for r in response.data['results']], [self.old_call.id])

    def test_filter_created_by_user(self):
        response = self.client.get(f'/api/calls/?created_by={self.counselor.id}')
        self.assertEqual(
            [r['id'] for r in response.data['results']], [self.recent_call.id],
        )

    def test_filter_by_student(self):
        response = self.client.get(f'/api/calls/?student={self.student.id}')
        self.assertEqual(response.data['count'], 2)

    def test_filter_date_range(self):
        from django.utils import timezone as tz
        today = tz.now().date().isoformat()
        response = self.client.get(f'/api/calls/?call_timestamp__gte={today}')
        self.assertEqual(
            [r['id'] for r in response.data['results']], [self.recent_call.id],
        )

    def test_search(self):
        response = self.client.get('/api/calls/?search=Counselor Nine')
        self.assertEqual(response.data['count'], 1)
        search_lead = self.client.get('/api/calls/?search=Call Person')
        self.assertEqual(search_lead.data['count'], 2)

    def test_branch_scoping_for_non_staff(self):
        from apps.branch.models import Branch
        from apps.lead.models import LeadCall
        other_branch = Branch.objects.create(name='Dharan', code='DHR')
        other_admin = User.objects.create_user(
            email='br@test.com', name='BR User', password='x',
            branch=other_branch,
        )
        LeadCall.objects.filter(id=self.recent_call.id).update(branch=None)
        self.client.force_authenticate(other_admin)
        response = self.client.get('/api/calls/')
        self.assertNotIn(self.recent_call.id, [r['id'] for r in response.data['results']])
        # ---- Paste everything below at the END of apps/lead/tests.py ----------------
from datetime import datetime, timezone as dt_timezone

from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.branch.models import Branch
from apps.lead.models import LeadCall


def utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)


class GlobalCallSummaryTests(APITestCase):
    """US-1: GET /api/calls/summary/."""

    URL = '/api/calls/summary/'

    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.counselor = User.objects.create_user(
            email='c9@test.com', name='Counselor Nine', password='pass',
            is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.lead = Lead.objects.create(full_name='Call Person', phone='9811111111')

    def make_call(self, seconds, direction='outbound', when=None, by=None, branch=None):
        return LeadCall.objects.create(
            lead=self.lead,
            call_timestamp=when or utc(2026, 10, 28, 10, 0),
            direction=direction,
            caller='x',
            phone_number='9811111111',
            duration_seconds=seconds,
            created_by=by,
            branch=branch,
        )

    def test_totals_average_and_connect_rate_include_zero_second_call(self):
        self.make_call(120, 'outbound', by=self.counselor)
        self.make_call(60, 'inbound', by=self.admin)
        self.make_call(0, 'outbound', by=self.counselor)  # not connected

        data = self.client.get(self.URL).data

        self.assertEqual(data['total_calls'], 3)
        self.assertEqual(data['inbound'], 1)
        self.assertEqual(data['outbound'], 2)
        self.assertEqual(data['total_seconds'], 180)
        self.assertEqual(data['total_formatted'], '00:03:00')
        self.assertEqual(data['avg_seconds'], 60)
        self.assertEqual(data['connected_calls'], 2)
        self.assertEqual(data['connect_rate'], 0.667)

    def test_days_are_counted_in_nepal_time(self):
        # 20:00 UTC on Oct 28 is 01:45 NPT on Oct 29.
        self.make_call(100, when=utc(2026, 10, 28, 20, 0))
        # 17:00 UTC on Oct 28 is 22:45 NPT, still Oct 28.
        self.make_call(50, when=utc(2026, 10, 28, 17, 0))

        data = self.client.get(self.URL).data

        self.assertEqual(data['by_day'], [
            {'date': '2026-10-28', 'calls': 1, 'seconds': 50},
            {'date': '2026-10-29', 'calls': 1, 'seconds': 100},
        ])

    def test_empty_result_returns_zeros(self):
        data = self.client.get(self.URL).data

        self.assertEqual(data['total_calls'], 0)
        self.assertEqual(data['inbound'], 0)
        self.assertEqual(data['outbound'], 0)
        self.assertEqual(data['total_seconds'], 0)
        self.assertEqual(data['total_formatted'], '00:00:00')
        self.assertEqual(data['avg_seconds'], 0)
        self.assertEqual(data['connected_calls'], 0)
        self.assertEqual(data['connect_rate'], 0)
        self.assertEqual(data['by_day'], [])
        self.assertEqual(data['by_user'], [])

    def test_summary_follows_the_same_filters_as_the_list(self):
        self.make_call(120, 'outbound', by=self.counselor)
        self.make_call(60, 'inbound', by=self.admin)
        self.make_call(0, 'outbound', by=self.counselor)

        data = self.client.get(self.URL + '?direction=outbound').data
        listed = self.client.get('/api/calls/?direction=outbound').data

        self.assertEqual(data['total_calls'], 2)
        self.assertEqual(data['total_calls'], listed['count'])
        self.assertEqual(data['inbound'], 0)
        self.assertEqual(data['total_seconds'], 120)
        self.assertEqual(data['connected_calls'], 1)
        self.assertEqual(data['connect_rate'], 0.5)

    def test_by_user_ranks_callers_and_labels_missing_creator_unknown(self):
        self.make_call(30, by=self.counselor)
        self.make_call(40, by=self.counselor)
        self.make_call(10, by=None)

        by_user = self.client.get(self.URL).data['by_user']

        self.assertEqual(by_user[0], {
            'user_id': self.counselor.id, 'name': 'Counselor Nine',
            'calls': 2, 'seconds': 70,
        })
        self.assertEqual(by_user[1], {
            'user_id': None, 'name': 'Unknown', 'calls': 1, 'seconds': 10,
        })

    def test_query_count_does_not_grow_with_rows(self):
        self.make_call(60, by=self.counselor)
        with CaptureQueriesContext(connection) as few:
            self.client.get(self.URL)

        LeadCall.objects.bulk_create([
            LeadCall(
                lead=self.lead, call_timestamp=utc(2026, 10, 28, 10, 0),
                direction='outbound', duration_seconds=10 + i,
                created_by=self.counselor,
            )
            for i in range(50)
        ])
        with CaptureQueriesContext(connection) as many:
            self.client.get(self.URL)

        self.assertEqual(len(few), len(many))


class GlobalCallBranchScopingTests(APITestCase):
    """US-6: a non-staff user only sees their own branch's calls."""

    def setUp(self):
        self.branch_x = Branch.objects.create(name='Biratnagar', code='BRT')
        self.branch_y = Branch.objects.create(name='Dharan', code='DHR')
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.user_x = User.objects.create_user(
            email='x@test.com', name='User X', password='pass',
            branch=self.branch_x,
        )
        self.lead = Lead.objects.create(full_name='Call Person', phone='9811111111')
        self.call_x = self.make_call(100, self.branch_x)
        self.call_y1 = self.make_call(200, self.branch_y)
        self.call_y2 = self.make_call(300, self.branch_y)

    def make_call(self, seconds, branch):
        return LeadCall.objects.create(
            lead=self.lead, call_timestamp=utc(2026, 10, 28, 10, 0),
            direction='outbound', caller='x', phone_number='9811111111',
            duration_seconds=seconds, created_by=self.admin, branch=branch,
        )

    def test_list_only_shows_own_branch(self):
        self.client.force_authenticate(self.user_x)
        response = self.client.get('/api/calls/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['id'] for r in response.data['results']], [self.call_x.id])

    def test_summary_only_counts_own_branch(self):
        self.client.force_authenticate(self.user_x)
        response = self.client.get('/api/calls/summary/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 1)
        self.assertEqual(response.data['total_seconds'], 100)

    def test_non_staff_cannot_widen_scope_with_branch_id(self):
        self.client.force_authenticate(self.user_x)
        response = self.client.get(f'/api/calls/summary/?branch_id={self.branch_y.id}')
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.data['total_calls'], 2)
        self.assertEqual(response.data['total_seconds'], 100)

    def test_staff_sees_all_branches_by_default(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get('/api/calls/').data['count'], 3)
        self.assertEqual(self.client.get('/api/calls/summary/').data['total_calls'], 3)

    def test_staff_can_narrow_to_one_branch(self):
        self.client.force_authenticate(self.admin)
        listed = self.client.get(f'/api/calls/?branch_id={self.branch_y.id}')
        summary = self.client.get(f'/api/calls/summary/?branch_id={self.branch_y.id}')
        self.assertEqual(listed.data['count'], 2)
        self.assertEqual(summary.data['total_calls'], 2)
        self.assertEqual(summary.data['total_seconds'], 500)
        


import csv
import io

EXPORT_URL = '/api/calls/export/'
EXPORT_HEADER = [
    'Date & time (NPT)', 'Direction', 'Lead', 'Reference', 'Phone', 'Caller',
    'Logged by', 'Duration (MM:SS)', 'Seconds', 'Outcome', 'Notes', 'Recording',
]


def read_export(client, url=EXPORT_URL):
    """Fetch the streamed CSV and return (response, raw bytes, parsed rows)."""
    response = client.get(url)
    raw = b''.join(response.streaming_content)
    text = raw.decode('utf-8').lstrip('\ufeff')
    return response, raw, list(csv.reader(io.StringIO(text)))


class GlobalCallExportTests(APITestCase):
    """US-4: GET /api/calls/export/."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.lead = Lead.objects.create(full_name='Sita Kumari', phone='9811111111')

    def make_call(self, seconds=60, direction='outbound', notes=None, when=None, **extra):
        return LeadCall.objects.create(
            lead=extra.pop('lead', self.lead),
            call_timestamp=when or utc(2026, 10, 28, 10, 0),
            direction=direction, caller='Counselor', phone_number='9811111111',
            duration_seconds=seconds, notes=notes, created_by=self.admin, **extra,
        )

    def test_content_type_and_filename(self):
        self.make_call()
        response, _, _ = read_export(self.client)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/csv'))
        self.assertRegex(
            response['Content-Disposition'], r'attachment; filename="calls-\d{4}-\d{2}-\d{2}\.csv"',
        )

    def test_starts_with_utf8_bom_and_has_the_agreed_header(self):
        self.make_call()
        _, raw, rows = read_export(self.client)
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(rows[0], EXPORT_HEADER)

    def test_exports_every_matching_call_not_just_one_page(self):
        LeadCall.objects.bulk_create([
            LeadCall(
                lead=self.lead, call_timestamp=utc(2026, 10, 28, 10, 0),
                direction='outbound', caller='x', phone_number='9811111111',
                duration_seconds=i, created_by=self.admin,
            )
            for i in range(25)
        ])
        listed = self.client.get('/api/calls/')
        self.assertEqual(len(listed.data['results']), 10)  # default page size
        _, _, rows = read_export(self.client)
        self.assertEqual(len(rows) - 1, 25)
        self.assertEqual(len(rows) - 1, listed.data['count'])

    def test_respects_filters(self):
        self.make_call(60, 'outbound')
        self.make_call(90, 'inbound')
        _, _, rows = read_export(self.client, EXPORT_URL + '?direction=inbound')
        self.assertEqual(len(rows) - 1, 1)
        self.assertEqual(rows[1][1], 'Inbound')
        self.assertEqual(rows[1][8], '90')

    def test_row_values_use_nepal_time_and_formatted_duration(self):
        # 20:00 UTC on Oct 28 is 01:45 NPT on Oct 29.
        self.make_call(259, when=utc(2026, 10, 28, 20, 0), outcome='interested')
        _, _, rows = read_export(self.client)
        row = rows[1]
        self.assertEqual(row[0], '2026-10-29 01:45:00')
        self.assertEqual(row[2], 'Sita Kumari')
        self.assertTrue(row[3].startswith('LD-'))
        self.assertEqual(row[6], 'Admin')
        self.assertEqual(row[7], '04:19')
        self.assertEqual(row[8], '259')
        self.assertEqual(row[9], 'interested')

    def test_devanagari_names_survive_the_round_trip(self):
        lead = Lead.objects.create(full_name='सीता कुमारी', phone='9822222222')
        self.make_call(30, lead=lead, notes='नमस्ते')
        _, raw, rows = read_export(self.client)
        self.assertIn('सीता कुमारी'.encode('utf-8'), raw)
        self.assertEqual(rows[1][2], 'सीता कुमारी')
        self.assertEqual(rows[1][10], 'नमस्ते')

    def test_notes_that_look_like_formulas_are_neutralised(self):
        formulas = ['=HYPERLINK("http://evil.example","x")', '+1+1', '-5', '@SUM(A1)']
        for i, text in enumerate(formulas):
            self.make_call(10 + i, notes=text)
        _, _, rows = read_export(self.client)
        notes = sorted(r[10] for r in rows[1:])
        self.assertEqual(notes, sorted("'" + t for t in formulas))
        for cell in notes:
            self.assertFalse(cell.startswith(('=', '+', '-', '@')))

    def test_query_count_does_not_grow_with_rows(self):
        self.make_call(60)
        with CaptureQueriesContext(connection) as few:
            read_export(self.client)

        LeadCall.objects.bulk_create([
            LeadCall(
                lead=self.lead, call_timestamp=utc(2026, 10, 28, 10, 0),
                direction='outbound', caller='x', phone_number='9811111111',
                duration_seconds=10 + i, created_by=self.admin,
            )
            for i in range(200)
        ])
        with CaptureQueriesContext(connection) as many:
            read_export(self.client)

        # The queries run while the stream is consumed, so both reads are inside the context.
        self.assertEqual(len(few), len(many))


class GlobalCallExportScopingTests(APITestCase):
    """US-6: the export respects branch scoping like the list and summary."""

    def setUp(self):
        self.branch_x = Branch.objects.create(name='Biratnagar', code='BRT')
        self.branch_y = Branch.objects.create(name='Dharan', code='DHR')
        self.admin = User.objects.create_user(
            email='admin@test.com', name='Admin', password='pass', is_staff=True,
        )
        self.user_x = User.objects.create_user(
            email='x@test.com', name='User X', password='pass', branch=self.branch_x,
        )
        self.lead = Lead.objects.create(full_name='Call Person', phone='9811111111')
        for seconds, branch in [(100, self.branch_x), (200, self.branch_y), (300, self.branch_y)]:
            LeadCall.objects.create(
                lead=self.lead, call_timestamp=utc(2026, 10, 28, 10, 0),
                direction='outbound', caller='x', phone_number='9811111111',
                duration_seconds=seconds, created_by=self.admin, branch=branch,
            )

    def test_non_staff_only_exports_own_branch(self):
        self.client.force_authenticate(self.user_x)
        response, _, rows = read_export(self.client)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r[8] for r in rows[1:]], ['100'])

    def test_non_staff_cannot_widen_export_with_branch_id(self):
        self.client.force_authenticate(self.user_x)
        _, _, rows = read_export(self.client, f'{EXPORT_URL}?branch_id={self.branch_y.id}')
        self.assertEqual([r[8] for r in rows[1:]], ['100'])

    def test_staff_exports_everything_or_one_branch(self):
        self.client.force_authenticate(self.admin)
        _, _, everything = read_export(self.client)
        _, _, only_y = read_export(self.client, f'{EXPORT_URL}?branch_id={self.branch_y.id}')
        self.assertEqual(len(everything) - 1, 3)
        self.assertEqual(sorted(r[8] for r in only_y[1:]), ['200', '300'])


class GlobalCallNoBranchUserTests(APITestCase):
    """US-6 write-up: what a non-staff user with NO branch sees today.

    BranchFilterBackend only filters when the user has a branch, so a branch-less
    non-staff user is not filtered at all and sees every branch. These tests pin
    that current behaviour on purpose. The proposed fix (return queryset.none()
    for such users) is described in the PR write-up and is not part of this change.
    """

    def setUp(self):
        self.branch_x = Branch.objects.create(name='Biratnagar', code='BRT')
        self.branch_y = Branch.objects.create(name='Dharan', code='DHR')
        self.no_branch_user = User.objects.create_user(
            email='nb@test.com', name='No Branch', password='pass',
        )
        self.lead = Lead.objects.create(full_name='Call Person', phone='9811111111')
        for seconds, branch in [(100, self.branch_x), (200, self.branch_y), (300, None)]:
            LeadCall.objects.create(
                lead=self.lead, call_timestamp=utc(2026, 10, 28, 10, 0),
                direction='outbound', caller='x', phone_number='9811111111',
                duration_seconds=seconds, created_by=self.no_branch_user, branch=branch,
            )
        self.client.force_authenticate(self.no_branch_user)

    def test_list_is_not_filtered(self):
        response = self.client.get('/api/calls/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 3)

    def test_summary_is_not_filtered(self):
        response = self.client.get('/api/calls/summary/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_calls'], 3)

    def test_export_is_not_filtered(self):
        response, _, rows = read_export(self.client)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(rows) - 1, 3)