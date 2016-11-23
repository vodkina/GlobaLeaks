# -*- encoding: utf-8 -*-
import os

from twisted.internet.defer import inlineCallbacks

from globaleaks.tests import helpers

from globaleaks import models
from globaleaks.orm import transact
from globaleaks.jobs import cleaning_sched
from globaleaks.utils.utility import datetime_null
from globaleaks.settings import GLSettings


class TestCleaningSched(helpers.TestGLWithPopulatedDB):
    population_of_submissions = 10

    @transact
    def force_wbtip_expiration(self, store):
        for itip in store.find(models.InternalTip):
            itip.wb_last_access = datetime_null()

    @transact
    def force_itip_expiration(self, store):
        for itip in store.find(models.InternalTip):
            itip.expiration_date = datetime_null()

    @transact
    def check0(self, store):
        self.assertTrue(os.listdir(GLSettings.submission_path) == [])
        self.assertTrue(os.listdir(GLSettings.tmp_upload_path) == [])

        self.db_test_model_count(store, models.InternalTip, 0)
        self.db_test_model_count(store, models.ReceiverTip, 0)
        self.db_test_model_count(store, models.WhistleblowerTip, 0)
        self.db_test_model_count(store, models.InternalFile, 0)
        self.db_test_model_count(store, models.ReceiverFile, 0)
        self.db_test_model_count(store, models.Comment, 0)
        self.db_test_model_count(store, models.Message, 0)
        self.db_test_model_count(store, models.SecureFileDelete, 0)

    @transact
    def check1(self, store):
        self.assertTrue(os.listdir(GLSettings.submission_path) != [])

        self.db_test_model_count(store, models.InternalTip, self.population_of_submissions)
        self.db_test_model_count(store, models.ReceiverTip, self.population_of_recipients * self.population_of_submissions)
        self.db_test_model_count(store, models.WhistleblowerTip, self.population_of_submissions)
        self.db_test_model_count(store, models.InternalFile, self.population_of_attachments * self.population_of_submissions)
        self.db_test_model_count(store, models.ReceiverFile, 0)
        self.db_test_model_count(store, models.Comment, self.population_of_submissions * self.population_of_comments)
        self.db_test_model_count(store, models.Message, self.population_of_submissions * self.population_of_recipients * self.population_of_messages)

    @transact
    def check2(self, store):
        self.assertTrue(os.listdir(GLSettings.submission_path) != [])

        self.db_test_model_count(store, models.InternalTip, self.population_of_submissions)
        self.db_test_model_count(store, models.ReceiverTip, self.population_of_recipients * self.population_of_submissions)
        self.db_test_model_count(store, models.WhistleblowerTip, 0) # Note the diff
        self.db_test_model_count(store, models.InternalFile, self.population_of_attachments * self.population_of_submissions)
        self.db_test_model_count(store, models.ReceiverFile, 0)
        self.db_test_model_count(store, models.Comment, self.population_of_submissions * self.population_of_comments)
        self.db_test_model_count(store, models.Message, self.population_of_submissions * self.population_of_recipients * self.population_of_messages)


    @inlineCallbacks
    def test_submission_life(self):
        # verify that the system starts clean
        yield self.check0()

        yield self.perform_full_submission_actions()

        # verify tip creation
        yield self.check1()

        yield cleaning_sched.CleaningSchedule().run()

        # verify tips survive the scheduler if they are not expired
        yield self.check1()

        yield self.force_wbtip_expiration()

        yield cleaning_sched.CleaningSchedule().run()

        # verify rtips survive the scheduler if the wbtip expires
        yield self.check2()

        yield self.force_itip_expiration()

        yield cleaning_sched.CleaningSchedule().run()

        # verify cascade deletion when tips expire
        yield self.check0()
