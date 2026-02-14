from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals
import datetime


class EthiopianDateConverter(object):
    @classmethod
    def _start_day_of_ethiopian(cls, year):
        new_year_day = (year // 100) - (year // 400) - 4
        if (year - 1) % 4 == 3:
            new_year_day += 1
        return new_year_day

    @classmethod
    def _is_ethiopian_leap_year(cls, year):
        """Check if an Ethiopian year is a leap year (year before Ethiopian new year in Gregorian calendar)"""
        return (year - 1) % 4 == 3

    @classmethod
    def _is_gregorian_leap_year(cls, year):
        """Check if a Gregorian year is a leap year"""
        return (year % 4 == 0 and year % 100 != 0) or year % 400 == 0

    @classmethod
    def _validate_ethiopian_date(cls, year, month, date):
        """Validate Ethiopian date and return detailed error message if invalid"""
        # Check if inputs are integers
        if not all(isinstance(x, int) for x in [year, month, date]):
            return "All date components (year, month, day) must be integers."
        
        # Check for zero or negative values
        if year <= 0:
            return f"Invalid Ethiopian year: {year}. Year must be a positive integer."
        if month <= 0:
            return f"Invalid Ethiopian month: {month}. Month must be between 1 and 13."
        if date <= 0:
            return f"Invalid Ethiopian day: {date}. Day must be a positive integer."
        
        # Validate month range (Ethiopian calendar has 13 months)
        if month > 13:
            return f"Invalid Ethiopian month: {month}. Ethiopian calendar has only 13 months (1-13)."
        
        # Validate day based on month
        if month <= 12:
            # First 12 months always have 30 days
            if date > 30:
                return f"Invalid Ethiopian date: Month {month} has only 30 days, but day {date} was provided."
        else:
            # Month 13 (Pagume) has 5 or 6 days depending on leap year
            is_leap = cls._is_ethiopian_leap_year(year)
            max_days = 6 if is_leap else 5
            if date > max_days:
                leap_status = "leap year" if is_leap else "non-leap year"
                return (f"Invalid Ethiopian date: Pagume (month 13) has only {max_days} days "
                       f"in {leap_status} {year}, but day {date} was provided.")
        
        return None  # Valid date

    @classmethod
    def _validate_gregorian_date(cls, year, month, date):
        """Validate Gregorian date and return detailed error message if invalid"""
        # Check if inputs are integers
        if not all(isinstance(x, int) for x in [year, month, date]):
            return "All date components (year, month, day) must be integers."
        
        # Check for zero or negative values
        if year <= 0:
            return f"Invalid Gregorian year: {year}. Year must be a positive integer."
        if month <= 0:
            return f"Invalid Gregorian month: {month}. Month must be between 1 and 12."
        if date <= 0:
            return f"Invalid Gregorian day: {date}. Day must be a positive integer."
        
        # Validate month range
        if month > 12:
            return f"Invalid Gregorian month: {month}. Gregorian calendar has only 12 months (1-12)."
        
        # Days in each month (non-leap year)
        days_in_month = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        month_names = ["", "January", "February", "March", "April", "May", "June",
                      "July", "August", "September", "October", "November", "December"]
        
        # Adjust for leap year
        if cls._is_gregorian_leap_year(year):
            days_in_month[2] = 29
        
        # Check for dates that were skipped during Gregorian calendar adoption
        if year == 1582 and month == 10 and 5 <= date <= 14:
            return (f"Invalid Gregorian date: October 5-14, 1582 do not exist. "
                   f"These dates were skipped during the adoption of the Gregorian calendar.")
        
        # Validate day based on month
        max_days = days_in_month[month]
        if date > max_days:
            leap_info = ""
            if month == 2:
                leap_status = "leap year" if cls._is_gregorian_leap_year(year) else "non-leap year"
                leap_info = f" ({leap_status})"
            return (f"Invalid Gregorian date: {month_names[month]} has only {max_days} days "
                   f"in {year}{leap_info}, but day {date} was provided.")
        
        return None  # Valid date

    @classmethod
    def date_to_gregorian(cls, adate):
        return cls.to_gregorian(adate.year, adate.month, adate.day)

    @classmethod
    def date_to_ethiopian(cls, adate):
        return cls.to_ethiopian(adate.year, adate.month, adate.day)

    @classmethod
    def to_gregorian(cls, year, month, date):
        """Convert Ethiopian date to Gregorian date"""
        # Validate Ethiopian date
        error_msg = cls._validate_ethiopian_date(year, month, date)
        if error_msg:
            raise ValueError(error_msg)

        new_year_day = cls._start_day_of_ethiopian(year)
        gregorian_year = year + 7
        gregorian_months = [0, 30, 31, 30, 31, 31, 28, 31, 30, 31, 30, 31, 31, 30]
        
        next_year = gregorian_year + 1
        if cls._is_gregorian_leap_year(next_year):
            gregorian_months[6] = 29

        until = ((month - 1) * 30) + date
        if until <= 37 and year <= 1575:
            until += 28
            gregorian_months[0] = 31
        else:
            until += new_year_day - 1

        # Fixed: corrected operator precedence issue
        if (year - 1) % 4 == 3:
            until += 1

        m = 0
        gregorian_date = until
        for i in range(0, len(gregorian_months)):
            if until <= gregorian_months[i]:
                m = i
                gregorian_date = until
                break
            else:
                m = i
                until -= gregorian_months[i]

        if m > 4:
            gregorian_year += 1

        order = [8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9]
        gregorian_month = order[m]

        return datetime.date(gregorian_year, gregorian_month, gregorian_date)

    @classmethod
    def to_ethiopian(cls, year, month, date):
        """Ethiopian date string representation of provided Gregorian date"""
        # Validate Gregorian date
        error_msg = cls._validate_gregorian_date(year, month, date)
        if error_msg:
            raise ValueError(error_msg)

        gregorian_months = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        ethiopian_months = [0, 30, 30, 30, 30, 30, 30, 30, 30, 30, 5, 30, 30, 30, 30]

        if cls._is_gregorian_leap_year(year):
            gregorian_months[2] = 29

        ethiopian_year = year - 8

        if cls._is_ethiopian_leap_year(ethiopian_year):
            ethiopian_months[10] = 6
        else:
            ethiopian_months[10] = 5

        new_year_day = cls._start_day_of_ethiopian(year - 8)

        until = 0
        for i in range(1, month):
            until += gregorian_months[i]
        until += date

        if ethiopian_year % 4 == 0:
            tahissas = 26
        else:
            tahissas = 25

        if year < 1582:
            ethiopian_months[1] = 0
            ethiopian_months[2] = tahissas
        elif until <= 277 and year == 1582:
            ethiopian_months[1] = 0
            ethiopian_months[2] = tahissas
        else:
            tahissas = new_year_day - 3
            ethiopian_months[1] = tahissas

        m = 0
        ethiopian_date = until
        for m in range(1, len(ethiopian_months)):
            if until <= ethiopian_months[m]:
                if m == 1 or ethiopian_months[m] == 0:
                    ethiopian_date = until + (30 - tahissas)
                else:
                    ethiopian_date = until
                break
            else:
                until -= ethiopian_months[m]

        if m > 10:
            ethiopian_year += 1

        order = [0, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 1, 2, 3, 4]
        ethiopian_month = order[m]

        return ethiopian_year, ethiopian_month, ethiopian_date