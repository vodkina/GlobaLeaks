# -*- coding: UTF-8
#
# validator
# *********
#
# Utilities to validate data recorded in the ORM
import re

from globaleaks import LANGUAGES_SUPPORTED_CODES
from globaleaks.settings import GLSettings
from globaleaks.rest.errors import InvalidModelInput
from globaleaks.utils.utility import log


def in_range(x, y):
      """
      Returns a validator that ensures the passed value stays between x and y
      """
      def range_v(self, attr, value):
          if not isinstance(value, int) and not isinstance(value, float):
              raise InvalidModelInput("range_v: expected a float or an int: (%s)" % type(value))
          if value < x or y < value:
              raise InvalidModelInput("range_v: input outside of valid range: (%s:%d)" % (attr, value))
          return value
      return range_v


def natnum_v(self, attr, value):
    """
    Validates that the passed value is a natural number (in Z+)
    """
    if not isinstance(value, int) or value < 0:
        raise InvalidModelInput("natnum_v: expected val to be in Z+ (%s:%d)" % (attr, value))
    return value


def shorttext_v(self, attr, value):
    """
    """
    if isinstance(value, str):
        value = unicode(value)

    if not isinstance(value, unicode):
        raise InvalidModelInput("shorttext_v: expected unicode (%s:%s)" % (attr, value))

    if GLSettings.enable_input_length_checks and len(value) > GLSettings.memory_copy.maximum_namesize:
        raise InvalidModelInput("shorttext_v: length need to be < of %d"
                                        % GLSettings.memory_copy.maximum_namesize)

    return value


def longtext_v(self, attr, value):
    """
    """
    if not attr:
        return value

    if isinstance(value, str):
        value = unicode(value)

    if not isinstance(value, unicode):
        raise InvalidModelInput("longtext_v: expected unicode (%s:%s)" %
                                 (attr, value))

    if GLSettings.enable_input_length_checks and len(value) > GLSettings.memory_copy.maximum_textsize:
        raise InvalidModelInput("longtext_v: unicode text in %s " \
                                 "overcomes length " \
                                 "limit %d" % (attr, GLSettings.memory_copy.maximum_textsize))

    return value


def dict_v(self, attr, value):
    """
    """
    if not value:
        return {}

    if not isinstance(value, dict):
        raise InvalidModelInput("dict_v: expected dict (%s)" % attr)

    for key, subvalue in value.iteritems():
        if isinstance(subvalue, str):
            subvalue = unicode(subvalue)

        if isinstance(subvalue, unicode):
            if GLSettings.enable_input_length_checks and len(subvalue) > GLSettings.memory_copy.maximum_textsize:
                raise InvalidModelInput("dict_v: text for key %s in %s " \
                                        "overcomes length limit of %d" % (key, attr,
                                                                          GLSettings.memory_copy.maximum_textsize))

        if isinstance(subvalue, dict):
            dict_v(self, attr, subvalue)

    return value


def shortlocal_v(self, attr, value):
    """
    """
    dict_v(None, attr, value)

    if not value:
        return value

    # If a language does not exist, it does not mean that a malicious input have been provided,
    # this condition in fact may happen when a language is removed from the package and 
    # so the proper way to handle it so is simply to log the issue and discard the input.
    # https://github.com/globaleaks/GlobaLeaks/issues/879
    remove = [lang for lang in value if lang not in LANGUAGES_SUPPORTED_CODES]
    for k in remove:
        try:
            del value[unicode(k)]
        except KeyError:
            pass
        log.debug("shortlocal_v: (%s) Invalid language code in %s, skipped" %
                  (k, attr))

    for lang, text in value.iteritems():
        shorttext_v(None, None, text)

    return value


def longlocal_v(self, attr, value):
    dict_v(None, attr, value)

    if not value:
        return value

    # If a language does not exist, it does not mean that a malicious input have been provided,
    # this condition in fact may happen when a language is removed from the package and
    # so the proper way to handle it so is simply to log the issue and discard the input.
    # https://github.com/globaleaks/GlobaLeaks/issues/879
    remove = [lang for lang in value if lang not in LANGUAGES_SUPPORTED_CODES]
    for k in remove:
        try:
            del value[unicode(k)]
        except KeyError:
            pass
        log.debug("longlocal_v: (%s) Invalid language code in %s, skipped" %
                  (k, attr))

    for lang, text in value.iteritems():
        longtext_v(None, attr, text)

    return value


def shorturl_v(self, attr, value):
    if not re.match(r'^(/s/[a-z0-9]{1,30})$', value):
        raise InvalidModelInput("invalid shorturl")

    return value


def longurl_v(self, attr, value):
    if not re.match(r'^(/[a-z0-9#=_&?/-]{1,255})$', value):
        raise InvalidModelInput("invalid longurl")

    return value
