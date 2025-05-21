# Save this as iptv_client.py and compile using: pyinstaller --noconsole --onefile iptv_client.py

import os
import urllib.request
from typing import Dict, List
import wx
import wx.adv

class IPTVClient(wx.Frame):
    def __init__(self):
        super().__init__(None, title="Accessible IPTV Client", size=(800, 600))

        self.channels_by_group: Dict[str, List[Dict[str, str]]] = {}
        self.all_channels: List[Dict[str, str]] = []
        self.current_group: str = "All Channels"
        self.default_player = "VLC"

        self.panel = wx.Panel(self)
        self.sizer = wx.BoxSizer(wx.HORIZONTAL)

        left_sizer = wx.BoxSizer(wx.VERTICAL)
        right_sizer = wx.BoxSizer(wx.VERTICAL)

        self.group_list = wx.ListBox(self.panel, style=wx.LB_SINGLE)
        self.filter_box = wx.TextCtrl(self.panel, style=wx.TE_PROCESS_ENTER)
        self.channel_list = wx.ListBox(self.panel, style=wx.LB_SINGLE)
        self.url_display = wx.TextCtrl(self.panel, style=wx.TE_READONLY | wx.TE_MULTILINE)

        left_sizer.Add(self.group_list, 1, wx.EXPAND | wx.ALL, 5)
        right_sizer.Add(self.filter_box, 0, wx.EXPAND | wx.ALL, 5)
        right_sizer.Add(self.channel_list, 1, wx.EXPAND | wx.ALL, 5)
        right_sizer.Add(self.url_display, 0, wx.EXPAND | wx.ALL, 5)

        self.sizer.Add(left_sizer, 1, wx.EXPAND)
        self.sizer.Add(right_sizer, 2, wx.EXPAND)

        self.panel.SetSizerAndFit(self.sizer)
        self.panel.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)

        self.group_list.Bind(wx.EVT_LISTBOX, self.on_group_select)
        self.group_list.Bind(wx.EVT_KEY_DOWN, self.on_group_key_down)
        self.channel_list.Bind(wx.EVT_LISTBOX_DCLICK, self.on_channel_activate)
        self.channel_list.Bind(wx.EVT_LISTBOX, self.on_channel_highlight)
        self.channel_list.Bind(wx.EVT_KEY_DOWN, self.on_channel_key_down)
        self.filter_box.Bind(wx.EVT_TEXT_ENTER, self.apply_filter)

        self.filtered_channels = []

        self.create_menu()
        self.Centre()
        self.Show()

    def create_menu(self):
        menubar = wx.MenuBar()

        file_menu = wx.Menu()
        open_file = file_menu.Append(wx.ID_OPEN, "Open File\tCtrl+O")
        open_url = file_menu.Append(wx.ID_ANY, "Open URL\tCtrl+U")
        file_menu.AppendSeparator()
        exit_item = file_menu.Append(wx.ID_EXIT, "Exit\tCtrl+Q")

        options_menu = wx.Menu()
        player_menu = wx.Menu()
        self.player_vlc = player_menu.AppendRadioItem(wx.ID_ANY, "VLC")
        self.player_mpc = player_menu.AppendRadioItem(wx.ID_ANY, "Media Player Classic")
        self.player_foobar = player_menu.AppendRadioItem(wx.ID_ANY, "Foobar2000")
        options_menu.AppendSubMenu(player_menu, "Select Default Player")

        menubar.Append(file_menu, "File")
        menubar.Append(options_menu, "Options")
        self.SetMenuBar(menubar)

        self.Bind(wx.EVT_MENU, self.load_file, open_file)
        self.Bind(wx.EVT_MENU, self.load_url, open_url)
        self.Bind(wx.EVT_MENU, self.on_exit, exit_item)
        self.Bind(wx.EVT_MENU, self.select_player, self.player_vlc)
        self.Bind(wx.EVT_MENU, self.select_player, self.player_mpc)
        self.Bind(wx.EVT_MENU, self.select_player, self.player_foobar)

    def select_player(self, event):
        if self.player_vlc.IsChecked():
            self.default_player = "VLC"
        elif self.player_mpc.IsChecked():
            self.default_player = "MPC"
        elif self.player_foobar.IsChecked():
            self.default_player = "Foobar"

    def load_file(self, event):
        with wx.FileDialog(self, "Open M3U file", wildcard="M3U files (*.m3u;*.m3u8)|*.m3u;*.m3u8",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as file_dialog:
            if file_dialog.ShowModal() == wx.ID_CANCEL:
                return
            path = file_dialog.GetPath()
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                self.parse_m3u(content)
            except Exception as e:
                wx.MessageBox(f"Failed to open file: {e}", "Error", wx.ICON_ERROR)

    def load_url(self, event):
        dialog = wx.TextEntryDialog(self, "Enter M3U or M3U+ URL:", "Open URL")
        if dialog.ShowModal() == wx.ID_OK:
            url = dialog.GetValue()
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req) as response:
                    content = response.read().decode('utf-8', errors='ignore')
                self.parse_m3u(content)
            except Exception as e:
                wx.MessageBox(f"Failed to load URL: {e}", "Error", wx.ICON_ERROR)

    def on_exit(self, event):
        self.Close(True)

    def parse_m3u(self, content: str):
        self.channels_by_group.clear()
        self.all_channels.clear()
        current_group = "Uncategorized"
        lines = content.splitlines()
        current_channel = {}

        for line in lines:
            if line.startswith("#EXTINF"):
                info_parts = line.split(',')
                if len(info_parts) > 1:
                    current_channel = {'name': info_parts[1]}
                if 'group-title' in line:
                    group_start = line.find('group-title="')
                    if group_start != -1:
                        group_end = line.find('"', group_start + 13)
                        current_group = line[group_start + 13:group_end]
                        current_channel['group'] = current_group
            elif line.startswith("http"):
                current_channel['url'] = line
                group = current_channel.get('group', current_group)
                if group not in self.channels_by_group:
                    self.channels_by_group[group] = []
                self.channels_by_group[group].append(current_channel)
                self.all_channels.append(current_channel)
                current_channel = {}

        self.populate_groups()
        self.group_list.SetSelection(0)
        self.on_group_select(None)
        if self.channel_list.GetCount() > 0:
            self.channel_list.SetFocus()
            self.channel_list.SetSelection(0)
            self.on_channel_highlight(None)

    def populate_groups(self):
        self.group_list.Clear()
        self.group_list.Append(f"All Channels ({len(self.all_channels)})")
        for group in sorted(self.channels_by_group):
            count = len(self.channels_by_group[group])
            self.group_list.Append(f"{group} ({count})")

    def on_group_select(self, event):
        group_name_label = self.group_list.GetStringSelection()
        group_name = group_name_label.split(" (")[0]
        self.current_group = group_name
        self.apply_filter(None)
        if self.channel_list.GetCount() > 0:
            self.channel_list.SetFocus()
            self.channel_list.SetSelection(0)
            self.on_channel_highlight(None)

    def on_group_key_down(self, event):
        if event.GetKeyCode() == wx.WXK_RETURN:
            self.on_group_select(None)
        else:
            event.Skip()

    def on_channel_key_down(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_RETURN:
            self.play_selected_channel()
        elif key in (wx.WXK_UP, wx.WXK_DOWN):
            event.Skip()
        elif key in (wx.WXK_LEFT, wx.WXK_RIGHT):
            pass
        else:
            event.Skip()

    def apply_filter(self, event):
        filter_text = self.filter_box.GetValue().lower()
        self.filtered_channels.clear()
        self.channel_list.Clear()

        if self.current_group == "All Channels":
            source = self.all_channels
        else:
            source = self.channels_by_group.get(self.current_group, [])

        for channel in source:
            if filter_text in channel['name'].lower():
                self.filtered_channels.append(channel)
                self.channel_list.Append(channel['name'])

    def on_channel_activate(self, event):
        self.play_selected_channel()

    def on_channel_highlight(self, event):
        index = self.channel_list.GetSelection()
        if index != wx.NOT_FOUND and index < len(self.filtered_channels):
            channel = self.filtered_channels[index]
            self.url_display.SetValue(channel['url'])

    def on_char_hook(self, event):
        if event.GetKeyCode() == wx.WXK_RETURN and self.channel_list.HasFocus():
            self.play_selected_channel()
        else:
            event.Skip()

    def play_selected_channel(self):
        selection = self.channel_list.GetSelection()
        if selection == wx.NOT_FOUND or selection >= len(self.filtered_channels):
            return
        channel = self.filtered_channels[selection]
        url = channel['url']

        players = {
            "VLC": r"C:\\Program Files\\VideoLAN\\VLC\\vlc.exe",
            "MPC": r"C:\\Program Files\\MPC-HC\\mpc-hc64.exe",
            "Foobar": r"C:\\Program Files\\foobar2000\\foobar2000.exe"
        }

        player_path = players.get(self.default_player)

        if player_path and os.path.exists(player_path):
            os.spawnl(os.P_NOWAIT, player_path, os.path.basename(player_path), url)
        else:
            wx.MessageBox(f"{self.default_player} not found at expected path.", "Error", wx.ICON_ERROR)

if __name__ == '__main__':
    app = wx.App(False)
    IPTVClient()
    app.MainLoop()
