# Save as iptvclient.py

import os
import sys
import json
import urllib.request
from typing import Dict, List
import wx
import wx.adv

CONFIG_FILE = "iptvclient.conf"

def get_base_path():
    # Works for both PyInstaller EXE and .py script
    if getattr(sys, 'frozen', False):  # PyInstaller
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_config_path():
    return os.path.join(get_base_path(), CONFIG_FILE)

def load_playlist_sources():
    path = get_config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_playlist_sources(sources):
    path = get_config_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sources, f)

class PlaylistManagerDialog(wx.Dialog):
    def __init__(self, parent, playlist_sources, playlists_channels, *args, **kwargs):
        super().__init__(parent, title="Playlist Manager", size=(700, 300), *args, **kwargs)
        self.playlist_sources = playlist_sources  # list of file paths and URLs
        self.playlists_channels = playlists_channels  # dict: source -> list of channels (dicts)
        self.InitUI()
        self.CenterOnParent()
        wx.CallAfter(self.add_file_btn.SetFocus)

    def InitUI(self):
        panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)

        # Playlist action buttons at the top
        self.add_file_btn = wx.Button(panel, label="Add File")
        self.add_url_btn = wx.Button(panel, label="Add URL")
        self.remove_btn = wx.Button(panel, label="Remove Selected Playlist")
        hbox_buttons = wx.BoxSizer(wx.HORIZONTAL)
        hbox_buttons.Add(self.add_file_btn, 1, wx.ALL | wx.EXPAND, 2)
        hbox_buttons.Add(self.add_url_btn, 1, wx.ALL | wx.EXPAND, 2)
        hbox_buttons.Add(self.remove_btn, 1, wx.ALL | wx.EXPAND, 2)
        vbox.Add(hbox_buttons, 0, wx.EXPAND)

        # Playlist label and list
        playlist_label = wx.StaticText(panel, label="Playlists:")
        vbox.Add(playlist_label, 0, wx.ALL, 2)
        self.playlist_listbox = wx.ListBox(panel, style=wx.LB_SINGLE)
        self.playlist_listbox.Append("All Playlists")
        for src in self.playlist_sources:
            self.playlist_listbox.Append(src)
        self.playlist_listbox.SetSelection(0)
        vbox.Add(self.playlist_listbox, 1, wx.ALL | wx.EXPAND, 2)

        # OK/Cancel as real buttons
        btn_hbox = wx.BoxSizer(wx.HORIZONTAL)
        self.ok_btn = wx.Button(panel, id=wx.ID_OK, label="OK")
        self.cancel_btn = wx.Button(panel, id=wx.ID_CANCEL, label="Cancel")
        btn_hbox.Add(self.ok_btn, 0, wx.ALL, 5)
        btn_hbox.Add(self.cancel_btn, 0, wx.ALL, 5)
        vbox.Add(btn_hbox, 0, wx.ALIGN_RIGHT)

        panel.SetSizer(vbox)

        # Event bindings
        self.add_file_btn.Bind(wx.EVT_BUTTON, self.OnAddFile)
        self.add_url_btn.Bind(wx.EVT_BUTTON, self.OnAddURL)
        self.remove_btn.Bind(wx.EVT_BUTTON, self.OnRemove)
        self.playlist_listbox.Bind(wx.EVT_LISTBOX, self.OnPlaylistSelect)
        self.ok_btn.Bind(wx.EVT_BUTTON, self.OnOk)
        self.cancel_btn.Bind(wx.EVT_BUTTON, self.OnCancel)

    def save(self):
        # Save to config after every change
        save_playlist_sources(self.playlist_sources)

    def OnPlaylistSelect(self, event):
        pass

    def OnRemove(self, event):
        idx = self.playlist_listbox.GetSelection()
        if idx == 0:
            wx.MessageBox("Cannot remove 'All Playlists'. Select a specific playlist.", "Error")
            wx.CallAfter(self.playlist_listbox.SetFocus)
            return
        src = self.playlist_sources[idx-1]
        dlg = wx.MessageDialog(self, f"Remove this playlist?\n{src}", "Confirm Remove", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        if dlg.ShowModal() == wx.ID_YES:
            self.playlist_sources.pop(idx-1)
            self.playlist_listbox.Delete(idx)
            if src in self.playlists_channels:
                del self.playlists_channels[src]
            self.playlist_listbox.SetSelection(0)
            wx.CallAfter(self.playlist_listbox.SetFocus)
            self.save()
        dlg.Destroy()

    def OnAddFile(self, event):
        with wx.FileDialog(self, "Add M3U File to Playlist", wildcard="M3U files (*.m3u;*.m3u8)|*.m3u;*.m3u8",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as file_dialog:
            if file_dialog.ShowModal() == wx.ID_CANCEL:
                wx.CallAfter(self.add_file_btn.SetFocus)
                return
            path = file_dialog.GetPath()
            if path not in self.playlist_sources:
                self.playlist_sources.append(path)
                self.playlist_listbox.Append(path)
                self.save()
        wx.CallAfter(self.playlist_listbox.SetFocus)

    def OnAddURL(self, event):
        dialog = wx.TextEntryDialog(self, "Enter M3U or M3U+ URL:", "Add URL to Playlist")
        if dialog.ShowModal() == wx.ID_OK:
            url = dialog.GetValue()
            if url and url not in self.playlist_sources:
                self.playlist_sources.append(url)
                self.playlist_listbox.Append(url)
                self.save()
        dialog.Destroy()
        wx.CallAfter(self.playlist_listbox.SetFocus)

    def OnOk(self, event):
        self.save()
        self.EndModal(wx.ID_OK)

    def OnCancel(self, event):
        self.EndModal(wx.ID_CANCEL)

    def GetResult(self):
        return list(self.playlist_sources)

class IPTVClient(wx.Frame):
    def __init__(self):
        super().__init__(None, title="Accessible IPTV Client", size=(800, 600))
        self.channels_by_group: Dict[str, List[Dict[str, str]]] = {}
        self.all_channels: List[Dict[str, str]] = []
        self.current_group: str = "All Channels"
        self.default_player = "VLC"
        self.filtered_channels = []
        self.playlist_sources: List[str] = []
        self.playlists_channels: Dict[str, List[Dict[str, str]]] = {}

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

        self.create_menu()
        self.Centre()

        self.load_playlist()
        self.reload_all_sources()
        self.Show()

    def load_playlist(self):
        self.playlist_sources = load_playlist_sources()

    def save_playlist(self):
        save_playlist_sources(self.playlist_sources)

    def reload_all_sources(self):
        self.channels_by_group.clear()
        self.all_channels.clear()
        self.playlists_channels.clear()
        for src in self.playlist_sources:
            playlist_channels = []
            if src.startswith("http://") or src.startswith("https://"):
                try:
                    req = urllib.request.Request(src, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req) as response:
                        content = response.read().decode('utf-8', errors='ignore')
                    playlist_channels = self.parse_m3u_return(content)
                except Exception:
                    playlist_channels = []
            else:
                try:
                    with open(src, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    playlist_channels = self.parse_m3u_return(content)
                except Exception:
                    playlist_channels = []
            self.playlists_channels[src] = playlist_channels
            for ch in playlist_channels:
                self.all_channels.append(ch)
                group = ch.get('group', 'Uncategorized')
                if group not in self.channels_by_group:
                    self.channels_by_group[group] = []
                self.channels_by_group[group].append(ch)
        self.populate_groups()
        if self.group_list.GetCount() > 0:
            self.group_list.SetSelection(0)
            self.on_group_select(None)

    def create_menu(self):
        menubar = wx.MenuBar()
        file_menu = wx.Menu()
        open_file = file_menu.Append(wx.ID_OPEN, "Open File (One-time)\tCtrl+O")
        open_url = file_menu.Append(wx.ID_ANY, "Open URL (One-time)\tCtrl+U")
        file_menu.AppendSeparator()
        playlist_manager = file_menu.Append(wx.ID_ANY, "Open Playlist Manager\tCtrl+M")
        file_menu.AppendSeparator()
        exit_item = file_menu.Append(wx.ID_EXIT, "Exit\tCtrl+Q")
        menubar.Append(file_menu, "File")

        options_menu = wx.Menu()
        player_menu = wx.Menu()
        self.player_vlc = player_menu.AppendRadioItem(wx.ID_ANY, "VLC")
        self.player_mpc = player_menu.AppendRadioItem(wx.ID_ANY, "Media Player Classic")
        self.player_kodi = player_menu.AppendRadioItem(wx.ID_ANY, "Kodi")
        self.player_winamp = player_menu.AppendRadioItem(wx.ID_ANY, "Winamp")
        self.player_foobar = player_menu.AppendRadioItem(wx.ID_ANY, "Foobar2000")
        options_menu.AppendSubMenu(player_menu, "Select Default Player")
        menubar.Append(options_menu, "Options")
        self.SetMenuBar(menubar)

        self.Bind(wx.EVT_MENU, self.load_file, open_file)
        self.Bind(wx.EVT_MENU, self.load_url, open_url)
        self.Bind(wx.EVT_MENU, self.show_playlist_manager, playlist_manager)
        self.Bind(wx.EVT_MENU, self.on_exit, exit_item)
        self.Bind(wx.EVT_MENU, self.select_player, self.player_vlc)
        self.Bind(wx.EVT_MENU, self.select_player, self.player_mpc)
        self.Bind(wx.EVT_MENU, self.select_player, self.player_kodi)
        self.Bind(wx.EVT_MENU, self.select_player, self.player_winamp)
        self.Bind(wx.EVT_MENU, self.select_player, self.player_foobar)

    def select_player(self, event):
        if self.player_vlc.IsChecked():
            self.default_player = "VLC"
        elif self.player_mpc.IsChecked():
            self.default_player = "MPC"
        elif self.player_kodi.IsChecked():
            self.default_player = "Kodi"
        elif self.player_winamp.IsChecked():
            self.default_player = "Winamp"
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
                self.populate_groups()
                self.group_list.SetSelection(0)
                self.on_group_select(None)
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
                self.populate_groups()
                self.group_list.SetSelection(0)
                self.on_group_select(None)
            except Exception as e:
                wx.MessageBox(f"Failed to load URL: {e}", "Error", wx.ICON_ERROR)
        dialog.Destroy()

    def show_playlist_manager(self, event):
        dlg = PlaylistManagerDialog(self, list(self.playlist_sources), dict(self.playlists_channels))
        dlg.ShowModal()
        dlg.Destroy()
        self.load_playlist()
        self.reload_all_sources()

    def on_exit(self, event):
        self.Close(True)

    def parse_m3u(self, content: str, append=False):
        if not append:
            self.channels_by_group.clear()
            self.all_channels.clear()
        current_group = "Uncategorized"
        lines = content.splitlines()
        current_channel = {}

        for line in lines:
            if line.startswith("#EXTINF"):
                info_parts = line.split(',', 1)
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

    def parse_m3u_return(self, content: str):
        current_group = "Uncategorized"
        lines = content.splitlines()
        channels = []
        current_channel = {}
        for line in lines:
            if line.startswith("#EXTINF"):
                info_parts = line.split(',', 1)
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
                channel = dict(current_channel)
                channels.append(channel)
                current_channel = {}
        return channels

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
            self.url_display.SetValue(channel.get('url', ''))

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

        player_paths = {
            "VLC": [
                r"C:\Program Files\VideoLAN\VLC\vlc.exe",
                r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"
            ],
            "MPC": [
                r"C:\Program Files\MPC-HC\mpc-hc64.exe",
                r"C:\Program Files (x86)\K-Lite Codec Pack\MPC-HC64\mpc-hc64.exe",
                r"C:\Program Files (x86)\MPC-HC\mpc-hc64.exe"
            ],
            "Kodi": [
                r"C:\Program Files\Kodi\kodi.exe",
                r"C:\Program Files (x86)\Kodi\kodi.exe"
            ],
            "Winamp": [
                r"C:\Program Files\Winamp\winamp.exe",
                r"C:\Program Files (x86)\Winamp\winamp.exe"
            ],
            "Foobar": [
                r"C:\Program Files\foobar2000\foobar2000.exe",
                r"C:\Program Files (x86)\foobar2000\foobar2000.exe"
            ]
        }

        player_key = self.default_player
        paths_to_try = player_paths.get(player_key, [])
        for player_path in paths_to_try:
            if os.path.exists(player_path):
                os.spawnl(os.P_NOWAIT, player_path, os.path.basename(player_path), url)
                return

        wx.MessageBox(
            f"{self.default_player} not found in any known location.",
            "Error", wx.ICON_ERROR
        )

if __name__ == '__main__':
    app = wx.App(False)
    IPTVClient()
    app.MainLoop()
