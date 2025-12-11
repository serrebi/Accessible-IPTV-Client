
import subprocess
import tempfile
import os
import time
import shutil
import sys

# The URL that was failing
SOURCE_URL = "https://gohyperspeed.com/700462241/nb3KdUgE63/600000220"
USER_AGENT = "Mozilla/5.0"

def test_remux():
    temp_dir = tempfile.mkdtemp(prefix="debug_remux_")
    playlist_path = os.path.join(temp_dir, "stream.m3u8")
    
    print(f"Temp dir: {temp_dir}")
    
    cmd = [
        "ffmpeg",
        "-user_agent", USER_AGENT,
        "-i", SOURCE_URL,
        "-c", "copy",
        "-f", "hls",
        "-hls_time", "4",
        "-hls_list_size", "10",
        "-hls_flags", "delete_segments",
        "-hls_segment_filename", os.path.join(temp_dir, "seg_%03d.ts"),
        playlist_path
    ]
    
    print(f"Running: {' '.join(cmd)}")
    
    # Run ensuring we see stderr
    process = subprocess.Popen(
        cmd, 
        stdout=subprocess.DEVNULL, 
        stderr=subprocess.PIPE, 
        bufsize=0
    )
    
    print("Process started. Monitoring for 60 seconds...")
    start_time = time.time()
    
    try:
        while time.time() - start_time < 60:
            if process.poll() is not None:
                print(f"Process exited prematurely with code {process.returncode}!")
                print("STDERR Output:")
                print(process.stderr.read())
                break
            
            # Check playlist generation
            if os.path.exists(playlist_path):
                size = os.path.getsize(playlist_path)
                with open(playlist_path, 'r') as f:
                    lines = f.read().splitlines()
                
                # Get last sequence number
                seq = "N/A"
                for line in lines:
                    if "#EXT-X-MEDIA-SEQUENCE:" in line:
                        seq = line.split(":")[1]
                
                segment_count = len([l for l in lines if not l.startswith('#')])
                print(f"[{int(time.time()-start_time)}s] Playlist: {size} bytes | Seq: {seq} | Segments: {segment_count}")
            else:
                print(f"[{int(time.time()-start_time)}s] Playlist not found yet...")
            
            # Read some stderr if available (non-blockingish)
            # Actually simple read/print loop is hard without blocking.
            # We'll just rely on final dump if it crashes, or assume it's working if playlist updates.
            
            time.sleep(2)
            
    except KeyboardInterrupt:
        print("Interrupted.")
    finally:
        print("Stopping ffmpeg...")
        process.terminate()
        try:
            print(process.stderr.read())
        except:
            pass
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    test_remux()
