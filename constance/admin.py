from datetime import datetime, date, time
from decimal import Decimal
import hashlib
from operator import itemgetter

from django import forms, VERSION
from django.conf.urls import url
from django.contrib import admin, messages
from django.contrib.admin import widgets
from django.contrib.admin.options import csrf_protect_m
from django.core.exceptions import PermissionDenied, ImproperlyConfigured
from django.forms import fields
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.utils import six
from django.utils.encoding import smart_bytes
from django.utils.formats import localize
from django.utils.module_loading import import_string
from django.utils.translation import ugettext_lazy as _
import django


from . import LazyConfig, settings

config = LazyConfig()


NUMERIC_WIDGET = forms.TextInput(attrs={'size': 10})

INTEGER_LIKE = (fields.IntegerField, {'widget': NUMERIC_WIDGET})
STRING_LIKE = (fields.CharField, {
    'widget': forms.Textarea(attrs={'rows': 3}),
    'required': False,
})

FIELDS = {
    bool: (fields.BooleanField, {'required': False}),
    int: INTEGER_LIKE,
    Decimal: (fields.DecimalField, {'widget': NUMERIC_WIDGET}),
    str: STRING_LIKE,
    datetime: (fields.DateTimeField, {'widget': widgets.AdminSplitDateTime}),
    date: (fields.DateField, {'widget': widgets.AdminDateWidget}),
    time: (fields.TimeField, {'widget': widgets.AdminTimeWidget}),
    float: (fields.FloatField, {'widget': NUMERIC_WIDGET}),
}


def parse_additional_fields(fields):
    for key in fields:
        field = fields[key]

        field[0] = import_string(field[0])

        if 'widget' in field[1]:
            klass = import_string(field[1]['widget'])
            field[1]['widget'] = klass(**(field[1].get('widget_kwargs', {}) or {}))

            if 'widget_kwargs' in field[1]:
                del field[1]['widget_kwargs']

    return fields


FIELDS.update(parse_additional_fields(settings.ADDITIONAL_FIELDS))

if not six.PY3:
    FIELDS.update({
        long: INTEGER_LIKE,
        unicode: STRING_LIKE,
    })


class ConstanceForm(forms.Form):
    version = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, initial, *args, **kwargs):
        super(ConstanceForm, self).__init__(*args, initial=initial, **kwargs)
        version_hash = hashlib.md5()

        for name, options in settings.CONFIG.items():
            default, help_text = options[0], options[1]
            if len(options) == 3:
               config_type = options[2]
            else:
               config_type = type(default)

            if config_type not in FIELDS:
                raise ImproperlyConfigured(_("Constance doesn't support "
                                             "config values of the type "
                                             "%(config_type)s. Please fix "
                                             "the value of '%(name)s'.")
                                           % {'config_type': config_type,
                                              'name': name})
            field_class, kwargs = FIELDS[config_type]
            self.fields[name] = field_class(label=name, **kwargs)

            version_hash.update(smart_bytes(initial.get(name, '')))
        self.initial['version'] = version_hash.hexdigest()

    def save(self):
        for name in settings.CONFIG:
            setattr(config, name, self.cleaned_data[name])

    def clean_version(self):
        value = self.cleaned_data['version']
        if value != self.initial['version']:
            raise forms.ValidationError(_('The settings have been modified '
                                          'by someone else. Please reload the '
                                          'form and resubmit your changes.'))
        return value


class ConstanceAdmin(admin.ModelAdmin):
    change_list_template = 'admin/constance/change_list.html'

    def get_urls(self):
        info = self.model._meta.app_label, self.model._meta.module_name
        return [
            url(r'^$',
                self.admin_site.admin_view(self.changelist_view),
                name='%s_%s_changelist' % info),
            url(r'^$',
                self.admin_site.admin_view(self.changelist_view),
                name='%s_%s_add' % info),
        ]

    @csrf_protect_m
    def changelist_view(self, request, extra_context=None):
        # First load a mapping between config name and default value
        if not self.has_change_permission(request, None):
            raise PermissionDenied
        default_initial = ((name, options[0])
            for name, options in settings.CONFIG.items())
        # Then update the mapping with actually values from the backend
        initial = dict(default_initial,
            **dict(config._backend.mget(settings.CONFIG.keys())))
        form = ConstanceForm(initial=initial)
        if request.method == 'POST':
            form = ConstanceForm(data=request.POST, initial=initial)
            if form.is_valid():
                form.save()
                # In django 1.5 this can be replaced with self.message_user
                messages.add_message(
                    request,
                    messages.SUCCESS,
                    _('Live settings updated successfully.'),
                )
                return HttpResponseRedirect('.')
        context = {
            'config': [],
            'title': _('Constance config'),
            'app_label': 'constance',
            'opts': Config._meta,
            'form': form,
            'media': self.media + form.media,
        }
        for name, options in settings.CONFIG.items():
            default, help_text = options[0], options[1]
            # First try to load the value from the actual backend
            value = initial.get(name)
            # Then if the returned value is None, get the default
            if value is None:
                value = getattr(config, name)
            context['config'].append({
                'name': name,
                'default': localize(default),
                'help_text': _(help_text),
                'value': localize(value),
                'modified': value != default,
                'form_field': form[name],
            })
        context['config'].sort(key=itemgetter('name'))
        request.current_app = self.admin_site.name
        # compatibility to be removed when 1.7 is deprecated
        extra = {'current_app': self.admin_site.name} if VERSION < (1, 8) else {}
        return TemplateResponse(request, self.change_list_template, context,
                                **extra)

    def has_add_permission(self, *args, **kwargs):
        return False

    def has_delete_permission(self, *args, **kwargs):
        return False

    def has_change_permission(self, request, obj=None):
        if settings.SUPERUSER_ONLY:
            return request.user.is_superuser
        return super(ConstanceAdmin, self).has_change_permission(request, obj)


class Config(object):
    class Meta(object):
        app_label = 'constance'
        object_name = 'Config'
        model_name = module_name = 'config'
        verbose_name_plural = _('config')
        abstract = False
        swapped = False

        def get_ordered_objects(self):
            return False

        def get_change_permission(self):
            return 'change_%s' % self.model_name

    _meta = Meta()


admin.site.register([Config], ConstanceAdmin)
