from pytest import mark

from tests.test_utils import assert_host_exists
from tests.test_utils import generate_uuid


def test_find_host_using_subset_canonical_fact_match(db_create_host):
    fqdn = "fred.flintstone.com"
    canonical_facts = {"fqdn": fqdn, "bios_uuid": generate_uuid(), "rhel_machine_id": generate_uuid()}

    host = db_create_host(canonical_facts)

    # Create the subset of canonical facts to search by
    subset_canonical_facts = {"fqdn": fqdn}

    assert_host_exists(host.id, subset_canonical_facts)


def test_find_host_using_superset_canonical_fact_match(db_create_host):
    canonical_facts = {"fqdn": "fred", "bios_uuid": generate_uuid()}

    # Create the superset of canonical facts to search by
    superset_canonical_facts = canonical_facts.copy()
    superset_canonical_facts["rhel_machine_id"] = generate_uuid()
    superset_canonical_facts["satellite_id"] = generate_uuid()

    host = db_create_host(canonical_facts)

    assert_host_exists(host.id, superset_canonical_facts)


def test_find_host_using_insights_id_match(db_create_host):
    canonical_facts = {"fqdn": "fred", "bios_uuid": generate_uuid(), "insights_id": generate_uuid()}

    # Change the canonical facts except the insights_id...match on insights_id
    search_canonical_facts = {
        "fqdn": "barney",
        "bios_uuid": generate_uuid(),
        "insights_id": canonical_facts["insights_id"],
    }

    host = db_create_host(canonical_facts)

    assert_host_exists(host.id, search_canonical_facts)


def test_find_host_using_subscription_manager_id_match(db_create_host):
    canonical_facts = {"fqdn": "fred", "bios_uuid": generate_uuid(), "subscription_manager_id": generate_uuid()}

    # Change the bios_uuid so that falling back to subset match will fail
    search_canonical_facts = {
        "bios_uuid": generate_uuid(),
        "subscription_manager_id": canonical_facts["subscription_manager_id"],
    }

    host = db_create_host(canonical_facts)

    assert_host_exists(host.id, search_canonical_facts)


@mark.parametrize(("host_create_order", "expected_host"), (((0, 1), 1), ((1, 0), 0)))
def test_find_host_using_elevated_ids_match(db_create_host, host_create_order, expected_host):
    hosts_canonical_facts = ({"subscription_manager_id": generate_uuid()}, {"insights_id": generate_uuid()})

    created_hosts = []
    for host_canonical_facts in host_create_order:
        created_host = db_create_host(hosts_canonical_facts[host_canonical_facts])
        created_hosts.append(created_host)

    search_canonical_facts = {
        key: value for host_canonical_facts in hosts_canonical_facts for key, value in host_canonical_facts.items()
    }

    assert_host_exists(created_hosts[expected_host].id, search_canonical_facts)