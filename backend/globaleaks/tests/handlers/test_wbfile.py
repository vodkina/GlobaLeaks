# -*- coding: utf-8 -*-
import json

from twisted.internet.defer import inlineCallbacks
from globaleaks.tests import helpers
from globaleaks.handlers import wbtip, rtip

class TestWhistleblowerFileWorkFlow(helpers.TestHandlerWithPopulatedDB):
    _handler = None

    @inlineCallbacks
    def test_get(self):
        yield self.perform_full_submission_actions()

        self._handler = rtip.WhistleblowerFileHandler
        rtips_desc = yield self.get_rtips()
        for rtip_desc in rtips_desc:
            handler = self.request(role='receiver', user_id = rtip_desc['receiver_id'])
            yield handler.post(rtip_desc['id'])

        self._handler = wbtip.WBTipWBFileInstanceHandler
        wbtips_desc = yield self.get_wbtips()
        for wbtip_desc in wbtips_desc:
            wbfiles_desc = yield self.get_wbfiles(wbtip_desc['id'])
            for wbfile_desc in wbfiles_desc:
                # whistleblower file download
                handler = self.request(role='whistleblower', user_id = wbtip_desc['id'])
                yield handler.get(wbfile_desc['id'])

        self._handler = rtip.RTipWBFileInstanceHandler
        rtips_desc = yield self.get_rtips()
        deleted_wbfiles_ids = []
        for rtip_desc in rtips_desc:
            for wbfile_desc in rtip_desc['wbfiles']:
                if wbfile_desc['id'] in deleted_wbfiles_ids:
                    continue

                self.assertEqual(wbfile_desc['description'], 'description')

                handler = self.request(role='receiver', user_id = rtip_desc['receiver_id'])

                # receiver file download
                yield handler.get(wbfile_desc['id'])

                # receiver file deletion
                yield handler.delete(wbfile_desc['id'])
                deleted_wbfiles_ids.append(wbfile_desc['id'])

        # check the file are affectively not there anymore
        rtips_desc = yield self.get_rtips()
        for rtip_desc in rtips_desc:
            self.assertEqual(len(rtip_desc['wbfiles']), 0)
