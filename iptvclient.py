import os
import sys
import json
import urllib.request
from typing import Dict, List
import wx

CONFIG_FILE = "iptvclient.conf"

def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_config_path():
    return os.path.join(get_base_path(), CONFIG_FILE)

def load_playlist_sources() -> List[str]:
    path = get_config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_playlist_sources(sources: List[str]):
    with open(get_config_path(), "w", encoding="utf-8") as f:
        json.dump(sources, f, indent=2)

class PlaylistManagerDialog(wx.Dialog):
    def __init__(self, parent, playlist_sources):
        super().__init__(parent, title="Playlist Manager", size=(700,300))
        self.playlist_sources = playlist_sources
        self._build_ui()
        self.CenterOnParent()
        wx.CallAfter(self.add_file_btn.SetFocus)

    def _build_ui(self):
        p = wx.Panel(self)
        v = wx.BoxSizer(wx.VERTICAL)
        h = wx.BoxSizer(wx.HORIZONTAL)
        self.add_file_btn = wx.Button(p, "Add File")
        self.add_url_btn  = wx.Button(p, "Add URL")
        self.remove_btn   = wx.Button(p, "Remove Selected Playlist")
        for btn in (self.add_file_btn, self.add_url_btn, self.remove_btn):
            h.Add(btn, 1, wx.ALL|wx.EXPAND, 2)
        v.Add(h, 0, wx.EXPAND)
        v.Add(wx.StaticText(p, label="Playlists:"), 0, wx.ALL, 2)
        self.lb = wx.ListBox(p, style=wx.LB_SINGLE)
        self.lb.Append("All Playlists")
        for s in self.playlist_sources:
            self.lb.Append(s)
        self.lb.SetSelection(0)
        v.Add(self.lb, 1, wx.ALL|wx.EXPAND, 2)
        b = wx.BoxSizer(wx.HORIZONTAL)
        self.ok   = wx.Button(p, wx.ID_OK)
        self.cancel=wx.Button(p, wx.ID_CANCEL)
        b.Add(self.ok,   0, wx.ALL, 5)
        b.Add(self.cancel,0, wx.ALL, 5)
        v.Add(b, 0, wx.ALIGN_RIGHT)
        p.SetSizer(v)
        self.add_file_btn.Bind(wx.EVT_BUTTON, self.OnAddFile)
        self.add_url_btn .Bind(wx.EVT_BUTTON, self.OnAddURL)
        self.remove_btn  .Bind(wx.EVT_BUTTON, self.OnRemove)

    def OnAddFile(self, _):
        with wx.FileDialog(self, "Add M3U File", wildcard="*.m3u;*.m3u8",
                           style=wx.FD_OPEN|wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal()==wx.ID_OK:
                p = dlg.GetPath()
                if p not in self.playlist_sources:
                    self.playlist_sources.append(p)
                    self.lb.Append(p)
        wx.CallAfter(self.add_file_btn.SetFocus)

    def OnAddURL(self, _):
        dlg = wx.TextEntryDialog(self, "Enter M3U URL:", "Add URL")
        if dlg.ShowModal()==wx.ID_OK:
            u = dlg.GetValue().strip()
            if u and u not in self.playlist_sources:
                self.playlist_sources.append(u)
                self.lb.Append(u)
        dlg.Destroy()
        wx.CallAfter(self.add_url_btn.SetFocus)

    def OnRemove(self, _):
        i = self.lb.GetSelection()
        if i<=0:
            wx.MessageBox("Cannot remove 'All Playlists'.","Error",wx.OK|wx.ICON_ERROR)
            return
        src = self.playlist_sources[i-1]
        if wx.MessageBox(f"Remove this playlist?\n{src}", "Confirm", wx.YES_NO|wx.NO_DEFAULT|wx.ICON_WARNING)==wx.YES:
            self.playlist_sources.pop(i-1)
            self.lb.Delete(i)

    def GetResult(self):
        return list(self.playlist_sources)

class IPTVClient(wx.Frame):
    def __init__(self):
        super().__init__(None, title="Accessible IPTV Client", size=(800,600))
        self.playlist_sources = load_playlist_sources()
        self.channels_by_group: Dict[str,List[Dict[str,str]]] = {}
        self.all_channels: List[Dict[str,str]] = []
        self.filtered: List[Dict[str,str]] = []
        self.current_group = "All Channels"
        self.default_player = "VLC"
        self._build_ui()
        self.Centre()
        self.reload_all_sources()
        self.Show()

    def _build_ui(self):
        p = wx.Panel(self)
        hs = wx.BoxSizer(wx.HORIZONTAL)
        vs_l = wx.BoxSizer(wx.VERTICAL)
        vs_r = wx.BoxSizer(wx.VERTICAL)
        self.group_list = wx.ListBox(p, style=wx.LB_SINGLE)
        self.filter_box = wx.TextCtrl(p, style=wx.TE_PROCESS_ENTER)
        self.channel_list= wx.ListBox(p, style=wx.LB_SINGLE)
        self.url_display = wx.TextCtrl(p, style=wx.TE_READONLY|wx.TE_MULTILINE)
        vs_l.Add(self.group_list,1,wx.EXPAND|wx.ALL,5)
        vs_r.Add(self.filter_box,0,wx.EXPAND|wx.ALL,5)
        vs_r.Add(self.channel_list,1,wx.EXPAND|wx.ALL,5)
        vs_r.Add(self.url_display,0,wx.EXPAND|wx.ALL,5)
        hs.Add(vs_l,1,wx.EXPAND)
        hs.Add(vs_r,2,wx.EXPAND)
        p.SetSizerAndFit(hs)
        p.Bind(wx.EVT_CHAR_HOOK, lambda e: self._on_char(e))
        self.group_list.Bind(wx.EVT_LISTBOX, lambda _: self.on_group_select())
        self.group_list.Bind(wx.EVT_KEY_DOWN,
            lambda e: self.on_group_select() if e.GetKeyCode()==wx.WXK_RETURN else e.Skip())
        self.filter_box.Bind(wx.EVT_TEXT_ENTER, lambda _: self.apply_filter())
        self.channel_list.Bind(wx.EVT_LISTBOX, lambda _: self.on_highlight())
        self.channel_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda _: self.play_selected())
        self.channel_list.Bind(wx.EVT_KEY_DOWN,
            lambda e: self.play_selected() if e.GetKeyCode()==wx.WXK_RETURN else e.Skip())
        mb = wx.MenuBar()
        fm = wx.Menu()
        m_mgr   = fm.Append(wx.ID_ANY,  "Playlist Manager\tCtrl+M")
        fm.AppendSeparator()
        m_exit  = fm.Append(wx.ID_EXIT, "Exit\tCtrl+Q")
        mb.Append(fm, "File")
        # --- Options menu with submenu ---
        om = wx.Menu()
        player_menu = wx.Menu()
        self.player_VLC = player_menu.AppendRadioItem(wx.ID_ANY, "VLC")
        self.player_MPC = player_menu.AppendRadioItem(wx.ID_ANY, "MPC")
        self.player_Kodi = player_menu.AppendRadioItem(wx.ID_ANY, "Kodi")
        self.player_Winamp = player_menu.AppendRadioItem(wx.ID_ANY, "Winamp")
        self.player_Foobar2000 = player_menu.AppendRadioItem(wx.ID_ANY, "Foobar2000")
        om.AppendSubMenu(player_menu, "Media Player to Use")
        mb.Append(om, "Options")
        self.SetMenuBar(mb)
        self.Bind(wx.EVT_MENU, self.show_manager,   m_mgr)
        self.Bind(wx.EVT_MENU, lambda _: self.Close(), m_exit)
        self.Bind(wx.EVT_MENU, lambda _: self._select_player(), self.player_VLC)
        self.Bind(wx.EVT_MENU, lambda _: self._select_player(), self.player_MPC)
        self.Bind(wx.EVT_MENU, lambda _: self._select_player(), self.player_Kodi)
        self.Bind(wx.EVT_MENU, lambda _: self._select_player(), self.player_Winamp)
        self.Bind(wx.EVT_MENU, lambda _: self._select_player(), self.player_Foobar2000)

    def _select_player(self):
        for attr in ("VLC","MPC","Kodi","Winamp","Foobar2000"):
            if getattr(self, f"player_{attr}").IsChecked():
                self.default_player = attr
                break

    def reload_all_sources(self):
        # Optimized: minimize allocations, do not rebuild more than needed
        self.channels_by_group.clear()
        self.all_channels.clear()
        append_channel = self.all_channels.append
        append_group = lambda grp, ch: self.channels_by_group.setdefault(grp, []).append(ch)
        for src in self.playlist_sources:
            try:
                if src.startswith(("http://","https://")):
                    text = urllib.request.urlopen(urllib.request.Request(src,
                        headers={"User-Agent":"Mozilla/5.0"})).read().decode("utf-8","ignore")
                else:
                    with open(src, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                for ch in self._parse_m3u_return(text):
                    grp = ch.get("group", "Uncategorized")
                    append_group(grp, ch)
                    append_channel(ch)
            except Exception:
                continue
        self.populate_groups()
        self.group_list.SetSelection(0)
        self.on_group_select()

    def populate_groups(self):
        self.group_list.Clear()
        self.group_list.Append(f"All Channels ({len(self.all_channels)})")
        for grp in sorted(self.channels_by_group):
            self.group_list.Append(f"{grp} ({len(self.channels_by_group[grp])})")

    def on_group_select(self):
        sel = self.group_list.GetStringSelection().split(" (",1)[0]
        self.current_group = sel
        self.apply_filter()

    def apply_filter(self):
        txt = self.filter_box.GetValue().lower()
        self.filtered.clear()
        self.channel_list.Clear()
        source = self.all_channels if self.current_group=="All Channels" \
            else self.channels_by_group.get(self.current_group,[])
        filtered = [ch for ch in source if txt in ch.get("name","").lower()]
        self.filtered.extend(filtered)
        for ch in filtered:
            self.channel_list.Append(ch.get("name",""))

    def on_highlight(self):
        i = self.channel_list.GetSelection()
        if 0<=i<len(self.filtered):
            self.url_display.SetValue(self.filtered[i].get("url",""))

    def _on_char(self, evt):
        if evt.GetKeyCode()==wx.WXK_RETURN and self.channel_list.HasFocus():
            self.play_selected()
        else:
            evt.Skip()

    def play_selected(self):
        i = self.channel_list.GetSelection()
        if not (0<=i<len(self.filtered)): return
        url = self.filtered[i].get("url","")
        exe_list = {
            "VLC":[r"C:\Program Files\VideoLAN\VLC\vlc.exe",
                   r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"],
            "MPC":[r"C:\Program Files\MPC-HC\mpc-hc64.exe",
                   r"C:\Program Files (x86)\K-Lite Codec Pack\MPC-HC64\mpc-hc64.exe"],
            "Kodi":[r"C:\Program Files\Kodi\kodi.exe"],
            "Winamp":[r"C:\Program Files\Winamp\winamp.exe"],
            "Foobar2000":[r"C:\Program Files\foobar2000\foobar2000.exe"]
        }[self.default_player]
        for p in exe_list:
            if os.path.exists(p):
                os.spawnl(os.P_NOWAIT, p, os.path.basename(p), url)
                return
        wx.MessageBox(f"{self.default_player} not found.","Error",wx.OK|wx.ICON_ERROR)

    def show_manager(self, _):
        dlg = PlaylistManagerDialog(self, self.playlist_sources)
        if dlg.ShowModal()==wx.ID_OK:
            self.playlist_sources = dlg.GetResult()
            save_playlist_sources(self.playlist_sources)
            self.reload_all_sources()
        dlg.Destroy()

    def _parse_m3u_return(self, content:str)->List[Dict[str,str]]:
        # Optimized, avoids unnecessary dict copies
        lines = content.splitlines()
        current_group = "Uncategorized"
        out = []
        for i, line in enumerate(lines):
            if line.startswith("#EXTINF"):
                name = ""
                if ',' in line:
                    name = line.split(',',1)[1]
                group = current_group
                if 'group-title="' in line:
                    try:
                        group = line.split('group-title="',1)[1].split('"',1)[0]
                        current_group = group
                    except Exception:
                        group = current_group
                # look ahead for next http
                url = ""
                for j in range(i+1, min(i+4, len(lines))):
                    if lines[j].startswith("http"):
                        url = lines[j]
                        break
                if name and url:
                    out.append({"name":name, "url":url, "group":group})
        return out

if __name__=='__main__':
    app=wx.App(False)
    IPTVClient()
    app.MainLoop()
