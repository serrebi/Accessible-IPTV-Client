import os
import sys
import logging
import sqlite3

# Mocking environment
sys.path.append(os.getcwd())
from playlist import EPGDatabase, canonicalize_name, strip_noise_words

# Setup logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("repro")

def run_test():
    db_path = "test_skymix_repro.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    print(f"Creating DB at {db_path}")
    db = EPGDatabase(db_path)
    
    # 1. Simulate EPG Import
    # The user says "right below it is the sky mix epg".
    # This implies the EPG display name is "Sky Mix".
    # I'll add a few variants just in case.
    epg_channels = [
        ("skymix", "Sky Mix", "uk"),      # Ideal case
        ("skymix_raw", "Sky Mix", ""),    # No region
        ("skymix_hd", "Sky Mix HD", "uk"),
    ]
    
    for ch_id, disp, grp in epg_channels:
        # We manually insert to simulate the import process
        norm = canonicalize_name(strip_noise_words(disp))
        print(f"Inserting EPG: id={ch_id} disp='{disp}' norm='{norm}' grp='{grp}'")
        c = db.conn.cursor()
        c.execute(
            "INSERT INTO channels (id, display_name, norm_name, group_tag) VALUES (?, ?, ?, ?)",
            (ch_id, disp, norm, grp)
        )
    db.conn.commit()

    # 2. Simulate Playlist Channel
    # "Sky Mix UKHD"
    pl_channel = {
        "name": "Sky Mix UKHD",
        "group": "UK Entertainment",
        "tvg-id": "", 
        "tvg-name": "" 
    }
    
    print("\n--- Testing Match ---")
    print(f"Playlist Channel: {pl_channel}")
    
    # 3. Resolve
    matches, region = db.get_matching_channel_ids(pl_channel)
    print(f"Detected Region: {region}")
    
    if not matches:
        print("!! NO MATCHES FOUND !!")
        # Debug why
        norm_pl = canonicalize_name(strip_noise_words(pl_channel['name']))
        print(f"Playlist Norm: '{norm_pl}'")
        
        c = db.conn.cursor()
        rows = c.execute("SELECT * FROM channels").fetchall()
        print("DB Content:")
        for r in rows:
            print(r)
    else:
        print(f"Found {len(matches)} candidates:")
        for m in matches:
            print(f"  ID: {m['id']} | Score: {m['score']} | Why: {m['why']}")
            
        best = db.resolve_best_channel_id(pl_channel)
        print(f"\nRESOLVED BEST ID: {best}")
        
    db.close()
    if os.path.exists(db_path):
        try: os.remove(db_path)
        except: pass

if __name__ == "__main__":
    run_test()
