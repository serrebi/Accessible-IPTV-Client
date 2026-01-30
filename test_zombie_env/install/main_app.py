import os
import sys
import subprocess
import time
print("MAIN_APP running")
helper_bat = r"C:\Users\admin\git\Accessible-IPTV-Client\test_zombie_env\temp_helper\update_helper.bat"
install_dir = r"C:\Users\admin\git\Accessible-IPTV-Client\test_zombie_env\install"
staging_dir = r"C:\Users\admin\git\Accessible-IPTV-Client\test_zombie_env\staging"
backup_dir = r"C:\Users\admin\git\Accessible-IPTV-Client\test_zombie_env\backup"
exe_name = "mock_app.exe"
cmd = ["cmd", "/c", helper_bat, "-ParentPid", str(os.getpid()), "-InstallDir", install_dir, "-StagingDir", staging_dir, "-BackupDir", backup_dir, "-ExeName", exe_name]
print(f"Launching update: {cmd}")
subprocess.Popen(cmd, cwd=r"C:\Users\admin\git\Accessible-IPTV-Client\test_zombie_env\temp_helper")
print("Update process launched. Exiting.")
sys.exit(0)