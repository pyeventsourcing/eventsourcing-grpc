from time import sleep
from typing import cast
from unittest import TestCase, skip
from uuid import UUID

from eventsourcing_grpc.application_client import ApplicationClient, ServiceUnavailable
from eventsourcing_grpc.application_server import ApplicationServer
from tests.fixtures import Order, Orders, Reservations


class TestApplicationServer(TestCase):
    def test_client_connect_success(self) -> None:
        address = "localhost:50051"
        orders = Orders()
        server = ApplicationServer(
            application=orders, address=address, poll_interval=10
        )
        server.start()
        client: ApplicationClient[Orders] = ApplicationClient(
            address=address, transcoder=orders.construct_transcoder()
        )
        client.connect()

    @skip("Need to find a faster way to do this")
    def test_client_connect_failure(self) -> None:
        orders = Orders()
        client: ApplicationClient[Orders] = ApplicationClient(
            address="localhost:50049",
            transcoder=orders.construct_transcoder(),
            request_deadline=1,
        )
        with self.assertRaises(ServiceUnavailable):
            client.connect()

    def test_call_application_method(self) -> None:
        address = "localhost:50051"
        orders = Orders()
        server = ApplicationServer(
            application=orders, address=address, poll_interval=10
        )
        server.start()
        client: ApplicationClient[Orders] = ApplicationClient(
            address=address, transcoder=orders.construct_transcoder()
        )
        client.connect()

        order_id = client.app.create_new_order()
        self.assertIsInstance(order_id, UUID)

        order = client.app.get_order(order_id)
        self.assertIsInstance(order, dict)
        self.assertEqual(order["id"], order_id)

    def test_get_notifications(self) -> None:
        # Set up.
        address = "localhost:50051"
        orders = Orders()
        server = ApplicationServer(
            application=orders, address=address, poll_interval=10
        )
        server.start()
        client: ApplicationClient[Orders] = ApplicationClient(
            address=address, transcoder=orders.construct_transcoder()
        )
        client.connect()

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

    def test_lead_and_follow(self) -> None:
        # Set up.
        orders_address = "localhost:50051"
        reservations_address = "localhost:50052"
        orders_application = Orders()
        reservations_application = Reservations()

        orders_server = ApplicationServer(
            application=orders_application, address=orders_address, poll_interval=10
        )
        orders_server.start()

        reservations_server = ApplicationServer(
            application=reservations_application,
            address=reservations_address,
            poll_interval=10,
        )
        reservations_server.start()

        orders_client: ApplicationClient[Orders] = ApplicationClient(
            address=orders_address, transcoder=orders_application.construct_transcoder()
        )
        orders_client.connect()

        reservations_client: ApplicationClient[Orders] = ApplicationClient(
            address=reservations_address,
            transcoder=reservations_application.construct_transcoder(),
        )
        reservations_client.connect()

        reservations_client.follow(name=orders_application.name, address=orders_address)
        orders_client.lead(
            name=reservations_application.name, address=reservations_address
        )

        orders_client.follow(
            name=reservations_application.name, address=reservations_address
        )
        reservations_client.lead(name=orders_application.name, address=orders_address)

        # Create an order.
        order1_id = orders_client.app.create_new_order()
        self.assertIsInstance(order1_id, UUID)

        # Wait for the processing to happen.
        for _ in range(20):
            if len(orders_application.notification_log.select(start=1, limit=10)) > 1:
                break
            else:
                sleep(0.1)
        else:
            self.fail("Timeout waiting for len notifications > 1")

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
