#------------------------------------------------------------------------------
# Copyright (c) 2013, Nucleic Development Team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file COPYING.txt, distributed with this software.
#------------------------------------------------------------------------------
from PyQt4.QtCore import Qt, QRect, QObject
from PyQt4.QtGui import QApplication

from atom.api import Atom, Typed, List

from enaml.layout.dock_layout import docklayout, dockarea, dockitem

from .dock_overlay import DockOverlay
from .event_types import DockAreaContentsChanged
from .layout_handling import (
    build_layout, save_layout, layout_hit_test, plug_frame,
)
from .q_dock_area import QDockArea
from .q_dock_container import QDockContainer
from .q_dock_window import QDockWindow
from .q_guide_rose import QGuideRose


def ensure_on_screen(rect):
    """ Ensure that the given rect is contained on screen.

    If the origin of the rect is not contained within the closest
    desktop screen, the rect will be moved so that it is fully on the
    closest screen. If the rect is larger than the closest screen, the
    origin will never be less than the screen origin.

    Parameters
    ----------
    rect : QRect
        The geometry rect of interest.

    """
    d = QApplication.desktop()
    pos = rect.topLeft()
    drect = d.screenGeometry(pos)
    if not drect.contains(pos):
        x = pos.x()
        if x < drect.x() or x > drect.right():
            dw = drect.width() - rect.width()
            x = max(drect.x(), drect.x() + dw)
        y = pos.y()
        if x < drect.top() or y > drect.bottom():
            dh = drect.height() - rect.height()
            y = max(drect.y(), drect.y() + dh)
        rect = QRect(x, y, rect.width(), rect.height())
    return rect


class QDockAreaFilter(QObject):
    """ An event filter to listen for content changes in a dock area.

    """
    def eventFilter(self, obj, event):
        """ Filter the events for dock area.

        """
        if event.type() == DockAreaContentsChanged:
            self.processArea(obj)
        return False

    def processArea(self, area):
        """ Process the contents change of a dock area.

        This will close the dock window if there is only one remaining
        container in the dock area.

        Parameters
        ----------
        area : QDockArea
            The dock area whose contents have changed.

        """
        parent = area.parent()
        if isinstance(parent, QDockWindow):
            widget = area.layoutWidget()
            if widget is None or isinstance(widget, QDockContainer):
                if parent.isMaximized():
                    parent.showNormal()
                geo = parent.geometry()
                area.setLayoutWidget(None)
                if widget is not None:
                    widget.float()
                    widget.setGeometry(geo)
                    attr = Qt.WA_ShowWithoutActivating
                    old = widget.testAttribute(attr)
                    widget.setAttribute(attr, True)
                    widget.show()
                    widget.setAttribute(attr, old)
                    manager = widget.manager()
                    # Stack the last widget under the toplevel frame.
                    if manager is not None:
                        frames = manager.dock_frames
                        frames.remove(widget)
                        frames.insert(-1, widget)
                parent.destroy()


class DockManager(Atom):
    """ A class which manages the docking behavior of a dock area.

    """
    #: The handler which holds the primary dock area.
    dock_area = Typed(QDockArea)

    #: The overlay used when hovering over a dock area.
    overlay = Typed(DockOverlay, ())

    #: The dock area filter installed on floating dock windows.
    area_filter = Typed(QDockAreaFilter, ())

    #: The list of QDockFrame instances maintained by the manager. The
    #: QDockFrame class maintains this list in proper Z-order.
    dock_frames = List()

    #: The set of QDockItem instances added to the manager.
    dock_items = Typed(set, ())

    def __init__(self, dock_area):
        """ Initialize a DockingManager.

        Parameters
        ----------
        dock_area : QDockArea
            The primary dock area to be managed. Docking will be
            restricted to this area and to windows spawned by the
            area.

        """
        assert dock_area is not None
        self.dock_area = dock_area

    #--------------------------------------------------------------------------
    # Public API
    #--------------------------------------------------------------------------
    def add_item(self, item):
        """ Add a dock item to the dock manager.

        If the item has already been added, this is a no-op.

        Parameters
        ----------
        items : QDockItem
            The item to be managed by this dock manager. It will be
            reparented to a dock container and made available to the
            the layout system.

        """
        if item in self.dock_items:
            return
        self.dock_items.add(item)
        container = QDockContainer(self, self.dock_area)
        container.setDockItem(item)
        container.setObjectName(item.objectName())
        self.dock_frames.append(container)

    def remove_item(self, item):
        """ Remove a dock item from the dock manager.

        If the item has not been added to the manager, this is a no-op.

        Parameters
        ----------
        items : QDockItem
            The item to remove from the dock manager. It will be hidden
            and unparented, but not destroyed.

        """
        if item not in self.dock_items:
            return
        container = self._find_container(item.objectName())
        if container is not None:
            container.destroy()

    def clear_items(self):
        """ Clear the dock items from the dock manager.

        This method will hide and unparent all of the dock items that
        were previously added to the dock manager. This is equivalent
        to calling the 'remove_item()' method for every item managed
        by the dock manager.

        """
        for item in list(self.dock_items):
            self.remove_item(item)
        for frame in self.dock_frames[:]:
            frame.destroy()
        del self.dock_frames

    def apply_layout(self, layout):
        """ Apply a layout to the dock area.

        Parameters
        ----------
        layout : docklayout
            The docklayout to apply to the managed area.

        """
        # Remove the layout widget before resetting the handlers. This
        # prevents a re-used container from being hidden by the call to
        # setLayoutWidget after it has already been reset. The reference
        # is held so the containers do not get prematurely destroyed.
        widget = self.dock_area.layoutWidget()
        self.dock_area.setLayoutWidget(None)
        containers = list(self._dock_containers())
        for container in containers:
            container.reset()
        for frame in self.dock_frames[:]:
            if isinstance(frame, QDockWindow):
                frame.destroy()

        main_area = None
        floating_items = list(layout.children)
        for item in layout.children:
            if isinstance(item, dockarea):
                if not item.floating:
                    main_area = item
                    floating_items.remove(item)
                    break

        if main_area is not None:
            widget = build_layout(main_area.child, containers)
            self.dock_area.setLayoutWidget(widget)
            if main_area.maximized_item:
                maxed = self._find_container(main_area.maximized_item)
                if maxed is not None:
                    maxed.showMaximized()

        for f_item in floating_items:
            if isinstance(f_item, dockarea):
                child = f_item.child
                if isinstance(child, dockitem):
                    target = self._find_container(child.name)
                    target.float()
                else:
                    target = QDockWindow.create(self, self.dock_area)
                    self.dock_frames.append(target)
                    widget = build_layout(child, containers)
                    win_area = target.dockArea()
                    win_area.setLayoutWidget(widget)
                    win_area.installEventFilter(self.area_filter)
                    if f_item.maximized_item:
                        maxed = self._find_container(f_item.maximized_item)
                        if maxed is not None:
                            maxed.showMaximized()
            else:
                target = self._find_container(f_item.name)
                target.float()
            rect = QRect(*f_item.geometry)
            if rect.isValid():
                rect = ensure_on_screen(rect)
                target.setGeometry(rect)
            target.show()
            if f_item.maximized:
                target.showMaximized()

    def save_layout(self):
        """ Get the current layout of the dock area.

        Returns
        -------
        result : docklayout
            A docklayout instance which represents the current layout
            state.

        """
        layout_items = []

        area = self.dock_area
        widget = area.layoutWidget()
        if widget is not None:
            item = dockarea(save_layout(widget))
            maxed = area.maximizedWidget()
            if maxed is not None:
                item.maximized_item = maxed.objectName()
            layout_items.append(item)

        for frame in self.dock_frames:
            if frame.isWindow():
                if isinstance(frame, QDockWindow):
                    area = frame.dockArea()
                    item = dockarea(save_layout(area.layoutWidget()))
                    maxed = area.maximizedWidget()
                    if maxed is not None:
                        item.maximized_item = maxed.objectName()
                else:
                    item = save_layout(frame)
                item.floating = True
                item.maximized = frame.isMaximized()
                if frame.isMaximized():
                    geo = frame.normalGeometry()
                else:
                    geo = frame.geometry()
                item.geometry = (geo.x(), geo.y(), geo.width(), geo.height())
                layout_items.append(item)

        return docklayout(*layout_items)

    #--------------------------------------------------------------------------
    # Framework API
    #--------------------------------------------------------------------------
    def frame_moved(self, frame, pos):
        """ Handle a dock frame being moved by the user.

        This method is called by a floating dock frame as it is dragged
        by the user. It shows the dock overlay at the proper location.

        Parameters
        ----------
        frame : QDockFrame
            The dock frame being dragged by the user.

        pos : QPoint
            The global coordinates of the mouse position.

        """
        target = self._dock_target(frame, pos)
        if isinstance(target, QDockContainer):
            local = target.mapFromGlobal(pos)
            self.overlay.mouse_over_widget(target, local)
        elif isinstance(target, QDockArea):
            # Disallow docking onto an area with a maximized widget.
            # This prevents a non-intuitive user experience.
            if target.maximizedWidget() is not None:
                self.overlay.hide()
                return
            local = target.mapFromGlobal(pos)
            if target.layoutWidget() is None:
                self.overlay.mouse_over_widget(target, local, empty=True)
            else:
                widget = layout_hit_test(target, local)
                self.overlay.mouse_over_area(target, widget, local)
        else:
            self.overlay.hide()

    def frame_released(self, frame, pos):
        """ Handle the dock frame being released by the user.

        This method is called by a floating dock frame when the user
        has completed the drag operation. It will hide the overlay and
        redock the frame if the drag ended over a valid dock guide.

        Parameters
        ----------
        frame : QDockFrame
            The dock frame being dragged by the user.

        pos : QPoint
            The global coordinates of the mouse position.

        """
        overlay = self.overlay
        overlay.hide()
        guide = overlay.guide_at(pos)
        if guide == QGuideRose.Guide.NoGuide:
            return
        target = self._dock_target(frame, pos)
        if isinstance(target, QDockArea):
            # Disallow docking onto an area with a maximized widget.
            # This prevents a non-intuitive user experience.
            if target.maximizedWidget() is not None:
                return
            local = target.mapFromGlobal(pos)
            widget = layout_hit_test(target, local)
            plug_frame(target, widget, frame, guide)
            if isinstance(frame, QDockWindow):
                frame.destroy()
        elif isinstance(target, QDockContainer):
            window = QDockWindow.create(self, self.dock_area)
            self.dock_frames.append(window)
            window.setGeometry(target.geometry())
            win_area = window.dockArea()
            center_guide = QGuideRose.Guide.AreaCenter
            plug_frame(win_area, None, target, center_guide)
            plug_frame(win_area, target, frame, guide)
            if isinstance(frame, QDockWindow):
                frame.destroy()
            win_area.installEventFilter(self.area_filter)
            window.show()

    #--------------------------------------------------------------------------
    # Private API
    #--------------------------------------------------------------------------
    def _dock_containers(self):
        """ Get an iterable of QDockContainer instances.

        Returns
        -------
        result : generator
            A generator which yields the QDockContainer instances owned
            by this dock manager.

        """
        for frame in self.dock_frames:
            if isinstance(frame, QDockContainer):
                yield frame

    def _find_container(self, name):
        """ Find the dock container with the given object name.

        Parameters
        ----------
        name : basestring
            The object name of the dock container to locate.

        Returns
        -------
        result : QDockContainer or None
            The dock container for the given object name or None.

        """
        for container in self._dock_containers():
            if container.objectName() == name:
                return container

    def _iter_dock_targets(self):
        """ Get an iterable of potential dock targets.

        Returns
        -------
        result : generator
            A generator which yields the dock container and dock area
            instances which are potential dock targets.

        """
        for target in reversed(self.dock_frames):
            if target.isWindow():
                if isinstance(target, QDockContainer):
                    yield target
                elif isinstance(target, QDockWindow):
                    yield target.dockArea()
        yield self.dock_area

    def _dock_target(self, frame, pos):
        """ Get the dock target for the given frame and position.

        Parameters
        ----------
        frame : QDockFrame
            The dock frame which should be docked.

        pos : QPoint
            The global mouse position.

        Returns
        -------
        result : QDockArea, QDockContainer, or None
            The potential dock target for the frame and position.

        """
        for target in self._iter_dock_targets():
            if target is not frame:
                local = target.mapFromGlobal(pos)
                if target.rect().contains(local):
                    return target
