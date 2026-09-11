"""Four optional X-view groups; never link Y ranges or acquisition state."""


class XSyncGroups:
    def __init__(self):
        self.panes = []
        self.updating = False

    def add(self, pane):
        self.panes.append(pane)
        pane.x_group_requested.connect(self.assign)
        pane.x_view_changed.connect(self.changed)

    def remove(self, pane):
        if pane in self.panes:
            self.panes.remove(pane)

    def members(self, group):
        return [p for p in self.panes if p.sync_group == group] if group else []

    def assign(self, pane, group):
        if group not in range(5):
            raise ValueError('X group must be 0..4')
        peers = [p for p in self.members(group) if p is not pane]
        pane.sync_group = group
        pane.sync_end = None
        pane.follow_button.setToolTip('跟随最新时间' + (f' · X 同步组 {group}' if group else ''))
        if peers:
            self.copy_view(peers[0], pane)
        pane.invalidate()

    @staticmethod
    def copy_view(source, target):
        # A phase axis cannot share limits meaningfully with absolute time.
        target.display_mode = source.display_mode
        target.span, target.following = source.span, source.following
        target.view.setXRange(*source.view.viewRange()[0], padding=0)
        target.invalidate()
        target.update_state()

    def changed(self, pane):
        if self.updating or not pane.sync_group:
            return
        self.updating = True
        try:
            for peer in self.members(pane.sync_group):
                if peer is not pane:
                    self.copy_view(pane, peer)
        finally:
            self.updating = False

    def prepare(self):
        for group in range(1,5):
            members = self.members(group)
            ends = [float(x[-1]) for p in members for key in p.curves
                    if p.styles[key].visible for x,_ in [p.arrays(key)] if len(x)]
            end = max(ends) if ends else None
            for pane in members:
                if pane.sync_end != end:
                    pane.sync_end = end
                    pane.invalidate()
