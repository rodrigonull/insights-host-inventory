import json

import pytest
from sqlalchemy_utils import create_database
from sqlalchemy_utils import database_exists
from sqlalchemy_utils import drop_database

from app import create_app
from app import db
from app.config import Config
from app.config import RuntimeEnvironment
from app.models import Host
from app.queue.queue import handle_message
from tests.test_utils import ACCOUNT
from tests.test_utils import get_required_headers
from tests.test_utils import HOST_URL
from tests.test_utils import inject_qs
from tests.test_utils import MockEventProducer
from tests.test_utils import now
from tests.test_utils import wrap_message


@pytest.fixture(scope="session")
def database():
    config = Config(RuntimeEnvironment.TEST)
    if not database_exists(config.db_uri):
        create_database(config.db_uri)

    yield config.db_uri

    drop_database(config.db_uri)


@pytest.fixture(scope="session")
def new_flask_app(database):
    app = create_app(RuntimeEnvironment.TEST)
    app.testing = True

    return app


@pytest.fixture(scope="function")
def flask_app(new_flask_app):
    with new_flask_app.app_context() as ctx:
        db.create_all()
        ctx.push()

        yield new_flask_app

        ctx.pop()
        db.session.remove()
        db.drop_all()


@pytest.fixture(scope="function")
def flask_client(flask_app):
    return flask_app.test_client()


@pytest.fixture(scope="function")
def api_create_or_update_host(flask_client):
    def _api_create_or_update_host(host_data, query_parameters=None, auth_type="account_number"):
        payload = [item.data() for item in host_data]

        url = inject_qs(HOST_URL, **query_parameters) if query_parameters else HOST_URL

        response = flask_client.post(url, data=json.dumps(payload), headers=get_required_headers(auth_type))

        return response.status_code, json.loads(response.data)

    return _api_create_or_update_host


@pytest.fixture(scope="function")
def api_get_host(flask_client):
    def _api_get_host(url, query_parameters=None):
        url = inject_qs(url, **query_parameters) if query_parameters else url

        response = flask_client.get(url, headers=get_required_headers())

        return response.status_code, json.loads(response.data)

    return _api_get_host


@pytest.fixture(scope="function")
def db_get_host(flask_app):
    def _db_get_host(host_id):
        return Host.query.get(host_id)

    return _db_get_host


@pytest.fixture(scope="function")
def db_get_host_by_insights_id(flask_app):
    def _db_get_host_by_insights_id(insights_id):
        return Host.query.filter(Host.canonical_facts["insights_id"].astext == insights_id).one()

    return _db_get_host_by_insights_id


@pytest.fixture(scope="function")
def db_create_host(flask_app):
    def _db_create_host(canonical_facts, display_name=None, account=ACCOUNT):
        host = Host(canonical_facts, display_name=display_name, account=account)
        db.session.add(host)
        db.session.commit()
        return host

    return _db_create_host


@pytest.fixture(scope="function")
def handle_msg(flask_app):
    def _handle_msg(message, producer):
        with flask_app.app_context():
            handle_message(json.dumps(message), producer)

    return _handle_msg


@pytest.fixture(scope="function")
def mq_create_or_update_host(handle_msg, event_producer_mock):
    def _mq_create_or_update_host(host_data, platform_metadata=None):
        message = wrap_message(host_data=host_data, platform_metadata=platform_metadata)
        handle_msg(message, event_producer_mock)

        return event_producer_mock.key, json.loads(event_producer_mock.event), event_producer_mock.headers

    return _mq_create_or_update_host


@pytest.fixture(scope="function")
def event_producer_mock():
    return MockEventProducer()


@pytest.fixture(scope="function")
def event_datetime_mock(mocker):
    return mocker.patch("app.queue.events.datetime", **{"now.return_value": now()})