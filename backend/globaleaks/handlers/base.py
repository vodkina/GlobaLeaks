# -*- encoding: utf-8 -*-
"""
Implementation of BaseHandler, the Cyclone class RequestHandler postponeed with
our needs.
"""

import base64
import collections
import json
import mimetypes
import os
import re
import shutil
import sys
import time
from StringIO import StringIO
from twisted.internet import fdesc, reactor
from twisted.internet.defer import inlineCallbacks
from twisted.python.failure import Failure

from cyclone import httputil, web, template
from cyclone.escape import native_str
from cyclone.httpserver import HTTPConnection, HTTPRequest, _BadRequestException
from cyclone.web import RequestHandler, HTTPError, HTTPAuthenticationRequired, RedirectHandler
from globaleaks.event import track_handler
from globaleaks.rest import errors, requests
from globaleaks.security import GLSecureTemporaryFile, directory_traversal_check, generateRandomKey
from globaleaks.settings import GLSettings
from globaleaks.utils.mailutils import mail_exception_handler, send_exception_email
from globaleaks.utils.tempdict import TempDict
from globaleaks.utils.utility import log, datetime_now, deferred_sleep

HANDLER_EXEC_TIME_THRESHOLD = 30

GLUploads = {}
GLSessions = TempDict(timeout=GLSettings.authentication_lifetime)

# https://github.com/globaleaks/GlobaLeaks/issues/1601
mimetypes.add_type('image/svg+xml', '.svg')
mimetypes.add_type('application/vnd.ms-fontobject', '.eot')
mimetypes.add_type('application/x-font-ttf', '.ttf')
mimetypes.add_type('application/woff', '.woff')
mimetypes.add_type('application/woff2', '.woff2')


def write_upload_plaintext_to_disk(uploaded_file, destination):
    """
    @param uploaded_file: uploaded_file data struct
    @param the file destination
    @return: a descriptor dictionary for the saved file
    """
    try:
        if os.path.exists(destination):
            log.err('Overwriting file %s with %d bytes' % (destination, uploaded_file['size']))
        else:
            log.debug('Creating file %s with %d bytes' % (destination, uploaded_file['size']))

        with open(destination, 'w+') as fd:
            uploaded_file['body'].seek(0, 0)
            data = uploaded_file['body'].read(4000)
            while data != '':
                os.write(fd.fileno(), data)
                data = uploaded_file['body'].read(4000)
    finally:
        uploaded_file['body'].close()
        uploaded_file['path'] = destination

    return uploaded_file


def write_upload_encrypted_to_disk(uploaded_file, destination):
    """
    @param uploaded_file: uploaded_file data struct
    @param the file destination
    @return: a descriptor dictionary for the saved file
    """
    log.debug("Moving encrypted bytes %d from file [%s] %s => %s" %
        (uploaded_file['size'],
         uploaded_file['name'],
         uploaded_file['path'],
         destination)
    )

    shutil.move(uploaded_file['path'], destination)

    uploaded_file['path'] = destination

    return uploaded_file



class StaticFileProducer(object):
    """Streaming producter for files

    @ivar handler: The L{IRequest} to write the contents of the file to.
    @ivar fileObject: The file the contents of which to write to the request.
    """
    bufferSize = GLSettings.file_chunk_size

    def __init__(self, handler, fileObject):
        self.handler = handler
        self.fileObject = fileObject

    def start(self):
        self.handler.request.connection.transport.registerProducer(self, False)

    def resumeProducing(self):
        try:
            if not self.handler:
                return
            data = self.fileObject.read(self.bufferSize)
            if data:
                self.handler.write(data)
                self.handler.flush()
            else:
                self.handler.request.connection.transport.unregisterProducer()
                self.handler.finish()
                self.stopProducing()
        except:
            self.handler.finish()
            raise

    def stopProducing(self):
        self.fileObject.close()
        self.handler = None


class GLSession(object):
    expireCall = None # attached to object by tempDict

    def __init__(self, user_id, user_role, user_status):
        self.id = generateRandomKey(42)
        self.user_id = user_id
        self.user_role = user_role
        self.user_status = user_status

        GLSessions.set(self.id, self)

    def getTime(self):
        return self.expireCall.getTime()

    def __repr__(self):
        return "%s %s expire in %s" % (self.user_role, self.user_id, self.expireCall)


class GLHTTPConnection(HTTPConnection):
    def __init__(self):
        self.uploaded_file = {}

    def _on_headers(self, data):
        try:
            data = native_str(data.decode("latin1"))
            eol = data.find("\r\n")
            start_line = data[:eol]

            try:
                method, uri, version = start_line.split(" ")
            except ValueError:
                raise _BadRequestException("Malformed HTTP request line")

            if not version.startswith("HTTP/"):
                raise _BadRequestException(
                    "Malformed HTTP version in HTTP Request-Line")

            try:
                headers = httputil.HTTPHeaders.parse(data[eol:])
                content_length = int(headers.get("Content-Length", 0))
            except ValueError:
                raise _BadRequestException(
                    "Malformed Content-Length header")

            self._request = HTTPRequest(
                connection=self, method=method, uri=uri, version=version,
                headers=headers, remote_ip=self._remote_ip)

            if content_length:
                megabytes = int(content_length) / (1024 * 1024)
                if megabytes > GLSettings.memory_copy.maximum_filesize:
                    raise _BadRequestException("Request exceeded size limit %d" %
                                               GLSettings.memory_copy.maximum_filesize)

                if headers.get("Expect") == "100-continue":
                    self.transport.write("HTTP/1.1 100 (Continue)\r\n\r\n")

                if content_length < 100000:
                    self._contentbuffer = StringIO()
                else:
                    self._contentbuffer = GLSecureTemporaryFile(GLSettings.tmp_upload_path)

                self.content_length = content_length
                self.setRawMode()
                return

            self.request_callback(self._request)
        except _BadRequestException as e:
            log.msg("Exception while handling HTTP request from %s: %s" % (self._remote_ip, e))
            self.transport.loseConnection()


class BaseHandler(RequestHandler):
    serialize_lists = True
    handler_exec_time_threshold = HANDLER_EXEC_TIME_THRESHOLD
    filehandler = False

    def __init__(self, application, request, **kwargs):
        RequestHandler.__init__(self, application, request, **kwargs)

        self.name = type(self).__name__

        self.handler_time_analysis_begin()
        self.handler_request_logging_begin()

        self.req_id = GLSettings.requests_counter
        GLSettings.requests_counter += 1

        self.request.start_time = datetime_now()

        self.request.request_type = None
        if 'import' in self.request.arguments:
            self.request.request_type = 'import'
        elif 'export' in self.request.arguments:
            self.request.request_type = 'export'

        language = self.request.headers.get('GL-Language')

        if language is None:
            for l in self.parse_accept_language_header():
                if l in GLSettings.memory_copy.languages_enabled:
                    language = l
                    break

        if language is None or language not in GLSettings.memory_copy.languages_enabled:
            language = GLSettings.memory_copy.default_language

        self.request.language = language
        self.set_header("Content-Language", language)

    def parse_accept_language_header(self):
        if "Accept-Language" in self.request.headers:
            languages = self.request.headers["Accept-Language"].split(",")
            locales = []
            for language in languages:
                parts = language.strip().split(";")
                if len(parts) > 1 and parts[1].startswith("q="):
                    try:
                        score = float(parts[1][2:])
                    except (ValueError, TypeError):
                        score = 0.0
                else:
                    score = 1.0
                locales.append((parts[0], score))
            if locales:
                locales.sort(key=lambda pair: pair[1], reverse=True)
                return [l[0] for l in locales]

        return GLSettings.memory_copy.default_language

    @staticmethod
    def authenticated(role):
        """
        Decorator for authenticated sessions.
        If the user is not authenticated, return a http 412 error.
        """
        def wrapper(method_handler):
            def call_handler(cls, *args, **kwargs):
                """
                If not yet auth, is redirected
                If is logged with the right account, is accepted
                If is logged with the wrong account, is rejected with a special message
                """
                if GLSettings.memory_copy.basic_auth:
                    cls.basic_auth()

                if not cls.current_user:
                    raise errors.NotAuthenticated

                if role == '*' or role == cls.current_user.user_role:
                    log.debug("Authentication OK (%s)" % cls.current_user.user_role)
                    return method_handler(cls, *args, **kwargs)

                raise errors.InvalidAuthentication

            return call_handler

        return wrapper

    @staticmethod
    def unauthenticated(method_handler):
        """
        Decorator for unauthenticated requests.
        If the user is logged in an authenticated sessions it does refresh the session.
        """
        def call_handler(cls, *args, **kwargs):
            if GLSettings.memory_copy.basic_auth:
                cls.basic_auth()

            return method_handler(cls, *args, **kwargs)

        return call_handler

    @staticmethod
    def transport_security_check(role):
        """
        Decorator for enforcing the required transport security: Tor/HTTPS
        """
        def wrapper(method_handler):
            def call_handler(cls, *args, **kwargs):
                using_tor2web = cls.check_tor2web()

                if using_tor2web and not GLSettings.memory_copy.accept_tor2web_access[role]:
                    log.err("Denied request on Tor2web for role %s and resource '%s'" %
                            (role, cls.request.uri))
                    raise errors.TorNetworkRequired

                if using_tor2web:
                    log.debug("Accepted request on Tor2web for role '%s' and resource '%s'" %
                              (role, cls.request.uri))

                return method_handler(cls, *args, **kwargs)

            return call_handler

        return wrapper

    def basic_auth(self):
        msg = None
        if "Authorization" in self.request.headers:
            try:
                auth_type, data = self.request.headers["Authorization"].split()
                usr, pwd = base64.b64decode(data).split(":", 1)
                if auth_type != "Basic" or \
                    usr != GLSettings.memory_copy.basic_auth_username or \
                    pwd != GLSettings.memory_copy.basic_auth_password:
                    msg = "Authentication failed"
            except AssertionError:
                msg = "Authentication failed"
        else:
            msg = "Authentication required"

        if msg is not None:
            raise web.HTTPAuthenticationRequired(log_message=msg, auth_type="Basic", realm="")

    def set_default_headers(self):
        """
        In this function are written some security enforcements
        related to WebServer versioning and XSS attacks.

        This is the first function called when a new request reach GLB
        """
        self.request.start_time = datetime_now()

        # to avoid version attacks
        self.set_header("Server", "globaleaks")

        # to reduce possibility for XSS attacks.
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("X-XSS-Protection", "1; mode=block")

        self.set_header("Cache-control", "no-cache, no-store, must-revalidate")
        self.set_header("Pragma", "no-cache")

        self.set_header("Expires", "-1")

        # to avoid information leakage via referrer
        self.set_header("Content-Security-Policy", "referrer no-referrer")

        # to avoid Robots spidering, indexing, caching
        if not GLSettings.memory_copy.allow_indexing:
            self.set_header("X-Robots-Tag", "noindex")

        # to mitigate clickjaking attacks on iframes allowing only same origin
        # same origin is needed in order to include svg and other html <object>
        if not GLSettings.memory_copy.allow_iframes_inclusion:
            self.set_header("X-Frame-Options", "sameorigin")

    @staticmethod
    def validate_host(host_key):
        """
        validate_host checks in the GLSettings list of valid 'Host:' values
        and if matched, return True, else return False
        Is used by all the Web handlers inherit from Cyclone
        """
        # strip eventually port
        hostchunk = str(host_key).split(":")
        if len(hostchunk) == 2:
            host_key = hostchunk[0]

        # hidden service has not a :port
        if re.match(r'^[0-9a-z]{16}\.onion$', host_key):
            return True

        if host_key != '' and host_key in GLSettings.accepted_hosts:
            return True

        log.debug("Error in host requested: %s not accepted between: %s " %
                  (host_key, GLSettings.accepted_hosts))

        return False

    @staticmethod
    def validate_python_type(value, python_type):
        """
        Return True if the python class instantiates the specified python_type.
        """
        if value is None:
            return True

        if python_type == requests.SkipSpecificValidation:
            return True

        if python_type == int:
            try:
                int(value)
                return True
            except Exception:
                return False

        if python_type == bool:
            if value == u'true' or value == u'false':
                return True

        return isinstance(value, python_type)

    @staticmethod
    def validate_regexp(value, type):
        """
        Return True if the python class matches the given regexp.
        """
        try:
            value = unicode(value)
        except Exception:
            return False

        return bool(re.match(type, value))

    @staticmethod
    def validate_type(value, type):
        # if it's callable, than assumes is a primitive class
        if callable(type):
            retval = BaseHandler.validate_python_type(value, type)
            if not retval:
                log.err("-- Invalid python_type, in [%s] expected %s" % (value, type))
            return retval
        # value as "{foo:bar}"
        elif isinstance(type, collections.Mapping):
            retval = BaseHandler.validate_jmessage(value, type)
            if not retval:
                log.err("-- Invalid JSON/dict [%s] expected %s" % (value, type))
            return retval
        # regexp
        elif isinstance(type, str):
            retval = BaseHandler.validate_regexp(value, type)
            if not retval:
                log.err("-- Failed Match in regexp [%s] against %s" % (value, type))
            return retval
        # value as "[ type ]"
        elif isinstance(type, collections.Iterable):
            # empty list is ok
            if len(value) == 0:
                return True
            else:
                retval = all(BaseHandler.validate_type(x, type[0]) for x in value)
                if not retval:
                    log.err("-- List validation failed [%s] of %s" % (value, type))
                return retval
        else:
            raise AssertionError

    @staticmethod
    def validate_jmessage(jmessage, message_template):
        """
        Takes a string that represents a JSON messages and checks to see if it
        conforms to the message type it is supposed to be.

        This message must be either a dict or a list. This function may be called
        recursively to validate sub-parameters that are also go GLType.

        message: the message string that should be validated

        message_type: the GLType class it should match.
        """
        if isinstance(message_template, dict):
            success_check = 0
            keys_to_strip = []
            for key, value in jmessage.iteritems():
                if key not in message_template:
                    # strip whatever is not validated
                    #
                    # reminder: it's not possible to raise an exception for the
                    # in case more values are presenct because it's normal that the
                    # client will send automatically more data.
                    #
                    # e.g. the client will always send 'creation_date' attributs of
                    #      objects and attributes like this are present generally only
                    #      from the second request on.
                    #
                    keys_to_strip.append(key)
                    continue

                if not BaseHandler.validate_type(value, message_template[key]):
                    log.err("Received key %s: type validation fail " % key)
                    raise errors.InvalidInputFormat("Key (%s) type validation failure" % key)
                success_check += 1

            for key in keys_to_strip:
                del jmessage[key]

            for key, value in message_template.iteritems():
                if key not in jmessage.keys():
                    log.debug("Key %s expected but missing!" % key)
                    log.debug("Received schema %s - Expected %s" %
                              (jmessage.keys(), message_template.keys()))
                    raise errors.InvalidInputFormat("Missing key %s" % key)

                if not BaseHandler.validate_type(jmessage[key], value):
                    log.err("Expected key: %s type validation failure" % key)
                    raise errors.InvalidInputFormat("Key (%s) double validation failure" % key)
                success_check += 1

            if success_check == len(message_template.keys()) * 2:
                return True
            else:
                log.err("Success counter double check failure: %d" % success_check)
                raise errors.InvalidInputFormat("Success counter double check failure")

        elif isinstance(message_template, list):
            ret = all(BaseHandler.validate_type(x, message_template[0]) for x in jmessage)
            if not ret:
                raise errors.InvalidInputFormat("Not every element in %s is %s" %
                                                (jmessage, message_template[0]))
            return True

        else:
            raise errors.InvalidInputFormat("invalid json massage: expected dict or list")

    @staticmethod
    def validate_message(message, message_template):
        try:
            jmessage = json.loads(message)
        except ValueError:
            raise errors.InvalidInputFormat("Invalid JSON format")

        if BaseHandler.validate_jmessage(jmessage, message_template):
            return jmessage

        raise errors.InvalidInputFormat("Unexpected condition!?")

    def on_connection_close(self, *args, **kwargs):
        pass

    def prepare(self):
        """
        Here is implemented:
          - The performance analysts
          - the Request/Response logging
        """
        if not self.validate_host(self.request.host):
            raise errors.InvalidHostSpecified

    def on_finish(self):
        """
        Here is implemented:
          - The performance analysts
          - the Request/Response logging
        """
        # file uploads works on chunk basis so that we count 1 the file upload
        # as a whole in function get_file_upload()
        if not self.filehandler:
            track_handler(self)

        self.handler_time_analysis_end()
        self.handler_request_logging_end()

    def do_verbose_log(self, content):
        """
        Record in the verbose log the content as defined by Cyclone wrappers.

        This option is only available in devel mode and intentionally does not filter
        any input/output; It should be used only for debug purposes.
        """

        try:
            with open(GLSettings.httplogfile, 'a+') as fd:
                fdesc.writeToFD(fd.fileno(), content + "\n")
        except Exception as excep:
            log.err("Unable to open %s: %s" % (GLSettings.httplogfile, excep))

    def write_error(self, status_code, **kw):
        exception = kw.get('exception')
        if exception and hasattr(exception, 'error_code'):
            error_dict = {
                'error_message': exception.reason,
                'error_code': exception.error_code
            }

            if hasattr(exception, 'arguments'):
                error_dict.update({'arguments': exception.arguments})
            else:
                error_dict.update({'arguments': []})

            self.set_status(status_code)
            self.write(error_dict)
        else:
            RequestHandler.write_error(self, status_code, **kw)

    def write_file(self, filepath):
        if not os.path.exists(filepath):
          raise HTTPError(404)

        mime_type, encoding = mimetypes.guess_type(filepath)
        if mime_type:
            self.set_header("Content-Type", mime_type)

        StaticFileProducer(self, open(filepath, "rb")).start()

    def force_file_download(self, filename, filepath):
        if not os.path.exists(filepath):
          raise HTTPError(404)

        self.set_header('X-Download-Options', 'noopen')
        self.set_header('Content-Type', 'application/octet-stream')
        self.set_header('Content-Disposition', 'attachment; filename=\"%s\"' % filename)

        StaticFileProducer(self, open(filepath, "rb")).start()

    @inlineCallbacks
    def uniform_answers_delay(self):
        """
        @return: nothing.

        the function perform a sleep uniforming requests to the side_channels_guard
        defined in GLSettings.side_channels_guard in order to counteract some
        side channel attacks.
        """
        request_time = self.request.request_time()
        needed_delay = GLSettings.side_channels_guard - request_time

        if needed_delay > 0:
            yield deferred_sleep(needed_delay)

    @property
    def current_user(self):
        # Check for header based authentication
        session_id = self.request.headers.get('X-Session')

        if session_id is None:
            return None

        return GLSessions.get(session_id)

    def check_tor2web(self):
        return False if self.request.headers.get('X-Tor2Web', None) is None else True

    def get_file_upload(self):
        try:
            if len(self.request.files) != 1:
                raise errors.InvalidInputFormat("cannot accept more than a file upload at once")

            chunk_size = len(self.request.files['file'][0]['body'])
            total_file_size = int(self.request.arguments['flowTotalSize'][0]) if 'flowTotalSize' in self.request.arguments else chunk_size
            flow_identifier = self.request.arguments['flowIdentifier'][0] if 'flowIdentifier' in self.request.arguments else generateRandomKey(10)

            if ((chunk_size / (1024 * 1024)) > GLSettings.memory_copy.maximum_filesize or
                (total_file_size / (1024 * 1024)) > GLSettings.memory_copy.maximum_filesize):
                log.err("File upload request rejected: file too big")
                raise errors.FileTooBig(GLSettings.memory_copy.maximum_filesize)

            if flow_identifier not in GLUploads:
                f = GLSecureTemporaryFile(GLSettings.tmp_upload_path)
                GLUploads[flow_identifier] = f
            else:
                f = GLUploads[flow_identifier]

            f.write(self.request.files['file'][0]['body'])

            if 'flowChunkNumber' in self.request.arguments and 'flowTotalChunks' in self.request.arguments:
                if self.request.arguments['flowChunkNumber'][0] != self.request.arguments['flowTotalChunks'][0]:
                    return None


            uploaded_file = {
                'name': self.request.files['file'][0]['filename'],
                'type': self.request.files['file'][0]['content_type'],
                'size': total_file_size,
                'path': f.filepath,
                'body': f,
                'description': self.request.arguments.get('description', [''])[0]
            }

            self.request._start_time = f.creation_date
            track_handler(self)

            return uploaded_file

        except errors.FileTooBig:
            raise  # propagate the exception

        except Exception as exc:
            log.err("Error while handling file upload %s" % exc)
            return None

    def _handle_request_exception(self, e):
        ret = RequestHandler._handle_request_exception(self, e)

        if isinstance(e, Failure):
            exc_type, exc_value, exc_tb = [e.type, e.value, e.getTracebackObject()]
            e = e.value
        else:
            exc_type, exc_value, exc_tb = sys.exc_info()

        if not isinstance(e, (template.TemplateError,
                              HTTPError, HTTPAuthenticationRequired)):
            mail_exception_handler(exc_type, exc_value, exc_tb)

        return ret

    def handler_time_analysis_begin(self):
        self.start_time = time.time()

    def handler_time_analysis_end(self):
        """
        If the software is running with the option -S --stats (GLSetting.log_timing_stats)
        then we are doing performance testing, having our mailbox spammed is not important,
        so we just skip to report the anomaly.
        """
        current_run_time = time.time() - self.start_time

        if current_run_time > self.handler_exec_time_threshold:
            error = "Handler [%s] exceeded execution threshold (of %d secs) with an execution time of %.2f seconds" % \
                    (self.name, self.handler_exec_time_threshold, current_run_time)
            log.err(error)

            send_exception_email(error)

        if GLSettings.log_timing_stats:
            TimingStatsHandler.log_measured_timing(self.request.method, self.request.uri, self.start_time, current_run_time)


    def handler_request_logging_begin(self):
        if GLSettings.devel_mode and GLSettings.log_requests_responses:
            try:
                content = (">" * 15)
                content += (" Request %d " % GLSettings.requests_counter)
                content += (">" * 15) + "\n\n"

                content += self.request.method + " " + self.request.full_url() + "\n\n"

                content += "request-headers:\n"
                for k, v in self.request.headers.get_all():
                    content += "%s: %s\n" % (k, v)

                if type(self.request.body) == dict and 'body' in self.request.body:
                    # this is needed due to cyclone hack for file uploads
                    body = self.request.body['body'].read()
                else:
                    body = self.request.body

                if len(body):
                    content += "\nrequest-body:\n" + body + "\n"

                self.do_verbose_log(content)

            except Exception as excep:
                log.err("HTTP Request logging fail: %s" % excep.message)
                return

    def handler_request_logging_end(self):
        if GLSettings.devel_mode and GLSettings.log_requests_responses:
            try:
                content = ("<" * 15)
                content += (" Response %d " % self.req_id)
                content += ("<" * 15) + "\n\n"
                content += "\nbody: " + str(self._write_buffer) + "\n"

                self.do_verbose_log(content)
            except Exception as excep:
                log.err("HTTP Requests/Responses logging fail (end): %s" % excep.message)


class BaseStaticFileHandler(BaseHandler):
    def initialize(self, path):
        self.root = "%s%s" % (os.path.abspath(path), "/")

    @BaseHandler.unauthenticated
    @web.asynchronous
    def get(self, path):
        if path == '':
            path = 'index.html'

        abspath = os.path.abspath(os.path.join(self.root, path))

        directory_traversal_check(self.root, abspath)

        self.write_file(abspath)


class BaseRedirectHandler(BaseHandler, RedirectHandler):
    pass


class TimingStatsHandler(BaseHandler):
    TimingsTracker = []

    @staticmethod
    def log_measured_timing(method, uri, start_time, run_time):
        if not GLSettings.log_timing_stats:
            return

        if uri == '/s/timings':
            return

        TimingStatsHandler.TimingsTracker = \
            TimingStatsHandler.TimingsTracker[999:] if len(TimingStatsHandler.TimingsTracker) > 999 else TimingStatsHandler.TimingsTracker

        if method == 'POST' and uri == '/token':
            category = 'token'
        elif method == 'PUT' and uri.startswith('/submission'):
            category = 'submission'
        elif method == 'POST' and uri == '/wbtip/comments':
            category = 'comment'
        elif method == 'JOB' and uri == 'Delivery':
            category = 'delivery'
        else:
            category = 'uncategorized'

        TimingStatsHandler.TimingsTracker.append({
            'category': category,
            'method': method,
            'uri': uri,
            'start_time': start_time,
            'run_time': run_time
        })

    def get(self):
        csv = "category,method,uri,start_time,run_time\n"
        for measure in TimingStatsHandler.TimingsTracker:
            csv += "%s,%s,%s,%s,%d\n" % (measure['category'],
                                         measure['method'],
                                         measure['uri'],
                                         measure['start_time'],
                                         measure['run_time'])
        self.write(csv)
