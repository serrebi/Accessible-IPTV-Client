import os
import sys
import shutil
import subprocess
import time
import tempfile

def create_mock_env():
    base_dir = os.path.abspath("test_zombie_env")
    if os.path.exists(base_dir):
        # cleanup with retry
        for i in range(3):
            try:
                shutil.rmtree(base_dir)
                break
            except Exception:
                time.sleep(1)
        
    os.makedirs(base_dir, exist_ok=True)

    install_dir = os.path.join(base_dir, "install")
    staging_dir = os.path.join(base_dir, "staging")
    backup_dir = os.path.join(base_dir, "backup")
    temp_dir = os.path.join(base_dir, "temp_helper")

    os.makedirs(install_dir)
    os.makedirs(staging_dir)
    os.makedirs(temp_dir)

    # Copy helpers to temp_dir
    shutil.copy("update_helper.bat", os.path.join(temp_dir, "update_helper.bat"))
    shutil.copy("update_helper.ps1", os.path.join(temp_dir, "update_helper.ps1"))

    # Determine python executable
    python_exe = sys.executable

    # 1. Create the "Main App" (updater launcher)
    # It will launch the updater and then exit.
    lines = []
    lines.append('import os')
    lines.append('import sys')
    lines.append('import subprocess')
    lines.append('import time')
    lines.append('print("MAIN_APP running")')
    lines.append(f'helper_bat = r"{os.path.join(temp_dir, "update_helper.bat")}"')
    lines.append(f'install_dir = r"{install_dir}"')
    lines.append(f'staging_dir = r"{staging_dir}"')
    lines.append(f'backup_dir = r"{backup_dir}"')
    # The exe name we are updating. In this test, we pretend 'python.exe' is the app 
    # BUT we can't kill python.exe or we kill the test runner.
    # So we must use a dummy file name for the "ExeName" parameter, 
    # but the locking is simulating by the ZOMBIE process holding a file handle.
    # Actually, the user says "make sure all iptv client is shut down".
    # The helper looks for process IDs.
    # If I run a "zombie" python script that holds a file open in install_dir, 
    # the move should fail.
    
    lines.append('exe_name = "mock_app.exe"') 
    lines.append('cmd = ["cmd", "/c", helper_bat, "-ParentPid", str(os.getpid()), "-InstallDir", install_dir, "-StagingDir", staging_dir, "-BackupDir", backup_dir, "-ExeName", exe_name]')
    lines.append('print(f"Launching update: {cmd}")')
    # Run helper in temp_dir to avoid the previous lock bug
    lines.append(f'subprocess.Popen(cmd, cwd=r"{temp_dir}")')
    lines.append('print("Update process launched. Exiting.")')
    lines.append('sys.exit(0)')
    
    with open(os.path.join(install_dir, "main_app.py"), "w") as f:
        f.write("\n".join(lines))

    # 2. Create the "Zombie App"
    # This process runs from install_dir and sleeps forever.
    # It simulates a tray icon or stuck background thread.
    # It shares the same "name" (conceptually) or at least holds a lock.
    # To simulate the "Kill all clients" requirement, we need the helper to look for this process.
    # Since we can't rename python.exe to IPTVClient.exe easily, 
    # we will rely on the helper simply killing ANY process locking the folder?
    # No, the request was "make sure all iptv client is shut down".
    # So the helper probably needs to kill by ImageName.
    # For this test, we can't easily test "kill by name" unless we mock the name.
    
    # However, we CAN test that if a file is locked, it fails (baseline).
    # And we can test that if we modify the helper to kill "python" (unsafe for test)
    
    # Alternative: We create a dummy exe by copying python.exe?
    # That might be heavy.
    
    # Let's just create a zombie script that holds a lock.
    zombie_code = """
import time
import os
print(f"ZOMBIE running PID {os.getpid()}")
# Hold a file open to prevent directory move
f = open("lock.txt", "w")
while True:
    time.sleep(1)
"""
    with open(os.path.join(install_dir, "zombie.py"), "w") as f:
        f.write(zombie_code)

    # 3. Create dummy new version
    with open(os.path.join(staging_dir, "mock_app.exe"), "w") as f:
        f.write("new version")

    return install_dir, python_exe

def run_test():
    print("Setting up test environment...")
    install_dir, python_exe = create_mock_env()
    
    # Start Zombie
    zombie_script = os.path.join(install_dir, "zombie.py")
    print(f"Starting zombie: {zombie_script}")
    # We must run it with cwd=install_dir so it locks the folder (conceptually) 
    # or just holding the file "lock.txt" inside it is enough.
    zombie_proc = subprocess.Popen([python_exe, zombie_script], cwd=install_dir)
    print(f"Zombie PID: {zombie_proc.pid}")
    time.sleep(1) # Wait for it to start
    
    # Start Main App
    main_script = os.path.join(install_dir, "main_app.py")
    print(f"Starting main app: {main_script}")
    subprocess.run([python_exe, main_script], cwd=install_dir, check=True)
    
    print("Main app exited. Waiting for update...")
    
    # Wait loop
    start = time.time()
    success = False
    while time.time() - start < 15:
        # Check if backup exists (means move succeeded)
        backup_dir = os.path.join(os.path.dirname(install_dir), "backup")
        if os.path.exists(backup_dir):
            # Check if we moved the zombie's file?
            # If move succeeded, the zombie might have crashed or the OS allowed it?
            # Windows usually disallows moving a dir if a file inside is open.
            print("Backup dir appeared!")
            success = True
            break
        time.sleep(1)
        print(".", end="", flush=True)
    
    print("\n")
    
    # Cleanup zombie
    zombie_proc.kill()
    
    if success:
        print("TEST PASSED (Update succeeded despite zombie? Windows allows moving open files?)")
        # Actually Windows sometimes allows moving a folder even if a file is open inside, 
        # as long as the drive doesn't change.
        # But if the Helper tries to delete the old dir (if it was a copy+delete strategy), it would fail.
        # The helper uses Move-Item.
        # Let's check if the Helper LOG indicates failure.
        
    else:
        print("TEST FAILED (Update timed out or failed)")
        
    # Print Log
    log_path = os.path.join(tempfile.gettempdir(), "AccessibleIPTVClient_update.log")
    if os.path.exists(log_path):
        print("Log content:")
        with open(log_path, "r") as f:
            print(f.read())
            
if __name__ == "__main__":
    run_test()
