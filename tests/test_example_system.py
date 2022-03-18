from unittest import TestCase

from eventsourcing.system import SingleThreadedRunner

from eventsourcing_grpc.example import Orders, system


class TestExampleSystem(TestCase):
    def test_run_system(self) -> None:
        runner = SingleThreadedRunner(system=system)
        runner.start()
        orders = runner.get(Orders)
        order_id = orders.create_new_order()
        self.assertTrue(orders.is_order_reserved(order_id))
        self.assertTrue(orders.is_order_paid(order_id))
