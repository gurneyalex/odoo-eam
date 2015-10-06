#! # -*- coding: utf-8 -*-
"""
helper functions for time manipulation

"""
import calendar
import time

from openerp.tools import DEFAULT_SERVER_DATE_FORMAT as DATE_FMT

DAY = 3600. * 24


def timegm(date=None, format=DATE_FMT):
    """convert a string representing a date (using DEFAULT_SERVER_DATE_FORMAT by
    default) to a UTC Unix timestamp as a floating point number.

    if date is None, then the current time is used.

    """
    if date is None:
        date = time.strftime(format, time.gmtime())
    return 1.0*calendar.timegm(time.strptime(date, format))


def today():
    """return the current date as a string"""
    return time.strftime(DATE_FMT)


