import wx
import platform_setup
from main_window import IPTVClient

platform_setup.set_linux_env()

if __name__ == "__main__":
    app = wx.App(False)
    IPTVClient()
    app.MainLoop()
