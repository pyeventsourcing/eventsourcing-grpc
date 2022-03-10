import logging
import os
from typing import Optional, cast
from uuid import NAMESPACE_OID, UUID, uuid5

from eventsourcing.domain import Aggregate, event
from eventsourcing.persistence import OperationalError, RecordConflictError
from eventsourcing.system import ProcessApplication, System
from eventsourcing.utils import retry


def set_db_uri():
    raise Exception("Use PostgreSQL here instead of MySQL..........")
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    user = os.getenv("MYSQL_USER", "eventsourcing")
    password = os.getenv("MYSQL_PASSWORD", "eventsourcing")
    db_uri = (
        "mysql+pymysql://{}:{}@{}/eventsourcing?charset=utf8mb4&binary_prefix=true"
    ).format(user, password, host)
    os.environ["DB_URI"] = db_uri


class Order(Aggregate):
    def __init__(self):
        self.is_reserved = False
        self.is_paid = False
        self.reservation_id = None
        self.payment_id = None

    class Reserved(Aggregate.Event):
        reservation_id: UUID

    @event(Reserved)
    def set_is_reserved(self, reservation_id):
        assert not self.is_reserved, "Order {} already reserved.".format(self.id)
        self.is_reserved = True
        self.reservation_id = reservation_id

    @event("Paid")
    def set_is_paid(self, payment_id):
        assert not self.is_paid, "Order {} already paid.".format(self.id)
        self.is_paid = True
        self.payment_id = payment_id


class Reservation(Aggregate):
    def __init__(self, order_id):
        self.order_id = order_id

    @classmethod
    def create_id(cls, order_id):
        return uuid5(NAMESPACE_OID, str(order_id))


class Payment(Aggregate):
    def __init__(self, order_id):
        self.order_id = order_id


logger = logging.getLogger()


class Orders(ProcessApplication):
    def policy(self, domain_event, processing_event) -> None:
        if isinstance(domain_event, Reservation.Created):
            # Set the order as reserved.
            order = self.get_order(order_id=domain_event.order_id)
            assert not order.is_reserved
            order.set_is_reserved(domain_event.originator_id)
            processing_event.collect_events(order)

        elif isinstance(domain_event, Payment.Created):
            # Set the order as paid.
            order = self.get_order(domain_event.order_id)
            assert not order.is_paid
            order.set_is_paid(domain_event.originator_id)
            processing_event.collect_events(order)

    @retry((OperationalError, RecordConflictError), max_attempts=10, wait=0.01)
    def create_new_order(self):
        order = Order()
        self.save(order)
        return order.id

    def is_order_reserved(self, order_id):
        order = self.get_order(order_id)
        return order is not None

    def is_order_paid(self, order_id):
        order = self.get_order(order_id)
        return order is not None and order.is_paid

    def get_order(self, order_id) -> Optional[Order]:
        try:
            return cast(Order, self.repository.get(order_id))
        except KeyError:
            return None


class Reservations(ProcessApplication):
    def policy(self, domain_event, processing_event):
        if isinstance(domain_event, Order.Created):
            # Create a reservation.
            reservation = Reservation(order_id=domain_event.originator_id)
            processing_event.collect_events(reservation)
            a = 1


class Payments(ProcessApplication):
    def policy(self, domain_event, processing_event):
        if isinstance(domain_event, Order.Reserved):
            # Make a payment.
            payment = Payment(order_id=domain_event.originator_id)
            processing_event.collect_events(payment)
            a = 1


system = System([[Orders, Reservations, Orders, Payments, Orders]])
