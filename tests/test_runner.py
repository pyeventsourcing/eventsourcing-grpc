from datetime import datetime
from itertools import count
from threading import Thread
from time import sleep
from unittest import TestCase
from uuid import UUID

from eventsourcing_grpc.example import Orders, system
from eventsourcing_grpc.runner import GrpcRunner

# NB, uses "localhost" so client and server don't authenticate with certificates.

system_env = {
    "ORDERS_GRPC_SERVER_ADDRESS": "localhost:50051",
    "RESERVATIONS_GRPC_SERVER_ADDRESS": "localhost:50052",
    "PAYMENTS_GRPC_SERVER_ADDRESS": "localhost:50053",
    "MAX_PULL_INTERVAL": "1",
}


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
        runner = GrpcRunner(system=system, env=system_env)
        runner.start(with_subprocesses=with_subprocesses)
        if runner.has_errored.is_set():
            self.fail("Couldn't start runner")

        # Create an order.
        orders = runner.get(Orders)
        order1_id = orders.create_new_order()
        self.assertIsInstance(order1_id, UUID)

        # Wait for the processing to happen.
        for _ in range(100):
            sleep(0.1)
            if orders.is_order_paid(order1_id):
                break
            elif runner.has_errored.is_set():
                self.fail("Runner error")
        else:
            self.fail("Timeout waiting for order to be paid")

        # Get the notifications.
        notifications = orders.notification_log.select(start=1, limit=10)
        self.assertEqual(len(notifications), 3)
        self.assertEqual(notifications[0].id, 1)
        self.assertEqual(notifications[0].originator_id, order1_id)
        self.assertEqual(notifications[0].originator_version, 1)
        self.assertEqual(
            notifications[0].topic, "eventsourcing_grpc.example:Order.Created"
        )
        self.assertEqual(notifications[1].id, 2)
        self.assertEqual(notifications[1].originator_id, order1_id)
        self.assertEqual(notifications[1].originator_version, 2)
        self.assertEqual(
            notifications[1].topic, "eventsourcing_grpc.example:Order.Reserved"
        )
        self.assertEqual(notifications[2].id, 3)
        self.assertEqual(notifications[2].originator_id, order1_id)
        self.assertEqual(notifications[2].originator_version, 3)
        self.assertEqual(
            notifications[2].topic, "eventsourcing_grpc.example:Order.Paid"
        )

        orders_app = Orders()
        first_event = orders_app.mapper.to_domain_event(notifications[0])
        last_event = orders_app.mapper.to_domain_event(notifications[-1])
        duration = last_event.timestamp - first_event.timestamp
        print("Duration:", duration)

        runner.stop()

    def _long_runner(self, with_subprocesses: bool = False) -> None:
        # Set up.
        env = {
            "ORDERS_GRPC_SERVER_ADDRESS": "localhost:50051",
            "RESERVATIONS_GRPC_SERVER_ADDRESS": "localhost:50052",
            "PAYMENTS_GRPC_SERVER_ADDRESS": "localhost:50053",
            "MAX_PULL_INTERVAL": "1",
        }
        if with_subprocesses:
            env["SYSTEM_TOPIC"] = "eventsourcing_grpc.example:system"

        runner = GrpcRunner(system=system, env=env)
        runner.start(with_subprocesses=with_subprocesses)
        if runner.has_errored.is_set():
            self.fail("Couldn't start runner")

        # sleep(1)

        # Create an order.
        orders = runner.get(Orders)

        order_ids = []

        started = datetime.now()

        def create_order() -> None:
            for _ in range(10000):
                order_ids.append(orders.create_new_order())
                sleep(0.0005)
                # self.assertIsInstance(order1_id, UUID)

        def check_order() -> None:
            for i in range(10000):
                # Wait for the processing to happen.
                for _ in range(100):
                    try:
                        order_id = order_ids[i]
                    except IndexError:
                        sleep(0.1)
                        continue
                    order = orders.get_order(order_id)
                    if order["is_paid"]:
                        order_duration = (
                            order["modified_on"] - order["created_on"]
                        ).total_seconds()
                        rate = (i + 1) / (datetime.now() - started).total_seconds()

                        print(
                            f"Done order {i}",
                            f"duration: {order_duration:.4f}s",
                            f"rate: {rate:.0f}/s",
                        )
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
