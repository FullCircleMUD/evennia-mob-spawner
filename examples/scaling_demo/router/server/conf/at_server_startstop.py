"""
Server startstop hooks

This module contains functions called by Evennia at various
points during its startup, reload and shutdown sequence. It
allows for customizing the server operation as desired.

This module must contain at least these global functions:

at_server_init()
at_server_start()
at_server_stop()
at_server_reload_start()
at_server_reload_stop()
at_server_cold_start()
at_server_cold_stop()

"""


def at_server_init():
    """
    This is called first as the server is starting up, regardless of how.
    """
    pass


def _even_minute(rule):
    """The demo's one spawn condition: yes during even wall-clock minutes.

    Alternates often enough to watch a conditional rule fill and then stall.
    """
    import time

    return int(time.time() // 60) % 2 == 0


def at_server_start():
    """
    This is called every time the server starts up, regardless of
    how it was shut down.

    Every instance runs the same hooks. Conditions are registered before
    the spawner starts, so a rule naming one validates and runs. The router
    holds no rule rows, so its spawner ticks over an empty table.
    """
    from evennia_calendar.service import start_calendar_clock
    from evennia_mob_spawner import register_condition, start_mob_spawner

    register_condition("even_minute", _even_minute)
    start_calendar_clock()
    start_mob_spawner()


def at_server_stop():
    """
    This is called just before the server is shut down, regardless
    of it is for a reload, reset or shutdown.
    """
    pass


def at_server_reload_start():
    """
    This is called only when server starts back up after a reload.
    """
    pass


def at_server_reload_stop():
    """
    This is called only time the server stops before a reload.
    """
    pass


def at_server_cold_start():
    """
    This is called only when the server starts "cold", i.e. after a
    shutdown or a reset.
    """
    pass


def at_server_cold_stop():
    """
    This is called only when the server goes down due to a shutdown or
    reset.
    """
    pass
