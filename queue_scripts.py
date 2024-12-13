import sys
import os
import subprocess
import fcntl
import threading
from contextlib import contextmanager

QUEUE_FILE = "/tmp/script_queue.lock"
SCRIPT_TO_RUN = "file_processor.py"
TIMEOUT = 60 # seconds

event = threading.Event()

@contextmanager
def locked_file(filename, mode):
    with open(filename, mode) as file:
        fd = file.fileno()
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield file
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)

def append_pid_to_queue(pid):
    with locked_file(QUEUE_FILE, "a") as file:
        file.write(f"{pid}\n")

def read_queue():
    with locked_file(QUEUE_FILE, "r") as file:
        return [int(line.strip()) for line in file.readlines()]

def get_queue_position():
    queue = read_queue()
    current_pid = os.getpid()
    for idx, pid in enumerate(queue):
        if pid == current_pid:
            return idx
    return -1

def notify_next():
    event.set()

def print_status(position):
    if position > 0:
        messages = [
            "STATUS:",
            f"Waiting in queue... {position} ahead",
            f"排队中...前方还有{position}个任务",
            "STATUS_END"
        ]
        for message in messages:
            print(message)

def terminate_process(process):
    try:
        process.terminate()
        process.wait(timeout=5)  # give it a few seconds to terminate
        if process.poll() is None:
            process.kill()  # force kill if it did not terminate
            messages = [
                f"Running timeout：{' '.join(cmd)}"
                "STATUS:",
                "Running timeout, please retry",
                "任务超时，请重试",
                "STATUS_END"
            ]
            for message in messages:
                print(message)
    except Exception as e:
        print(f"Error terminating process: {e}")

def main():
    if not os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE, "w"):
            pass

    append_pid_to_queue(os.getpid())

    try:
        last_position = -1
        while True:
            position = get_queue_position()
            if position != last_position:
                print_status(position)
                last_position = position
            if position == 0:
                cmd = [sys.executable, SCRIPT_TO_RUN] + sys.argv[1:]
                print(f"Executing command: {' '.join(cmd)}")

                process = subprocess.Popen(cmd)
                timer = threading.Timer(TIMEOUT, terminate_process, [process])
                timer.start()

                try:
                    process.wait()
                    if process.returncode == 0:
                        print("Process completed successfully.")
                    else:
                        print(f"Process terminated with return code {process.returncode}.")
                finally:
                    timer.cancel()

                notify_next()
                break
            else:
                event.wait(timeout=1)
                event.clear()
    finally:
        queue = read_queue()
        queue = [pid for pid in queue if pid != os.getpid()]
        with locked_file(QUEUE_FILE, "w") as file:
            for pid in queue:
                file.write(f"{pid}\n")

if __name__ == "__main__":
    main()