# Radioco - Broadcasting Radio Recording Scheduling system.
# Copyright (C) 2014  Iago Veloso Abalo
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


import copy

from django.conf.urls import url, patterns
from django.contrib import admin
from django.core.checks import messages
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect
from django.utils.translation import ugettext_lazy as _

from radioco.apps.programmes.models import Programme
from radioco.apps.schedules.models import Schedule, ScheduleBoard

try:
    from django.utils.encoding import force_unicode
except ImportError:
    from django.utils.encoding import force_text as force_unicode


@admin.register(ScheduleBoard)
class ScheduleBoardAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active')
    list_filter = ['is_active']
    search_fields = ['name']
    ordering = ['name']
    actions = ['copy_ScheduleBoard']

    def set_active(self, request, queryset):
        if queryset.count() == 1:
            active_boards = ScheduleBoard.objects.filter(is_active=True)
            active_boards.update(is_active=False)
            queryset.update(is_active=True)
            self.message_user(request, _('Board marked as active'))
        else:
            self.message_user(request, _('You cannot mark more than 1 schedule as active'), level=messages.ERROR)
    set_active.short_description = _("Set a calendar active")

    def copy_ScheduleBoard(self, request, queryset):
        for obj in queryset:
            obj_copy = copy.copy(obj)
            obj_copy.id = None
            obj_copy.pk = None
            copy_name = _('Copy of ') + obj.name
            obj_copy.name = copy_name
            obj_copy.is_active = False
            try:
                if ScheduleBoard.objects.get(name=copy_name):
                    self.message_user(
                        request,
                        _('A calendar with the name %(obj)s already exists') % {'obj': force_unicode(obj)},
                        level=messages.ERROR
                    )
                    pass
                    # Don't work
                    # self.message_user(
                    # request, _('There is already a calendar with this name \"%s\"') % copy_name, level=messages.ERROR
                    # )
            except ScheduleBoard.DoesNotExist:
                obj_copy.save()
                # Live Schedules lives must be created first
                schedules = []
                schedules.extend(Schedule.objects.filter(schedule_board=obj, type='L'))
                schedules.extend(Schedule.objects.filter(schedule_board=obj).exclude(type='L'))
                for schedule in schedules:
                    schedule_copy = copy.copy(schedule)
                    schedule_copy.id = None
                    schedule_copy.pk = None
                    schedule_copy.schedule_board = obj_copy
                    if schedule_copy.source:
                        source = schedule_copy.source
                        source_copy = Schedule.objects.get(
                            schedule_board=obj_copy, day=source.day, start_hour=source.start_hour,
                            type=source.type, programme=source.programme
                        )
                        schedule_copy.source = source_copy
                    schedule_copy.save()

    copy_ScheduleBoard.short_description = _("Make a Copy of calendar")


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    fieldsets = (
        (None, {
            'fields': ('programme', 'type', 'start_dt', 'end_dt', 'recurrences')
        }),
        (_('Advanced options'), {
            'classes': ('collapse',),
            'fields': ('effective_start_dt', 'effective_end_dt', 'from_collection', 'source'),
        }),
    )
    readonly_fields = ('end_dt', 'effective_start_dt', 'effective_end_dt', 'source', 'from_collection')
    change_list_template = "admin/schedules/calendar.html"

    def changelist_view(self, request, extra_context=dict()):
        extra_context['schedule_boards'] = ScheduleBoard.objects.all()
        return super(ScheduleAdmin, self).changelist_view(request, extra_context=extra_context)

    def has_add_permission(self, request):
        return False