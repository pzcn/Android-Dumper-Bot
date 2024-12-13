import sys
import os
import subprocess
import threading

TIMEOUT = 10  # seconds

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
    cmd = [sys.executable, "file_processor.py"] + sys.argv[1:]
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

if __name__ == "__main__":
    main()