# -*- coding: UTF-8
#
#   models/externaltip
#   ******************
#
# implementation of Storm DB side of ReceiverTip and WhistleblowerTip
# and File and Comment tables, all those tables has relationship with
# InternalTip

from storm.twisted.transact import transact
from storm.exceptions import NotOneError
from storm.locals import Int, Pickle, Date, Unicode, RawStr, Bool, DateTime
from storm.locals import Reference

from globaleaks.utils import idops, log, gltime
from globaleaks.models.base import TXModel
from globaleaks.models.receiver import Receiver
from globaleaks.models.internaltip import InternalTip
from globaleaks.rest.errors import TipGusNotFound, TipReceiptNotFound,\
    TipPertinenceExpressed, ReceiverGusNotFound, FileGusNotFound

__all__ = [ 'Folder', 'File', 'Comment', 'ReceiverTip', 'PublicStats', 'WhistleblowerTip' ]

class ReceiverTip(TXModel):
    """
    This is the table keeping track of ALL the receivers activities and
    date in a Tip, Tip core data are stored in StoredTip. The data here
    provide accountability of Receiver accesses, operations, options.
    """
    __storm_table__ = 'receivertips'

    # remind: the previous name of this variable was 'address'
    tip_gus = Unicode(primary=True)

    authoptions = Pickle()

    internaltip_id = Int()
    internaltip = Reference(internaltip_id, InternalTip.id)

    access_counter = Int()
    last_access = DateTime()

    expressed_pertinence = Int()

    notification_date = DateTime()
    notification_mark = Unicode()
        # TODO ENUM 'not notified' 'notified' 'unable to notify' 'notification ignore'

    receiver_gus = Unicode()
    receiver = Reference(receiver_gus, Receiver.receiver_gus)

    # is not a transact operation, is self filling, called by
    # create_receiver_tips
    def initialize(self, selected_it, receiver_subject):

        # XXX TODO log verbose

        self.tip_gus = idops.random_tip_gus()

        self.notification_mark = u'not notified'
        self.notification_date = None

        self.last_access = None
        self.access_counter= 0
        self.expressed_pertinence = 0
        self.authoptions = {}

        # self.receiver_gus = mapped['receiver_gus'] -- fixed during the hackathon with the line below
        self.receiver_gus = receiver_subject.receiver_gus
        self.receiver = receiver_subject

        self.internaltip_id = selected_it.id
        self.internaltip = selected_it


    # XXX this would be moved in the new 'task queue'
    @transact
    def flip_mark(self, tip_gus, newmark):

        notification_markers = [ u'not notified', u'notified', u'unable to notify', u'notification ignored' ]

        if not newmark in notification_markers:
            raise NotImplemented

        store = self.getStore()

        requested_t = store.find(ReceiverTip, ReceiverTip.tip_gus == tip_gus).one()
        requested_t.notification_mark = newmark

    @transact
    def get_all(self):
        """
        this is called only by /admin/overview API
        """
        store = self.getStore()

        all_rt = store.find(ReceiverTip)

        retVal = []
        for single_rt in all_rt:
            retVal.append(single_rt._description_dict())

        return retVal

    # Removed - unused, right ?
    # def receivertip_get_single(self, tip_gus):

    @transact
    def get_single(self, tip_gus):
        """
        This is the method called when a receiver is accessing to Tip. It return
        InternalTip details and update the last_access date.
        """
        store = self.getStore()

        try:
            requested_t = store.find(ReceiverTip, ReceiverTip.tip_gus == tip_gus).one()
        except NotOneError:
            raise TipGusNotFound
        if not requested_t:
            raise TipGusNotFound

        # Access counter is incremented before the data extraction,
        # last_access is incremented after (because the receiver want know
        # if someone has accesses in his place)
        requested_t.access_counter += 1

        # The single tip dump is composed by InternalTip + ReceiverTip details
        tip_details = requested_t.internaltip._description_dict()
        tip_details.update(requested_t._description_dict())

        requested_t.last_access = gltime.utcTimeNow()

        # need to be added the receipt in the message dictionary
        # ad the identifier of the resource is in fact the auth key
        tip_details.update({ 'id' : requested_t.tip_gus })

        return dict(tip_details)


    @transact
    def pertinence_vote(self, tip_gus, vote):
        """
        check if the receiver has already voted. if YES: raise an exception, if NOT
        mark the expressed vote and call the internaltip to register the fact.
        @vote would be True or False, default is "I'm not expressed".

        return the actual vote expressed by all the receivers, to the same iTip.
        """

        store = self.getStore()

        try:
            requested_t = store.find(ReceiverTip, ReceiverTip.tip_gus == tip_gus).one()
        except NotOneError:
            raise TipGusNotFound
        if not requested_t:
            raise TipGusNotFound

        if requested_t.expressed_pertinence:
            raise TipPertinenceExpressed

        # expressed_pertinence has these meanings:
        # 0 = unassigned
        # 1 = negative vote
        # 2 = positive vote
        requested_t.expressed_pertinence = 2 if vote else 1
        requested_t.last_access = gltime.utcTimeNow()

        expressed_t = store.find(ReceiverTip, (ReceiverTip.internaltip_id == requested_t.internaltip_id and ReceiverTip.expressed_pertinence != 0))

        vote_sum = 0
        for et in expressed_t:
            if et.expressed_pertinence == 1:
                vote_sum -= 1
            else:
                vote_sum += 1

        itip_id_copy = requested_t.internaltip_id
        return (itip_id_copy, vote_sum)


    @transact
    def get_sibiligs_by_tip(self, tip_gus):
        """
        @param tip_gus: a valid tip_gus
        @return: a dict composed with:
            {
                'sibilings': [ sibilings_ReceiverTip ],
                'requested': this_ReceiverTip,
                'internaltip': InternalTip
            }
        this function is needed to perform "total delete" feature, return the
        list of all the ReceiverTip descending from the same InternalTip.

        This method is called by internal routine, not by Receiver
        """
        store = self.getStore()

        try:
            requested_t = store.find(ReceiverTip, ReceiverTip.tip_gus == unicode(tip_gus)).one()
        except NotOneError:
            raise TipGusNotFound
        if not requested_t:
            raise TipGusNotFound

        sibilings = store.find(ReceiverTip, ReceiverTip.internaltip_id == requested_t.internaltip_id)

        sibilings_description = []
        for s in sibilings:

            if s.tip_gus == requested_t.tip_gus:
                continue

            single_description = s._description_dict()
            sibilings_description.append(single_description)

        requested_description = requested_t._description_dict()
        internal_description = requested_t.internaltip._description_dict()

        retDict = {
                'sibilings': sibilings_description,
                'requested': requested_description,
                'internaltip' : internal_description
                }
        return retDict


    @transact
    def get_receivers_by_tip(self, tip_gus):
        """
        @param tip_gus: a valid tip gus
        @return: a list composed with:
            {
                'others' : [ other_Receivers ],
                'actor': this_Receiver,
                'mapped' [ mapped_receivers_in_itip]
            }
        The 'mapped' value can be ignored.

        The structured contain a complete receivers description, all the Receiver
        description working on the same InternalTip.
        """
        store = self.getStore()

        try:
            requested_t = store.find(ReceiverTip, ReceiverTip.tip_gus == unicode(tip_gus)).one()
        except NotOneError:
            raise TipGusNotFound
        if not requested_t:
            raise TipGusNotFound

        sibilings_tips = store.find(ReceiverTip, ReceiverTip.internaltip_id == requested_t.internaltip_id)

        other_receivers = []
        for s in sibilings_tips:

            if s.tip_gus == requested_t.tip_gus:
                continue

            receiver_desc = s.receiver._description_dict()
            other_receivers.append(receiver_desc)

        requester_receiver = requested_t.receiver._description_dict()
        internaltip_receivers =  list(requested_t.internaltip.receivers)

        retDict = { 'others': other_receivers,
                    'actor' : requester_receiver,
                    'mapped' : internaltip_receivers
                  }
        return retDict


    @transact
    def get_tips_by_tip(self, tip_gus):
        """
        @param tip_gus: a valid tip gus
        @return: a dict composed by:
            {
            'othertips': other_Tips_of_the_same_Receiver
            'request' : requested RecveiverTip
            }
        containing a complete ReceiverTip description.
        """
        store = self.getStore()

        try:
            requested_t = store.find(ReceiverTip, ReceiverTip.tip_gus == unicode(tip_gus)).one()
        except NotOneError:
            raise TipGusNotFound
        if not requested_t:
            raise TipGusNotFound

        other_tips = store.find(ReceiverTip, ReceiverTip.receiver_gus == requested_t.receiver_gus)

        tips = []
        for t in other_tips:

            if t.tip_gus == tip_gus:
                continue

            tips.append(t._description_dict())

        requested_tip = requested_t._description_dict()

        retDict = { 'othertips' : tips, 'request' : requested_tip }
        return retDict


    @transact
    def get_tips_by_receiver(self, receiver_gus):
        """
        @param receiver_gus: A receiver_gus
        @return: a list of ReceiverTip dict associated with receiver_gus
        """

        store = self.getStore()

        related_t = store.find(ReceiverTip, ReceiverTip.receiver_gus == unicode(receiver_gus))

        related_list = []
        for t in related_t:
            related_list.append(t._description_dict())

        return related_list


    @transact
    def get_tips_by_notification_mark(self, marker):
        """
        @param marker: one valid marker
        @return: a list of [ ReceiverTip ]
        """
        notification_markers = [ u'not notified', u'notified', u'unable to notify', u'notification ignored' ]
        store = self.getStore()

        if unicode(marker) not in notification_markers:
            raise NotImplemented

        marked_t = store.find(ReceiverTip, ReceiverTip.notification_mark == unicode(marker))

        list_by_mark = []
        for t in marked_t:
            list_by_mark.append(t._description_dict())

        return list_by_mark


    @transact
    def get_tips_by_context(self, context_gus):
        """
        This function works as observer of all the Tips related to a specific Context,
        This method need to stay in externaltip.py, but for the Storm dependencies
        I've to choose just one Class in which put this function.

        @param context_gus:
        @return: a dict with the keys:
            'internaltip' : InternalTip,
            'receivertip' : [ ReceiverTips]
            'whistleblowertip' : [ WhistleBlowerTip ]
            'comments' : [ Comment ]
            'files' : [ Files ]

        Remind: this operation can be avoided if DELETE ON CASCADE would be
        stable and packed into Storm, but I've see works in this topic half month ago,
        now is the 28 Dec 2012, AKA Year 0 month 0 day 7 AMF (after the MayaFailure )
        """

        retList = []

        store = self.getStore()
        itip_related = store.find(InternalTip, InternalTip.context_gus == unicode(context_gus))

        for itip in itip_related:

            receiverD = []
            wbtD = []
            fileD = []
            commentD = []

            itipD = itip._description_dict()

            rtips = store.find(ReceiverTip, ReceiverTip.internaltip_id == itip.id )
            for r in rtips:
                receiverD.append(r._description_dict())

            wtips = store.find(WhistleblowerTip, WhistleblowerTip.internaltip_id == itip.id )
            for w in wtips:
                wbtD.append(w._description_dict())

            comments = store.find(Comment, Comment.internaltip_id == itip.id )
            for c in comments:
                commentD.append(c._description_dict())

            files = store.find(File, File.internaltip_id == itip.id )
            for f in files:
                fileD.append(f._description_dict())

            internaltip_related =  {
                "internaltip" : itipD,
                "receivertip" : receiverD,
                "whistleblowertip" : wbtD,
                "comments" : commentD,
                "files" : fileD
            }

            # TODO Applicative log
            print "Cascade remove of: itip", itipD['internaltip_id'],\
                "rtip", len(receiverD), "comments", len(commentD), "files", len(fileD)
            retList.append(internaltip_related)

        return retList

    @transact
    def personal_delete(self, tip_gus):
        """
        remove the Receiver Tip access.
        Happen when a Receiver choose to remove himself from a single Tip analysis,
            more massive form of Tip remove, are handled by the 'massive_delete' below
        Is called by handler, handler checks and align eventually references
        """

        store = self.getStore()

        try:
            requested_t = store.find(ReceiverTip, ReceiverTip.tip_gus == unicode(tip_gus)).one()
        except NotOneError:
            raise TipGusNotFound
        if not requested_t:
            raise TipGusNotFound

        store.remove(requested_t)

    @transact
    def massive_delete(self, internaltip_id):
        """
        remove the Receiver Tip access.
        Happen when a when a Context is deleted
            when an InternalTip is deleted
            when an InternalTip is expired
        Is called by handler, handler checks and align eventually references
        """

        store = self.getStore()
        # Sadly the matching query can't be used in store.remove()
        related_tips = store.find(ReceiverTip, ReceiverTip.internaltip_id == int(internaltip_id))
        for single_tip in related_tips:
            store.remove(single_tip)


    # This method is separated by initialize routine, because the tip creation
    # event can be exported/overriden/implemented by a plugin in a certain future.
    # like notification or delivery, it has a dedicated event in the scheduler, and
    # is called by TipSched
    @transact
    def create_receiver_tips(self, internaltip_id, tier):
        """
        act on self. create the ReceiverTip based on InternalTip.receivers
        """
        store = self.getStore()

        selected_it = store.find(InternalTip, InternalTip.id == int(internaltip_id)).one()

        created_counter = 0
        for choosen_r in selected_it.receivers:

            try:
                receiver_subject = store.find(Receiver, Receiver.receiver_gus == unicode(choosen_r)).one()
            except NotOneError:
                # This would happen only if a receiver has been removed between the
                # submission creation and the tip creation.
                # TODO administrator system error
                continue
            if receiver_subject is None:
                # TODO administrator system error
                continue

            if not receiver_subject.receiver_level == tier:
                continue

            new_receiver_tip =  ReceiverTip()

            # this initialize the Tip, (with a "not notified" status)
            new_receiver_tip.initialize(selected_it, receiver_subject)
            store.add(new_receiver_tip)
            created_counter += 1

        return created_counter


    # called by a transact operation,dump the ReceiverTip (without the Tip details,
    # they stay in InternalTip)
    def _description_dict(self):

        descriptionDict = {
            'internaltip_id' : unicode(self.internaltip_id),
            'tip_gus' : unicode(self.tip_gus),
            'notification_mark' : bool(self.notification_mark),
            'notification_date' : unicode(gltime.prettyDateTime(self.notification_date)),
            'last_access' : unicode(gltime.prettyDateTime(self.last_access)) if self.last_access else u'Never',
            'access_counter' : unicode(self.access_counter),
            'expressed_pertinence': unicode(self.expressed_pertinence),
            'receiver_gus' : unicode(self.receiver_gus),
            'receiver_name' : unicode(self.receiver.name),
            'authoptions' : dict(self.authoptions)
        }
        return dict(descriptionDict)


class WhistleblowerTip(TXModel):
    """
    WhisteleblowerTip is intended, at the moment, to provide a whistleblower access to the Tip.
    differently from the ReceiverTips, has a secret and/or authentication checks, has
    different capabilities, like: cannot not download, cannot express pertinence, and
    other operation permitted to the WB shall be configured by the Admin.
    """
    __storm_table__ = 'whistleblowertips'

    receipt = Unicode(primary=True)
        # receipt can be proposed by whistleblower, the globaleaks node *always* perform a
        # little modification on that (because NEED to be unique), before returining 
        # back. having a tip_gus was no more useful in whistleblower tip, has start to 
        # sound as a limit.

    authoptions = Pickle()
        # this would be choosen by the WB when submission is finalized, in example,
        # WB should decide to comeback only using the same client (= the same private key)
        # and a signature schema can be used. other options would be available,
        # they are not yet specified.

    last_access = DateTime()

    internaltip_id = Int()
    internaltip = Reference(internaltip_id, InternalTip.id)


    @transact
    def get_single(self, receipt):

        store = self.getStore()

        try:
            requested_t = store.find(WhistleblowerTip, WhistleblowerTip.receipt == unicode(receipt)).one()
        except NotOneError:
            raise TipReceiptNotFound
        if not requested_t:
            raise TipReceiptNotFound

        wb_tip_dict = requested_t.internaltip._description_dict()
        requested_t.last_access = gltime.utcTimeNow()

        complete_tip_dict = wb_tip_dict

        # need to be added the receipt in the message dictionary
        # ad the identifier of the resource is in fact the auth key
        complete_tip_dict.update({ 'id' : requested_t.receipt })

        return dict(complete_tip_dict)

    @transact
    def get_all(self):
        """
        This is called by API /admin/overview only
        """

        store = self.getStore()

        all_wt = store.find(WhistleblowerTip)

        retVal = []
        for single_wt in all_wt:
            retVal.append(single_wt._description_dict())

        return retVal

    @transact
    def delete_access(self, receipt):
        """
        a WhistleBlower can delete is own access, removing Whistleblower tip and invalidating the receipt
        """
        # XXX Log + system comment need to be called by handler, not by model
        store = self.getStore()

        try:
            requested_t = store.find(WhistleblowerTip, WhistleblowerTip.receipt == unicode(receipt)).one()
        except NotOneError:
            raise TipReceiptNotFound
        if not requested_t:
            raise TipReceiptNotFound

        store.remove(requested_t)

    @transact
    def delete_access_by_itip(self, internaltip_id):
        """
        Called by cascade delete from DELETE admin/context, or by Tip (total_delete)
        """

        store = self.getStore()
        selected = store.find(WhistleblowerTip, WhistleblowerTip.internaltip_id == int(internaltip_id))
        for single_tip in selected:
            store.remove(single_tip)


    # called by a transact operation, dump the WhistleBlower Tip
    def _description_dict(self):

        descriptionDict = {
            'last_access' :  unicode(gltime.prettyDateTime(self.last_access)),
            'internaltip_id' : int(self.internaltip_id),
            'authoption' : dict(self.authoptions) if self.authoptions else {},
            'receipt' : unicode(self.receipt)
        }
        return dict(descriptionDict)


class File(TXModel):
    """
    The file are *stored* here, along with their properties
    """
    __storm_table__ = 'files'

    file_gus = Unicode(primary=True)

    name = Unicode()

    content = RawStr()
    shasum = RawStr()

    completed = Bool()

    description = Unicode()
    content_type = Unicode()
    mark = Unicode()
        # 'not processed': 'ready' 'blocked'
        # 'delivered' 'unable to be delivered', 'stored'
        # TODO with Task table would be possibile handle
        # the different behaviour for every receiver, ATM not!

    size = Int()

    metadata_cleaned = Bool()
    uploaded_date = Date()

    internaltip_id = Int()
    internaltip = Reference(internaltip_id, InternalTip.id)


    @transact
    def new(self):
        pass

    @transact
    def get_file_by_maker(self, marker):
        """
        @return: all the files matching with the requested
            marked, between this list of option:
        marker_avail = [ u'not processed', u'ready', u'blocked'  ]

        'delivered' and 'stored' depends from the single Receiver
        TODO handle that with the schedule queue
        """

        store = self.getStore()

        if marker == u'not processed':
            req_fi = store.find(File, File.mark == u'new')
        elif marker == u'ready':
            req_fi = store.find(File, File.mark == u'ready')
        elif marker == u'blocked':
            req_fi = store.find(File, File.mark == u'blocked')
        else:
            raise NotImplemented

        retVal = []
        for single_file in req_fi:
            retVal.append(single_file._description_dict())

        return retVal


    @transact
    def get_files_by_itip(self, internaltip_id):

        store = self.getStore()

        referenced_f = store.find(File, File.internaltip_id == int(internaltip_id))

        referenced_files = []

        for single_file in referenced_f:
            referenced_files.append(single_file._description_dict())

        return referenced_files


    @transact
    def delete_file_by_itip(self, internaltip_id):

        store = self.getStore()

        referenced_f = store.find(File, File.internaltip_id == int(internaltip_id))

        counter_test = 0
        for single_f in referenced_f:
            counter_test += 1
            store.remove(single_f)

        return counter_test


    @transact
    def get_all(self):

        store = self.getStore()

        files = store.find(File)

        all_files = []

        for single_file in files:
            all_files.append(single_file._description_dict())

        return all_files


    @transact
    def get_single(self, file_gus):

        store = self.getStore()

        try:
            filelookedat = store.find(File, File.file_gus ==file_gus).one()
        except NotOneError:
            store.close()
            raise FileGusNotFound
        if not filelookedat:
            store.close()
            raise FileGusNotFound

        return filelookedat._description_dict()

    def _description_dict(self):

        # Note: the content is not serialized
        descriptionDict = {
            'size' : self.size,
            'file_gus' : self.file_gus,
            'content_type' : self.content_type,
            'name' : self.name,
            'description' : self.description,
            'uploaded_date': gltime.prettyDateTime(self.uploaded_date),
            'completed' : self.completed,
            'metadata_cleaned' : self.metadata_cleaned

        }
        return dict(descriptionDict)


class Comment(TXModel):
    """
    This table handle the, 311 americano, remind.
    This table handle the comment collection, has an InternalTip referenced
    """
    __storm_table__ = 'comments'

    id = Int(primary=True)

    internaltip_id = Int()
    internaltip = Reference(internaltip_id, InternalTip.id)

    creation_time = DateTime()
    source = Unicode()
    content = Unicode()
    author_gus = Unicode()
    notification_mark = Unicode()

    @transact
    def add_comment(self, itip_id, comment, source, author_gus=None):
        """
        @param itip_id: InternalTip.id of reference, need to be addressed
        @param comment: the unicode text expected to be recorded
        @param source: the source kind of the comment (receiver, wb, system)
        @param name: the Comment author name to be show and recorded, can be absent if source is enough
        @return: None
        """

        if not source in [ u'receiver', u'whistleblower', u'system' ]:
            raise NotImplemented

        store = self.getStore()

        try:
            itip = store.find(InternalTip, InternalTip.id == int(itip_id)).one()
        except NotOneError:
            # This can't actually happen
            raise Exception
        if itip is None:
            # This can't actually happen
            raise Exception

        # this approach is a little different from the other classes in ExternalTip
        # they use a new Object() in the caller method, and then Object.initialize
        # to fill with data.
        newcomment = Comment()

        newcomment.creation_time = gltime.utcTimeNow()
        newcomment.source = source
        newcomment.content = comment
        newcomment.author_gus = author_gus
        newcomment.internaltip = itip
        newcomment.internaltip_id = int(itip_id)

        # TODO |need to be reeingineered, only with the queue task
        # TODO |manager can be digest and track the single notification.
        # TODO |
        newcomment.notification_mark = u'not notified'
        store.add(newcomment)

        return newcomment._description_dict()


    @transact
    def flip_mark(self, comment_id, newmark):


        notification_markers = [ u'not notified', u'notified', u'unable to notify', u'notification ignored' ]

        if not newmark in notification_markers:
            raise NotImplemented

        store = self.getStore()

        requested_c = store.find(Comment, Comment.id  == comment_id).one()
        requested_c.notification_mark = newmark


    @transact
    def delete_comment_by_itip(self, internaltip_id):

        store = self.getStore()

        comments_selected = store.find(Comment, Comment.internaltip_id ==  int(internaltip_id))

        counter_test = 0
        for single_c in comments_selected:
            counter_test += 1
            store.remove(single_c)

        return counter_test


    @transact
    # XXX part of the schedule object refactor in TODO
    def get_comment_by_mark(self, marker):

        store = self.getStore()

        notification_markers = [ u'not notified', u'notified', u'unable to notify', u'notification ignored' ]
        if not marker in notification_markers:
            raise NotImplemented

        marked_comments = store.find(Comment, Comment.notification_mark == marker)

        retVal = []
        for single_comment in marked_comments:
            retVal.append(single_comment._description_dict())

        return retVal


    @transact
    def get_all(self):
        """
        This is called by API /admin/overview only
        """
        store = self.getStore()

        comments = store.find(Comment)

        retVal = []
        for single_c in comments:
            retVal.append(single_c._description_dict())

        return retVal

    def _description_dict(self):

        descriptionDict = {
            'comment_id' : unicode(self.id),
            'source' : unicode(self.source),
            'content' : unicode(self.content),
            'author_gus' : unicode(self.author_gus),
            'notification_mark': bool(self.notification_mark),
            'internaltip_id' : int(self.internaltip_id),
            'creation_time' : unicode(gltime.prettyDateTime(self.creation_time))
        }
        return dict(descriptionDict)




