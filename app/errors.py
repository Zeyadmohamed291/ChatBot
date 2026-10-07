class BackendUnavailable(Exception):
    pass


class OrderUnavailable(Exception):
    """Deliberately does not distinguish an absent order from an unowned order."""


class PlannerUnavailable(Exception):
    pass


class SessionUnavailable(Exception):
    pass


class Busy(Exception):
    pass
