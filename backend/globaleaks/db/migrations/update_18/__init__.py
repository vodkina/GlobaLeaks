# -*- encoding: utf-8 -*-

"""
  Changes
    - node: allow_iframes_inclusion

"""

from storm.locals import Int, Bool, Unicode, DateTime, JSON
from globaleaks.db.migrations.update import MigrationBase
from globaleaks.models import ModelWithID


class Node_v_17(ModelWithID):
    __storm_table__ = 'node'
    name = Unicode()
    public_site = Unicode()
    hidden_service = Unicode()
    email = Unicode()
    receipt_salt = Unicode()
    last_update = DateTime()
    receipt_regexp = Unicode()
    languages_enabled = JSON()
    default_language = Unicode()
    default_timezone = Int()
    description = JSON()
    presentation = JSON()
    footer = JSON()
    security_awareness_title = JSON()
    security_awareness_text = JSON()
    stats_update_time = Int()
    maximum_namesize = Int()
    maximum_textsize = Int()
    maximum_filesize = Int()
    tor2web_admin = Bool()
    tor2web_submission = Bool()
    tor2web_receiver = Bool()
    tor2web_unauth = Bool()
    allow_unencrypted = Bool()
    postpone_superpower = Bool()
    can_delete_submission = Bool()
    ahmia = Bool()
    wizard_done = Bool()
    disable_privacy_badge = Bool()
    disable_security_awareness_badge = Bool()
    disable_security_awareness_questions = Bool()
    whistleblowing_question = JSON()
    whistleblowing_button = JSON()
    enable_custom_privacy_badge = Bool()
    custom_privacy_badge_tbb = JSON()
    custom_privacy_badge_tor = JSON()
    custom_privacy_badge_none = JSON()
    header_title_homepage = JSON()
    header_title_submissionpage = JSON()
    landing_page = Unicode()
    exception_email = Unicode()


class MigrationScript(MigrationBase):
    def migrate_Node(self):
        old_node = self.store_old.find(self.model_from['Node']).one()
        new_node = self.model_to['Node']()

        for _, v in new_node._storm_columns.iteritems():
            if v.name == 'allow_iframes_inclusion':
                new_node.allow_iframes_inclusion = False
                continue

            setattr(new_node, v.name, getattr(old_node, v.name))

        self.store_new.add(new_node)
