
import time
import os
print(f"ZOMBIE running PID {os.getpid()}")
# Hold a file open to prevent directory move
f = open("lock.txt", "w")
while True:
    time.sleep(1)
