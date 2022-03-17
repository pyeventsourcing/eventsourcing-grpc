from time import sleep
from typing import Type
from unittest import TestCase
from uuid import UUID

from eventsourcing.application import Application, TApplication
from eventsourcing.system import System
from eventsourcing.utils import EnvType

from eventsourcing_grpc.application_client import (
    ApplicationClient,
    ChannelConnectTimeout,
)
from eventsourcing_grpc.application_server import ApplicationServer, GrpcEnvironment
from tests.fixtures import Orders, Reservations

system_orders = System([[Orders]])

assert system_orders.topic

env_orders: EnvType = {
    "SYSTEM_TOPIC": system_orders.topic,
    "ORDERS_GRPC_APPLICATION_ADDRESS": "localhost:50051",
    "POLL_INTERVAL": "1",
}

system_orders_and_reservations = System([[Orders, Reservations, Orders]])

assert system_orders_and_reservations.topic

env_orders_and_reservations: EnvType = {
    "SYSTEM_TOPIC": system_orders_and_reservations.topic,
    "ORDERS_GRPC_APPLICATION_ADDRESS": "localhost:50051",
    "RESERVATIONS_GRPC_APPLICATION_ADDRESS": "localhost:50052",
    "PAYMENTS_GRPC_APPLICATION_ADDRESS": "localhost:50053",
    "POLL_INTERVAL": "1",
}


class TestApplicationServer(TestCase):
    def test_start_stop(self) -> None:
        server = ApplicationServer(app_class=Orders, env=env_orders)
        self.assertFalse(server.has_started.is_set())
        self.assertFalse(server.has_stopped.is_set())

        server.start()
        self.assertTrue(server.has_started.is_set())
        self.assertFalse(server.has_stopped.is_set())

        server.stop()
        self.assertFalse(server.has_started.is_set())
        self.assertTrue(server.has_stopped.is_set())

        server.start()
        self.assertTrue(server.has_started.is_set())
        self.assertFalse(server.has_stopped.is_set())

        server.start()
        self.assertTrue(server.has_started.is_set())
        self.assertFalse(server.has_stopped.is_set())

        server.stop()
        self.assertFalse(server.has_started.is_set())
        self.assertTrue(server.has_stopped.is_set())

        server.stop()
        self.assertFalse(server.has_started.is_set())
        self.assertTrue(server.has_stopped.is_set())

    def _start_server(
        self, app_class: Type[Application], env: EnvType
    ) -> ApplicationServer:
        server = ApplicationServer(app_class=app_class, env=env)
        server.start()
        return server

    def test_client_connect_failure(self) -> None:
        env = env_orders
        app_class = Orders
        client = self._create_client(app_class, env)
        with self.assertRaises(ChannelConnectTimeout):
            client.connect(max_attempts=1)

    def _create_client(
        self, app_class: Type[TApplication], env: EnvType
    ) -> ApplicationClient[TApplication]:
        address = GrpcEnvironment(env=env).get_grpc_address(app_class.name)
        transcoder = app_class().construct_transcoder()
        client: ApplicationClient[TApplication] = ApplicationClient(
            client_name="test",
            address=address,
            transcoder=transcoder,
        )
        return client

    def test_client_connect_success(self) -> None:
        _ = self._start_server(Orders, env_orders)
        client = self._create_client(Orders, env_orders)
        client.connect(max_attempts=10)

    def test_call_application_method(self) -> None:
        _ = self._start_server(Orders, env_orders)
        client = self._create_client(Orders, env_orders)
        client.connect(max_attempts=10)

        # Create order.
        order_id = client.app.create_new_order()
        self.assertIsInstance(order_id, UUID)

        # Get order.
        order = client.app.get_order(order_id)
        self.assertIsInstance(order, dict)
        self.assertEqual(order["id"], order_id)
        self.assertEqual(order["is_reserved"], False)
        self.assertEqual(order["is_paid"], False)

    def test_get_notifications(self) -> None:
        _ = self._start_server(Orders, env_orders)
        client = self._create_client(Orders, env_orders)
        client.connect(max_attempts=10)

        # Create an order.
        order1_id = client.app.create_new_order()

        # Get the notifications.
        notifications = client.get_notifications(start=1, limit=10, topics=[])
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].id, 1)
        self.assertEqual(notifications[0].originator_id, order1_id)
        self.assertEqual(notifications[0].originator_version, 1)
        self.assertEqual(notifications[0].topic, "tests.fixtures:Order.Created")

        # Create another order.
        order2_id = client.app.create_new_order()

        # Get the notifications.
        notifications = client.get_notifications(start=1, limit=10, topics=[])
        self.assertEqual(len(notifications), 2)
        self.assertEqual(notifications[1].id, 2)
        self.assertEqual(notifications[1].originator_id, order2_id)
        self.assertEqual(notifications[1].originator_version, 1)
        self.assertEqual(notifications[1].topic, "tests.fixtures:Order.Created")

        # Get the notifications start=1, limit=1.
        notifications = client.get_notifications(start=1, limit=1, topics=[])
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].id, 1)
        self.assertEqual(notifications[0].originator_id, order1_id)

        # Get the notifications from notification ID = 2.
        notifications = client.get_notifications(start=2, limit=10, topics=[])
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].id, 2)
        self.assertEqual(notifications[0].originator_id, order2_id)

        # Get the notifications from notification ID = 3.
        notifications = client.get_notifications(start=3, limit=10, topics=[])
        self.assertEqual(len(notifications), 0)

        # Get the notifications from start=1, with wrong topic.
        notifications = client.get_notifications(start=1, limit=10, topics=["wrong"])
        self.assertEqual(len(notifications), 0)

        # Get the notifications from start=1, with correct topic.
        notifications = client.get_notifications(
            start=1, limit=10, topics=["tests.fixtures:Order.Created"]
        )
        self.assertEqual(len(notifications), 2)

    def test_lead_and_follow(self) -> None:
        # Set up.
        _ = (
            self._start_server(Orders, env_orders_and_reservations),
            self._start_server(Reservations, env_orders_and_reservations),
        )
        orders_client = self._create_client(Orders, env_orders_and_reservations)
        orders_client.connect(max_attempts=10)

        # Create an order.
        order1_id = orders_client.app.create_new_order()

        # Wait for the processing to happen.
        for __ in range(20):
            order = orders_client.app.get_order(order1_id)
            if order["is_reserved"]:
                break
            else:
                sleep(0.1)
        else:
            self.fail("Timed out waiting for order to be reserved")

        # Get the notifications.
        notifications = orders_client.get_notifications(start=1, limit=10, topics=[])
        self.assertEqual(len(notifications), 2)
        self.assertEqual(notifications[0].id, 1)
        self.assertEqual(notifications[0].originator_id, order1_id)
        self.assertEqual(notifications[0].originator_version, 1)
        self.assertEqual(notifications[0].topic, "tests.fixtures:Order.Created")
        self.assertEqual(notifications[1].id, 2)
        self.assertEqual(notifications[1].originator_id, order1_id)
        self.assertEqual(notifications[1].originator_version, 2)
        self.assertEqual(notifications[1].topic, "tests.fixtures:Order.Reserved")
