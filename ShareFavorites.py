# -*- coding: utf-8 -*-
#Copyright (c) 2013 Walter Bender

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with this library; if not, write to the Free Software
# Foundation, 51 Franklin Street, Suite 500 Boston, MA 02110-1335 USA


from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import GObject

import os

from sugar3.activity import activity
from sugar3 import profile
from sugar3 import env

from sugar3.activity.widgets import ActivityToolbarButton
from sugar3.activity.widgets import StopButton

from sugar3.graphics.toolbarbox import ToolbarBox
from sugar3.graphics.alert import NotifyAlert, Alert
from sugar3.graphics.icon import Icon, CanvasIcon
from sugar3.graphics import style
from sugar3.graphics.xocolor import XoColor

from jarabe.model import bundleregistry
from jarabe.model.session import get_session_manager

from toolbar_utils import separator_factory
from gettext import gettext as _

import logging
_logger = logging.getLogger("share-favorites")

import json

import telepathy
from dbus.service import signal
from dbus.gobject_service import ExportedGObject
from sugar3.presence import presenceservice
from sugar3.presence.tubeconn import TubeConnection


SERVICE = 'org.sugarlabs.ShareFavorites'
IFACE = SERVICE
PATH = '/org/sugarlabs/ShareFavorites'


class ShareFavorites(activity.Activity):
    ''' Share desktop favorites '''

    def __init__(self, handle):
        ''' Initialize the toolbars and the work surface '''
        super(ShareFavorites, self).__init__(handle)

        self.initiating = None  # sharing (True) or joining (False)
        self._old_cursor = self.get_window().get_cursor()
        self._buddy_count = 0
        self._hboxes = []

        self._setup_toolbars()
        self._setup_canvas()
        self._setup_presence_service()

        # Start with the Neighborhood View icon in the center of the screen
        self._icon = self._create_icon('#FFFFFF,#000000',
                                       name='zoom-neighborhood')
        self._icon.show()
        self._vbox.pack_end(self._icon, True, True, 0)
        self._vbox.show()

    def _setup_canvas(self):
        ''' Create a canvas '''
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.show()

        self.set_canvas(sw)

        self._vbox = Gtk.VBox(False, 0)
        self._vbox.set_size_request(Gdk.Screen.width(),
                                    Gdk.Screen.height() - style.GRID_CELL_SIZE)
        sw.add(self._vbox)
        self._vbox.show()

    def _show_bundle_icon(self, icon_path):
        if self._icon in self._vbox:
            self._vbox.remove(self._icon)
        self._icon = CanvasIcon(file_name=icon_path,
                                xo_color=XoColor('#000000,#FFFFFF'),
                                pixel_size=style.LARGE_ICON_SIZE)
        self._vbox.pack_end(self._icon, True, True, 0)
        self._icon.show()

    def _animate_icons(self, icon_paths):
        if len(icon_paths) == 0:
            # Until we set up a dbus method to update the views, we need
            # to restart
            # self._restart_alert()
            self._notify_alert(title=_('Warning'),
                               msg=_('Changes require restart'))
            return
        else:
            self._show_bundle_icon(icon_paths[-1])
            icon_paths.remove(icon_paths[-1])
            GObject.timeout_add(500, self._animate_icons, icon_paths)

    def _create_icon(self, color, name='computer-xo'):
        return CanvasIcon(icon_name=name,
                          xo_color=XoColor(color),
                          pixel_size=style.STANDARD_ICON_SIZE)

    def _add_buddy(self, icon, nick):
        ''' Add buddies to sharer's canavs to show whom has shared data '''

        n = int(Gdk.Screen.width() / (3 * style.STANDARD_ICON_SIZE))
        if self._buddy_count % n == 0:
            self._hboxes.append(Gtk.HBox(False, 0))
            self._hboxes[-1].show()
            self._vbox.pack_end(self._hboxes[-1], True, False, 0)

        self._buddy_count += 1

        vbox = Gtk.VBox(False, 0)
        label = Gtk.Label(nick)
        vbox.pack_start(icon, False, False, 0)
        vbox.pack_start(label, False, False, 10)
        icon.show()
        label.show()
        vbox.show()
        self._hboxes[-1].pack_end(vbox, True, False, 0)

    def _setup_toolbars(self):
        ''' Setup the toolbars. '''
        self.max_participants = 5

        toolbox = ToolbarBox()

        # Activity toolbar
        activity_button_toolbar = ActivityToolbarButton(self)

        toolbox.toolbar.insert(activity_button_toolbar, 0)
        activity_button_toolbar.show()

        self.set_toolbar_box(toolbox)
        toolbox.show()
        self.toolbar = toolbox.toolbar

        separator_factory(toolbox.toolbar, True, False)

        stop_button = StopButton(self)
        stop_button.props.accelerator = '<Ctrl>q'
        toolbox.toolbar.insert(stop_button, -1)
        stop_button.show()

        toolbox.toolbar.show_all()

    def _restore_cursor(self):
        ''' No longer waiting, so restore standard cursor. '''
        if not hasattr(self, 'get_window'):
            return
        self.get_window().set_cursor(self._old_cursor)

    def _waiting_cursor(self):
        ''' Waiting, so set watch cursor. '''
        if not hasattr(self, 'get_window'):
            return
        self._old_cursor = self.get_window().get_cursor()
        self.get_window().set_cursor(Gdk.Cursor.new(Gdk.CursorType.WATCH))

    def _restart_alert(self):
        alert = Alert()
        alert.props.title = _('Warning')
        alert.props.msg = _('Changes require restart')

        icon = Icon(icon_name='dialog-cancel')
        alert.add_button(Gtk.ResponseType.CANCEL, _('Cancel changes'), icon)
        icon.show()

        icon = Icon(icon_name='dialog-ok')
        alert.add_button(Gtk.ResponseType.ACCEPT, _('Later'), icon)
        icon.show()

        icon = Icon(icon_name='system-restart')
        alert.add_button(Gtk.ResponseType.APPLY, _('Restart now'), icon)
        icon.show()

        alert.connect('response', self.__response_cb)
        self.add_alert(alert)
        alert.show()

    def __response_cb(self, alert, response_id):
        self.remove_alert(alert)

        if response_id is Gtk.ResponseType.CANCEL:
            pass
        elif response_id is Gtk.ResponseType.ACCEPT:
            pass
        elif response_id is Gtk.ResponseType.APPLY:
            session_manager = get_session_manager()
            session_manager.logout()

    def _notify_alert(self, title='', msg='', action=None):
        ''' Notify user when xfer is completed '''

        def _notification_alert_response_cb(alert, response_id, self, action):
            self.remove_alert(alert)
            if action is not None:
                action()

        alert = NotifyAlert()
        alert.props.title = title
        alert.connect('response', _notification_alert_response_cb, self,
                      action)
        alert.props.msg = msg
        self.add_alert(alert)
        alert.show()

    # When favorites are shared, the sharer sends out list; joiners
    # receive the list.
    def _read_favorites(self):
        favorites_path = os.path.join(env.get_profile_path(),
                                      'favorite_activities')
        favorites = json.load(open(favorites_path))
        return favorites

    def _save_favorites(self, favorites):
        favorites_path = os.path.join(env.get_profile_path(),
                                      'favorite_activities')
        json.dump(favorites, open(favorites_path, 'w'), indent=1)

    def _unset_favorites(self, favorites_data):
        favorites = favorites_data['favorites']
        keys = favorites.keys()
        registry = bundleregistry.get_registry()
        for bundle in keys:
            bundle = bundle.encode('ascii', 'replace')
            bundle_id, version = bundle.split(' ')
            logging.debug('removing %s' % (bundle_id))
            registry.set_bundle_favorite(bundle_id, version, False)

    def _set_favorites(self, data):
        # data is coming over tube, so it needs to be decoded
        favorites_data = json.loads(data)
        favorites = favorites_data['favorites']
        keys = favorites.keys()
        registry = bundleregistry.get_registry()
        icon_paths = []
        for bundle in keys:
            bundle = bundle.encode('ascii', 'replace')
            bundle_id, version = bundle.split(' ')
            try:
                logging.debug('adding %s' % (bundle_id))
                registry.set_bundle_favorite(bundle_id, version, True)
                icon_path = registry.get_bundle(bundle_id).get_icon()
                if os.path.exists(icon_path):
                    icon_paths.append(icon_path)
            except:
                logging.debug('bundle %s version %s not available' %
                              (bundle_id, version))

        self._animate_icons(icon_paths)

    def _setup_presence_service(self):
        ''' Setup the Presence Service. '''
        self.pservice = presenceservice.get_instance()

        owner = self.pservice.get_owner()
        self.owner = owner
        self.buddies = [owner]
        self._share = ''
        self.connect('shared', self._shared_cb)
        self.connect('joined', self._joined_cb)

    def _shared_cb(self, activity):
        ''' Either set up initial share...'''
        if self.get_shared_activity() is None:
            _logger.error('Failed to share or join activity ... \
                shared_activity is null in _shared_cb()')
            return

        self.initiating = True
        self.waiting = False
        _logger.debug('I am sharing...')

        self.conn = self.shared_activity.telepathy_conn
        self.tubes_chan = self.shared_activity.telepathy_tubes_chan
        self.text_chan = self.shared_activity.telepathy_text_chan

        self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].connect_to_signal(
            'NewTube', self._new_tube_cb)

        _logger.debug('This is my activity: making a tube...')
        self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].OfferDBusTube(
            SERVICE, {})

        if self._icon in self._vbox:
            self._vbox.remove(self._icon)

    def _joined_cb(self, activity):
        ''' ...or join an exisiting share. '''
        if self.get_shared_activity() is None:
            _logger.error('Failed to share or join activity ... \
                shared_activity is null in _shared_cb()')
            return

        self.initiating = False
        _logger.debug('I joined a shared activity.')

        self.conn = self.shared_activity.telepathy_conn
        self.tubes_chan = self.shared_activity.telepathy_tubes_chan
        self.text_chan = self.shared_activity.telepathy_text_chan

        self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].connect_to_signal(
            'NewTube', self._new_tube_cb)

        _logger.debug('I am joining an activity: waiting for a tube...')
        self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].ListTubes(
            reply_handler=self._list_tubes_reply_cb,
            error_handler=self._list_tubes_error_cb)

        self.waiting = True
        self._waiting_cursor()

        if self._icon in self._vbox:
            self._vbox.remove(self._icon)

        self._notify_alert(title=_('Share Favorites'),
                           msg=_('Downloading favorites... please wait.'),
                           action=self._share_favorites)

    def _list_tubes_reply_cb(self, tubes):
        ''' Reply to a list request. '''
        for tube_info in tubes:
            self._new_tube_cb(*tube_info)

    def _list_tubes_error_cb(self, e):
        ''' Log errors. '''
        _logger.error('ListTubes() failed: %s', e)

    def _new_tube_cb(self, id, initiator, type, service, params, state):
        ''' Create a new tube. '''
        _logger.debug('New tube: ID=%d initator=%d type=%d service=%s '
                      'params=%r state=%d', id, initiator, type, service,
                      params, state)

        if (type == telepathy.TUBE_TYPE_DBUS and service == SERVICE):
            if state == telepathy.TUBE_STATE_LOCAL_PENDING:
                self.tubes_chan[
                    telepathy.CHANNEL_TYPE_TUBES].AcceptDBusTube(id)

            tube_conn = TubeConnection(
                self.conn,
                self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES],
                id,
                group_iface=self.text_chan[telepathy.CHANNEL_INTERFACE_GROUP])

            self.chattube = ChatTube(tube_conn, self.initiating,
                                     self.event_received_cb)

    def event_received_cb(self, text):
        ''' Data is passed as tuples: cmd:text '''
        dispatch_table = {'F': self._update_favorites,
                          'f': self._share_favorites,
                          }
        _logger.debug('<<< %s' % (text[0]))
        dispatch_table[text[0]](text[2:])

    def _new_join(self, data):
        if self.initiating:
            self._share_favorites()

    def _update_favorites(self, favorites):
        self._set_favorites(favorites)
        self._restore_cursor()

    def _share_favorites(self, data=None):
        logging.debug('SHARE FAVORITES %s' % (str(self.initiating)))
        if self.initiating:
            favorites = self._read_favorites()
            self._send_event('F:%s' % (json.dumps(favorites)))
            data_array = json.loads(data)
            nick = data_array[0]
            colors = data_array[1].encode('ascii', 'replace')
            icon = self._create_icon(colors)
            self._add_buddy(icon, nick)
        else:
            favorites_data = self._read_favorites()
            self._unset_favorites(favorites_data)
            nick = profile.get_nick_name()
            colors = profile.get_color().to_string()
            self._send_event('f:%s' % (json.dumps([nick, colors])))

    def _send_event(self, text):
        ''' Send event through the tube. '''
        if hasattr(self, 'chattube') and self.chattube is not None:
            _logger.debug('>>> %s' % (text[0]))
            self.chattube.SendText(text)


class ChatTube(ExportedGObject):
    ''' Class for setting up tube for sharing '''
    def __init__(self, tube, is_initiator, stack_received_cb):
        super(ChatTube, self).__init__(tube, PATH)
        self.tube = tube
        self.is_initiator = is_initiator  # Are we sharing or joining activity?
        self.stack_received_cb = stack_received_cb
        self.stack = ''

        self.tube.add_signal_receiver(self.send_stack_cb, 'SendText', IFACE,
                                      path=PATH, sender_keyword='sender')

    def send_stack_cb(self, text, sender=None):
        if sender == self.tube.get_unique_name():
            return
        self.stack = text
        self.stack_received_cb(text)

    @signal(dbus_interface=IFACE, signature='s')
    def SendText(self, text):
        self.stack = text
