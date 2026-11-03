from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

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