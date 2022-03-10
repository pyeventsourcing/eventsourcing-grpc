from typing import cast
from unittest import TestCase
from uuid import UUID

from eventsourcing_grpc.application_client import ApplicationClient
from eventsourcing_grpc.application_server import ApplicationServer
from tests.fixtures import Order, Orders


class TestApplicationServer(TestCase):
    def setUp(self) -> None:
        self.address = "localhost:50051"

    def test_client_connect_success(self) -> None:
        orders = Orders()
        server = ApplicationServer(application=orders, address=self.address)
        server.start()
        client: ApplicationClient[Orders] = ApplicationClient(
            address=self.address, transcoder=orders.construct_transcoder()
        )
        client.connect(timeout=1)

    def test_client_connect_failure(self) -> None:
        orders = Orders()
        server = ApplicationServer(application=orders, address=self.address)
        server.start()
        client: ApplicationClient[Orders] = ApplicationClient(
            address="localhost:50052", transcoder=orders.construct_transcoder()
        )
        with self.assertRaises(TimeoutError):
            client.connect(timeout=0.5)

    def test_call_application_method(self) -> None:
        orders = Orders()
        server = ApplicationServer(application=orders, address=self.address)
        server.start()
        client: ApplicationClient[Orders] = ApplicationClient(
            address=self.address, transcoder=orders.construct_transcoder()
        )
        client.connect(timeout=1)

        order_id = client.app.create_new_order()
        self.assertIsInstance(order_id, UUID)

        order = client.app.get_order(order_id)
        self.assertIsInstance(order, dict)
        self.assertEqual(order["id"], order_id)

    def test_get_notifications(self) -> None:
        # Set up.
        orders = Orders()
        server = ApplicationServer(application=orders, address=self.address)
        server.start()
        client: ApplicationClient[Orders] = ApplicationClient(
            address=self.address, transcoder=orders.construct_transcoder()
        )
        client.connect(timeout=1)

        # Create an order.
        order1_id = client.app.create_new_order()
        self.assertIsInstance(order1_id, UUID)

        # Get the notifications.
        notifications = client.get_notifications(start=1, limit=10, topics=[])
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].id, 1)
        self.assertEqual(notifications[0].originator_id, order1_id)
        self.assertEqual(notifications[0].originator_version, 1)
        self.assertEqual(notifications[0].topic, "tests.fixtures:Order.Created")

        order_created = orders.mapper.to_domain_event(notifications[0])
        copy_order1 = cast(Order, order_created.mutate(None))
        self.assertIsInstance(copy_order1, Order)
        self.assertEqual(copy_order1.id, order1_id)

        # Create another order.
        order2_id = client.app.create_new_order()
        self.assertIsInstance(order2_id, UUID)

        # Get the notifications.
        notifications = client.get_notifications(start=1, limit=10, topics=[])
        self.assertEqual(len(notifications), 2)
        self.assertEqual(notifications[1].id, 2)
        self.assertEqual(notifications[1].originator_id, order2_id)
        self.assertEqual(notifications[1].originator_version, 1)
        self.assertEqual(notifications[1].topic, "tests.fixtures:Order.Created")

        order_created = orders.mapper.to_domain_event(notifications[1])
        copy_order2 = cast(Order, order_created.mutate(None))
        assert isinstance(copy_order2, Order)
        self.assertIsInstance(copy_order2, Order)
        self.assertEqual(copy_order2.id, order2_id)

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
