# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import datetime
from collections import namedtuple
from itertools import chain

import django.db.models.deletion
import pytz
from django.db import migrations, models
from django.utils import timezone

from radioco.apps.radioco.tz_utils import transform_dt_to_default_tz, fix_recurrence_dst


def calculate_effective_schedule_start_dt(schedule):
    """
    Calculation of the first start date to improve performance
    """
    tz = timezone.get_default_timezone()
    programme_start_dt = tz.localize(
        datetime.datetime.combine(schedule.programme.start_date, datetime.time())
    ).astimezone(pytz.utc) if schedule.programme.start_date else None

    programme_end_dt = tz.localize(
        datetime.datetime.combine(schedule.programme.end_date, datetime.time(23, 59, 59))
    ).astimezone(pytz.utc) if schedule.programme.end_date else None

    # If there are no rrules
    if not schedule.recurrences:
        if programme_start_dt and programme_start_dt > schedule.start_dt:
            return None
        if programme_end_dt and schedule.start_dt > programme_end_dt:
            return None
        return schedule.start_dt

    # Get first date
    after_dt = schedule.start_dt
    if programme_start_dt:
        after_dt = max(schedule.start_dt, programme_start_dt)
    first_start_dt = fix_recurrence_dst(schedule.recurrences.after(
        transform_dt_to_default_tz(after_dt), True, dtstart=transform_dt_to_default_tz(schedule.start_dt)))
    if first_start_dt:
        if programme_end_dt and programme_end_dt < first_start_dt:
            return None
        return first_start_dt
    return None


def calculate_effective_schedule_end_dt(schedule):
    """
    Calculation of the last end date to improve performance
    """
    tz = timezone.get_default_timezone()
    programme_start_dt = tz.localize(
        datetime.datetime.combine(schedule.programme.start_date, datetime.time())
    ).astimezone(pytz.utc) if schedule.programme.start_date else None

    programme_end_dt = tz.localize(
        datetime.datetime.combine(schedule.programme.end_date, datetime.time(23, 59, 59))
    ).astimezone(pytz.utc) if schedule.programme.end_date else None

    runtime = datetime.timedelta(minutes=schedule.programme._runtime)

    # If there are no rrules
    if not schedule.recurrences:
        if not schedule.effective_start_dt:
            # WARNING: this depends on effective_start_dt
            return None  # returning None if there is no effective_start_dt
        return schedule.start_dt + runtime

    # If we have a programme restriction
    if programme_end_dt:
        last_effective_start_date = schedule.recurrences.before(
            transform_dt_to_default_tz(programme_end_dt), dtstart=transform_dt_to_default_tz(schedule.start_dt))
        if last_effective_start_date:
            if programme_start_dt and programme_start_dt > last_effective_start_date:
                return None
            return fix_recurrence_dst(last_effective_start_date) + runtime

    rrules_until_dates = [_rrule.until for _rrule in schedule.recurrences.rrules]

    # If we have a rrule without a until date we don't know the last date
    if any(map(lambda x: x is None, rrules_until_dates)):
        return None

    possible_limit_dates = schedule.recurrences.rdates + rrules_until_dates
    if not possible_limit_dates:
        return None

    # Get the biggest possible start_date. It could be that the biggest date is excluded
    biggest_date = max(possible_limit_dates)
    last_effective_start_date = schedule.recurrences.before(
        transform_dt_to_default_tz(biggest_date), True, dtstart=transform_dt_to_default_tz(schedule.start_dt))
    if last_effective_start_date:
        if programme_start_dt and programme_start_dt > last_effective_start_date:
            return None
        return fix_recurrence_dst(last_effective_start_date) + runtime
    return None



def migrate_schedules(apps, schema_editor):
    """
    Final Migration to v3.0

    Before this migration Calendar (ScheduleBoard) had a date limit and all the schedules were repeated weekly
    We want to move constraint dates from Calendar to programmes and clone the active schedules into the active calendar
    """
    CalendarTuple = namedtuple('Calendar', ['start_date', 'end_date'])
    ProgrammeTuple = namedtuple('Programme', ['id', 'start_date', 'end_date'])
    Schedule = apps.get_model("schedules", "Schedule")
    Calendar = apps.get_model("schedules", "Calendar")
    Programme = apps.get_model("programmes", "Programme")
    tz = timezone.get_default_timezone()
    MIN_DATE = datetime.date(1970, 1, 2)

    calendars = {}
    for calendar in Calendar.objects.all():
        calendars[calendar.id] = CalendarTuple(calendar.start_date, calendar.end_date)

    programmes = {}
    for programme in Programme.objects.all():
        programmes[programme.id] = ProgrammeTuple(programme.id, MIN_DATE, MIN_DATE)

    active_calendar = Calendar.objects.get(name='Active Calendar', is_active=True)  # Created by previous migration
    # Live schedules have to be created first because we are linking to those objects
    schedule_iterator = chain(
        Schedule.objects.filter(type='L').select_related('programme').iterator(),
        Schedule.objects.all().exclude(type='L').select_related('programme').iterator()
    )

    # schedule_iterator = list(
    #     Schedule.objects.filter(type='L').select_related('programme')
    # ) + list(
    #     Schedule.objects.all().exclude(type='L').select_related('programme')
    # )
    
    for schedule in schedule_iterator:
        calendar = calendars[schedule.calendar.id]
        programme = programmes[schedule.programme.id]
        if calendar.start_date:
            # Updating schedule start_date
            schedule.start_dt = tz.localize(datetime.datetime.combine(calendar.start_date, schedule.start_hour))
            schedule.save()

            # Create a copy, keeping previous schedule
            schedule.id = schedule.pk = None
            schedule.calendar = active_calendar

            if schedule.source:
                source = schedule.source
                # We should have created the referenced object first
                # Only live schedules should be in the source field
                schedule.source = Schedule.objects.get(
                    calendar=active_calendar, start_dt=source.start_dt,
                    type=source.type, programme=source.programme
                )

            schedule.save()

            # Add the lower start_date to the programme
            if programme.start_date is not None and (programme.start_date == MIN_DATE or programme.start_date > calendar.start_date):
                programmes[programme.id] = ProgrammeTuple(programme.id, calendar.start_date, programme.end_date)

            # Add the bigger end_date to the programme
            if calendar.end_date:
                # Add end_date to rrule until
                schedule.recurrences.rrules[0].until = tz.localize(
                    datetime.datetime.combine(calendar.end_date, datetime.time(23, 59, 59))
                )
                schedule.save()

                if programme.end_date is not None and (programme.end_date == MIN_DATE or programme.end_date < calendar.end_date):
                    programmes[programme.id] = ProgrammeTuple(programme.id, programme.start_date, calendar.end_date)
            else:
                # No end_date programme restriction
                programmes[programme.id] = ProgrammeTuple(programme.id, programme.start_date, None)

        else:
            # case when start_date and end_date doesn't exist
            # this schedules are disable, we don't need to migrate them but at least we fix the weekday
            date = datetime.date(2016, 1, 4) + datetime.timedelta(days=schedule.day)
            schedule.start_dt = tz.localize(datetime.datetime.combine(date, schedule.start_hour))
            schedule.save()

    # Updating programme constraint dates
    for _programme in programmes.values():
        db_programme = Programme.objects.get(id=_programme.id)
        db_programme.start_date = None if _programme.start_date == MIN_DATE else _programme.start_date
        db_programme.end_date = None if _programme.end_date == MIN_DATE else _programme.end_date
        db_programme.save()

    # Updating all effective schedules dates
    for schedule in Schedule.objects.all().select_related('programme'):
        # End date has to be calculated first
        schedule.effective_start_dt = calculate_effective_schedule_start_dt(schedule)
        schedule.effective_end_dt = calculate_effective_schedule_end_dt(schedule)
        schedule.save()


class Migration(migrations.Migration):

    dependencies = [
        ('schedules', '0004_dev_schedules'),
        ('programmes', '0009_dev_auto_20160820_1634'),
    ]

    operations = [
        migrations.RunPython(migrate_schedules),
        migrations.RemoveField(
            model_name='schedule',
            name='day',
        ),
        migrations.RemoveField(
            model_name='schedule',
            name='start_hour',
        ),
        migrations.RemoveField(
            model_name='calendar',
            name='end_date',
        ),
        migrations.RemoveField(
            model_name='calendar',
            name='start_date',
        ),
        migrations.RemoveField(
            model_name='schedule',
            name='end_date',
        ),
        migrations.AlterField(
            model_name='schedule',
            name='from_collection',
            field=models.ForeignKey(related_name='child_schedules', on_delete=django.db.models.deletion.SET_NULL, blank=True, to='schedules.Schedule', help_text='Parent schedule (only happens when it is changed from recurrence.', null=True),
        ),
        migrations.AlterField(
            model_name='schedule',
            name='source',
            field=models.ForeignKey(on_delete=django.db.models.deletion.SET_NULL, blank=True, to='schedules.Schedule', help_text='Main schedule when (if this is a broadcast).', null=True, verbose_name='source'),
        ),
    ]
