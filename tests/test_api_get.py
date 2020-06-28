#!/usr/bin/env python
import copy
import json
import uuid
from itertools import chain
from urllib.parse import quote_plus as url_quote

from app import db
from app.culling import Timestamps
from app.queue.queue import handle_message
from app.serialization import serialize_host
from app.utils import HostWrapper
from lib.host_repository import canonical_fact_host_query
from tests.helpers.api_utils import api_pagination_invalid_parameters_test
from tests.helpers.api_utils import api_pagination_test
from tests.helpers.api_utils import api_query_test
from tests.helpers.api_utils import assert_host_ids_in_response
from tests.helpers.api_utils import assert_response_status
from tests.helpers.api_utils import build_expected_host_list
from tests.helpers.api_utils import build_host_id_list_for_url
from tests.helpers.api_utils import build_hosts_url
from tests.helpers.api_utils import build_order_query_parameters
from tests.helpers.api_utils import build_system_profile_url
from tests.helpers.api_utils import HOST_URL
from tests.helpers.api_utils import inject_qs
from tests.helpers.api_utils import quote_everything
from tests.helpers.api_utils import UUID_1
from tests.helpers.api_utils import UUID_2
from tests.helpers.api_utils import UUID_3
from tests.helpers.mq_utils import MockEventProducer
from tests.helpers.test_utils import ACCOUNT
from tests.helpers.test_utils import generate_uuid
from tests.helpers.test_utils import minimal_host
from tests.helpers.test_utils import now
from tests.test_api_utils import PaginationBaseTestCase
from tests.test_api_utils import PreCreatedHostsBaseTestCase


def test_query_all(mq_create_three_specific_hosts, api_get, subtests):
    created_hosts = mq_create_three_specific_hosts
    expected_host_list = build_expected_host_list(created_hosts)

    response_status, response_data = api_get(HOST_URL)

    assert response_status == 200
    assert expected_host_list == response_data["results"]

    api_pagination_test(api_get, subtests, HOST_URL, expected_total=len(created_hosts))
    api_pagination_invalid_parameters_test(api_get, subtests, HOST_URL)


def test_query_using_display_name(mq_create_three_specific_hosts, api_get):
    created_hosts = mq_create_three_specific_hosts
    expected_host_list = build_expected_host_list([created_hosts[0]])

    response_status, response_data = api_get(
        HOST_URL, query_parameters={"display_name": created_hosts[0].display_name}
    )

    assert response_status == 200
    assert len(response_data["results"]) == 1
    assert expected_host_list == response_data["results"]


def test_query_using_fqdn_two_results(mq_create_three_specific_hosts, api_get):
    created_hosts = mq_create_three_specific_hosts
    expected_host_list = build_expected_host_list([created_hosts[0], created_hosts[1]])

    response_status, response_data = api_get(HOST_URL, query_parameters={"fqdn": created_hosts[0].fqdn})

    assert response_status == 200
    assert len(response_data["results"]) == 2
    assert expected_host_list == response_data["results"]


def test_query_using_fqdn_one_result(mq_create_three_specific_hosts, api_get):
    created_hosts = mq_create_three_specific_hosts
    expected_host_list = build_expected_host_list([created_hosts[2]])

    response_status, response_data = api_get(HOST_URL, query_parameters={"fqdn": created_hosts[2].fqdn})

    assert response_status == 200
    assert len(response_data["results"]) == 1
    assert expected_host_list == response_data["results"]


def test_query_using_non_existent_fqdn(api_get):
    response_status, response_data = api_get(HOST_URL, query_parameters={"fqdn": "ROFLSAUCE.com"})

    assert response_status == 200
    assert len(response_data["results"]) == 0


def test_query_using_display_name_substring(mq_create_three_specific_hosts, api_get, subtests):
    created_hosts = mq_create_three_specific_hosts
    expected_host_list = build_expected_host_list(created_hosts)

    host_name_substr = created_hosts[0].display_name[:4]

    url = f"{HOST_URL}?display_name={host_name_substr}"

    response_status, response_data = api_get(url)

    assert response_status == 200
    assert expected_host_list == response_data["results"]

    api_pagination_test(api_get, subtests, url, expected_total=len(created_hosts))
    api_pagination_invalid_parameters_test(api_get, subtests, url)


def test_query_existent_hosts(mq_create_three_specific_hosts, api_get, subtests):
    created_hosts = mq_create_three_specific_hosts
    host_lists = [created_hosts[0:1], created_hosts[1:3], created_hosts]

    for host_list in host_lists:
        with subtests.test(host_list=host_list):
            host_id_list = build_host_id_list_for_url(host_list)
            api_query_test(api_get, subtests, host_id_list, host_list)


def test_query_single_non_existent_host(api_get, subtests):
    api_query_test(api_get, subtests, generate_uuid(), [])


def test_query_multiple_hosts_with_some_non_existent(mq_create_three_specific_hosts, api_get, subtests):
    created_hosts = mq_create_three_specific_hosts
    host_list = created_hosts[0:1]

    existent_host_id_list = build_host_id_list_for_url(host_list)
    non_existent_host_id = generate_uuid()

    host_id_list = f"{non_existent_host_id},{existent_host_id_list}"

    api_query_test(api_get, subtests, host_id_list, host_list)


def test_query_invalid_host_id(mq_create_three_specific_hosts, api_get, subtests):
    created_hosts = mq_create_three_specific_hosts
    bad_id_list = ["notauuid", "1234blahblahinvalid"]
    only_bad_id = bad_id_list.copy()

    # Can’t have empty string as an only ID, that results in 404 Not Found.
    more_bad_id_list = bad_id_list + [""]
    valid_id = created_hosts[0].id
    with_bad_id = [f"{valid_id},{bad_id}" for bad_id in more_bad_id_list]

    for host_id_list in chain(only_bad_id, with_bad_id):
        with subtests.test(host_id_list=host_id_list):
            response_status, response_data = api_get(f"{HOST_URL}/{host_id_list}")
            assert response_status == 400


def test_query_host_id_without_hyphens(mq_create_three_specific_hosts, api_get, subtests):
    created_hosts = mq_create_three_specific_hosts
    host_lists = [created_hosts[0:1], created_hosts]

    for original_host_list in host_lists:
        with subtests.test(host_list=original_host_list):
            # deepcopy host.__data to insulate original_host_list from changes.
            host_data = (host.data() for host in original_host_list)
            host_data = (copy.deepcopy(host) for host in host_data)
            query_host_list = [HostWrapper(host) for host in host_data]

            # Remove the hyphens from one of the valid hosts.
            query_host_list[0].id = uuid.UUID(query_host_list[0].id, version=4).hex

            host_id_list = build_host_id_list_for_url(query_host_list)
            api_query_test(api_get, subtests, host_id_list, original_host_list)


def test_query_with_branch_id_parameter(mq_create_three_specific_hosts, api_get, subtests):
    created_hosts = mq_create_three_specific_hosts
    url_host_id_list = build_host_id_list_for_url(created_hosts)
    # branch_id parameter is accepted, but doesn’t affect results.
    api_query_test(api_get, subtests, f"{url_host_id_list}?branch_id=123", created_hosts)


def test_query_invalid_paging_parameters(mq_create_three_specific_hosts, api_get, subtests):
    created_hosts = mq_create_three_specific_hosts
    url = build_hosts_url(created_hosts)

    api_pagination_invalid_parameters_test(api_get, subtests, url)


def test_query_using_display_name_as_hostname(mq_create_three_specific_hosts, api_get, subtests):
    created_hosts = mq_create_three_specific_hosts

    url = f"{HOST_URL}?hostname_or_id={created_hosts[0].display_name}"

    response_status, response_data = api_get(url)

    assert response_status == 200
    assert len(response_data["results"]) == 2

    api_pagination_test(api_get, subtests, url, expected_total=2)
    api_pagination_invalid_parameters_test(api_get, subtests, url)


def test_query_using_fqdn_as_hostname(mq_create_three_specific_hosts, api_get, subtests):
    created_hosts = mq_create_three_specific_hosts

    url = f"{HOST_URL}?hostname_or_id={created_hosts[2].display_name}"

    response_status, response_data = api_get(url)

    assert response_status == 200
    assert len(response_data["results"]) == 1

    api_pagination_test(api_get, subtests, url, expected_total=1)
    api_pagination_invalid_parameters_test(api_get, subtests, url)


def test_query_using_id(mq_create_three_specific_hosts, api_get, subtests):
    created_hosts = mq_create_three_specific_hosts

    url = f"{HOST_URL}?hostname_or_id={created_hosts[0].id}"

    response_status, response_data = api_get(url)

    assert response_status == 200
    assert len(response_data["results"]) == 1

    api_pagination_test(api_get, subtests, url, expected_total=1)
    api_pagination_invalid_parameters_test(api_get, subtests, url)


def test_query_using_non_existent_hostname(mq_create_three_specific_hosts, api_get, subtests):
    url = f"{HOST_URL}?hostname_or_id=NotGonnaFindMe"

    response_status, response_data = api_get(url)

    assert response_status == 200
    assert len(response_data["results"]) == 0

    api_pagination_test(api_get, subtests, url, expected_total=0)
    api_pagination_invalid_parameters_test(api_get, subtests, url)


def test_query_using_non_existent_id(mq_create_three_specific_hosts, api_get, subtests):
    url = f"{HOST_URL}?hostname_or_id={generate_uuid()}"

    response_status, response_data = api_get(url)

    assert response_status == 200
    assert len(response_data["results"]) == 0

    api_pagination_test(api_get, subtests, url, expected_total=0)
    api_pagination_invalid_parameters_test(api_get, subtests, url)


def test_query_with_matching_insights_id(mq_create_three_specific_hosts, api_get, subtests):
    created_hosts = mq_create_three_specific_hosts

    url = f"{HOST_URL}?insights_id={created_hosts[0].insights_id}"

    response_status, response_data = api_get(url)

    assert response_status == 200
    assert len(response_data["results"]) == 1

    api_pagination_test(api_get, subtests, url, expected_total=1)
    api_pagination_invalid_parameters_test(api_get, subtests, url)


def test_query_with_no_matching_insights_id(mq_create_three_specific_hosts, api_get, subtests):
    url = f"{HOST_URL}?insights_id={generate_uuid()}"

    response_status, response_data = api_get(url)

    assert response_status == 200
    assert len(response_data["results"]) == 0

    api_pagination_test(api_get, subtests, url, expected_total=0)
    api_pagination_invalid_parameters_test(api_get, subtests, url)


def test_query_with_invalid_insights_id(mq_create_three_specific_hosts, api_get, subtests):
    response_status, response_data = api_get(f"{HOST_URL}?insights_id=notauuid")

    assert response_status == 400


def test_query_with_matching_insights_id_and_branch_id(mq_create_three_specific_hosts, api_get, subtests):
    created_hosts = mq_create_three_specific_hosts
    valid_insights_id = created_hosts[0].insights_id

    response_status, response_data = api_get(f"{HOST_URL}?insights_id={valid_insights_id}&branch_id=123")

    assert response_status == 200


def test_query_using_fqdn_not_subset_match(mocker, api_get):
    mock = mocker.patch("api.host_query_db.canonical_fact_host_query", wraps=canonical_fact_host_query)

    fqdn = "some fqdn"
    api_get(f"{HOST_URL}?fqdn={fqdn}")

    mock.assert_called_once_with(ACCOUNT, "fqdn", fqdn)


def test_query_using_insights_id_not_subset_match(mocker, api_get):
    mock = mocker.patch("api.host_query_db.canonical_fact_host_query", wraps=canonical_fact_host_query)

    insights_id = "ff13a346-19cb-42ae-9631-44c42927fb92"
    api_get(f"{HOST_URL}?insights_id={insights_id}")

    mock.assert_called_once_with(ACCOUNT, "insights_id", insights_id)


# class QueryByTagTestCase(PreCreatedHostsBaseTestCase, PaginationBaseTestCase):
#     def _compare_responses(self, expected_response_list, response_list, test_url):
#         self.assertEqual(len(expected_response_list), len(response_list["results"]))
#         for host, result in zip(expected_response_list, response_list["results"]):
#             self.assertEqual(host.id, result["id"])
#         self._base_paging_test(test_url, len(expected_response_list))
#
#     def test_get_host_by_tag(self):
#         """
#         Get only the one host with the special tag to find on it.
#         """
#         host_list = self.added_hosts.copy()
#
#         expected_response_list = [host_list[0]]  # host with tag SPECIAL/tag=ToFind
#
#         test_url = f"{HOST_URL}?tags=SPECIAL/tag=ToFind"
#         response_list = self.get(test_url, 200)
#
#         self._compare_responses(expected_response_list, response_list, test_url)
#
#     def test_get_multiple_hosts_by_tag(self):
#         """
#         Get only the one host with the special tag to find on it.
#         """
#         host_list = self.added_hosts.copy()
#
#         expected_response_list = [host_list[0], host_list[1]]  # hosts with tag "NS1/key1=val1"
#
#         test_url = f"{HOST_URL}?tags=NS1/key1=val1&order_by=updated&order_how=ASC"
#         response_list = self.get(test_url, 200)
#
#         self._compare_responses(expected_response_list, response_list, test_url)
#
#     def test_get_host_by_multiple_tags(self):
#         """
#         Get only the host with all three tags on it and not the other host
#         which both have some, but not all of the tags we query for.
#         """
#         host_list = self.added_hosts.copy()
#
#         expected_response_list = [host_list[1]]
#         # host with tags ["NS1/key1=val1", "NS2/key2=val2", "NS3/key3=val3"]
#
#         test_url = f"{HOST_URL}?tags=NS1/key1=val1,NS2/key2=val2,NS3/key3=val3"
#         response_list = self.get(test_url, 200)
#
#         self._compare_responses(expected_response_list, response_list, test_url)
#
#     def test_get_host_by_subset_of_tags(self):
#         """
#         Get a host using a subset of it's tags
#         """
#         host_list = self.added_hosts.copy()
#
#         expected_response_list = [host_list[1]]
#         # host with tags ["NS1/key1=val1", "NS2/key2=val2", "NS3/key3=val3"]
#
#         test_url = f"{HOST_URL}?tags=NS1/key1=val1,NS3/key3=val3"
#         response_list = self.get(test_url, 200)
#
#         self._compare_responses(expected_response_list, response_list, test_url)
#
#     def test_get_host_with_different_tags_same_namespace(self):
#         """
#         get a host with two tags in the same namespace with diffent key and same value
#         """
#         host_list = self.added_hosts.copy()
#
#         expected_response_list = [host_list[0]]  # host with tags ["NS1/key1=val1", "NS1/key2=val1"]
#
#         test_url = f"{HOST_URL}?tags=NS1/key1=val1,NS1/key2=val1"
#         response_list = self.get(test_url, 200)
#
#         self._compare_responses(expected_response_list, response_list, test_url)
#
#     def test_get_no_host_with_different_tags_same_namespace(self):
#         """
#         Don’t get a host with two tags in the same namespace, from which only one match. This is a
#         regression test.
#         """
#         test_url = f"{HOST_URL}?tags=NS1/key1=val2,NS1/key2=val1"
#         response_list = self.get(test_url, 200)
#
#         # self.added_hosts[0] would have been matched by NS1/key2=val1, this must not happen.
#         self.assertEqual(0, len(response_list["results"]))
#
#     def test_get_host_with_same_tags_different_namespaces(self):
#         """
#         get a host with two tags in the same namespace with diffent key and same value
#         """
#         host_list = self.added_hosts.copy()
#
#         expected_response_list = [host_list[2]]  # host with tags ["NS3/key3=val3", "NS1/key3=val3"]
#
#         test_url = f"{HOST_URL}?tags=NS3/key3=val3,NS1/key3=val3"
#         response_list = self.get(test_url, 200)
#
#         self._compare_responses(expected_response_list, response_list, test_url)
#
#     def test_get_host_with_tag_no_value_at_all(self):
#         """
#         Attempt to find host with a tag with no stored value
#         """
#         host_list = self.added_hosts.copy()
#
#         expected_response_list = [host_list[0]]  # host with tag "no/key"
#
#         test_url = f"{HOST_URL}?tags=no/key"
#         response_list = self.get(test_url, 200)
#
#         self._compare_responses(expected_response_list, response_list, test_url)
#
#     def test_get_host_with_tag_no_value_in_query(self):
#         """
#         Attempt to find host with a tag with a stored value by a value-less query
#         """
#         host_list = self.added_hosts.copy()
#
#         expected_response_list = [host_list[0]]  # host with tag "no/key"
#
#         test_url = f"{HOST_URL}?tags=NS1/key2"
#         response_list = self.get(test_url, 200)
#
#         self._compare_responses(expected_response_list, response_list, test_url)
#
#     def test_get_host_with_tag_no_namespace(self):
#         """
#         Attempt to find host with a tag with no namespace.
#         """
#         host_list = self.added_hosts.copy()
#
#         expected_response_list = [host_list[2]]  # host with tag "key4=val4"
#         test_url = f"{HOST_URL}?tags=key4=val4"
#         response_list = self.get(test_url, 200)
#
#         self._compare_responses(expected_response_list, response_list, test_url)
#
#     def test_get_host_with_tag_only_key(self):
#         """
#         Attempt to find host with a tag with no namespace.
#         """
#         host_list = self.added_hosts.copy()
#
#         expected_response_list = [host_list[2]]  # host with tag "key5"
#         test_url = f"{HOST_URL}?tags=key5"
#         response_list = self.get(test_url, 200)
#
#         self._compare_responses(expected_response_list, response_list, test_url)
#
#     def test_get_host_with_invalid_tag_no_key(self):
#         """
#         Attempt to find host with an incomplete tag (no key).
#         Expects 400 response.
#         """
#         test_url = f"{HOST_URL}?tags=namespace/=Value"
#         self.get(test_url, 400)
#
#     def test_get_host_by_display_name_and_tag(self):
#         """
#         Attempt to get only the host with the specified key and
#         the specified display name
#         """
#
#         host_list = self.added_hosts.copy()
#
#         expected_response_list = [host_list[0]]
#         # host with tag NS1/key1=val1 and host_name "host1"
#
#         test_url = f"{HOST_URL}?tags=NS1/key1=val1&display_name=host1"
#         response_list = self.get(test_url, 200)
#
#         self._compare_responses(expected_response_list, response_list, test_url)
#
#     def test_get_host_by_display_name_and_tag_backwards(self):
#         """
#         Attempt to get only the host with the specified key and
#         the specified display name, but the parameters are backwards
#         """
#
#         host_list = self.added_hosts.copy()
#
#         expected_response_list = [host_list[0]]
#         # host with tag NS1/key1=val1 and host_name "host1"
#
#         test_url = f"{HOST_URL}?display_name=host1&tags=NS1/key1=val1"
#         response_list = self.get(test_url, 200)
#
#         self._compare_responses(expected_response_list, response_list, test_url)
#
#     def test_get_host_tag_part_too_long(self):
#         """
#         send a request to find hosts with a string tag where the length
#         of the namespace excedes the 255 character limit
#         """
#         too_long = "a" * 256
#
#         for tags_query, part_name in (
#             (f"{too_long}/key=val", "namespace"),
#             (f"namespace/{too_long}=val", "key"),
#             (f"namespace/key={too_long}", "value"),
#         ):
#             with self.subTest(part=part_name):
#                 response = self.get(f"{HOST_URL}?tags={tags_query}", 400)
#                 assert part_name in str(response)
#
#     def test_get_host_with_unescaped_special_characters(self):
#         host_wrapper = HostWrapper(
#             {
#                 "account": ACCOUNT,
#                 "insights_id": generate_uuid(),
#                 "stale_timestamp": now().isoformat(),
#                 "reporter": "test",
#                 "tags": [
#                     {"namespace": ";?:@&+$", "key": "-_.!~*'()'", "value": "#"},
#                     {"namespace": " \t\n\r\f\v", "key": " \t\n\r\f\v", "value": " \t\n\r\f\v"},
#                 ],
#             }
#         )
#         message = {"operation": "add_host", "data": host_wrapper.data()}
#
#         with self.app.app_context():
#             mock_event_producer = MockEventProducer()
#             handle_message(json.dumps(message), mock_event_producer)
#             response_data = json.loads(mock_event_producer.event)
#             created_host = response_data["host"]
#
#         for tags_query in (";?:@&+$/-_.!~*'()'=#", " \t\n\r\f\v/ \t\n\r\f\v= \t\n\r\f\v"):
#             with self.subTest(tags_query=tags_query):
#                 get_response = self.get(f"{HOST_URL}?tags={url_quote(tags_query)}", 200)
#
#                 self.assertEqual(get_response["count"], 1)
#                 self.assertEqual(get_response["results"][0]["id"], created_host["id"])
#
#     def test_get_host_with_escaped_special_characters(self):
#         host_wrapper = HostWrapper(
#             {
#                 "account": ACCOUNT,
#                 "insights_id": generate_uuid(),
#                 "stale_timestamp": now().isoformat(),
#                 "reporter": "test",
#                 "tags": [
#                     {"namespace": ";,/?:@&=+$", "key": "-_.!~*'()", "value": "#"},
#                     {"namespace": " \t\n\r\f\v", "key": " \t\n\r\f\v", "value": " \t\n\r\f\v"},
#                 ],
#             }
#         )
#         message = {"operation": "add_host", "data": host_wrapper.data()}
#
#         with self.app.app_context():
#             mock_event_producer = MockEventProducer()
#             handle_message(json.dumps(message), mock_event_producer)
#             response_data = json.loads(mock_event_producer.event)
#             created_host = response_data["host"]
#
#         for namespace, key, value in ((";,/?:@&=+$", "-_.!~*'()", "#"), (" \t\n\r\f\v", " \t\n\r\f\v", " \t\n\r\f\v")):
#             with self.subTest(namespace=namespace, key=key, value=value):
#                 tags_query = url_quote(
#                     f"{quote_everything(namespace)}/{quote_everything(key)}={quote_everything(value)}"
#                 )
#                 get_response = self.get(f"{HOST_URL}?tags={tags_query}", 200)
#
#                 self.assertEqual(get_response["count"], 1)
#                 self.assertEqual(get_response["results"][0]["id"], created_host["id"])
#
#
# class QueryOrderBaseTestCase(PreCreatedHostsBaseTestCase):
#     def _queries_subtests_with_added_hosts(self):
#         host_id_list = [host.id for host in self.added_hosts]
#         url_host_id_list = ",".join(host_id_list)
#         urls = (HOST_URL, f"{HOST_URL}/{url_host_id_list}", f"{HOST_URL}/{url_host_id_list}/system_profile")
#         for url in urls:
#             with self.subTest(url=url):
#                 yield url
#
#     def _get(self, base_url, order_by=None, order_how=None, status=200):
#         kwargs = {}
#         if order_by:
#             kwargs["order_by"] = order_by
#         if order_how:
#             kwargs["order_how"] = order_how
#
#         full_url = inject_qs(base_url, **kwargs)
#         return self.get(full_url, status)
#
#
# class QueryOrderWithAdditionalHostsBaseTestCase(QueryOrderBaseTestCase):
#     def setUp(self):
#         super().setUp()
#         host_wrapper = HostWrapper()
#         host_wrapper.account = ACCOUNT
#         host_wrapper.display_name = "host1"  # Same as self.added_hosts[0]
#         host_wrapper.insights_id = generate_uuid()
#         host_wrapper.stale_timestamp = now().isoformat()
#         host_wrapper.reporter = "test"
#         response_data = self.post(HOST_URL, [host_wrapper.data()], 207)
#         self.added_hosts.append(HostWrapper(response_data["data"][0]["host"]))
#
#     def _assert_host_ids_in_response(self, response, expected_hosts):
#         response_ids = [host["id"] for host in response["results"]]
#         expected_ids = [host.id for host in expected_hosts]
#         self.assertEqual(response_ids, expected_ids)
#
#
# class QueryOrderTestCase(QueryOrderWithAdditionalHostsBaseTestCase):
#     def _added_hosts_by_updated_desc(self):
#         expected_hosts = self.added_hosts.copy()
#         expected_hosts.reverse()
#         return expected_hosts
#
#     def _added_hosts_by_updated_asc(self):
#         return self.added_hosts
#
#     def _added_hosts_by_display_name_asc(self):
#         return (
#             # Hosts with same display_name are ordered by updated descending
#             self.added_hosts[3],
#             self.added_hosts[0],
#             self.added_hosts[1],
#             self.added_hosts[2],
#         )
#
#     def _added_hosts_by_display_name_desc(self):
#         return (
#             self.added_hosts[2],
#             self.added_hosts[1],
#             # Hosts with same display_name are ordered by updated descending
#             self.added_hosts[3],
#             self.added_hosts[0],
#         )
#
#     def tests_hosts_are_ordered_by_updated_desc_by_default(self):
#         for url in self._queries_subtests_with_added_hosts():
#             with self.subTest(url=url):
#                 response = self._get(url)
#                 expected_hosts = self._added_hosts_by_updated_desc()
#                 self._assert_host_ids_in_response(response, expected_hosts)
#
#     def tests_hosts_ordered_by_updated_are_descending_by_default(self):
#         for url in self._queries_subtests_with_added_hosts():
#             with self.subTest(url=url):
#                 response = self._get(url, order_by="updated")
#                 expected_hosts = self._added_hosts_by_updated_desc()
#                 self._assert_host_ids_in_response(response, expected_hosts)
#
#     def tests_hosts_are_ordered_by_updated_descending(self):
#         for url in self._queries_subtests_with_added_hosts():
#             with self.subTest(url=url):
#                 response = self._get(url, order_by="updated", order_how="DESC")
#                 expected_hosts = self._added_hosts_by_updated_desc()
#                 self._assert_host_ids_in_response(response, expected_hosts)
#
#     def tests_hosts_are_ordered_by_updated_ascending(self):
#         for url in self._queries_subtests_with_added_hosts():
#             with self.subTest(url=url):
#                 response = self._get(url, order_by="updated", order_how="ASC")
#                 expected_hosts = self._added_hosts_by_updated_asc()
#                 self._assert_host_ids_in_response(response, expected_hosts)
#
#     def tests_hosts_ordered_by_display_name_are_ascending_by_default(self):
#         for url in self._queries_subtests_with_added_hosts():
#             with self.subTest(url=url):
#                 response = self._get(url, order_by="display_name")
#                 expected_hosts = self._added_hosts_by_display_name_asc()
#                 self._assert_host_ids_in_response(response, expected_hosts)
#
#     def tests_hosts_are_ordered_by_display_name_ascending(self):
#         for url in self._queries_subtests_with_added_hosts():
#             with self.subTest(url=url):
#                 response = self._get(url, order_by="display_name", order_how="ASC")
#                 expected_hosts = self._added_hosts_by_display_name_asc()
#                 self._assert_host_ids_in_response(response, expected_hosts)
#
#     def tests_hosts_are_ordered_by_display_name_descending(self):
#         for url in self._queries_subtests_with_added_hosts():
#             with self.subTest(url=url):
#                 response = self._get(url, order_by="display_name", order_how="DESC")
#                 expected_hosts = self._added_hosts_by_display_name_desc()
#                 self._assert_host_ids_in_response(response, expected_hosts)
#
#
# def _test_order_by_id_desc(
#     flask_app, api_get, mq_create_four_specific_hosts, db_get_host, subtests, specifications, order_by, order_how
# ):
#     created_hosts = mq_create_four_specific_hosts
#
#     for updates, expected_added_hosts in specifications:
#         # Update hosts to they have a same modified_on timestamp, but different IDs.
#         # New modified_on value must be set explicitly so it’s saved the same to all
#         # records. Otherwise SQLAlchemy would consider it unchanged and update it
#         # automatically to its own "now" only for records whose ID changed.
#         new_modified_on = now()
#
#         for added_host_index, new_id in updates:
#             old_id = created_hosts[added_host_index].id
#             old_host = db_get_host(old_id)
#             old_host.id = new_id
#             old_host.modified_on = new_modified_on
#             db.session.add(old_host)
#
#             staleness_offset = Timestamps.from_config(flask_app.config["INVENTORY_CONFIG"])
#             serialized_old_host = serialize_host(old_host, staleness_offset)
#             created_hosts[added_host_index] = HostWrapper(serialized_old_host)
#
#         db.session.commit()
#
#         # Check the order in the response against the expected order. Only indexes
#         # are passed, because self.added_hosts values were replaced during the
#         # update.
#         expected_hosts = tuple(created_hosts[added_host_index] for added_host_index in expected_added_hosts)
#
#         urls = (build_hosts_url(), build_hosts_url(created_hosts), build_system_profile_url(created_hosts))
#         for url in urls:
#             with subtests.test(url=url, updates=updates):
#                 order_query_parameters = build_order_query_parameters(order_by=order_by, order_how=order_how)
#                 response_status, response_data = api_get(url, query_parameters=order_query_parameters)
#
#                 assert_response_status(response_status, expected_status=200)
#                 assert_host_ids_in_response(response_data, expected_hosts)
#
#
# def test_hosts_ordered_by_updated_are_also_ordered_by_id_desc():
#     # The first two hosts (0 and 1) with different display_names will have the same
#     # modified_on timestamp, but different IDs.
#     specifications = (
#         (((0, UUID_1), (1, UUID_2)), (1, 0, 3, 2)),
#         (((1, UUID_2), (0, UUID_3)), (0, 1, 3, 2)),
#         # UPDATE order may influence actual result order.
#         (((1, UUID_2), (0, UUID_1)), (1, 0, 3, 2)),
#         (((0, UUID_3), (1, UUID_2)), (0, 1, 3, 2)),
#     )
#
#     _test_order_by_id_desc(specifications=specifications, order_by="updated", order_how="DESC")
#
#
# def test_hosts_ordered_by_display_name_are_also_ordered_by_id_desc(self):
#     # The two hosts with the same display_name (1 and 2) will have the same
#     # modified_on timestamp, but different IDs.
#     specifications = (
#         (((0, self.UUID_1), (3, self.UUID_2)), (3, 0, 1, 2)),
#         (((3, self.UUID_2), (0, self.UUID_3)), (0, 3, 1, 2)),
#         # UPDATE order may influence actual result order.
#         (((3, self.UUID_2), (0, self.UUID_1)), (3, 0, 1, 2)),
#         (((0, self.UUID_3), (3, self.UUID_2)), (0, 3, 1, 2)),
#     )
#     self._test_order_by_id_desc(specifications, "display_name", "ASC")


def test_invalid_order_by(mq_create_three_specific_hosts, api_get, subtests):
    created_hosts = mq_create_three_specific_hosts

    urls = (HOST_URL, build_hosts_url(host_list=created_hosts), build_system_profile_url(host_list=created_hosts))
    for url in urls:
        with subtests.test(url=url):
            order_query_parameters = build_order_query_parameters(order_by="fqdn", order_how="ASC")
            response_status, response_data = api_get(url, query_parameters=order_query_parameters)
            assert response_status == 400


def test_invalid_order_how(mq_create_three_specific_hosts, api_get, subtests):
    created_hosts = mq_create_three_specific_hosts

    urls = (HOST_URL, build_hosts_url(host_list=created_hosts), build_system_profile_url(host_list=created_hosts))
    for url in urls:
        with subtests.test(url=url):
            order_query_parameters = build_order_query_parameters(order_by="display_name", order_how="asc")
            response_status, response_data = api_get(url, query_parameters=order_query_parameters)
            assert response_status == 400


def test_only_order_how(mq_create_three_specific_hosts, api_get, subtests):
    created_hosts = mq_create_three_specific_hosts

    urls = (HOST_URL, build_hosts_url(host_list=created_hosts), build_system_profile_url(host_list=created_hosts))
    for url in urls:
        with subtests.test(url=url):
            order_query_parameters = build_order_query_parameters(order_by=None, order_how="ASC")
            response_status, response_data = api_get(url, query_parameters=order_query_parameters)
            assert response_status == 400


def test_get_hosts_only_insights(mq_create_three_specific_hosts, mq_create_or_update_host, api_get):
    created_hosts_with_insights_id = mq_create_three_specific_hosts

    host_without_insights_id = minimal_host(subscription_manager_id=generate_uuid())
    created_host_without_insights_id = mq_create_or_update_host(host_without_insights_id)

    response_status, response_data = api_get(HOST_URL, query_parameters={"registered_with": "insights"})

    assert response_status == 200
    assert len(response_data["results"]) == 3

    result_ids = sorted([host["id"] for host in response_data["results"]])
    expected_ids = sorted([host.id for host in created_hosts_with_insights_id])
    non_expected_id = created_host_without_insights_id.id

    assert expected_ids == result_ids
    assert non_expected_id not in expected_ids
