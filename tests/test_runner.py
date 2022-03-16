from itertools import count
from threading import Thread
from time import sleep
from unittest import TestCase
from uuid import UUID

from eventsourcing_grpc.runner import GrpcRunner
from tests.fixtures import Orders, system


class TestRunner(TestCase):
    def test_runner_with_in_process_servers(self) -> None:
        self._test_runner(with_subprocesses=False)

    def test_runner_with_subprocess_servers(self) -> None:
        self._test_runner(with_subprocesses=True)

    def _test_infinite_runner_with_subprocess_servers(self) -> None:
        c = count()
        while True:
            print("Test number:", next(c))
            self._test_runner(with_subprocesses=True)

    def _test_long_runner_with_subprocess_servers(self) -> None:
        self._long_runner(with_subprocesses=True)

    def _test_runner(self, with_subprocesses: bool = False) -> None:
        # Set up.
        env = {
            "ORDERS_GRPC_ADDRESS": "localhost:50051",
            "RESERVATIONS_GRPC_ADDRESS": "localhost:50052",
            "PAYMENTS_GRPC_ADDRESS": "localhost:50053",
            "POLL_INTERVAL": "1",
        }
        if with_subprocesses:
            env["SYSTEM_TOPIC"] = "tests.fixtures:system"

        runner = GrpcRunner(system=system, env=env)
        runner.start(with_subprocesses=with_subprocesses)
        if runner.has_errored.is_set():
            self.fail("Couldn't start runner")

        # sleep(1)

        # Create an order.
        orders = runner.get_client(Orders)
        order1_id = orders.app.create_new_order()
        self.assertIsInstance(order1_id, UUID)

        # Wait for the processing to happen.
        for _ in range(100):
            sleep(0.1)
            order = orders.app.get_order(order1_id)
            if order["is_paid"]:
                break
            elif runner.has_errored.is_set():
                self.fail("Runner error")
        else:
            self.fail("Timeout waiting for order to be paid")

        # Get the notifications.
        notifications = orders.get_notifications(start=1, limit=10, topics=[])
        self.assertEqual(len(notifications), 3)
        self.assertEqual(notifications[0].id, 1)
        self.assertEqual(notifications[0].originator_id, order1_id)
        self.assertEqual(notifications[0].originator_version, 1)
        self.assertEqual(notifications[0].topic, "tests.fixtures:Order.Created")
        self.assertEqual(notifications[1].id, 2)
        self.assertEqual(notifications[1].originator_id, order1_id)
        self.assertEqual(notifications[1].originator_version, 2)
        self.assertEqual(notifications[1].topic, "tests.fixtures:Order.Reserved")
        self.assertEqual(notifications[2].id, 3)
        self.assertEqual(notifications[2].originator_id, order1_id)
        self.assertEqual(notifications[2].originator_version, 3)
        self.assertEqual(notifications[2].topic, "tests.fixtures:Order.Paid")

        orders_app = Orders()
        first_event = orders_app.mapper.to_domain_event(notifications[0])
        last_event = orders_app.mapper.to_domain_event(notifications[-1])
        duration = last_event.timestamp - first_event.timestamp
        print("Duration:", duration)

        runner.stop()

    def _long_runner(self, with_subprocesses: bool = False) -> None:
        # Set up.
        env = {
            "ORDERS_GRPC_ADDRESS": "localhost:50051",
            "RESERVATIONS_GRPC_ADDRESS": "localhost:50052",
            "PAYMENTS_GRPC_ADDRESS": "localhost:50053",
            "POLL_INTERVAL": "1",
        }
        if with_subprocesses:
            env["SYSTEM_TOPIC"] = "tests.fixtures:system"

        runner = GrpcRunner(system=system, env=env)
        runner.start(with_subprocesses=with_subprocesses)
        if runner.has_errored.is_set():
            self.fail("Couldn't start runner")

        # sleep(1)

        # Create an order.
        orders = runner.get_client(Orders)

        order_ids = []

        def create_order() -> None:
            for _ in range(10000):
                order_ids.append(orders.app.create_new_order())
                # self.assertIsInstance(order1_id, UUID)

        def check_order() -> None:
            for i in range(10000):
                # Wait for the processing to happen.
                for _ in range(100):
                    try:
                        order_id = order_ids[i]
                    except IndexError:
                        sleep(0.01)
                        continue
                    order = orders.app.get_order(order_id)
                    if order["is_paid"]:
                        print("Done order", i)
                        break
                    elif runner.has_errored.is_set():
                        self.fail("Runner error")
                    else:
                        sleep(0.1)
                else:
                    self.fail("Timeout waiting for order to be paid")

        thread1 = Thread(target=create_order)
        thread1.start()
        thread2 = Thread(target=check_order)
        thread2.start()

        thread2.join()

        return
